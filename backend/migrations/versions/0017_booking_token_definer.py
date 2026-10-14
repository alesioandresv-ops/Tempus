"""Resolver el token de reserva con el rol definer (BYPASSRLS)"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op
revision: str = "0017_booking_token_definer"
down_revision: str | None = "0016_fase4_whatsapp_avatar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
DEFINER_ROLE = "tempus_login_definer"
OWNER_ROLE = "tempus_owner"
BOOKING_TENANT = "booking_tenant_for_token"
COLUMNAS = ("id", "business_id", "secure_token_hash")
def _grant_if_exists(sql_grant: str, role_name: str):
    op.execute(f""" DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role_name}') THEN EXECUTE $g${sql_grant}$g$; END IF; END $do$; """)
def upgrade() -> None:
    cols = ", ".join(COLUMNAS)
    _grant_if_exists(f'GRANT SELECT ({cols}) ON bookings TO "{DEFINER_ROLE}"', DEFINER_ROLE)
    op.execute("""
        DO $do$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_login_definer') THEN
                RAISE NOTICE '[0017] Skipping, no definer role in single-role env';
                RETURN;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_login_definer') THEN
                EXECUTE 'ALTER FUNCTION booking_tenant_for_token(bytea) OWNER TO "tempus_login_definer"';
            END IF;
        EXCEPTION WHEN others THEN NULL;
        END $do$;
    """)
def downgrade() -> None:
    op.execute("""
        DO $do$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_owner') THEN
                EXECUTE 'ALTER FUNCTION booking_tenant_for_token(bytea) OWNER TO tempus_owner';
            END IF;
        EXCEPTION WHEN others THEN NULL;
        END $do$;
    """)
    op.execute("""
        DO $do$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_login_definer') THEN
                EXECUTE 'REVOKE SELECT (id, business_id, secure_token_hash) ON bookings FROM "tempus_login_definer"';
            END IF;
        EXCEPTION WHEN others THEN NULL;
        END $do$;
    """)
