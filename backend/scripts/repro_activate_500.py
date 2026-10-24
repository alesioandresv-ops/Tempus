"""Reproduccion local del 500 en admin activate.

Hipotesis: `get_session` corre con el rol app (tempus_app), que no puede escribir
en `businesses` (RLS + permisos DDL solo para el rol de migraciones). El router
admin usa `get_session`, entonces el UPDATE que activa Pro revienta.
"""

import asyncio
import os
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

os.environ['DATABASE_URL'] = 'postgresql+asyncpg://tempus_app:tempus_app_dev_pw@localhost:5432/tempus_test'
os.environ['DATABASE_MIGRATION_URL'] = 'postgresql+asyncpg://tempus_owner:tempus_owner_dev_pw@localhost:5432/tempus_test'


async def main() -> None:
    eng = create_async_engine(os.environ['DATABASE_MIGRATION_URL'])
    async with eng.connect() as c:
        r = await c.execute(sa.text("SELECT id FROM businesses WHERE slug LIKE 'test-qa-%' LIMIT 1"))
        print('negocio QA:', r.scalar())
    await eng.dispose()


asyncio.run(main())
