"""booking token lookup"""

from __future__ import annotations
import os
from collections.abc import Sequence
from alembic import op

revision: str = "0006_booking_token_lookup"
down_revision: str = "0005_booking_tokens"
branch_labels = None
depends_on = None

MIGRATION_ROLE = os.getenv("DB_OWNER_ROLE", "tempus_owner")
APP_ROLE = os.getenv("DB_APP_ROLE", "tempus_app")
BOOKING_TENANT = "booking_tenant_for_token"

def _grant_if_exists(sql_grant: str, role_name: str):
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role_name}') THEN
                EXECUTE $${sql_grant}$$;
            END IF;
        END
        $$;
    """)

def upgrade() -> None:
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {BOOKING_TENANT}(p_token bytea)
        RETURNS TABLE (business_id uuid, location_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
        AS $$
            SELECT business_id, location_id FROM booking_tokens WHERE token = p_token
        $$;
    """)
    _grant_if_exists(f"GRANT EXECUTE ON FUNCTION {BOOKING_TENANT}(bytea) TO {APP_ROLE}", APP_ROLE)
    _grant_if_exists(f"GRANT EXECUTE ON FUNCTION {BOOKING_TENANT}(bytea) TO {MIGRATION_ROLE}", MIGRATION_ROLE)
    op.execute(f"GRANT EXECUTE ON FUNCTION {BOOKING_TENANT}(bytea) TO CURRENT_USER")

def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {BOOKING_TENANT}(bytea)")