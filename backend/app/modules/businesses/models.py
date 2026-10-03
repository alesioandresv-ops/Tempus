"""Tenancy: el negocio, sus medios y las palabras reservadas.

`businesses` es la raiz del aislamiento, asi que **no** lleva `business_id` ni RLS.
No es una excepcion a proposito: una politica de RLS sobre `businesses` que
comparase `business_id` contra si mismo no filtraria nada, y el filtrado real
ocurre en las tablas hijas. Lo que si importa es que el rol de la aplicacion no
pueda crear negocios: eso se resuelve con permisos, en la migracion.

`slug` va en `citext` y es UNIQUE global, no por negocio. El slug **es** la
identidad publica del negocio (el cliente reserva en `/p/{slug}`), asi que dos
negocios con el mismo slug seria una ambiguedad irresoluble, no un dato duplicado.
Por eso el §5 lo dice explicitamente: no se usa como clave foranea en ningun lado.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, GlobalBase, TenantBase, tenant_unique_key
from app.models.enums import BusinessStatus, MediaKind, MediaStatus
from app.models.sql_types import (
    business_status_enum,
    media_kind_enum,
    media_status_enum,
)


class Business(GlobalBase, Base):
    """El negocio. Raiz de todo el aislamiento por tenant."""

    __tablename__ = "businesses"
    __table_args__ = (
        CheckConstraint("slot_interval_minutes > 0", name="slot_interval_positive"),
        CheckConstraint("min_lead_minutes >= 0", name="min_lead_non_negative"),
        CheckConstraint("max_advance_days > 0", name="max_advance_days_positive"),
        CheckConstraint(
            "cancellation_window_minutes >= 0", name="cancellation_window_non_negative"
        ),
    )

    slug: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'America/Argentina/Buenos_Aires'")
    )
    locale: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'es-AR'"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'ARS'"))
    slot_interval_minutes: Mapped[int] = mapped_column(nullable=False, server_default=text("15"))
    min_lead_minutes: Mapped[int] = mapped_column(nullable=False, server_default=text("60"))
    max_advance_days: Mapped[int] = mapped_column(nullable=False, server_default=text("60"))
    cancellation_window_minutes: Mapped[int] = mapped_column(
        nullable=False, server_default=text("120")
    )
    status: Mapped[BusinessStatus] = mapped_column(
        business_status_enum, nullable=False, server_default=text("'trial'")
    )
    phone_e164: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand_color: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_media_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media.id", ondelete="SET NULL", name="fk_businesses_logo_media_id_media"),
        nullable=True,
    )
    cover_media_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media.id", ondelete="SET NULL", name="fk_businesses_cover_media_id_media"),
        nullable=True,
    )

    @property
    def is_bookable(self) -> bool:
        """Solo `active` acepta reservas. Un `trial` se configura, no se publica."""
        return self.status is BusinessStatus.ACTIVE


class Media(TenantBase, Base):
    """Metadatos de una imagen en R2.

    La **imagen no vive en Postgres**: aqui solo esta la clave del objeto, el
    tipo y las medidas. Guardar bytes en la base y duplicar R2 seria el peor de los
    dos mundos.

    `object_key` y no una URL: una URL firmada expira y el dia que expira la
    referencia queda muerta. La clave es permanente y la URL se genera al
    mostrarse.
    """

    __tablename__ = "media"
    __table_args__ = (
        tenant_unique_key("media"),
        CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),
        CheckConstraint("(width IS NULL) = (height IS NULL)", name="dimensions_paired"),
    )

    kind: Mapped[MediaKind] = mapped_column(media_kind_enum, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[MediaStatus] = mapped_column(
        media_status_enum, nullable=False, server_default=text("'pending'")
    )
    alt_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def is_usable(self) -> bool:
        return self.status is MediaStatus.READY


class SlugReservation(GlobalBase, Base):
    """Palabras que nadie puede tomar como slug.

    Se siembla con nombres de marca, palabras reservadas del sistema y dominios
    que no queremos que un negocio se apropie. Vive aparte de `businesses` y no
    tiene `business_id` a proposito: la reserva es anterior a que exista el
    negocio.
    """

    __tablename__ = "slug_reservations"

    slug: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


__all__ = [
    "Business",
    "Media",
    "SlugReservation",
]
