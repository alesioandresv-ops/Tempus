"""Siembra un negocio completo para probar el flujo publico de punta a punta.

No es un fixture de pytest: es una herramienta de diagnostico. Se corre a mano
contra la base de desarrollo y deja un negocio activo, con servicios,
profesionales y horarios, para poder pedir disponibilidad y crear una reserva
sin tener que hacerlo todo a mano. Es **idempotente**: volver a correrlo
re-siembra el negocio (mismo id, mismo slug) y descarta los hijos previos.

Usa el rol de **migraciones** (`DATABASE_MIGRATION_URL`), no el de la aplicacion,
para el alta del negocio y del admin, porque el rol de la app tiene `SELECT` y
nada mas sobre `businesses`: no puede insertar. Esa es una separacion deliberada
del §7, y por eso el alta de un negocio tiene que hacerla un rol con permiso de
escritura. El resto (servicios, profesionales, horarios) se siembra con el rol de
la app dentro del tenant, como cualquier operacion normal del panel.
"""

import asyncio
import datetime as dt
import uuid
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.db.session import dispose_engine, tenant_session
from app.models import BlockKind, BusinessUserRole, MembershipStatus, registrar_modelos
from app.modules.professionals.models import Professional
from app.modules.schedules.models import Block, BusinessHour
from app.modules.services.models import ProfessionalService, Service
from sqlalchemy import text

SLUG = "el-peluche"
#: `.test` esta reservado por la RFC 2606 y `email-validator` -- la misma libreria
#: que valida el body del login -- lo rechaza. Con un `.test` el seed dejaba una
#: cuenta que el endpoint no acepta, o sea un admin que existe en la base pero que
#: no se puede loguear. Un dominio real, entonces.
ADMIN_EMAIL = "admin@el-peluche.com.ar"
PASSWORD = "Demo1234!"

#: Cuentas de login de los profesionales, `(nombre, email)`. El nombre coincide con
#: el de `PROFESIONALES` para poder vincular `professionals.user_id` sin adivinar:
#: cada profesional entra al panel con su correo (rol `professional`, scopes `*:own`).
PROFESIONAL_USERS = (
    ("Gabriel", "gabriel@el-peluche.com.ar"),
    ("Daniel", "daniel@el-peluche.com.ar"),
    ("Jose", "jose@el-peluche.com.ar"),
)

#: Bloqueos de demostracion, `(nombre_de_profesional_o_None, dias_desde_hoy, inicio,
#: fin, motivo)`. `None` como profesional es un cierre de todo el negocio. Se ponen
#: lejos en el calendario (2-3 semanas) para que no corten la disponibilidad de la
#: semana que viene, que es la que usa el flujo publico de reserva.
BLOQUEOS_DEMO = (
    (None, 14, dt.time(8, 0), dt.time(12, 0), "Mantenimiento del local"),
    ("Gabriel", 17, dt.time(17, 0), dt.time(21, 0), "Gestion personal"),
)

#: Franjas por dia: L-V manana y tarde, sabado solo manana. Son dos ventanas, no
#: una con descanso: el §11 prohibe el concepto de descanso, y la siesta (12-17)
#: directamente no pertenece al horario laboral.
FRANJAS_LABORABLES = ((dt.time(8, 0), dt.time(12, 0)), (dt.time(17, 0), dt.time(21, 0)))
FRANJA_SABADO = ((dt.time(9, 0), dt.time(13, 0)),)

#: Servicios: `(nombre, duracion_min, precio_ars)`.
SERVICIOS = (
    ("Corte de cabello", 45, Decimal("9000.00")),
    ("Corte + barba", 60, Decimal("12000.00")),
)

#: Profesionales: `(nombre, color)`. Gabriel y Daniel hacen todo; Jose solo corte,
#: como en el ejemplo del §1 -- "cualquier profesional" tiene que dar un resultado
#: distinto del que daria si los tres hicieran todo.
PROFESIONALES = (
    ("Gabriel", "#2563EB"),
    ("Daniel", "#16A34A"),
    ("Jose", "#9333EA"),
)

#: Hijos que se limpian al re-sembrar, en orden de FK. Cada re-corrida deja el
#: negocio como recien creado: sin reservas, sin clientes, sin servicios previos.
TABLAS_DE_RESET = (
    "idempotency_keys",
    "bookings",
    "customers",
    "blocks",
    "professional_schedules",
    "time_off",
    "professional_services",
    "services",
    "professionals",
    "business_hours",
)


