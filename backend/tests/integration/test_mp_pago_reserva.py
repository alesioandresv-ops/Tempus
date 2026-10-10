"""Tests del pago de reserva en el flujo publico (Fase 9.1b).

El endpoint `POST /public/bookings` consulta la politica del
negocio **antes** de crear la reserva: si cobra seña (o el
total), el turno nace `pending_payment`--sigue ocupando la
agenda, pero no es turno atendido--y se crea la preference de
Mercado Pago con el `external_reference` = id de la reserva.
Si no cobra, la reserva se confirma en el acto, como siempre.

1. **Con seña fija**: `pending_payment`, monto desglosado
   (seña / resto en el local) e `init_point` de checkout.
2. **Pago completo**: la seña es el total y no queda resto.
3. **Sin politica**: se confirma en el acto, sin `pago`.
4. **Sin cuenta de MP activa** (pero con politica de cobro):
   422, y no queda turno ocupado.
5. **Flujo completo**: crear con seña, webhook `approved`,
   reserva confirmada y recordatorios programados (el aviso
   de confirmacion es del pago, no de la creacion).

Ningun test llama a MP: `create_preference` y `get_payment`
se monkeypatchean, que es la unica forma de correr la suite
sin credenciales.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from app.core.encryption import encrypt_string
from app.db.session import TENANT_GUC
from app.modules.payments.mercadopago import PaymentInfo, PreferenceInfo
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

pytestmark = pytest.mark.integration

SLUG = "negocio-pago-mp"
BUSINESS = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee14")
CUSTOMER = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee61")
SERVICE = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee62")
PROFESSIONAL = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee63")
ACCOUNT_ID = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee64")
TOKEN_MP = "APP_USR-reserva-pseudoAccessToken"
TIMEZONE = "America/Argentina/Buenos_Aires"
PRECIO_SERVICIO = 1500.0
SEÑA_FIJA = 500.0


def _cuerpo_reserva(*, starts_at: dt.datetime, telefono: str) -> dict[str, str]:
    """Cuerpo del POST publico (el fin no se deduce: el schema lo exige)."""
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
    """Proximo lunes a `hora_local`, con margen de lead time y cancelacion."""
    tz = ZoneInfo(TIMEZONE)
    base = dt.datetime.now(tz).date() + dt.timedelta(days=30)
    lunes = base + dt.timedelta(days=(-base.weekday()) % 7)
    local = dt.datetime(lunes.year, lunes.month, lunes.day, hora_local, 0, tzinfo=tz)
    return local.astimezone(dt.UTC)


@pytest_asyncio.fixture
async def negocio_pago(
    migration_database_url: str,
) -> AsyncIterator[None]:
    """Negocio con agenda, servicio, profesional y cuenta de MP.

    La ruta publica commitea con sesion propia, asi que los turnos
    que crea cada test sobreviven al rollback: la limpieza va en
    **setup** (como en `test_public_token_flow`), y el estado de
    politica/cuenta vuelve al punto de partida en cada corrida.
    """
    mig_engine: AsyncEngine = create_async_engine(
        migration_database_url, poolclass=None, echo=False
    )
    try:
        async with mig_engine.begin() as conn:
            await conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                {"tenant": str(BUSINESS)},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO businesses (id, name, slug, timezone, status)
                    VALUES (:id, 'Negocio Pago MP', :slug, :tz, 'active')
                    ON CONFLICT (slug) DO UPDATE SET status = 'active'
                    """
                ),
                {"id": BUSINESS, "slug": SLUG, "tz": TIMEZONE},
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
                    VALUES (:id, :business, 'Corte', 30, 1500, 'ARS')
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
                    VALUES (:id, :business, 'Profe Pago', 1)
                    ON CONFLICT (id) DO UPDATE SET
                        display_name = EXCLUDED.display_name, sort_order = EXCLUDED.sort_order,
                        is_active = true
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
            await conn.execute(
                text(
                    """
                    INSERT INTO mp_payment_accounts (id, business_id, access_token_encrypted, is_active)
                    VALUES (:id, :business_id, :token, true)
                    ON CONFLICT (business_id) DO NOTHING
                    """
                ),
                {"id": ACCOUNT_ID, "business_id": BUSINESS, "token": encrypt_string(TOKEN_MP)},
            )
            # Estado de partida: seña fija de 500 sobre un servicio de 1500.
            await conn.execute(
                text(
                    "UPDATE businesses SET payment_policy = 'deposit', "
                    "deposit_mode = 'fixed', deposit_value = 500, "
                    "deposit_non_refundable = false WHERE id = :b"
                ),
                {"b": str(BUSINESS)},
            )
            # Limpieza en setup: la ruta publica commitea de verdad.
            # `notification_requests` va por FK en cascada con `bookings`.
            await conn.execute(
                text("DELETE FROM booking_payments WHERE business_id = :b"),
                {"b": str(BUSINESS)},
            )
            await conn.execute(
                text("DELETE FROM bookings WHERE business_id = :b"),
                {"b": str(BUSINESS)},
            )
            await conn.execute(
                text("DELETE FROM customers WHERE business_id = :b"),
                {"b": str(BUSINESS)},
            )
            await conn.execute(
                text("UPDATE mp_payment_accounts SET is_active = true WHERE business_id = :b"),
                {"b": str(BUSINESS)},
            )
            await conn.execute(
                text(
                    "DELETE FROM webhook_events WHERE provider = 'mercadopago' "
                    "AND external_id LIKE 'payment:pay_e2e_%'"
                )
            )
        yield
    finally:
        await mig_engine.dispose()


