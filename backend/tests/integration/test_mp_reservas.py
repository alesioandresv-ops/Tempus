"""Tests de pagos de reserva via Mercado Pago (Fase 9.1): webhook `payment`.

El webhook de pago llega **sin** GUC de tenant: el negocio se resuelve con la
funcion SECURITY DEFINER `mercadopago_business_for_event` (0019) por
`payment_id` o `external_reference`, y despues el pago se aplica dentro de la
sesion del tenant. Un pago aprobado confirma el turno `pending_payment`; uno
rechazado lo libera.

1. **El calculo de monto** (politica `none`/`full`/seña fija/porcentual) es
   independiente de la base: se prueba como funcion pura.
2. **`approved` confirma la reserva** pendiente y guarda el `payment_id`.
3. **`rejected` cancela la reserva** y libera el horario.
4. **Idempotencia**: un reintento identico de MP no re-aplica (mismo
   `external_id` por hash del cuerpo).
5. **Pago ajeno** se marca `ignored`, sin tocar la reserva.

Ningun test llama a MP: `get_payment` se monkeypatchea, que es la unica forma
de correr la suite sin credenciales.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.core.encryption import encrypt_string
from app.db.session import TENANT_GUC
from app.models.enums import DepositMode, PaymentPolicy
from app.modules.payments.config import calcular_monto_servicio
from app.modules.payments.mercadopago import PaymentInfo
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

pytestmark = pytest.mark.integration

SLUG = "test-mp-reservas"
BUSINESS = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee13")
CUSTOMER = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee51")
SERVICE = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee52")
PROFESSIONAL = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee53")
ACCOUNT_ID = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee54")
BOOKING = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee5a")
BOOKING_PAYMENT = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee5b")
PAYMENT_ID = "pay_reserv_0001"
EXT_REF = "ext_reserv_0001"
TOKEN_MP = "APP_USR-reserva-pseudoAccessToken"

_BOOKING_SQL = """
INSERT INTO bookings (
    id, business_id, customer_id, professional_id, service_id, status, source,
    starts_at, ends_at, occupied_from, occupied_to, local_date,
    duration_minutes, price_snapshot, currency, secure_token_hash
)
VALUES (
    :id, :business_id, :customer_id, :professional_id, :service_id,
    'pending_payment', 'public',
    :starts_at, :ends_at, :occupied_from, :occupied_to, :local_date,
    30, 1000, 'ARS', :token_hash
)
ON CONFLICT (id) DO NOTHING
"""


@pytest_asyncio.fixture
async def reserva_mp(
    migration_database_url: str,
) -> AsyncIterator[None]:
    """Negocio aislado con cliente, servicio, profesional y una reserva `pending_payment`.

    El webhook commitea de verdad, asi que los estados quedan de una corrida para
    otra: la limpieza va en **setup** y devuelve la reserva a `pending_payment`
    con el pago `pending` y sin `payment_id`, listos para volver a notificar.
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
                    VALUES (:id, 'Reservas con Pago', :slug, 'America/Argentina/Buenos_Aires', 'active')
                    ON CONFLICT (slug) DO UPDATE SET status = 'active', created_at = now()
                    """
                ),
                {"id": BUSINESS, "slug": SLUG},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO customers (id, business_id, first_name, last_name, phone_e164)
                    VALUES (:id, :business_id, 'Carla', 'Diaz', '+54911000000r')
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": CUSTOMER, "business_id": BUSINESS},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO services (id, business_id, name, duration_minutes, price, currency)
                    VALUES (:id, :business_id, 'Corte y lavado', 30, 1000, 'ARS')
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": SERVICE, "business_id": BUSINESS},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO professionals (id, business_id, display_name)
                    VALUES (:id, :business_id, 'Profesional Reserva')
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": PROFESSIONAL, "business_id": BUSINESS},
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
            inicio = dt.datetime(2031, 1, 5, 10, 0, tzinfo=dt.UTC)
            fin = dt.datetime(2031, 1, 5, 10, 30, tzinfo=dt.UTC)
            await conn.execute(
                text(_BOOKING_SQL),
                {
                    "id": BOOKING,
                    "business_id": BUSINESS,
                    "customer_id": CUSTOMER,
                    "professional_id": PROFESSIONAL,
                    "service_id": SERVICE,
                    "starts_at": inicio,
                    "ends_at": fin,
                    "occupied_from": inicio,
                    "occupied_to": fin,
                    "local_date": dt.date(2031, 1, 5),
                    "token_hash": bytes(range(1, 33)),
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO booking_payments
                        (id, booking_id, business_id, policy, amount, currency,
                         external_reference, status)
                    VALUES (:id, :booking_id, :business_id, 'deposit', 1000, 'ARS',
                            :external_reference, 'pending')
                    ON CONFLICT (booking_id) DO NOTHING
                    """
                ),
                {
                    "id": BOOKING_PAYMENT,
                    "booking_id": BOOKING,
                    "business_id": BUSINESS,
                    "external_reference": EXT_REF,
                },
            )
            # Limpieza en setup: el webhook commitea de verdad.
            await conn.execute(
                text(
                    "UPDATE bookings SET status = 'pending_payment', cancelled_at = NULL "
                    "WHERE id = :id"
                ),
                {"id": BOOKING},
            )
            await conn.execute(
                text(
                    "UPDATE booking_payments SET payment_id = NULL, status = 'pending', "
                    "refunded_amount = 0, refunded_at = NULL, status_detail = NULL "
                    "WHERE booking_id = :id"
                ),
                {"id": BOOKING},
            )
            await conn.execute(
                text(
                    "DELETE FROM webhook_events WHERE provider = 'mercadopago' "
                    "AND external_id LIKE 'payment:pay_reserv_%'"
                )
            )
        yield
    finally:
        await mig_engine.dispose()


