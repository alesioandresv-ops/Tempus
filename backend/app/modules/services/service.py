"""Servicio de servicios del negocio: alta, edicion y baja logica.

**Archivar, no borrar.** `Service.archived_at` existe y `is_offered` depende de el
por una razon concreta: hay `bookings.service_id` con FK `RESTRICT`. Un `DELETE`
físico de un servicio que tiene reservas historicas falla con un `IntegrityError`,
y el `IntegrityError` traducido a 500 le dice al administrador que el sistema esta
roto cuando lo que paso es que el servicio se uso. Archivar deja la historia
intacta y saca el servicio de la pagina publica, que es lo que el quiere.

Tambien se desactiva con `is_active`, que es distinto de archivar: desactivar
deja el servicio cargado en el panel y lo saca de la reserva online; archivar ademas
lo saca del panel. Las dos cosas se ofrecen porque son decisiones distintas y
mezclarlas obliga al administrador a elegir entre "no se puede reservar" y "no se
ve".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import NotFoundError, ValidationError
from app.core.time import now
from app.modules.professionals.models import Professional
from app.modules.services.models import ProfessionalService, Service

#: Estados que cuentan como "la reserva ocupa lugar": los unicos que bloquean el
#: horario y los que un reporte de facturacion tendria que mirar. Canceladas y
#: `no_show` no, y por eso no impiden archivar.
#:
#: Se deja el dato documentado pero no se usa todavia: `archivar_servicio` no
#: consulta reservas, y no debe empezar a hacerlo por el solo hecho de que este
#: comentario mentione el criterio. Si alguna vez hace falta, la consulta va con
#: estos estados.
OCUPYING_STATUSES: tuple[str, ...] = ("confirmed", "pending_hold")

MAX_NAME_LENGTH = 160
MAX_DESCRIPTION_LENGTH = 1024

#: Duraciones que un servicio puede tener. No es una lista abierta a
#: proposito: el motor de disponibilidad (Fase 5) cuenta ventanas en
#: pasos de cuarto de hora, y una duracion fuera del set deja slots que
#: no caen en ninguna ventana. Si el negocio necesita otra, se cambia
#: aca -- un solo lugar -- y no en cada formulario.
DURACIONES_PERMITIDAS: tuple[int, ...] = (15, 30, 45, 60, 90, 120)


@dataclass(frozen=True, slots=True)
class ServiceCreate:
    """Datos de alta de un servicio."""

    name: str
    duration_minutes: int
    price: Decimal
    currency: str
    description: str | None = None
    color: str | None = None
    sort_order: int = 0


@dataclass(frozen=True, slots=True)
class CoberturaServicio:
    """Un servicio del negocio y cuantos profesionales pueden hacerlo.

    Es la lectura que necesita el panel para avisarle al dueño que tiene un
    servicio **sin nadie que lo atienda**.
    """

    service_id: uuid.UUID
    nombre: str
    profesionales_elegibles: int


@dataclass(frozen=True, slots=True)
class ServiceUpdate:
    """Cambios parciales de un servicio. Los `None` se ignoran (PATCH)."""

    name: str | None = None
    description: str | None = None
    duration_minutes: int | None = None
    price: Decimal | None = None
    currency: str | None = None
    color: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


def _validar_nombre(nombre: str) -> str:
    limpio = nombre.strip()
    if not limpio:
        raise ValidationError("El nombre del servicio no puede estar vacio.")
    if len(limpio) > MAX_NAME_LENGTH:
        raise ValidationError(f"El nombre no puede pasar de {MAX_NAME_LENGTH} caracteres.")
    return limpio


def _validar_duracion(duracion: int) -> int:
    """La duracion tiene que ser una de `DURACIONES_PERMITIDAS`."""
    if duracion not in DURACIONES_PERMITIDAS:
        raise ValidationError(
            "La duracion debe ser una de "
            + ", ".join(str(d) for d in DURACIONES_PERMITIDAS)
            + " minutos."
        )
    return duracion


async def _siguiente_sort_order(session: AsyncSession, business_id: uuid.UUID) -> int:
    """Proximo `sort_order` al final de la lista.

    Maximo actual mas uno, y no count: con `sort_order` libres, `count` reutiliza
    numeros y el servicio nuevo se inserta en medio de la lista sin que nadie lo
    haya pedido. El panel manda el orden completo igual, asi que el valor por
    defecto solo importa en la creacion desde la API.
    """
    result = await session.execute(
        select(func.coalesce(func.max(Service.sort_order), 0)).where(
            Service.business_id == business_id
        )
    )
    return int(result.scalar_one()) + 1


async def listar_servicios(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    incluir_archivados: bool = False,
    incluir_inactivos: bool = False,
) -> list[Service]:
    """Servicios del negocio, ordenados como los muestra el panel.

    Los archivados se excluyen por defecto. El filtro por `business_id` es
    redundante con la RLS y va igual a proposito: la RLS limita por GUC, el filtro
    limita por intencion, y si alguien llega a esta sesion sin GUC lo que quiere el
    codigo es cero filas, no todas.
    """
    query = select(Service).where(Service.business_id == business_id)
    if not incluir_archivados:
        query = query.where(Service.archived_at.is_(None))
    if not incluir_inactivos:
        query = query.where(Service.is_active.is_(True))
    result = await session.execute(query.order_by(Service.sort_order, Service.name))
    return list(result.scalars())


async def obtener_servicio(
    session: AsyncSession, service_id: uuid.UUID, *, business_id: uuid.UUID
) -> Service:
    """Un servicio del negocio, o `NotFoundError`.

    `business_id` va por parametro y no se deduce de la RLS. Un 404 para un
    servicio de otro tenant es lo correcto -- no se le confirma que existe -- pero
    para que el `business_id` llegue, el router tiene que tomarlo del principal y
    pasarlo explicitamente. Es la unica forma de que el filtro quede a la vista.
    """
    result = await session.execute(
        select(Service).where(Service.id == service_id, Service.business_id == business_id)
    )
    service = result.scalar_one_or_none()
    if service is None:
        raise NotFoundError("Servicio no encontrado.")
    return service


async def profesionales_que_pueden_hacer(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    service_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Los profesionales del negocio que pueden realizar el servicio.

    **Esta es la unica definicion de "puede hacer este servicio".** Antes de que
    existiera, la misma regla estaba copiada en cuatro lugares: el endpoint que
    calcula la disponibilidad, el que crea la reserva, el listado de profesionales
    del panel y el listado publico. Cuatro copias de la misma consulta es cuatro
    lugares donde puede haber una de mas: la disponibilidad de un profesional
    concreto se ofrezca sin mirar `professional_services`, y la reserva si lo
    miraba. El resultado era un horario que el sistema mostraba y despues
    rechazaba, que es la peor forma de mentir.

    Son tres condiciones, y las tres son de negocio:

    1. **El profesional es del negocio.** Sin esto, un `professional_id` de otro
       tenant entraria por la puerta de atrás.
    2. **Esta activo y no esta archivado.** Un profesional desactivado o archivado
       no atiende, aunque todavia tenga el vinculo con el servicio.
    3. **Tiene el vinculo activo en `professional_services`.** Es lo que dice que
       ese profesional sabe hacer ese trabajo. Un negocio con un peluquero y un
       barbero no ofrece los servicios de uno al otro.

    El `ORDER BY sort_order` es parte del contrato: el indice de esta lista es el
    desempate de la estrategia de asignacion cuando dos profesionales tienen la
    misma carga, asi que el orden tiene que ser estable entre llamadas y no
    depender del plan de ejecucion de PostgreSQL.
    """
    result = await session.execute(
        select(Professional.id)
        .join(
            ProfessionalService,
            (ProfessionalService.professional_id == Professional.id)
            & (ProfessionalService.service_id == service_id),
        )
        .where(
            Professional.business_id == business_id,
            Professional.is_active.is_(True),
            Professional.archived_at.is_(None),
            ProfessionalService.is_active.is_(True),
        )
        .order_by(Professional.sort_order, Professional.id)
    )
    return list(result.scalars())


