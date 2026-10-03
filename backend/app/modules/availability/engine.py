"""Motor de disponibilidad: función pura de intervalos.

Este módulo es la **única** fuente de verdad para calcular disponibilidad.
No toca la base de datos, no tiene efectos secundarios, y no conoce HTTP.

Recibe intervalos ya cargados y devuelve slots reservables. La capa de
servicio es responsable de cargar los datos y llamar a este motor.

Decisiones de diseño (ver ADR-0006, ADR-0007):

1. **Función pura**: misma entrada = misma salida. Sin estado, sin DB.
2. **Intervalos semiabiertos `[inicio, fin)`**: un turno que termina 12:00 no
   solapa con uno que empieza 12:00.
3. **Grilla anclada a medianoche**: los slots se generan desde el inicio de
   cada ventana, espaciados por `slot_interval_minutes`. No se anclan a la
   hora actual ni a un "próximo slot" arbitrario.
4. **Buffers incluidos en `occupied_from`/`occupied_to`**: el motor recibe
   intervalos ya con buffer aplicado. No calcula buffers.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class Interval:
    """Un intervalo semiabierto `[start, end)` en UTC."""

    start: dt.datetime
    end: dt.datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"Intervalo inválido: end ({self.end}) <= start ({self.start})")

    def overlaps(self, other: Interval) -> bool:
        """True si se solapan (semiabierto: tocar bordes no cuenta)."""
        return self.start < other.end and other.start < self.end

    def contains(self, other: Interval) -> bool:
        """True si `other` está completamente dentro de `self`."""
        return self.start <= other.start and other.end <= self.end

    def intersection(self, other: Interval) -> Interval | None:
        """Intersección de dos intervalos, o None si no se solapan."""
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        if start < end:
            return Interval(start, end)
        return None


@dataclass(frozen=True, slots=True)
class Slot:
    """Un slot reservable en la grilla."""

    starts_at: dt.datetime
    ends_at: dt.datetime

    @property
    def duration_minutes(self) -> int:
        return int((self.ends_at - self.starts_at).total_seconds() / 60)

    def overlaps(self, other: Interval) -> bool:
        """True si el slot se solapa con un intervalo (semiabierto)."""
        return self.starts_at < other.end and other.start < self.ends_at


@dataclass(frozen=True, slots=True)
class AvailabilityInput:
    """Datos necesarios para calcular disponibilidad.

    Todos los intervalos ya vienen con buffer aplicado (si aplica).
    """

    # Ventanas del negocio para el día consultado (ya filtradas por weekday/excepción/feriado)
    business_windows: tuple[Interval, ...]
    # Horarios del profesional (ya resueltos: override o herencia del negocio)
    professional_windows: tuple[Interval, ...]
    # Reservas existentes que ocupan la agenda
    occupied_intervals: tuple[Interval, ...]
    # Bloqueos (time_off aprobado + blocks)
    blocked_intervals: tuple[Interval, ...]
    # Duración del servicio en minutos
    duration_minutes: int
    # Intervalo de la grilla en minutos
    slot_interval_minutes: int
    # Fecha local del negocio
    local_date: dt.date
    # Timezone del negocio
    timezone: ZoneInfo | str
    # Minutos de antelación mínima (lead time)
    min_lead_minutes: int = 60
    # Momento actual (para lead time)
    now: dt.datetime | None = None


def _generate_grid_windows(
    windows: tuple[Interval, ...],
    duration_minutes: int,
    slot_interval_minutes: int,
) -> list[Slot]:
    """Genera slots de la grilla dentro de las ventanas dadas.

    Los slots se anclan al inicio de cada ventana, espaciados por
    `slot_interval_minutes`. Un slot solo es válido si cabe completo dentro
    de la ventana.
    """
    slots: list[Slot] = []
    for window in windows:
        current = window.start
        while True:
            slot_end = current + dt.timedelta(minutes=duration_minutes)
            if slot_end > window.end:
                break
            slots.append(Slot(starts_at=current, ends_at=slot_end))
            current += dt.timedelta(minutes=slot_interval_minutes)
    return slots


def _filter_by_intervals(
    slots: list[Slot],
    blocking_intervals: tuple[Interval, ...],
) -> list[Slot]:
    """Filtra slots que se solapan con intervalos bloqueantes."""
    if not blocking_intervals:
        return slots
    return [
        slot for slot in slots if not any(slot.overlaps(blocked) for blocked in blocking_intervals)
    ]


def _filter_by_lead_time(
    slots: list[Slot],
    now: dt.datetime,
    min_lead_minutes: int,
) -> list[Slot]:
    """Filtra slots que no cumplen la antelación mínima."""
    min_start = now + dt.timedelta(minutes=min_lead_minutes)
    return [slot for slot in slots if slot.starts_at >= min_start]


def calculate_availability(input_data: AvailabilityInput) -> list[Slot]:
    """Calcula los slots disponibles para un servicio en un día dado.

    Algoritmo:
    1. Intersectar ventanas del negocio con las del profesional.
    2. Generar la grilla de slots dentro de la intersección.
    3. Filtrar slots que se solapan con reservas o bloqueos.
    4. Filtrar slots que no cumplen el lead time.

    Returns:
        Lista de slots ordenados cronológicamente.
    """
    if input_data.duration_minutes <= 0:
        return []

    # 1. Intersección de ventanas del negocio con las del profesional
    if not input_data.business_windows or not input_data.professional_windows:
        return []

    available_windows: list[Interval] = []
    for biz_window in input_data.business_windows:
        for prof_window in input_data.professional_windows:
            intersection = biz_window.intersection(prof_window)
            if intersection:
                available_windows.append(intersection)

    if not available_windows:
        return []

    # 2. Generar grilla de slots
    slots = _generate_grid_windows(
        tuple(available_windows),
        input_data.duration_minutes,
        input_data.slot_interval_minutes,
    )

    # 3. Filtrar por reservas y bloqueos
    all_blocking = input_data.occupied_intervals + input_data.blocked_intervals
    slots = _filter_by_intervals(slots, all_blocking)

    # 4. Filtrar por lead time
    if input_data.now is not None:
        slots = _filter_by_lead_time(slots, input_data.now, input_data.min_lead_minutes)

    return slots


def find_eligible_professionals(
    professionals_windows: dict[str, tuple[Interval, ...]],
    business_windows: tuple[Interval, ...],
    occupied_intervals: dict[str, tuple[Interval, ...]],
    blocked_intervals: dict[str, tuple[Interval, ...]],
    duration_minutes: int,
    slot_interval_minutes: int,
    local_date: dt.date,
    timezone: dt.tzinfo | str,
    now: dt.datetime | None = None,
    min_lead_minutes: int = 60,
) -> dict[str, list[Slot]]:
    """Encuentra profesionales disponibles para "cualquier profesional".

    `local_date` y `timezone` son parametros y no constantes porque la grilla se
    ancla a la medianoche **del dia local del negocio**. Fijarlos a UTC hacia que
    un negocio de Argentina con horario de 09 a 18 produzca una grilla corrida
    cuatro o cinco horas, con slots que caen fuera de su ventana.

    Returns:
        Diccionario `professional_id -> slots disponibles`.
        Solo incluye profesionales con al menos un slot disponible.
    """
    result: dict[str, list[Slot]] = {}
    for prof_id, prof_windows in professionals_windows.items():
        input_data = AvailabilityInput(
            business_windows=business_windows,
            professional_windows=prof_windows,
            occupied_intervals=occupied_intervals.get(prof_id, ()),
            blocked_intervals=blocked_intervals.get(prof_id, ()),
            duration_minutes=duration_minutes,
            slot_interval_minutes=slot_interval_minutes,
            local_date=local_date,
            timezone=timezone,
            min_lead_minutes=min_lead_minutes,
            now=now,
        )
        slots = calculate_availability(input_data)
        if slots:
            result[prof_id] = slots
    return result


__all__ = [
    "AvailabilityInput",
    "Interval",
    "Slot",
    "calculate_availability",
    "find_eligible_professionals",
]
