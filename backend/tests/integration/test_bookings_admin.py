"""Tests de integración para el panel admin de reservas (Fase 7)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from app.db.session import TENANT_GUC
from app.models.enums import BookingEventType
from app.modules.auth.scopes import Scope
from app.modules.auth.tokens import create_access_token
from app.modules.bookings.admin import MAX_LIMITE
from app.modules.notifications.models import NotificationRequest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

pytestmark = pytest.mark.integration

SLUG = "negocio-admin-bookings"
BUSINESS = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeee002")
SERVICE = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeee003")
PROFESSIONAL = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeee004")
CUSTOMER = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeee005")
#: Miembro del panel. El token del fixture `admin_token` lleva este id a
#: proposito: cada walk-in escribe `created_by_user_id` (FK a `business_users`),
#: y un id inventado reventaria el flush con un IntegrityError que no dice nada.
USER = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeee001")

TIMEZONE = "America/Argentina/Buenos_Aires"


def _cuerpo_reserva(*, starts_at: dt.datetime, telefono: str) -> dict[str, str]:
    """Cuerpo canonico del POST publico, con lo que `BookingCreateRequest` pide.

    La ruta es `/public/bookings` y el `slug` va en el cuerpo--no hay
    `/businesses/{slug}/bookings`. Y el fin **no se deduce** del servicio en
    el request: el esquema exige `ends_at` y `local_date` explicitos, que es
    lo que el motor vuelve a validar contra la duracion real.
    """
    tz = ZoneInfo(TIMEZONE)
    return {
        "slug": SLUG,
        "service_id": str(SERVICE),
        "professional_id": str(PROFESSIONAL),
        "customer_first_name": "Ana",
        "customer_last_name": "Perez",
        "customer_phone_e164": telefono,
        "starts_at": starts_at.isoformat().replace("+00:00", "Z"),
        "ends_at": (starts_at + dt.timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
        "local_date": starts_at.astimezone(tz).date().isoformat(),
    }


async def _sembrar_migration(conn: AsyncConnection) -> None:
    # `businesses` es global pero `FORCE RLS` tambien sobre el dueno, y la
    # rama `DO UPDATE` del upsert de abajo cae en `businesses_write`, que
    # exige que el GUC coincida con el negocio. El `set_config` va en
    # local, asi que no se filtra del `begin()` de este fixture.
    await conn.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(BUSINESS)},
    )
    # `status` es parte de la prueba y no del detalle: `booking_futuro`
    # se crea por la ruta **publica**, que solo resuelve negocios
    # `active` (default de la columna: `trial`, que no se publica).
    await conn.execute(
        text(
            """
            INSERT INTO businesses (id, name, slug, timezone, status)
            VALUES (:id, 'Negocio Admin Bookings', :slug, :tz, 'active')
            ON CONFLICT (slug) DO UPDATE SET status = 'active'
            """
        ),
        {"id": BUSINESS, "slug": SLUG, "tz": TIMEZONE},
    )


async def _sembrar_app(conn: AsyncConnection) -> None:
    await conn.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(BUSINESS)},
    )

    # Horarios Lun-Vie 09:00-12:00 y 16:00-20:00
    for weekday in range(5):
        for indice, (inicio, fin) in enumerate(
            ((dt.time(9, 0), dt.time(12, 0)), (dt.time(16, 0), dt.time(20, 0)))
        ):
            await conn.execute(
                text(
                    """
                    INSERT INTO business_hours
                        (business_id, weekday, window_index, start_time, end_time, is_open)
                    VALUES (:business, :weekday, :indice, :inicio, :fin, true)
                    ON CONFLICT (business_id, weekday, window_index) DO UPDATE SET
                        start_time = EXCLUDED.start_time,
                        end_time = EXCLUDED.end_time,
                        is_open = true
                    """
                ),
                {
                    "business": BUSINESS,
                    "weekday": weekday,
                    "indice": indice,
                    "inicio": inicio,
                    "fin": fin,
                },
            )

    # Servicio
    await conn.execute(
        text(
            """
            INSERT INTO services (id, business_id, name, duration_minutes, price, currency)
            VALUES (:id, :business, 'Corte', 30, 1500.00, 'ARS')
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, duration_minutes = EXCLUDED.duration_minutes,
                price = EXCLUDED.price, currency = EXCLUDED.currency, is_active = true
            """
        ),
        {"id": SERVICE, "business": BUSINESS},
    )

    # Profesional
    await conn.execute(
        text(
            """
            INSERT INTO professionals (id, business_id, display_name, sort_order)
            VALUES (:id, :business, 'Profe', 1)
            ON CONFLICT (id) DO UPDATE SET
                display_name = EXCLUDED.display_name, sort_order = EXCLUDED.sort_order, is_active = true
            """
        ),
        {"id": PROFESSIONAL, "business": BUSINESS},
    )

    # Asignación servicio-profesional
    await conn.execute(
        text(
            """
            INSERT INTO professional_services (professional_id, service_id, business_id, is_active)
            VALUES (:prof, :serv, :business, true)
            ON CONFLICT (professional_id, service_id) DO UPDATE SET is_active = true
            """
        ),
        {"prof": PROFESSIONAL, "serv": SERVICE, "business": BUSINESS},
    )

    # Cliente
    await conn.execute(
        text(
            """
            INSERT INTO customers (id, business_id, first_name, last_name, phone_e164)
            VALUES (:id, :business, 'Ana', 'Perez', '+54911000000')
            ON CONFLICT (id) DO UPDATE SET first_name=EXCLUDED.first_name, last_name=EXCLUDED.last_name
            """
        ),
        {"id": CUSTOMER, "business": BUSINESS},
    )

    # Miembro del panel, con id fijo: es el `user_id` del token de
    # `admin_token`, y los walk-in lo referencian por FK al escribir
    # `created_by_user_id`.
    await conn.execute(
        text(
            """
            INSERT INTO business_users (id, business_id, email, password_hash, full_name, role, status)
            VALUES (:id, :business, 'admin@tempus.test', 'x', 'Admin', 'admin', 'active')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": USER, "business": BUSINESS},
    )

    await conn.execute(text(f"SELECT set_config('{TENANT_GUC}', '', true)"))


