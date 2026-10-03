"""Reloj del dominio.

Todo el tiempo de negocio pasa por aca. La razon es concreta: los tests de
disponibilidad y de recordatorios necesitan simular "ahora" para comprobar
comportamientos que de otro modo solo se pueden probar el dia correcto a la hora
correcta. Con `datetime.now()` disperso por el codigo, esos tests no existen.

Ademas centraliza la regla de ADR-0004: la base guarda UTC, y la conversion al
timezone del negocio ocurre en un solo lugar.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from zoneinfo import ZoneInfo


def _real_clock() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# Fuente del "ahora". Reemplazable en tests.
_clock: Callable[[], dt.datetime] = _real_clock


def now() -> dt.datetime:
    """Instante actual, en UTC y con timezone.

    Nunca devuelve naive: un datetime naive no sabe que dia es.
    """
    return _clock().astimezone(dt.UTC)


def set_clock(clock: Callable[[], dt.datetime]) -> None:
    """Reemplaza la fuente del tiempo. Para tests."""
    global _clock
    _clock = clock


def reset_clock() -> None:
    """Restaura el reloj real."""
    global _clock
    _clock = _real_clock


def freeze_clock(moment: dt.datetime) -> Callable[[], None]:
    """Congela el reloj en `moment` y devuelve como restaurar el anterior.

    Lo que devuelve es un **restaurador**, no el reloj anterior. La diferencia
    importa en los tests: si devolviera el reloj previo, el que congena tendria que
    guardarlo a mano para deshacer, y basta con que uno lo olvide para que el reloj
    congelado se leaks al test siguiente y falle con un datetime de 2026.
    """
    global _clock
    if moment.tzinfo is None:
        raise ValueError("freeze_clock necesita un datetime con timezone")
    frozen = moment.astimezone(dt.UTC)
    previous = _clock

    def _frozen_clock() -> dt.datetime:
        return frozen

    _clock = _frozen_clock

    def restore() -> None:
        global _clock
        _clock = previous

    return restore


def to_local(moment: dt.datetime, timezone: ZoneInfo | str) -> dt.datetime:
    """Instante arbitrario en la hora local del negocio."""
    tz = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    return moment.astimezone(tz)


def now_local(timezone: ZoneInfo | str) -> dt.datetime:
    """Instante actual en la hora local del negocio."""
    return to_local(now(), timezone)


def local_date(moment: dt.datetime, timezone: ZoneInfo | str) -> dt.date:
    """Fecha local del negocio.

    Es la columna `bookings.local_date` y la que decide contra que `weekday` se
    resuelve un horario. Calcularla en UTC seria el bug R-03: en Buenos Aires, un
    turno de las 23:30 es del dia siguiente en UTC, y al dia siguiente no le
    corresponde el horario del dia anterior.
    """
    return to_local(moment, timezone).date()


def local_today(timezone: ZoneInfo | str) -> dt.date:
    """Fecha local del negocio hoy."""
    return to_local(now(), timezone).date()
