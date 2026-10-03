"""Arreglar el login de negocio: faltaba SELECT sobre `email`

Revision ID: 0008_login_definer_email
Revises: 0007_principal_by_id
Create Date: 2026-10-01

**El bug.** `0005` creo `tempus_login_definer` con Privilegio minimo por columna: le
concedio `SELECT` sobre las **6** columnas que `auth_business_user_for_login`
*devuelve* -- `id`, `business_id`, `password_hash`, `role`, `status`, `full_name` --
y no sobre la tabla. La decision es correcta y no se toca: que un rol con
`BYPASSRLS` no tenga `SELECT` sobre `business_users` completa es exactamente lo que
queria el ADR-0010.

Lo que faltaba es `email`. El cuerpo de la funcion es

    SELECT u.id, u.business_id, u.password_hash, u.role, u.status, u.full_name
    FROM business_users u
    WHERE u.email = p_email

y en PostgreSQL el privilegio de columna es **por columna tocada**, no por columna
devuelta: el `WHERE` tambien necesita `SELECT` sobre `email`. Como el rol no lo
tenia, la consulta entera fallaba con

    ERROR: permiso denegado a la tabla business_users

y el login de negocio devolvia 500 para **todos** los usuarios, siempre. No era un
caso raro ni una condicion de carrera: era un login que nunca funciono. Solo se
manifiesto ahora porque es la primera vez que se ejercita el endpoint contra la base
de verdad.

El conteo de "6 columnas" estaba bien para el `SELECT ... FROM` y solo por eso
miraba bien; el `WHERE` no se conto porque no devuelve nada.

**Por que no se anuncia como `GRANT SELECT ON business_users`.** Seria mas corto y
dejaria de funcionar el punto de `0005`: el rol pasaria a poder leer cualquier columna
que se agregue en el futuro, sin que nadie lo decida. El minimo privilegio se
mantiene precisamente por ser minimo, y ampliarlo "por las dudas" es como se pierde.

**Tambien devuelve el ownership de las dos funciones de `0007`** al rol dedicado.
Nacen propiedad de `tempus_owner`, que es superusuario, asi que funcionan, pero
correrian con mas privilegios de los que necesitan y serian el unico par del proyecto
que se saltea la RLS por ser superusuario en vez de por tener `BYPASSRLS`. Con este
cambio las cuatro funciones de login comparten un unico dueno y un unico conjunto de
privilegios, que es lo que hace auditable la superficie pre-tenant: si manana se
agrega una quinta, hereda el mismo modelo de forma automatica.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_login_definer_email"
down_revision: str | None = "0007_principal_by_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFINER_ROLE = "tempus_login_definer"
OWNER_ROLE = "tempus_owner"

BUSINESS_LOGIN = "auth_business_user_for_login"
BUSINESS_BY_ID = "auth_business_user_by_id"
PLATFORM_BY_ID = "auth_platform_user_by_id"

#: Las columnas que las funciones de login necesitan *leer*, con `email` incluida
#: por el `WHERE`. Documentar el conjunto completo --y no solo el del `SELECT`-- es
#: lo que evita volver a commitear este bug.
COLUMNAS = ("id", "business_id", "password_hash", "role", "status", "full_name", "email")


def upgrade() -> None:
    # La septima columna. `email` es la que falta: es la del `WHERE`.
    op.execute(f'GRANT SELECT ({", ".join(COLUMNAS)}) ON business_users TO "{DEFINER_ROLE}"')

    # `auth_business_user_by_id` no filtra por `email`, asi que le alcanzan las
    # columnas que ya tenia: el privilegio se resuelve por rol, no por funcion.
    op.execute(f'GRANT "{DEFINER_ROLE}" TO {OWNER_ROLE} WITH ADMIN OPTION')
    op.execute(f'ALTER FUNCTION {BUSINESS_BY_ID}(uuid) OWNER TO "{DEFINER_ROLE}"')
    op.execute(f'ALTER FUNCTION {PLATFORM_BY_ID}(uuid) OWNER TO "{DEFINER_ROLE}"')
    op.execute(f'GRANT SELECT ON platform_users TO "{DEFINER_ROLE}"')


def downgrade() -> None:
    op.execute(f"ALTER FUNCTION {PLATFORM_BY_ID}(uuid) OWNER TO {OWNER_ROLE}")
    op.execute(f"ALTER FUNCTION {BUSINESS_BY_ID}(uuid) OWNER TO {OWNER_ROLE}")
    op.execute(f'REVOKE SELECT ON platform_users FROM "{DEFINER_ROLE}"')
    op.execute(f'REVOKE "{DEFINER_ROLE}" FROM {OWNER_ROLE}')

    # Se devuelve el estado de `0005`: sin `email`, que es lo que deja caer el
    # login de negocio. Es un downgrade que rompe el login a proposito -- es el
    # estado anterior -- y por eso vale la pena dejarlo explicito.
    op.execute(
        "REVOKE SELECT (id, business_id, password_hash, role, status, full_name) "
        f'ON business_users FROM "{DEFINER_ROLE}"'
    )
