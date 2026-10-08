"""WhatsApp y notificaciones.

`notification_requests` es la fila que **garantiza** que un recordatorio existe, no
una tarea en memoria. Es la diferencia entre "el worker se cae y el cliente nunca
se entera de que tiene turno manana" y "el recordatorio esta en la base y sale
cuando el worker vuelve".

`UNIQUE (booking_id, kind)` es la garantia de no-envio-duplicado. Sin ella, un
reintento del job manda el mismo mensaje dos veces, que en WhatsApp es la razon
principal por la que un cliente bloquea a un negocio.

`whatsapp_connections` guarda los tokens **cifrados** con Fernet. No cifrados "por
buena practica": sin cifrar, un dump de la tabla da acceso a la cuenta de
WhatsApp del cliente, y esa cuenta puede facturar.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, GlobalBase, OptionalTenantBase, TenantBase, tenant_unique_key
from app.db.types import UtcDateTime
from app.models.enums import (
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
    TemplateStatus,
    WebhookProvider,
    WebhookStatus,
    WhatsAppConnectionStatus,
)
from app.models.sql_types import (
    notification_channel_enum,
    notification_kind_enum,
    notification_status_enum,
    template_status_enum,
    webhook_provider_enum,
    webhook_status_enum,
    whatsapp_status_enum,
)


class WhatsAppConnection(TenantBase, Base):
    """Conexion de WhatsApp de un negocio. Una por negocio."""

    __tablename__ = "whatsapp_connections"
    __table_args__ = (
        tenant_unique_key("whatsapp_connections"),
        ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_whatsapp_connections_business_id_businesses",
            ondelete="CASCADE",
        ),
        UniqueConstraint("business_id", name="uq_whatsapp_connections_business_id"),
        UniqueConstraint("waba_id", name="uq_whatsapp_connections_waba_id"),
    )

    phone_number_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: `waba_id`, `display_phone` y `app_secret_encrypted` son opcionales a
    #: proposito (0018): el flujo de "connect" de Meta los trae juntos, pero el
    #: opt-in simple de Fase D-3 solo pide `phone_number_id` y token.
    waba_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Fernet, no texto. La clave vive en `ENCRYPTION_KEY`, fuera de la base.
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    app_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    messaging_limit_tier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quality_rating: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[WhatsAppConnectionStatus] = mapped_column(
        whatsapp_status_enum, nullable=False, server_default=text("'pending'")
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    connected_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    token_expires_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    #: Nombre de la plantilla de cada recordatorio, o `NULL` para usar el
    #: default de la aplicacion (`recordatorio_24h` / `recordatorio_2h`).
    reminder_24h_template: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reminder_2h_template: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def can_send(self) -> bool:
        return self.is_active and self.status is WhatsAppConnectionStatus.ACTIVE


class WhatsAppTemplate(OptionalTenantBase, Base):
    """Plantilla de WhatsApp. `business_id` NULL = plantilla de plataforma.

    Los cuatro templates base que se ofrecen a todos viven con `business_id` NULL.
    Un negocio puede tener los suyos, y entonces los usa en lugar de los base.
    Meter los dos casos en una sola tabla colgandolos de un negocio "sistema"
    mezclaria el aislamiento por tenant con la pregunta de quien es el dueno.
    """

    __tablename__ = "whatsapp_templates"
    __table_args__ = (
        tenant_unique_key("whatsapp_templates"),
        ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_whatsapp_templates_business_id_businesses",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "business_id",
            "meta_name",
            "language",
            name="uq_whatsapp_templates_business_id_meta_name_language",
        ),
    )

    meta_name: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[TemplateStatus] = mapped_column(
        template_status_enum, nullable=False, server_default=text("'pending'")
    )
    # Nombres y posiciones de las variables: `{{1}}` nombre, `{{2}}` fecha.
    variables: Mapped[list[object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def is_platform_template(self) -> bool:
        return self.business_id is None


class NotificationRequest(TenantBase, Base):
    """Registro durable de "que hay que mandarle a quien y cuando".

    `scheduled_for` es UTC. Para el envio inmediato es "ahora", y el worker lo
    toma. La ventana de 24 horas de WhatsApp (§12.2) es la que decide si esto se
    puede mandar como plantilla o como mensaje libre.
    """

    __tablename__ = "notification_requests"
    __table_args__ = (
        tenant_unique_key("notification_requests"),
        ForeignKeyConstraint(
            ["booking_id", "business_id"],
            ["bookings.id", "bookings.business_id"],
            name="fk_notification_requests_booking",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["customer_id", "business_id"],
            ["customers.id", "customers.business_id"],
            name="fk_notification_requests_customer",
            ondelete="CASCADE",
        ),
        # La garantia de no-envio-duplicado.
        UniqueConstraint("booking_id", "kind", name="uq_notification_requests_booking_id_kind"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[NotificationKind] = mapped_column(notification_kind_enum, nullable=False)
    channel: Mapped[NotificationChannel] = mapped_column(
        notification_channel_enum, nullable=False, server_default=text("'whatsapp'")
    )
    scheduled_for: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(
        notification_status_enum, nullable=False, server_default=text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("3")
    )
    template_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    delivered_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def can_retry(self) -> bool:
        return self.attempts < self.max_attempts and self.status.value in ("pending", "failed")


class WebhookEvent(GlobalBase, Base):
    """Webhook entrante de Meta. `(provider, external_id)` unico.

    La unicidad es la idempotencia del lado receptor: Meta reintenta webhooks si no
    recibe 2xx, y un reintento no puede volver a aplicar el efecto. `received_at`
    se llena con el reloj de la **base** por la misma razon que en `jobs.run_at`.
    """

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_webhook_events_provider_external_id"),
    )

    provider: Mapped[WebhookProvider] = mapped_column(webhook_provider_enum, nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[WebhookStatus] = mapped_column(
        webhook_status_enum, nullable=False, server_default=text("'received'")
    )
    received_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=text("now()")
    )
    processed_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "NotificationRequest",
    "WebhookEvent",
    "WhatsAppConnection",
    "WhatsAppTemplate",
]