def _payload_pago(external_reference: str | None = EXT_REF) -> dict:
    data = {"id": PAYMENT_ID}
    if external_reference:
        data["external_reference"] = external_reference
    return {"type": "payment", "data": data}


async def _estado_pago(status: str) -> PaymentInfo:
    return PaymentInfo(
        payment_id=PAYMENT_ID,
        status=status,
        transaction_amount=1000.0,
        currency="ARS",
        external_reference=EXT_REF,
    )


async def test_calcular_monto_servicio_politicas() -> None:
    """El monto a cobrar sigue la politica: nada, todo, seña fija o porcentual."""
    sin_pago = calcular_monto_servicio(
        base_amount=5000.0,
        policy=PaymentPolicy.NONE,
        deposit_mode=None,
        deposit_value=None,
        deposit_non_refundable=False,
    )
    assert sin_pago == {
        "total": 5000.0,
        "deposit_amount": 0.0,
        "remaining": 5000.0,
        "non_refundable_deposit": 0.0,
    }

    completo = calcular_monto_servicio(
        base_amount=5000.0,
        policy=PaymentPolicy.FULL,
        deposit_mode=None,
        deposit_value=None,
        deposit_non_refundable=False,
    )
    assert completo["deposit_amount"] == 5000.0
    assert completo["remaining"] == 0.0

    fija = calcular_monto_servicio(
        base_amount=5000.0,
        policy=PaymentPolicy.DEPOSIT,
        deposit_mode=DepositMode.FIXED,
        deposit_value=1500.0,
        deposit_non_refundable=True,
    )
    assert fija["deposit_amount"] == 1500.0
    assert fija["remaining"] == 3500.0
    assert fija["non_refundable_deposit"] == 1500.0

    porcentual = calcular_monto_servicio(
        base_amount=2000.0,
        policy=PaymentPolicy.DEPOSIT,
        deposit_mode=DepositMode.PERCENT,
        deposit_value=10.0,
        deposit_non_refundable=False,
    )
    assert porcentual["deposit_amount"] == 200.0
    assert porcentual["remaining"] == 1800.0

    # Una senia porcentual nunca supera el total (la politica lo capa).
    exagerada = calcular_monto_servicio(
        base_amount=2000.0,
        policy=PaymentPolicy.DEPOSIT,
        deposit_mode=DepositMode.PERCENT,
        deposit_value=150.0,
        deposit_non_refundable=False,
    )
    assert exagerada["deposit_amount"] == 2000.0
    assert exagerada["remaining"] == 0.0


