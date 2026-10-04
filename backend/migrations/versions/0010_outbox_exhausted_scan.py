from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from migrations.roles_definer import (
    como_definer,
    definer_con_create,
    definer_sin_create,
    reset_role,
)

revision: str = "0010_outbox_exhausted_scan"
down_revision: str | None = "0009_outbox_tenant_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFINER_ROLE = "tempus_login_definer"
APP_ROLE = "tempus_app"
FUNCTION = "businesses_with_pending_notifications"
DEFAULT_LIMIT = 50


def upgrade() -> None:
    # `CREATE` en el esquema se le da al definer antes de crear la funcion y se le
    # quita despues. No se le deja permanente: `CREATE` en `public` para un rol
    # BYPASSRLS permite crear cualquier objeto, y una tabla sin RLS con el nombre de
    # otra es un agujero que no aparece en ningun test.
    definer_con_create()
    como_definer()
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {FUNCTION}(p_limit integer DEFAULT {DEFAULT_LIMIT})
        RETURNS TABLE (business_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT DISTINCT n.business_id
            FROM notification_requests n
            WHERE n.status IN ('pending', 'failed')
              AND n.scheduled_for <= now()
            ORDER BY 1
            LIMIT GREATEST(p_limit, 1)
        $$;
        """
    )
    reset_role()
    definer_sin_create()
    op.execute(f'GRANT EXECUTE ON FUNCTION {FUNCTION}(integer) TO "{DEFINER_ROLE}"')
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION}(integer) TO {APP_ROLE}")


def downgrade() -> None:
    definer_con_create()
    como_definer()
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {FUNCTION}(p_limit integer DEFAULT {DEFAULT_LIMIT})
        RETURNS TABLE (business_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT DISTINCT n.business_id
            FROM notification_requests n
            WHERE n.status IN ('pending', 'failed')
              AND n.scheduled_for <= now()
              AND n.attempts < n.max_attempts
            ORDER BY 1
            LIMIT GREATEST(p_limit, 1)
        $$;
        """
    )
    reset_role()
    definer_sin_create()
