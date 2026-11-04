"""Snapshot del nombre del cliente en la reserva.

**El bug.** `bookings` guardaba solo `customer_id` y todo el sistema leia el nombre
desde `customers`. Pero `customers` es unico por `(business_id, phone_e164)`: si el
mismo telefono se usaba antes con otro nombre --otra persona de la familia, una
reserva vieja, o un nombre mal tipeado-- la reserva nueva mostraba el nombre viejo.
El nombre que el cliente tipeo al reservar se descartaba.

**La salida.** Cada reserva guarda el nombre que se tipeo en ese momento. El
registro de `customers` sigue siendo la ficha viva del cliente (y se actualiza al
ultimo nombre usado), pero la reserva ya no depende de el para mostrarse.

Las columnas nacen **nullable** a proposito: las filas que ya existen se rellenan
con el nombre del cliente en esta misma migracion, y los inserts de tests que no
las conocen siguen funcionando. Las lecturas usan
`COALESCE(bookings.customer_first_name, customers.first_name)`.

**La RLS se respeta, no se esquiva.** Las tablas de tenant corren con
`FORCE ROW LEVEL SECURITY` y el rol de migraciones es el dueño: sin el GUC la
politica devuelve cero filas y el backfill no actualizaria nada. Por eso se recorre
negocio por negocio con `set_config(..., true)`, que es `SET LOCAL` y muere con la
transaccion de la migracion. Es el mismo patron de `0026`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_snapshot_nombre_cliente"
down_revision: str | None = "0031_recrear_funcion_reset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BACKFILL = """
DO $backfill$
DECLARE
    v_negocio uuid;
BEGIN
    FOR v_negocio IN SELECT id FROM businesses LOOP
        PERFORM set_config('app.current_business_id', v_negocio::text, true);

        UPDATE bookings b
           SET customer_first_name = c.first_name,
               customer_last_name = c.last_name
          FROM customers c
         WHERE c.id = b.customer_id
           AND c.business_id = b.business_id
           AND b.business_id = v_negocio
           AND b.customer_first_name IS NULL;
    END LOOP;

    -- Dejar el GUC como estaba: vacio significa "sin tenant" (deny all).
    PERFORM set_config('app.current_business_id', '', true);
END
$backfill$;
"""


def upgrade() -> None:
    op.add_column("bookings", sa.Column("customer_first_name", sa.Text(), nullable=True))
    op.add_column("bookings", sa.Column("customer_last_name", sa.Text(), nullable=True))
    op.execute(_BACKFILL)


def downgrade() -> None:
    op.drop_column("bookings", "customer_last_name")
    op.drop_column("bookings", "customer_first_name")
