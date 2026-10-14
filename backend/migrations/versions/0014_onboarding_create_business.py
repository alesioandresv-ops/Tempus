"""alta de tenants: la funcion que si puede crear un negocio"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op
revision: str = "0014_onboarding_create_business"
down_revision: str | None = "0013_rate_limit_deslizante"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_APP_ROLE = "tempus_app"
_FUNCION = "tempus_create_business"
_FIRMA = f"{_FUNCION}(citext, text, text, text)"
_FUNCION_DISPONIBLE = "tempus_slug_disponible"
_FIRMA_DISPONIBLE = f"{_FUNCION_DISPONIBLE}(citext)"
_SQLSTATE_SLUG_RESERVADO = "23514"
_CREAR_FUNCION = f"""
CREATE OR REPLACE FUNCTION {_FUNCION}(p_slug citext, p_name text, p_timezone text, p_phone_e164 text)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $cuerpo$
DECLARE
    v_id uuid;
BEGIN
    IF EXISTS (SELECT 1 FROM slug_reservations WHERE slug = p_slug) THEN
        RAISE EXCEPTION 'El nombre de URL % esta reservado', p_slug
            USING ERRCODE = '{_SQLSTATE_SLUG_RESERVADO}';
    END IF;
    INSERT INTO businesses (slug, name, timezone, phone_e164)
    VALUES (p_slug, p_name, p_timezone, p_phone_e164)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$cuerpo$
"""
_CREAR_DISPONIBLE = f"""
CREATE OR REPLACE FUNCTION {_FUNCION_DISPONIBLE}(p_slug citext)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $cuerpo$
    SELECT NOT EXISTS (SELECT 1 FROM slug_reservations WHERE slug = p_slug)
       AND NOT EXISTS (SELECT 1 FROM businesses WHERE slug = p_slug);
$cuerpo$
"""
def _grant_if_exists(sql_grant: str, role_name: str):
    op.execute(f"""
        DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role_name}') THEN EXECUTE $g${sql_grant}$g$; END IF; END $do$;
    """)
def upgrade() -> None:
    op.execute(_CREAR_FUNCION)
    op.execute(_CREAR_DISPONIBLE)
    op.execute("""
        DO $do$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_owner') THEN
                EXECUTE 'ALTER FUNCTION tempus_create_business(citext, text, text, text) OWNER TO tempus_owner';
            END IF;
        EXCEPTION WHEN others THEN NULL;
        END $do$;
    """)
    op.execute("""
        DO $do$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_owner') THEN
                EXECUTE 'ALTER FUNCTION tempus_slug_disponible(citext) OWNER TO tempus_owner';
            END IF;
        EXCEPTION WHEN others THEN NULL;
        END $do$;
    """)
    op.execute(f"REVOKE ALL ON FUNCTION {_FIRMA} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON FUNCTION {_FIRMA_DISPONIBLE} FROM PUBLIC")
    _grant_if_exists(f"GRANT EXECUTE ON FUNCTION {_FIRMA} TO {_APP_ROLE}", _APP_ROLE)
    _grant_if_exists(f"GRANT EXECUTE ON FUNCTION {_FIRMA_DISPONIBLE} TO {_APP_ROLE}", _APP_ROLE)
def downgrade() -> None:
    op.execute("""
        DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_app') THEN EXECUTE 'REVOKE ALL ON FUNCTION tempus_create_business(citext, text, text, text) FROM tempus_app'; END IF; END $do$;
    """)
    op.execute("""
        DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_app') THEN EXECUTE 'REVOKE ALL ON FUNCTION tempus_slug_disponible(citext) FROM tempus_app'; END IF; END $do$;
    """)
    op.execute(f"DROP FUNCTION IF EXISTS {_FIRMA}")
    op.execute(f"DROP FUNCTION IF EXISTS {_FIRMA_DISPONIBLE}")
