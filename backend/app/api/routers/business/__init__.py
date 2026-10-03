"""Router del panel: endpoints autenticados del negocio y del profesional.

Es la contraparte de `public/__init__.py`. La diferencia no es que estos exijan un
token: es **de donde sale el `business_id`**. En el router publico viene de resolver
un slug; aca sale del claim `tid` del principal y de ningun otro lado (ADR-0010). Por
eso aca no existe ningun parametro `business_id` en ninguna firma, y por eso
`get_tenant_session` no lo acepta: no hay forma de que un handler use el tenant de
otro, salvo que se ofrezca como parametro, y no se ofrece.

**El alcance de este router es exactamente el del §24 y el §21**, y nada mas:

- Panel administrativo (§24): negocio, profesionales, servicios, horarios,
  feriados, reservas.
- Panel del profesional (§21): su agenda, sus turnos, sus bloqueos.

Lo que **no** hay aca, a proposito:

- **Nada de "aprobar reservas online"** (§24 lo prohibe explicitamente). Las
  reservas nacen `confirmed` y el panel no tiene ningun endpoint para dejarlas
  pendientes.
- **Nada de editar la disponibilidad de otro profesional** sin el scope de
  configuracion. Un profesional ve su agenda y cierra sus turnos.
- **Nada de gestion de tenants.** Eso es del panel de plataforma, que va aparte y no
  comparte scopes.

Cada handler es corto y no contiene reglas: pide el principal, pide la sesion con
tenant, y delega en el modulo de dominio. El unico trabajo del router es traducir
HTTP a una llamada de servicio. Cuando un handler necesita una regla, esa regla
pertenece al servicio, y por eso `admin.py` de reservas esta separado de
`service.py`: los dos autorizan distinto.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_tenant_session, limit_panel_por_usuario, require_scopes
from app.api.errors import ConflictError
from app.api.routers.business.schemas import (
    AsignacionesIn,
    AsignacionServicioOut,
    AusenciaIn,
    AusenciaOut,
    BloqueoIn,
    BloqueoOut,
    CancelarDesdePanelIn,
    ClienteListadoOut,
    ClienteOut,
    ClientePatch,
    DiaHorarioOut,
    EstadoFinalIn,
    EventoReservaOut,
    ExcepcionIn,
    ExcepcionOut,
    FeriadoIn,
    FeriadoOut,
    HorarioProfesionalOut,
    NegocioOut,
    NegocioPatch,
    ProfesionalCreate,
    ProfesionalOut,
    ProfesionalPatch,
    ReservaOut,
    ReservaPagina,
    SemanaHorarioIn,
    SemanaHorarioOut,
    ServicioCreate,
    ServicioOut,
    ServicioPatch,
    SlugDisponibleOut,
    VentanaOut,
    WalkinIn,
)
from app.core.time import now
from app.models.enums import BookingStatus, BusinessUserRole
from app.modules.auth.scopes import Scope
from app.modules.auth.tokens import Principal
from app.modules.bookings import admin as bookings_admin
from app.modules.bookings.models import BookingEvent
from app.modules.businesses import service as negocios
from app.modules.customers import service as clientes
from app.modules.notifications.models import NotificationRequest
from app.modules.professionals import service as profesionales
from app.modules.professionals.models import Professional
from app.modules.schedules import service as horarios
from app.modules.services import service as servicios

#: 600/min por usuario (§10.5), por `user_id` del token y no por IP. El criterio esta
#: en `api.dependencies.limit_panel_por_usuario`.
router = APIRouter(
    prefix="/business",
    tags=["Panel del negocio"],
    dependencies=[Depends(limit_panel_por_usuario)],
)

#: Sesion con el tenant del principal. Es la unica sesion que usan estos handlers:
#: todas las tablas que tocan tienen RLS.
Sesion = Annotated[AsyncSession, Depends(get_tenant_session)]

# --------------------------------------------------------------------------- #
# Contexto
# --------------------------------------------------------------------------- #


def _bid(principal: Principal) -> uuid.UUID:
    """El `business_id` del principal.

    `get_tenant_session` ya se encargo de rechazar los tokens de plataforma con un
    403, asi que llegar aca con `business_id=None` es imposible. El `assert` esta
    para el tipador, no para el servidor: no es una rama de runtime, es la
    declaracion de que lo que la dependencia garantiza se puede usar sin chequeo.
    """
    if principal.business_id is None:
        raise AssertionError("get_tenant_session garantiza business_id; no se llego aca")
    return principal.business_id


def _es_admin(principal: Principal) -> bool:
    return principal.role == BusinessUserRole.ADMIN


# --------------------------------------------------------------------------- #
# Yo / negocio
# --------------------------------------------------------------------------- #


@router.get("/me", response_model=dict, summary="Quien soy y que puedo hacer")
async def mi_perfil(principal: Annotated[Principal, Depends(require_scopes())], sesion: Sesion):
    """Identidad, rol y scopes del token.

    No exige ningun scope a proposito: el unico endpoint al que tiene que poder
    entrar alguien es este, y sin el el frontend no tiene forma de saber que mostrar
    antes de esconder la parte del panel que no le corresponde.

    Devuelve los scopes ordenados porque van directo a la lista de checks del
    frontend, y un `set` de Python no tiene orden estable entre corridas.
    """
    del sesion  # la sesion no se usa, pero la dependencia fija el tenant
    return {
        "user_id": str(principal.user_id),
        "business_id": str(principal.business_id),
        "role": str(principal.role),
        "scopes": sorted(principal.scopes),
        "is_admin": _es_admin(principal),
    }


@router.get(
    "/negocio",
    response_model=NegocioOut,
    summary="Configuracion del negocio",
)
async def leer_negocio(
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BUSINESS_CONFIG_READ))],
) -> Any:
    return await negocios.get_business(sesion, _bid(principal))


@router.patch(
    "/negocio",
    response_model=NegocioOut,
    summary="Editar la configuracion del negocio",
)
async def editar_negocio(
    cuerpo: NegocioPatch,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BUSINESS_CONFIG_WRITE))],
) -> Any:
    """Aplica los cambios parciales que llegan.

    El slug se maneja aparte del resto porque necesita un chequeo de disponibilidad
    que los otros campos no requieren: es la unica clave global de la tabla y dos
    negocios podrian pedir el mismo a la vez. Se resuelve antes de tocar nada, para
    que un slug ocupado no deje la mitad de la edicion aplicada.
    """
    cambios = cuerpo.model_dump(exclude_unset=True, exclude={"slug"})
    slug_nuevo = cuerpo.slug

    business = await negocios.actualizar_configuracion(sesion, _bid(principal), cambios)

    if slug_nuevo is not None and slug_nuevo != business.slug:
        slug_final = negocios.slugify(slug_nuevo)
        if await negocios.slug_disponible(sesion, slug_final, excluir=business.id):
            negocios.cambiar_slug(business, slug_final)
        else:
            # Se levanta antes de aplicar `cambios`. Al revés, un slug tomado
            # dejaria el nombre, el telefono o el horario ya cambiados y el
            # administrador creyendo que no se guardo nada.
            raise ConflictError(f"El slug '{slug_final}' ya esta en uso.")

    await sesion.flush()
    return await negocios.get_business(sesion, _bid(principal))


@router.get(
    "/negocio/slug-disponible",
    response_model=SlugDisponibleOut,
    summary="Comprobar si un slug esta libre",
)
async def comprobar_slug(
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BUSINESS_CONFIG_WRITE))],
    slug: Annotated[str, Query(min_length=3, max_length=64, description="Slug a comprobar")],
) -> Any:
    """Para el selector de slug mientras se escribe.

    Se responde 200 con `disponible: false` en vez de 409: el frontend lo consulta
    en cada tecla y un error por cada slug tomado seria ruido de consola, no
    informacion. El 409 sigue reservandose para el guardado, que es donde importa.
    """
    candidato = negocios.slugify(slug)
    if not candidato:
        return SlugDisponibleOut(slug=candidato, disponible=False)
    return SlugDisponibleOut(
        slug=candidato,
        disponible=await negocios.slug_disponible(sesion, candidato, excluir=_bid(principal)),
    )


# --------------------------------------------------------------------------- #
# Servicios
# --------------------------------------------------------------------------- #


@router.get("/servicios", response_model=list[ServicioOut], summary="Listar servicios")
async def listar_servicios(
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_READ))],
    incluir_archivados: Annotated[bool, Query()] = False,
    incluir_inactivos: Annotated[bool, Query()] = False,
) -> Any:
    return await servicios.listar_servicios(
        sesion,
        _bid(principal),
        incluir_archivados=incluir_archivados,
        incluir_inactivos=incluir_inactivos,
    )


@router.post(
    "/servicios",
    response_model=ServicioOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un servicio",
)
async def crear_servicio(
    cuerpo: ServicioCreate,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_WRITE))],
) -> Any:
    return await servicios.crear_servicio(
        sesion,
        _bid(principal),
        servicios.ServiceCreate(
            name=cuerpo.name,
            description=cuerpo.description,
            duration_minutes=cuerpo.duration_minutes,
            price=cuerpo.price,
            currency=cuerpo.currency,
            color=cuerpo.color,
            sort_order=cuerpo.sort_order or 0,
        ),
    )


@router.patch("/servicios/{service_id}", response_model=ServicioOut, summary="Editar un servicio")
async def editar_servicio(
    service_id: uuid.UUID,
    cuerpo: ServicioPatch,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_WRITE))],
) -> Any:
    cambios = cuerpo.model_dump(exclude_unset=True)
    return await servicios.actualizar_servicio(
        sesion,
        service_id,
        _bid(principal),
        servicios.ServiceUpdate(**cambios),
    )


@router.delete(
    "/servicios/{service_id}",
    response_model=ServicioOut,
    summary="Archivar un servicio",
)
async def archivar_servicio(
    service_id: uuid.UUID,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_WRITE))],
) -> Any:
    """Archiva, no borra fisicamente.

    Hay `bookings.service_id` con FK `RESTRICT` apuntando ahi. Un borrado real de un
    servicio que se uso falla, y translated a 500 le diria al administrador que el
    sistema esta roto cuando lo que paso es que el servicio tiene historia. Archivar
    saca el servicio de la reserva online y deja la historia intacta.

    Responde 200 con el servicio archivado y no 204: el panel muestra el estado
    resultante, y devolverselo evita un segundo pedido para confirmar lo que paso.
    """
    return await servicios.archivar_servicio(sesion, service_id, _bid(principal))


# --------------------------------------------------------------------------- #
# Profesionales
# --------------------------------------------------------------------------- #


@router.get("/profesionales", response_model=list[ProfesionalOut], summary="Listar profesionales")
async def listar_profesionales(
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_READ))],
    incluir_archivados: Annotated[bool, Query()] = False,
    incluir_inactivos: Annotated[bool, Query()] = False,
    service_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Any:
    return await profesionales.listar_profesionales(
        sesion,
        _bid(principal),
        incluir_archivados=incluir_archivados,
        incluir_inactivos=incluir_inactivos,
        service_id=service_id,
    )


@router.post(
    "/profesionales",
    response_model=ProfesionalOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un profesional",
)
async def crear_profesional(
    cuerpo: ProfesionalCreate,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_WRITE))],
) -> Any:
    return await profesionales.crear_profesional(
        sesion,
        _bid(principal),
        profesionales.ProfessionalCreate(
            display_name=cuerpo.display_name,
            bio=cuerpo.bio,
            color=cuerpo.color,
            sort_order=cuerpo.sort_order,
        ),
    )


@router.patch(
    "/profesionales/{professional_id}",
    response_model=ProfesionalOut,
    summary="Editar un profesional",
)
async def editar_profesional(
    professional_id: uuid.UUID,
    cuerpo: ProfesionalPatch,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_WRITE))],
) -> Any:
    cambios = cuerpo.model_dump(exclude_unset=True)
    return await profesionales.actualizar_profesional(
        sesion, professional_id, _bid(principal), profesionales.ProfessionalUpdate(**cambios)
    )


@router.delete(
    "/profesionales/{professional_id}",
    response_model=ProfesionalOut,
    summary="Archivar un profesional",
)
async def archivar_profesional(
    professional_id: uuid.UUID,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_WRITE))],
) -> Any:
    """Archiva al profesional y **no** le cierra la cuenta.

    Son dos acciones distintas y mezclarlas seria un problema: archivar sacude a
    alguien de la reserva online (puede atender aun el turno de manana que ya tiene),
    mientras que cerrarle la cuenta le impide entrar. Archivar solo lo saca del
    selector; desvincular la cuenta es `DELETE /profesionales/{id}/cuenta`.
    """
    return await profesionales.archivar_profesional(sesion, professional_id, _bid(principal))


@router.delete(
    "/profesionales/{professional_id}/cuenta",
    response_model=ProfesionalOut,
    summary="Desvincular la cuenta de login",
)
async def desvincular_cuenta(
    professional_id: uuid.UUID,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_WRITE))],
) -> Any:
    """Saca el acceso. A diferencia del archivado, esto **no** se puede deshacer solo.

    Es endpoint aparte justamente porque la consecuencia es de seguridad y no de
    agenda: la persona deja de poder entrar. Un unico `DELETE` que hiciera las dos
    cosas dejaria sin forma de "dar de baja" sin tambien cerrar la cuenta.
    """
    return await profesionales.desvincular_usuario(sesion, professional_id, _bid(principal))


@router.get(
    "/profesionales/{professional_id}/servicios",
    response_model=list[AsignacionServicioOut],
    summary="Servicios que puede hacer",
)
async def servicios_del_profesional(
    professional_id: uuid.UUID,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_READ))],
) -> Any:
    return await profesionales.listar_servicios_de(sesion, professional_id, _bid(principal))


@router.put(
    "/profesionales/{professional_id}/servicios",
    response_model=list[AsignacionServicioOut],
    summary="Reemplazar los servicios que puede hacer",
)
async def asignar_servicios(
    professional_id: uuid.UUID,
    cuerpo: AsignacionesIn,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_WRITE))],
) -> Any:
    """Reemplazo completo, no un delta.

    El frontend manda la lista entera que quiere y el resultado es exactamente esa
    lista. Un endpoint que agrega y quita obliga al cliente a conocer el estado
    actual para calcular la diferencia, y en un formulario con "guardar" eso
    convierte un fallo de red en servicios cambiados a la mitad sin que nadie lo
    note hasta que un cliente no puede reservar.
    """
    filas = await profesionales.asignar_servicios(
        sesion,
        professional_id,
        _bid(principal),
        [(a.service_id, a.custom_duration_minutes, a.custom_price) for a in cuerpo.servicios],
    )
    return filas


# --------------------------------------------------------------------------- #
# Horarios del negocio
# --------------------------------------------------------------------------- #


@router.get("/horarios", response_model=SemanaHorarioOut, summary="Horario semanal")
async def leer_horarios(
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BUSINESS_CONFIG_READ))],
) -> Any:
    filas = await horarios.listar_horarios(sesion, _bid(principal))
    return SemanaHorarioOut(
        dias=[
            DiaHorarioOut(
                weekday=dia.weekday,
                windows=[VentanaOut(start=v.start, end=v.end) for v in dia.windows],
            )
            for dia in horarios.agrupar_horarios_por_dia(filas)
        ]
    )


@router.put("/horarios", response_model=SemanaHorarioOut, summary="Reemplazar el horario semanal")
async def reemplazar_horarios(
    cuerpo: SemanaHorarioIn,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BUSINESS_CONFIG_WRITE))],
) -> Any:
    """Reemplaza la semana entera, en una transaccion.

    El dia que no viene en la lista queda cerrado. Es la semantica de "este es mi
    horario", no de "agregar estos dias": el panel manda los 7 y el que no tiene
    ventanas queda sin horario, que es lo que el administrador quiere decir cuando
    desmarca un dia.
    """
    filas = await horarios.reemplazar_horarios(
        sesion,
        _bid(principal),
        [
            horarios.HorarioDia(
                weekday=d.weekday,
                windows=[horarios.Ventana(start=w.start, end=w.end) for w in d.windows],
            )
            for d in cuerpo.dias
        ],
    )
    return SemanaHorarioOut(
        dias=[
            DiaHorarioOut(
                weekday=dia.weekday,
                windows=[VentanaOut(start=v.start, end=v.end) for v in dia.windows],
            )
            for dia in horarios.agrupar_horarios_por_dia(filas)
        ]
    )


# --------------------------------------------------------------------------- #
# Horarios del profesional
# --------------------------------------------------------------------------- #


@router.get(
    "/profesionales/{professional_id}/horarios",
    response_model=HorarioProfesionalOut,
    summary="Horario propio de un profesional",
)
async def leer_horarios_profesional(
    professional_id: uuid.UUID,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.AVAILABILITY_READ))],
) -> Any:
    """Devuelve las ventanas propias y si esta heredando las del negocio.

    `hereda` se deduce de que no haya filas propias (ADR-0005). Viaja explicito
    porque el frontend no puede deducirlo: cero ventanas es un horario valido --un
    profesional que no atiende ningun dia-- y "sin filas" puede significar las dos
    cosas.
    """
    filas = await horarios.listar_horarios_profesional(sesion, professional_id, _bid(principal))
    return HorarioProfesionalOut(
        hereda=not filas,
        dias=[
            DiaHorarioOut(
                weekday=dia.weekday,
                windows=[VentanaOut(start=v.start, end=v.end) for v in dia.windows],
            )
            for dia in horarios.agrupar_horarios_por_dia(filas)
        ],
    )


@router.put(
    "/profesionales/{professional_id}/horarios",
    response_model=HorarioProfesionalOut,
    summary="Reemplazar el horario de un profesional",
)
async def reemplazar_horarios_profesional(
    professional_id: uuid.UUID,
    cuerpo: SemanaHorarioIn,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BUSINESS_CONFIG_WRITE))],
) -> Any:
    """Reemplaza el horario propio, o devuelve el profesional a heredar.

    Un profesional sin horario propio sigue el del negocio. Es un estado valido y el
    mas comun: se representa con **cero filas**, no con un flag. Un flag "heredar"
    conviviendo con filas propias genera tres estados y solo dos son coherentes.
    """
    filas = await horarios.reemplazar_horarios_profesional(
        sesion,
        professional_id,
        _bid(principal),
        [
            horarios.HorarioDia(
                weekday=d.weekday,
                windows=[horarios.Ventana(start=w.start, end=w.end) for w in d.windows],
            )
            for d in cuerpo.dias
        ],
        heredar=not cuerpo.dias,
    )
    return HorarioProfesionalOut(
        hereda=not filas,
        dias=[
            DiaHorarioOut(
                weekday=dia.weekday,
                windows=[VentanaOut(start=v.start, end=v.end) for v in dia.windows],
            )
            for dia in horarios.agrupar_horarios_por_dia(filas)
        ],
    )


# --------------------------------------------------------------------------- #
# Excepciones y feriados
# --------------------------------------------------------------------------- #


@router.get("/excepciones", response_model=list[ExcepcionOut], summary="Listar excepciones de dia")
async def listar_excepciones(
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BUSINESS_CONFIG_READ))],
) -> Any:
    return await horarios.listar_excepciones(sesion, _bid(principal))


@router.post(
    "/excepciones",
    response_model=ExcepcionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una excepcion de dia",
)
async def crear_excepcion(
    cuerpo: ExcepcionIn,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BUSINESS_CONFIG_WRITE))],
) -> Any:
    """Cierra un dia o le cambia el horario.

    Una excepcion **reemplaza** el horario normal de ese dia: si trae ventanas, el
    negocio abre solo esas; si `is_closed`, no abre. Por eso `is_closed` con ventanas
    es una contradiccion y se rechaza antes de insertar nada.
    """
    return await horarios.crear_excepcion(
        sesion,
        _bid(principal),
        local_date=cuerpo.local_date,
        is_closed=cuerpo.is_closed,
        reason=cuerpo.reason,
        windows=[horarios.Ventana(start=w.start, end=w.end) for w in cuerpo.windows],
    )


@router.delete(
    "/excepciones/{exception_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar una excepcion",
)
async def eliminar_excepcion(
    exception_id: uuid.UUID,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BUSINESS_CONFIG_WRITE))],
) -> Response:
    """Idempotente: borrar una excepcion que no existe es el resultado que se quiere.

    Un 404 en un "quitar" de la UI molesta y no informa: el usuario pidio que ya no
    este, y ya no esta.
    """
    await horarios.eliminar_excepcion(sesion, _bid(principal), exception_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/feriados", response_model=list[FeriadoOut], summary="Listar feriados")
async def listar_feriados(
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BUSINESS_CONFIG_READ))],
) -> Any:
    return await horarios.listar_feriados(sesion, _bid(principal))


@router.post(
    "/feriados",
    response_model=FeriadoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un feriado",
)
async def crear_feriado(
    cuerpo: FeriadoIn,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BUSINESS_CONFIG_WRITE))],
) -> Any:
    """El nombre es obligatorio.

    Sin nombre, el panel muestra un dia tachado y el administrador no sabe que esta
    cerrado. Es el dato que explica el cierre cuando alguien pregunta dos meses
    despues.
    """
    return await horarios.crear_feriado(
        sesion, _bid(principal), local_date=cuerpo.local_date, name=cuerpo.name
    )


@router.delete(
    "/feriados/{holiday_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar un feriado",
)
async def eliminar_feriado(
    holiday_id: uuid.UUID,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BUSINESS_CONFIG_WRITE))],
) -> Response:
    await horarios.eliminar_feriado(sesion, _bid(principal), holiday_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Ausencias y bloqueos
# --------------------------------------------------------------------------- #


@router.get("/ausencias", response_model=list[AusenciaOut], summary="Listar ausencias")
async def listar_ausencias(
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.AVAILABILITY_READ))],
    professional_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Any:
    return await horarios.listar_ausencias(sesion, _bid(principal), professional_id=professional_id)


@router.post(
    "/ausencias",
    response_model=AusenciaOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una ausencia",
)
async def crear_ausencia(
    cuerpo: AusenciaIn,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_WRITE))],
) -> Any:
    """Vacaciones, licencias o ausencias.

    Solo se crean ausencias **aprobadas** desde el panel. El estado `pending` existe
    para cuando la reporta el propio profesional y tiene que aprobarla alguien con
    `TEAM_WRITE`; crear una pendiente desde el panel seria un estado que nadie revisa.
    """
    return await horarios.crear_ausencia(
        sesion,
        _bid(principal),
        professional_id=cuerpo.professional_id,
        starts_at=cuerpo.starts_at,
        ends_at=cuerpo.ends_at,
        kind=str(cuerpo.kind),
        status=str(cuerpo.status),
        reason=cuerpo.reason,
    )


@router.delete(
    "/ausencias/{time_off_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar una ausencia",
)
async def eliminar_ausencia(
    time_off_id: uuid.UUID,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_WRITE))],
) -> Response:
    await horarios.eliminar_ausencia(sesion, _bid(principal), time_off_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/bloqueos", response_model=list[BloqueoOut], summary="Listar bloqueos")
async def listar_bloqueos(
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.AVAILABILITY_READ))],
    professional_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Any:
    """Sin `professional_id`, devuelve los bloqueos de todo el negocio.

    Ojo con lo que significa: un bloqueo con `professional_id=None` es un cierre de
    todo el negocio, y uno con id es de una persona. La lista sin filtro trae los dos
    y el frontend tiene que distinguir por el `professional_id` que venga `None`.
    """
    return await horarios.listar_bloqueos(sesion, _bid(principal), professional_id=professional_id)


@router.post(
    "/bloqueos",
    response_model=BloqueoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un bloqueo",
)
async def crear_bloqueo(
    cuerpo: BloqueoIn,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_WRITE))],
) -> Any:
    """Bloquea a un profesional o a todo el negocio.

    Sin `professional_id` el bloqueo es general. Un profesional puede bloquearse a si
    mismo con su propio scope de agenda, pero desde este endpoint hace falta
    `TEAM_WRITE`: decidir el calendario de otra persona es decision del negocio.
    """
    return await horarios.crear_bloqueo(
        sesion,
        _bid(principal),
        starts_at=cuerpo.starts_at,
        ends_at=cuerpo.ends_at,
        kind=str(cuerpo.kind),
        professional_id=cuerpo.professional_id,
        reason=cuerpo.reason,
        created_by_user_id=principal.user_id,
    )


@router.delete(
    "/bloqueos/{block_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar un bloqueo",
)
async def eliminar_bloqueo(
    block_id: uuid.UUID,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.TEAM_WRITE))],
) -> Response:
    await horarios.eliminar_bloqueo(sesion, _bid(principal), block_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Reservas
# --------------------------------------------------------------------------- #


@router.get("/reservas", response_model=ReservaPagina, summary="Listar reservas")
async def listar_reservas(
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BOOKINGS_READ_ANY))],
    desde: Annotated[dt.date | None, Query(description="Fecha local inicial")] = None,
    hasta: Annotated[dt.date | None, Query(description="Fecha local final")] = None,
    professional_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    estado: BookingStatus | None = None,
    solo_pendientes: Annotated[bool, Query(description="Turnos futuros sin cerrar")] = False,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> Any:
    """Reservas del negocio, filtrables.

    `desde`/`hasta` son **fechas locales del negocio**, no UTC: un filtro por UTC
    dejaria afuera el turno de las 22:00 del dia pedido, que en Buenos Aires ya es
    el dia siguiente. Por eso el filtro va por `bookings.local_date`, que ya esta
    calculada con el timezone del negocio.

    Devuelve el total ademas de la pagina para que el panel pueda mostrar "pagina 3
    de 47" sin adivinar por si la ultima vino llena.
    """
    filtros = bookings_admin.ReservaFiltros(
        desde=desde,
        hasta=hasta,
        professional_id=professional_id,
        service_id=service_id,
        customer_id=customer_id,
        estado=estado,
        solo_pendientes=solo_pendientes,
        limite=limite,
        offset=offset,
    )
    filas, total = await bookings_admin.listar_reservas(sesion, _bid(principal), filtros)
    return ReservaPagina(
        items=[_a_reserva_out(f) for f in filas],
        total=total,
        limite=limite,
        offset=offset,
    )


def _a_reserva_out(detalle: bookings_admin.ReservaDetalle) -> ReservaOut:
    """Arma el schema de salida desde el detalle con nombres.

    Los nombres vienen del `LEFT JOIN` de la consulta y no de consultas aparte por
    fila: la version ingenua --servicio, profesional y cliente por cada reserva-- es
    la razon por la que un listado de 50 filas tarda medio segundo.
    """
    b = detalle.booking
    return ReservaOut(
        id=b.id,
        status=b.status,
        source=b.source,
        starts_at=b.starts_at,
        ends_at=b.ends_at,
        local_date=b.local_date,
        duration_minutes=b.duration_minutes,
        price_snapshot=b.price_snapshot,
        currency=b.currency,
        service_id=b.service_id,
        professional_id=b.professional_id,
        customer_id=b.customer_id,
        notes=b.notes,
        cancel_reason=b.cancel_reason,
        cancelled_at=b.cancelled_at,
        created_at=b.created_at,
        servicio_nombre=detalle.servicio_nombre,
        profesional_nombre=detalle.profesional_nombre,
        cliente_nombre=detalle.cliente_nombre,
        cliente_telefono=detalle.cliente_telefono,
    )


@router.get(
    "/reservas/{booking_id}",
    response_model=ReservaOut,
    summary="Ver una reserva",
)
async def leer_reserva(
    booking_id: uuid.UUID,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BOOKINGS_READ_ANY))],
) -> Any:
    detalle = await bookings_admin.obtener_reserva(sesion, booking_id, business_id=_bid(principal))
    return _a_reserva_out(detalle)


@router.get(
    "/reservas/{booking_id}/eventos",
    response_model=list[EventoReservaOut],
    summary="Historial de una reserva",
)
async def eventos_reserva(
    booking_id: uuid.UUID,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BOOKINGS_READ_ANY))],
) -> Any:
    """La traza de auditoria: creada, reprogramada, cancelada, cerrada.

    Es la unica fuente de "que paso con este turno". El estado actual dice como termino
    la reserva; esta lista dice como llego hasta ahi, que es lo que necesita el
    administrador cuando un cliente reclama que le movieron el horario.
    """
    await bookings_admin.obtener_reserva(sesion, booking_id, business_id=_bid(principal))
    result = await sesion.execute(
        select(BookingEvent)
        .where(
            BookingEvent.booking_id == booking_id,
            BookingEvent.business_id == _bid(principal),
        )
        .order_by(BookingEvent.created_at)
    )
    return list(result.scalars())


@router.post(
    "/reservas/{booking_id}/cancelar",
    response_model=ReservaOut,
    summary="Cancelar una reserva desde el panel",
)
async def cancelar_reserva(
    booking_id: uuid.UUID,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BOOKINGS_WRITE_ANY))],
    cuerpo: Annotated[CancelarDesdePanelIn | None, Body()] = None,
) -> Any:
    """Cancela sin pedir secure token, porque la autorizacion es el principal.

    Aplica las mismas reglas que el cancelado del cliente: respeta la ventana de
    cancelacion del negocio y no cancela lo que ya empezo. Un negocio que necesite
    cancelar en cualquier momento pone la ventana en 0.

    Cancelar en cascada los recordatorios programados, igual que en el camino
    publico: un turno cancelado al que le saltan dos recordatorios despues es la
    peor clase de recordatorio.
    """
    business = await negocios.get_business(sesion, _bid(principal))
    await bookings_admin.cancelar_desde_panel(
        sesion,
        booking_id,
        business_id=business.id,
        cancellation_window_minutes=business.cancellation_window_minutes,
        motivo=cuerpo.motivo if cuerpo else None,
        actor_user_id=principal.user_id,
    )
    detalle = await bookings_admin.obtener_reserva(sesion, booking_id, business_id=_bid(principal))
    return _a_reserva_out(detalle)


@router.post(
    "/reservas/{booking_id}/estado",
    response_model=ReservaOut,
    summary="Marcar la reserva como completada o no-asistio",
)
async def marcar_estado(
    booking_id: uuid.UUID,
    cuerpo: EstadoFinalIn,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BOOKINGS_WRITE_ANY))],
) -> Any:
    """Cierra el turno: el cliente vino, o no vino.

    Solo se permite si el turno **ya termino**. Un "no asistio" antes de la hora seria
    una mentira en la agenda, y un "completado" antes de la hora es un error de dedo
    que despues nadie puede distinguir de un dato real.
    """
    await bookings_admin.marcar_estado_final(
        sesion,
        booking_id,
        business_id=_bid(principal),
        estado=cuerpo.estado,
        actor_user_id=principal.user_id,
    )
    detalle = await bookings_admin.obtener_reserva(sesion, booking_id, business_id=_bid(principal))
    return _a_reserva_out(detalle)


@router.post(
    "/reservas/walkin",
    response_model=ReservaOut,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar un cliente sin reserva",
)
async def registrar_walkin(
    cuerpo: WalkinIn,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BOOKINGS_WALKIN))],
) -> Any:
    """Agrega a mano un cliente que llego al mostrador (§23).

    Es una reserva mas: ocupa horario, aparece en la agenda y puede chocar con la
    EXCLUDE si ese horario ya estaba tomado. La unica diferencia es que no dispara
    WhatsApp, porque el cliente esta ahi mismo y un recordatorio de dos horas para
    alguien que ya llego es directamente falso.

    El horario puede no estar en la grilla de la disponibilidad --un turno entre dos
    reservas, por ejemplo-- y aun asi se registra. La EXCLUDE es la autoridad: si el
    hueco esta libre, el turno es valido. Prefijar esta operacion con una validacion
    de grilla haria que el panel no pueda registrar la realidad, y la agenda dejaria
    de reflejar lo que paso.
    """
    business = await negocios.get_business(sesion, _bid(principal))
    booking = await bookings_admin.registrar_walkin(
        sesion,
        business_id=business.id,
        service_id=cuerpo.service_id,
        professional_id=cuerpo.professional_id,
        customer_first_name=cuerpo.customer_first_name,
        customer_last_name=cuerpo.customer_last_name,
        customer_phone_e164=cuerpo.customer_phone_e164,
        starts_at=cuerpo.starts_at,
        timezone=business.timezone,
        actor_user_id=principal.user_id,
        notas=cuerpo.notas,
    )
    detalle = await bookings_admin.obtener_reserva(sesion, booking.id, business_id=_bid(principal))
    return _a_reserva_out(detalle)


# --------------------------------------------------------------------------- #
# Agenda del profesional (§21)
# --------------------------------------------------------------------------- #


@router.get(
    "/profesional/agenda",
    response_model=list[ReservaOut],
    summary="Mi agenda",
)
async def mi_agenda(
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BOOKINGS_READ_OWN))],
    desde: Annotated[dt.date | None, Query()] = None,
    hasta: Annotated[dt.date | None, Query()] = None,
) -> Any:
    """Turnos del profesional que esta mirando, para una ventana de fechas.

    Se resuelve el `professional_id` desde el `user_id` del principal, nunca desde un
    parametro. Aceptar un `professional_id` en la URL permitiria que un profesional con
    `BOOKINGS_READ_OWN` lea la agenda de otro cambiando un query param: el scope dice
    "los suyos" y la URL no puede ampliarlo.
    """
    professional_id = await _professional_id_del_principal(sesion, principal)
    if professional_id is None:
        return []

    business = await negocios.get_business(sesion, _bid(principal))
    tz = _zona(business.timezone)
    hoy = now().astimezone(tz).date()
    inicio = dt.datetime.combine(desde or hoy, dt.time.min, tzinfo=tz)
    fin = dt.datetime.combine(hasta or hoy, dt.time.max, tzinfo=tz)

    filas = await bookings_admin.agenda_del_profesional(
        sesion, _bid(principal), professional_id, desde=inicio, hasta=fin
    )
    return [_a_reserva_out(f) for f in filas]


def _zona(timezone: str) -> ZoneInfo:
    """El timezone del negocio. Lanza `ZoneInfoNotFoundError` si esta mal guardado.

    Que lance es correcto: el timezone se valida al guardarlo, asi que si aparece
    aca es que alguien lo metio en la base saltandose el servicio. Un `timezone`
    invalido no puede caerse a un default silencioso, porque las fechas locales de la
    agenda dejarian de coincidir con las que ve el cliente.
    """
    return ZoneInfo(timezone)


async def _professional_id_del_principal(
    sesion: AsyncSession, principal: Principal
) -> uuid.UUID | None:
    """El `professional_id` del usuario autenticado, o `None` si no es profesional.

    Va por `business_users.id`, que es el `user_id` del token. Un administrador que
    entra al panel del profesional no tiene profesional asociado y recibe una agenda
    vacia en vez de un error: no es un caso roto, es un rol que no usa esa vista.
    """
    result = await sesion.execute(
        select(Professional.id).where(
            Professional.business_id == _bid(principal),
            Professional.user_id == principal.user_id,
            Professional.archived_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


# --------------------------------------------------------------------------- #
# Clientes
# --------------------------------------------------------------------------- #


@router.get("/clientes", response_model=list[ClienteListadoOut], summary="Listar clientes")
async def listar_clientes(
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.CLIENTS_READ))],
    busqueda: Annotated[str | None, Query(max_length=100)] = None,
    solo_optout: Annotated[bool, Query()] = False,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> Any:
    """Clientes del negocio, con su historial de reservas.

    El conteo de reservas y la fecha de la ultima se agregan en la misma consulta, no
    como columnas de `customers`: guardarlos seria una segunda fuente de verdad que
    se desincroniza en la primera cancelacion, y el panel empezaria a mentir sin que
    nadie se entere.
    """
    filas = await clientes.listar_clientes(
        sesion,
        _bid(principal),
        busqueda=busqueda,
        solo_optout=solo_optout,
        limite=limite,
        offset=offset,
    )
    return [
        ClienteListadoOut(
            **ClienteOut.model_validate(item.customer).model_dump(),
            total_reservas=item.booking_count,
            ultima_reserva=item.last_booking_at,
        )
        for item in filas
    ]


@router.get("/clientes/{customer_id}", response_model=ClienteOut, summary="Ver un cliente")
async def leer_cliente(
    customer_id: uuid.UUID,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.CLIENTS_READ))],
) -> Any:
    return await clientes.obtener_cliente(sesion, customer_id, business_id=_bid(principal))


@router.patch(
    "/clientes/{customer_id}",
    response_model=ClienteOut,
    summary="Editar un cliente",
)
async def editar_cliente(
    customer_id: uuid.UUID,
    cuerpo: ClientePatch,
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.CLIENTS_WRITE))],
) -> Any:
    """Edita nombre, notas y estado de mensajes.

    El opt-out y el marketing se mueven juntos porque la base tiene un
    `CHECK` que lo exige: un cliente dado de baja tiene `marketing_opt_in = false`.
    Sacar el opt-out **no** reactiva el marketing solo; volver a dar permiso de
    marketing es otra decision, con su propio campo en el mismo pedido.
    """
    cambios = cuerpo.model_dump(exclude_unset=True)
    return await clientes.actualizar_cliente(
        sesion,
        customer_id,
        _bid(principal),
        clientes.CustomerUpdate(**cambios),
    )


# --------------------------------------------------------------------------- #
# Notificaciones (diagnostico del panel)
# --------------------------------------------------------------------------- #


@router.get(
    "/notificaciones",
    response_model=list[dict],
    summary="Ver el estado de los mensajes programados",
)
async def listar_notificaciones(
    sesion: Sesion,
    principal: Annotated[Principal, Depends(require_scopes(Scope.BUSINESS_CONFIG_READ))],
    reserva_id: Annotated[uuid.UUID | None, Query()] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Any:
    """Los mensajes programados y su estado. Diagnostico, no gestion.

    Existe para responder "¿le llego el recordatorio a este cliente?". Un recordatorio
    que salio a las 10:07 y el cliente dice que no le llego, y la respuesta esta en
    estas filas: el estado es `sent`, o `pending` con el motivo del reintento, o
    `skipped` porque el cliente pidio no recibir mensajes.

    Es de solo lectura y no reintenta nada. Reintentar desde el panel permitiria
    duplicar mensajes a un cliente que ya los recibio, y esa decision necesita un
    criterio que todavia no esta definido.
    """
    filtros = [
        NotificationRequest.business_id == _bid(principal),
    ]
    if reserva_id is not None:
        filtros.append(NotificationRequest.booking_id == reserva_id)

    result = await sesion.execute(
        select(NotificationRequest)
        .where(*filtros)
        .order_by(NotificationRequest.scheduled_for.desc())
        .limit(limite)
    )
    return [
        {
            "id": str(n.id),
            "booking_id": str(n.booking_id),
            "kind": str(n.kind),
            "status": str(n.status),
            "attempts": n.attempts,
            "max_attempts": n.max_attempts,
            "scheduled_for": n.scheduled_for,
            "error": n.error,
        }
        for n in result.scalars()
    ]


__all__ = ["router"]
