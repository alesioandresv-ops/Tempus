"""Mercado Pago: credenciales, suscripciones y pagos de reserva (Fase 9.1)

Revision ID: 0019_mercado_pago
Revises: 0018_whatsapp_reminders
Create Date: 2026-10-09

Agrega el schema completo de la Fase 9.1: tres tablas de tenant nuevas
(`mp_payment_accounts`, `mp_subscriptions`, `booking_payments`), cinco enums
nuevos (`business_plan`, `mp_subscription_status`, `mp_payment_status`,
`payment_policy`, `deposit_mode`), dos valores nuevos en enums existentes
(`pending_payment` en `booking_status`, `mercadopago` en `webhook_provider`) y
las columnas de plan/politica de pago en `businesses`.

**Por que RLS en esta migracion y no en 0002.** 0002 maneja las tablas que creo
0001. Las tablas nuevas se crean aca y su RLS, sus triggers de `updated_at` y sus
grants se declaran aca, con el mismo patron que 0002 pero sin tocar sus listas
congeladas: una migracion vieja no se edita para cubrir tablas que todavia no
existian cuando corrio.

**Los valores nuevos de enums existentes van en `autocommit_block`.** Un
`ALTER TYPE ... ADD VALUE` no corre dentro de una transaccion normal (patron de
0003). Los tipos nuevos se crean con `CREATE TYPE` idempotente dentro de la
transaccion, y las columnas los referencian con `create_type=False` para que
Alembic no intente recrearlos.

**`businesses` sigue prohibida para la app; se reabre columna por columna.**
El rol de la app no puede hacer UPDATE sobre `businesses` (0002 la prohibe y
0011 la reabrio por columnas). `plan` y la politica de pago son columnas que el
servicio de suscripciones/pagos actualiza con el GUC puesto, asi que esta
migracion concede el UPDATE exclusivamente sobre esas cinco columnas.

**La EXCLUDE `no_overlap` se recrea con `pending_payment` en el predicado.**
Un turno pendiente de pago `pending_payment` reserva el horario igual que uno
confirmado: no se puede ofrecer dos veces el hueco mientras el cliente no paga.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Tipos propios del proyecto: `app.db.types.UtcDateTime`.
import app.db.types
from migrations.roles_definer import (
    definer_con_create,
    definer_sin_create,
    membresia_temporal_al_definer,
    revoca_membresia_al_definer,
)
from sqlalchemy.dialects import postgresql

revision: str = "0019_mercado_pago"
down_revision: str | None = "0018_whatsapp_reminders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tempus_app"
TENANT_GUC = "app.current_business_id"
OWNER_ROLE = "tempus_owner"
DEFINER_ROLE = "tempus_login_definer"

#: Funcion de pre-tenant que resuelve el negocio de un evento de Mercado Pago.
#: El webhook no trae GUC (MP no sabe de tenants), asi que la resolucion corre como
#: el rol BYPASSRLS antes de abrir la sesion de tenant.
RESOLVE_FUNCTION = "mercadopago_business_for_event"
RESOLVE_SIGNATURE = "mercadopago_business_for_event(text, text)"

#: Las tres tablas de tenant nuevas. Copia congelada, no import de `app.models`.
NUEVAS_TABLAS: tuple[str, ...] = (
    "mp_payment_accounts",
    "mp_subscriptions",
    "booking_payments",
)

#: Columnas que el servicio de pagos actualiza sobre `businesses`. Coincide con
#: las columnas del modelo `Business` de la Fase 9.1.
COLUMNAS_PAGO_BUSINESSES = (
    "plan",
    "payment_policy",
    "deposit_mode",
    "deposit_value",
    "deposit_non_refundable",
)


def _crear_enums_nuevos() -> None:
    """`CREATE TYPE` idempotente para los cinco enums de la Fase 9.1."""
    definiciones: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("business_plan", ("free", "pro")),
        (
            "mp_subscription_status",
            ("pending", "active", "paused", "in_arrears", "cancelled"),
        ),
        (
            "mp_payment_status",
            (
                "pending",
                "in_process",
                "approved",
                "rejected",
                "refunded",
                "partially_refunded",
            ),
        ),
        ("payment_policy", ("none", "deposit", "full")),
        ("deposit_mode", ("fixed", "percent")),
    )
    for tipo, valores in definiciones:
        lista = ", ".join(f"'{v}'" for v in valores)
        op.execute(
            f"DO $$ BEGIN CREATE TYPE {tipo} AS ENUM ({lista}); "
            f"EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
        )


def _agregar_valores_a_enums_existentes() -> None:
    """`ALTER TYPE ... ADD VALUE` fuera de la transaccion (patron 0003)."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE booking_status ADD VALUE IF NOT EXISTS 'pending_payment'")
        op.execute("ALTER TYPE webhook_provider ADD VALUE IF NOT EXISTS 'mercadopago'")


