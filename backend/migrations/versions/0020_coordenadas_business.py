"""Fase 9.2 — Columnas de coordenadas en businesses.

Suma dos columnas float nullable: `latitude` y `longitude`.
Sirven para el mapa arrastrable con MapLibre GL JS en el panel.

`businesses` se escribe columna por columna (patrón 0011/0019): el UPDATE
sobre las coordenadas se reabre para `tempus_app` junto con las columnas,
porque sin el GRANT el `PUT /config/whatsapp` que las persiste caería con
"permission denied" (un 500 sin mensaje útil).

Migración 0020, numerada manualmente tras 0019.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_coordenadas_business_v2"
down_revision: str | None = "0019_mercado_pago"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tempus_app"


def _grant_if_exists(sql_grant: str, role_name: str):
    op.execute(f"""
        DO $do$ 
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role_name}') THEN
                EXECUTE $g${sql_grant}$g$;
            END IF;
        END
        $do$;
    """)

def upgrade() -> None:
    #: Los comments coinciden con el modelo `Business` (la ordenada geografica
    #: en decimal); `alembic check` los compara y falla si faltan.
    op.add_column(
        "businesses",
        sa.Column(
            "latitude", sa.Float(), nullable=True, comment="Coordenada geográfica en decimal"
        ),
    )
    op.add_column(
        "businesses",
        sa.Column(
            "longitude", sa.Float(), nullable=True, comment="Coordenada geográfica en decimal"
        ),
    )
    _grant_if_exists(f\'GRANT UPDATE ("latitude", "longitude") ON businesses TO "{APP_ROLE}"\', APP_ROLE)


def downgrade() -> None:
    op.execute(f'REVOKE UPDATE ("latitude", "longitude") ON businesses FROM "{APP_ROLE}"')
    op.drop_column("businesses", "longitude")
    op.drop_column("businesses", "latitude")
