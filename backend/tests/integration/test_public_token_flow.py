"""Flujo publico completo por secure token, via HTTP real.

Cubre el camino que nadie cubria: `GET /public/bookings/{token}`, `/cancel` y
`/reschedule` resuelven el tenant con `booking_tenant_for_token` (SECURITY
DEFINER) **antes** de que exista el GUC. La gestion por token se ejercitaba
desde el service, con la sesion del test ya bajo GUC, y ninguna pasada por la
funcion real. Ese hueco levanto el bug de `0017`: la funcion corria como
`tempus_owner`--sujeto a la RLS forzada de `bookings`--y sin GUC resolvia cero
filas, asi que las tres rutas devolvian 404 "Reserva no encontrada" a una
reserva que existia.

Este test siembra un negocio, crea la reserva por la ruta publica (la unica que
conoce el token) y la gestiona por las tres rutas de token. Si la funcion de
resolucion volviera a perder el dueno BYPASSRLS, el 404 reaparece aca.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from app.db.session import TENANT_GUC
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)

pytestmark = pytest.mark.integration

SLUG = "negocio-token-flow"
BUSINESS = uuid.UUID("d3d3d3d3-d3d3-7d3d-8d3d-d3d3d3d3d301")
SERVICE = uuid.UUID("d3d3d3d3-d3d3-7d3d-8d3d-d3d3d3d3d302")
PROFESSIONAL = uuid.UUID("d3d3d3d3-d3d3-7d3d-8d3d-d3d3d3d3d303")

TIMEZONE = "America/Argentina/Buenos_Aires"


def _cuerpo_reserva(*, starts_at: dt.datetime, telefono: str) -> dict[str, str]:
    """Cuerpo del POST publico. El fin no se deduce: el esquema lo exige y el
    motor lo revalida contra la duracion real del servicio."""
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


def _proximo_lunes(hora_local: int) -> dt.datetime:
    """Proximo lunes a las `hora_local` del negocio, con +30 dias de margen.

    Los `min_lead_minutes` del negocio sembrado son 0, pero el margen deja el
    slot lejos de cualquier ventana de cancelacion y de otros inserts de la
    corrida."""
    tz = ZoneInfo(TIMEZONE)
    base = dt.datetime.now(tz).date() + dt.timedelta(days=30)
    lunes = base + dt.timedelta(days=(-base.weekday()) % 7)
    local = dt.datetime(lunes.year, lunes.month, lunes.day, hora_local, 0, tzinfo=tz)
    return local.astimezone(dt.UTC)


async def _sembrar_migration(conn: AsyncConnection) -> None:
    # `businesses` tiene RLS forzada tambien sobre el rol de migraciones, y la
    # rama `DO UPDATE` del upsert cae en `businesses_write` (GUC = id). El GUC
    # va antes de la sentencia, en el mismo `begin()`.
    await conn.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(BUSINESS)},
    )
    await conn.execute(
        text(
            """
            INSERT INTO businesses (id, name, slug, timezone, status)
            VALUES (:id, 'Negocio Token Flow', :slug, :tz, 'active')
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

    await conn.execute(text(f"SELECT set_config('{TENANT_GUC}', '', true)"))


@pytest_asyncio.fixture
async def negocio_token(
    migration_database_url: str,
    test_database_url: str,
) -> AsyncIterator[str]:
    # El negocio se siembra con el rol de migraciones (la app no puede insertar
    # en `businesses`). La limpieza de sobras va en **setup**: la ruta publica
    # commitea con sesion propia, asi que los turnos que crea cada test
    # sobreviven al rollback y se acumulan de corrida en corrida.
    mig_engine: AsyncEngine = create_async_engine(
        migration_database_url, poolclass=None, echo=False
    )
    app_engine: AsyncEngine = create_async_engine(test_database_url, poolclass=None, echo=False)
    try:
        async with mig_engine.begin() as conn:
            await _sembrar_migration(conn)
        async with mig_engine.begin() as conn:
            await conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                {"tenant": str(BUSINESS)},
            )
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


async def _crear_reserva(http_client: AsyncClient, telefono: str) -> dict:
    resp = await http_client.post(
        "/api/v1/public/bookings",
        json=_cuerpo_reserva(starts_at=_proximo_lunes(9), telefono=telefono),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestPublicTokenFlow:
    async def test_leer_reserva_por_token(
        self, http_client: AsyncClient, negocio_token: str
    ) -> None:
        """GET por token resuelve la reserva que la ruta publica acaba de hacer.

        El rescate de la respuesta es `BookingResponse`: trae `booking_id` y
        `secure_token`, no `id`.
        """
        creada = await _crear_reserva(http_client, "+54911000001")
        token = creada["secure_token"]

        resp = await http_client.get(f"/api/v1/public/bookings/{token}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "confirmed"
        assert data["secure_token"] == token
        assert data["booking_id"] == creada["booking_id"]
        assert data["service_name"] == "Corte"
        assert data["professional_name"] == "Profe"

    async def test_cancelar_por_token(self, http_client: AsyncClient, negocio_token: str) -> None:
        creada = await _crear_reserva(http_client, "+54911000002")
        token = creada["secure_token"]

        resp = await http_client.post(f"/api/v1/public/bookings/{token}/cancel")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cancelled"

        # La cancelacion persiste: el GET por token la vuelve a leer.
        resp = await http_client.get(f"/api/v1/public/bookings/{token}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cancelled"

    async def test_reprogramar_por_token(
        self, http_client: AsyncClient, negocio_token: str
    ) -> None:
        creada = await _crear_reserva(http_client, "+54911000003")
        token = creada["secure_token"]

        nuevo = _proximo_lunes(10)
        body = {
            "new_starts_at": nuevo.isoformat().replace("+00:00", "Z"),
            "new_ends_at": (nuevo + dt.timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
            "new_duration_minutes": 30,
        }
        resp = await http_client.post(f"/api/v1/public/bookings/{token}/reschedule", json=body)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "confirmed"
        assert data["starts_at"] == body["new_starts_at"]

        # El nuevo horario persiste para el mismo token.
        resp = await http_client.get(f"/api/v1/public/bookings/{token}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "confirmed"
        assert resp.json()["starts_at"] == body["new_starts_at"]
