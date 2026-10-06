"""whatsapp y avatar_url en professionals (Fase 4)

Revision ID: 0016_fase4_whatsapp_avatar
Revises: 0015_reserved_slugs
Create Date: 2026-10-04

La Fase 4 pide en `professionals` dos cosas que no existian: el WhatsApp
de contacto y la foto. Servicios, profesionales y horarios ya tenian tabla
y CRUD desde `0001`--lo que faltaba de verdad eran estas dos columnas.

**El WhatsApp es E.164 sin `+`.** Es el mismo patron que valida el
telefono del cliente en la reserva publica (`PHONE_E164_RE`): digitos,
primer digito no cero, de 8 a 15 en total. Sin el `+` porque es el
formato en el que ya vive el telefono en todo el proyecto y porque el
`+` lo pone la UI al marcar. El `CHECK` en la base es la garantia: el
servicio y el esquema validan antes, pero un `INSERT` directo desde
`psql` no pasa por ninguno de los dos.

**`avatar_url` es texto libre con tope**, no un tipo `URI`: Postgres no
tiene uno, y la unica validacion que importa (que sea http/https) la
hace el servicio, donde el error se traduce a 422 con el mensaje que el
panel muestra. La columna es nullable y sin default: NULL significa "sin
foto", y el panel ya sabe pintar ese caso.

Las columnas son `ADD COLUMN` nullable y sin default: no tocan filas
existentes, y la politica de RLS de `professionals` (que es por fila, no
por columna) no se enteran.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from collections.abc import Sequence

#: revision identifiers, used by Alembic.
#: El ID tiene que caber en `alembic_version.version_num`, que es
#: `varchar(32)`: `0014_onboarding_create_business` ya tiene 31 y es
#: el mas largo del proyecto. El nombre completo de la Fase 4 no cabe
#: (39), y el contenido real de esta migracion son las dos columnas.
revision: str = "0016_fase4_whatsapp_avatar"
down_revision: str | None = "0015_reserved_slugs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLA = "professionals"

#: E.164 sin `+`: primer digito no cero, 8 a 15 digitos. Es el patron de
#: `PHONE_E164_RE` del schema publico, escrito para regex POSIX.
_WHATSAPP_RE = "^[1-9][0-9]{6,14}$"


def upgrade() -> None:
    op.add_column(_TABLA, sa.Column("whatsapp", sa.Text(), nullable=True))
    op.add_column(_TABLA, sa.Column("avatar_url", sa.Text(), nullable=True))
    op.create_check_constraint(
        "whatsapp_e164_sin_mas",
        _TABLA,
        f"(whatsapp IS NULL) OR (whatsapp ~ '{_WHATSAPP_RE}')",
    )


def downgrade() -> None:
    op.drop_constraint("whatsapp_e164_sin_mas", _TABLA, type_="check")
    op.drop_column(_TABLA, "avatar_url")
    op.drop_column(_TABLA, "whatsapp")


__all__ = ["downgrade", "upgrade"]