async def cobertura_de_servicios(
    session: AsyncSession, business_id: uuid.UUID
) -> list[CoberturaServicio]:
    """Cuantos profesionales pueden hacer cada servicio que el negocio ofrece.

    **Es la preventiva del fallo que la regla unica evita.** Un servicio con cero
    profesionales no se puede reservar, aunque el negocio tenga horarios, precios y
    una pagina publica que se ve entera y correcta. El cliente llega, elige un
    servicio y se encuentra con que no hay nadie: el problema no es la agenda, es
    que nadie dijo "este servicio no lo hace nadie".

    El panel usa esto para avisar, y el aviso tiene que ser temprano: en el
    momento de activar el servicio, no cuando un cliente pierde la reserva.

    Se cuentan con los **mismos** criterios que `profesionales_que_pueden_hacer`:
    profesional del negocio, activo, no archivado y con el vinculo activo en
    `professional_services`. La regla esta escrita una sola vez; este numero es
    una forma de mirarla, no otra regla que pueda divergir.
    """
    result = await session.execute(
        select(Service.id, Service.name, func.count(Professional.id))
        .select_from(Service)
        .outerjoin(
            ProfessionalService,
            (ProfessionalService.service_id == Service.id)
            & (ProfessionalService.is_active.is_(True)),
        )
        .outerjoin(
            Professional,
            (Professional.id == ProfessionalService.professional_id)
            & (Professional.business_id == business_id)
            & (Professional.is_active.is_(True))
            & (Professional.archived_at.is_(None)),
        )
        .where(
            Service.business_id == business_id,
            Service.is_active.is_(True),
            Service.archived_at.is_(None),
        )
        .group_by(Service.id, Service.name, Service.sort_order)
        .order_by(Service.sort_order, Service.name)
    )
    return [
        CoberturaServicio(
            service_id=service_id,
            nombre=nombre,
            profesionales_elegibles=int(cantidad),
        )
        for service_id, nombre, cantidad in result.all()
    ]


