"""Tokens de reseteo de contrasena (flujo "olvide mi contrasena").

`password_reset_tokens` guarda el **hash** del token de un solo uso (SHA-256,
mismo criterio que `refresh_tokens`), el email denormalizado para soporte y la
IP del pedido. Un token sirve una vez, dentro de una hora, y un pedido nuevo
invalida el anterior (ultimo-token-gana).

**Tabla global, sin RLS.** Igual que `refresh_tokens`: el reset llega por email
sin contexto de negocio y una misma persona puede ser miembro de varios
negocios; aislar por tenant partiria el flujo. El dato sensible ya esta
protegido por diseño: la tabla guarda hashes, no tokens en claro.

**Grants.** No se conceden a mano: la migracion `0002` dejo un `ALTER DEFAULT
PRIVILEGES FOR ROLE tempus_owner GRANT ... ON TABLES TO tempus_app` que cubre
toda tabla nueva. Si un dia ese default desaparece, esta migracion se rompe en
los tests de integracion (el endpoint daria 403 al INSERT) y no en produccion.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.db.types

revision: str = "0030_password_resets"
down_revision: str | None = "0029_admin_panel_seguro"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # El rol de la app no tiene UPDATE sobre `password_hash` (por diseño, 0002),
    # y el reset corre sin GUC de tenant: el cambio de contraseña tiene que ser
    # una función SECURITY DEFINER, como `auth_business_user_for_login`.
    # Rehashea la contraseña en **todas** las membresías del email y revoca sus
    # refresh tokens, en una sola sentencia: la persona recupera la cuenta
    # completa, no negocio por negocio.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION auth_reset_password_for_email(
            p_email citext,
            p_password_hash text
        )
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $cuerpo$
        DECLARE
            v_afectadas integer;
        BEGIN
            UPDATE business_users u
               SET password_hash = p_password_hash
             WHERE u.email = p_email;
            GET DIAGNOSTICS v_afectadas = ROW_COUNT;

            UPDATE refresh_tokens rt
               SET revoked_at = now()
             WHERE rt.user_id IN (
                       SELECT id FROM business_users WHERE email = p_email
                   )
               AND rt.revoked_at IS NULL;

            RETURN v_afectadas;
        END;
        $cuerpo$;
        """
    )
    op.execute(
        """
        DO $do$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_app') THEN
                EXECUTE 'GRANT EXECUTE ON FUNCTION '
                        || 'auth_reset_password_for_email(citext, text) TO tempus_app';
            END IF;
        END $do$;
        """
    )

    op.create_table(
        "password_reset_tokens",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", app.db.types.UtcDateTime(timezone=True), nullable=False),
        sa.Column("used_at", app.db.types.UtcDateTime(timezone=True), nullable=True),
        sa.Column(
            "requested_at",
            app.db.types.UtcDateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["business_users.id"],
            name="fk_password_reset_tokens_user_id_business_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_password_reset_tokens_token_hash")),
    )
    op.create_index(
        "ix_password_reset_tokens_email", "password_reset_tokens", ["email"], unique=False
    )
    op.create_index(
        "ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_reset_password_for_email(citext, text)")
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_email", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