def _enable_rls() -> None:
    for tabla in NUEVAS_TABLAS:
        op.execute(f'ALTER TABLE "{tabla}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{tabla}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY "{tabla}_tenant_isolation" ON "{tabla}" '
            f"USING (business_id = NULLIF(current_setting('{TENANT_GUC}', true), '')::uuid) "
            f"WITH CHECK (business_id = NULLIF(current_setting('{TENANT_GUC}', true), '')::uuid)"
        )


def _disable_rls() -> None:
    for tabla in NUEVAS_TABLAS:
        op.execute(f'DROP POLICY IF EXISTS "{tabla}_tenant_isolation" ON "{tabla}"')
        op.execute(f'ALTER TABLE "{tabla}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{tabla}" DISABLE ROW LEVEL SECURITY')


def _create_triggers() -> None:
    # `set_updated_at()` viene de 0002.
    for tabla in NUEVAS_TABLAS:
        op.execute(
            f'CREATE TRIGGER trg_{tabla}_set_updated_at BEFORE UPDATE ON "{tabla}" '
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def _drop_triggers() -> None:
    for tabla in NUEVAS_TABLAS:
        op.execute(f'DROP TRIGGER IF EXISTS trg_{tabla}_set_updated_at ON "{tabla}"')


def _grant_app_role() -> None:
    columnas = ", ".join(f'"{col}"' for col in COLUMNAS_PAGO_BUSINESSES)
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE "
        f'ON TABLE "{NUEVAS_TABLAS[0]}", "{NUEVAS_TABLAS[1]}", "{NUEVAS_TABLAS[2]}" '
        f'TO "{APP_ROLE}"'
    )
    op.execute(f'GRANT UPDATE ({columnas}) ON businesses TO "{APP_ROLE}"')


def _revoke_app_role() -> None:
    op.execute(
        f'REVOKE ALL ON TABLE "{NUEVAS_TABLAS[0]}", "{NUEVAS_TABLAS[1]}", "{NUEVAS_TABLAS[2]}" '
        f'FROM "{APP_ROLE}"'
    )
    op.execute(f'REVOKE UPDATE ON businesses FROM "{APP_ROLE}"')


