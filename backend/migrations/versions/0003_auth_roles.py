"""Dos valores de enum que la tabla RBAC del §10.4 y el §10.3 necesitan

Revision ID: 0003_auth_roles
Revises: 0002_rls_triggers_grants
Create Date: 2026-09-29

Agrega dos valores a dos enums existentes. Es una migracion de una linea de DDL por
enum y aun asi tiene su propio archivo, por dos razones concretas.

**Por que `locked` y no reusar `disabled`.** El §10.3 dice que ante reuso de un
refresh token se hace `status = 'locked'`. Se podria haber escrito `disabled` y
ahorrarse el valor, pero `disabled` significa "un admin lo desactivo" y `locked`
significa "el sistema detecto un robo y bloqueo la cuenta". Son decisiones distintas,
de personas distintas, con salidas distintas: un `disabled` se levanta desde el panel,
un `locked` requiere revisar el `audit_log` de seguridad. Si comparten valor, un
bloqueo por robo se lee como un baja de rutina y el incidente se pierde de vista.

**Por que `professional` y no derivarlo de `professionals.user_id`.** La tabla RBAC
del §10.4 tiene una columna `professional` con "solo su propia" en la agenda, y el rol
`business_user_role` solo tenia `admin` y `staff`. Se podia intentar derivar el
comportamiento de "este login tiene fila en `professionals`", y esa es la lectura
elegante del §5.1. El problema es que `staff` ya concede ver la agenda completa: un
login que es a la vez profesional y staff tendria que ver toda la agenda, y la tabla
del §10.4 dice que no. Sin un valor propio, la unica forma de que un profesional vea
solo lo suyo es negarle `staff`, y `staff` es el rol mas bajo que existe.

Con `professional` como valor propio, cada login tiene exactamente una fila de la
tabla del §10.4, y el mapa rol -> scopes es una funcion total: no hay combinaciones
implicitas que interpretar.

`ALTER TYPE ... ADD VALUE` no puede correr dentro de un bloque de transaccion, asi que
va en `autocommit_block`. Sin eso, la migracion falla con "ALTER TYPE ... ADD cannot
run inside a transaction block", que es un error que no dice nada sobre el problema
real. El precio es que no hay rollback: en un deploy, un fallo entre el primer ALTER y
el segundo deja la base con `professional` pero sin `locked`. Se acepta: los dos
`ADD VALUE` no tienen dependencias entre si, y reintentar la migracion completa el
segundo.

El `downgrade` si es reversible, con la condicion de que los valores no esten en uso;
ver ahi el razonamiento.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from alembic.util import CommandError

revision: str = "0003_auth_roles"
down_revision: str | None = "0002_rls_triggers_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUSINESS_USER_ROLE = "business_user_role"
MEMBERSHIP_STATUS = "membership_status"

NUEVOS_ROLES = ("professional",)
NUEVOS_ESTADOS = ("locked",)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for valor in NUEVOS_ROLES:
            op.execute(f"ALTER TYPE {BUSINESS_USER_ROLE} ADD VALUE IF NOT EXISTS '{valor}'")
        for valor in NUEVOS_ESTADOS:
            op.execute(f"ALTER TYPE {MEMBERSHIP_STATUS} ADD VALUE IF NOT EXISTS '{valor}'")


def downgrade() -> None:
    """Esta migracion es un piso: no se puede deshacer, y el error lo dice.

    PostgreSQL 12+ tiene `ALTER TYPE ... DROP VALUE`, asi que en principio habria una
    vuelta. En este proyecto no la hay, por una razon concreta: **asyncpg no la
    implementa**. Lanza `FeatureNotSupportedError: dropping an enum value is not
    implemented` antes de enviar nada a la base.

    Se intento esquivarlo y no se puede. El chequeo de asyncpg no mira el comando
    principal sino el texto del statement, asi que envolverlo en un `DO $$ ... $$` --
    que ademas es la forma correcta, porque `DROP VALUE` si corre dentro de una
    transaccion-- tambien falla. Y `DROP VALUE IF EXISTS` no es sintaxis valida: el
    `IF NOT EXISTS` existe solo para `ADD VALUE`, y el error que devuelve la base es
    "syntax error at or near IF", que no menciona enums ni migraciones.

    La unica forma seria agregar `psycopg` como segundo driver solo para esto, y no
    compensa: `DROP VALUE` tambien falla si alguna fila usa el valor, de modo que en
    una base con datos el rollback seria imposible igual. El unico caso que ganaria es
    una base vacia, que es exactamente el caso del job `migraciones` del CI, y para ese
    caso alcanza con que el CI knownzca el piso.

    Por eso el error es `CommandError` y no `NotImplementedError`: el codigo esta bien,
    la operacion es imposible en este setup. `NotImplementedError` dice "alguien se
    dejo un `pass`", y alguien lo iba a perseguir.

    Consecuencia operativa, que es lo unico que hay que recordar: **no se puede hacer
    `alembic downgrade base`**. El piso es `0002_rls_triggers_grants`. Revertir el
    codigo de esta migracion se hace cambiando el enum de Python; los dos valores de mas
    en la base no rompen nada, porque el codigo que no los usa los ignora.
    """
    raise CommandError(
        "0003_auth_roles es un piso de reversibilidad y no se puede deshacer: asyncpg no "
        "implementa 'ALTER TYPE ... DROP VALUE' y ningun wrapper lo esquiva. Para volver "
        "atras, `alembic downgrade 0002_rls_triggers_grants`. Para revertir solo el codigo, "
        "cambiar BusinessUserRole y MembershipStatus en Python; los dos valores de mas en la "
        "base son inofensivos."
    )
