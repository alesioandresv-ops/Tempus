"""Rol dedicado para login pre-tenant (BYPASSRLS minimo) - VERSION PROD"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import op

revision: str = "0005_login_definer_role"
down_revision: str | None = "0004_login_functions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.getenv("DB_APP_ROLE", "tempus_app")
DEFINER_ROLE = os.getenv("DB_DEFINER_ROLE", "tempus_login_definer")
BUSINESS_LOGIN = "auth_business_user_for_login"
COLUMNAS_LOGIN = "id, business_id, password_hash, role, status, full_name"

def _grant_if_exists(sql_grant: str, role_name: str):
    op.execute(f"""
        DO $do$ 
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role_name}') THEN
                EXECUTE $g${sql_grant}$g$;
            END IF;
        END
        $do$;
    """)

def upgrade() -> None:
    # En Render/produccion con un solo rol, no hay roles definer que crear.
    # Si OWNER y APP son el mismo usuario, salteamos toda esta lÃ³gica.
    owner = os.getenv("DB_OWNER_ROLE", "")
    app = os.getenv("DB_APP_ROLE", "")
    if owner and owner == app:
        print(f"[0005] Skipping definer role migration in single-role env (owner==app=={owner})")
        return
    if os.getenv("SKIP_DEFINER_MIGRATION", "false").lower() == "true":
        print("[0005] Skipping definer role migration due to SKIP_DEFINER_MIGRATION=true")
        return

    # Solo se ejecuta en local con todos los roles
    from migrations.roles_definer import (
        definer_con_create,
        definer_sin_create,
        membresia_temporal_al_definer,
        revoca_membresia_al_definer,
    )

 _grant_if_exists(f"""GRANT SELECT ({COLUMNAS_LOGIN}) ON business_users TO "{DEFINER_ROLE}"""", DEFINER_ROLE)
    membresia_temporal_al_definer()
    definer_con_create()
    op.execute(
        sa.text(
            f"DO $do$ "
            f"BEGIN "
            f"  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{DEFINER_ROLE}') THEN "
            f"    EXECUTE 'ALTER FUNCTION {BUSINESS_LOGIN}(citext) OWNER TO \"{DEFINER_ROLE}\"'; "
            f"  END IF; "
            f"END $do$;"
        )
    )
    definer_sin_create()
    revoca_membresia_al_definer()

def downgrade() -> None:
    owner = os.getenv("DB_OWNER_ROLE", "")
    app = os.getenv("DB_APP_ROLE", "")
    if owner and owner == app:
        print(f"[0005] Skipping definer role downgrade in single-role env")
        return
    if os.getenv("SKIP_DEFINER_MIGRATION", "false").lower() == "true":
        return

    from migrations.roles_definer import (
        definer_con_create,
        definer_sin_create,
        membresia_temporal_al_definer,
        revoca_membresia_al_definer,
    )

    membresia_temporal_al_definer()
    definer_con_create()
    op.execute(f"ALTER FUNCTION {BUSINESS_LOGIN}(citext) OWNER TO {owner or 'tempus_owner'}")
    definer_sin_create()
    revoca_membresia_al_definer()
    op.execute(f'REVOKE SELECT ({COLUMNAS_LOGIN}) ON business_users FROM "{DEFINER_ROLE}"')