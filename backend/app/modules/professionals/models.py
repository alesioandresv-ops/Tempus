"""Profesionales: las personas que atienden.

`professionals.user_id` es **nullable** y esa es la decision que mas conviene no
"corregir" despues: un negocio con ocho profesionales y tres personas con acceso
al panel es el caso normal, no una excepcion. La mayoria de los profesionales de
un negocio de servicios existen en la agenda y no tienen login.

Cuando `user_id` no es NULL, la FK es **compuesta** `(user_id, business_id)`:
sino, un bug que propague mal el `business_id` cruzaria un profesional del tenant A
con un login del tenant B, y el profesional pasaria a ver la agenda del otro
negocio. §7 Capa 1, exactamente.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantBase, tenant_unique_key
from app.db.types import UtcDateTime

# La FK de `user_id` apunta a `business_users`, y esa tabla tiene que estar
# registrada en `Base.metadata` para que SQLAlchemy pueda resolver la FK
# compuesta. Sin este import, `NoReferencedTableError: Foreign key associated
# with column 'professionals.user_id' could not find table 'business_users'`,
# y solo cuando este modulo es el primero que se importa.
#
# Va en `__all__` porque el nombre no se usa en el cuerpo del modulo: exportarlo
# es lo que le dice al linter que el import es deliberado, sin un `noqa`.
from app.modules.auth.models import BusinessUser

__all__ = ["BusinessUser", "Professional"]


class Professional(TenantBase, Base):
    """Persona que atiende. Concepto de negocio, no una cuenta."""

    __tablename__ = "professionals"
    __table_args__ = (
        tenant_unique_key("professionals"),
        ForeignKeyConstraint(
            ["user_id", "business_id"],
            ["business_users.id", "business_users.business_id"],
            name="fk_professionals_user_id_business_users",
            ondelete="SET NULL",
        ),
        CheckConstraint(
            "(user_id IS NULL) OR (archived_at IS NULL)",
            name="login_requires_not_archived",
        ),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    archived_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    @property
    def is_bookable(self) -> bool:
        """Reservable = activo y no archivado.

        `is_active` apaga la reserva sin perder el historial; `archived_at` es el
        borrado logico. Son dos cosas distintas y por eso son dos columnas.
        """
        return self.is_active and self.archived_at is None


__all__ = ["Professional"]
