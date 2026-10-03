"""Rol dedicado para login pre-tenant (BYPASSRLS mínimo)

Revision ID: 0005_login_definer_role
Revises: 0004_login_functions
Create Date: 2026-09-29

El problema: 0004 asume que SECURITY DEFINER saltea RLS, pero FORCE ROW LEVEL
SECURITY en business_users (0002) alcanza también al owner. La función no
devuelve filas sin GUC.

La solución: rol dedicado tempus_login_definer con BYPASSRLS, NOLOGIN,
y SOLO las 6 columnas que auth_business_user_for_login lee.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_login_definer_role"
down_revision: str | None = "0004_login_functions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tempus_app"
DEFINER_ROLE = "tempus_login_definer"
BUSINESS_LOGIN = "auth_business_user_for_login"


def upgrade() -> None:
    # 1. Privilegios mínimos: SOLO las 6 columnas que la función lee
    op.execute(
        f"GRANT SELECT (id, business_id, password_hash, role, status, full_name) "
        f'ON business_users TO "{DEFINER_ROLE}"'
    )

    # 2. Transferir ownership de la función (requiere SET ROLE temporal)
    #    Otorgamos la membresía CON ADMIN OPTION para que el downgrade pueda
    #    revertir el ownership. tempus_owner ya tiene DDL completo.
    op.execute(f'GRANT "{DEFINER_ROLE}" TO tempus_owner WITH ADMIN OPTION')
    op.execute(f'ALTER FUNCTION {BUSINESS_LOGIN}(citext) OWNER TO "{DEFINER_ROLE}"')


def downgrade() -> None:
    # Simétrico al upgrade: recuperar ownership y revocar privilegios
    # tempus_owner tiene ADMIN OPTION, así que puede cambiar el owner.
    op.execute(f"ALTER FUNCTION {BUSINESS_LOGIN}(citext) OWNER TO tempus_owner")
    op.execute(f'REVOKE "{DEFINER_ROLE}" FROM tempus_owner')

    op.execute(
        f"REVOKE SELECT (id, business_id, password_hash, role, status, full_name) "
        f'ON business_users FROM "{DEFINER_ROLE}"'
    )

    # El rol se crea en el init script (superuser), así que aquí solo revocamos
    # lo que concedimos. El DROP ROLE lo haría el superuser si se desea limpiar.