async def vincular_profesional_con_todos_los_servicios(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    professional_id: uuid.UUID,
) -> None:
    """Asigna a un profesional recien creado todos los servicios del negocio.

    **La asignacion por defecto tiene que existir, y tiene que ser automatica.** Un
    negocio con servicios y profesionales pero sin filas en
    `professional_services` se ve entero y correcto en el panel--horarios,
    precios, gente-- y su pagina publica no ofrece un solo horario. Paso dos veces
    con negocios reales. La causa no era un bug de cuentas: era que "asignarle el
    servicio a un profesional" es un paso del panel **distinto** del alta, y nadie
    lo hace si no sabe que existe.

    Asi que el alta asigna, y el paso manual queda para lo contrario: **destildar**
    al profesional que no hace un servicio. Un negocio chico, que es el caso normal,
    no toca nada y funciona; uno con un barbero y una manicura destilda los dos
    servicios que el otro no hace.

    Son tres decisiones, y las tres estan aca:

    1. **Todos los servicios ofrecidos** (`is_active` y no archivados). Un servicio
       archivado no se reserva, asi que no hay nada que asignar.
    2. **Solo profesionales no archivados** (`archived_at IS NULL`). Archivar es
       "esta persona ya no esta"; no se le siguen asignando cosas.
    3. **Los desactivados si cuentan.** `is_active = False` es "esta semana no
       atiende", no "no existe": el vinculo se crea igual y, cuando lo reactivan,
       el servicio ya lo espera. Si no se creara, reactivar a alguien lo dejaria sin
       poder hacer nada hasta que alguien se acuerde de asignarlo a mano--el mismo
       olvido que esta funcion existe para evitar.

    No hace falta `ON CONFLICT`: se llama en el alta, cuando la fila no puede
    existir. Si algun dia se llama sobre un profesional existente, el `UNIQUE`
    `(professional_id, service_id)` va a fallar y eso es correcto: el reemplazo
    del conjunto es `asignar_servicios`, no esto.
    """
    result = await session.execute(
        select(Service.id).where(
            Service.business_id == business_id,
            Service.is_active.is_(True),
            Service.archived_at.is_(None),
        )
    )
    servicios = list(result.scalars())
    if not servicios:
        return
    session.add_all(
        ProfessionalService(
            business_id=business_id,
            professional_id=professional_id,
            service_id=service_id,
            is_active=True,
        )
        for service_id in servicios
    )
    await session.flush()


async def vincular_servicio_con_todos_los_profesionales(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    service_id: uuid.UUID,
) -> None:
    """Asigna un servicio recien creado a todos los profesionales del negocio.

    Es la misma decision que `vincular_profesional_con_todos_los_servicios`, del
    otro lado: el alta de cualquiera de los dos tiene que dejar el negocio
    funcionando, sin importar en que orden se cargaron las cosas. Un negocio que
    primero crea los servicios y despues los profesionales no puede terminar con la
    pagina publica vacia, y uno que hace lo contrario tampoco.

    Los criterios son los mismos y estan explicados en la funcion de al lado:
    profesionales no archivados (los desactivados si), y solo servicios ofrecidos
    por construccion--el que se esta creando acaba de nacer ofrecido.
    """
    result = await session.execute(
        select(Professional.id).where(
            Professional.business_id == business_id,
            Professional.archived_at.is_(None),
        )
    )
    profesionales = list(result.scalars())
    if not profesionales:
        return
    session.add_all(
        ProfessionalService(
            business_id=business_id,
            professional_id=professional_id,
            service_id=service_id,
            is_active=True,
        )
        for professional_id in profesionales
    )
    await session.flush()


