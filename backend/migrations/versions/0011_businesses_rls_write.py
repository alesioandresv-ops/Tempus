"""RLS y permiso de escritura en `businesses`.

`businesses` era la unica tabla del tenant **sin** RLS, y la unica sobre la que
`tempus_app` no podia escribir. Las dos cosas tenian una razon: es la tabla global que
identifica al negocio, y el flujo publico la necesita leer por `slug` **antes** de
saber cual es el tenant. Resolver el negocio es el paso uno de una sesion sin GUC.

Eso resuelve la lectura, pero no la escritura, y a partir de que el panel existe
(escriba) el resultado es que **editar la configuracion del negocio es imposible**
con los privilegios de la aplicacion. Esta migracion cierra ese hueco sin tocar el
diseno de las dos fases.

## Lo que cambia

**RLS con dos politicas en vez de una.** `businesses` no tiene columna
`business_id` --se identifica por `id`-- asi que la comparacion es contra el mismo
GUC que usa el resto:

- `SELECT ... USING (true)`: leer el negocio por slug tiene que funcionar sin GUC,
  porque es justamente lo que se hace antes de conocer el tenant. Es lo que ya hacia
  `session_scope()`.
- `UPDATE ... USING (... = id::text) WITH CHECK (... = id::text)`: escribir solo
  sobre la fila del GUC. El `WITH CHECK` no es redundante con el `USING`: sin el,
  un `UPDATE` podria mover el `id` de la fila y dejar al tenant escribiendo en
  otra fila en la misma sentencia.

**Permiso de `UPDATE` por columna.** `GRANT UPDATE (name, description, ...)` en vez
de `GRANT UPDATE ON businesses`. Es lo que hace que el permiso del rol de la
aplicacion coincida con la lista blanca de `EDITABLE_FIELDS` del servicio, en vez de
ser mas amplio que ella. Si mañana alguien agrega una columna al modelo y la
incluye en la lista blanca, el permiso falta y el error es explicito; con un
`GRANT UPDATE` de tabla completa, la columna nueva queda editable de inmediato y
nadie lo decide.

`updated_at` entra en el permiso aunque el codigo no la escriba nunca, porque la
escribe el trigger `trg_businesses_set_updated_at` y `set_config`-free: sin el
privilegio, el trigger falla y el `UPDATE` entero se cae.

**Lo que NO cambia.** `tempus_app` sigue sin `INSERT` ni `DELETE` sobre
`businesses`: crear un negocio es la operacion de onboarding y sigue siendo
privilegiada (ver el router de onboarding). Esta migracion no la habilita, y no
deberia: alta de tenants y operacion dentro de un tenant son permisos distintos a
proposito.

## Por que `FORCE ROW LEVEL SECURITY`

Igual que en la migracion 0002. Sin `FORCE`, el dueño de la tabla (y cualquier rol
que sea miembro de un rol con `BYPASSRLS`) la esquivaria, y las politicas dejarian
de ser la garantia para las conexiones que se abren con la URL de la aplicacion.
Con `FORCE` la politica aplica a todos salvo a quien tiene `BYPASSRLS` explicito,
que es el caso de `tempus_login_definer` y de proposito.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

#: revision identifiers
revision: str = "0011_businesses_rls_write"
down_revision: str | None = "0010_outbox_exhausted_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_GUC = "app.current_business_id"

#: Columnas que el panel puede editar. Tiene que coincidir con
#: `EDITABLE_FIELDS` de `app.modules.businesses.service`. El comentario lo dice, pero
#: la unica garantia de que sigan coincideindo es que las dos listas esten juntas:
#: si divergen, el servicio manda un campo que la base no deja escribir y el error
#: es un 500, no un 422.
_EDITABLES = (
    "name",
    "description",
    "timezone",
    "locale",
    "currency",
    "phone_e164",
    "address_text",
    "brand_color",
    "slot_interval_minutes",
    "min_lead_minutes",
    "max_advance_days",
    "cancellation_window_minutes",
    "slug",
    # El trigger `trg_businesses_set_updated_at` la escribe en cada UPDATE. Sin el
    # privilegio sobre la columna, el UPDATE se cae con "permission denied" desde
    # adentro del trigger, que es un 500 sin mensaje util.
    "updated_at",
)


def upgrade() -> None:
    op.execute("ALTER TABLE businesses ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE businesses FORCE ROW LEVEL SECURITY")

    # `SELECT` sin GUC. Sin esta politica, la migracion 0002 no aplica nada a esta
    # tabla y el flujo publico --que resuelve el negocio por slug en una sesion sin
    # tenant-- se queda sin filas.
    #
    # El `FOR SELECT` es **obligatorio** y no una formality. Sin el, la politica
    # aplica a todos los comandos, y como las politicas permisivas de PostgreSQL se
    # combinan con OR, `businesses_read` con `USING (true)` haria que
    # `businesses_write` no restringiera nada: cualquier UPDATE pasaria primero por
    # el filtro de lectura y podria tocar la fila de otro tenant. Separar por comando
    # es lo que hace que "leer sin tenant" y "escribir con tenant" puedan tener
    # reglas distintas, que es justamente el punto de esta migracion.
    op.execute("CREATE POLICY businesses_read ON businesses FOR SELECT USING (true)")

    # `businesses` no tiene `business_id`: se identifica por `id`. La comparacion es
    # el GUC contra el `id`, con el mismo `NULLIF(..., '')` que usa el resto del
    # proyecto para que un GUC ausente no caste a uuid y reviente.
    op.execute(
        f"CREATE POLICY businesses_write ON businesses FOR UPDATE "
        f"USING (id = NULLIF(current_setting('{_TENANT_GUC}', true), '')::uuid) "
        f"WITH CHECK (id = NULLIF(current_setting('{_TENANT_GUC}', true), '')::uuid)"
    )

    columnas = ", ".join(_EDITABLES)
    op.execute(f"GRANT UPDATE ({columnas}) ON businesses TO tempus_app")


def downgrade() -> None:
    op.execute("REVOKE UPDATE ON businesses FROM tempus_app")
    op.execute("DROP POLICY businesses_write ON businesses")
    op.execute("DROP POLICY businesses_read ON businesses")
    op.execute("ALTER TABLE businesses NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE businesses DISABLE ROW LEVEL SECURITY")


__all__ = ["downgrade", "upgrade"]
