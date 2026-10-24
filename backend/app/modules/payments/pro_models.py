"""Modelos de los pagos del plan Pro por transferencia (sin pasarela).

Sigue el patron de `mp_subscriptions`: `TenantBase` aporta el id (uuidv7 del
servidor), `business_id` indexado y los timestamps, y aca solo viven las columnas
propias. La RLS, el trigger de `updated_at` y los grants a `tempus_app` los agrega
la migracion `0027_pro_payments_transferencia`.
"""

from __future__ import annotations

import datetime as dt
import enum
from decimal import Decimal

from sqlalchemy import Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantBase, tenant_unique_key
from app.db.types import UtcDateTime

class ProPaymentStatus(enum.StrEnum):
    """Estado de un intento de pago del plan Pro.

    `pending` nace cuando el dueno confirma el envio del comprobante por WhatsApp.
    `approved` lo pone el admin y activa 30 dias de Pro. `rejected` lo pone el
    admin cuando el comprobante no corresponde (monto mal, transferencia
    inexistente): el negocio sigue vencido y puede volver a mandar.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


from sqlalchemy import Enum as SQLAlchemyEnum  # noqa: E402

pro_payment_status_enum = SQLAlchemyEnum(
    ProPaymentStatus,
    name="pro_payment_status",
    values_callable=lambda e: [m.value for m in e],
    native_enum=True,
)


class ProPayment(TenantBase, Base):
    """Un intento de pago del plan Pro por transferencia.

    `business_name` y `email` viven denormalizados a proposito: son los datos que
    el dueno declaro al enviar el comprobante y los que el admin usa para decidir.
    Que el negocio luego cambie de nombre no reescribe la historia del pago. El
    `email` es el identificador con el que el soporte cruza el comprobante del
    banco con la cuenta de Tempus.
    """

    __tablename__ = "pro_payments"
    __table_args__ = (tenant_unique_key("pro_payments"),)

    business_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'ARS'"))
    status: Mapped[ProPaymentStatus] = mapped_column(
        pro_payment_status_enum, nullable=False, server_default=text("'pending'")
    )
    method: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'naranja_x'"))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    @property
    def is_pending(self) -> bool:
        return self.status is ProPaymentStatus.PENDING


__all__ = ["ProPayment", "ProPaymentStatus", "pro_payment_status_enum"]
