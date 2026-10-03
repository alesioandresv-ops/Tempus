"""Tests unitarios para el motor de disponibilidad.

Estos tests no necesitan base de datos: el engine es una función pura.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from app.modules.availability.engine import (
    AvailabilityInput,
    Interval,
    calculate_availability,
)

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _dt(hour: int, minute: int = 0) -> dt.datetime:
    """Helper para crear datetimes en UTC."""
    return dt.datetime(2026, 1, 12, hour, minute, tzinfo=dt.UTC)


def _interval(start_hour: int, end_hour: int) -> Interval:
    """Helper para crear intervalos."""
    return Interval(start=_dt(start_hour), end=_dt(end_hour))


class TestInterval:
    """Tests para la clase Interval."""

    def test_overlaps_true(self):
        a = _interval(9, 10)
        b = _interval(9, 10)
        assert a.overlaps(b)

    def test_overlaps_partial(self):
        a = _interval(9, 11)
        b = _interval(10, 12)
        assert a.overlaps(b)

    def test_overlaps_false(self):
        a = _interval(9, 10)
        b = _interval(10, 11)
        assert not a.overlaps(b)  # Semiabierto: tocar bordes no cuenta

    def test_contains_true(self):
        a = _interval(9, 12)
        b = _interval(10, 11)
        assert a.contains(b)

    def test_contains_false(self):
        a = _interval(9, 10)
        b = _interval(9, 11)
        assert not a.contains(b)

    def test_intersection(self):
        a = _interval(9, 11)
        b = _interval(10, 12)
        result = a.intersection(b)
        assert result == Interval(start=_dt(10), end=_dt(11))

    def test_intersection_none(self):
        a = _interval(9, 10)
        b = _interval(10, 11)
        assert a.intersection(b) is None


class TestCalculateAvailability:
    """Tests para calculate_availability."""

    def test_basic_availability(self):
        """Un negocio abierto 9-18, servicio de 60 min, grilla de 15 min."""
        input_data = AvailabilityInput(
            business_windows=(_interval(9, 18),),
            professional_windows=(_interval(9, 18),),
            occupied_intervals=(),
            blocked_intervals=(),
            duration_minutes=60,
            slot_interval_minutes=15,
            local_date=dt.date(2026, 1, 12),
            timezone=TZ,
            min_lead_minutes=0,
        )
        slots = calculate_availability(input_data)

        # 9 horas = 540 min, servicio de 60 min, grilla de 15 min
        # Slots: 9:00, 9:15, 9:30, ... hasta 17:00 (17:00 + 60min = 18:00)
        # Total: (540 - 60) / 15 + 1 = 33 slots
        assert len(slots) == 33
        assert slots[0].starts_at == _dt(9)
        assert slots[0].ends_at == _dt(10)
        assert slots[-1].starts_at == _dt(17)
        assert slots[-1].ends_at == _dt(18)

    def test_filter_by_occupied(self):
        """Una reserva existente bloquea los slots que se solapan."""
        input_data = AvailabilityInput(
            business_windows=(_interval(9, 18),),
            professional_windows=(_interval(9, 18),),
            occupied_intervals=(_interval(10, 11),),  # Reserva 10-11
            blocked_intervals=(),
            duration_minutes=60,
            slot_interval_minutes=15,
            local_date=dt.date(2026, 1, 12),
            timezone=TZ,
            min_lead_minutes=0,
        )
        slots = calculate_availability(input_data)

        # Los slots que se solapan con 10-11 deben estar filtrados
        # Slot 10:00-11:00 se solapa, slot 10:15-11:15 se solapa, etc.
        # Slot 9:00-10:00 NO se solapa (semiabierto)
        # Slot 11:00-12:00 NO se solapa
        for slot in slots:
            assert not slot.overlaps(_interval(10, 11))

    def test_filter_by_blocked(self):
        """Un bloqueo bloquea los slots que se solapan."""
        input_data = AvailabilityInput(
            business_windows=(_interval(9, 18),),
            professional_windows=(_interval(9, 18),),
            occupied_intervals=(),
            blocked_intervals=(_interval(14, 15),),  # Bloqueo 14-15
            duration_minutes=60,
            slot_interval_minutes=15,
            local_date=dt.date(2026, 1, 12),
            timezone=TZ,
            min_lead_minutes=0,
        )
        slots = calculate_availability(input_data)

        for slot in slots:
            assert not slot.overlaps(_interval(14, 15))

    def test_filter_by_lead_time(self):
        """Los slots que no cumplen el lead time se filtran."""
        now = _dt(10)  # Son las 10:00
        input_data = AvailabilityInput(
            business_windows=(_interval(9, 18),),
            professional_windows=(_interval(9, 18),),
            occupied_intervals=(),
            blocked_intervals=(),
            duration_minutes=60,
            slot_interval_minutes=15,
            local_date=dt.date(2026, 1, 12),
            timezone=TZ,
            min_lead_minutes=60,  # 1 hora de antelación
            now=now,
        )
        slots = calculate_availability(input_data)

        # El primer slot debe ser al menos 11:00 (10:00 + 60 min)
        assert slots[0].starts_at >= _dt(11)

    def test_no_business_windows(self):
        """Sin ventanas del negocio, no hay slots."""
        input_data = AvailabilityInput(
            business_windows=(),
            professional_windows=(_interval(9, 18),),
            occupied_intervals=(),
            blocked_intervals=(),
            duration_minutes=60,
            slot_interval_minutes=15,
            local_date=dt.date(2026, 1, 12),
            timezone=TZ,
            min_lead_minutes=0,
        )
        slots = calculate_availability(input_data)
        assert slots == []

    def test_no_professional_windows(self):
        """Sin ventanas del profesional, no hay slots."""
        input_data = AvailabilityInput(
            business_windows=(_interval(9, 18),),
            professional_windows=(),
            occupied_intervals=(),
            blocked_intervals=(),
            duration_minutes=60,
            slot_interval_minutes=15,
            local_date=dt.date(2026, 1, 12),
            timezone=TZ,
            min_lead_minutes=0,
        )
        slots = calculate_availability(input_data)
        assert slots == []

    def test_professional_restricts_availability(self):
        """El profesional reduce la disponibilidad del negocio."""
        input_data = AvailabilityInput(
            business_windows=(_interval(9, 18),),
            professional_windows=(_interval(10, 16),),  # Profesional 10-16
            occupied_intervals=(),
            blocked_intervals=(),
            duration_minutes=60,
            slot_interval_minutes=15,
            local_date=dt.date(2026, 1, 12),
            timezone=TZ,
            min_lead_minutes=0,
        )
        slots = calculate_availability(input_data)

        # Solo slots entre 10:00 y 16:00
        assert slots[0].starts_at >= _dt(10)
        assert slots[-1].ends_at <= _dt(16)

    def test_multiple_windows(self):
        """Múltiples ventanas (con descanso)."""
        input_data = AvailabilityInput(
            business_windows=(_interval(9, 12), _interval(14, 18)),
            professional_windows=(_interval(9, 12), _interval(14, 18)),
            occupied_intervals=(),
            blocked_intervals=(),
            duration_minutes=60,
            slot_interval_minutes=15,
            local_date=dt.date(2026, 1, 12),
            timezone=TZ,
            min_lead_minutes=0,
        )
        slots = calculate_availability(input_data)

        # Slots en la primera ventana: 9:00, 9:15, ..., 11:00 (11:00+60=12:00)
        # Slots en la segunda ventana: 14:00, 14:15, ..., 17:00
        # Total: 9 + 13 = 22 slots
        assert len(slots) == 22

    def test_duration_longer_than_window(self):
        """Un servicio más largo que la ventana no genera slots."""
        input_data = AvailabilityInput(
            business_windows=(_interval(9, 10),),  # 1 hora
            professional_windows=(_interval(9, 10),),
            occupied_intervals=(),
            blocked_intervals=(),
            duration_minutes=90,  # 1.5 horas
            slot_interval_minutes=15,
            local_date=dt.date(2026, 1, 12),
            timezone=TZ,
            min_lead_minutes=0,
        )
        slots = calculate_availability(input_data)
        assert slots == []

    def test_zero_duration(self):
        """Duración cero no genera slots."""
        input_data = AvailabilityInput(
            business_windows=(_interval(9, 18),),
            professional_windows=(_interval(9, 18),),
            occupied_intervals=(),
            blocked_intervals=(),
            duration_minutes=0,
            slot_interval_minutes=15,
            local_date=dt.date(2026, 1, 12),
            timezone=TZ,
            min_lead_minutes=0,
        )
        slots = calculate_availability(input_data)
        assert slots == []


class TestFindEligibleProfessionals:
    """Tests para find_eligible_professionals."""

    def test_no_professionals(self):
        """Sin profesionales, no hay resultados."""
        result = calculate_availability(
            AvailabilityInput(
                business_windows=(_interval(9, 18),),
                professional_windows=(),
                occupied_intervals=(),
                blocked_intervals=(),
                duration_minutes=60,
                slot_interval_minutes=15,
                local_date=dt.date(2026, 1, 12),
                timezone=TZ,
                min_lead_minutes=0,
            )
        )
        assert result == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
