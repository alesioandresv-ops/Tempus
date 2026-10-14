"""Rol dedicado para login pre-tenant (BYPASSRLS minimo) - VERSION PROD"""

from __future__ import annotations
import os
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from migrations.rls_safe import revoke_if_exists

revision: str = "0005_login_definer_role"
down_revision: str | None = "0004_login_functions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
APP_ROLE = os.getenv("DB_APP_ROLE", "tempus_app")
DEFINER_ROLE = os.getenv("DB_DEFINER_ROLE", "tempus_login_definer")
BUSINESS_LOGIN = "auth_business_user_for_login"
COLUMNAS_LOGIN = "id, business_id, password_hash, role, status, full_name"


def _grant_if_exists(sql_grant: str, role_name: str):
    op.execute(
        f""" DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role_name}') THEN EXECUTE $g${sql_grant}$g$; END IF; END $do$; """
    )


def upgrade() -> None:
    op.execute("""
        DO $do$
        DECLARE has_owner boolean; has_app boolean; has_definer boolean;
        BEGIN
            SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_owner') INTO has_owner;
            SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_app') INTO has_app;
            SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_login_definer') INTO has_definer;
            IF NOT has_owner OR NOT has_app OR (has_owner AND has_app AND (SELECT rolname FROM pg_roles WHERE rolname='tempus_owner' LIMIT 1) = (SELECT rolname FROM pg_roles WHERE rolname='tempus_app' LIMIT 1)) THEN
                RAISE NOTICE '[0005] Skipping definer role migration in single-role env';
                RETURN;
            END IF;
            IF NOT has_definer THEN
                RAISE NOTICE '[0005] Skipping, definer role not present';
                RETURN;
            END IF;
        END $do$;
    """)
    owner = os.getenv("DB_OWNER_ROLE", "")
    app = os.getenv("DB_APP_ROLE", "")
    if owner and owner == app:
        print(f"[0005] Skipping definer role migration in single-role env (owner==app=={owner})")
        return
    if os.getenv("SKIP_DEFINER_MIGRATION", "false").lower() == "true":
        print("[0005] Skipping definer role migration due to SKIP_DEFINER_MIGRATION=true")
        return
    from migrations.roles_definer import (
        definer_con_create,
        definer_sin_create,
        membresia_temporal_al_definer,
        revoca_membresia_al_definer,
    )

    _grant_if_exists(
        f'GRANT SELECT ({COLUMNAS_LOGIN}) ON business_users TO"{DEFINER_ROLE}"', DEFINER_ROLE
    )
    membresia_temporal_al_definer()
    definer_con_create()
    op.execute(
        sa.text(
            f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{DEFINER_ROLE}') THEN EXECUTE 'ALTER FUNCTION {BUSINESS_LOGIN}(citext) OWNER TO\"{DEFINER_ROLE}\"'; END IF; END $do$;"
        )
    )
    definer_sin_create()
    revoca_membresia_al_definer()


def downgrade() -> None:
    op.execute(
        """ DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_login_definer') THEN NULL; ELSE RETURN; END IF; END $do$; """
    )
    owner = os.getenv("DB_OWNER_ROLE", "")
    app = os.getenv("DB_APP_ROLE", "")
    if owner and owner == app:
        print("[0005] Skipping definer role downgrade in single-role env")
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
    op.execute(
        """ DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_owner') THEN EXECUTE 'ALTER FUNCTION auth_business_user_for_login(citext) OWNER TO tempus_owner'; END IF; END $do$; """
    )
    definer_sin_create()
    revoca_membresia_al_definer()
    revoke_if_exists(
        f'REVOKE SELECT ({COLUMNAS_LOGIN}) ON business_users FROM "{DEFINER_ROLE}"', DEFINER_ROLE
    )
