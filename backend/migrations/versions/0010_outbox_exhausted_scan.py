"""Que la barredora de agotados tenga negocios que visitar.

Revision ID: 0010_outbox_exhausted_scan
Revises: 0009_outbox_tenant_scan
Create Date: 2026-10-01

**El agujero.** `businesses_with_pending_notifications` filtraba por
`attempts < max_attempts`. Eso es lo correcto para *elegir trabajo que se puede
mandar*, pero dejaba sin visitar el negocio justamente en el caso que hay que
visitar: una notificacion que agoto sus intentos y sigue en `pending`.

El filtro del claim de `app.workers.outbox` tambien dice `attempts < max_attempts`,
lo cual significa que esa fila:

- no es reclamable, asi que el dreno no la toma;
- no es terminal, asi que no se reporta como perdida;
- y el negocio ya no aparece en la lista de visita, asi que nadie la mira.

Se queda `pending` para siempre. El sintoma es la peor forma posible: el sistema
enuncia recordatorios, la base los muestra como pendientes, y no hay ni un error en
ningun lado que diga que uno se perdio. Es un fallo silencioso por construccion.

**El arreglo.** Se saca el filtro de intentos de la funcion. La funcion pasa a
responder "que negocios tienen algo vencido que resolver", que es la pregunta
correcta, y no "que negocios tienen un envio que intentar", que es una pregunta mas
estrecha que deja trabajo sin hacer. El filtro de intentos sigue donde tiene que
estar: en el `UPDATE` del claim, que es el unico momento en que la pregunta "esto se
puede mandar?" importa de verdad.

La consecuencia es que la funcion puede devolver un negocio que solo tiene trabajo
agotado. No es un problema: la barredora lo resuelve en su primera sentencia y el
claim no encuentra nada. Un `SELECT` de mas por tick, a cambio de que ninguna fila
quede olvidada.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_outbox_exhausted_scan"
down_revision: str | None = "0009_outbox_tenant_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFINER_ROLE = "tempus_login_definer"
APP_ROLE = "tempus_app"
OWNER_ROLE = "tempus_owner"

FUNCTION = "businesses_with_pending_notifications"
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
            ORDER BY 1
            LIMIT GREATEST(p_limit, 1)
        $$;
        """
    )
    op.execute(f'GRANT "{DEFINER_ROLE}" TO {OWNER_ROLE}')
    op.execute(f'ALTER FUNCTION {FUNCTION}(integer) OWNER TO "{DEFINER_ROLE}"')
    # `CREATE OR REPLACE FUNCTION` conserva los privilegios del objeto anterior, asi
    # que los GRANT de `0009` siguen vigentes. Se repiten igual, de forma explicita y
    # en la misma transaccion, para que esta migracion sea legible por si sola.
    op.execute(f'GRANT EXECUTE ON FUNCTION {FUNCTION}(integer) TO "{DEFINER_ROLE}"')
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION}(integer) TO {APP_ROLE}")


def downgrade() -> None:
    """Vuelve al filtro por intentos, que es el comportamiento con el stall."""
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
