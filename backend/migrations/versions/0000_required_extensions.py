"""required extensions

Revision ID: 0000_required_extensions
Revises:
Create Date: 2026-10-02

Crea las extensiones de las que depende el esquema.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0000_required_extensions"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONES: tuple[tuple[str, str], ...] = (
    (
        "citext",
        "comparacion insensible a mayusculas para `platform_users.email`, "
        "`businesses.slug`, `services.slug` y `business_users.email`.",
    ),
    (
        "btree_gist",
        "operador de igualdad GiST sobre `uuid`. Restriccion EXCLUDE anti-doble-reserva.",
    ),
    (
        "pgcrypto",
        "`gen_random_bytes`. No se usa en runtime en este entorno (AppLocker bloquea dll).",
    ),
)


def upgrade() -> None:
    for nombre, _ in EXTENSIONES:
        if nombre == "pgcrypto":
            # Entorno de la facu bloquea pgcrypto.dll via AppLocker.
            # Se intenta crear, si falla se ignora - verificado que no se usa en runtime.
            op.execute("""
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS "pgcrypto";
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgcrypto no disponible por AppLocker, se ignora - no se usa en runtime';
END
$$;
""")
        else:
            op.execute(f'CREATE EXTENSION IF NOT EXISTS "{nombre}"')


def downgrade() -> None:
    pass
