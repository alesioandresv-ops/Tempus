"""Tests de suscripciones Mercado Pago (Fase 9.1): activar Pro y el webhook.

El plan pasa a `pro` **por webhook**, nunca en la activacion: el endpoint del
panel crea el preapproval (devuelve el `init_point`) y deja la suscripcion en
`pending`; cuando MP confirma (`authorized`/`active`), el webhook sube el plan.

1. **Estado de la suscripcion**: `pending` mientras el webhook no confirme, y
   `none` si nunca se activo Pro (criterio del servicio, no un pendiente).
2. **Sin cuenta ni precio configurado la activacion es 422** con el motivo.
3. **Activacion feliz**: crea el preapproval pendiente y devuelve el init_point.
4. **Firma del webhook**: con secreto configurado, un webhook sin firma (o con
   firma mala) es 400; con firma valida entra.
5. **Webhook `authorized`** sube el negocio a `pro` y la suscripcion a `active`.
6. **Webhook de un preapproval ajeno** se marca `ignored`, sin tocar nada.

Ningun test llama a MP: `create_preapproval` y `get_preapproval` se
monkeypatchean, que es la unica forma de correr la suite sin credenciales.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.core.config import get_settings
from app.core.encryption import encrypt_string
from app.db.session import TENANT_GUC
from app.modules.auth.scopes import Scope
from app.modules.auth.tokens import create_access_token
from app.modules.payments.mercadopago import PreapprovalInfo
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

pytestmark = pytest.mark.integration

SLUG = "test-mp-subscriptions"
BUSINESS = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee12")
USER = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee22")
ACCOUNT_ID = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee32")
SUBSCRIPTION_ID = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee42")
PREAPPROVAL_ID = "pre_susc_0000000001"
TOKEN_MP = "APP_USR-susc-pseudoAccessToken-0001"
PRECIO = 15000.0
SIGNO_SECRETO = "s3creto-de-prueba-123456"


@pytest_asyncio.fixture
async def negocio_mp(
    migration_database_url: str,
) -> AsyncIterator[None]:
    """Negocio, cuenta y suscripcion `pending` sembradas con el rol de DDL.

    El webhook aplica con session propia (commitea de verdad), asi que el estado
    deja de ser el del seed entre corridas: la limpieza va en **setup** para que
    cada corrida arranque con cuenta lista, suscripcion `pending` y plan `free`.
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
                    VALUES (:id, 'Peluqueria Pro', :slug, 'America/Argentina/Buenos_Aires', 'active')
                    ON CONFLICT (slug) DO UPDATE SET status = 'active'
                    """
                ),
                {"id": BUSINESS, "slug": SLUG},
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
            await conn.execute(
                text(
                    """
                    INSERT INTO mp_subscriptions
                        (id, business_id, preapproval_id, plan, status, amount, currency)
                    VALUES (:id, :business_id, :preapproval_id, 'free', 'pending', 15000, 'ARS')
                    ON CONFLICT (business_id) DO NOTHING
                    """
                ),
                {
                    "id": SUBSCRIPTION_ID,
                    "business_id": BUSINESS,
                    "preapproval_id": PREAPPROVAL_ID,
                },
            )
            # Limpieza en setup: el webhook commitea de verdad, asi que el estado
            # vuelve al punto de partida en cada corrida.
            await conn.execute(
                text(
                    "UPDATE mp_subscriptions SET status = 'pending', plan = 'free' "
                    "WHERE business_id = :b"
                ),
                {"b": str(BUSINESS)},
            )
            await conn.execute(
                text("UPDATE businesses SET plan = 'free' WHERE id = :b"),
                {"b": str(BUSINESS)},
            )
            await conn.execute(
                text(
                    "DELETE FROM webhook_events WHERE provider = 'mercadopago' "
                    "AND external_id LIKE 'preapproval:pre_susc_%'"
                )
            )
        yield
    finally:
        await mig_engine.dispose()


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _token() -> str:
    return create_access_token(
        user_id=USER,
        business_id=BUSINESS,
        role="ADMIN",
        scopes=[Scope.BUSINESS_CONFIG_READ, Scope.BUSINESS_CONFIG_WRITE],
    ).token


def _configurar_precio(monkeypatch: pytest.MonkeyPatch, precio: str = "15000") -> None:
    monkeypatch.setenv("MERCADOPAGO_PRO_PLAN_PRICE", precio)
    get_settings.cache_clear()


async def test_estado_suscripcion_sin_fila_es_none(
    http_client: AsyncClient,
    migration_database_url: str,
    negocio_mp: None,
    set_tenant,
) -> None:
    """Sin suscripcion nunca hubo `pending` ni `cancelled`: es `none`."""
    motor: AsyncEngine = create_async_engine(migration_database_url, poolclass=None, echo=False)
    try:
        async with motor.begin() as conn:
            await conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :t, true)"), {"t": str(BUSINESS)}
            )
            await conn.execute(
                text("DELETE FROM mp_subscriptions WHERE business_id = :b"),
                {"b": str(BUSINESS)},
            )
    finally:
        await motor.dispose()

    await set_tenant(BUSINESS)
    respuesta = await http_client.get(
        "/api/v1/business/suscripcion", headers=_auth_header(_token())
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["plan"] == "free"
    assert cuerpo["status"] == "none"
    assert cuerpo["preapproval_id"] is None


async def test_estado_suscripcion_pendiente_con_plan_free(
    http_client: AsyncClient,
    negocio_mp: None,
    set_tenant,
) -> None:
    """La suscripcion pendiente se ve como tal: el plan recien sube por webhook."""
    await set_tenant(BUSINESS)
    respuesta = await http_client.get(
        "/api/v1/business/suscripcion", headers=_auth_header(_token())
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["plan"] == "free"
    assert cuerpo["status"] == "pending"
    assert cuerpo["preapproval_id"] == PREAPPROVAL_ID


async def test_activar_sin_cuenta_es_422(
    http_client: AsyncClient,
    negocio_mp: None,
    set_tenant,
    monkeypatch,
) -> None:
    """Sin cuenta activa el preapproval no se puede crear: 422 con el motivo.

    La cuenta sembrada se apaga desde el propio panel: con eso se llega al mismo
    estado que un negocio que nunca conecto, sin tocar la base por otra via.
    """
    _configurar_precio(monkeypatch)
    await set_tenant(BUSINESS)
    apagada = await http_client.put(
        "/api/v1/business/config/payments",
        headers=_auth_header(_token()),
        json={"activo": False, "access_token": None},
    )
    assert apagada.status_code == 200

    respuesta = await http_client.post(
        "/api/v1/business/suscripcion/activar",
        headers=_auth_header(_token()),
        json={"back_url": "https://panel.tempus.app/plan"},
    )
    assert respuesta.status_code == 422
    assert "mercado pago" in respuesta.json()["detail"].lower()


async def test_activar_sin_precio_configurado_es_422(
    http_client: AsyncClient,
    negocio_mp: None,
    set_tenant,
    monkeypatch,
) -> None:
    """Sin `MERCADOPAGO_PRO_PLAN_PRICE` la activacion dice exactamente eso."""
    monkeypatch.delenv("MERCADOPAGO_PRO_PLAN_PRICE", raising=False)
    get_settings.cache_clear()
    await set_tenant(BUSINESS)
    respuesta = await http_client.post(
        "/api/v1/business/suscripcion/activar",
        headers=_auth_header(_token()),
        json={"back_url": "https://panel.tempus.app/plan"},
    )
    assert respuesta.status_code == 422
    assert "MERCADOPAGO_PRO_PLAN_PRICE" in respuesta.json()["detail"]


async def test_activar_crea_preapproval_pendiente(
    http_client: AsyncClient,
    session: AsyncSession,
    negocio_mp: None,
    set_tenant,
    monkeypatch,
) -> None:
    """La activacion crea el preapproval `pending` y devuelve el init_point.

    El plan **no** cambia aca: recien el webhook `authorized` lo sube a `pro`.
    """
    _configurar_precio(monkeypatch)

    async def _crea_preapproval(access_token: str, **kwargs) -> PreapprovalInfo:
        assert access_token == TOKEN_MP
        assert kwargs["frequency_type"] == "months"
        assert kwargs["transaction_amount"] == PRECIO
        return PreapprovalInfo(
            preapproval_id=PREAPPROVAL_ID,
            init_point="https://www.mercadopago.com.ar/pay-suscripcion",
            status="pending",
            next_billing_at=None,
        )

    monkeypatch.setattr("app.modules.payments.subscriptions.create_preapproval", _crea_preapproval)

    await set_tenant(BUSINESS)
    respuesta = await http_client.post(
        "/api/v1/business/suscripcion/activar",
        headers=_auth_header(_token()),
        json={"back_url": "https://panel.tempus.app/plan", "frequency_type": "months"},
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["preapproval_id"] == PREAPPROVAL_ID
    assert cuerpo["init_point"].startswith("https://")
    assert cuerpo["transaction_amount"] == PRECIO
    assert cuerpo["status"] == "pending"

    fila = (
        await session.execute(
            text(
                "SELECT preapproval_id, status, amount FROM mp_subscriptions WHERE business_id = :b"
            ),
            {"b": str(BUSINESS)},
        )
    ).fetchone()
    assert fila is not None
    assert fila[0] == PREAPPROVAL_ID
    assert fila[1] == "pending"
    assert float(fila[2]) == PRECIO

    plan = (
        await session.execute(
            text("SELECT plan FROM businesses WHERE id = :b"), {"b": str(BUSINESS)}
        )
    ).scalar_one()
    assert plan == "free"  # el webhook todavia no confirmo


async def test_webhook_sin_firma_se_rechaza_cuando_hay_secret(
    http_client: AsyncClient,
    negocio_mp: None,
    set_tenant,
    monkeypatch,
) -> None:
    """Con secreto configurado, un webhook sin `x-signature` es 400."""
    monkeypatch.setenv("MERCADOPAGO_WEBHOOK_SECRET", SIGNO_SECRETO)
    get_settings.cache_clear()
    respuesta = await http_client.post(
        "/api/v1/webhooks/mercadopago",
        json={"type": "preapproval", "data": {"id": PREAPPROVAL_ID}},
    )
    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == "Firma inválida"


async def test_webhook_preapproval_authorized_activa_pro(
    http_client: AsyncClient,
    session: AsyncSession,
    negocio_mp: None,
    set_tenant,
    monkeypatch,
) -> None:
    """El webhook `authorized` sube el plan a `pro` y la suscripcion a `active`."""

    async def _estado_preapproval(
        access_token: str, preapproval_id: str, **kwargs
    ) -> PreapprovalInfo:
        assert access_token == TOKEN_MP
        assert preapproval_id == PREAPPROVAL_ID
        return PreapprovalInfo(
            preapproval_id=PREAPPROVAL_ID,
            init_point="https://www.mercadopago.com.ar/pay-suscripcion",
            status="authorized",
            next_billing_at="2031-02-05T10:00:00+00:00",
        )

    monkeypatch.setattr("app.api.routers.webhooks.get_preapproval", _estado_preapproval)

    respuesta = await http_client.post(
        "/api/v1/webhooks/mercadopago",
        json={"type": "preapproval", "data": {"id": PREAPPROVAL_ID}},
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ok"

    # El plan vive en `businesses` y fue commiteado por la sesion del webhook: la
    # sesion del test, en READ COMMITTED, ve la fila nueva en la sentencia siguiente.
    await set_tenant(BUSINESS)
    plan = (
        await session.execute(
            text("SELECT plan FROM businesses WHERE id = :b"), {"b": str(BUSINESS)}
        )
    ).scalar_one()
    assert plan == "pro"

    fila = (
        await session.execute(
            text("SELECT status, next_billing_at FROM mp_subscriptions WHERE business_id = :b"),
            {"b": str(BUSINESS)},
        )
    ).fetchone()
    assert fila is not None
    assert fila[0] == "active"
    assert fila[1] is not None


async def test_webhook_preapproval_con_firma_valida_entra(
    http_client: AsyncClient,
    session: AsyncSession,
    negocio_mp: None,
    set_tenant,
    monkeypatch,
) -> None:
    """Con la firma correcta (manifest id/request-id/ts) el webhook se procesa."""
    monkeypatch.setenv("MERCADOPAGO_WEBHOOK_SECRET", SIGNO_SECRETO)
    get_settings.cache_clear()

    async def _estado_preapproval(
        access_token: str, preapproval_id: str, **kwargs
    ) -> PreapprovalInfo:
        return PreapprovalInfo(
            preapproval_id=PREAPPROVAL_ID,
            init_point="https://www.mercadopago.com.ar/pay-suscripcion",
            status="active",
            next_billing_at=None,
        )

    monkeypatch.setattr("app.api.routers.webhooks.get_preapproval", _estado_preapproval)

    payload = {"type": "preapproval", "data": {"id": PREAPPROVAL_ID}}
    request_id = "req-test-0001"
    ts = "1700000000"
    manifest = f"id:{PREAPPROVAL_ID};request-id:{request_id};ts:{ts};"
    v1 = hmac.new(SIGNO_SECRETO.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    respuesta = await http_client.post(
        "/api/v1/webhooks/mercadopago",
        json=payload,
        headers={
            "x-signature": f"ts={ts},v1={v1}",
            "x-request-id": request_id,
        },
    )
    assert respuesta.status_code == 200

    await set_tenant(BUSINESS)
    plan = (
        await session.execute(
            text("SELECT plan FROM businesses WHERE id = :b"), {"b": str(BUSINESS)}
        )
    ).scalar_one()
    assert plan == "pro"


async def test_webhook_preapproval_ajeno_se_ignora(
    http_client: AsyncClient,
    session: AsyncSession,
    negocio_mp: None,
    set_tenant,
    monkeypatch,
) -> None:
    """Un preapproval que no es de este sistema se descarta, no falla ni toca nada."""
    respuesta = await http_client.post(
        "/api/v1/webhooks/mercadopago",
        json={"type": "preapproval", "data": {"id": "pre_de_otra_app_0001"}},
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ok"

    await set_tenant(BUSINESS)
    plan = (
        await session.execute(
            text("SELECT plan FROM businesses WHERE id = :b"), {"b": str(BUSINESS)}
        )
    ).scalar_one()
    assert plan == "free"
    estado = (
        await session.execute(
            text("SELECT status FROM mp_subscriptions WHERE business_id = :b"),
            {"b": str(BUSINESS)},
        )
    ).scalar_one()
    assert estado == "pending"
