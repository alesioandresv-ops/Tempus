"""RLS y permiso de escritura en businesses."""

from __future__ import annotations
from collections.abc import Sequence
from alembic import op

revision: str = "0011_businesses_rls_write"
down_revision: str | None = "0010_outbox_exhausted_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_TENANT_GUC = "app.current_business_id"
_EDITABLES = (
    "name",
    "description",
    "timezone",
    "locale",
    "currency",
    "phone_e164",
    "address_text",
    "brand_color",
    "slot_interval_minutes",
    "min_lead_minutes",
    "max_advance_days",
    "cancellation_window_minutes",
    "slug",
    "updated_at",
)


def _grant_if_exists(sql_grant: str, role_name: str):
    op.execute(
        f""" DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role_name}') THEN EXECUTE $g${sql_grant}$g$; END IF; END $do$; """
    )


def upgrade() -> None:
    op.execute("ALTER TABLE businesses ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE businesses FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS businesses_read ON businesses")
    op.execute("DROP POLICY IF EXISTS businesses_write ON businesses")
    op.execute("CREATE POLICY businesses_read ON businesses FOR SELECT USING (true)")
    op.execute(
        f"CREATE POLICY businesses_write ON businesses FOR UPDATE USING (id = NULLIF(current_setting('{_TENANT_GUC}', true), '')::uuid) WITH CHECK (id = NULLIF(current_setting('{_TENANT_GUC}', true), '')::uuid)"
    )
    columnas = ", ".join(_EDITABLES)
    _grant_if_exists(f"GRANT UPDATE ({columnas}) ON businesses TO tempus_app", "tempus_app")


def downgrade() -> None:
    op.execute(
        """ DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_app') THEN EXECUTE 'REVOKE UPDATE ON businesses FROM tempus_app'; END IF; END $do$; """
    )
    op.execute("DROP POLICY IF EXISTS businesses_write ON businesses")
    op.execute("DROP POLICY IF EXISTS businesses_read ON businesses")
    op.execute("ALTER TABLE businesses NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE businesses DISABLE ROW LEVEL SECURITY")
