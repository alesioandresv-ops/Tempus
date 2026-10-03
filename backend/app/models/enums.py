"""Enums del dominio.

Viven en un solo modulo y no pegados a cada modelo, porque los mismos valores
aparecen en dos capas: el enum de SQLAlchemy genera el tipo en PostgreSQL, y el
mismo objeto se usa para validar en Python. Dos copias del mismo enum divergen.

Son enums **nativos** de PostgreSQL, no `text` con un CHECK. La diferencia no es
estetica: `ALTER TYPE ... ADD VALUE` no es transaccional, asi que un valor nuevo
no se puede añadir dentro de una migracion que ademas sage en bloque. La
alternativa (`text` + CHECK) se paga en cada escritura con un indice extra.
"""

from __future__ import annotations

import enum

#: Enum de string, para que el valor que va a la base sea el nombre del miembro.
#:
#: Se usa `enum.StrEnum` de la libreria estandar y no la clase artesanal
#: `class StrEnum(str, Enum)` que estaba antes. La artesanal obliga a redefinir
#: `__str__` a mano y se comporta distinto en las comparaciones y en el formateo,
#: que es exactamente el tipo de diferencia que aparece a las tres de la manana.
StrEnum = enum.StrEnum


class BusinessStatus(StrEnum):
    ACTIVE = "active"
    TRIAL = "trial"
    SUSPENDED = "suspended"


class BusinessUserRole(StrEnum):
    """Privilegio dentro del panel del negocio. No es lo mismo que ser profesional.

    `PROFESSIONAL` no describe una capacidad sino un **limite**: un login de
    profesional ve su propia agenda y no la del negocio. Es un valor propio y no una
    bandera, porque el §10.4 lo pone en la misma tabla que `admin` y `staff`, y porque
    derivarlo de "este login tiene fila en `professionals`" no alcanza: `staff` ya
    concede ver la agenda completa, asi que un login que fuera profesional y staff a la
    vez tendria que ver todo, que es justo lo que el §10.4 le prohibe.

    La combinacion "profesional que ademas gestiona el equipo" se resuelve hoy con dos
    cuentas, no con un cuarto rol: son dos personas distintas con la misma abilities
    tecnicas y expectativas distintas. Si aparece el caso real, es un rol nuevo y su
    propio ADR.
    """

    ADMIN = "admin"
    STAFF = "staff"
    PROFESSIONAL = "professional"


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    INVITED = "invited"
    DISABLED = "disabled"
    #: Bloqueo por seguridad: reuso de un refresh token (§10.3). No lo levanta un
    #: admin desde el panel, se levanta revisando el `audit_log`. `DISABLED` y
    #: `LOCKED` separe porque son decisiones de personas distintas con salidas
    #: distintas, y un incidente de robo no puede verse como una baja de rutina.
    LOCKED = "locked"


class PlatformRole(StrEnum):
    OWNER = "owner"
    SUPPORT = "support"
    ADMIN = "admin"


class BookingStatus(StrEnum):
    PENDING_HOLD = "pending_hold"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class BookingSource(StrEnum):
    PUBLIC = "public"
    ADMIN = "admin"
    WALKIN = "walkin"


class BookingEventType(StrEnum):
    CREATED = "created"
    CANCELLED = "cancelled"
    RESCHEDULED = "rescheduled"
    RESCHEDULED_TO = "rescheduled_to"
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    WALKIN_REGISTERED = "walkin_registered"
    TOKEN_ROTATED = "token_rotated"


class TimeOffKind(StrEnum):
    VACATION = "vacation"
    LEAVE = "leave"
    SICK = "sick"
    ABSENCE = "absence"


class TimeOffStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class BlockKind(StrEnum):
    UNPAID = "unpaid"
    PRIVATE = "private"
    BLOCKED = "blocked"


class NotificationKind(StrEnum):
    BOOKING_CONFIRMED = "booking_confirmed"
    BOOKING_CANCELLED = "booking_cancelled"
    BOOKING_RESCHEDULED = "booking_rescheduled"
    REMINDER_24H = "reminder_24h"
    REMINDER_2H = "reminder_2h"
    REMINDER_1H = "reminder_1h"
    PROFESSIONAL_NEW_BOOKING = "professional_new_booking"
    PROFESSIONAL_BOOKING_CANCELLED = "professional_booking_cancelled"


class NotificationChannel(StrEnum):
    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class WebhookStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    FAILED = "failed"
    IGNORED = "ignored"


class WebhookProvider(StrEnum):
    META = "meta"


class TemplateStatus(StrEnum):
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"


class WhatsAppConnectionStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"


class MediaKind(StrEnum):
    LOGO = "logo"
    COVER = "cover"
    PROFESSIONAL_PHOTO = "professional_photo"


class MediaStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


class JobKind(StrEnum):
    REMINDER = "reminder"
    WHATSAPP_OUTBOUND = "whatsapp_outbound"
    WEBHOOK = "webhook"
    MEDIA = "media"
    REPORT = "report"


class AuditActorType(StrEnum):
    USER = "user"
    CUSTOMER = "customer"
    SYSTEM = "system"


__all__ = [
    "AuditActorType",
    "BlockKind",
    "BookingEventType",
    "BookingSource",
    "BookingStatus",
    "BusinessStatus",
    "BusinessUserRole",
    "JobKind",
    "JobStatus",
    "MediaKind",
    "MediaStatus",
    "MembershipStatus",
    "NotificationChannel",
    "NotificationKind",
    "NotificationStatus",
    "PlatformRole",
    "StrEnum",
    "TemplateStatus",
    "TimeOffKind",
    "TimeOffStatus",
    "WebhookProvider",
    "WebhookStatus",
    "WhatsAppConnectionStatus",
]
