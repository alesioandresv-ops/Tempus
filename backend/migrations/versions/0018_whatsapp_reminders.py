"""Configuracion de WhatsApp y recordatorios por negocio (Fase D-3)

Revision ID: 0018_whatsapp_reminders
Revises: 0017_booking_token_definer
Create Date: 2026-10-08

`whatsapp_connections` existe desde `0001` como el lugar natural de la
configuracion de WhatsApp **por negocio**: una fila por negocio, con
`phone_number_id`, los tokens cifrados con Fernet, `is_active` y `status`.
Solo que estaba huerfana: la outbox marcaba todo `failed` con "sin
credenciales de Meta" porque nadie creaba ni leia esa fila.

La Fase D-3 enciende los recordatorios 24h/2h con un opt-in simple por
negocio. Dos cambios sobre la tabla:

1. **`waba_id`, `display_phone` y `app_secret_encrypted` dejan de ser
   obligatorios.** El flujo de "connect" de Meta (embedded signup) los trae
   todos juntos; el opt-in de Fase D-3 solo pide `phone_number_id` y token.
   Obligar a llenarlos para encender un recordatorio obligaria al negocio a
   pasar por un onboarding de Meta que no esta en alcance.

2. **Dos columnas nuevas de plantilla por recordatorio.** El `name` de la
   plantilla es propiedad del negocio: `recordatorio_24h` y
   `recordatorio_2h` son los defaults (constantes de la aplicacion), y el
   negocio puede elegir otros nombres si Meta los aprobo con otros ids.
   Guardar el nombre elegido en la conexion (y no en `whatsapp_templates`)
   porque es configuracion, no un catalogo de plantillas de Meta.

Nada de RLS ni roles: las columnas nuevas caen bajo la politica
`tenant_isolation` existente y el rol de la app ya tiene UPDATE de tabla
completa sobre `whatsapp_connections` (solo `business_users` esta
restringido por columnas, ver `0002`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_whatsapp_reminders"
down_revision: str | None = "0017_booking_token_definer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "whatsapp_connections"


def upgrade() -> None:
    # El opt-in simple de Fase D-3 no trae estos datos; el flujo de "connect"
    # completo de Meta los llena después.
    for columna in ("waba_id", "display_phone", "app_secret_encrypted"):
        op.execute(f'ALTER TABLE "{TABLE}" ALTER COLUMN "{columna}" DROP NOT NULL')

    op.add_column(TABLE, sa.Column("reminder_24h_template", sa.String(64), nullable=True))
    op.add_column(TABLE, sa.Column("reminder_2h_template", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, "reminder_2h_template")
    op.drop_column(TABLE, "reminder_24h_template")

    # Volver a NOT NULL solo si no hay filas con NULL: un downgrade que
    # reviente por datos reales es preferible a uno que los invente.
    for columna in ("waba_id", "display_phone", "app_secret_encrypted"):
        op.execute(f'ALTER TABLE "{TABLE}" ALTER COLUMN "{columna}" SET NOT NULL')
