"""Horarios: ventanas, excepciones, feriados, agenda propia y bloqueos.

Cuatro reglas de este modulo que conviene tener presentes al tocarlo:

1. **No existe el concepto de "descanso"** dentro de una ventana. §11 lo prohibe
   explicitamente. Si un negocio abre 09 a 18 con un descanso de 13 a 14, eso son
   **dos filas** en `business_hours`, no una ventana con un hueco en el medio. Un
   unico modelo de ventana obligaria a la disponibilidad a restar descansos dentro
   de un intervalo, que es donde aparecen los slots fantasma.

2. **Las excepciones reemplazan, no suman.** Si hay `business_exception` para una
   fecha con ventanas, esas ventanas **sustituyen** a las de `business_hours` de ese
   dia. Es lo que permite abrir un domingo normalmente cerrado. Si se sumaran, no
   habria forma de abrir fuera de horario.

3. **`professional_schedules` es override, no definicion.** `is_override = false`
   significa "hereda el negocio". Se resolvio asi en ADR-0005 para que agregar un
   profesional no obligue a duplicar el horario del negocio.

4. **`weekday` va de 0 a 6 con el 0 = lunes.** Lunes-primero y no domingo-primero a
   proposito: el ISO-8601 usa lunes-primero, y entre Postgres, Python y el cliente
   hay tres lugares donde el mismo numero puede significar dos dias distintos. Un
   unico criterio evita el bug del "viernes" que aparece el jueves.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantBase, tenant_unique_key
from app.db.types import UtcDateTime
from app.models.enums import BlockKind, TimeOffKind, TimeOffStatus
from app.models.sql_types import (
    block_kind_enum,
    time_off_kind_enum,
    time_off_status_enum,
)

WEEKDAY_MIN = 0
WEEKDAY_MAX = 6

# `business_id` -> `businesses.id`. Se repite en cada tabla en vez de hacer una FK
# desde el mixin porque SQLAlchemy no resuelve una FK declarada en un mixin sin
# duplicarla en cada subclase; el helper existe para que sea una linea y no un
# bloque de texto repetido.


def _fk_business(name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["business_id"],
        ["businesses.id"],
        name=f"fk_{name}_business_id_businesses",
        ondelete="CASCADE",
    )


def _fk_professional(name: str) -> ForeignKeyConstraint:
    """FK compuesta al profesional. Es lo que impide cruzar tenants (§7)."""
    return ForeignKeyConstraint(
        ["professional_id", "business_id"],
        ["professionals.id", "professionals.business_id"],
        name=f"fk_{name}_professional",
        ondelete="CASCADE",
    )


class BusinessHour(TenantBase, Base):
    """Ventana de funcionamiento. Varias filas por dia = varias ventanas."""

    __tablename__ = "business_hours"
    __table_args__ = (
        tenant_unique_key("business_hours"),
        _fk_business("business_hours"),
        CheckConstraint(
            f"weekday BETWEEN {WEEKDAY_MIN} AND {WEEKDAY_MAX}", name="weekday_in_range"
        ),
        CheckConstraint("window_index >= 0", name="window_index_non_negative"),
        CheckConstraint("is_open = false OR end_time IS NOT NULL", name="open_window_has_end"),
        CheckConstraint("is_open = false OR end_time > start_time", name="end_after_start"),
        UniqueConstraint(
            "business_id",
            "weekday",
            "window_index",
            name="uq_business_hours_business_id_weekday_window_index",
        ),
    )

    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    window_index: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    start_time: Mapped[dt.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[dt.time | None] = mapped_column(Time, nullable=True)
    # `is_open = false` es como se modela "cerrado ese dia". Es una fila, no la
    # ausencia de filas: asi el admin ve el domingo en la grilla como una decision
    # tomada, y no como un dato que falta.
    is_open: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    @property
    def window(self) -> tuple[dt.time, dt.time] | None:
        """`(inicio, fin)` si la fila representa una ventana abierta."""
        if not self.is_open or self.end_time is None:
            return None
        return (self.start_time, self.end_time)


class BusinessException(TenantBase, Base):
    """Cambio excepcional de un dia concreto. Reemplaza a las ventanas de ese dia."""

    __tablename__ = "business_exceptions"
    __table_args__ = (
        tenant_unique_key("business_exceptions"),
        _fk_business("business_exceptions"),
        UniqueConstraint(
            "business_id", "local_date", name="uq_business_exceptions_business_id_local_date"
        ),
    )

    local_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    # `true` = cerrado y sin ventanas. `false` = las ventanas de
    # `business_exception_windows` sustituyen a las de `business_hours`.
    is_closed: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def is_open_override(self) -> bool:
        return not self.is_closed


class BusinessExceptionWindow(TenantBase, Base):
    """Ventanas de una excepcion."""

    __tablename__ = "business_exception_windows"
    __table_args__ = (
        tenant_unique_key("business_exception_windows"),
        ForeignKeyConstraint(
            ["exception_id", "business_id"],
            ["business_exceptions.id", "business_exceptions.business_id"],
            name="fk_business_exception_windows_exception",
            ondelete="CASCADE",
        ),
        CheckConstraint("window_index >= 0", name="window_index_non_negative"),
        CheckConstraint("end_time > start_time", name="end_after_start"),
        UniqueConstraint(
            "exception_id",
            "window_index",
            name="uq_business_exception_windows_exception_id_window_index",
        ),
    )

    exception_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    window_index: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    start_time: Mapped[dt.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[dt.time] = mapped_column(Time, nullable=False)


class Holiday(TenantBase, Base):
    """Feriado. No hay feriados nacionales automaticos: se cargan a mano.

    Un negocio que cierra el jueves por el feriado y otro que abre son dos
    negocios distintos, y la plataforma no puede decidirlo por ellos.
    """

    __tablename__ = "holidays"
    __table_args__ = (
        tenant_unique_key("holidays"),
        _fk_business("holidays"),
        UniqueConstraint("business_id", "local_date", name="uq_holidays_business_id_local_date"),
    )

    local_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class ProfessionalSchedule(TenantBase, Base):
    """Agenda propia del profesional. Solo como override (ADR-0005)."""

    __tablename__ = "professional_schedules"
    __table_args__ = (
        tenant_unique_key("professional_schedules"),
        _fk_professional("professional_schedules"),
        CheckConstraint(
            f"weekday BETWEEN {WEEKDAY_MIN} AND {WEEKDAY_MAX}", name="weekday_in_range"
        ),
        CheckConstraint("window_index >= 0", name="window_index_non_negative"),
        CheckConstraint("end_time > start_time", name="end_after_start"),
        UniqueConstraint(
            "professional_id",
            "weekday",
            "window_index",
            name="uq_professional_schedules_professional_id_weekday_window_index",
        ),
    )

    professional_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    window_index: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    start_time: Mapped[dt.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[dt.time] = mapped_column(Time, nullable=False)
    # `false` = hereda el negocio. La fila existe pero no manda: por eso es una
    # columna y no la ausencia de la fila.
    is_override: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))


class TimeOff(TenantBase, Base):
    """Vacaciones, licencias y ausencias. Bloquean solo si estan `approved`."""

    __tablename__ = "time_off"
    __table_args__ = (
        tenant_unique_key("time_off"),
        _fk_professional("time_off"),
        CheckConstraint("ends_at > starts_at", name="ends_after_starts"),
    )

    professional_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[TimeOffKind] = mapped_column(time_off_kind_enum, nullable=False)
    status: Mapped[TimeOffStatus] = mapped_column(
        time_off_status_enum, nullable=False, server_default=text("'pending'")
    )
    # Rango semiabierto `[starts_at, ends_at)` en UTC. Semiabierto porque una
    # ausencia que termina 12:00 no bloquea un turno de las 12:00, igual que un
    # turno que termina 12:00 no solapa con uno que empieza 12:00.
    starts_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False)
    ends_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def blocks_calendar(self) -> bool:
        """Un `pending` no bloquea: se aprueba despues."""
        return self.status is TimeOffStatus.APPROVED


class Block(TenantBase, Base):
    """Bloqueo puntual. `professional_id` NULL = bloqueo de todo el negocio."""

    __tablename__ = "blocks"
    # Anotado porque esta clase hace `__table_args__ += (...)` mas abajo: sin el
    # anotado, mypy fija el tipo a la tupla del primer assignment y rechaza el `+=`.
    __table_args__: tuple[Any, ...] = (
        tenant_unique_key("blocks"),
        ForeignKeyConstraint(
            ["professional_id", "business_id"],
            ["professionals.id", "professionals.business_id"],
            name="fk_blocks_professional",
            ondelete="CASCADE",
        ),
        CheckConstraint("ends_at > starts_at", name="ends_after_starts"),
        CheckConstraint(
            "(occupied_from IS NULL) = (occupied_to IS NULL)",
            name="occupied_range_paired",
        ),
        CheckConstraint(
            "occupied_from IS NULL OR occupied_to > occupied_from",
            name="occupied_to_after_from",
        ),
    )

    # Nullable a proposito: un bloqueo de negocio entero no pertenece a ningun
    # profesional. Por eso la FK es compuesta pero admite NULL, y en ese caso solo
    # la parte `business_id` de la FK aplica.
    professional_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    kind: Mapped[BlockKind] = mapped_column(block_kind_enum, nullable=False)
    starts_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False)
    ends_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False)
    occupied_from: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    occupied_to: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    __table_args__ += (
        Index("ix_blocks_occupied_range", "business_id", "occupied_from", "occupied_to"),
    )


__all__ = [
    "Block",
    "BusinessException",
    "BusinessExceptionWindow",
    "BusinessHour",
    "Holiday",
    "ProfessionalSchedule",
    "TimeOff",
]
