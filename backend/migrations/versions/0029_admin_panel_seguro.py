"""auditoria del panel administrativo seguro

`POST /admin/login` y las acciones del panel (activar Pro, bloquear,
desbloquear, aprobar/rechazar un pago) dejan una fila en `admin_audit_log` con
quien la hizo y desde que IP.

**La tabla es global y no lleva RLS.** No es un olvido: el panel administra la
plataforma entera, no un tenant, y `business_id` es el *objetivo* de la accion,
nullable porque un login no apunta a ningun negocio. Ademas se guarda como UUID
suelto, sin FK, para que borrar un negocio no borre la historia de quien lo
bloqueo ni cuando.

**El rol de la app no tiene grants sobre esta tabla.** El panel usa el engine
administrativo (`DATABASE_MIGRATION_URL`, rol `tempus_owner`), que es el unico
que debe poder escribirla. El `REVOKE` explicito es defensa en profundidad: si
un dia alguien agrega `ALTER DEFAULT PRIVILEGES` para las tablas nuevas, la
auditoria no se vuelve escribible desde una request de negocio.

**El panel tambien necesita leer `pro_payments` de todos los negocios** para
mostrar el email del ultimo comprobante y contar los pagos pendientes. Esa tabla
tiene `FORCE ROW LEVEL SECURITY` y `tempus_owner` es `NOBYPASSRLS`, asi que sin
una politica dirigida al rol administrativo la lectura cruzada devuelve cero
filas en silencio. La politica de abajo es **solo SELECT y solo para
`tempus_owner`**: las politicas de aislamiento de `tempus_app` no se tocan y un
request de negocio sigue viendo unicamente su tenant.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.db.types

revision: str = "0029_admin_panel_seguro"
down_revision: str | None = "0028_grant_businesses_pro_update"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tempus_app"
ADMIN_ROLE = "tempus_owner"


def _revocar_app_role() -> None:
    op.execute(
        f"""
        DO $revoke$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                EXECUTE 'REVOKE ALL ON TABLE admin_audit_log FROM "{APP_ROLE}"';
            END IF;
        END $revoke$;
        """
    )


def _permitir_lectura_admin() -> None:
    """Deja que el rol del panel lea pagos de cualquier tenant.

    Sin esto el panel muestra el email y los pagos pendientes vacios: la RLS de
    `pro_payments` filtra contra un GUC que el engine administrativo no fija.
    La politica no aplica a `tempus_app`, que sigue atado a su tenant.
    """
    op.execute(
        f"""
        DO $policy$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_ROLE}') THEN
                EXECUTE 'DROP POLICY IF EXISTS pro_payments_admin_read ON pro_payments';
                EXECUTE 'CREATE POLICY pro_payments_admin_read ON pro_payments '
                        'FOR SELECT TO "{ADMIN_ROLE}" USING (true)';
            END IF;
        END $policy$;
        """
    )


def _quitar_lectura_admin() -> None:
    op.execute("DROP POLICY IF EXISTS pro_payments_admin_read ON pro_payments")


def upgrade() -> None:
    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("admin_user", sa.Text(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            app.db.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit_log")),
        # Sin `info={"rls": True}` a proposito. Ver el docstring del modulo.
    )
    op.create_index(
        op.f("ix_admin_audit_log_business_id"),
        "admin_audit_log",
        ["business_id"],
    )
    _revocar_app_role()
    _permitir_lectura_admin()


def downgrade() -> None:
    _quitar_lectura_admin()
    op.drop_index(op.f("ix_admin_audit_log_business_id"), table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
