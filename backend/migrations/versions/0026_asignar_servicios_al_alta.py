"""un servicio que nadie puede hacer se asigna: el link publico no se queda vacio

Un negocio puede tener servicios, profesionales y horarios impecables y una pagina
publica **sin un solo horario**: pasa cuando `professional_services`--la tabla que
dice quien pueden hacer que--esta vacia. Le paso a dos negocios reales. El motivo no
es un bug de cuentas: asignar un servicio a un profesional es un paso del panel
distinto del alta, y nadie lo hace si no sabe que existe.

Desde esta version el alta asigna por defecto (`crear_servicio` y
`crear_profesional` enlazan con todos los del negocio), asi que el paso manual queda
para **destildar** al que no hace un servicio. Esta migracion es la otra mitad: los
negocios que ya estan asi, hoy, en produccion.

**El criterio es "servicio ofrecido que no tiene a nadie", no "todo con todo".** Se
mira cada servicio y se repara solo el que no tiene ningun profesional elegible.
Asi un negocio que ya configuro sus asignaciones a mano no se toca--sus servicios
tienen gente y no se tocan--, y uno que quedo vacio se arregla entero. Reparar de
mas rompe una decision tomada; reparar de menos deja el link publico roto, que es el
fallo que esta migracion existe para cerrar.

La elegibilidad se evalua con la regla del codigo (ADR-0018): profesional del mismo
negocio, `is_active`, no archivado, y vinculo activo. Por eso el `NOT EXISTS` mira
`p2.is_active` y `p2.archived_at`: un servicio cuyo unico profesional esta
desactivado **si** se repara, porque hoy no lo puede atender nadie.

**La RLS se respeta, no se esquiva.** Las tablas de tenant corren con
`FORCE ROW LEVEL SECURITY` y el rol de migraciones es el dueño: sin el GUC la
politica devuelve cero filas--y un `INSERT ... SELECT` sobre cero filas no inserta
nada--. Por eso se recorre negocio por negocio con `set_config(..., true)`, que es
`SET LOCAL` y muere con la transaccion de la migracion.

`ON CONFLICT DO NOTHING` sobre la PK `(professional_id, service_id)` hace la
migracion idempotente: si se corre dos veces (o si el alta automatica ya creo el
vinculo), no cambia nada.

**Downgrade: no hace nada, y es a proposito.** Las filas que esta migracion crea son
indistinguibles de las que crea el panel--son lo mismo--y borrarlas dejaria negocios
sin horarios: exactamente el fallo que se esta arreglando. Una migracion de datos que
se puede revertir sin perder nada tiene que poder identificar lo que agrego; esta no
puede, y borrar a ciegas no es una opcion.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0026_asignar_servicios_al_alta"
down_revision: str | None = "0025_fix_profs_fk_setnull"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: `set_config(..., true)` y no `SET`: el tercer argumento lo hace local a la
#: transaccion, asi que no queda pegado en la conexion que Alembic devuelve al pool.
_REPARAR = """
DO $reparacion$
DECLARE
    v_negocio uuid;
BEGIN
    FOR v_negocio IN SELECT id FROM businesses LOOP
        PERFORM set_config('app.current_business_id', v_negocio::text, true);

        INSERT INTO professional_services
            (business_id, professional_id, service_id, is_active)
        SELECT s.business_id, p.id, s.id, true
          FROM services s
          JOIN professionals p
            ON p.business_id = s.business_id
           AND p.archived_at IS NULL
         WHERE s.business_id = v_negocio
           AND s.is_active
           AND s.archived_at IS NULL
           AND NOT EXISTS (
                SELECT 1
                  FROM professional_services ps
                  JOIN professionals p2
                    ON p2.id = ps.professional_id
                   AND p2.business_id = ps.business_id
                 WHERE ps.service_id = s.id
                   AND ps.business_id = s.business_id
                   AND ps.is_active
                   AND p2.is_active
                   AND p2.archived_at IS NULL
           )
        ON CONFLICT (professional_id, service_id) DO NOTHING;
    END LOOP;

    -- Dejar el GUC como estaba: vacio significa "sin tenant" (deny all) y es el
    -- estado en el que queda la conexion despues de cada request.
    PERFORM set_config('app.current_business_id', '', true);
END
$reparacion$;
"""


def upgrade() -> None:
    op.execute(_REPARAR)


def downgrade() -> None:
    # Ver el docstring del modulo: las filas son las mismas que crea el panel, no
    # hay forma de saber cuales agrego esta migracion, y borrarlas dejaria negocios
    # sin horarios.
    pass
