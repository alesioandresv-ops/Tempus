"""politica de INSERT en `businesses` para el rol de onboarding"""

from __future__ import annotations
from collections.abc import Sequence
from alembic import op

revision: str = "0012_businesses_insert_policy"
down_revision: str | None = "0011_businesses_rls_write"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.execute("""
        DO $do$
        DECLARE
            r_owner boolean;
            r_app boolean;
        BEGIN
            SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_owner') INTO r_owner;
            SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_app') INTO r_app;
            EXECUTE 'DROP POLICY IF EXISTS businesses_insert ON businesses';
            IF r_owner THEN
                EXECUTE 'CREATE POLICY businesses_insert ON businesses FOR INSERT TO tempus_owner WITH CHECK (true)';
            ELSIF r_app THEN
                EXECUTE 'CREATE POLICY businesses_insert ON businesses FOR INSERT TO tempus_app WITH CHECK (true)';
            ELSE
                EXECUTE 'CREATE POLICY businesses_insert ON businesses FOR INSERT WITH CHECK (true)';
            END IF;
            IF r_app THEN
                EXECUTE 'REVOKE INSERT ON businesses FROM tempus_app';
            END IF;
        END
        $do$;
    """)

def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS businesses_insert ON businesses");
    op.execute("""
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_owner') THEN
                EXECUTE 'GRANT INSERT ON businesses TO tempus_owner';
            END IF;
        END
        $do$;
    """)
