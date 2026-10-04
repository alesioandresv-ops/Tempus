"""Rol dedicado para login pre-tenant (BYPASSRLS minimo)

Revision ID: 0005_login_definer_role
Revises: 0004_login_functions
Create Date: 2026-09-29

## Por que la transferencia de dueno necesita un puente

La funcion de `0004`--`auth_business_user_for_login`-- nace duena de `tempus_owner`,
que **no** puede correr con RLS: el login ocurre antes de que exista un tenant, asi
que no hay GUC que poner y la politica de `business_users` no deja pasar ninguna fila.
Por eso el dueno tiene que ser `tempus_login_definer`, el rol BYPASSRLS minimo.

PostgreSQL pone dos condiciones para `ALTER FUNCTION ... OWNER TO tempus_login_definer`:

1. Poder hacer `SET ROLE tempus_login_definer`, o sea **ser miembro** del rol destino.
   `tempus_owner` no lo es--deliberadamente, y
   `tests/tenancy/test_login_definer.py::test_tempus_owner_no_es_miembro_del_definer`
   lo verifica-- asi que la membresia se otorga y se revoca dentro de esta misma
   transaccion, a traves de `tempus_migrator`: el rol puente que crea
   `infra/postgres/init/02-login-definer.sql`, unico rol con `ADMIN OPTION` sobre el
   definer, y al que `tempus_owner` pertenece **sin** `ADMIN OPTION`. Por eso existe
   en el init y no en una migracion: para llegar a el haria falta `CREATE ROLE`, que
   `tempus_owner`--`NOSUPERUSER`-- no puede hacer. Las cuatro piezas estan en
   `migrations/roles_definer.py` para que la garantia se lea en un solo lugar.
2. Que el **dueno nuevo** tenga `CREATE` en el esquema. Por eso `CREATE` se otorga
   antes de transferir y se devuelve despues: es lo que permite que PostgreSQL borre
   y recree la funcion bajo el dueno nuevo.

Ambas cosas son de la sesion, no del esquema, asi que al terminar la migracion no
queda membresia permanente ni `CREATE` para un rol BYPASSRLS.

## Lo que se mantiene igual

El `GRANT` de columnas sigue siendo por columna y no sobre la tabla. El rol tiene
BYPASSRLS, asi que darle `SELECT` sobre `business_users` entera lo convertiria en el
lector global de contrasenas y roles de los tenants: con `SELECT` solo sobre las
seis columnas que el cuerpo consulta, lo maximo que se puede exfiltrar es "este email
existe, tiene este hash y esta bloqueado".
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from migrations.roles_definer import (
    definer_con_create,
    definer_sin_create,
    membresia_temporal_al_definer,
    revoca_membresia_al_definer,
)

revision: str = "0005_login_definer_role"
down_revision: str | None = "0004_login_functions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tempus_app"
DEFINER_ROLE = "tempus_login_definer"
BUSINESS_LOGIN = "auth_business_user_for_login"

#: Columnas que el cuerpo de la funcion lee. Es el maximo de lo que el rol BYPASSRLS
#: puede tocar de esta tabla, y por eso estan una por una.
COLUMNAS_LOGIN = "id, business_id, password_hash, role, status, full_name"


def upgrade() -> None:
    op.execute(f'GRANT SELECT ({COLUMNAS_LOGIN}) ON business_users TO "{DEFINER_ROLE}"')

    # Las tres piezas de la transferencia, en el orden en que hacen falta: primero la
    # membresia (para poder hacer `SET ROLE`), despues el `CREATE` del dueno nuevo
    # (para que la funcion se pueda recrear), y la transferencia en el medio.
    membresia_temporal_al_definer()
    definer_con_create()
    op.execute(f'ALTER FUNCTION {BUSINESS_LOGIN}(citext) OWNER TO "{DEFINER_ROLE}"')
    definer_sin_create()
    revoca_membresia_al_definer()


def downgrade() -> None:
    # Al reves: la membresia y el `CREATE` hacen falta para **devolver** el dueno,
    # porque cambiar de dueno hacia atras tiene las mismas dos condiciones.
    membresia_temporal_al_definer()
    definer_con_create()
    op.execute(f"ALTER FUNCTION {BUSINESS_LOGIN}(citext) OWNER TO tempus_owner")
    definer_sin_create()
    revoca_membresia_al_definer()

    op.execute(f'REVOKE SELECT ({COLUMNAS_LOGIN}) ON business_users FROM "{DEFINER_ROLE}"')
