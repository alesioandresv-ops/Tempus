"""Reparar la ACL que la 0002 le revocó al dueño en la base de un solo rol.

**El problema.** La 0002 original revoca `ALL` desde el rol de la aplicación sobre
las tres tablas de administración (`FORBIDDEN_FOR_APP_ROLE`). En multi-rol el
revocado es `tempus_app`, un rol que **no** es dueño de las tablas, y el dueño
conserva sus privilegios. En el despliegue de Render hay **un único rol**
(`DATABASE_URL == DATABASE_MIGRATION_URL`, y `DB_APP_ROLE` apunta a ese mismo
rol), así que el `REVOKE` cayó sobre el propio dueño y lo dejó sin privilegios
sobre la tabla.

`businesses` y `slug_reservations` se reparan solas: 0015 y 0019 reclaman el
ownership y re-otorgan `ALL` al insertar o crear la FK. **`platform_users` no la
toca ninguna migración posterior**, así que ese hueco quedó abierto y rompe las
funciones `SECURITY DEFINER` de plataforma (`auth_platform_user_for_login` de
0004 y `auth_platform_user_by_id` de 0007): corren como el dueño, y sin ACL
fallan con `permission denied for table platform_users` en el primer login de un
operador. El síntoma llega recién en runtime, no en la migración, que es la peor
forma de enterarse.

**Qué hace.** Re-otorga `ALL` a `CURRENT_USER` (nunca a un rol hardcodeado) sobre
las tres tablas prohibidas. Es idempotente y no-op en multi-rol: ahí
`CURRENT_USER` es el dueño, que ya tiene todo. No re-otorga el `UPDATE` por
columnas de `business_users`: esa restricción es deliberada (0002) y ningún flujo
la necesita — el alta **inserta** al miembro, no actualiza `status` ni
`password_hash`.

Migración 0021, numerada manualmente tras 0020.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021_platform_users_acl"
down_revision: str | None = "0020_coordenadas_business_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Copia congelada de `FORBIDDEN_FOR_APP_ROLE` de 0002 (ver su docstring).
FORBIDDEN_FOR_APP_ROLE: tuple[str, ...] = (
    "platform_users",
    "businesses",
    "slug_reservations",
)


def upgrade() -> None:
    for table in FORBIDDEN_FOR_APP_ROLE:
        # `CURRENT_USER` porque el nombre del rol de Render es efímero y distinto
        # por base; el único nombre estable es el de la conexión que migra.
        op.execute(f'GRANT ALL ON TABLE "{table}" TO CURRENT_USER')


def downgrade() -> None:
    """No hay un estado anterior seguro: la ACL rota **era** el bug.

    Un `REVOKE` acá reintroduciría el `permission denied` de plataforma. Es un
    piso duro, como `0003_auth_roles`.
    """
