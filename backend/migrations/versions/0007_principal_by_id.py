"""Resolver un principal por su id, sin conocer el tenant

Revision ID: 0007_principal_by_id
Revises: 0006_booking_token_lookup
Create Date: 2026-10-01

**El problema que resuelve.** Rotar un refresh token exige volver a emitir un access
token, y para eso hacen falta `business_id`, `role` y `status` del titular. La fila
de `refresh_tokens` guarda el `user_id` pero **no** el negocio: guardarlo duplicaria
un dato que ya se puede derivar, y ademas se quedaria desactualizado si el usuario
cambiaba de negocio.

O sea: con el token en la mano se sabe *quien* es, pero no *de que negocio*, y sin
ese dato no se puede abrir una sesion con tenant ni volver a emitir el access token.
Es el mismo circulo que resolvieron `0004` (login) y `0006` (reserva por secure
token), y se resuelve con la misma tecnica.

`0004` resuelve por `email` porque en el login el cliente manda el email. El refresh
manda el token, asi que aqui el lookup es por `id`. Las dos funciones se parecen
deliberadamente: misma forma de retorno, mismas garantias, mismo dueno.

**Por que releer el status en cada refresh y no confiar en el del login.** Es la
parte que hace que esto valga la pena. `business_users.status` puede pasar a
`suspended` o `inactive` con el session abierta, y si el refresh emitiera tokens
sin volver a mirar la fila, ese miembro tendria acceso indefinido: desactivarlo
dejaria de cortar el acceso solo a los access tokens que ya vencieron, y no a la
cadena de refresh que puede renovar el access cada 15 minutos indefinidamente. Al
releer el status en cada rotacion, desactivar a alguien es una operacion que
produce efecto en la proxima renovacion --que es como maximo 15 minutos-- y sin
tener que buscar sesiones activas para revocarlas.

Mismo argumento para `platform_users.is_active`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_principal_by_id"
down_revision: str | None = "0006_booking_token_lookup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tempus_app"

BUSINESS_BY_ID = "auth_business_user_by_id"
PLATFORM_BY_ID = "auth_platform_user_by_id"
SEARCH_PATH = "pg_catalog, public"


def upgrade() -> None:
    # Misma forma que `auth_business_user_for_login` de 0004, sin `password_hash`:
    # la rotacion no verifica contrasena, y por lo tanto no deberia poder leerla.
    # Que la superficie de la funcion no exponga el hash evita que un descuido
    # futuro la convierta en una via para leer hashes de todos los tenants.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {BUSINESS_BY_ID}(p_user_id uuid)
        RETURNS TABLE (
            id uuid,
            business_id uuid,
            role business_user_role,
            status membership_status,
            full_name text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = {SEARCH_PATH}
        AS $$
            SELECT u.id, u.business_id, u.role, u.status, u.full_name
            FROM business_users u
            WHERE u.id = p_user_id
        $$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {BUSINESS_BY_ID}(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {BUSINESS_BY_ID}(uuid) TO {APP_ROLE}")

    # `is_active` y no `status`: es la unica columna de estado que tiene
    # `platform_users`, y el CHECK `exactly_one_principal` de `refresh_tokens` ya
    # garantiza que un token de plataforma no trae `user_id`.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {PLATFORM_BY_ID}(p_platform_user_id uuid)
        RETURNS TABLE (
            id uuid,
            role platform_role,
            is_active boolean,
            full_name text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = {SEARCH_PATH}
        AS $$
            SELECT u.id, u.role, u.is_active, u.full_name
            FROM platform_users u
            WHERE u.id = p_platform_user_id
        $$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {PLATFORM_BY_ID}(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {PLATFORM_BY_ID}(uuid) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {BUSINESS_BY_ID}(uuid)")
    op.execute(f"DROP FUNCTION IF EXISTS {PLATFORM_BY_ID}(uuid)")
