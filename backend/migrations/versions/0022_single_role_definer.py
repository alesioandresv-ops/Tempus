"""Rol definer single-role: login pre-tenant sin `BYPASSRLS`.

**El problema.** El diseño multi-rol resuelve el login pre-tenant con un rol
`tempus_login_definer` con `BYPASSRLS` (creado por
`infra/postgres/init/02-login-definer.sql`) que es dueño de las funciones
`SECURITY DEFINER` que leen tablas de tenant sin GUC. En el despliegue de Render
hay **un solo rol** (`tempus_db_6h9u_user`): no es superusuario y `BYPASSRLS`
**solo lo puede otorgar un superusuario**, así que el definer no existe y las
funciones quedan a nombre del rol de la app, que está sujeto a
`FORCE ROW LEVEL SECURITY` y por lo tanto no ve ninguna fila sin GUC de tenant.

El síntoma llega recién en runtime: un alta devuelve `201` y el login inmediato
responde `401` con `motivo=sin_coincidencia, candidatos=0`; el dren de la outbox
ve `0` negocios con trabajo; el webhook de Mercado Pago no resuelve el negocio.

**La salida.** No hace falta `BYPASSRLS`. Una política RLS dirigida a un rol
(`TO <rol>`) es *permissive* y se suma (OR) a la de tenant **solo para ese rol**:
el resto de los roles--incluido el de la app--sigue sujeto a la política de
tenant. Esta migración crea el rol definer (`NOBYPASSRLS`), le da lectura
cross-tenant con políticas `TO definer USING (true)`, y mueve a él la propiedad
de las funciones que necesitan leer sin GUC. La política no alcanza al rol de la
app, así que su aislamiento no cambia.

**No-op en multi-rol.** Si `tempus_login_definer` ya existe con `BYPASSRLS`, el
diseño original es el correcto y esta migración no toca nada. La detección es el
atributo del rol, no una variable de entorno.

Migración 0022, numerada manualmente tras 0021.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_single_role_definer"
down_revision: str | None = "0021_platform_users_acl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Mismo nombre que el definer multi-rol: así las migraciones que lo buscan por
#: nombre (`0005`, `0009`, `0017`, `0019`) lo encuentran igual.
DEFINER_ROLE = os.getenv("DB_LOGIN_DEFINER_ROLE", "tempus_login_definer")

#: Tablas que las funciones definer leen sin GUC de tenant, con las columnas que
#: cada cuerpo toca (incluidas las del `WHERE`, que también exigen privilegio).
#: Copia congelada de los `GRANT SELECT (...)` de 0008/0009/0017/0019.
LECTURAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "business_users",
        ("id", "business_id", "email", "password_hash", "role", "status", "full_name"),
    ),
    (
        "notification_requests",
        ("business_id", "status", "scheduled_for", "attempts", "max_attempts"),
    ),
    ("mp_subscriptions", ("business_id", "preapproval_id")),
    ("booking_payments", ("business_id", "payment_id", "external_reference")),
)

#: Firmas de las funciones que deben correr como el definer. Son las que resuelven
#: algo *antes* de que exista un tenant: login y refresh de negocio, qué negocios
#: tienen recordatorios vencidos, y a qué negocio pertenece un evento de MP.
FUNCIONES: tuple[str, ...] = (
    "auth_business_user_for_login(citext)",
    "auth_business_user_by_id(uuid)",
    "businesses_with_pending_notifications(integer)",
    "mercadopago_business_for_event(text, text)",
)


def _es_multirol() -> bool:
    """`True` si el definer ya existe con `BYPASSRLS` (diseño multi-rol)."""
    bind = op.get_bind()
    fila = bind.execute(
        sa.text("SELECT rolbypassrls FROM pg_roles WHERE rolname = :r"),
        {"r": DEFINER_ROLE},
    ).first()
    return bool(fila and fila[0])


def _sentencias_single_role(app_role: str) -> list[str]:
    """Sentencias del fix single-rol, en orden. Extraído para poder validarlo."""
    rol = DEFINER_ROLE
    sentencias: list[str] = [
        # El rol definer sin BYPASSRLS: no puede crearse con el atributo, pero
        # tampoco hace falta. `NOLOGIN`/`NOINHERIT` como el definer multi-rol.
        f"""
        DO $do$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{rol}') THEN
                CREATE ROLE "{rol}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
            END IF;
        END
        $do$;
        """,
        # `SET` (no `INHERIT`) para poder transferirle la propiedad de las
        # funciones: `ALTER FUNCTION ... OWNER TO` exige poder hacer `SET ROLE`.
        f'GRANT "{rol}" TO CURRENT_USER WITH SET TRUE, INHERIT FALSE',
        f'GRANT USAGE ON SCHEMA public TO "{rol}"',
    ]
    for tabla, columnas in LECTURAS:
        sentencias.append(f'DROP POLICY IF EXISTS "{tabla}_login_definer" ON "{tabla}"')
        # `FOR SELECT ... USING (true)`: solo lectura y solo para el definer. La
        # política de tenant sigue vigente para todos los demás roles.
        sentencias.append(
            f'CREATE POLICY "{tabla}_login_definer" ON "{tabla}" FOR SELECT TO "{rol}" USING (true)'
        )
        lista = ", ".join(f'"{c}"' for c in columnas)
        sentencias.append(f'GRANT SELECT ({lista}) ON "{tabla}" TO "{rol}"')
    # En single-rol los `GRANT EXECUTE ... TO tempus_app` de 0009/0017/0019 eran
    # no-ops (ese rol no existe) y algunos cuerpos revocaron `PUBLIC`, así que sin
    # esto la app no podría *llamar* a la función.
    #
    # El `GRANT` va **después** de transferir la propiedad y entrando al definer:
    # mientras la app es la dueña, `GRANT ... TO <dueño>` no deja entrada en la
    # ACL, y al transferir la propiedad la app pierde el `EXECUTE` implícito.
    # `ALTER ... OWNER TO` exige además que el dueño nuevo tenga `CREATE` en el
    # esquema; se otorga para la transferencia y se revoca enseguida.
    sentencias.append(f'GRANT CREATE ON SCHEMA public TO "{rol}"')
    for firma in FUNCIONES:
        sentencias.append(f'ALTER FUNCTION {firma} OWNER TO "{rol}"')
    sentencias.append(f'REVOKE CREATE ON SCHEMA public FROM "{rol}"')
    sentencias.append(f'SET ROLE "{rol}"')
    for firma in FUNCIONES:
        sentencias.append(f'GRANT EXECUTE ON FUNCTION {firma} TO "{app_role}"')
    sentencias.append("RESET ROLE")
    return sentencias


def upgrade() -> None:
    if _es_multirol():
        print(f"[0022] '{DEFINER_ROLE}' ya tiene BYPASSRLS (multi-rol): no-op")
        return
    app_role = str(op.get_bind().execute(sa.text("SELECT current_user")).scalar_one())
    for sentencia in _sentencias_single_role(app_role):
        op.execute(sentencia)


def downgrade() -> None:
    """No hay un estado anterior seguro: el rol sin definer **era** el bug.

    Revertir dejaría otra vez las funciones `SECURITY DEFINER` sujetas a `FORCE
    RLS`, o sea login roto. Es un piso duro, como `0003_auth_roles` y `0021`.
    """
