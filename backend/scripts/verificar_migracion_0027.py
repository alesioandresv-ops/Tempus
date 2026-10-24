"""Verificacion de la migracion 0027 contra la BD de tests local.

Comprueba: version de alembic, columnas de pro_payments, RLS, grants y pro_until
en businesses. Es un script de una corrida; no forma parte de la suite.
"""

import asyncio
import sys

import asyncpg

DSN = 'postgresql://tempus_owner:tempus_owner_dev_pw@localhost:5432/tempus_test'


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    version = await conn.fetchval('SELECT version_num FROM alembic_version')
    columnas = [
        r['column_name']
        for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='pro_payments' ORDER BY ordinal_position"
        )
    ]
    rls = await conn.fetchval(
        "SELECT c.relrowsecurity FROM pg_class c WHERE c.relname='pro_payments'"
    )
    pro_until = await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name='businesses' AND column_name='pro_until'"
    )
    enum = await conn.fetchval(
        "SELECT count(*) FROM pg_type WHERE typname='pro_payment_status'"
    )
    print('alembic_version:', version)
    print('pro_payments columnas:', columnas)
    print('pro_payments RLS:', bool(rls))
    print('businesses.pro_until presente:', bool(pro_until))
    print('enum pro_payment_status:', bool(enum))
    await conn.close()


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