async def test_webhook_pago_aprobado_confirma_reserva(
    http_client: AsyncClient,
    session: AsyncSession,
    reserva_mp: None,
    set_tenant,
    monkeypatch,
) -> None:
    """`approved` confirma la reserva `pending_payment` y guarda el payment_id."""

    async def _consulta_pago(access_token: str, payment_id: str, **kwargs) -> PaymentInfo:
        assert access_token == TOKEN_MP
        assert payment_id == PAYMENT_ID
        return await _estado_pago("approved")

    monkeypatch.setattr("app.api.routers.webhooks.get_payment", _consulta_pago)

    respuesta = await http_client.post("/api/v1/webhooks/mercadopago", json=_payload_pago())
    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ok"
    assert respuesta.json()["resultado"]["reserva"] == "confirmada"

    await set_tenant(BUSINESS)
    estado_reserva = (
        await session.execute(
            text("SELECT status FROM bookings WHERE id = :b"), {"b": str(BOOKING)}
        )
    ).scalar_one()
    assert estado_reserva == "confirmed"

    fila = (
        await session.execute(
            text("SELECT payment_id, status FROM booking_payments WHERE booking_id = :b"),
            {"b": str(BOOKING)},
        )
    ).fetchone()
    assert fila is not None
    assert fila[0] == PAYMENT_ID
    assert fila[1] == "approved"


async def test_webhook_pago_rechazado_cancela_reserva(
    http_client: AsyncClient,
    session: AsyncSession,
    reserva_mp: None,
    set_tenant,
    monkeypatch,
) -> None:
    """Un pago rechazado no puede seguir bloqueando la agenda: se cancela el turno."""

    async def _consulta_pago(access_token: str, payment_id: str, **kwargs) -> PaymentInfo:
        return await _estado_pago("rejected")

    monkeypatch.setattr("app.api.routers.webhooks.get_payment", _consulta_pago)

    respuesta = await http_client.post("/api/v1/webhooks/mercadopago", json=_payload_pago())
    assert respuesta.status_code == 200
    assert respuesta.json()["resultado"]["reserva"] == "cancelada"

    await set_tenant(BUSINESS)
    estado_reserva = (
        await session.execute(
            text("SELECT status FROM bookings WHERE id = :b"), {"b": str(BOOKING)}
        )
    ).scalar_one()
    assert estado_reserva == "cancelled"

    estado_pago = (
        await session.execute(
            text("SELECT status FROM booking_payments WHERE booking_id = :b"),
            {"b": str(BOOKING)},
        )
    ).scalar_one()
    assert estado_pago == "rejected"


async def test_webhook_pago_duplicado_no_se_aplica_dos_veces(
    http_client: AsyncClient,
    session: AsyncSession,
    reserva_mp: None,
    set_tenant,
    monkeypatch,
) -> None:
    """Un reintento identico reusa el evento `PROCESSED`: no vuelve a aplicar."""
    llamadas = {"n": 0}

    async def _consulta_pago(access_token: str, payment_id: str, **kwargs) -> PaymentInfo:
        llamadas["n"] += 1
        return await _estado_pago("approved")

    monkeypatch.setattr("app.api.routers.webhooks.get_payment", _consulta_pago)

    payload = _payload_pago()
    primera = await http_client.post("/api/v1/webhooks/mercadopago", json=payload)
    segunda = await http_client.post("/api/v1/webhooks/mercadopago", json=payload)
    assert primera.status_code == 200
    assert segunda.status_code == 200
    assert llamadas["n"] == 1  # la segunda no consulto a MP de nuevo

    await set_tenant(BUSINESS)
    eventos = (
        await session.execute(
            text(
                "SELECT count(*) FROM webhook_events WHERE provider = 'mercadopago' "
                "AND external_id LIKE 'payment:pay_reserv_%'"
            )
        )
    ).scalar_one()
    assert eventos == 1


async def test_webhook_pago_ajeno_se_ignora(
    http_client: AsyncClient,
    session: AsyncSession,
    reserva_mp: None,
    set_tenant,
    monkeypatch,
) -> None:
    """Un pago que no es de este sistema se descarta sin tocar la reserva."""
    respuesta = await http_client.post(
        "/api/v1/webhooks/mercadopago",
        json={"type": "payment", "data": {"id": "pay_de_otra_app_0001"}},
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ok"

    await set_tenant(BUSINESS)
    estado_reserva = (
        await session.execute(
            text("SELECT status FROM bookings WHERE id = :b"), {"b": str(BOOKING)}
        )
    ).scalar_one()
    assert estado_reserva == "pending_payment"
    fila = (
        await session.execute(
            text("SELECT payment_id, status FROM booking_payments WHERE booking_id = :b"),
            {"b": str(BOOKING)},
        )
    ).fetchone()
    assert fila is not None
    assert fila[0] is None
    assert fila[1] == "pending"
