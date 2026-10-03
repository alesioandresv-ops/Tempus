"""Resolver una reserva por su secure token, sin conocer el tenant

Revision ID: 0006_booking_token_lookup
Revises: 0005_login_definer_role
Create Date: 2026-10-01

**El problema que resuelve.** La gestion de una reserva (ver, cancelar,
reprogramar) se direcciona por `/r/{token}` y **no** por `booking_id`. Eso es
deliberado: `ARCHITECTURE.md` §11 dice que el cliente nunca ve un id numerico,
porque un id adivinable convierte "ver mi turno" en "ver los turnos de todos".

Pero `bookings` tiene RLS con la politica habitual,
`business_id = NULLIF(current_setting('app.current_business_id', true), '')::uuid`.
Para leer la reserva hace falta el `business_id`. Para poner el `business_id` en
el GUC hace falta la reserva. Es el mismo circulo que resolvio `0004` para el
login, y se resuelve con la misma tecnica.

Y no sale de lado: el token **no** contiene el `business_id`, y no deberia.
Meterlo significaria que el token deja de ser un secreto opaco y pasa a ser un
portador de datos, y ademas habria que elegir un formato para el token que hoy
es `secrets.token_urlsafe(32)` y que la arquitectura fija como tal.

**Por que una funcion `SECURITY DEFINER` y no las otras dos salidas.** Las
mismas que descarta `0004`, y por el mismo motivo:

- `BYPASSRLS` para el rol de la app: apaga el aislamiento entero.
- `ALTER TABLE ... DISABLE ROW LEVEL SECURITY` alrededor del SELECT: deja una
  ventana entre el SELECT y el `SET LOCAL` en la que cualquier consulta
  concurrente ve todas las reservas de todos los tenants.

**Lo que hace que la funcion no sea un agujero.** Las mismas cuatro guarantias
de `0004`, y las cuatro importan:

1. `SET search_path = pg_catalog, public`, sin `pg_temp`.
2. `REVOKE ALL ... FROM PUBLIC`. Por defecto PostgreSQL da `EXECUTE` a `PUBLIC`
   sobre toda funcion nueva, y eso permitiria enumerar reservas de todos los
   negocios probando hashes.
3. Columnas explicitas en el `RETURNS TABLE`. La superficie de la funcion es su
   propia lista de columnas: agregar una columna a `bookings` no la agrega a la
   funcion, y en particular **nunca** agrega el token en claro.
4. `GRANT EXECUTE` solo a `tempus_app`.

**Lo que la funcion NO devuelve.** Solo `business_id` y `id`. El token en claro
no se persiste en ningun lado, asi que la funcion no puede devolverlo. Quien
llama recibe el identificador del negocio, abre su sesion con tenant, y recien ahi
lee la reserva -- con la RLS aplicandose de verdad al resto de la consulta.

**Por que no devuelve tambien el negocio directamente.** Podria. Se separa en dos
llamadas para que el GUC lo ponga el codigo de la aplicacion y no la funcion: si
la funcion pusiera el GUC ella misma, la sesion quedaria con tenant despues de
que la funcion terminara, y eso es exactamente el leakage que `SET LOCAL` evita.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_booking_token_lookup"
down_revision: str | None = "0005_login_definer_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tempus_app"

BOOKING_TENANT = "booking_tenant_for_token"
SEARCH_PATH = "pg_catalog, public"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {BOOKING_TENANT}(p_token_hash bytea)
        RETURNS TABLE (
            business_id uuid,
            booking_id uuid
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = {SEARCH_PATH}
        AS $$
            SELECT b.business_id, b.id
            FROM bookings b
            WHERE b.secure_token_hash = p_token_hash
        $$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {BOOKING_TENANT}(bytea) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {BOOKING_TENANT}(bytea) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {BOOKING_TENANT}(bytea)")
