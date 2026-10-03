"""Clientes.

El cliente **no tiene cuenta**. Su identidad es el WhatsApp, y eso es lo que hace
que la reserva publica no necesite registro, ni password, ni recuperacion.

La unicidad es `(business_id, phone_e164)`. Dos personas que comparten un telefono
comparten registro, y eso es una decision consciente con riesgo conocido (R-11):
el §8 solo pide nombre, apellido y WhatsApp, asi que el modelo asume que el
telefono identifica al cliente. La alternativa (pedir email) agrega friccion al
embudo de reserva para cubrir un caso raro.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantBase, tenant_unique_key
from app.db.types import UtcDateTime


class Customer(TenantBase, Base):
    """Cliente de un negocio, identificado por su telefono en E.164."""

    __tablename__ = "customers"
    __table_args__ = (
        tenant_unique_key("customers"),
        ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_customers_business_id_businesses",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "is_opted_out = false OR marketing_opt_in = false", name="opt_out_clears_marketing"
        ),
        Index("uq_customers_business_id_phone", "business_id", "phone_e164", unique=True),
    )

    first_name: Mapped[str] = mapped_column(Text, nullable=False)
    last_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone_e164: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL significa "hereda del negocio". Guardar una copia congelada obligaria a
    # editar cada cliente cuando el negocio cambia su idioma, que no es lo que
    # nadie espera.
    locale: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # `is_opted_out` bloquea **todos** los mensajes. `marketing_opt_in` es
    # independiente: un cliente puede seguir recibiendo recordatorios de sus citas
    # y no querer publicidad. Son dos consentimientos distintos y se guardan
    # separados.
    is_opted_out: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    marketing_opt_in: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    last_booking_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def accepts_any_message(self) -> bool:
        return not self.is_opted_out


__all__ = ["Customer"]
