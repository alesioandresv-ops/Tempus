"""el panel admin puede escribir el estado de cobro de un negocio

El router administrativo (`/api/v1/admin/*`, protegido por `ADMIN_SECRET`)
activa el plan Pro por transferencia: escribe `businesses.plan`,
`businesses.pro_until` y `businesses.status`. La migracion 0002 le concedio al
rol de la app **solo SELECT** sobre `businesses` — bien para los endpoints de
siempre, que nunca escriben la tabla — y el deploy en vivo devolvia 500
(permission denied) en `PUT /businesses/{id}/activate`.

La salida no es abrir la tabla entera: se concede **UPDATE de tres columnas**
(`plan`, `pro_until`, `status`) al rol de la app. El resto de las columnas
(nombre, slug, timezone, datos del negocio) sigue en solo-SELECT, y el INSERT
sigue prohibido: crear negocios sigue siendo cosa del onboarding con el rol de
DDL. Con grants de columna, un endpoint con bug de mass assignment no puede
reescribir el slug ni la zona horaria de nadie.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028_grant_businesses_pro_update"
down_revision: str | None = "0027_pro_payments_transferencia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tempus_app"

#: Columnas que el admin escribe al activar/bloquear. Nada mas.
_COLUMNAS = ("plan", "pro_until", "status")

_UP = f'GRANT UPDATE ({", ".join(_COLUMNAS)}) ON TABLE businesses TO "{APP_ROLE}"'
_DOWN = f'REVOKE UPDATE ({", ".join(_COLUMNAS)}) ON TABLE businesses FROM "{APP_ROLE}"'


def _grant_si_existe(sql: str, rol: str) -> None:
    op.execute(
        f"""
        DO $grant$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{rol}') THEN
                EXECUTE '{sql}';
            END IF;
        END $grant$;
        """
    )


def upgrade() -> None:
    _grant_si_existe(_UP, APP_ROLE)


def downgrade() -> None:
    _grant_si_existe(_DOWN, APP_ROLE)