async def main() -> None:
    # El reexport de `app.models` es perezoso a proposito: `from app.models import
    # BusinessUserRole` no registra ninguna tabla. Para que la sesion de tenant
    # pueda resolver la FK `services.business_id -> businesses` y ordenar los
    # INSERT por dependencia, los modelos tienen que estar todos registrados.
    registrar_modelos()

    settings = get_settings()

    # --- Rol de migraciones: alta o re-siembra del negocio y del admin ---
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(settings.sqlalchemy_url(for_migrations=True))
    async with engine.begin() as conn:
        # `businesses` tiene RLS **forzada** y no existe politica de DELETE para
        # ningun rol (solo SELECT, UPDATE y INSERT): borrar el negocio viejo es un
        # no-op silencioso. La re-siembra es entonces un upsert por slug que
        # conserva el id existente y descarta el uuid que se habia generado.
        exists = await conn.scalar(text("SELECT id FROM businesses WHERE slug = :s"), {"s": SLUG})
        business_id = exists if exists is not None else uuid.uuid4()
        print(
            f"Negocio: {'re-siembra sobre ' + str(exists) if exists else 'creacion de ' + str(business_id)} (slug={SLUG})"
        )

        # El GUC va antes de cualquier sentencia sobre tablas de tenant: las
        # politicas lo miran en el momento de la sentencia. Con el id existente,
        # el UPDATE de `ON CONFLICT` pasa `businesses_write` (USING: id = GUC);
        # con id nuevo, el INSERT es inocuo (`businesses_insert` es WITH CHECK
        # true, y el rol es `tempus_owner`).
        await conn.execute(
            text("SELECT set_config(:guc, :tenant, true)"),
            {"guc": "app.current_business_id", "tenant": str(business_id)},
        )

        await conn.execute(
            text(
                """
                INSERT INTO businesses (
                    id, slug, name, description, timezone, locale, currency,
                    slot_interval_minutes, min_lead_minutes, max_advance_days,
                    cancellation_window_minutes, status, phone_e164, address_text,
                    brand_color, created_at, updated_at
                ) VALUES (
                    :id, :slug, 'El Peluche', 'Barberia de barrio con turno',
                    'America/Argentina/Buenos_Aires', 'es-AR', 'ARS',
                    15, 0, 60, 120, 'active', '+5491100000002',
                    'Av. Siempre Viva 123, Buenos Aires', '#2563EB',
                    now(), now()
                )
                ON CONFLICT (slug) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    timezone = EXCLUDED.timezone,
                    locale = EXCLUDED.locale,
                    currency = EXCLUDED.currency,
                    phone_e164 = EXCLUDED.phone_e164,
                    address_text = EXCLUDED.address_text,
                    brand_color = EXCLUDED.brand_color,
                    slot_interval_minutes = EXCLUDED.slot_interval_minutes,
                    min_lead_minutes = EXCLUDED.min_lead_minutes,
                    max_advance_days = EXCLUDED.max_advance_days,
                    cancellation_window_minutes = EXCLUDED.cancellation_window_minutes,
                    status = EXCLUDED.status,
                    updated_at = now()
                """
            ),
            {"id": business_id, "slug": SLUG},
        )

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
                ON CONFLICT (business_id, email) DO UPDATE SET
                    password_hash = EXCLUDED.password_hash,
                    full_name = EXCLUDED.full_name,
                    role = EXCLUDED.role,
                    status = EXCLUDED.status,
                    updated_at = now()
                """
            ),
            {
                "id": uuid.uuid4(),
                "bid": business_id,
                "email": ADMIN_EMAIL,
                "hash": password_hash,
                "role": BusinessUserRole.ADMIN.value,
                "status": MembershipStatus.ACTIVE.value,
            },
        )
        print(f"Admin asegurado: {ADMIN_EMAIL} / {PASSWORD}")

        # --- Cuentas de los profesionales, rol `professional` ---
        #
        # Igual que el admin: el alta es upsert por (business_id, email) y la
        # re-siembra conserva el id. Se relee el id despues del upsert para
        # vincularlo a `professionals.user_id` en la seccion de tenant.
        ids_de_usuario: dict[str, uuid.UUID] = {}
        for nombre, correo in PROFESIONAL_USERS:
            await conn.execute(
                text(
                    """
                    INSERT INTO business_users (
                        id, business_id, email, password_hash, full_name, role,
                        status, created_at, updated_at
                    ) VALUES (
                        :id, :bid, :email, :hash, :nombre, :role, :status,
                        now(), now()
                    )
                    ON CONFLICT (business_id, email) DO UPDATE SET
                        password_hash = EXCLUDED.password_hash,
                        full_name = EXCLUDED.full_name,
                        role = EXCLUDED.role,
                        status = EXCLUDED.status,
                        updated_at = now()
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "bid": business_id,
                    "email": correo,
                    "hash": password_hash,
                    "nombre": nombre,
                    "role": BusinessUserRole.PROFESSIONAL.value,
                    "status": MembershipStatus.ACTIVE.value,
                },
            )
            fila = await conn.execute(
                text("SELECT id FROM business_users WHERE business_id = :bid AND email = :email"),
                {"bid": business_id, "email": correo},
            )
            ids_de_usuario[nombre] = fila.scalar_one()
        print(
            "Profesionales con login: "
            + ", ".join(f"{n} ({c})" for n, c in PROFESIONAL_USERS)
            + f" / {PASSWORD}"
        )

    await engine.dispose()

    # --- Rol de la app, con tenant: reset de hijos y el resto ---
    async with tenant_session(business_id) as session:
        for tabla in TABLAS_DE_RESET:
            await session.execute(
                text(f"DELETE FROM {tabla} WHERE business_id = :bid"),
                {"bid": business_id},
            )
        await session.flush()
        print(f"Reset de {len(TABLAS_DE_RESET)} tablas de hijo completado.")

        service_ids = []
        for nombre, duracion, precio in SERVICIOS:
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
        for nombre, color in PROFESIONALES:
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
                    # El profesional entra al panel con su cuenta (`role`
                    # professional): el vinculo permite que `/profesional/agenda`
                    # resuelva su `professional_id` desde el `user_id` del token.
                    user_id=ids_de_usuario[nombre],
                )
            )
        await session.flush()
        print(f"Profesionales: {len(prof_ids)}")

        # Gabriel y Daniel hacen todo; Jose solo corte. Asi "cualquier
        # profesional" da un resultado distinto del que daria si los tres
        # hicieran todo.
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
        print("Gabriel y Daniel: todos los servicios. Jose: solo corte.")

        # Horarios: L-V 08-12 y 17-21; sabado 09-13.
        for weekday in range(5):
            for idx, (ini, fin) in enumerate(FRANJAS_LABORABLES):
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
        for idx, (ini, fin) in enumerate(FRANJA_SABADO):
            session.add(
                BusinessHour(
                    business_id=business_id,
                    weekday=5,
                    window_index=idx,
                    is_open=True,
                    start_time=ini,
                    end_time=fin,
                )
            )
        await session.flush()
        print("Horarios: L-V 08:00-12:00 y 17:00-21:00; sabado 09:00-13:00")

        # Bloqueos de demostracion. `occupied_from`/`occupied_to` replican el rango
        # igual que `crear_bloqueo`: sin ellos el indice `ix_blocks_occupied_range`
        # no sirve y el motor de disponibilidad no los veria como huecos ocupados.
        tz = ZoneInfo("America/Argentina/Buenos_Aires")
        hoy_local = dt.datetime.now(tz).date()
        pid_por_nombre = {
            nombre: pid for (nombre, _), pid in zip(PROFESIONALES, prof_ids, strict=True)
        }
        for nombre, dias, inicio, fin, motivo in BLOQUEOS_DEMO:
            dia = hoy_local + dt.timedelta(days=dias)
            # `None` en `nombre` es un cierre de todo el negocio (professional_id
            # NULL); si no, el de un profesional concreto.
            prof_id = None if nombre is None else pid_por_nombre[nombre]
            session.add(
                Block(
                    business_id=business_id,
                    professional_id=prof_id,
                    kind=BlockKind.BLOCKED,
                    starts_at=dt.datetime.combine(dia, inicio, tzinfo=tz),
                    ends_at=dt.datetime.combine(dia, fin, tzinfo=tz),
                    occupied_from=dt.datetime.combine(dia, inicio, tzinfo=tz),
                    occupied_to=dt.datetime.combine(dia, fin, tzinfo=tz),
                    reason=motivo,
                    created_by_user_id=None,
                )
            )
        await session.flush()
        print(f"Bloqueos demo: {len(BLOQUEOS_DEMO)} (general + Gabriel)")

    await dispose_engine()

    print()
    print("Listo. Para probar:")
    print(f"  GET  http://localhost:8000/api/v1/public/businesses/{SLUG}")
    print(f"  POST http://localhost:8000/api/v1/auth/login  {ADMIN_EMAIL} / {PASSWORD}")
    for _, correo in PROFESIONAL_USERS:
        print(f"  POST http://localhost:8000/api/v1/auth/login  {correo} / {PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
