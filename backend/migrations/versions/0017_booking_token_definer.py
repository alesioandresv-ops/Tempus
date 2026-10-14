"""Resolver el token de reserva con el rol definer (BYPASSRLS)

`booking_tenant_for_token` (0006) es una funcion de **pre-tenant**: corre antes
de que exista el GUC, y su trabajo es justamente resolver el `business_id` a
partir del hash. Por eso `0006` ya la creo `SECURITY DEFINER` --sin eso no habia
forma de leer `bookings` antes de conocer el tenant-- pero **no le cambio el
dueno**: quedo en el rol que ejecuta la migracion, `tempus_owner`.

Y `tempus_owner` no escapa a la RLS: `bookings` tiene `FORCE ROW LEVEL
SECURITY` (0011), la politica `bookings_tenant_isolation` compara contra el GUC,
y sin GUC devuelve cero filas. El resultado es que en runtime la funcion resuelve
nada, y `GET /public/bookings/{token}`, `/cancel` y `/reschedule` responden 404
"Reserva no encontrada" a una reserva que existe--el mismo circulo del login que
`0004`/`0005` resolvieron con el rol dedicado.

La suite no lo veia por dos razones: los tests de gestion por token ejercitan el
servicio (`cancel_booking`, `reschedule_booking`, `get_by_secure_token`) con la
sesion del test--que ya corre con el GUC de la transaccion compartida-- y ninguno
pasa por la funcion real. El camino HTTP con la funcion quedo sin cobertura, y
la demo al desplegarla devolvio el 404.

## Lo que cambia

1. **Dueno de la funcion** pasa a `tempus_login_definer` (el rol BYPASSRLS
   minimo, creacion de `0005`). Mismo puente de membresia que `0008` y `0009`:
   la membresia se otorga y se revoca dentro de esta misma transaccion, y el
   `CREATE` en el esquema--que el dueno nuevo necesita para que PostgreSQL
   borre y recree la funcion-- tambien es transitorio.
2. **Grant columnar** al definer sobre `bookings`: solo `id`, `business_id` y
   `secure_token_hash`, las tres columnas que lee el cuerpo de la funcion. Nada
   mas: el rol BYPASSRLS debe ver el minimo necesario para resolver el tenant, y
   un `SELECT` sobre la tabla entera lo convertiria en un lector global de
   reservas (ver `0008` y `0009`).
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

revision: str = "0017_booking_token_definer"
down_revision: str | None = "0016_fase4_whatsapp_avatar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFINER_ROLE = "tempus_login_definer"
OWNER_ROLE = "tempus_owner"
BOOKING_TENANT = "booking_tenant_for_token"
#: Las columnas que lee el cuerpo de la funcion:
#: `SELECT b.business_id, b.id FROM bookings b WHERE b.secure_token_hash = ...`.
COLUMNAS = ("id", "business_id", "secure_token_hash")


def upgrade() -> None:
    cols = ", ".join(COLUMNAS)

    op.execute(f'GRANT SELECT ({cols}) ON bookings TO "{DEFINER_ROLE}"')

    membresia_temporal_al_definer()
    definer_con_create()
    op.execute(f'ALTER FUNCTION {BOOKING_TENANT}(bytea) OWNER TO "{DEFINER_ROLE}"')
    definer_sin_create()
    revoca_membresia_al_definer()


def downgrade() -> None:
    membresia_temporal_al_definer()
    definer_con_create()
    op.execute(f"ALTER FUNCTION {BOOKING_TENANT}(bytea) OWNER TO {OWNER_ROLE}")
    definer_sin_create()
    revoca_membresia_al_definer()

    cols = ", ".join(COLUMNAS)
    op.execute(f'REVOKE SELECT ({cols}) ON bookings FROM "{DEFINER_ROLE}"')
