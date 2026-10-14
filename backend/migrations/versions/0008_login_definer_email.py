"""Arreglar el login de negocio: faltaba SELECT sobre email"""

from __future__ import annotations
import os
from alembic import op

revision = "0008_login_definer_email"
down_revision = "0007_principal_by_id"
branch_labels = None
depends_on = None

DEFINER_ROLE = os.getenv("DB_LOGIN_DEFINER_ROLE", "tempus_login_definer")

def _grant_if_exists(sql_grant: str, role_name: str):
    clean = role_name.strip().strip('"').strip("'")
    op.execute(f"""
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{clean}') THEN
                EXECUTE $g${sql_grant}$g$;
            END IF;
        END
        $do$;
    """)

def upgrade() -> None:
    cols = "id, business_id, password_hash, role, status, full_name, email"
    _grant_if_exists(f'GRANT SELECT ({cols}) ON business_users TO "{DEFINER_ROLE}"', DEFINER_ROLE)
    op.execute(f'GRANT SELECT ({cols}) ON business_users TO CURRENT_USER')

def downgrade() -> None:
    pass