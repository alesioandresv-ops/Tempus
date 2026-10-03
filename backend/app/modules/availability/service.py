"""Servicio de disponibilidad: carga datos de BD y llama al motor puro.

Este módulo es la capa que conecta el motor de disponibilidad (función pura)
con la base de datos. Carga horarios, reservas, bloqueos y vacancias, y
los pasa al engine.

**Regla crítica**: la disponibilidad se calcula UNA SOLA VEZ, aquí.
El frontend puede previsualizar, pero el backend siempre valida de nuevo.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import now as utc_now
from app.modules.availability.engine import (
    AvailabilityInput,
    Interval,
    calculate_availability,
    find_eligible_professionals,
)
from app.modules.bookings.models import OCCUPYING_STATUSES, Booking
from app.modules.professionals.models import Professional
from app.modules.schedules.models import (
    Block,
    BusinessException,
    BusinessExceptionWindow,
    BusinessHour,
    Holiday,
    ProfessionalSchedule,
    TimeOff,
)
from app.modules.services.models import ProfessionalService


@dataclass(frozen=True, slots=True)
class SlotDisponible:
    """Un horario reservable y **quien puede atenderlo**.

    `candidatos` viene ordenado por la estrategia de asignacion, no en el orden en
    que salio la consulta. El primero es a quien se le asignaria el turno.

    **Por que existe `candidatos` y no un `professional_id` suelto.** La version
    anterior de este modulo devolvia `None` para "cualquier profesional" y dejaba
    la eleccion para el momento de reservar. Eso rompia en dos lugares:

    1. El endpoint de disponibilidad ofrecia un horario sin decir de quien era,
       contradiciendo el contrato que declara su propio schema.
    2. `create_booking` reasignaba por `sort_order`, o sea "el primero de la lista",
       sin mirar si estaba libre a esa hora. Si el slot lo ofrecia Beto y el
       primero de la lista era Ana, ya ocupada, la EXCLUDE rechazaba el INSERT y el
       cliente recibia un 409 sobre un horario que el propio sistema le habia
       mostrado. Eso es exactamente lo que el §48 prohíbe: el sistema tiene que
       representar la realidad, y la realidad de las 15:00 es "puede Beto", no
       "puede el que sorting dio primero".

    Con la lista de candidatos el endpoint puede dizer a quien le toca y
    `create_booking` puede elegir entre los que de verdad pueden, sin reimplementar
    el calculo ni confiar en un `ORDER BY`.
    """

    starts_at: dt.datetime
    ends_at: dt.datetime
    candidatos: tuple[uuid.UUID, ...]


async def _load_business_windows(
    session: AsyncSession,
    business_id: str,
    local_date: dt.date,
    timezone: ZoneInfo | str,
) -> tuple[Interval, ...]:
    """Carga las ventanas del negocio para un día dado.

    Resuelve excepciones y feriados:
    - Si hay excepción cerrada → no hay ventanas.
    - Si hay excepción abierta → usa las ventanas de la excepción.
    - Si no hay excepción → usa las ventanas regulares del weekday.
    - Si es feriado → no hay ventanas.
    """
    tz = ZoneInfo(timezone) if isinstance(timezone, str) else timezone

    # Verificar feriado
    holiday_result = await session.execute(
        select(Holiday).where(
            Holiday.business_id == business_id,
            Holiday.local_date == local_date,
        )
    )
    if holiday_result.scalar_one_or_none():
        return ()

    # Verificar excepción
    exception_result = await session.execute(
        select(BusinessException).where(
            BusinessException.business_id == business_id,
            BusinessException.local_date == local_date,
        )
    )
    exception = exception_result.scalar_one_or_none()

    if exception and exception.is_closed:
        return ()

    if exception and not exception.is_closed:
        # Usar ventanas de la excepción
        window_result = await session.execute(
            select(BusinessExceptionWindow)
            .where(
                BusinessExceptionWindow.exception_id == exception.id,
            )
            .order_by(BusinessExceptionWindow.window_index)
        )
        windows = []
        for w in window_result.scalars():
            start_dt = dt.datetime.combine(local_date, w.start_time, tzinfo=tz)
            end_dt = dt.datetime.combine(local_date, w.end_time, tzinfo=tz)
            windows.append(
                Interval(start=start_dt.astimezone(dt.UTC), end=end_dt.astimezone(dt.UTC))
            )
        return tuple(windows)

    # Usar ventanas regulares
    weekday = local_date.weekday()  # 0 = lunes
    hours_result = await session.execute(
        select(BusinessHour)
        .where(
            BusinessHour.business_id == business_id,
            BusinessHour.weekday == weekday,
            BusinessHour.is_open.is_(True),
        )
        .order_by(BusinessHour.window_index)
    )
    windows = []
    for h in hours_result.scalars():
        if h.end_time is None:
            continue
        start_dt = dt.datetime.combine(local_date, h.start_time, tzinfo=tz)
        end_dt = dt.datetime.combine(local_date, h.end_time, tzinfo=tz)
        windows.append(Interval(start=start_dt.astimezone(dt.UTC), end=end_dt.astimezone(dt.UTC)))
    return tuple(windows)


async def _load_professional_windows(
    session: AsyncSession,
    professional_id: str,
    business_windows: tuple[Interval, ...],
    local_date: dt.date,
    timezone: ZoneInfo | str,
) -> tuple[Interval, ...]:
    """Carga los horarios del profesional.

    Si el profesional tiene `is_override = True`, usa sus horarios.
    Si `is_override = False`, hereda los del negocio.
    """
    tz = ZoneInfo(timezone) if isinstance(timezone, str) else timezone

    schedule_result = await session.execute(
        select(ProfessionalSchedule)
        .where(
            ProfessionalSchedule.professional_id == professional_id,
            ProfessionalSchedule.weekday == local_date.weekday(),
        )
        .order_by(ProfessionalSchedule.window_index)
    )
    schedules = list(schedule_result.scalars())

    # Si no tiene horarios o todos son is_override=False, hereda del negocio
    has_override = any(s.is_override for s in schedules)
    if not has_override:
        return business_windows

    windows = []
    for s in schedules:
        if not s.is_override:
            continue
        start_dt = dt.datetime.combine(local_date, s.start_time, tzinfo=tz)
        end_dt = dt.datetime.combine(local_date, s.end_time, tzinfo=tz)
        windows.append(Interval(start=start_dt.astimezone(dt.UTC), end=end_dt.astimezone(dt.UTC)))
    return tuple(windows) if windows else business_windows


async def _load_occupied_intervals(
    session: AsyncSession,
    professional_id: str,
    local_date: dt.date,
) -> tuple[Interval, ...]:
    """Carga reservas que ocupan la agenda del profesional."""
    result = await session.execute(
        select(Booking).where(
            Booking.professional_id == professional_id,
            Booking.local_date == local_date,
            Booking.status.in_(["confirmed", "pending_hold"]),
        )
    )
    intervals = []
    for booking in result.scalars():
        intervals.append(Interval(start=booking.occupied_from, end=booking.occupied_to))
    return tuple(intervals)


async def _load_blocked_intervals(
    session: AsyncSession,
    professional_id: str,
    local_date: dt.date,
    timezone: ZoneInfo | str,
) -> tuple[Interval, ...]:
    """Carga bloqueos (time_off aprobado + blocks)."""
    tz = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    day_start = dt.datetime.combine(local_date, dt.time.min, tzinfo=tz).astimezone(dt.UTC)
    day_end = dt.datetime.combine(local_date, dt.time.max, tzinfo=tz).astimezone(dt.UTC)

    intervals: list[Interval] = []

    # Time off aprobado
    time_off_result = await session.execute(
        select(TimeOff).where(
            TimeOff.professional_id == professional_id,
            TimeOff.status == "approved",
            TimeOff.starts_at < day_end,
            TimeOff.ends_at > day_start,
        )
    )
    for to in time_off_result.scalars():
        intervals.append(Interval(start=to.starts_at, end=to.ends_at))

    # Blocks
    block_result = await session.execute(
        select(Block).where(
            Block.professional_id == professional_id,
            Block.starts_at < day_end,
            Block.ends_at > day_start,
        )
    )
    for block in block_result.scalars():
        if block.occupied_from and block.occupied_to:
            intervals.append(Interval(start=block.occupied_from, end=block.occupied_to))
        else:
            intervals.append(Interval(start=block.starts_at, end=block.ends_at))

    return tuple(intervals)


async def _cargar_carga_del_dia(
    session: AsyncSession,
    business_id: str,
    professional_ids: list[str],
    local_date: dt.date,
) -> dict[str, int]:
    """Turnos confirmados de cada profesional en un dia. Una sola consulta.

    Es la entrada de la estrategia de asignacion de "cualquier profesional": el
    §9 deja el criterio eleccionado a la implementacion y pide que sea
    configurable. El criterio por defecto es **menos carga**, es decir repartir los
    turnos en vez de apilarlo todo sobre el primero de la lista.

    Una consulta agrupada y no un `COUNT` por profesional: con 30 profesionales son
    30 idas a la base para obtener un numero que PostgreSQL puede devolver ya
    agregado.
    """
    if not professional_ids:
        return {}

    result = await session.execute(
        select(Booking.professional_id, func.count(Booking.id))
        .where(
            Booking.business_id == business_id,
            Booking.local_date == local_date,
            Booking.status.in_(OCCUPYING_STATUSES),
            Booking.professional_id.in_([uuid.UUID(pid) for pid in professional_ids]),
        )
        .group_by(Booking.professional_id)
    )
    return {str(prof_id): int(total) for prof_id, total in result.all()}


async def get_availability(
    session: AsyncSession,
    *,
    business_id: str,
    professional_id: str | None,
    service_duration_minutes: int,
    slot_interval_minutes: int,
    local_date: dt.date,
    timezone: ZoneInfo | str,
    service_id: str | None = None,
    min_lead_minutes: int = 60,
    current_time: dt.datetime | None = None,
) -> list[SlotDisponible]:
    """Calcula disponibilidad para un servicio.

    Args:
        session: Sesión de BD (sin contexto de tenant, el servicio lo inyecta).
        business_id: ID del negocio.
        professional_id: ID del profesional, o None para "cualquier profesional".
        service_duration_minutes: Duración del servicio.
        slot_interval_minutes: Intervalo de la grilla.
        local_date: Fecha local del negocio.
        timezone: Timezone del negocio.
        service_id: Servicio a realizar. Obligatorio cuando
            `professional_id` es None: sin el, "cualquier profesional" no puede
            filtrar por quien realmente puede hacer el servicio.
        min_lead_minutes: Antelación mínima.
        current_time: Momento actual (para lead time). Usa `now()` si es None.

    Returns:
        Lista de `SlotDisponible` ordenada cronológicamente, cada uno con la lista
        de profesionales que pueden atenderlo **ordenada por la estrategia**
        (menos carga del día primero).

        - **Profesional concreto:** `candidatos` tiene un solo elemento, ese
          profesional, en cada slot. Si aparece el slot es que puede hacerlo.
        - **Cualquier profesional:** `candidatos` tiene a todos los que pueden, y
          el primero es a quien le tocaría.
    """
    if current_time is None:
        current_time = utc_now()

    # Cargar ventanas del negocio
    business_windows = await _load_business_windows(session, business_id, local_date, timezone)
    if not business_windows:
        return []

    # Cargar horarios del profesional
    if professional_id:
        prof_windows = await _load_professional_windows(
            session, professional_id, business_windows, local_date, timezone
        )
        occupied = await _load_occupied_intervals(session, professional_id, local_date)
        blocked = await _load_blocked_intervals(session, professional_id, local_date, timezone)

        input_data = AvailabilityInput(
            business_windows=business_windows,
            professional_windows=prof_windows,
            occupied_intervals=occupied,
            blocked_intervals=blocked,
            duration_minutes=service_duration_minutes,
            slot_interval_minutes=slot_interval_minutes,
            local_date=local_date,
            timezone=timezone,
            min_lead_minutes=min_lead_minutes,
            now=current_time,
        )
        slots = calculate_availability(input_data)
        unico = uuid.UUID(professional_id)
        return [
            SlotDisponible(starts_at=s.starts_at, ends_at=s.ends_at, candidatos=(unico,))
            for s in slots
        ]

    # "Cualquier profesional". Sin `service_id` no hay forma de saber quien puede
    # hacer el trabajo, y ofrecer un horario que despues no se puede tomar es peor
    # que no ofrecer ninguno: el cliente elige, y el error cae en la reserva.
    if not service_id:
        return []

    candidatos_ordenados = await _load_eligible_professionals(session, business_id, service_id)
    if not candidatos_ordenados:
        return []

    professional_windows: dict[str, tuple[Interval, ...]] = {}
    # Los nombres llevan el sufijo `_per_pro` y no repiten `occupied`/`blocked` de las
    # ramas de arriba **a proposito**. mypy no reinicia el estrechamiento de tipos al
    # reenlazar un nombre, asi que con el mismo nombre denunciaba estas dos lineas
    # como "asignacion por indice sobre una tupla": son falsos positivos--arriba son
    # tuplas, aqui son dict--, pero son seis errores falsos que taparian para siempre
    # un error real en esas mismas lineas. Con el otro nombre, el error que someday se
    # agregue ahi aparece de verdad.
    occupied_per_pro: dict[str, tuple[Interval, ...]] = {}
    blocked_per_pro: dict[str, tuple[Interval, ...]] = {}

    for candidate in candidatos_ordenados:
        key = str(candidate)
        professional_windows[key] = await _load_professional_windows(
            session, key, business_windows, local_date, timezone
        )
        occupied_per_pro[key] = await _load_occupied_intervals(session, key, local_date)
        blocked_per_pro[key] = await _load_blocked_intervals(session, key, local_date, timezone)

    per_professional = find_eligible_professionals(
        professionals_windows=professional_windows,
        business_windows=business_windows,
        occupied_intervals=occupied_per_pro,
        blocked_intervals=blocked_per_pro,
        duration_minutes=service_duration_minutes,
        slot_interval_minutes=slot_interval_minutes,
        local_date=local_date,
        timezone=timezone,
        min_lead_minutes=min_lead_minutes,
        now=current_time,
    )

    # **Union de horarios, no un slot por profesional.** "Cualquier profesional"
    # significa que al cliente le da igual quien atienda: lo que quiere es una
    # lista de horarios en los que puede ir. Tomar solo el primer slot de cada uno
    # devuelve las 09:00 tres veces --una por profesional-- y obliga a elegir a
    # mano justo la persona que el cliente dijo que no le importa. Peor: si Ana
    # esta libre a las 09 y a las 10 pero Beto solo a las 09, el enfoque anterior
    # pierde las 10:00, que era un horario que si se podia ofrecer.
    #
    # Un horario que puede atender mas de uno aparece **una vez**, con los
    # candidatos acumulados.
    #
    # Se usa el `starts_at` como clave de deduplicacion y no el `Slot` entero
    # porque dos profesionales con la misma duracion producen intervalos
    # identicos, y comparar los dos seria correcto por casualidad y no por
    # diseno. La duracion viene del servicio, que es el mismo para todos los
    # candidatos de esta llamada.
    por_inicio: dict[dt.datetime, list[uuid.UUID]] = {}
    for prof_id, prof_slots in per_professional.items():
        for slot in prof_slots:
            por_inicio.setdefault(slot.starts_at, []).append(uuid.UUID(prof_id))

    # Ordenar por estrategia: menos carga del dia, y a igual carga el `sort_order`
    # del negocio, que es el criterio que el admin usa para ordenar a su equipo.
    carga = await _cargar_carga_del_dia(
        session,
        business_id,
        [str(pid) for pid in candidatos_ordenados],
        local_date,
    )
    rango = {pid: i for i, pid in enumerate(candidatos_ordenados)}

    disponibles: list[SlotDisponible] = []
    for inicio in sorted(por_inicio):
        # `dict.fromkeys` des-duplica conservando el primer orden de aparicion.
        unicos = tuple(dict.fromkeys(por_inicio[inicio]))
        ordenados = tuple(
            sorted(
                unicos,
                key=lambda pid: (carga.get(str(pid), 0), rango.get(pid, 0), str(pid)),
            )
        )
        fin = inicio + dt.timedelta(minutes=service_duration_minutes)
        disponibles.append(SlotDisponible(starts_at=inicio, ends_at=fin, candidatos=ordenados))
    return disponibles


async def _load_eligible_professionals(
    session: AsyncSession, business_id: str, service_id: str
) -> list[uuid.UUID]:
    """Profesionales activos que pueden realizar un servicio.

    Los tres filtros son los del PROJECT_MASTER §9: pertenece al negocio, esta
    activo, y puede realizar el servicio. Los otros cinco condiciones (trabaja en
    ese horario, no esta de vacaciones, no tiene un bloqueo, no tiene otro turno,
    tiene ventana continua para la duracion) las descarta el motor mas adelante,
    por profesional, porque dependen del horario pedido y no del servicio.

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


