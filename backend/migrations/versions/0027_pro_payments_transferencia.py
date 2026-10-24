"""pagos del plan pro por transferencia (naranja x) y vigencia del pro

El plan Pro deja de cobrarse solo por Mercado Pago: se agrega el cobro manual por
transferencia bancaria. El dueño transfiere a la cuenta de la plataforma, manda el
comprobante por WhatsApp, y un humano lo aprueba en el panel administrativo.

Dos piezas:

1. **`pro_payments`**: un registro por intento de pago. Nace `pending` cuando el
   dueño confirma el envío (`POST /api/v1/payments/intent`), y pasa a `approved`
   (activa 30 días de Pro) o `rejected` cuando el admin lo revisa. Es tabla de
   tenant: sigue el patrón de `mp_subscriptions` (RLS por `business_id`,
   `tenant_unique_key` para las FK compuestas, trigger de `updated_at`).

2. **`businesses.pro_until`**: hasta cuándo vale el Pro cobrado por transferencia.
   El Pro por suscripción de MP no caduca (NULL); el cobrado manual sí. La regla
   vive en `publicacion.mes_grats_vencido`, que consulta esta columna: un Pro con
   `pro_until` ya pasado vuelve a la regla del trial.

La RLS, el trigger de `updated_at` y los grants a `tempus_app` se declaran aquí,
con el mismo patrón que 0019 pero sin tocar sus listas congeladas.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.db.types

revision: str = "0027_pro_payments_transferencia"
down_revision: str | None = "0026_asignar_servicios_al_alta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_GUC = "app.current_business_id"
APP_ROLE = "tempus_app"
DEFINER_ROLE = "tempus_definer"

NUEVAS_TABLAS = ["pro_payments"]


def _crear_enum() -> None:
    op.execute(
        """
        DO $enum$ BEGIN
            CREATE TYPE pro_payment_status AS ENUM ('pending', 'approved', 'rejected');
        EXCEPTION WHEN duplicate_object THEN NULL; END $enum$;
        """
    )


def _crear_trigger_updated_at() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_pro_payments_updated_at ON pro_payments;
        CREATE TRIGGER trg_pro_payments_updated_at
            BEFORE UPDATE ON pro_payments
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def _crear_rls() -> None:
    for tabla in NUEVAS_TABLAS:
        op.execute(f'ALTER TABLE "{tabla}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{tabla}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY "{tabla}_tenant_isolation" ON "{tabla}" '
            f"USING (business_id = NULLIF(current_setting('{TENANT_GUC}', true), '')::uuid) "
            f"WITH CHECK (business_id = NULLIF(current_setting('{TENANT_GUC}', true), '')::uuid)"
        )


def _quitar_rls() -> None:
    for tabla in NUEVAS_TABLAS:
        op.execute(f'DROP POLICY IF EXISTS "{tabla}_tenant_isolation" ON "{tabla}"')
        op.execute(f'ALTER TABLE "{tabla}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{tabla}" DISABLE ROW LEVEL SECURITY')


def _grant_app_role() -> None:
    op.execute(
        f"""
        DO $grant$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE pro_payments TO "{APP_ROLE}"';
            END IF;
        END $grant$;
        """
    )


def _grant_definer_role() -> None:
    """El rol de definición solo necesita leer los pagos para resolver el estado."""
    op.execute(
        f"""
        DO $grant$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{DEFINER_ROLE}') THEN
                EXECUTE 'GRANT SELECT ON TABLE pro_payments TO "{DEFINER_ROLE}"';
            END IF;
        END $grant$;
        """
    )


def upgrade() -> None:
    _crear_enum()

    # La FK de `pro_payments` apunta a `businesses`, que está protegida: se abre
    # con `claim_and_open` y se cierra al final. Es el patrón de 0019.
    from alembic_postgresql_compat import claim_and_open, restore

    claim_and_open("businesses")

    op.create_table(
        "pro_payments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("business_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default=sa.text("'ARS'"), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="pro_payment_status", create_type=False),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("method", sa.String(length=32), server_default=sa.text("'naranja_x'"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("approved_at", app.db.types.UtcDateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            app.db.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            app.db.types.UtcDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pro_payments")),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_pro_payments_business_id_businesses",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "business_id", name="uq_pro_payments_id_business_key"),
        sa.CheckConstraint("amount >= 0", name=op.f("ck_pro_payments_amount_non_negative")),
        info={"rls": True},
    )
    op.create_index(op.f("ix_pro_payments_business_id"), "pro_payments", ["business_id"])
    op.create_index(
        "ix_pro_payments_status_created_at",
        "pro_payments",
        ["status", "created_at"],
    )

    op.add_column("businesses", sa.Column("pro_until", app.db.types.UtcDateTime(timezone=True), nullable=True))

    _crear_trigger_updated_at()
    _crear_rls()
    _grant_app_role()
    _grant_definer_role()
    restore("businesses")


def downgrade() -> None:
    op.drop_column("businesses", "pro_until")
    op.drop_index(op.f("ix_pro_payments_status_created_at"), table_name="pro_payments")
    op.drop_index(op.f("ix_pro_payments_business_id"), table_name="pro_payments")
    op.drop_table("pro_payments")
    op.execute("DROP TYPE IF EXISTS pro_payment_status")