@pytest_asyncio.fixture
async def negocio_admin(
    migration_database_url: str,
    test_database_url: str,
) -> AsyncIterator[str]:
    mig_engine: AsyncEngine = create_async_engine(
        migration_database_url, poolclass=None, echo=False
    )
    app_engine: AsyncEngine = create_async_engine(test_database_url, poolclass=None, echo=False)
    try:
        async with mig_engine.begin() as conn:
            await _sembrar_migration(conn)
        # Limpieza en **setup** y no en teardown: la ruta publica commitea de
        # verdad--sesion propia, no la del test--asi que los turnos que crea
        # `booking_futuro` sobreviven al rollback y se acumulan de corrida en
        # corrida. Sin esto, el segundo test que reserva el mismo slot en la
        # misma corrida choca con la EXCLUDE de la corrida anterior y responde
        # 409 por un motivo ajeno a lo que prueba.
        #
        # Va **antes** del seed de app: si las sobras quedaran hasta despues,
        # el cliente que la ruta publica creo para el mismo telefono reventaria
        # el upsert del seed con la unicidad `(business_id, phone_e164)`.
        # En teardown no se puede: todavia hay filas del test sin deshacer, y el
        # `DELETE` se quedaria esperando sus locks hasta el timeout.
        async with mig_engine.begin() as conn:
            await conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                {"tenant": str(BUSINESS)},
            )
            # `notification_requests` se va por cascade en `bookings`.
            await conn.execute(
                text("DELETE FROM bookings WHERE business_id = :b"),
                {"b": str(BUSINESS)},
            )
            await conn.execute(
                text("DELETE FROM customers WHERE business_id = :b"),
                {"b": str(BUSINESS)},
            )
        async with app_engine.begin() as conn:
            await _sembrar_app(conn)
        yield SLUG
    finally:
        await app_engine.dispose()
        await mig_engine.dispose()


@pytest_asyncio.fixture
async def admin_token(negocio_admin: str) -> str:
    """Token de panel, emitido con la API real en vez de armado a mano.

    `create_access_token` recibe los claim y arma el payload el solo; el
    `Principal` no se construye aca porque es lo que **decodifica** el token,
    no lo que lo emite. `require_principal` no toca `business_users`, asi que
    no hace falta sembrar un miembro para que autentique: si lo que se
    quisiera probar fuera el login, seria otro test.
    """
    token = create_access_token(
        user_id=USER,
        business_id=BUSINESS,
        role="ADMIN",
        scopes=[
            Scope.BOOKINGS_READ_ANY,
            Scope.BOOKINGS_WRITE_ANY,
            Scope.BOOKINGS_WALKIN,
            Scope.BUSINESS_CONFIG_READ,
            Scope.BUSINESS_CONFIG_WRITE,
        ],
    )
    return token.token


