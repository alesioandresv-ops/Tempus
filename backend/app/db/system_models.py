"""La cola de trabajos y las tablas que no pertenecen a un dominio.

Tres tablas viven aca porque no tienen dueno de dominio:

- `jobs` es de la infraestructura de ejecución.
- `audit_log` es evidencia: es de todos los dominios y de ninguno.
- `rate_limit_buckets` es anterior al tenant.

`jobs` y `rate_limit_buckets` no llevan RLS, y en `jobs` la ausencia es
deliberada: el rol de la aplicacion lo usa sin contexto de tenant porque el
dispatcher saca trabajos de varias cuentas a la vez, y una politica que exigiera
`business_id` dejaria al worker sin poder leer su propia cola. Lo mismo con
`rate_limit_buckets`, que se consulta por IP y no por cuenta.

`audit_log` **si** lleva RLS, como el resto de las tablas con `business_id`: el
hecho de que la evidencia sea transversal no la exime de aislamiento. Un tenant
que llegue a la fila de otro por `business_id` tendria acceso a datos de otro
tenant, y "es evidencia" no es un criterio de aislamiento. La app escribe con
`business_id` explicito y nunca consulta sin filtro.

Lo que estas tablas no pueden es salirse del esquema ni tocar `platform_users`,
`businesses` ni `slug_reservations`, y eso se resuelve con permisos en la
migracion, no con RLS.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, GlobalBase, TenantBase, tenant_unique_key
from app.db.types import UtcDateTime
from app.models.enums import AuditActorType, JobKind, JobStatus
from app.models.sql_types import (
    audit_actor_type_enum,
    job_kind_enum,
    job_status_enum,
)


class Job(GlobalBase, Base):
    """Trabajo pendiente. La cola de §13.

    `business_id` es nullable a proposito: los jobs de plataforma (purga de
    `rate_limit_buckets`, recordatorio de `idempotency_keys`) no pertenecen a un
    negocio. Un handler de negocio **debe** leerlo y fallar ruidosamente si es
    NULL; no se ignora en silencio.

    `run_at` lo pone `now()` de la base, no el reloj de la aplicacion. Con el
    reloj de la app, un worker con el reloj corrido agenda todo en el pasado o en
    el futuro y la cola se vacia de golpe o no se vacia.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        UniqueConstraint("dedupe_key", name="uq_jobs_dedupe_key"),
        # Indice parcial. Solo se consultan los `pending` que ya tocan su
        # `run_at`; un indice completo obliga a filtrar en memoria lo que el
        # planner puede descartar.
        Index(
            "ix_jobs_ready",
            "run_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    queue: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[JobKind] = mapped_column(job_kind_enum, nullable=False)
    business_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    run_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=text("now()")
    )
    status: Mapped[JobStatus] = mapped_column(
        job_status_enum, nullable=False, server_default=text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("3")
    )
    # Leasing: un worker toma el trabajo con `locked_by = su id` y `locked_at`. Si
    # el proceso muere, otro worker ve el lock vencido y lo reintenta. Sin esto, un
    # crash deja el trabajo en `running` para siempre.
    locked_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Clave de deduplicacion. NULL = sin deduplicar. Lo usan los jobs que se crean
    # desde una accion ya idempotente, para que un doble clic no encole dos
    # recordatorios aunque la transaccion que los crea se repita.
    dedupe_key: Mapped[str | None] = mapped_column(nullable=True)

    @property
    def is_terminal(self) -> bool:
        return self.status in (JobStatus.DONE, JobStatus.DEAD)


class AuditLog(TenantBase, Base):
    """Evidencia de que paso. Append-only y de retencion larga.

    Los logs de aplicacion son efimeros: un despliegue los borra. Esto responde
    "¿quien toco este dato de este negocio y cuando?", y tiene que seguir
    respondiendolo un ano despues.

    `actor_id` es polymorphico a proposito: un cliente no tiene fila propia, y su
    `actor_id` es el id del cliente. Por eso va `actor_type` al lado.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        tenant_unique_key("audit_log"),
        ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_audit_log_business_id_businesses",
            ondelete="CASCADE",
        ),
    )

    actor_type: Mapped[AuditActorType] = mapped_column(audit_actor_type_enum, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    meta: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    ip_address: Mapped[str | None] = mapped_column(nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)


class RateLimitBucket(GlobalBase, Base):
    """Contador de peticiones. La **unica** tabla sin `business_id`.

    El limite se aplica por IP y por email, que son valores que existen antes de que
    haya tenant: un intento de login fallido hay que poder frenarlo antes de saber
    de que negocio viene. Por eso no lleva RLS, y por eso es unica.

    `key` es un **hash** de la IP o del identificador, nunca el valor en claro: esta
    tabla se purga con un job de retencion y nadie deberia poder leer de ella una
    lista de IPs de clientes.
    """

    __tablename__ = "rate_limit_buckets"
    __table_args__ = (
        CheckConstraint("count >= 0", name="count_non_negative"),
        UniqueConstraint("key", "window_start", name="uq_rate_limit_buckets_key_window_start"),
    )

    key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # Inicio de la ventana. Con ventana fija es el piso a la granularidad del
    # periodo; `hits` guarda los timestamps de la ventana deslizante.
    window_start: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Timestamps de la ventana deslizante, para §10.5.
    hits: Mapped[list[object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)

    def is_exceeded(self, limit: int) -> bool:
        return self.count > limit

    def retry_after(self) -> int:
        return max(1, int((self.window_start.timestamp() % 60) or 60))


__all__ = [
    "AuditLog",
    "Job",
    "RateLimitBucket",
]