async def _politica(migration_database_url: str, politica: str) -> None:
    """Pisa la politica del negocio (fija, completa o ninguna)."""
    motor: AsyncEngine = create_async_engine(migration_database_url, poolclass=None, echo=False)
    try:
        async with motor.begin() as conn:
            await conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :t, true)"),
                {"t": str(BUSINESS)},
            )
            await conn.execute(
                text(
                    "UPDATE businesses SET payment_policy = :p, "
                    "deposit_mode = NULL, deposit_value = NULL WHERE id = :b"
                ),
                {"p": politica, "b": str(BUSINESS)},
            )
    finally:
        await motor.dispose()


async def _apagar_cuenta(migration_database_url: str) -> None:
    motor: AsyncEngine = create_async_engine(migration_database_url, poolclass=None, echo=False)
    try:
        async with motor.begin() as conn:
            await conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :t, true)"),
                {"t": str(BUSINESS)},
            )
            await conn.execute(
                text("UPDATE mp_payment_accounts SET is_active = false WHERE business_id = :b"),
                {"b": str(BUSINESS)},
            )
    finally:
        await motor.dispose()


async def _crear_preferencia_falsa(access_token: str, **kwargs) -> PreferenceInfo:
    assert access_token == TOKEN_MP
    assert kwargs["external_reference"]
    return PreferenceInfo(
        preference_id="pref_pago_0001",
        init_point="https://www.mercadopago.com.ar/pay-checkout",
        sandbox_init_point="https://sandbox.mercadopago.com.ar/pay-checkout",
    )


async def test_reserva_publica_con_seña_nace_pendiente_de_pago(
    http_client: AsyncClient,
    session: AsyncSession,
    negocio_pago: None,
    set_tenant,
    migration_database_url: str,
    monkeypatch,
) -> None:
    """Seña fija: turno `pending_payment` con desglose e init_point."""
    monkeypatch.setattr("app.modules.payments.reservas.create_preference", _crear_preferencia_falsa)
    telefono = "+5491100000901"
    respuesta = await http_client.post(
        "/api/v1/public/bookings",
        json=_cuerpo_reserva(starts_at=_proximo_lunes(9), telefono=telefono),
    )
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "pending_payment"

    pago = cuerpo["pago"]
    assert pago is not None
    assert pago["status"] == "pending"
    assert pago["policy"] == "deposit"
    assert pago["deposit_mode"] == "fixed"
    assert pago["deposit_amount"] == SEÑA_FIJA
    assert pago["remaining"] == PRECIO_SERVICIO - SEÑA_FIJA
    assert pago["preference_id"] == "pref_pago_0001"
    # La cuenta sembrada es sandbox: el init_point es el de sandbox.
    assert pago["init_point"] == "https://sandbox.mercadopago.com.ar/pay-checkout"

    # La reserva y su pago quedaron committed por la ruta publica.
    await set_tenant(BUSINESS)
    fila_reserva = (
        await session.execute(
            text("SELECT id, status, currency FROM bookings WHERE business_id = :b"),
            {"b": str(BUSINESS)},
        )
    ).fetchone()
    assert fila_reserva is not None
    assert fila_reserva[1] == "pending_payment"

    fila_pago = (
        await session.execute(
            text(
                "SELECT policy, deposit_mode, amount, external_reference, "
                "preference_id FROM booking_payments WHERE business_id = :b"
            ),
            {"b": str(BUSINESS)},
        )
    ).fetchone()
    assert fila_pago is not None
    assert fila_pago[0] == "deposit"
    assert fila_pago[1] == "fixed"
    assert float(fila_pago[2]) == SEÑA_FIJA
    assert fila_pago[3] == str(fila_reserva[0])  # external_reference = reserva
    assert fila_pago[4] == "pref_pago_0001"


