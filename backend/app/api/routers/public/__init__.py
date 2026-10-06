"""Router público: endpoints de disponibilidad y reserva sin autenticación.

Estos endpoints NO requieren autenticación. El cliente final (sin cuenta)
los usa para:
1. Ver servicios y profesionales de un negocio.
2. Calcular disponibilidad.
3. Crear una reserva.
4. Gestionar una reserva (ver, cancelar, reprogramar) vía secure token.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_settings_dep,
    limit_public_per_ip,
)
from app.api.routers.public.schemas import (
    AvailabilityDetailResponse,
    AvailabilityRequest,
    AvailabilityResponse,
    AvailabilitySlot,
    AvailabilitySlotCandidatos,
    BookingCreateRequest,
    BookingResponse,
    BusinessPublicInfo,
    ProfessionalPublicInfo,
    RescheduleRequest,
    ServicePublicInfo,
)
from app.core.config import Settings
from app.core.rate_limit import (
    WINDOW_HOUR,
    WINDOW_MINUTE,
    client_ip,
    enforce_rate_limit,
    rate_limit_key,
)
from app.core.security import hash_token_bytes
from app.core.time import now
from app.db.session import session_scope, tenant_session
from app.modules.availability.service import get_availability
from app.modules.bookings.models import Booking
from app.modules.bookings.service import (
    ReglasAgenda,
    cancel_booking,
    create_booking,
    reschedule_booking,
)
from app.modules.businesses.models import Business
from app.modules.customers.models import Customer
from app.modules.professionals.models import Professional
from app.modules.services.models import ProfessionalService, Service

#: Techo general de la superficie publica: 120/min por IP (§10.5). El endpoint de
#: reservas cuelga de este router **y** trae su propio limite, mas ajustado; los dos
#: cuentan y no se pisan porque cada uno tiene su cubo.
router = APIRouter(
    prefix="/public",
    tags=["public"],
    dependencies=[Depends(limit_public_per_ip)],
)


def _telefono_para_la_clave(telefono: str) -> str:
    """Telefono normalizado para armar el cubo del rate limit.

    **Solo para la clave.** Lo que se guarda en `customers.phone_e164` es lo que
    mande el cliente, sin tocar: cambiarlo aca seria cambiar el producto.

    La normalizacion es la misma razon por la que `normalizar_email` existe en el
    modulo de auth: si `+5491100009999`, `5491100009999` y `+54 911 0000 9999` dan
    tres claves distintas, el limite de "5 por telefono" se esquiva cambiando un
    espacio. Un limitador que se esquiva con el formato no limita.
    """
    return "".join(ch for ch in telefono if ch.isdigit()) or telefono


async def limitar_reserva(*, telefono: str, ip: str, settings: Settings) -> None:
    """10/min por IP y 5/hora por telefono en `POST /public/bookings`.

    Son dos cubos porque los dos ataques son distintos y un limite no frena a los dos:

    - **Por IP** es el volumen bruto: alguien que programa reservas y las borra.
    - **Por telefono** es el que cuesta plata. Es lo que frena a alguien que pide que
      le manden el recordatorio a un numero que no es suyo, y por eso la ventana es
      de una **hora** y no de un minuto: el mensaje de WhatsApp se paga una vez
      enviado, no una vez reservado, asi que el limite tiene que cubrir la hora en la
      que el costo se acumula. Con 5 por minuto bastaba esperar un minuto y seguir.

    **Se llama desde el handler y no es una dependencia de FastAPI, a proposito.** La
    version como dependencia declaraba `reserva: BookingCreateRequest`, y el handler
    declara lo mismo. Con dos parametros de cuerpo, FastAPI deja de tratar el cuerpo
    como el modelo entero y pasa a esperar un objeto con un campo por parametro: el
    endpoint respondia 422 con `body.reserva: Field required` a **cualquier** cliente
    que mandara un cuerpo de reserva bien formado. No se rompia solo el limite: se
    rompia la reserva.

    Llamandolo desde el handler, el cuerpo llega validado--con lo que la clave del
    cubo sale de datos que ya pasaron por el esquema-- y el limite queda igual de
    arriba de todo: es la primera sentencia del handler, antes de buscar el negocio,
    el servicio o la agenda.
    """
    if not settings.rate_limit_enabled:
        return

    # IP antes que telefono, siempre. Con dos cubos y orden variable, dos peticiones
    # concurrentes que los tomaran al reves se trabarian esperando la fila una de la
    # otra.
    await enforce_rate_limit(
        key=rate_limit_key("booking:ip", ip),
        limit=settings.rate_limit_public_booking_per_ip,
        window_seconds=WINDOW_MINUTE,
        scope="booking:ip",
    )
    await enforce_rate_limit(
        key=rate_limit_key("booking:telefono", _telefono_para_la_clave(telefono)),
        limit=settings.rate_limit_public_booking_per_phone_hourly,
        window_seconds=WINDOW_HOUR,
        scope="booking:telefono",
    )


async def _resolve_business_by_slug(session: AsyncSession, slug: str) -> Business | None:
    """Negocio activo por slug, o `None`.

    `businesses` es una tabla global sin RLS, asi que se puede leer sin contexto
    de tenant. Por eso la busqueda va en una sesion propia y no en la del
    endpoint: el `business_id` todavia no se conoce cuando hay que buscarlo.
    """
    result = await session.execute(
        select(Business).where(Business.slug == slug, Business.status == "active")
    )
    return result.scalar_one_or_none()


@asynccontextmanager
async def _public_session(
    slug: str,
) -> AsyncIterator[tuple[AsyncSession, Business]]:
    """Sesion con tenant para un endpoint publico, resuelta por slug.

     Son dos fases y el orden no es negociable:

     1. `session_scope()` resuelve el negocio por slug. Sin RLS, sin tenant.
     2. `tenant_session(business.id)` para todo lo demas. Aca si hace falta el
        `SET LOCAL`, y hace falta antes de la primera lectura de cualquier tabla
        de tenant.

     El sintoma de saltarse la fase 2 no es un error de permisos: es un `[]` o un
     404, porque la politica de RLS compara contra un GUC vacio y no deja pasar
     ninguna fila.

     **Por que `@asynccontextmanager` y no un generador plano con `async for`.**
     Un generador async recorrido con `async for` y abandonado con un `return`
     adentro del loop queda *suspendido en el `yield`*: nadie ejecuta lo que hay
     despues. Con `session.begin()` en el `__aexit__`, eso significa que el COMMIT
     no corre, la reserva se pierde, y el endpoint devuelve 201 con los datos que
    lviene de leer de la fila en memoria.

     Es el peor tipo de bug posible en este lugar: la respuesta dice que la reserva
     existe, el cliente guarda el enlace `/r/{token}`, y al abrirlo da 404 porque
     nunca hubo fila. Tarda en aparecer --solo cuando alguien usa el token--, y
     no parece relacionado con la reserva que se acaba de crear.

     Con `async with`, el `__aexit__` corre siempre y en orden, incluso con un
     `return` en el medio del bloque, asi que el commit esta garantizado antes de
     que el handler devuelva algo.

     **Las dos fases van en serie, no anidadas.** La version anterior metia el
     `tenant_session` adentro del `session_scope`, y eso dejaba la transaccion del
     resolutor abierta durante toda la request: `pg_stat_activity` la mostraba como
     `idle in transaction` con minutos de antiguedad. Tres consecuencias:

     1. Cada request publico retenia **dos** conexiones del pool en vez de una, y
        el pool es el recurso que se agota primero cuando todo va bien.
     2. Esa transaccion abierta fija una foto del catalogo. Una reserva creada
        despues no la ve, asi que el siguiente request puede leer un estado
        anterior del negocio.
     3. `pool_size` es 5 y `max_overflow` 10, o sea 15 conexiones. Con dos por
        request, ocho requests publicos simultaneos--algo normal un lunes a la
        manana-- agotan el pool y las requests empiezan a hacer timeout de 30s
        esperando. No es un problema de codigo de negocio: es el pool entero
        esperando.

     Cerrar el resolutor antes de abrir la sesion de tenant deja una sola conexion
     viva por request y elimina las tres.
    """
    async with session_scope() as resolver_session:
        business = await _resolve_business_by_slug(resolver_session, slug)

    # Fuera del `with` a proposito: la 404 tiene que propagarse sin haber abierto
    # una sesion de tenant para un negocio que no existe.
    if business is None:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    async with tenant_session(business.id) as session:
        yield session, business


@router.get(
    "/businesses/{slug}",
    response_model=BusinessPublicInfo,
    status_code=status.HTTP_200_OK,
    summary="Información pública de un negocio",
    responses={
        200: {"description": "Negocio encontrado"},
        404: {"description": "Negocio no encontrado"},
    },
)
async def get_business_public_info(
    slug: str,
) -> BusinessPublicInfo:
    """Devuelve la información pública de un negocio para su página de reserva."""
    async with session_scope() as session:
        business = await _resolve_business_by_slug(session, slug)
        if business is None:
            raise HTTPException(status_code=404, detail="Negocio no encontrado")

    return BusinessPublicInfo(
        id=business.id,
        name=business.name,
        description=business.description,
        timezone=business.timezone,
        locale=business.locale,
        currency=business.currency,
        slot_interval_minutes=business.slot_interval_minutes,
        min_lead_minutes=business.min_lead_minutes,
        brand_color=business.brand_color,
    )


@router.get(
    "/businesses/{slug}/services",
    response_model=list[ServicePublicInfo],
    status_code=status.HTTP_200_OK,
    summary="Servicios activos de un negocio",
)
async def list_business_services(
    slug: str,
) -> list[ServicePublicInfo]:
    """Lista los servicios activos de un negocio."""
    async with _public_session(slug) as (session, _business):
        services_result = await session.execute(
            select(Service)
            .where(
                Service.business_id == _business.id,
                Service.is_active.is_(True),
                Service.archived_at.is_(None),
            )
            .order_by(Service.sort_order)
        )
        return [
            ServicePublicInfo(
                id=s.id,
                name=s.name,
                description=s.description,
                duration_minutes=s.duration_minutes,
                price=s.price,
                currency=s.currency,
            )
            for s in services_result.scalars()
        ]


@router.get(
    "/businesses/{slug}/professionals",
    response_model=list[ProfessionalPublicInfo],
    status_code=status.HTTP_200_OK,
    summary="Profesionales activos de un negocio",
)
async def list_business_professionals(
    slug: str,
    service_id: UUID | None = Query(None, description="Filtrar por servicio"),
) -> list[ProfessionalPublicInfo]:
    """Lista los profesionales activos de un negocio.

    Si se pasa `service_id`, solo devuelve profesionales que pueden realizar
    ese servicio.
    """
    async with _public_session(slug) as (session, business):
        query = (
            select(Professional)
            .where(
                Professional.business_id == business.id,
                Professional.is_active.is_(True),
                Professional.archived_at.is_(None),
            )
            .order_by(Professional.sort_order)
        )

        if service_id:
            query = query.join(
                ProfessionalService,
                (ProfessionalService.professional_id == Professional.id)
                & (ProfessionalService.service_id == service_id)
                & (ProfessionalService.is_active.is_(True)),
            )

        prof_result = await session.execute(query)
        return [
            ProfessionalPublicInfo(
                id=p.id,
                display_name=p.display_name,
                bio=p.bio,
                color=p.color,
            )
            for p in prof_result.scalars()
        ]


@router.post(
    "/businesses/{slug}/availability",
    response_model=AvailabilityResponse,
    status_code=status.HTTP_200_OK,
    summary="Calcular disponibilidad",
    responses={
        200: {"description": "Disponibilidad calculada"},
        404: {"description": "Negocio o servicio no encontrado"},
    },
)
async def calculate_availability(
    slug: str,
    request: AvailabilityRequest,
) -> AvailabilityResponse:
    """Calcula los slots disponibles para un servicio en una fecha dada.

    Si `professional_id` es None, calcula para "cualquier profesional": la
    devuelve como slots anotados con el profesional asignado, para que el cliente
    pueda elegir el horario sin saber de antemano a quien le toca.
    """
    async with _public_session(slug) as (session, business):
        # Resolver servicio
        service_result = await session.execute(
            select(Service).where(
                Service.id == request.service_id,
                Service.business_id == business.id,
                Service.is_active.is_(True),
            )
        )
        service = service_result.scalar_one_or_none()
        if not service:
            raise HTTPException(status_code=404, detail="Servicio no encontrado")

        # Calcular disponibilidad
        slots = await get_availability(
            session,
            business_id=str(business.id),
            professional_id=str(request.professional_id) if request.professional_id else None,
            service_id=str(service.id),
            service_duration_minutes=service.duration_minutes,
            slot_interval_minutes=business.slot_interval_minutes,
            local_date=request.date,
            timezone=business.timezone,
            min_lead_minutes=business.min_lead_minutes,
            current_time=now(),
        )

        return AvailabilityResponse(
            slots=[
                AvailabilitySlot(
                    starts_at=slot.starts_at,
                    ends_at=slot.ends_at,
                    # El primero de los candidatos, que es a quien **le tocaría**
                    # ese horario segun la estrategia del negocio (menos carga del
                    # dia). Antes este campo era `None` siempre que el cliente
                    # pedia "cualquier profesional", y el schema de al lado declara
                    # que deberia viajar en cada slot justamente para eso. Sin el,
                    # el cliente ve "las 09:00" sin saber de quien son y reserva a
                    # ciegas; el backend lo sabe, y el contrato dice que se lo
                    # digamos.
                    #
                    # Sigue siendo solo una *recomendacion*: el cliente puede
                    # mandar `professional_id: null` al reservar y el backend vuelve
                    # a elegir en ese momento, contra el mismo motor, con la EXCLUDE
                    # como autoridad final. Esta es la diferencia entre "se lo
                    # sugerimos" y "queda fijado", y acá esta en el medio.
                    professional_id=slot.candidatos[0] if slot.candidatos else None,
                )
                for slot in slots
            ]
        )


@router.get(
    "/businesses/{slug}/availability",
    response_model=AvailabilityDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Calcular disponibilidad",
    responses={
        200: {"description": "Disponibilidad calculada"},
        404: {"description": "Negocio o servicio no encontrado"},
    },
)
async def get_availability_public(
    slug: str,
    service_id: UUID = Query(..., description="Servicio a reservar"),
    date: dt.date = Query(..., description="Fecha local del negocio (YYYY-MM-DD)"),
    professional_id: UUID | None = Query(
        None,
        description="Profesional concreto, o ausente para 'cualquier profesional'",
    ),
) -> AvailabilityDetailResponse:
    """Calcula los slots disponibles para un servicio en una fecha.

    Es la misma cuenta que el POST de al lado, por query params en
    vez de por cuerpo: un GET es cacheable por el navegador y por
    un proxy, y la disponibilidad es lectura pura. Los dos conviven
    a proposito--el POST ya lo usan clientes que mandan el cuerpo,
    y sacarlo los romperia.

    Si `professional_id` **no** viene, la cuenta es para "cualquier
    profesional": los slots se anotan con `candidatos`, la lista
    completa de quienes pueden atenderlo **ordenada por la
    estrategia** (menos carga del dia primero, desempate por
    `sort_order`). Si no hay profesionales elegibles para el
    servicio, la respuesta es una lista vacia y no un 404: no es
    un error de la peticion, es que ese servicio nadie lo puede
    atender hoy.

    La logica de disponibilidad **no vive aca**. Este handler solo
    resuelve el negocio por slug y el servicio por id--los dos 404
    con los que arranca el contrato--y delega en
    `app.modules.availability.service.get_availability`, que es la
    unica que sabe de ventanas, reservas y bloqueos.
    """
    async with _public_session(slug) as (session, business):
        service_result = await session.execute(
            select(Service).where(
                Service.id == service_id,
                Service.business_id == business.id,
                Service.is_active.is_(True),
            )
        )
        service = service_result.scalar_one_or_none()
        if not service:
            raise HTTPException(status_code=404, detail="Servicio no encontrado")

        slots = await get_availability(
            session,
            business_id=str(business.id),
            professional_id=str(professional_id) if professional_id else None,
            service_duration_minutes=service.duration_minutes,
            # El intervalo de grilla es del **negocio**, no del
            # servicio: es el negocio el que define cada cuantos
            # minutos ofrece horarios. El `or 15` es defensa--la
            # columna es NOT NULL con default 15 y el CHECK la
            # mantiene mayor que cero, asi que en la practica nunca
            # se activa.
            slot_interval_minutes=business.slot_interval_minutes or 15,
            local_date=date,
            timezone=business.timezone,
            service_id=str(service.id),
            min_lead_minutes=business.min_lead_minutes or 60,
            current_time=now(),
        )

        return AvailabilityDetailResponse(
            business_id=business.id,
            service_id=service.id,
            date=date,
            slots=[
                AvailabilitySlotCandidatos(
                    starts_at=slot.starts_at,
                    ends_at=slot.ends_at,
                    # La lista completa, no el primero: `candidatos`
                    # ya viene ordenada por la estrategia desde el
                    # servicio, y es lo que el cliente muestra.
                    candidatos=list(slot.candidatos),
                )
                for slot in slots
            ],
        )


@router.post(
    "/bookings",
    response_model=BookingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una reserva",
    responses={
        201: {"description": "Reserva creada"},
        400: {"description": "Datos inválidos"},
        404: {"description": "Negocio o servicio no encontrado"},
        409: {"description": "Conflicto de idempotencia o slot no disponible"},
        429: {"description": "Demasiadas reservas desde esta IP o para este teléfono"},
    },
)
async def create_booking_endpoint(
    request: BookingCreateRequest,
    http_request: Request,
    settings: Settings = Depends(get_settings_dep),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> BookingResponse:
    """Crea una reserva de forma transaccional.

    - Revalida el horario contra el motor de disponibilidad (§8.9).
    - Asigna un profesional si no se especifica, de entre los que están libres.
    - Genera un secure token para gestionar la reserva.
    - Idempotencia vía header `Idempotency-Key`.

    La clave de idempotencia se acepta por header y por body. El header es el
    contrato de `ADR-0008` y es lo que manda; el body se acepta porque
    `docs/api.md` lo documenta y los clientes que ya lo usan no se rompen. Si
    vienen las dos y son distintas, manda el header: es el que el cliente no
    genera por accidente.
    """
    # Lo primero del handler, antes de tocar la base: el techo de 10/min por IP y
    # 5/hora por telefono (§10.5). Si se bajara de aca, seguirian valiendo el RLS y
    # las reglas de negocio, pero el limite de este endpoint pasaria a depender de que
    # uno se acuerde de la linea.
    await limitar_reserva(
        telefono=request.customer_phone_e164,
        ip=client_ip(http_request),
        settings=settings,
    )

    effective_key = idempotency_key or request.idempotency_key

    async with _public_session(request.slug) as (session, business):
        # Resolver servicio
        service_result = await session.execute(
            select(Service).where(
                Service.id == request.service_id,
                Service.business_id == business.id,
                Service.is_active.is_(True),
            )
        )
        service = service_result.scalar_one_or_none()
        if not service:
            raise HTTPException(status_code=404, detail="Servicio no encontrado")

        # `agenda` es lo que activa la revalidacion contra el motor. Sin ella,
        # `create_booking` se limita a la EXCLUDE, que evita el solapamiento con
        # otras reservas pero no mira el calendario: aceptaria un domingo con el
        # negocio cerrado, un feriado, o las tres de la manana. Este endpoint es el
        # unico que recibe entrada sin autenticar, asi que es el unico que no puede
        # confiar en que el horario venga de una pantalla que el backend genero.
        agenda = ReglasAgenda.de_negocio(business)

        # El profesional viene del slot que el cliente eligio. En "cualquier
        # profesional" llega `None` y el service elige entre los que pueden.
        try:
            booking_result = await create_booking(
                session,
                business_id=business.id,
                service_id=service.id,
                professional_id=request.professional_id,
                customer_first_name=request.customer_first_name,
                customer_last_name=request.customer_last_name,
                customer_phone_e164=request.customer_phone_e164,
                starts_at=request.starts_at,
                ends_at=request.ends_at,
                duration_minutes=service.duration_minutes,
                price=service.price,
                currency=service.currency,
                local_date=request.local_date,
                agenda=agenda,
                idempotency_key=effective_key,
            )
        except ValueError as e:
            # Todo `ValueError` de este camino --horario tomado, profesional que no
            # hace el servicio, clave de idempotencia reusada-- es 409 y no 400: el
            # pedido del cliente era valido, lo que cambio es el mundo. Un cliente
            # bien escrito reintenta ante un 409 y aborta ante un 400.
            raise HTTPException(status_code=409, detail=str(e)) from e

        # Se devuelve la misma respuesta que el GET por token, no una recortada.
        # Armar el `BookingResponse` a mano con seis campos dejaba en `null` el
        # nombre del servicio, del profesional y del negocio, el precio y la fecha
        # local: el cliente recien al reservar --el momento en que mira la pantalla
        # de confirmacion-- era el unico que veia la version incompleta. La fila
        # recien creada viene en el resultado, asi que no hace falta volver a
        # consultarla.
        if booking_result.booking is None:
            # Solo llega en el replay de una fila de idempotencia anterior a que
            # existiera `response_body`. No hay token que devolver y tampoco se
            # puede reconstruir la respuesta completa, asi que se dice en vez de
            # devolver un 201 con un token vacio que el cliente guardaria como
            # enlace de gestion y no abriria nunca.
            raise HTTPException(
                status_code=409,
                detail=(
                    "Esta reserva ya se habia creado pero no se pudo devolver el "
                    "enlace de gestion. Contacta al negocio."
                ),
            )

        return await _booking_response(
            session,
            booking_result.booking,
            booking_result.secure_token,
        )


@asynccontextmanager
async def _booking_session(token: str) -> AsyncIterator[tuple[AsyncSession, Booking]]:
    """Sesion con tenant para gestionar una reserva por token.

    Tres pasos, en este orden:

    1. `booking_tenant_for_token` (SECURITY DEFINER) resuelve el `business_id` a
       partir del hash. Sin este paso no hay forma de saber que GUC poner, y sin
       GUC la RLS devuelve cero filas.
    2. `tenant_session(...)` con ese `business_id`, que es la sesion que usa todo
       lo demas.
    3. La lectura de la reserva con la RLS ya activa.

    Los pasos 1 y 2 son consultas separadas a proposito: la funcion devuelve el
    tenant, y poner el GUC lo hace el codigo. Si la funcion lo pusiera, el valor
    sobrevive hasta el commit y la siguiente peticion del pool hereda el tenant.

    `@asynccontextmanager` por el mismo motivo que `_public_session`: cancelar y
    reprogramar escriben, y su commit tiene que correr antes de que el handler
    devuelva. Con `async for` + `return`, la escritura se perderia en silencio.
    """
    token_hash = hash_token_bytes(token)

    async with session_scope() as resolver_session:
        resolved = await resolver_session.execute(
            text("SELECT business_id, booking_id FROM booking_tenant_for_token(:h)"),
            {"h": token_hash},
        )
        row = resolved.one_or_none()

    if row is None:
        # Mismo 404 que "no existe": no se distingue entre token invalido y
        # reserva de otro negocio, porque esa distincion confirma la existencia.
        raise HTTPException(status_code=404, detail="Reserva no encontrada")

    business_id, booking_id = row

    async with tenant_session(business_id) as session:
        found = await session.execute(
            select(Booking).where(
                Booking.id == booking_id,
                Booking.secure_token_hash == token_hash,
            )
        )
        booking = found.scalar_one_or_none()
        if booking is None:
            raise HTTPException(status_code=404, detail="Reserva no encontrada")
        yield session, booking


@router.get(
    "/bookings/{token}",
    response_model=BookingResponse,
    status_code=status.HTTP_200_OK,
    summary="Ver reserva por secure token",
    responses={
        200: {"description": "Reserva encontrada"},
        404: {"description": "Reserva no encontrada"},
    },
)
async def get_booking_by_token(token: str) -> BookingResponse:
    """Devuelve la información de una reserva dado su secure token.

    El token es el único identificador que el cliente necesita. No se expone
    el booking_id internamente.
    """
    async with _booking_session(token) as (session, booking):
        return await _booking_response(session, booking, token)


async def _booking_response(session: AsyncSession, booking: Booking, token: str) -> BookingResponse:
    """Arma la respuesta de una reserva con lo que el cliente puede ver.

    Deliberadamente **no** incluye email ni telefono del cliente. El README lo
    fija como requisito, y la razon concreta es que esta respuesta viaja a
    `/r/{token}`, una URL que el cliente puede compartir, reenviar o dejar en el
    historial del navegador. Solo nombre.

    **Las cuatro consultas son secuenciales y no un `asyncio.gather`.** Se podría
    leer como una optimizacion obvia y es un bug esperando: una `AsyncSession` de
    SQLAlchemy no admite operaciones concurrentes sobre la misma sesion.
    """
    # Una `AsyncSession` de SQLAlchemy no admite operaciones concurrentes sobre la
    # misma sesion: `gather` con cuatro `session.execute` sobre la misma instancia
    # compite por el mismo cursor de asyncpg y revienta con
    # `InterfaceError: another operation is in progress`. El paralelismo real
    # --varias sesiones-- no aplica en una request autenticada por token, donde
    # todas las lecturas dependen del mismo GUC de tenant. Cuatro `SELECT` por
    # indice son ruido comparado con una reserva.
    service_result = await session.execute(select(Service).where(Service.id == booking.service_id))
    service = service_result.scalar_one_or_none()

    prof_result = await session.execute(
        select(Professional).where(Professional.id == booking.professional_id)
    )
    professional = prof_result.scalar_one_or_none()

    biz_result = await session.execute(select(Business).where(Business.id == booking.business_id))
    business = biz_result.scalar_one_or_none()

    # Esta consulta es la que faltaba. `customer_first_name` estaba declarado en
    # `BookingResponse`, el frontend lo pintaba en la pantalla de gestion, y
    # ningun camino lo llenaba: el nombre del propio cliente salia `null` al
    # confirmar, que es el unico momento en que le interesa.
    cust_result = await session.execute(select(Customer).where(Customer.id == booking.customer_id))
    customer = cust_result.scalar_one_or_none()

    return BookingResponse(
        booking_id=booking.id,
        secure_token=token,
        starts_at=booking.starts_at,
        ends_at=booking.ends_at,
        professional_id=booking.professional_id,
        status=str(booking.status),
        service_name=service.name if service else None,
        professional_name=professional.display_name if professional else None,
        # Solo el nombre, nunca email ni telefono: la misma regla que aplica al
        # resto de la respuesta, y el motivo es el mismo --esta URL se comparte.
        customer_first_name=customer.first_name if customer else None,
        local_date=booking.local_date,
        price=booking.price_snapshot,
        currency=booking.currency,
        cancelled_at=booking.cancelled_at,
        business_name=business.name if business else None,
        business_phone=business.phone_e164 if business else None,
        business_slug=business.slug if business else None,
    )


@router.post(
    "/bookings/{token}/cancel",
    response_model=BookingResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancelar reserva",
    responses={
        200: {"description": "Reserva cancelada"},
        404: {"description": "Reserva no encontrada"},
        409: {"description": "La reserva ya está cancelada"},
    },
)
async def cancel_booking_endpoint(
    token: str,
) -> BookingResponse:
    """Cancela una reserva dado su secure token.

    El horario se libera automáticamente (la restricción EXCLUDE solo aplica
    a estados 'confirmed' y 'pending_hold').
    """
    async with _booking_session(token) as (session, booking):
        # La ventana de cancelacion la define el negocio, no un valor fijo.
        # Leerla de `businesses` es lo que hace que un negocio con 24 horas de
        # antelacion acepte una cancelacion tardia que otro rechazaria.
        biz_result = await session.execute(
            select(Business).where(Business.id == booking.business_id)
        )
        business = biz_result.scalar_one_or_none()
        cancellation_window = business.cancellation_window_minutes if business else 120

        try:
            await cancel_booking(
                session,
                booking_id=booking.id,
                secure_token=token,
                cancellation_window_minutes=cancellation_window,
            )
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

        return await _booking_response(session, booking, token)
    raise HTTPException(status_code=404, detail="Reserva no encontrada")


@router.post(
    "/bookings/{token}/reschedule",
    response_model=BookingResponse,
    status_code=status.HTTP_200_OK,
    summary="Reprogramar reserva",
    responses={
        200: {"description": "Reserva reprogramada"},
        404: {"description": "Reserva no encontrada"},
        409: {"description": "Conflicto de horario"},
    },
)
async def reschedule_booking_endpoint(
    token: str,
    payload: RescheduleRequest,
) -> BookingResponse:
    """Reprograma una reserva dado su secure token.

    La restricción EXCLUDE garantiza que no se solape con otras reservas.

    Los tres parametros del cuerpo, no query params: van anotados como body para
    que `docs/api.md` y el codigo digan lo mismo. Antes eran parametros sueltos
    sin `Query()` ni `Body()`, que FastAPI interpretaba como query obligatorios,
    asi que un cliente que seguia la documentacion y mandaba JSON recibia 422.
    """
    async with _booking_session(token) as (session, booking):
        # El timezone del negocio, no UTC: `local_date` se recalcula con el y
        # con UTC un turno de las 22:00 en Buenos Aires se archiva en el dia
        # siguiente. `businesses` es global, asi que se lee sin depender del
        # GUC de tenant.
        biz = await session.execute(select(Business).where(Business.id == booking.business_id))
        business = biz.scalar_one_or_none()

        try:
            await reschedule_booking(
                session,
                booking_id=booking.id,
                secure_token=token,
                new_starts_at=payload.new_starts_at,
                new_ends_at=payload.new_ends_at,
                new_duration_minutes=payload.new_duration_minutes,
                timezone=business.timezone if business else "UTC",
            )
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

        return await _booking_response(session, booking, token)


__all__ = ["router"]
