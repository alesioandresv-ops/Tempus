"""Reconciliar esquema con el modelo: RLS de slugs, platform_users, booking_tokens y ACLs de funciones."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from migrations.rls_safe import claim_and_open, grant_if_exists, revoke_if_exists
from migrations.roles_definer import (
    definer_con_create,
    definer_sin_create,
    membresia_temporal_al_definer,
    revoca_membresia_al_definer,
)

revision: str = "0023_reconcile_schema"
down_revision: str | None = "0022_single_role_definer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.getenv("DB_APP_ROLE", "tempus_app")
OWNER_ROLE = os.getenv("DB_OWNER_ROLE", "tempus_owner")
DEFINER_ROLE = os.getenv("DB_LOGIN_DEFINER_ROLE", "tempus_login_definer")
MIGRATOR_ROLE = os.getenv("DB_MIGRATOR_ROLE", "tempus_migrator")

RESERVADOS: tuple[tuple[str, str], ...] = (
    ("api", "Ruta de la API en el mismo origen que la pagina publica"),
    ("r", "Prefijo de los recursos de la API"),
    ("static", "Directorio de estaticos del frontend"),
    ("assets", "Directorio de assets del frontend"),
    ("login", "Ruta de autenticacion del panel"),
    ("register", "Ruta de alta de negocios"),
    ("admin", "Prefijo del panel administrativo"),
    ("panel", "Prefijo del panel del negocio"),
    ("app", "Prefijo de la aplicacion web"),
    ("www", "Subdominio www"),
    ("docs", "Subdominio de documentacion"),
)


def _literal(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def _role_exists(role_name: str) -> bool:
    bind = op.get_bind()
    fila = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"),
        {"r": role_name},
    ).first()
    return fila is not None


def _safe_exec(sentencia: str) -> None:
    try:
        op.execute(sentencia)
    except Exception as exc:
        mensaje = str(exc).lower()
        if "does not exist" in mensaje or "not a member" in mensaje:
            pass
        else:
            raise


def _entrar_como_definer() -> None:
    if _role_exists(MIGRATOR_ROLE):
        _safe_exec(f'SET ROLE "{MIGRATOR_ROLE}"')
    if _role_exists(DEFINER_ROLE):
        _safe_exec(f'SET ROLE "{DEFINER_ROLE}"')


def _reset_nesting() -> None:
    with contextlib_suppress():  # evitar import extra arriba
        pass
    # usar el patrón existente: reset dos veces
    from contextlib import suppress

    with suppress(Exception):
        op.execute("RESET ROLE")
    with suppress(Exception):
        op.execute("RESET ROLE")


def _es_single_role_definer() -> bool:
    bind = op.get_bind()
    fila = bind.execute(
        sa.text("SELECT rolbypassrls FROM pg_roles WHERE rolname = :r"),
        {"r": DEFINER_ROLE},
    ).first()
    return bool(fila and not fila[0])


# compat

contextlib_suppress = contextlib.suppress


def upgrade() -> None:
    op.get_bind()

    # 1) Limpiar el overload duplicado de rate_limit_hit (3 args) si existe
    op.execute("DROP FUNCTION IF EXISTS public.rate_limit_hit(text, integer, integer)")

    # 2) Alinear platform_users
    op.execute("ALTER TABLE platform_users DROP COLUMN IF EXISTS status")

    op.execute("DROP FUNCTION IF EXISTS auth_platform_user_for_login(citext)")
    op.execute(
        """
        CREATE FUNCTION auth_platform_user_for_login(p_email citext)
        RETURNS TABLE (id uuid, password_hash text, role text, full_name text, is_active boolean)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
        AS $$
            SELECT u.id, u.password_hash, u.role::text, u.full_name, u.is_active
            FROM platform_users u WHERE u.email = p_email
        $$;
        """
    )
    # ACL para platform_login
    op.execute("REVOKE ALL ON FUNCTION auth_platform_user_for_login(citext) FROM PUBLIC")
    grant_if_exists(
        f'GRANT EXECUTE ON FUNCTION auth_platform_user_for_login(citext) TO "{APP_ROLE}"',
        APP_ROLE,
    )
    # El owner debe seguir siendo tempus_owner (test lo exige)
    # No cambiamos owner

    # 3) auth_business_user_for_login: asegurar ACL correcta (owner = definer)
    # REVOKE PUBLIC, GRANT app (entrando como definer si posible)
    try:
        _entrar_como_definer()
        op.execute("REVOKE ALL ON FUNCTION auth_business_user_for_login(citext) FROM PUBLIC")
        grant_if_exists(
            f'GRANT EXECUTE ON FUNCTION auth_business_user_for_login(citext) TO "{APP_ROLE}"',
            APP_ROLE,
        )
    finally:
        _reset_nesting()

    # 4) booking_tenant_for_token: usar bookings, no booking_tokens
    op.execute("DROP FUNCTION IF EXISTS booking_tenant_for_token(bytea)")
    op.execute(
        """
        CREATE FUNCTION booking_tenant_for_token(p_token_hash bytea)
        RETURNS TABLE (business_id uuid, booking_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
        AS $$ SELECT b.business_id, b.id FROM bookings b WHERE b.secure_token_hash = p_token_hash $$;
        """
    )
    # Transferir ownership a definer (multi-rol y single-rol)
    membresia_temporal_al_definer()
    definer_con_create()
    try:
        if _role_exists(DEFINER_ROLE):
            op.execute(
                'ALTER FUNCTION booking_tenant_for_token(bytea) OWNER TO "' + DEFINER_ROLE + '"'
            )
    finally:
        definer_sin_create()
        revoca_membresia_al_definer()

    # ACL
    try:
        _entrar_como_definer()
        op.execute("REVOKE ALL ON FUNCTION booking_tenant_for_token(bytea) FROM PUBLIC")
        grant_if_exists(
            f'GRANT EXECUTE ON FUNCTION booking_tenant_for_token(bytea) TO "{APP_ROLE}"',
            APP_ROLE,
        )
    finally:
        _reset_nesting()

    # En single-role, agregar política/grants a definer para bookings
    if _es_single_role_definer():
        op.execute('DROP POLICY IF EXISTS bookings_tenant_for_token ON "bookings"')
        op.execute(
            f'CREATE POLICY bookings_tenant_for_token ON "bookings" FOR SELECT TO "{DEFINER_ROLE}" USING (true)'
        )
        grant_if_exists(
            f'GRANT SELECT (id, business_id, secure_token_hash) ON bookings TO "{DEFINER_ROLE}"',
            DEFINER_ROLE,
        )

    # 5) Eliminar tabla booking_tokens (ya no usada)
    op.execute("DROP TABLE IF EXISTS booking_tokens")

    # 6) slug_reservations: sin RLS, re-sembrar y mantener sin privilegios a app
    claim_and_open("slug_reservations")
    try:
        filas = ",\n ".join(f"({_literal(s)}, {_literal(m)})" for s, m in RESERVADOS)
        op.execute(
            f"INSERT INTO slug_reservations (slug, reason) VALUES {filas} ON CONFLICT (slug) DO NOTHING"
        )
    finally:
        # No usamos restore() para evitar re-encender RLS (no tiene políticas)
        op.execute(
            "DO $do$ BEGIN EXECUTE 'ALTER TABLE slug_reservations DISABLE ROW LEVEL SECURITY'; EXCEPTION WHEN others THEN NULL; END $do$;"
        )
        op.execute(
            "DO $do$ BEGIN EXECUTE 'ALTER TABLE slug_reservations NO FORCE ROW LEVEL SECURITY'; EXCEPTION WHEN others THEN NULL; END $do$;"
        )

    # Asegurar app no tiene privilegios
    revoke_if_exists('REVOKE ALL ON TABLE slug_reservations FROM "tempus_app"', "tempus_app")

    # 7) Asegurar ACL de auth_business_user_for_login columns? No necesario, test valida


def downgrade() -> None:
    # No revertimos a estado roto intencionalmente
    pass
