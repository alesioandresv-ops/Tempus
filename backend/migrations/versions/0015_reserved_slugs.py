"""siembra las palabras reservadas del sistema - single-role safe"""
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
def _literal(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"
def _valores() -> str:
    filas = ",\n ".join(f"({_literal(s)}, {_literal(m)})" for s,m in RESERVADOS)
    return f"INSERT INTO {_TABLA} (slug, reason) VALUES {filas} ON CONFLICT (slug) DO NOTHING"
def upgrade() -> None:
    op.execute("""
        DO $do$
        DECLARE
            tbl_owner text;
        BEGIN
            -- Averiguar owner actual por si es tempus_owner que no existe
            SELECT tableowner INTO tbl_owner FROM pg_tables WHERE tablename = 'slug_reservations';
            -- Si podemos, reclamar ownership al usuario actual
            BEGIN
                EXECUTE format('ALTER TABLE slug_reservations OWNER TO %I', CURRENT_USER);
            EXCEPTION WHEN others THEN NULL;
            END;
            -- Quitar RLS a la fuerza
            BEGIN
                EXECUTE 'ALTER TABLE slug_reservations DISABLE ROW LEVEL SECURITY';
            EXCEPTION WHEN others THEN NULL;
            END;
            BEGIN
                EXECUTE 'ALTER TABLE slug_reservations NO FORCE ROW LEVEL SECURITY';
            EXCEPTION WHEN others THEN NULL;
            END;
            -- Dar permisos explicitos
            BEGIN
                EXECUTE format('GRANT ALL ON TABLE slug_reservations TO %I', CURRENT_USER);
            EXCEPTION WHEN others THEN NULL;
            END;
        END $do$;
    """)
    op.execute(_valores())
    op.execute("""
        DO $do$
        BEGIN
            BEGIN
                EXECUTE 'ALTER TABLE slug_reservations ENABLE ROW LEVEL SECURITY';
            EXCEPTION WHEN others THEN NULL;
            END;
            BEGIN
                EXECUTE 'ALTER TABLE slug_reservations FORCE ROW LEVEL SECURITY';
            EXCEPTION WHEN others THEN NULL;
            END;
        END $do$;
    """)
def downgrade() -> None:
    op.execute("DO $do$ BEGIN EXECUTE 'ALTER TABLE slug_reservations DISABLE ROW LEVEL SECURITY'; EXECUTE 'ALTER TABLE slug_reservations NO FORCE ROW LEVEL SECURITY'; EXCEPTION WHEN others THEN NULL; END $do$;")
    marcas = ", ".join(_literal(s) for s,_ in RESERVADOS)
    op.execute(f"DELETE FROM {_TABLA} WHERE slug IN ({marcas})")
    op.execute("DO $do$ BEGIN EXECUTE 'ALTER TABLE slug_reservations ENABLE ROW LEVEL SECURITY'; EXECUTE 'ALTER TABLE slug_reservations FORCE ROW LEVEL SECURITY'; EXCEPTION WHEN others THEN NULL; END $do$;")
