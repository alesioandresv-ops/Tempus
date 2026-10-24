"""Repro local del flujo del endpoint admin activate con admin_session."""

import asyncio
import datetime as dt
import os

import sqlalchemy as sa
from sqlalchemy import select

os.environ['DATABASE_URL'] = 'postgresql+asyncpg://tempus_app:tempus_app_dev_pw@localhost:5432/tempus_test'
os.environ['DATABASE_MIGRATION_URL'] = 'postgresql+asyncpg://tempus_owner:tempus_owner_dev_pw@localhost:5432/tempus_test'


async def main() -> None:
    from app.core.time import now
    from app.db.session import admin_session
    from app.models.enums import BusinessPlan
    from app.modules.businesses.models import Business
    from app.modules.payments.pro_models import ProPayment

    async with admin_session() as s:
        total = (await s.execute(sa.select(sa.func.count()).select_from(ProPayment))).scalar_one()
        print('pro_payments count:', total)
        neg = (await s.execute(select(Business).where(Business.slug.like('test-qa-%')))).scalar_one_or_none()
        print('negocio:', neg.slug if neg else None)
        if neg:
            neg.plan = BusinessPlan.PRO
            neg.pro_until = now() + dt.timedelta(days=30)
            await s.flush()
            print('UPDATE ok -> pro_until:', neg.pro_until)


asyncio.run(main())
