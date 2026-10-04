"""Funcion de soporte para el dreno de la outbox: que negocios tienen trabajo.

Revision ID: 0009_outbox_tenant_scan
Revises: 0008_login_definer_email
Create Date: 2026-10-01

**El problema.** `notification_requests` tiene RLS con `business_id = current_setting(...)`
y FORCE ROW LEVEL SECURITY. El worker que drena la outbox es cross-tenant por
naturaleza -- recorre todos los negocios del SaaS -- asi que no puede poner un GUC de
tenant y por lo tanto no ve **ninguna** fila. Sin esta funcion el dreno devolveria
siempre cero, y el sintoma seria un sistema que encola recordatorios, los muestra
pendientes en la base, y nunca manda ninguno.

Las dos salidas obvias rechazan el problema:

1. **Darle BYPASSRLS al rol de la app.** Es lo mas corto y lo que menos conviene:
   el rol que atiende peticiones HTTP publicas pasaria a poder leer las filas de
   todos los tenants. Un `WHERE` olvidado en un endpoint deja de ser un bug de
   aislamiento y pasa a ser un bug invisible.

2. **Recorrer todos los negocios con `tenant_session()` sin saber cuales tienen
   trabajo.** No weakens nada, pero abre una transaccion por negocio en cada tick,
   para todos los negocios, aunque no tengan nada pendiente. Con 30s de tick y
   cientos de negocios eso es una carga constante y vacia.

Esta migracion elige una tercera: una funcion `SECURITY DEFINER` que devuelve
**unicamente los `business_id` que tienen notificaciones vencidas**, y nada mas.

Por que "unicamente ids" es la parte importante del acuerdo: la funcion no
devuelve datos de clientes, ni telefonos, ni mensajes. Devuelve el mismo dato que
la sesion HTTP recibe del claim `tid` del access token, y a partir de ahi el worker
usa `tenant_session()` y queda RLS como la unica puerta. El unico priviliegio que
se agrega es *saber a quien hay que visitar*, no *leer sus datos*.

Consecuencia que hay que tener presente: quien puede llamar esta funcion puede
enumerar los negocios con notificaciones pendientes. Es el mismo dato que ya es
publico por el slug, asi que no expone nada nuevo. Lo que **no** se hace es
devolver filas de clientes: esa sigue siendo la unica forma de mandar un mensaje.
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

revision: str = "0009_outbox_tenant_scan"
down_revision: str | None = "0008_login_definer_email"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFINER_ROLE = "tempus_login_definer"
APP_ROLE = "tempus_app"
OWNER_ROLE = "tempus_owner"

FUNCTION = "businesses_with_pending_notifications"

#: Techo de negocios por tick. Es un limite de trabajo, no de correctitud: lo que
#: queda fuera se toma en el tick siguiente. Sin tope, un dia que se acumulen
#: notificaciones vencidas podria abrir miles de transacciones de una vez y agotar
#: el pool.
DEFAULT_LIMIT = 50


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {FUNCTION}(p_limit integer DEFAULT {DEFAULT_LIMIT})
        RETURNS TABLE (business_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT DISTINCT n.business_id
            FROM notification_requests n
            WHERE n.status IN ('pending', 'failed')
              AND n.scheduled_for <= now()
              AND n.attempts < n.max_attempts
            ORDER BY 1
            LIMIT GREATEST(p_limit, 1)
        $$;
        """
    )
    # El dueno tiene que ser el rol BYPASSRLS, y por eso va por el puente: `tempus_owner`
    # no es miembro del definer, y `ALTER ... OWNER TO` ademas exige que el dueno
    # nuevo tenga `CREATE` en el esquema. Las dos piezas--la membresia y el `CREATE`--
    # se dan y se devuelven dentro de esta misma transaccion.
    membresia_temporal_al_definer()
    definer_con_create()
    op.execute(f'ALTER FUNCTION {FUNCTION}(integer) OWNER TO "{DEFINER_ROLE}"')
    definer_sin_create()
    revoca_membresia_al_definer()

    # `SELECT` por columna, igual que en `0005`, y por la misma razon: el rol tiene
    # BYPASSRLS, y darle `SELECT` sobre la tabla entera lo convertiria en elReader
    # global de datos de clientes. Lo unico que necesita son las cinco columnas que
    # el filtro del cuerpo toca.
    #
    # Ojo con el conteo: se cuentan las columnas que **toca la consulta**, no las que
    # devuelve. El cuerpo lee `business_id` en el `SELECT` y las otras cuatro en el
    # `WHERE`; el `WHERE` tambien necesita privilegio, que es el mismo error de
    # `0008` y la razon por la que aqui se listan las cinco explicitamente.
    #
    # Sin este GRANT la funcion se crea sin error y falla en el primer SELECT con
    # "permiso denegado a la tabla notification_requests", o sea un 500 en el tick
    # que parece un bug de la funcion y no del permiso.
    op.execute(
        f"GRANT SELECT (business_id, status, scheduled_for, attempts, max_attempts) "
        f'ON notification_requests TO "{DEFINER_ROLE}"'
    )

    # El permiso va a los **dos** roles y por motivos distintos:
    #  - al definer, porque es quien ejecuta el cuerpo y necesita leer la tabla
    #    notwithstanding la RLS;
    #  - a `tempus_app`, porque es quien la invoca y sin EXECUTE el tick falla con
    #    un 500 por privilegio. Confundir estas dos cosas es el error clasico: un
    #    GRANT al unico rol y el sintoma es "la funcion existe pero no puedo llamarla".
    op.execute(f'GRANT EXECUTE ON FUNCTION {FUNCTION}(integer) TO "{DEFINER_ROLE}"')
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION}(integer) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}(integer)")