@pytest_asyncio.fixture
async def booking_futuro(
    http_client: AsyncClient, negocio_admin: str, admin_token: str
) -> uuid.UUID:
    tz = ZoneInfo(TIMEZONE)
    # Próximo lunes 09:00 local
    base = dt.datetime.now(ZoneInfo(TIMEZONE)).date() + dt.timedelta(days=30)
    lunes = base + dt.timedelta(days=(-base.weekday()) % 7)
    local = dt.datetime(lunes.year, lunes.month, lunes.day, 9, 0, tzinfo=tz)
    starts_at = local.astimezone(dt.UTC)

    resp = await http_client.post(
        "/api/v1/public/bookings",
        json=_cuerpo_reserva(starts_at=starts_at, telefono="+54911000000"),
    )
    assert resp.status_code == 201, resp.text
    # La respuesta publica es `BookingResponse`, que trae `booking_id` y
    # no `id`: `ReservaOut` (el del panel) es el que usa `id`, y mezclar
    # los dos es como se llega a un `KeyError` que no dice nada.
    return uuid.UUID(resp.json()["booking_id"])


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestBookingsAdmin:
    async def test_listado_limite_max(
        self,
        http_client: AsyncClient,
        negocio_admin: str,
        admin_token: str,
        booking_futuro: uuid.UUID,
    ) -> None:
        """El maximo de la pagina se acepta y se devuelve tal cual.

        El esquema del query capa `limite` con `le=200` (el `MAX_LIMITE` del
        servicio): pedir 1000 es un 422 de validacion, no un corte silencioso.
        Lo que el endpoint si acepta hasta el borde es el maximo, y lo devuelve
        en la respuesta para que el cliente sepa cual es la pagina real.
        """
        resp = await http_client.get(
            "/api/v1/business/reservas",
            headers=_auth_header(admin_token),
            params={"limite": MAX_LIMITE},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["limite"] == MAX_LIMITE

    async def test_filtro_por_local_date(
        self,
        http_client: AsyncClient,
        negocio_admin: str,
        admin_token: str,
        booking_futuro: uuid.UUID,
        set_tenant,
    ) -> None:
        # El panel lee por la sesion del test: sin el GUC no ve ni la reserva
        # que `booking_futuro` acaba de cerrar (RLS devuelve cero filas).
        await set_tenant(BUSINESS)

        # Cargar booking creado
        resp = await http_client.get(
            "/api/v1/business/reservas",
            headers=_auth_header(admin_token),
        )
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert len(items) >= 1
        # Leer directo: el id del panel sale del recurso, no del listado.
        br = await http_client.get(
            f"/api/v1/business/reservas/{booking_futuro}",
            headers=_auth_header(admin_token),
        )
        assert br.status_code == 200, br.text
        booking = br.json()
        ld = dt.date.fromisoformat(booking["local_date"])

        # Filtro correcto
        resp = await http_client.get(
            "/api/v1/business/reservas",
            headers=_auth_header(admin_token),
            params={"desde": ld.isoformat(), "hasta": ld.isoformat()},
        )
        assert resp.status_code == 200
        assert len(resp.json()["items"]) >= 1

        # Filtro incorrecto
        otro = (ld + dt.timedelta(days=2)).isoformat()
        resp = await http_client.get(
            "/api/v1/business/reservas",
            headers=_auth_header(admin_token),
            params={"desde": otro, "hasta": otro},
        )
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 0

    async def test_cancelar_booking_ya_pasado_da_409(
        self,
        http_client: AsyncClient,
        session: AsyncSession,
        negocio_admin: str,
        admin_token: str,
        set_tenant,
    ) -> None:
        """Cancelar un turno que ya empezo da 409, no lo cancela.

        La ruta publica no deja reservar en el pasado (`min_lead_minutes`),
        asi que el turno pasado no se crea asi nomas: se reserva uno futuro y
        se atrasa la hora en la transaccion del test, que es la misma desde la
        que el panel va a leer el estado.
        """
        await set_tenant(BUSINESS)

        tz = ZoneInfo(TIMEZONE)
        base = dt.datetime.now(ZoneInfo(TIMEZONE)).date() + dt.timedelta(days=30)
        lunes = base + dt.timedelta(days=(-base.weekday()) % 7)
        local = dt.datetime(lunes.year, lunes.month, lunes.day, 9, 0, tzinfo=tz)

        resp = await http_client.post(
            "/api/v1/public/bookings",
            json=_cuerpo_reserva(starts_at=local.astimezone(dt.UTC), telefono="+54911999999"),
        )
        assert resp.status_code == 201, resp.text
        bid = uuid.UUID(resp.json()["booking_id"])

        ayer = dt.datetime.now(ZoneInfo(TIMEZONE)).date() - dt.timedelta(days=1)
        pasado = dt.datetime(ayer.year, ayer.month, ayer.day, 9, 0, tzinfo=tz).astimezone(dt.UTC)
        await session.execute(
            text(
                "UPDATE bookings SET starts_at = :inicio, ends_at = :fin, "
                "occupied_from = :inicio, occupied_to = :fin WHERE id = :bid"
            ),
            {
                "inicio": pasado,
                "fin": pasado + dt.timedelta(minutes=30),
                "bid": bid,
            },
        )
        await session.flush()

        # Intentar cancelar
        resp = await http_client.post(
            f"/api/v1/business/reservas/{bid}/cancelar",
            headers=_auth_header(admin_token),
            json={"motivo": "test"},
        )
        assert resp.status_code == 409, resp.text

    async def test_completar_antes_de_fin_da_409(
        self,
        http_client: AsyncClient,
        negocio_admin: str,
        admin_token: str,
        booking_futuro: uuid.UUID,
        set_tenant,
    ) -> None:
        await set_tenant(BUSINESS)
        resp = await http_client.post(
            f"/api/v1/business/reservas/{booking_futuro}/estado",
            headers=_auth_header(admin_token),
            json={"estado": "completed"},
        )
        assert resp.status_code == 409, resp.text

    async def test_walkin_mismo_slot_doble_409(
        self, http_client: AsyncClient, negocio_admin: str, admin_token: str, set_tenant
    ) -> None:
        # El walk-in lee el servicio por la sesion del test: sin el GUC, la RLS
        # devuelve cero filas y responde "Servicio no encontrado" (404) en vez
        # de llegar a la EXCLUDE que este test quiere probar.
        await set_tenant(BUSINESS)

        tz = ZoneInfo(TIMEZONE)
        base = dt.datetime.now(ZoneInfo(TIMEZONE)).date() + dt.timedelta(days=30)
        lunes = base + dt.timedelta(days=(-base.weekday()) % 7)
        local = dt.datetime(lunes.year, lunes.month, lunes.day, 10, 0, tzinfo=tz)
        starts_at = local.astimezone(dt.UTC)

        r1 = await http_client.post(
            "/api/v1/business/reservas/walkin",
            headers=_auth_header(admin_token),
            json={
                "service_id": str(SERVICE),
                "professional_id": str(PROFESSIONAL),
                "starts_at": starts_at.isoformat().replace("+00:00", "Z"),
                "customer_first_name": "Marta",
                "customer_last_name": "Diaz",
                "customer_phone_e164": "+54911888888",
            },
        )
        assert r1.status_code == 201, r1.text

        r2 = await http_client.post(
            "/api/v1/business/reservas/walkin",
            headers=_auth_header(admin_token),
            json={
                "service_id": str(SERVICE),
                "professional_id": str(PROFESSIONAL),
                "starts_at": starts_at.isoformat().replace("+00:00", "Z"),
                "customer_first_name": "Marta",
                "customer_last_name": "Diaz",
                "customer_phone_e164": "+54911888888",
            },
        )
        assert r2.status_code == 409, r2.text

    async def test_walkin_no_genera_notificaciones(
        self,
        http_client: AsyncClient,
        negocio_admin: str,
        admin_token: str,
        test_database_url: str,
        set_tenant,
    ) -> None:
        await set_tenant(BUSINESS)

        tz = ZoneInfo(TIMEZONE)
        base = dt.datetime.now(ZoneInfo(TIMEZONE)).date() + dt.timedelta(days=30)
        lunes = base + dt.timedelta(days=(-base.weekday()) % 7)
        local = dt.datetime(lunes.year, lunes.month, lunes.day, 11, 0, tzinfo=tz)
        starts_at = local.astimezone(dt.UTC)

        before = await self._count_notif(test_database_url, BUSINESS)
        r = await http_client.post(
            "/api/v1/business/reservas/walkin",
            headers=_auth_header(admin_token),
            json={
                "service_id": str(SERVICE),
                "professional_id": str(PROFESSIONAL),
                "starts_at": starts_at.isoformat().replace("+00:00", "Z"),
                "customer_first_name": "Sofi",
                "customer_last_name": "Lopez",
                "customer_phone_e164": "+54911777777",
            },
        )
        assert r.status_code == 201, r.text
        after = await self._count_notif(test_database_url, BUSINESS)
        assert after == before

        bid = uuid.UUID(r.json()["id"])
        ev = await http_client.get(
            f"/api/v1/business/reservas/{bid}/eventos",
            headers=_auth_header(admin_token),
        )
        assert ev.status_code == 200
        eventos = ev.json()
        assert any(e["type"] == BookingEventType.CREATED.value for e in eventos)

    async def _count_notif(self, test_database_url: str, business_id: uuid.UUID) -> int:
        """Cuenta `notification_requests` del negocio, comprometidas a la base.

        La cuenta va por una conexion aparte y con el GUC puesto: sin el, la RLS
        devuelve cero para **cualquier** negocio y el test no distinguiria un
        walk-in que no notifica de un bug que borra filas.
        """
        engine = create_async_engine(test_database_url, poolclass=None)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :t, true)"),
                    {"t": str(business_id)},
                )
                res = await conn.execute(select(func.count()).select_from(NotificationRequest))
                return int(res.scalar() or 0)
        finally:
            await engine.dispose()
