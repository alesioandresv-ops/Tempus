"""siembra las palabras reservadas del sistema - single-role safe"""

from __future__ import annotations
from collections.abc import Sequence
from alembic import op
from migrations.rls_safe import claim_and_open, restore

revision: str = "0015_reserved_slugs"
down_revision: str | None = "0014_onboarding_create_business"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_TABLA = "slug_reservations"
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


def _valores() -> str:
    filas = ",\n ".join(f"({_literal(s)}, {_literal(m)})" for s, m in RESERVADOS)
    return f"INSERT INTO {_TABLA} (slug, reason) VALUES {filas} ON CONFLICT (slug) DO NOTHING"


def upgrade() -> None:
    # Single-role safe: reclama ownership, apaga RLS y re-otorga privilegios al
    # usuario actual, y al final deja la tabla protegida otra vez. El patron vive
    # en `migrations.rls_safe` para no repetirlo en cada migracion.
    claim_and_open(_TABLA)
    op.execute(_valores())
    restore(_TABLA)


def downgrade() -> None:
    claim_and_open(_TABLA)
    marcas = ", ".join(_literal(s) for s, _ in RESERVADOS)
    op.execute(f"DELETE FROM {_TABLA} WHERE slug IN ({marcas})")
    restore(_TABLA)
