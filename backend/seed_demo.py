"""Siembra un negocio completo para probar el flujo publico de punta a punta.

No es un fixture de pytest: es una herramienta de diagnostico. Se corre a mano
contra la base de desarrollo y deja un negocio activo, con servicios,
profesionales, horarios y un bloque, para poder pedir disponibilidad y crear una
reserva sin tener que hacerlo todo a mano.

Usa el rol de **migraciones** (`DATABASE_MIGRATION_URL`), no el de la aplicacion,
porque el rol de la app tiene `SELECT` y nada mas sobre `businesses`: no puede
insertar. Esa es una separacion deliberada del §7, y por eso el alta de un
negocio tiene que hacerla un rol con permiso de escritura.
"""

import asyncio
import datetime as dt
import uuid
from decimal import Decimal

from app.core.config import get_settings
from app.db.session import dispose_engine, tenant_session
from app.models import BusinessUserRole, MembershipStatus
from app.modules.professionals.models import Professional
from app.modules.schedules.models import BusinessHour
from app.modules.services.models import ProfessionalService, Service
from sqlalchemy import text

SLUG = "pelu-demo"
#: `.test` esta reservado por la RFC 2606 y `email-validator` -- la misma libreria
#: que valida el body del login -- lo rechaza. Con un `.test` el seed dejaba una
#: cuenta que el endpoint no acepta, o sea un admin que existe en la base pero que
#: no se puede loguear. Un dominio real, entonces.
ADMIN_EMAIL = "admin@pelu-demo.com.ar"
PASSWORD = "Demo1234!"


async def main() -> None:
    settings = get_settings()

    # --- Rol de migraciones: alta del negocio y del admin ---
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(settings.sqlalchemy_url(for_migrations=True))
    async with engine.begin() as conn:
        exists = await conn.scalar(text("SELECT id FROM businesses WHERE slug = :s"), {"s": SLUG})
        if exists:
            print(f"El negocio '{SLUG}' ya existe ({exists}). Se borra y se re-siembra.")
            await conn.execute(text("DELETE FROM businesses WHERE slug = :s"), {"s": SLUG})

        business_id = uuid.uuid4()
        user_id = uuid.uuid4()

        await conn.execute(
            text(
                """
                INSERT INTO businesses (
                    id, slug, name, description, timezone, locale, currency,
                    slot_interval_minutes, min_lead_minutes, max_advance_days,
                    cancellation_window_minutes, status, phone_e164, address_text,
                    brand_color, created_at, updated_at
                ) VALUES (
                    :id, :slug, 'Pelu Demo', 'Peluqueria de prueba',
                    'America/Argentina/Buenos_Aires', 'es-AR', 'ARS',
                    15, 0, 60, 120, 'active', '+5491100000001',
                    'Av. Siempreviva 742', '#2563EB',
                    now(), now()
                )
                """
            ),
            {"id": business_id, "slug": SLUG},
        )
        print(f"Negocio creado: {business_id} (slug={SLUG})")

        from app.core.security import get_password_hash

        password_hash = get_password_hash().hash(PASSWORD)

        await conn.execute(
            text(
                """
                INSERT INTO business_users (
                    id, business_id, email, password_hash, full_name, role,
                    status, created_at, updated_at
                ) VALUES (
                    :id, :bid, :email, :hash, 'Admin Demo', :role, :status,
                    now(), now()
                )
                """
            ),
            {
                "id": user_id,
                "bid": business_id,
                "email": ADMIN_EMAIL,
                "hash": password_hash,
                "role": BusinessUserRole.ADMIN.value,
                "status": MembershipStatus.ACTIVE.value,
            },
        )
        print(f"Admin creado: {ADMIN_EMAIL} / {PASSWORD}")

    await engine.dispose()

    # --- Rol de la app, con tenant: el resto ---
    async with tenant_session(business_id) as session:
        service_ids = []
        for nombre, duracion, precio in (
            ("Corte de pelo", 30, Decimal("8000.00")),
            ("Coloracion", 90, Decimal("35000.00")),
            ("Manicure", 45, Decimal("12000.00")),
        ):
            sid = uuid.uuid4()
            service_ids.append(sid)
            session.add(
                Service(
                    id=sid,
                    business_id=business_id,
                    name=nombre,
                    description=f"{nombre} estandar",
                    duration_minutes=duracion,
                    price=precio,
                    currency="ARS",
                    is_active=True,
                    sort_order=len(service_ids),
                )
            )
        await session.flush()
        print(f"Servicios: {len(service_ids)}")

        prof_ids = []
        for nombre, color in (
            ("Ana", "#DC2626"),
            ("Beto", "#16A34A"),
            ("Caro", "#9333EA"),
        ):
            pid = uuid.uuid4()
            prof_ids.append(pid)
            session.add(
                Professional(
                    id=pid,
                    business_id=business_id,
                    display_name=nombre,
                    is_active=True,
                    sort_order=len(prof_ids),
                    color=color,
                )
            )
        await session.flush()
        print(f"Profesionales: {len(prof_ids)}")

        # Ana y Beto hacen todo; Caro solo corte. Asi "cualquier profesional"
        # tiene un resultado distinto del que daria si los tres hicieran todo.
        for pid in prof_ids[:2]:
            for sid in service_ids:
                session.add(
                    ProfessionalService(
                        business_id=business_id,
                        professional_id=pid,
                        service_id=sid,
                        is_active=True,
                    )
                )
        session.add(
            ProfessionalService(
                business_id=business_id,
                professional_id=prof_ids[2],
                service_id=service_ids[0],
                is_active=True,
            )
        )
        await session.flush()
        print("Ana y Beto: todos los servicios. Caro: solo corte.")

        # Horarios: L-V 09-13 y 15-19. Dos ventanas, no una con descanso: el
        # §11 prohibe el concepto de descanso.
        for weekday in range(5):
            for idx, (ini, fin) in enumerate(
                ((dt.time(9, 0), dt.time(13, 0)), (dt.time(15, 0), dt.time(19, 0)))
            ):
                session.add(
                    BusinessHour(
                        business_id=business_id,
                        weekday=weekday,
                        window_index=idx,
                        is_open=True,
                        start_time=ini,
                        end_time=fin,
                    )
                )
        await session.flush()
        print("Horarios: L-V 09:00-13:00 y 15:00-19:00")

    await dispose_engine()

    print()
    print("Listo. Para probar:")
    print(f"  GET  http://localhost:8000/api/v1/public/businesses/{SLUG}")
    print(f"  POST http://localhost:8000/api/v1/auth/login  {ADMIN_EMAIL} / {PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
