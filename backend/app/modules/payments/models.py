"""Modelos de las tablas de tenant de Mercado Pago (Fase 9.1).

Siguen el patron de `WhatsAppConnection`: `TenantBase` aporta el id (uuidv7 del
servidor), `business_id` indexado y los timestamps, y aca solo viven las columnas
propias de cada tabla. La RLS, los triggers de `updated_at` y los grants a
`tempus_app` los agrega la migracion `0019_mercado_pago`, como hace `0002` con
las tablas de `0001`.

Las tres tablas son de tenant: el GUC `app.current_business_id` filtra las filas
via RLS, y por eso todas llevan `tenant_unique_key` (`UNIQUE (id, business_id)`)
para que las FK compuestas puedan referenciarlas.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantBase, tenant_unique_key
from app.db.types import UtcDateTime
from app.models.enums import (
    BusinessPlan,
    DepositMode,
    MpPaymentStatus,
    MpSubscriptionStatus,
    PaymentPolicy,
)
from app.models.sql_types import (
    business_plan_enum,
    deposit_mode_enum,
    mp_payment_status_enum,
    mp_subscription_status_enum,
    payment_policy_enum,
)


class MpPaymentAccount(TenantBase, Base):
    """Credenciales de Mercado Pago de un negocio. Una fila por negocio."""

    __tablename__ = "mp_payment_accounts"
    __table_args__ = (
        tenant_unique_key("mp_payment_accounts"),
        ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_mp_payment_accounts_business_id_businesses",
            ondelete="CASCADE",
        ),
        UniqueConstraint("business_id", name="uq_mp_payment_accounts_business_id"),
        CheckConstraint(
            "char_length(access_token_encrypted) > 0",
            name="access_token_not_empty",
        ),
    )

    #: Access token cifrado con Fernet (la clave vive en `ENCRYPTION_KEY`, fuera
    #: de la base; mismo patron que `whatsapp_connections`).
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    public_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    @property
    def can_process_payments(self) -> bool:
        return self.is_active and bool(self.access_token_encrypted)


class MpSubscription(TenantBase, Base):
    """Suscripcion de un negocio a un plan (preapproval de Mercado Pago).

    `status` son valores propios, no los de MP: `authorized` de MP se guarda como
    `active` aca, y `suspended` como `paused`. El downgrade a `free` por mora o
    cancelacion es un UPDATE de `businesses.plan`, decidido por el servicio de
    suscripciones.
    """

    __tablename__ = "mp_subscriptions"
    __table_args__ = (
        tenant_unique_key("mp_subscriptions"),
        ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_mp_subscriptions_business_id_businesses",
            ondelete="CASCADE",
        ),
        UniqueConstraint("business_id", name="uq_mp_subscriptions_business_id"),
        UniqueConstraint("preapproval_id", name="uq_mp_subscriptions_preapproval_id"),
    )

    preapproval_id: Mapped[str] = mapped_column(String(64), nullable=False)
    preapproval_plan_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    plan: Mapped[BusinessPlan] = mapped_column(
        business_plan_enum, nullable=False, server_default=text("'free'")
    )
    status: Mapped[MpSubscriptionStatus] = mapped_column(
        mp_subscription_status_enum,
        nullable=False,
        server_default=text("'pending'"),
    )
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'ARS'"))
    frequency: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'months'")
    )
    frequency_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    next_billing_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    activated_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    @property
    def is_active(self) -> bool:
        return self.status is MpSubscriptionStatus.ACTIVE


class BookingPayment(TenantBase, Base):
    """Pago (o seña) de una reserva via Mercado Pago.

    `booking_id` UNIQUE: una reserva tiene un solo pago; si la politica cambia a
    mitad de camino el registro se actualiza, no se agrega. `payment_id` es
    nullable porque nace con la preferencia (antes de que MP emita un pago) y se
    completa cuando llega el webhook.
    """

    __tablename__ = "booking_payments"
    __table_args__ = (
        tenant_unique_key("booking_payments"),
        ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_booking_payments_business_id_businesses",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["booking_id", "business_id"],
            ["bookings.id", "bookings.business_id"],
            name="fk_booking_payments_booking",
            ondelete="CASCADE",
        ),
        UniqueConstraint("booking_id", name="uq_booking_payments_booking_id"),
        UniqueConstraint("payment_id", name="uq_booking_payments_payment_id"),
        CheckConstraint(
            "refunded_amount >= 0",
            name="refunded_amount_non_negative",
        ),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    preference_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[MpPaymentStatus] = mapped_column(
        mp_payment_status_enum, nullable=False, server_default=text("'pending'")
    )
    policy: Mapped[PaymentPolicy] = mapped_column(payment_policy_enum, nullable=False)
    deposit_mode: Mapped[DepositMode | None] = mapped_column(deposit_mode_enum, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'ARS'"))
    refunded_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, server_default=text("0")
    )
    refunded_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def is_approved(self) -> bool:
        return self.status is MpPaymentStatus.APPROVED


__all__ = ["BookingPayment", "MpPaymentAccount", "MpSubscription"]
