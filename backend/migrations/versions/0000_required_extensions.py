"""required extensions

Revision ID: 0000_required_extensions
Revises:
Create Date: 2026-10-02

Crea las extensiones de las que depende el esquema.

**Por que esta migracion existe.** El esquema usa `citext` en cuatro columnas
--los emails de los dos logos de acceso y los slugs de negocio y de servicio--,
`btree_gist` para la restriccion EXCLUDE anti-doble-reserva, y `pgcrypto` para
`gen_random_bytes`. Ninguna migracion las creaba: en la base de desarrollo
existian porque alguien ejecuto `CREATE EXTENSION` a mano, una vez, y desde ahi
nadie volvio a necesitarlo.

Eso no se ve hasta que alguien crea una base de verdad--un entorno nuevo, una
integracion continua, una maquina de desarrollo-- y `alembic upgrade head` falla
en la primera tabla con `no existe el tipo "citext"`. Un despliegue limpio del
proyecto no era posible, y el unico indicio de que las extensiones hacian falta
era una base de desarrollo que ya las tenia.

Va **antes** que `0001` y no dentro de ella por dos razones:

1. Si estuviera al principio de `0001`, `0001` dejaria de ser "el esquema" y
   pasaria a ser "el esquema y su preambulo". El nombre miente.
2. `down_revision` de `0001` queda apuntando acá, y una base ya migrada --la de
   desarrollo, que esta en `0011`-- no vuelve a correr nada: Alembic solo busca
   las revisiones que faltan, y `0000` ya esta implicitamente aplicada por el
   hecho de que `0001` esta sellada. Insertar una revision antes de la primera es
   una operacion soportada justamente para este caso.

Las tres son extensiones *de confianza*: las puede instalar un rol sin ser
superusuario, con permiso de `CREATE` sobre la base. Por eso no hace falta que
las migraciones corran como `postgres`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0000_required_extensions"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Las tres, con el motivo por el que el esquema las necesita. El comentario no es
#: decorativo: la proxima vez que alguien agregue una columna con `CITEXT` va a
#: querer saber si la extension sigue en uso o si ya se puede quitar.
EXTENSIONES: tuple[tuple[str, str], ...] = (
    (
        "citext",
        "comparacion insensible a mayusculas para `platform_users.email`, "
        "`businesses.slug`, `services.slug` y `business_users.email`. El slug es "
        "la identidad publica del negocio (`/p/{slug}`), asi que `Pelu` y `pelu` "
        "tienen que ser el mismo negocio y no dos.",
    ),
    (
        "btree_gist",
        "operador de igualdad GiST sobre `uuid`. La restriccion EXCLUDE de "
        "`bookings` compara `(professional_id WITH =, occupied_range WITH &&)` y "
        "sin esta extension no se puede construir: es la autorizacion "
        "anti-doble-reserva de §9.",
    ),
    (
        "pgcrypto",
        "`gen_random_bytes`, para el material aleatorio del cifrado en reposo. "
        "En PostgreSQL 13 y anteriores `gen_random_uuid()` tambien venia de aca.",
    ),
)


def upgrade() -> None:
    for nombre, _ in EXTENSIONES:
        # `IF NOT EXISTS` por dos motivos: la migracion se puede correr contra una
        # base donde alguien las creo a mano --que es exactamente lo que pasaba--,
        # y `CREATE EXTENSION` sin el IF falla y aborta toda la cadena.
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{nombre}"')


def downgrade() -> None:
    # No se borran. Una extension sobrevive a todas las tablas que la usan: si el
    # `downgrade` de `0001` funciono, ya no queda ninguna, pero si alguien hace
    # `alembic downgrade 0000` desde una revision intermedia las tablas siguen
    # ahi y tirar la extension las dejaria inservibles con un error que no menciona
    # la extension.
    #
    # `0000` no baja de `None`, asi que este `downgrade` solo se alcanza si alguien
    # pide explicitamente reversar la base entera, y en ese caso las extensiones
    # son lo ultimo que deberia irse.
    pass
