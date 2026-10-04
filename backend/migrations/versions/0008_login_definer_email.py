"""Arreglar el login de negocio: faltaba SELECT sobre email

Las dos funciones de busqueda por id (`auth_business_user_by_id`,
`auth_platform_user_by_id`) tambien son pre-tenant: corren antes de que exista un
tenant, asi que su dueno tiene que ser el rol BYPASSRLS. Mismo mecanismo que `0005`,
y por eso mismo va por el puente de `tempus_migrator`: la membresia del definer
se otorga y se devuelve dentro de esta transaccion, y el `CREATE` en el esquema--que
el dueno nuevo necesita para que la funcion se pueda recrear-- tambien. Ver
`0005_login_definer_role` para por que hacen falta las dos mitades.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from migrations.roles_definer import (
    definer_con_create,
    definer_sin_create,
    membresia_temporal_al_definer,
    revoca_membresia_al_definer,
)

revision: str = "0008_login_definer_email"
down_revision: str | None = "0007_principal_by_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFINER_ROLE = "tempus_login_definer"
OWNER_ROLE = "tempus_owner"
BUSINESS_LOGIN = "auth_business_user_for_login"
BUSINESS_BY_ID = "auth_business_user_by_id"
PLATFORM_BY_ID = "auth_platform_user_by_id"
COLUMNAS = ("id", "business_id", "password_hash", "role", "status", "full_name", "email")


def upgrade() -> None:
    cols = ", ".join(COLUMNAS)
    op.execute(f'GRANT SELECT ({cols}) ON business_users TO "{DEFINER_ROLE}"')

    membresia_temporal_al_definer()
    definer_con_create()
    op.execute(f'ALTER FUNCTION {BUSINESS_BY_ID}(uuid) OWNER TO "{DEFINER_ROLE}"')
    op.execute(f'ALTER FUNCTION {PLATFORM_BY_ID}(uuid) OWNER TO "{DEFINER_ROLE}"')
    definer_sin_create()
    revoca_membresia_al_definer()

    op.execute(f'GRANT SELECT ON platform_users TO "{DEFINER_ROLE}"')


def downgrade() -> None:
    membresia_temporal_al_definer()
    definer_con_create()
    op.execute(f"ALTER FUNCTION {PLATFORM_BY_ID}(uuid) OWNER TO {OWNER_ROLE}")
    op.execute(f"ALTER FUNCTION {BUSINESS_BY_ID}(uuid) OWNER TO {OWNER_ROLE}")
    definer_sin_create()
    revoca_membresia_al_definer()

    op.execute(f'REVOKE SELECT ON platform_users FROM "{DEFINER_ROLE}"')
    op.execute(
        "REVOKE SELECT (id, business_id, password_hash, role, status, full_name) "
        f'ON business_users FROM "{DEFINER_ROLE}"'
    )
