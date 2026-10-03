"""Los tipos `ENUM` de PostgreSQL, definidos una sola vez.

Centralizarlos no es estetica. Un `SAEnum` es un objeto con estado: registra el
tipo en el `MetaData` y lo crea en la base. Si dos modulos crean un `SAEnum` con
el mismo `name`, Alembic ve dos definiciones del mismo tipo y la migracion
intenta crearlo dos veces, o genera un `ALTER TYPE` espurio en cada autogenerate.

Los `values_callable` son lo que hace que en la base se guarde `"confirmed"` y no
`"BookingStatus.CONFIRMED"`. Sin eso, leer una fila devuelve un enum de Python por
coincidencia de nombre y escribir una la rompe en cuanto se renombra un miembro.
"""

from __future__ import annotations

from sqlalchemy import Enum as SAEnum

from app.models.enums import (
    AuditActorType,
    BlockKind,
    BookingEventType,
    BookingSource,
    BookingStatus,
    BusinessStatus,
    BusinessUserRole,
    JobKind,
    JobStatus,
    MediaKind,
    MediaStatus,
    MembershipStatus,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
    PlatformRole,
    TemplateStatus,
    TimeOffKind,
    TimeOffStatus,
    WebhookProvider,
    WebhookStatus,
    WhatsAppConnectionStatus,
)


def _enum(py_enum: type, name: str) -> SAEnum:
    return SAEnum(py_enum, name=name, values_callable=lambda members: [m.value for m in members])


platform_role_enum = _enum(PlatformRole, "platform_role")
business_user_role_enum = _enum(BusinessUserRole, "business_user_role")
membership_status_enum = _enum(MembershipStatus, "membership_status")
business_status_enum = _enum(BusinessStatus, "business_status")
media_kind_enum = _enum(MediaKind, "media_kind")
media_status_enum = _enum(MediaStatus, "media_status")
booking_status_enum = _enum(BookingStatus, "booking_status")
booking_source_enum = _enum(BookingSource, "booking_source")
booking_event_type_enum = _enum(BookingEventType, "booking_event_type")
audit_actor_type_enum = _enum(AuditActorType, "audit_actor_type")
time_off_kind_enum = _enum(TimeOffKind, "time_off_kind")
time_off_status_enum = _enum(TimeOffStatus, "time_off_status")
block_kind_enum = _enum(BlockKind, "block_kind")
notification_kind_enum = _enum(NotificationKind, "notification_kind")
notification_channel_enum = _enum(NotificationChannel, "notification_channel")
notification_status_enum = _enum(NotificationStatus, "notification_status")
webhook_provider_enum = _enum(WebhookProvider, "webhook_provider")
webhook_status_enum = _enum(WebhookStatus, "webhook_status")
template_status_enum = _enum(TemplateStatus, "template_status")
whatsapp_status_enum = _enum(WhatsAppConnectionStatus, "whatsapp_connection_status")
job_status_enum = _enum(JobStatus, "job_status")
job_kind_enum = _enum(JobKind, "job_kind")

__all__ = [
    "audit_actor_type_enum",
    "block_kind_enum",
    "booking_event_type_enum",
    "booking_source_enum",
    "booking_status_enum",
    "business_status_enum",
    "business_user_role_enum",
    "job_kind_enum",
    "job_status_enum",
    "media_kind_enum",
    "media_status_enum",
    "membership_status_enum",
    "notification_channel_enum",
    "notification_kind_enum",
    "notification_status_enum",
    "platform_role_enum",
    "template_status_enum",
    "time_off_kind_enum",
    "time_off_status_enum",
    "webhook_provider_enum",
    "webhook_status_enum",
    "whatsapp_status_enum",
]