def _crear_funcion_resolver() -> None:
    """Crea la funcion de pre-tenant que resuelve el negocio de un evento de MP.

    El webhook llega sin GUC: Mercado Pago no sabe de tenants. La funcion corre
    como el rol BYPASSRLS, lee las dos tablas de tenant y devuelve el `business_id`
    para que el handler abra la sesion del tenant correcto. Es el mismo patron de
    `booking_tenant_for_token` (0006/0017): `SECURITY DEFINER`, `search_path`
    cerrado y `SELECT` por columna para el definer.

    El `SELECT` granulado importa: el definer es BYPASSRLS, asi que darle `SELECT`
    sobre la tabla entera lo convertiria en un lector global de pagos de clientes.
    Solo ve las columnas que el cuerpo de la funcion toca.
    """
    op.execute(
        f'GRANT SELECT (business_id, preapproval_id) ON mp_subscriptions TO "{DEFINER_ROLE}"'
    )
    op.execute(
        f"GRANT SELECT (business_id, payment_id, external_reference) ON booking_payments "
        f'TO "{DEFINER_ROLE}"'
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {RESOLVE_FUNCTION}(p_kind text, p_external_id text)
        RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT business_id
            FROM public.mp_subscriptions
            WHERE p_kind = 'preapproval'
              AND preapproval_id = p_external_id
            UNION ALL
            SELECT business_id
            FROM public.booking_payments
            WHERE p_kind = 'payment'
              AND (payment_id = p_external_id OR external_reference = p_external_id)
            LIMIT 1
        $$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {RESOLVE_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {RESOLVE_SIGNATURE} TO {APP_ROLE}")
    op.execute(f'GRANT EXECUTE ON FUNCTION {RESOLVE_SIGNATURE} TO "{DEFINER_ROLE}"')

    # El dueno pasa a ser el definer (BYPASSRLS), igual que 0017: `tempus_owner`
    # no escapa a la RLS, y sin esto la funcion devolveria cero filas en runtime.
    membresia_temporal_al_definer()
    definer_con_create()
    op.execute(f'ALTER FUNCTION {RESOLVE_SIGNATURE} OWNER TO "{DEFINER_ROLE}"')
    definer_sin_create()
    revoca_membresia_al_definer()


def _borrar_funcion_resolver() -> None:
    # Primero se devuelve la propiedad a `tempus_owner` para poder hacer el DROP,
    # despues se sueltan los `SELECT` por columna del definer.
    membresia_temporal_al_definer()
    definer_con_create()
    op.execute(f'ALTER FUNCTION {RESOLVE_SIGNATURE} OWNER TO "{OWNER_ROLE}"')
    definer_sin_create()
    revoca_membresia_al_definer()

    op.execute(f"DROP FUNCTION IF EXISTS {RESOLVE_SIGNATURE}")
    op.execute(
        f"REVOKE SELECT (business_id, payment_id, external_reference) "
        f'ON booking_payments FROM "{DEFINER_ROLE}"'
    )
    op.execute(
        f'REVOKE SELECT (business_id, preapproval_id) ON mp_subscriptions FROM "{DEFINER_ROLE}"'
    )


def upgrade() -> None:
    _crear_enums_nuevos()
    _agregar_valores_a_enums_existentes()

    op.create_table(
        "mp_payment_accounts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("public_key", sa.String(length=255), nullable=True),
        sa.Column("sandbox", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mp_payment_accounts")),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_mp_payment_accounts_business_id_businesses",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("business_id", name="uq_mp_payment_accounts_business_id"),
        sa.UniqueConstraint("id", "business_id", name="uq_mp_payment_accounts_id_business_key"),
        sa.CheckConstraint(
            "char_length(access_token_encrypted) > 0",
            name=op.f("ck_mp_payment_accounts_access_token_not_empty"),
        ),
        info={"rls": True},
    )
    op.create_index(
        op.f("ix_mp_payment_accounts_business_id"),
        "mp_payment_accounts",
        ["business_id"],
    )

    op.create_table(
        "mp_subscriptions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("preapproval_id", sa.String(length=64), nullable=False),
        sa.Column("preapproval_plan_id", sa.String(length=128), nullable=True),
        sa.Column(
            "plan",
            postgresql.ENUM(name="business_plan", create_type=False),
            server_default=sa.text("'free'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="mp_subscription_status", create_type=False),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("status_detail", sa.Text(), nullable=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=3),
            server_default=sa.text("'ARS'"),
            nullable=False,
        ),
        sa.Column(
            "frequency",
            sa.String(length=16),
            server_default=sa.text("'months'"),
            nullable=False,
        ),
        sa.Column("frequency_type", sa.String(length=32), nullable=True),
        sa.Column("next_billing_at", app.db.types.UtcDateTime(timezone=True), nullable=True),
        sa.Column("activated_at", app.db.types.UtcDateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", app.db.types.UtcDateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mp_subscriptions")),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_mp_subscriptions_business_id_businesses",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("business_id", name="uq_mp_subscriptions_business_id"),
        sa.UniqueConstraint("preapproval_id", name="uq_mp_subscriptions_preapproval_id"),
        sa.UniqueConstraint("id", "business_id", name="uq_mp_subscriptions_id_business_key"),
        info={"rls": True},
    )
    op.create_index(
        op.f("ix_mp_subscriptions_business_id"),
        "mp_subscriptions",
        ["business_id"],
    )

    op.create_table(
        "booking_payments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("booking_id", sa.Uuid(), nullable=False),
        sa.Column("preference_id", sa.String(length=64), nullable=True),
        sa.Column("payment_id", sa.String(length=64), nullable=True),
        sa.Column("external_reference", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="mp_payment_status", create_type=False),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "policy",
            postgresql.ENUM(name="payment_policy", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "deposit_mode",
            postgresql.ENUM(name="deposit_mode", create_type=False),
            nullable=True,
        ),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=3),
            server_default=sa.text("'ARS'"),
            nullable=False,
        ),
        sa.Column(
            "refunded_amount",
            sa.Numeric(10, 2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("refunded_at", app.db.types.UtcDateTime(timezone=True), nullable=True),
        sa.Column("status_detail", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_booking_payments")),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_booking_payments_business_id_businesses",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["booking_id", "business_id"],
            ["bookings.id", "bookings.business_id"],
            name="fk_booking_payments_booking",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("booking_id", name="uq_booking_payments_booking_id"),
        sa.UniqueConstraint("payment_id", name="uq_booking_payments_payment_id"),
        sa.UniqueConstraint("id", "business_id", name="uq_booking_payments_id_business_key"),
        sa.CheckConstraint(
            "refunded_amount >= 0",
            name=op.f("ck_booking_payments_refunded_amount_non_negative"),
        ),
        info={"rls": True},
    )
    op.create_index(
        op.f("ix_booking_payments_business_id"),
        "booking_payments",
        ["business_id"],
    )

    # --- Columnas de plan y politica de pago en `businesses` ---
    op.add_column(
        "businesses",
        sa.Column(
            "plan",
            postgresql.ENUM(name="business_plan", create_type=False),
            server_default=sa.text("'free'"),
            nullable=False,
        ),
    )
    op.add_column(
        "businesses",
        sa.Column(
            "payment_policy",
            postgresql.ENUM(name="payment_policy", create_type=False),
            server_default=sa.text("'none'"),
            nullable=False,
        ),
    )
    op.add_column(
        "businesses",
        sa.Column(
            "deposit_mode",
            postgresql.ENUM(name="deposit_mode", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "businesses",
        sa.Column("deposit_value", sa.Numeric(10, 2), nullable=True),
    )
    op.add_column(
        "businesses",
        sa.Column(
            "deposit_non_refundable",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # --- Recrear la EXCLUDE de `bookings` con `pending_payment` ---
    op.execute("ALTER TABLE bookings DROP CONSTRAINT no_overlap")
    op.execute(
        "ALTER TABLE bookings ADD CONSTRAINT no_overlap EXCLUDE USING gist "
        "(professional_id WITH =, "
        "tstzrange(occupied_from, occupied_to, '[)') WITH &&) "
        "WHERE (status IN ('confirmed', 'pending_hold', 'pending_payment'))"
    )

    _enable_rls()
    _create_triggers()
    _grant_app_role()
    _crear_funcion_resolver()


def downgrade() -> None:
    # Primero lo que depende de las tablas, en orden inverso al upgrade.
    _borrar_funcion_resolver()
    _revoke_app_role()
    _drop_triggers()
    _disable_rls()

    op.drop_table("booking_payments")
    op.drop_table("mp_subscriptions")
    op.drop_table("mp_payment_accounts")

    op.drop_column("businesses", "deposit_non_refundable")
    op.drop_column("businesses", "deposit_value")
    op.drop_column("businesses", "deposit_mode")
    op.drop_column("businesses", "payment_policy")
    op.drop_column("businesses", "plan")

    # Restaurar la EXCLUDE original (sin `pending_payment`).
    op.execute("ALTER TABLE bookings DROP CONSTRAINT no_overlap")
    op.execute(
        "ALTER TABLE bookings ADD CONSTRAINT no_overlap EXCLUDE USING gist "
        "(professional_id WITH =, "
        "tstzrange(occupied_from, occupied_to, '[)') WITH &&) "
        "WHERE (status IN ('confirmed', 'pending_hold'))"
    )

    # Tipos nuevos, que ya no usa nadie tras los DROP de arriba.
    for tipo in (
        "mp_payment_status",
        "mp_subscription_status",
        "payment_policy",
        "deposit_mode",
        "business_plan",
    ):
        op.execute(f'DROP TYPE IF EXISTS "{tipo}" CASCADE')

    # `pending_payment` y `mercadopago` no se pueden quitar de los enums viejos
    # (ALTER TYPE no permite quitar valores): es un piso, igual que 0003.