async def crear_servicio(
    session: AsyncSession, business_id: uuid.UUID, datos: ServiceCreate
) -> Service:
    """Crea un servicio y devuelve el registro ya persistido.

    `flush()` y no `commit()`: el commit es de la dependencia HTTP. Lo mismo que en
    el resto del servicio, pero aqui importa mas porque el `id` se devuelve en la
    respuesta y sin `flush` no habria id.

    **El servicio nace asignado a todos los profesionales del negocio** (ver
    `vincular_servicio_con_todos_los_profesionales`). Sin eso, un servicio nuevo
    aparece en la pagina publica y no lo puede reservar nadie, y el dueño no tiene
    forma de saber que le falta un paso.
    """
    nombre = _validar_nombre(datos.name)
    duracion = _validar_duracion(datos.duration_minutes)
    if datos.price < 0:
        raise ValidationError("El precio no puede ser negativo.")

    service = Service(
        business_id=business_id,
        name=nombre,
        description=datos.description,
        duration_minutes=duracion,
        price=datos.price,
        currency=datos.currency.strip().upper(),
        color=datos.color,
        is_active=True,
        sort_order=datos.sort_order or await _siguiente_sort_order(session, business_id),
    )
    session.add(service)
    await session.flush()
    await vincular_servicio_con_todos_los_profesionales(
        session, business_id=business_id, service_id=service.id
    )
    return service


async def actualizar_servicio(
    session: AsyncSession,
    service_id: uuid.UUID,
    business_id: uuid.UUID,
    cambios: ServiceUpdate,
) -> Service:
    """Aplica cambios parciales. Los campos `None` se dejan como estan."""
    service = await obtener_servicio(session, service_id, business_id=business_id)

    if cambios.name is not None:
        service.name = _validar_nombre(cambios.name)
    if cambios.description is not None:
        service.description = cambios.description
    if cambios.color is not None:
        service.color = cambios.color
    if cambios.is_active is not None:
        service.is_active = cambios.is_active
    if cambios.sort_order is not None:
        service.sort_order = cambios.sort_order
    if cambios.currency is not None:
        service.currency = cambios.currency.strip().upper()
    if cambios.duration_minutes is not None:
        # La duracion no se cambia de Reservas hechas: `bookings.duration_minutes`
        # es un snapshot y el `bookings.ends_at` ya esta calculado. Cambiar el
        # servicio no puede reescribir el pasado; el admin decide reprogramar.
        service.duration_minutes = _validar_duracion(cambios.duration_minutes)
    if cambios.price is not None:
        if cambios.price < 0:
            raise ValidationError("El precio no puede ser negativo.")
        service.price = cambios.price

    await session.flush()
    return service


async def archivar_servicio(
    session: AsyncSession, service_id: uuid.UUID, business_id: uuid.UUID
) -> Service:
    """Archiva un servicio. Idempotente.

    Archivar un servicio ya archivado no es error: es el resultado que el cliente
    quiere y repetir el click no deberia cambiar la respuesta.
    """
    service = await obtener_servicio(session, service_id, business_id=business_id)
    if service.archived_at is None:
        service.archived_at = now()
        # Desactivar ademas es parte del archivado. Un servicio archivado que queda
        # `is_active=True` desaparece del panel y sigue vivo en la API: el
        # cliente ve que lo borro y sigue apareciendo en un listado.
        service.is_active = False
        await session.flush()
    return service


__all__ = [
    "DURACIONES_PERMITIDAS",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_NAME_LENGTH",
    "OCUPYING_STATUSES",
    "CoberturaServicio",
    "ServiceCreate",
    "ServiceUpdate",
    "actualizar_servicio",
    "archivar_servicio",
    "cobertura_de_servicios",
    "crear_servicio",
    "listar_servicios",
    "obtener_servicio",
    "profesionales_que_pueden_hacer",
    "vincular_profesional_con_todos_los_servicios",
    "vincular_servicio_con_todos_los_profesionales",
]
