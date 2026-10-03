"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Tipos propios del proyecto. El autogenere los renderiza con su ruta completa
# (`app.db.types.UtcDateTime`) porque no son de la libreria, asi que la migracion
# necesita poder resolver ese nombre.
#
# Los imports que deduce el autogenere van en `${imports}`, y se dejan donde estan:
# arriba de los imports propios del proyecto, que es donde el formateador quiere
# encontrarlos. Por eso aqui no se escribe `from sqlalchemy.dialects import
# postgresql` a mano: el autogenere lo agrega solo cuando la migracion lo usa, y
# escribirlo aqui terminaba duplicandolo.
import app.db.types

${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
