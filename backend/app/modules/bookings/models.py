"""Reservas: la tabla central, su auditoria y la idempotencia.

Tres decisiones de este modulo concentran casi todo el riesgo del sistema:

1. **`professional_id` nunca es NULL**, ni siquiera cuando el cliente pide
   "cualquier profesional". En ese caso el backend elige el candidato con menos
   carga y lo persiste. La diferencia entre "se le asigno alguien" y "pendiente de
   asignar" es una clase entera de estados inconsistentes, y esta es la forma de
   que no exista.

2. **`occupied_from` / `occupied_to` son distintos de `starts_at` / `ends_at`.**
   El primero es el intervalo que bloquea la agenda e incluye el buffer entre
   turnos; el segundo es el servicio que se le vende al cliente. Sin separarlos, un
   buffer de 5 minutos entre turnos se pisa con el turno siguiente y aparecen
   solapamientos fantasma. Cancelar libera el intervalo del servicio y recalcula el
   ocupado; las reservas de al lado siguen bloqueando.

3. **La restriccion de exclusion es la autoridad anti-doble-reserva** (§9 Capa 1).
   Un `SELECT` seguido de `INSERT` es un check-then-act: dos transacciones lo pasan
   a la vez y las dos escriben. `EXCLUDE USING gist` se evalua dentro de la
   transaccion que inserta, con el lock que toma la propia base, asi que no hay
   ventana entre verificar y escribir.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantBase, tenant_unique_key
from app.db.types import UtcDateTime
from app.models.enums import AuditActorType, BookingEventType, BookingSource, BookingStatus
from app.models.sql_types import (
    audit_actor_type_enum,
    booking_event_type_enum,
    booking_source_enum,
    booking_status_enum,
)

# Estados que ocupan la agenda. Cancelar y completar sacan la fila del predicado
# de la restriccion, que es lo que libera el horario sin borrar nada.
OCCUPYING_STATUSES = ("confirmed", "pending_hold")

#: La columna de version la resuelve `__mapper_args__` pasando **el nombre del
#: atributo**, no un string.
#:
#: El string `"version"` no lo resuelve SQLAlchemy: lo coerciona a `TextClause` y
#: revienta en el primer `flush` con `ArgumentError: Textual column expression
#: 'version' should be explicitly declared with text('version')`. Es decir, el
#: error aparece a un INSERT de una reserva, a metros del `__mapper_args__` que lo
#: causa, y no al importar el modulo.
#:
#: Tampoco sirve `sqlalchemy.column("version")` a nivel de modulo: construye una
#: `ColumnClause` suelta que no pertenece a la tabla, asi que el mapper la acepta
#: pero no la encuentra en `local_table` y termina fallando con
#: `UnmappedColumnError: No column version is configured on mapper`.
#:
#: Y tampoco `Booking.__mapper_args__ = {...}` despues de la clase: el mapper ya
#: se capturo el dict al construirse.
#:
#: Lo que si funciona es pasar el atributo. El cuerpo de la clase se ejecuta de
#: arriba abajo y `version` ya esta definido cuando llega a `__mapper_args__`, que
#: esta mas abajo. El nombre se coerciona a la `Column` real de la tabla, con lo
#: cual el versionado optimista queda activo de verdad: dos admins editando la
#: misma reserva, el segundo UPDATE lleva `WHERE version = <viejo>`, no matchea y
#: sale `StaleDataError` en vez de pisar el primero.


class Booking(TenantBase, Base):
    """Una reserva. La fila que define un turno ocupado."""

    __tablename__ = "bookings"
    __table_args__ = (
        tenant_unique_key("bookings"),
        ForeignKeyConstraint(
            ["customer_id", "business_id"],
            ["customers.id", "customers.business_id"],
            name="fk_bookings_customer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["professional_id", "business_id"],
            ["professionals.id", "professionals.business_id"],
            name="fk_bookings_professional",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["service_id", "business_id"],
            ["services.id", "services.business_id"],
            name="fk_bookings_service",
            ondelete="RESTRICT",
        ),
        # Cadena de reprogramaciones. `ON DELETE SET NULL` porque una reserva
        # cancelada no debe borrar su historia: la fila origen sobrevive como
        # evidencia de que existio un enlace.
        ForeignKeyConstraint(
            ["rescheduled_from_id", "business_id"],
            ["bookings.id", "bookings.business_id"],
            name="fk_bookings_rescheduled_from",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["business_users.id"],
            name="fk_bookings_created_by_user_id_business_users",
            ondelete="SET NULL",
        ),
        CheckConstraint("ends_at > starts_at", name="ends_after_starts"),
        CheckConstraint("occupied_from <= starts_at", name="occupied_starts_before_service"),
        CheckConstraint("occupied_to >= ends_at", name="occupied_ends_after_service"),
        CheckConstraint("duration_minutes > 0", name="duration_positive"),
        CheckConstraint("price_snapshot >= 0", name="price_snapshot_non_negative"),
        CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)",
            name="cancelled_flag_matches_timestamp",
        ),
        # `btree_gist` es lo que permite mezclar la igualdad de `professional_id`
        # con el rango de `tstzrange` en una sola restriccion EXCLUDE.
        #
        # Las columnas van como `text()` y no como atributos. En el cuerpo de la
        # clase, cuando se evalua `__table_args__`, `occupied_from` todavia no
        # existe como nombre: pasarlo a `func.tstzrange(...)` da NameError. La
        # referencia textual se resuelve al compilar el DDL, que es lo unico que
        # hace esta expresion.
        ExcludeConstraint(
            ("professional_id", "="),
            (func.tstzrange(text("occupied_from"), text("occupied_to"), "[)"), "&&"),
            using="gist",
            where=text(f"status IN {OCCUPYING_STATUSES}"),
            name="no_overlap",
        ),
        Index("ix_bookings_business_id_local_date", "business_id", "local_date"),
        Index("ix_bookings_professional_id_starts_at", "professional_id", "starts_at"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    professional_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    service_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        booking_status_enum, nullable=False, server_default=text("'pending_hold'")
    )
    source: Mapped[BookingSource] = mapped_column(
        booking_source_enum, nullable=False, server_default=text("'public'")
    )
    # `starts_at` / `ends_at` son UTC. `local_date` es el dia local del negocio y
    # se calcula con el timezone del negocio, nunca en UTC: un turno de las 23:30
    # en Buenos Aires es del dia siguiente en UTC, y al dia siguiente no le
    # corresponde el horario del dia anterior.
    starts_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False)
    ends_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False)
    occupied_from: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False)
    occupied_to: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False)
    local_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    # Snapshots. Cambiar el precio o la duracion de un servicio no debe alterar
    # las reservas ya tomadas: el cliente acordo un precio concreto.
    duration_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    price_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # SHA-256 del secure_token. Se guarda el hash porque el token viaja en el link
    # publico de cancelar/reprogramar, y un link es un correo que se reenvia.
    secure_token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    token_rotated_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rescheduled_from_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    # Optimistic locking. Dos admins que editan la misma reserva: el segundo
    # UPDATE no entra y recibe un 409 en vez de pisar el primero.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1"), default=1
    )
    # La reserva nace confirmada, no "pendiente de confirmacion". Un estado
    # intermedio seria un turno que el negocio ve y no puede cobrar.
    confirmed_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=text("now()")
    )
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    # Sin `ClassVar` a proposito: SQLAlchemy declara `__mapper_args__` como atributo
    # de instancia en su base, y marcarlo como variable de clase rompe el override
    # (`Cannot override instance variable with class variable`). Se silencia RUF012 con
    # su razon porque el dict es de configuracion, no estado mutable por instancia.
    #
    # `version` y no `VERSION_COLUMN` ni el string: ver la nota de arriba. Este
    # atributo tiene que quedar *despues* de la declaracion de `version` en el
    # cuerpo de la clase, o el nombre no resuelve.
    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012

    @property
    def occupies_calendar(self) -> bool:
        """Si este turno ocupa la agenda y bloquea el `EXCLUDE`.

        Se compara con `in` y no con `.value` a proposito. `BookingStatus` es un
        `StrEnum`, asi que el miembro y su texto se comparan igual, y la propiedad
        da el mismo resultado este el atributo sea el enum--que es lo que trae una
        fila leida de la base-- o el texto--que es lo que tiene una fila creada en
        memoria y todavia no ida a la base.

        Con `.value` la propiedad funciona para el primer caso y revienta con
        `AttributeError` para el segundo, que es el que se consulta justo despues de
        crear el turno. Un fallo que depende de si el objeto hizo un viaje a la base
        no es un fallo que se pueda reproducir leyendo el codigo.
        """
        return self.status in OCCUPYING_STATUSES

    @property
    def buffer_minutes(self) -> int:
        """Minutos de limpieza antes y despues, en total."""
        before = (self.starts_at - self.occupied_from).total_seconds() / 60
        after = (self.occupied_to - self.ends_at).total_seconds() / 60
        return int(before + after)


class BookingEvent(TenantBase, Base):
    """Auditoria append-only de una reserva. Nunca se borra.

    Es lo que permite responder "¿que paso con esta reserva?" y la base de las
    estadisticas futuras. Se escribe en la **misma transaccion** que el cambio: si
    el evento quedara para despues, un fallo entre ambos dejaria el estado sin
    explicacion.
    """

    __tablename__ = "booking_events"
    # Se anota como `tuple[Any, ...]` porque mas abajo esta clase hace
    # `__table_args__ += (...)`. Sin el anotado, mypy fija el tipo a la tupla de
    # largo N del primer assignment y despues rechaza el `+=` por tener un elemento
    # mas. No es un problema de tipos reales: los dos son `ConstraintElement`.
    __table_args__: tuple[Any, ...] = (
        tenant_unique_key("booking_events"),
        ForeignKeyConstraint(
            ["booking_id", "business_id"],
            ["bookings.id", "bookings.business_id"],
            name="fk_booking_events_booking",
            ondelete="CASCADE",
        ),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    type: Mapped[BookingEventType] = mapped_column(booking_event_type_enum, nullable=False)
    actor_type: Mapped[AuditActorType] = mapped_column(audit_actor_type_enum, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    from_starts_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    to_starts_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    @property
    def summary(self) -> str:
        """Texto legible para la linea de tiempo del panel."""
        if self.type is BookingEventType.RESCHEDULED and self.from_starts_at and self.to_starts_at:
            return (
                f"Reprogramada de {self.from_starts_at.isoformat()} "
                f"a {self.to_starts_at.isoformat()}"
            )
        return self.type.value


class IdempotencyKey(TenantBase, Base):
    """Una peticion que ya fue atendida.

    `UNIQUE (business_id, scope, key)`: un doble clic o un reintento de red
    recuperan la respuesta original en vez de crear una segunda reserva. El
    `request_hash` es lo que detecta el caso peligroso: la misma key con un cuerpo
    **distinto** es un bug del cliente, no un reintento, y se rechaza con 409 en
    lugar de devolver una respuesta que no corresponde.
    """

    __tablename__ = "idempotency_keys"
    # Anotado por el mismo motivo que en `booking_events`: hay un `+=` mas abajo.
    __table_args__: tuple[Any, ...] = (
        tenant_unique_key("idempotency_keys"),
        UniqueConstraint(
            "business_id", "scope", "key", name="uq_idempotency_keys_business_id_scope_key"
        ),
        CheckConstraint(
            "response_status IS NULL OR (response_status >= 100 AND response_status < 600)",
            name="response_status_valid",
        ),
    )

    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    expires_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False)
    locked_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    __table_args__ += (
        UniqueConstraint(
            "business_id", "scope", "key", name="uq_idempotency_keys_business_id_scope_key"
        ),
    )


# Indices fuera del cuerpo de la clase. Un `Index(...)` declarado en
# `__table_args__` que menciona una columna que viene de un mixin (`created_at`)
# falla al resolverla, porque en el momento de evaluar `__table_args__` la tabla
# todavia no existe. Aqui si existe.
Index("ix_booking_events_booking_id_created_at", BookingEvent.booking_id, BookingEvent.created_at)
Index("ix_idempotency_keys_expires_at", IdempotencyKey.expires_at)


__all__ = [
    "OCCUPYING_STATUSES",
    "Booking",
    "BookingEvent",
    "IdempotencyKey",
]
