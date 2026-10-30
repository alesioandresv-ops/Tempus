"""Garantiza auth_reset_password_for_email(citext, text) y su EXECUTE para la app.

**El problema.** La migracion `0030` crea la funcion, la tabla `password_reset_tokens`
y los grants en el mismo upgrade. En produccion la tabla existe (el pedido del mail
funciona: hace INSERT) pero la funcion **no** (el consumo responde
`function auth_reset_password_for_email(citext, text) does not exist`), con
`alembic_version` ya en `0030`: Alembic no va a volver a correr ese upgrade.

No hay forma segura de reconstruir desde los logs *por que* el CREATE no dejo
rastro (un fallo a mitad de la migracion habria dejado la tabla a medias tambien).
Lo que importa es que el estado catalogo vs. repo **discrepa**, y eso es exactamente
la situacion para la que existe `verificar_produccion.py`: se arregla leyendo el
catalogo, no adivinando.

**La salida.** Una migracion nueva, idempotente: `CREATE OR REPLACE` la funcion y
`GRANT ... IF EXISTS` via DO block. Idempotente significa tambien que no rompe los
despliegues donde la funcion SI esta (local, CI): recrear el mismo cuerpo es un no-op
logico. Migracion 0031, numerada manualmente tras 0030.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0031_recrear_funcion_reset"
down_revision: str | None = "0030_password_resets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
    # REVOKE PUBLIC + GRANT app, en un solo DO block tolerante: si el rol de la app
    # no existe (single-role de Render, donde el rol se llama distinto), el GRANT se
    # salta pero el REVOKE cierra la superficie igual.
    op.execute(
        """
        REVOKE ALL ON FUNCTION auth_reset_password_for_email(citext, text) FROM PUBLIC
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


def downgrade() -> None:
    # No se revierte: dejar la funcion ausente es el estado roto que esta migracion
    # arregla. Bajar de aqui en adelante exige reconstruir el entorno.
    pass
