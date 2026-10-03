"""Identidad: operadores de plataforma, membresias y refresh tokens.

La separacion entre `platform_users` y `business_users` no se puede colapsar mas
adelante: un operador de plataforma administra el servicio (soporte, altas) y un
miembro administra **un** negocio. Meterlos en una tabla con un rol mas ya seria
tornar indistinguible "ve todos los negocios" de "ve el suyo", que es exactamente
el IDOR que §25 pide cerrar.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, GlobalBase, TenantBase, tenant_unique_key
from app.db.types import UtcDateTime
from app.models.enums import BusinessUserRole, MembershipStatus, PlatformRole
from app.models.sql_types import (
    business_user_role_enum,
    membership_status_enum,
    platform_role_enum,
)


class PlatformUser(GlobalBase, Base):
    """Personal de la plataforma. Sin `business_id` y sin RLS, por diseño.

    El aislamiento no lo da la RLS aqui, lo dan los permisos: el rol de la
    aplicacion no tiene ningun INSERT ni UPDATE sobre esta tabla, asi que ni una
    consulta mal escrita convierte a un cliente en operador.
    """

    __tablename__ = "platform_users"

    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[PlatformRole] = mapped_column(platform_role_enum, nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    last_login_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    __table_args__ = (
        # El login es case-insensitive, asi que la unicidad tambien. Un indice
        # unico sobre `lower(email)` es lo que hace que `Ana@x.com` y
        # `ana@x.com` no puedan coexistir; un UNIQUE simple sobre la columna
        # `citext` ya lo resuelve, pero el indice explicito sirve para el planner
        # de la busqueda del login.
        Index("ix_platform_users_email", "email", unique=True),
    )

    @property
    def is_owner(self) -> bool:
        return self.role is PlatformRole.OWNER


class BusinessUser(TenantBase, Base):
    """Membresia: una persona con acceso al panel de **un** negocio.

    La unicidad es `(business_id, email)` y no `email` a secas. La misma persona
    puede trabajar en dos negocios, y con `email` unico el segundo alta fallaria
    con un error de constraint que no explica nada.
    """

    __tablename__ = "business_users"
    __table_args__ = (
        tenant_unique_key("business_users"),
        ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_business_users_business_id_businesses",
            ondelete="CASCADE",
        ),
        Index("uq_business_users_business_id_email", "business_id", "email", unique=True),
    )

    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[BusinessUserRole] = mapped_column(business_user_role_enum, nullable=False)
    status: Mapped[MembershipStatus] = mapped_column(
        membership_status_enum, nullable=False, server_default=text("'invited'")
    )
    last_login_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    invited_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    @property
    def can_login(self) -> bool:
        """Un `invited` todavia no tiene credenciales activas."""
        return self.status is MembershipStatus.ACTIVE


class RefreshToken(GlobalBase, Base):
    """Refresh tokens con rotacion y deteccion de reuso (ADR-0010).

    Se guarda el **hash**, no el token. Quien lea la tabla no suplanta a nadie; y
    quien la lea no puede escalar el robo de la base a un robo de sesiones, que
    es el escenario que hace que un incidente de lectura sea grave.

    `family_id` agrupa una cadena de rotaciones. Si un token ya rotado vuelve a
    aparecer, hay reuso, y se revoca **toda** la familia: el atacante y la victima
    pierden el acceso, que es la unica salida correcta cuando no se puede saber
    cual de los dos es cual.

    Los titulares son dos porque lo son: un miembro de negocio y un operador de
    plataforma se autentican en superficies distintas y viven en tablas distintas
    a proposito. Dos columnas nullable mas un CHECK que exija exactamente una es
    menos fragile que una tabla puente que recrearia el problema que §5.1 quiere
    evitar, y mas honesto que meter al operador de plataforma en `business_users`.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(user_id, platform_user_id) = 1",
            name="exactly_one_principal",
        ),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        Index("ix_refresh_tokens_family_id", "family_id"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "business_users.id",
            ondelete="CASCADE",
            name="fk_refresh_tokens_user_id_business_users",
        ),
        nullable=True,
        index=True,
    )
    platform_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "platform_users.id",
            ondelete="CASCADE",
            name="fk_refresh_tokens_platform_user_id_platform_users",
        ),
        nullable=True,
        index=True,
    )
    family_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "refresh_tokens.id", ondelete="SET NULL", name="fk_refresh_tokens_replaced_by_id"
        ),
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    @property
    def principal_id(self) -> uuid.UUID:
        """El titular, sea del tipo que sea."""
        return self.user_id if self.user_id is not None else self.platform_user_id  # type: ignore[return-value]

    def is_usable(self, moment: dt.datetime) -> bool:
        return self.revoked_at is None and self.expires_at > moment

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuracion
        return f"<RefreshToken titular={self.principal_id} revocado={self.revoked_at is not None}>"


__all__ = [
    "BusinessUser",
    "PlatformUser",
    "RefreshToken",
]
