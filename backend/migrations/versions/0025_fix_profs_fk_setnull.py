"""FK compuesta de professionals: el SET NULL no puede tocar business_id

`professionals` tiene una FK compuesta `(user_id, business_id) ->
business_users(id, business_id)` con `ON DELETE SET NULL`. Eso fue pensado para
desvincular al profesional cuando se borra el miembro del negocio (el profesional
sobrevive como persona sin login). El problema es que Postgres aplica `SET NULL` a
**las dos** columnas de la FK, y `business_id` es `NOT NULL`: en cuanto se borra
un `business_users`, el profesional intenta quedar con `business_id = NULL` y viola
el constraint. Como consecuencia, **borrar un negocio falla siempre**, aunque no
haya profesionales ni usuarios: el cascade de `businesses -> business_users` dispara
el `SET NULL` sobre professionals y se aborta la transaccion entera.

La correccion es separar las dos responsabilidades que la FK compuesta estaba
mezclando:

1. **Desvincular** (`user_id = NULL` cuando se borra el miembro) -> FK simple
   `user_id -> business_users.id ON DELETE SET NULL`. Solo toca `user_id`.

2. **Aislar por tenant** -> FK compuesta `(user_id, business_id) ->
   business_users(id, business_id)` con `NO ACTION` (default). Se verifica solo
   cuando `user_id` no es NULL (un NULL en una FK compuesta desactiva el chequeo),
   asi que no bloquea el borrado: la FK simple ya puso `user_id = NULL` antes de
   que el chequeo de esta corra.

La separacion es la que faltaba: antes, una sola FK hacia las dos cosas y Postgres
no distingue "que columna poner en NULL" de "que columna verificar".

**Downgrade** restaura la FK compuesta original con `SET NULL`. Eso vuelve a
introducir el bug, pero es el estado anterior y es lo que un downgrade promete.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0025_fix_profs_fk_setnull"
down_revision: str | None = "0024_activar_al_alta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_ORIGINAL = "fk_professionals_user_id_business_users"
_FK_SIMPLE = "fk_professionals_user_id"
_FK_TENANT = "fk_professionals_user_tenant"


def upgrade() -> None:
    op.execute(f"ALTER TABLE professionals DROP CONSTRAINT {_FK_ORIGINAL}")

    # 1) Desvincular: solo user_id, sin tocar business_id.
    op.execute(
        f"""
        ALTER TABLE professionals
        ADD CONSTRAINT {_FK_SIMPLE}
        FOREIGN KEY (user_id) REFERENCES business_users(id)
        ON DELETE SET NULL
        """
    )

    # 2) Aislar por tenant: compuesta, sin accion de borrado (NO ACTION).
    #    Solo se verifica cuando user_id no es NULL.
    op.execute(
        f"""
        ALTER TABLE professionals
        ADD CONSTRAINT {_FK_TENANT}
        FOREIGN KEY (user_id, business_id) REFERENCES business_users(id, business_id)
        """
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE professionals DROP CONSTRAINT {_FK_SIMPLE}")
    op.execute(f"ALTER TABLE professionals DROP CONSTRAINT {_FK_TENANT}")

    op.execute(
        f"""
        ALTER TABLE professionals
        ADD CONSTRAINT {_FK_ORIGINAL}
        FOREIGN KEY (user_id, business_id) REFERENCES business_users(id, business_id)
        ON DELETE SET NULL
        """
    )
