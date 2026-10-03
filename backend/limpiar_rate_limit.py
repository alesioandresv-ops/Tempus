import asyncio

from app.core.config import get_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main():
    settings = get_settings()
    # engine temporal, no toca el global
    engine = create_async_engine(settings.database_url, poolclass=None)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM rate_limit_buckets"))
        print("buckets borrados (engine temporal)")
    await engine.dispose()


asyncio.run(main())
