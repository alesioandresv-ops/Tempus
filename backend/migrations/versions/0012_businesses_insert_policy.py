"""politica de INSERT en `businesses` para el rol de onboarding

Revision ID: 0012_businesses_insert_policy
Revises: 0011_businesses_rls_write
Create Date: 2026-10-02

Devuelve al rol de DDL la capacidad de crear negocios, y solo a el.

## Que se rompio

`0011` le puso `FORCE ROW LEVEL SECURITY` a `businesses` y creo dos politicas:
una de `SELECT` con `USING (true)` y una de `UPDATE` atada al GUC. **No creo
politica de `INSERT` ni de `DELETE`, y no hacia falta en su momento: nadie
insertaba un negocio desde el codigo de aplicacion.** El alta de tenants--la unica
operacion del producto que necesita mas privilegios que operar dentro de un
tenant-- vive en un modulo aparte y corre con la sesion de migraciones
(`app.modules.businesses.service`: "crear un negocio: sesion de migraciones").

El problema es que `FORCE ROW LEVEL SECURITY` alcanza tambien al dueno de la
tabla. `tempus_owner` es dueno, asi que le aplica: sin politica de `INSERT`, su
`INSERT INTO businesses` no falla por permiso--falla por politica--y el resultado
es

    InsufficientPrivilegeError: permiso denegado a la tabla businesses

O sea, **el camino de onboarding dejo de funcionar y nadie lo noto**, porque los
tests que lo ejercitan se saltan en silencio cuando `TEST_DATABASE_URL` no esta
definido, y la siembra de `tests/conftest.py`--que inserta dos negocios--es justo
uno de esos tests. Estos 167 tests, que se saltaban en silencio, eran la unica cobertura
del alta de tenants, y el primer indicio de que faltaba una politica fue
conectarlos.

## Por que una politica y no `BYPASSRLS`

Dar `BYPASSRLS` al rol owner resolveria el INSERT en una linea, y es lo que
`tempus_login_definer` ya tiene--con buen motivo, porque su trabajo es leer
`business_users` sin GUC. Pero seria el error equivocado por dos razones:

1. `BYPASSRLS` es todo o nada. El rol owner tambien siembra las tablas con
   `business_id` en los tests de tenancy, y las politicas de esas tablas son las
   que se estan probando: con `BYPASSRLS` los tests de aislamiento pasarian
   probando de menos, porque ninguna politica se evaluaria. El conftest lo dice
   explicitamente y tiene razon.
2. El permiso que hace falta es "crear un tenant", no "ver todos los tenants".
   `INSERT` con `WITH CHECK (true)` no concede lectura: sin la politica
   `businesses_read`--que si existe-- el mismo `INSERT ... RETURNING` no puede
   devolver la fila creada.

## Por que `TO tempus_owner`

La politica es de `INSERT` y es **exclusiva del rol de onboarding**. `tempus_app`
no entra: `GRANT` no le da permiso de tabla sobre `businesses`, y sin politica que
le aplique sigue sin poder insertar. Lo que se agrega es una garantia--que ahora es
comprobable--y no una excepcion.

## Por que `WITH CHECK (true)` y no una comparacion

`INSERT` no se puede restringir por fila de forma util aca: la fila que se crea es
la del negocio nuevo, y su `id` todavia no es el de nadie. La separacion real esta
en el rol al que aplica la politica, no en el `WITH CHECK`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_businesses_insert_policy"
down_revision: str | None = "0011_businesses_rls_write"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_GUC = "app.current_business_id"
_ROL_ONBOARDING = "tempus_owner"


def upgrade() -> None:
    # `WITH CHECK (true)` a proposito: no hay fila previa a la que comparar. La
    # restriccion que si importa--"solo el rol de onboarding"-- la hace el `TO`.
    op.execute(
        f"CREATE POLICY businesses_insert ON businesses "
        f"FOR INSERT TO {_ROL_ONBOARDING} WITH CHECK (true)"
    )

    # El permiso de tabla sigue faltandole a `tempus_app`, y la migracion 0011
    # tambien se encargo de dejarlo con UPDATE por columna. Este bloque no otorga
    # INSERT a nadie: solo le devuelve al rol de DDL la capacidad de escribir una
    # tabla que ya es duena.
    #
    # Se hace explicito el REVOKE para que un despliegue donde alguien haya
    # ejecutado `GRANT INSERT ON businesses TO tempus_app` a mano no se quede con
    # un permiso que nadie reviso.
    op.execute("REVOKE INSERT ON businesses FROM tempus_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS businesses_insert ON businesses")
    op.execute("GRANT INSERT ON businesses TO tempus_owner")


__all__ = ["downgrade", "upgrade"]
