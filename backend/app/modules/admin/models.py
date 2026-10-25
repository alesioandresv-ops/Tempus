"""Auditoria de las acciones del panel administrativo.

`admin_audit_log` es global y **no** lleva RLS. No es un olvido: el panel
administra la plataforma entera, asi que la fila no pertenece a un tenant. El
`business_id` es el objetivo de la accion (nullable: un login no tiene negocio) y
se guarda como UUID suelto, sin FK, para que borrar un negocio no borre la
historia de quien lo bloqueo.

La tabla se escribe solo con el rol de migraciones (`tempus_owner`), que es el
que usa el panel (`get_admin_session`). El rol de la app no tiene grants sobre
ella; la migracion `0029_admin_panel_seguro` lo deja explicito.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import String, Text, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UtcDateTime


class AdminAuditLog(Base):
    """Una accion administrativa, con quien la hizo y desde donde."""

    __tablename__ = "admin_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    admin_user: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Objetivo de la accion. NULL en acciones que no son sobre un negocio.
    business_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    #: IP resuelta por el servidor (`request.client.host`), no por una cabecera.
    #: NULL si el cliente no tiene una IP parseable (tests, transporte raro).
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    #: La tabla es de solo-append: no tiene `updated_at` a proposito.
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=text("now()")
    )


__all__ = ["AdminAuditLog"]
