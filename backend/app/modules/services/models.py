"""Catalogo: servicios y que profesional puede hacer cual.

`professional_services.business_id` esta desnormalizado a proposito. Existe solo
para que las FK compuestas existan: sin el par `(professional_id, business_id)` en
la misma fila, esta tabla no puede referenciar a `professionals` de forma que el
tenant quede implicito, y la garantia de la §7 se pierde. El costo es una columna
mas; el beneficio es que cruzar tenants es fisicamente imposible, no una
convencion que depende de que todos escriban el filtro.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantBase, TenantMixin, TimestampsMixin, tenant_unique_key
from app.db.types import UtcDateTime


class Service(TenantBase, Base):
    """Un servicio que el negocio ofrece."""

    __tablename__ = "services"
    __table_args__ = (
        tenant_unique_key("services"),
        ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_services_business_id_businesses",
            ondelete="CASCADE",
        ),
        CheckConstraint("duration_minutes > 0", name="duration_positive"),
        CheckConstraint("price >= 0", name="price_non_negative"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    color: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    archived_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    @property
    def is_offered(self) -> bool:
        return self.is_active and self.archived_at is None


class ProfessionalService(TenantMixin, TimestampsMixin, Base):
    """Que profesional puede hacer que servicio, y con que ajustes propios.

    Los overrides de duracion y precio existen porque en la realidad un mismo
    servicio puede tardar distinto segun quien lo haga. Sin ellos, la agenda le
    miente al cliente: promete 30 minutos y el profesional necesita 45.

    No extiende `TenantBase` a proposito: su clave primaria es el par
    `(professional_id, service_id)`, no un `id` surrogate. Es una tabla de
    relacion, y agregar un `id` propio obligaria a elegir uno de los dos como
    identificador, con lo que uno de los dos deja de ser UNIQUE de verdad.
    """

    __tablename__ = "professional_services"
    __table_args__ = (
        PrimaryKeyConstraint("professional_id", "service_id", name="pk_professional_services"),
        ForeignKeyConstraint(
            ["professional_id", "business_id"],
            ["professionals.id", "professionals.business_id"],
            name="fk_professional_services_professional",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["service_id", "business_id"],
            ["services.id", "services.business_id"],
            name="fk_professional_services_service",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "custom_duration_minutes IS NULL OR custom_duration_minutes > 0",
            name="custom_duration_positive",
        ),
        CheckConstraint(
            "custom_price IS NULL OR custom_price >= 0", name="custom_price_non_negative"
        ),
    )

    professional_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    service_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    custom_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    custom_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    def effective_duration(self, service_duration: int) -> int:
        """Duracion real para este profesional."""
        return self.custom_duration_minutes or service_duration

    def effective_price(self, service_price: Decimal) -> Decimal:
        """Precio real para este profesional."""
        return self.custom_price if self.custom_price is not None else service_price


__all__ = ["ProfessionalService", "Service"]
