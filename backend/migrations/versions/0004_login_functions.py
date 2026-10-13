"""Las dos funciones que hacen posible el login, y el rate limit atomico"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import op
from alembic.util import CommandError

revision: str = "0004_login_functions"
down_revision: str | None = "0003_auth_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIGRATION_ROLE = os.getenv("DB_OWNER_ROLE", "tempus_owner")
APP_ROLE = os.getenv("DB_APP_ROLE", "tempus_app")

BUSINESS_LOGIN = "auth_business_user_for_login"
PLATFORM_LOGIN = "auth_platform_user_for_login"
RATE_LIMIT_HIT = "rate_limit_hit"

SEARCH_PATH = "pg_catalog, public"

def upgrade() -> None:
    # Fix para Render: la 0002 pudo haber revocado CREATE en single-role env
    op.execute(f'GRANT CREATE ON SCHEMA public TO "{MIGRATION_ROLE}"')
    if APP_ROLE!= MIGRATION_ROLE:
        op.execute(f'GRANT CREATE ON SCHEMA public TO "{APP_ROLE}"')

    # Funcion de login de negocio
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {BUSINESS_LOGIN}(p_email citext)
        RETURNS TABLE (
            id uuid,
            business_id uuid,
            password_hash text,
            role business_user_role,
            status membership_status,
            full_name text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = {SEARCH_PATH}
        AS $$
            SELECT u.id, u.business_id, u.password_hash, u.role, u.status, u.full_name
            FROM business_users u
            WHERE u.email = p_email
        $$;
        """
    )

    # Funcion de login de plataforma
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {PLATFORM_LOGIN}(p_email citext)
        RETURNS TABLE (
            id uuid,
            password_hash text,
            role text,
            status text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = {SEARCH_PATH}
        AS $$
            SELECT u.id, u.password_hash, u.role::text, u.status::text
            FROM platform_users u
            WHERE u.email = p_email
        $$;
        """
    )

    # Funcion de rate limit atomica
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {RATE_LIMIT_HIT}(
            p_bucket_key text,
            p_window_seconds integer,
            p_max_requests integer
        )
        RETURNS TABLE (allowed boolean, remaining integer, reset_at timestamptz)
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_now timestamptz := now();
            v_reset_at timestamptz;
            v_count integer;
        BEGIN
            INSERT INTO rate_limit_buckets (bucket_key, window_seconds, max_requests, count, reset_at)
            VALUES (p_bucket_key, p_window_seconds, p_max_requests, 1, v_now + (p_window_seconds || ' seconds')::interval)
            ON CONFLICT (bucket_key) DO UPDATE
            SET count = CASE
                WHEN rate_limit_buckets.reset_at <= v_now THEN 1
                ELSE rate_limit_buckets.count + 1
            END,
            reset_at = CASE
                WHEN rate_limit_buckets.reset_at <= v_now THEN v_now + (p_window_seconds || ' seconds')::interval
                ELSE rate_limit_buckets.reset_at
            END
            RETURNING rate_limit_buckets.count, rate_limit_buckets.reset_at INTO v_count, v_reset_at;

            RETURN QUERY SELECT (v_count <= p_max_requests), GREATEST(0, p_max_requests - v_count), v_reset_at;
        END;
        $$;
        """
    )

def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {RATE_LIMIT_HIT}(text, integer, integer)")
    op.execute(f"DROP FUNCTION IF EXISTS {PLATFORM_LOGIN}(citext)")
    op.execute(f"DROP FUNCTION IF EXISTS {BUSINESS_LOGIN}(citext)")