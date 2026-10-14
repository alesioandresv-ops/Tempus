"""siembra las palabras reservadas del sistema"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op
revision: str = "0015_reserved_slugs"
down_revision: str | None = "0014_onboarding_create_business"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_TABLA = "slug_reservations"
RESERVADOS: tuple[tuple[str, str],...] = (
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
def _literal(valor: str) -> str:
    assert "\\" not in valor, f"motivo con barra invertida, necesita E'...': {valor!r}"
    return "'" + valor.replace("'", "''") + "'"
def _valores() -> str:
    filas = ",\n ".join(f"({_literal(slug)}, {_literal(motivo)})" for slug, motivo in RESERVADOS)
    return f""" INSERT INTO {_TABLA} (slug, reason) VALUES {filas} ON CONFLICT (slug) DO NOTHING """
def upgrade() -> None:
    # En single-role env (Render) la tabla tiene FORCE RLS y bloquea incluso al owner.
    # Deshabilitamos RLS momentaneamente para sembrar, luego restauramos estado.
    op.execute("""
        DO $do$
        BEGIN
            EXECUTE 'ALTER TABLE slug_reservations DISABLE ROW LEVEL SECURITY';
        EXCEPTION WHEN others THEN NULL;
        END $do$;
    """)
    op.execute(_valores())
    op.execute("""
        DO $do$
        BEGIN
            EXECUTE 'ALTER TABLE slug_reservations ENABLE ROW LEVEL SECURITY';
            EXECUTE 'ALTER TABLE slug_reservations FORCE ROW LEVEL SECURITY';
        EXCEPTION WHEN others THEN NULL;
        END $do$;
    """)
def downgrade() -> None:
    op.execute(""" DO $do$ BEGIN EXECUTE 'ALTER TABLE slug_reservations DISABLE ROW LEVEL SECURITY'; EXCEPTION WHEN others THEN NULL; END $do$; """)
    marcas = ", ".join(_literal(slug) for slug, _ in RESERVADOS)
    op.execute(f"DELETE FROM {_TABLA} WHERE slug IN ({marcas})")
    op.execute(""" DO $do$ BEGIN EXECUTE 'ALTER TABLE slug_reservations ENABLE ROW LEVEL SECURITY'; EXECUTE 'ALTER TABLE slug_reservations FORCE ROW LEVEL SECURITY'; EXCEPTION WHEN others THEN NULL; END $do$; """)
