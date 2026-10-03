"""Operaciones de reservas desde el panel: consulta, estado y walk-in.

Va en un archivo aparte de `service.py` y no por tamano, sino porque **los dos
modulos tienen modelos de seguridad distintos** y mezclarlos invita a que uno
herede la seguridad del otro:

- `service.py` (publico) autoriza con el `secure_token`. Quien tiene el token
  puede ver y cambiar esa reserva y nada mas. Es el cliente sin cuenta.
- `admin.py` (este) autoriza con el `Principal` y sus scopes. Puede ver **todas** las
  reservas del tenant, no una sola.

Si vivieran juntos, la proxima funcion de admin escrita sobre `service.py` tending
a apoyarse en el token --porque ahi es lo natural-- o un endpoint publico
terminaria aceptando un `booking_id` sin comprobar el token. Son dos errores
opuestos y el mismo descuido los habilita. Estar en archivos distintos hace que la
diferencia se lea antes de escribir la funcion.

Sobre el walk-in (`PROJECT_MASTER` §23): se registra como una reserva mas, con
`source='walkin'`, y por lo tanto **ocupa horario y dispara recordatorios**. La
alternativa --un tipo de evento aparte-- dejaria la agenda incompleta y obligaria a
cada consulta del panel a excluir a los walk-ins. Se distinguen en `source` para
poder filtrarlos, no para esconderlos del resto del sistema.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ConflictError, NotFoundError, ValidationError
from app.core.time import now
from app.models.enums import AuditActorType, BookingEventType, BookingSource, BookingStatus
from app.modules.bookings.models import Booking, BookingEvent
from app.modules.bookings.service import (
    ReglasAgenda,
    _assert_cancellable,
    _cancel_pending_notifications,
    create_booking,
)
from app.modules.businesses.models import Business
from app.modules.customers.models import Customer
from app.modules.professionals.models import Professional
from app.modules.services.models import Service

#: Estados desde los que se puede cerrar una reserva. Son los unicos que el panel
#: puede llevar a `completed` o `no_show`. Una reserva ya cerrada no se cierra de
#: nuevo: se listan los estados validos en vez de excluir los invalidos, porque un
#: estado nuevo agregado al enum tiene que quedar del lado seguro por defecto.
CERRABLES: tuple[str, ...] = (BookingStatus.CONFIRMED,)

#: Tope del paginado. Sin tope, un cliente puede pedir `?limite=100000` y la base
#: tiene que materializar toda la tabla antes de descartarla. El panel pagina de a
#: paginas de 50-100, asi que 200 cubre de sobra cualquier vista real.
MAX_LIMITE = 200
MAX_OFFSET = 100_000


@dataclass(frozen=True, slots=True)
class ReservaFiltros:
    """Filtros del listado de reservas del panel."""

    desde: dt.date | None = None
    hasta: dt.date | None = None
    professional_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    estado: BookingStatus | None = None
    solo_pendientes: bool = False
    limite: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class ReservaDetalle:
    """Una reserva con lo que el panel necesita mostrar en una linea.

    Los nombres vienen del `LEFT JOIN` y no de consultas aparte por fila. La version
    ingenua --consultar servicio, profesional y cliente por cada fila-- es la razon
    por la que un listado de 50 reservas tarda medio segundo: 150 consultas en vez
    de una.
    """

    booking: Booking
    servicio_nombre: str | None
    profesional_nombre: str | None
    cliente_nombre: str | None
    cliente_telefono: str | None


def _aplicar_filtros(consulta: Select, business_id: uuid.UUID, f: ReservaFiltros) -> Select:
    """Aplica los filtros de listado sobre una consulta de `bookings`.

     `desde`/`hasta` son **fechas locales del negocio**, no UTC. Un filtro por fecha
     que se ejecutara en UTC dejaria afuera el turno de las 22:00 del dia que se
     pidio: en Buenos Aires son las 01:00 del dia siguiente. Es el mismo bug R-03 que
    -documenta `local_date`-- y por eso el filtro va por `bookings.local_date`, que
     ya esta calculada con el timezone del negocio, en vez de por `starts_at`.
    """
    consulta = consulta.where(Booking.business_id == business_id)
    if f.desde is not None:
        consulta = consulta.where(Booking.local_date >= f.desde)
    if f.hasta is not None:
        consulta = consulta.where(Booking.local_date <= f.hasta)
    if f.professional_id is not None:
        consulta = consulta.where(Booking.professional_id == f.professional_id)
    if f.service_id is not None:
        consulta = consulta.where(Booking.service_id == f.service_id)
    if f.customer_id is not None:
        consulta = consulta.where(Booking.customer_id == f.customer_id)
    if f.estado is not None:
        consulta = consulta.where(Booking.status == f.estado)
    if f.solo_pendientes:
        # "Pendientes" para el panel son las confirmadas de hoy y hacia adelante: lo
        # que todavia no paso y todavia se puede mover. No es un estado de la base,
        # asi que se define aca en vez de inventar un enum que solo usaria el filtro.
        consulta = consulta.where(
            and_(
                Booking.status.in_(CERRABLES),
                Booking.ends_at >= now(),
            )
        )
    return consulta


async def listar_reservas(
    session: AsyncSession, business_id: uuid.UUID, filtros: ReservaFiltros
) -> tuple[list[ReservaDetalle], int]:
    """Reservas del negocio segun `filtros`, y el total que hay antes del paginado.

    Devuelve el total ademas de la pagina porque el panel lo necesita para el
    "pagina 3 de 47", y sin el total el frontend tiene que adivinar si hay mas
    mirando si la ultima pagina vino llena -- y con un filtro que da exactamente en
    el limite eso muestra una pagina vacia extra que el usuario percibe como "no hay
    mas reservas" cuando si las hay.
    """
    limite = max(1, min(filtros.limite, MAX_LIMITE))
    offset = max(0, min(filtros.offset, MAX_OFFSET))

    consulta = _aplicar_filtros(select(Booking), business_id, filtros)

    total = await session.execute(
        _aplicar_filtros(
            select(func.count(Booking.id)),
            business_id,
            filtros,
        ).select_from(Booking)
    )
    total_reservas = int(total.scalar_one())

    filas = await session.execute(
        consulta.outerjoin(Service, Service.id == Booking.service_id)
        .outerjoin(Professional, Professional.id == Booking.professional_id)
        .outerjoin(Customer, Customer.id == Booking.customer_id)
        .add_columns(
            Service.name, Professional.display_name, Customer.first_name, Customer.phone_e164
        )
        .order_by(Booking.starts_at.desc(), Booking.id.desc())
        .limit(limite)
        .offset(offset)
    )

    return (
        [
            ReservaDetalle(
                booking=fila[0],
                servicio_nombre=fila[1],
                profesional_nombre=fila[2],
                cliente_nombre=fila[3],
                cliente_telefono=fila[4],
            )
            for fila in filas.all()
        ],
        total_reservas,
    )


async def obtener_reserva(
    session: AsyncSession, booking_id: uuid.UUID, *, business_id: uuid.UUID
) -> ReservaDetalle:
    """Una reserva con sus nombres, o `NotFoundError`.

    `business_id` va explicito y no se deduce de la RLS. Es lo que hace que un
    `booking_id` de otro tenant sea un 404 y no una reserva ajena: la RLS ya lo
    evita, pero el filtro a la vista es lo que lo documenta.
    """
    result = await session.execute(
        select(Booking)
        .outerjoin(Service, Service.id == Booking.service_id)
        .outerjoin(Professional, Professional.id == Booking.professional_id)
        .outerjoin(Customer, Customer.id == Booking.customer_id)
        .where(Booking.id == booking_id, Booking.business_id == business_id)
        .add_columns(
            Service.name, Professional.display_name, Customer.first_name, Customer.phone_e164
        )
    )
    fila = result.first()
    if fila is None:
        raise NotFoundError("Reserva no encontrada.")
    return ReservaDetalle(
        booking=fila[0],
        servicio_nombre=fila[1],
        profesional_nombre=fila[2],
        cliente_nombre=fila[3],
        cliente_telefono=fila[4],
    )


async def agenda_del_profesional(
    session: AsyncSession,
    business_id: uuid.UUID,
    professional_id: uuid.UUID,
    *,
    desde: dt.datetime,
    hasta: dt.datetime,
) -> list[ReservaDetalle]:
    """Turnos de un profesional en una ventana de tiempo.

    Es la consulta del panel del profesional (§21). Se ordena ascendente porque la
    agenda es una lista de "lo que viene": al revés, el turno más cercano queda
    abajo de la pantalla y el que atendió hace dos horas queda arriba.

    A diferencia del listado del admin, este no pagina: son los turnos de una
    ventana acotada y el panel los muestra enteros.
    """
    result = await session.execute(
        select(Booking)
        .outerjoin(Service, Service.id == Booking.service_id)
        .outerjoin(Professional, Professional.id == Booking.professional_id)
        .outerjoin(Customer, Customer.id == Booking.customer_id)
        .where(
            Booking.business_id == business_id,
            Booking.professional_id == professional_id,
            Booking.starts_at >= desde,
            Booking.starts_at <= hasta,
        )
        .add_columns(
            Service.name, Professional.display_name, Customer.first_name, Customer.phone_e164
        )
        .order_by(Booking.starts_at.asc())
    )
    return [
        ReservaDetalle(
            booking=fila[0],
            servicio_nombre=fila[1],
            profesional_nombre=fila[2],
            cliente_nombre=fila[3],
            cliente_telefono=fila[4],
        )
        for fila in result.all()
    ]


async def _obtener_para_escritura(
    session: AsyncSession, booking_id: uuid.UUID, business_id: uuid.UUID
) -> Booking:
    result = await session.execute(
        select(Booking).where(Booking.id == booking_id, Booking.business_id == business_id)
    )
    booking = result.scalar_one_or_none()
    if booking is None:
        raise NotFoundError("Reserva no encontrada.")
    return booking


async def cancelar_desde_panel(
    session: AsyncSession,
    booking_id: uuid.UUID,
    *,
    business_id: uuid.UUID,
    cancellation_window_minutes: int,
    motivo: str | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> Booking:
    """Cancela una reserva desde el panel, sin secure token.

    Aplica las mismas reglas que el cancelado del cliente
    (`_assert_cancellable`): la ventana de cancelacion del negocio, y no se cancela
    lo que ya empezo. La diferencia es solo de quien autoriza --aca el
    `Principal`-- y de que el evento de auditoria queda con `actor_type='user'`.

    Que el panel tampoco pueda saltarse la ventana de cancelacion es una decision
    deliberada: el negocio quiere poder cancelar un turno, pero no uno que ya empezo,
    porque a esa hora el horario ya no se recupera. Un negocio que necesite cancelar
    en cualquier momento pone `cancellation_window_minutes` en 0 y el comportamiento
    cambia, sin tocar codigo.
    """
    booking = await _obtener_para_escritura(session, booking_id, business_id)

    try:
        _assert_cancellable(booking, cancellation_window_minutes)
    except ValueError as exc:
        # Se traduce el `ValueError` del servicio de dominio a un 409 con el mismo
        # texto. El router no tiene que saber que `_assert_cancellable` lanza
        # `ValueError`.
        raise ConflictError(str(exc)) from exc

    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = now()
    booking.cancel_reason = motivo

    session.add(
        BookingEvent(
            business_id=booking.business_id,
            booking_id=booking.id,
            type=BookingEventType.CANCELLED,
            actor_type=AuditActorType.USER,
            actor_id=actor_user_id,
            payload={"reason": motivo, "origen": "panel"} if motivo else {"origen": "panel"},
        )
    )

    await _cancel_pending_notifications(session, booking.id)
    await session.flush()
    return booking


async def marcar_estado_final(
    session: AsyncSession,
    booking_id: uuid.UUID,
    *,
    business_id: uuid.UUID,
    estado: BookingStatus,
    actor_user_id: uuid.UUID | None = None,
) -> Booking:
    """Lleva una reserva a `completed` o `no_show`.

    Es el cierre de turno del panel: el profesional marco que el cliente vino, o
    que no vino. Ambos estados liberan el horario (la EXCLUDE solo mira
    `confirmed` y `pending_hold`) y ambos son terminales.

    No se puede cerrar una reserva que ya empezo *y* que aun no empezo a la vez: el
    servicio valida que el estado actual sea cerrable y que el turno ya haya
    ocurrido. Un "no show" antes de la hora seria una mentira en la agenda.
    """
    if estado not in (BookingStatus.COMPLETED, BookingStatus.NO_SHOW):
        raise ValidationError(f"Solo se puede marcar como completed o no_show, no {estado}.")

    booking = await _obtener_para_escritura(session, booking_id, business_id)

    if booking.status not in CERRABLES:
        raise ConflictError(f"No se puede cerrar una reserva {booking.status}.")
    if booking.ends_at > now():
        raise ConflictError("El turno todavia no termino. No se puede marcar como cerrado.")

    booking.status = estado
    session.add(
        BookingEvent(
            business_id=booking.business_id,
            booking_id=booking.id,
            type=(
                BookingEventType.COMPLETED
                if estado == BookingStatus.COMPLETED
                else BookingEventType.NO_SHOW
            ),
            actor_type=AuditActorType.USER,
            actor_id=actor_user_id,
        )
    )
    await session.flush()
    return booking


async def registrar_walkin(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    service_id: uuid.UUID,
    professional_id: uuid.UUID | None,
    customer_first_name: str,
    customer_last_name: str,
    customer_phone_e164: str,
    starts_at: dt.datetime,
    timezone: str,
    actor_user_id: uuid.UUID | None = None,
    notas: str | None = None,
) -> Booking:
    """Registra un cliente que llega sin reserva.

    Delega en `create_booking` en vez de reimplementar el alta: la EXCLUDE, la
    asignacion de profesional y el `_get_or_create_customer` tienen que ser
    **exactamente** los mismos que para una reserva online. Si el walk-in tuviera
    su propio camino, la agenda dejaria de ser la fuente de verdad de la ocupacion
    y habria dos reglas anti-doble-reserva que divergir con el primer bug.

    Lo que cambia son tres cosas, y se pasan como parametros en vez de parchear la
    fila despues:

    - `source=walkin`, para poder filtrar los turnos agregados a mano;
    - `notificar=False`, porque el cliente esta parado en el mostrador: un "tu turno
      quedo confirmado" por WhatsApp es ruido y el recordatorio de dos horas para
      alguien que ya llego es directamente falso;
    - `actor_user_id`, para que la auditoria diga que turno se agrego a mano y
      quien lo agrego, en vez de simular que el cliente se reservo solo.

    El `WALKIN_REGISTERED` va como evento propio ademas del `created` que emite
    `create_booking`. Son dos hechos y no uno: la reserva se creo *y* se registro
    desde el mostrador. Un unico evento obliga a elegir cual de los dos se
    consulta, y las dos consultas que hacen falta son distintas.
    """
    # Resolver el servicio para sacar duracion y precio, como snapshots. El precio
    # y la duracion se copian a la reserva, asi que cambiar el servicio despues no
    # reescribe lo que el cliente pago.
    servicio = await session.execute(
        select(Service).where(Service.id == service_id, Service.business_id == business_id)
    )
    svc = servicio.scalar_one_or_none()
    if svc is None:
        raise NotFoundError("Servicio no encontrado.")

    if starts_at < now():
        raise ValidationError("No se puede registrar un walk-in en el pasado.")

    ends_at = starts_at + dt.timedelta(minutes=svc.duration_minutes)
    local_date = starts_at.astimezone(ZoneInfo(timezone)).date()

    # El walk-in **si** revalida contra el motor, pero con la antelacion en cero.
    #
    # Las dos mitades importan por separado. Revalidar el calendario sin
    # anticipacion evita que el mostrador registre un turno con el negocio
    # cerrado, en feriado o encima de otro turno -- que es exactamente la clase de
    # dato basura que despues nadie sabe de donde salio. Y poner la anticipacion
    # en cero es lo que hace que esto no rompa el caso mas comun de todos: la
    # persona que entra ahora y quiere un turno ahora. `min_lead_minutes` protege
    # al cliente que reserva por la web de tener que levantarse al dia siguiente;
    # no le dice nada a quien ya esta parado en el mostrador.
    negocio = await session.execute(select(Business).where(Business.id == business_id))
    biz = negocio.scalar_one_or_none()
    if biz is None:
        raise NotFoundError("Negocio no encontrado.")

    agenda = ReglasAgenda.de_negocio(biz, min_lead_minutes=0)

    try:
        resultado = await create_booking(
            session,
            business_id=business_id,
            service_id=service_id,
            professional_id=professional_id,
            customer_first_name=customer_first_name,
            customer_last_name=customer_last_name,
            customer_phone_e164=customer_phone_e164,
            starts_at=starts_at,
            ends_at=ends_at,
            duration_minutes=svc.duration_minutes,
            price=svc.price,
            currency=svc.currency,
            local_date=local_date,
            agenda=agenda,
            source=BookingSource.WALKIN,
            notificar=False,
            actor_user_id=actor_user_id,
        )
    except ValueError as exc:
        # `create_booking` levanta `SlotNoDisponibleError` (un `ValueError`) cuando
        # la EXCLUDE choca. Para el walk-in es un 409 igual que para el publico: el
        # motivo real es que alguien se reserves ese horario hace un segundo.
        raise ConflictError(str(exc)) from exc

    booking = await _obtener_para_escritura(session, resultado.booking_id, business_id)
    if notas:
        booking.notes = notas

    session.add(
        BookingEvent(
            business_id=booking.business_id,
            booking_id=booking.id,
            type=BookingEventType.WALKIN_REGISTERED,
            actor_type=AuditActorType.USER,
            actor_id=actor_user_id,
        )
    )
    await session.flush()
    return booking


__all__ = [
    "CERRABLES",
    "MAX_LIMITE",
    "MAX_OFFSET",
    "ReservaDetalle",
    "ReservaFiltros",
    "agenda_del_profesional",
    "cancelar_desde_panel",
    "listar_reservas",
    "marcar_estado_final",
    "obtener_reserva",
    "registrar_walkin",
]