async def buscar_slot(
    session: AsyncSession,
    *,
    business_id: str,
    professional_id: str | None,
    service_id: str,
    service_duration_minutes: int,
    slot_interval_minutes: int,
    local_date: dt.date,
    timezone: ZoneInfo | str,
    starts_at: dt.datetime,
    min_lead_minutes: int = 60,
    current_time: dt.datetime | None = None,
) -> SlotDisponible | None:
    """El `SlotDisponible` que empieza exactamente en `starts_at`, o `None`.

    Es la revalidacion del §8.9 y la que hace que `create_booking` no tenga que
    reimplementar el calculo: pregunta al **mismo** motor que respondio el endpoint
    de disponibilidad. Una sola fuente de verdad, que es el §32.

    Se busca por `starts_at` exacto y no por "el slot mas cercano": un cliente que
    pide las 15:07 en una grilla de 15 minutos tiene que recibir un rechazo, no el
    turno de las 15:00 que el backend le encaja.
    """
    for slot in await get_availability(
        session,
        business_id=business_id,
        professional_id=professional_id,
        service_duration_minutes=service_duration_minutes,
        slot_interval_minutes=slot_interval_minutes,
        local_date=local_date,
        timezone=timezone,
        service_id=service_id,
        min_lead_minutes=min_lead_minutes,
        current_time=current_time,
    ):
        if slot.starts_at == starts_at:
            return slot
    return None


__all__ = ["SlotDisponible", "buscar_slot", "get_availability"]
