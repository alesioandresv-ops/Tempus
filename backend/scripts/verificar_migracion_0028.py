"""Verifica 0028: version de alembic y grant UPDATE de columnas al rol app."""

import asyncio
import sys

import asyncpg

DSN = 'postgresql://tempus_owner:tempus_owner_dev_pw@localhost:5432/tempus_test'
COLUMNAS = ('plan', 'pro_until', 'status')


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    version = await conn.fetchval('SELECT version_num FROM alembic_version')
    for col in COLUMNAS:
        ok = await conn.fetchval(
            "SELECT has_column_privilege('tempus_app','businesses',$1,'UPDATE')", col
        )
        print(f'UPDATE({col}) para tempus_app:', ok)
    insert = await conn.fetchval(
        "SELECT has_table_privilege('tempus_app','businesses','INSERT')"
    )
    print('INSERT en businesses (debe ser False):', insert)
    print('alembic_version:', version)
    await conn.close()


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
