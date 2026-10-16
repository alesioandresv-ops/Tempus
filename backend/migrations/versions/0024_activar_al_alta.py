"""alta de tenants: el negocio nace publicable (status = 'active')"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0024_activar_al_alta"
down_revision: str | None = "0023_reconcile_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCION = "tempus_create_business"


def _cuerpo_funcion(status: str) -> str:
    """Cuerpo de `tempus_create_business` con el `status` del alta parametrizado.

    Es identico al de 0014 salvo el `status` con el que nace el negocio: mismo
    `SECURITY DEFINER`, mismo `SET search_path = public`, mismo manejo del slug
    reservado. El upgrade lo deja en `active` y el downgrade lo devuelve a `trial`.
    """
    return f"""
CREATE OR REPLACE FUNCTION {_FUNCION}(p_slug citext, p_name text, p_timezone text, p_phone_e164 text)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $cuerpo$
DECLARE
    v_id uuid;
BEGIN
    IF EXISTS (SELECT 1 FROM slug_reservations WHERE slug = p_slug) THEN
        RAISE EXCEPTION 'El nombre de URL % esta reservado', p_slug
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO businesses (slug, name, timezone, phone_e164, status)
    VALUES (p_slug, p_name, p_timezone, p_phone_e164, '{status}')
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$cuerpo$
"""


def upgrade() -> None:
    # `CREATE OR REPLACE` **conserva dueño y ACL**, asi que no se repite el
    # `ALTER FUNCTION ... OWNER TO` ni los `GRANT` de 0014/0022: hacerlo de nuevo
    # solo daria oportunidad de romperlos. El unico requisito es que el rol de
    # `DATABASE_MIGRATION_URL` sea el dueño actual de la funcion. Si no lo es, la
    # sentencia falla y se ve; no hay fallback silencioso a proposito.
    op.execute(_cuerpo_funcion("active"))


def downgrade() -> None:
    op.execute(_cuerpo_funcion("trial"))