async def test_reserva_publica_con_pago_completo(
    http_client: AsyncClient,
    migration_database_url: str,
    negocio_pago: None,
    monkeypatch,
) -> None:
    """Politica `full`: la seña es el total y no queda resto en el local."""
    await _politica(migration_database_url, "full")
    monkeypatch.setattr("app.modules.payments.reservas.create_preference", _crear_preferencia_falsa)
    respuesta = await http_client.post(
        "/api/v1/public/bookings",
        json=_cuerpo_reserva(starts_at=_proximo_lunes(10), telefono="+5491100000902"),
    )
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "pending_payment"
    pago = cuerpo["pago"]
    assert pago is not None
    assert pago["policy"] == "full"
    assert pago["deposit_amount"] == PRECIO_SERVICIO
    assert pago["remaining"] == 0.0


async def test_reserva_publica_sin_politica_confirma_en_el_acto(
    http_client: AsyncClient,
    migration_database_url: str,
    negocio_pago: None,
    monkeypatch,
) -> None:
    """Sin politica de cobro, la reserva se confirma como siempre."""
    await _politica(migration_database_url, "none")
    monkeypatch.setattr("app.modules.payments.reservas.create_preference", _crear_preferencia_falsa)
    respuesta = await http_client.post(
        "/api/v1/public/bookings",
        json=_cuerpo_reserva(starts_at=_proximo_lunes(11), telefono="+5491100000903"),
    )
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "confirmed"
    assert cuerpo["pago"] is None


async def test_reserva_publica_con_seña_sin_cuenta_mp_es_422(
    http_client: AsyncClient,
    migration_database_url: str,
    negocio_pago: None,
    monkeypatch,
) -> None:
    """Politica de cobro sin cuenta activa: 422 y sin turno ocupado."""
    await _apagar_cuenta(migration_database_url)
    monkeypatch.setattr("app.modules.payments.reservas.create_preference", _crear_preferencia_falsa)
    respuesta = await http_client.post(
        "/api/v1/public/bookings",
        json=_cuerpo_reserva(starts_at=_proximo_lunes(16), telefono="+5491100000904"),
    )
    assert respuesta.status_code == 422
    assert "mercado pago" in respuesta.json()["detail"].lower()


async def test_flujo_completo_crear_pagar_confirmar(
    http_client: AsyncClient,
    session: AsyncSession,
    negocio_pago: None,
    set_tenant,
    monkeypatch,
) -> None:
    """Crear con seña, pagar por webhook: se confirma y se avisa.

    El aviso de "turno confirmado" no va en la creacion (el turno
    todavia no estaba pagado): lo programa el webhook cuando el
    pago es aprobado.
    """
    monkeypatch.setattr("app.modules.payments.reservas.create_preference", _crear_preferencia_falsa)
    telefono = "+5491100000905"
    creada = await http_client.post(
        "/api/v1/public/bookings",
        json=_cuerpo_reserva(starts_at=_proximo_lunes(9), telefono=telefono),
    )
    assert creada.status_code == 201, creada.text
    booking_id = creada.json()["booking_id"]

    async def _consulta_pago(access_token: str, payment_id: str, **kwargs) -> PaymentInfo:
        assert access_token == TOKEN_MP
        return PaymentInfo(
            payment_id="pay_e2e_0001",
            status="approved",
            transaction_amount=SEÑA_FIJA,
            currency="ARS",
            external_reference=booking_id,
        )

    monkeypatch.setattr("app.api.routers.webhooks.get_payment", _consulta_pago)
    webhook = await http_client.post(
        "/api/v1/webhooks/mercadopago",
        json={
            "type": "payment",
            "data": {"id": "pay_e2e_0001", "external_reference": booking_id},
        },
    )
    assert webhook.status_code == 200, webhook.text

    await set_tenant(BUSINESS)
    estado = (
        await session.execute(text("SELECT status FROM bookings WHERE id = :b"), {"b": booking_id})
    ).scalar_one()
    assert estado == "confirmed"

    # El pago aprobado programa la confirmacion y los dos recordatorios
    # (el turno es a +30 dias: los dos momentos estan en el futuro).
    recordatorios = (
        (
            await session.execute(
                text("SELECT kind FROM notification_requests WHERE booking_id = :b ORDER BY kind"),
                {"b": booking_id},
            )
        )
        .scalars()
        .all()
    )
    assert recordatorios == [
        "booking_confirmed",
        "reminder_24h",
        "reminder_2h",
    ]

    pago = (
        await session.execute(
            text("SELECT status, payment_id FROM booking_payments WHERE booking_id = :b"),
            {"b": booking_id},
        )
    ).fetchone()
    assert pago is not None
    assert pago[0] == "approved"
    assert pago[1] == "pay_e2e_0001"
