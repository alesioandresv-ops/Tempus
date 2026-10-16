"""Tests del panel de pagos (Fase 9.1): cuenta de Mercado Pago y politica de seña.

Cubre los cuatro endpoints de `/business/config/payments*`: ver y guardar la
cuenta, y ver y guardar la politica de pago. Son operaciones locales, sin
ninguna llamada a Mercado Pago: la cuenta se guarda cifrada con Fernet y el
panel solo ve la mascara del token.

1. **Default sin fila**: cuenta inactiva, sin token, politica `none`.
2. **Guardar cifra el token** en reposo y devuelve solo los ultimos caracteres.
3. **Crear sin token es 422**; **apagar conserva el token** para poder volver a
   encender sin pegar el secreto otra vez.
4. **Staff ve pero no escribe**: leer es `BUSINESS_CONFIG_READ`, escribir exige
   `BUSINESS_CONFIG_WRITE`.
5. **La politica valida su coherencia**: `deposit` exige modo y valor, y un
   porcentaje no pasa de 100.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.core.encryption import decrypt_string
from app.db.session import TENANT_GUC
from app.modules.auth.scopes import Scope
from app.modules.auth.tokens import create_access_token
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

pytestmark = pytest.mark.integration

SLUG = "test-mp-accounts"
BUSINESS = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee11")
USER = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee21")
TOKEN_MP = "APP_USR-1a2b3c4d5e6f-pseudoAccessToken"
PUBLIC_KEY_MP = "APP_PUB-abcdef0123456789"
MASK_SUFFIX = TOKEN_MP[-4:]


@pytest_asyncio.fixture
async def negocio_mp(
    migration_database_url: str,
) -> AsyncIterator[None]:
    """El negocio de este archivo, con limpieza en setup.

    Los endpoints del panel usan la sesion del test (se revierte sola), pero la
    limpieza va en **setup** igual que en `test_whatsapp_connect`: es barata y
    hace el archivo idempotente aunque una corrida anterior haya dejado filas de
    un test que corto a mitad.
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
                    VALUES (:id, 'Peluqueria Pagos', :slug, 'America/Argentina/Buenos_Aires', 'active')
                    ON CONFLICT (slug) DO UPDATE SET status = 'active', created_at = now()
                    """
                ),
                {"id": BUSINESS, "slug": SLUG},
            )
            await conn.execute(
                text("DELETE FROM mp_payment_accounts WHERE business_id = :b"),
                {"b": str(BUSINESS)},
            )
            await conn.execute(
                text(
                    "UPDATE businesses SET plan = 'free', payment_policy = 'none', "
                    "deposit_mode = NULL, deposit_value = NULL, deposit_non_refundable = false "
                    "WHERE id = :b"
                ),
                {"b": str(BUSINESS)},
            )
        yield
    finally:
        await mig_engine.dispose()


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _token(role: str = "ADMIN") -> str:
    scopes = {
        "ADMIN": [Scope.BUSINESS_CONFIG_READ, Scope.BUSINESS_CONFIG_WRITE],
        "STAFF": [Scope.TEAM_READ, Scope.BUSINESS_CONFIG_READ],
    }[role]
    return create_access_token(
        user_id=USER,
        business_id=BUSINESS,
        role=role,
        scopes=scopes,
    ).token


async def test_config_pagos_default_inactiva(
    http_client: AsyncClient,
    negocio_mp: None,
    set_tenant,
) -> None:
    """Sin fila en `mp_payment_accounts` el GET responde el default inactivo."""
    await set_tenant(BUSINESS)
    respuesta = await http_client.get(
        "/api/v1/business/config/payments", headers=_auth_header(_token())
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["activo"] is False
    assert cuerpo["has_token"] is False
    assert cuerpo["token_masked"] is None


async def test_guardar_config_pagos_cifra_el_token(
    http_client: AsyncClient,
    session: AsyncSession,
    negocio_mp: None,
    set_tenant,
) -> None:
    """Guardar credenciales: el token duerme cifrado y el panel ve la mascara."""
    await set_tenant(BUSINESS)
    respuesta = await http_client.put(
        "/api/v1/business/config/payments",
        headers=_auth_header(_token()),
        json={
            "activo": True,
            "access_token": TOKEN_MP,
            "public_key": PUBLIC_KEY_MP,
            "sandbox": False,
        },
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["activo"] is True
    assert cuerpo["has_token"] is True
    assert cuerpo["token_masked"] is not None
    assert cuerpo["token_masked"].endswith(MASK_SUFFIX)
    assert TOKEN_MP not in respuesta.text  # el secreto no viaja de vuelta

    fila = (
        await session.execute(
            text(
                "SELECT access_token_encrypted, public_key, sandbox, is_active "
                "FROM mp_payment_accounts WHERE business_id = :b"
            ),
            {"b": str(BUSINESS)},
        )
    ).fetchone()
    assert fila is not None
    assert fila[0] != TOKEN_MP  # cifrado en reposo
    assert decrypt_string(fila[0]) == TOKEN_MP
    assert fila[1] == PUBLIC_KEY_MP
    assert fila[2] is False
    assert fila[3] is True


async def test_guardar_config_pagos_sin_token_al_crear_es_422(
    http_client: AsyncClient,
    negocio_mp: None,
    set_tenant,
) -> None:
    """Crear la cuenta requiere el access token: sin el, 422 con el motivo."""
    await set_tenant(BUSINESS)
    respuesta = await http_client.put(
        "/api/v1/business/config/payments",
        headers=_auth_header(_token()),
        json={"activo": True, "access_token": None},
    )
    assert respuesta.status_code == 422
    assert "access token" in respuesta.json()["detail"].lower()


async def test_apagar_cuenta_conserva_el_token(
    http_client: AsyncClient,
    session: AsyncSession,
    negocio_mp: None,
    set_tenant,
) -> None:
    """Apagar sin token conserva el secreto: reencender es un click, no re-pegar."""
    await set_tenant(BUSINESS)
    primera = await http_client.put(
        "/api/v1/business/config/payments",
        headers=_auth_header(_token()),
        json={"activo": True, "access_token": TOKEN_MP},
    )
    assert primera.status_code == 200

    apagada = await http_client.put(
        "/api/v1/business/config/payments",
        headers=_auth_header(_token()),
        json={"activo": False, "access_token": None},
    )
    assert apagada.status_code == 200
    assert apagada.json()["activo"] is False
    assert apagada.json()["has_token"] is True  # el token sigue guardado

    fila = (
        await session.execute(
            text(
                "SELECT access_token_encrypted, is_active "
                "FROM mp_payment_accounts WHERE business_id = :b"
            ),
            {"b": str(BUSINESS)},
        )
    ).fetchone()
    assert fila is not None
    assert decrypt_string(fila[0]) == TOKEN_MP
    assert fila[1] is False


async def test_politica_default_none(
    http_client: AsyncClient,
    negocio_mp: None,
    set_tenant,
) -> None:
    """Sin configurar, el negocio no exige pago: politica `none`."""
    await set_tenant(BUSINESS)
    respuesta = await http_client.get(
        "/api/v1/business/config/payments/policy", headers=_auth_header(_token())
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["policy"] == "none"
    assert cuerpo["deposit_mode"] is None
    assert cuerpo["deposit_value"] is None
    assert cuerpo["deposit_non_refundable"] is False


async def test_guardar_politica_deposit_fija(
    http_client: AsyncClient,
    session: AsyncSession,
    negocio_mp: None,
    set_tenant,
) -> None:
    """`deposit` con modo `fixed` se guarda tal cual, y se ve de vuelta."""
    await set_tenant(BUSINESS)
    respuesta = await http_client.put(
        "/api/v1/business/config/payments/policy",
        headers=_auth_header(_token()),
        json={
            "policy": "deposit",
            "deposit_mode": "fixed",
            "deposit_value": 15000,
            "deposit_non_refundable": True,
        },
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["policy"] == "deposit"
    assert cuerpo["deposit_mode"] == "fixed"
    assert cuerpo["deposit_value"] == 15000.0
    assert cuerpo["deposit_non_refundable"] is True

    fila = (
        await session.execute(
            text(
                "SELECT payment_policy, deposit_mode, deposit_value, "
                "deposit_non_refundable FROM businesses WHERE id = :b"
            ),
            {"b": str(BUSINESS)},
        )
    ).fetchone()
    assert fila is not None
    assert fila[0] == "deposit"
    assert fila[1] == "fixed"
    assert float(fila[2]) == 15000.0
    assert fila[3] is True


async def test_guardar_politica_porcentaje_mayor_a_100_es_422(
    http_client: AsyncClient,
    negocio_mp: None,
    set_tenant,
) -> None:
    """Una seña porcentual no puede superar el 100%."""
    await set_tenant(BUSINESS)
    respuesta = await http_client.put(
        "/api/v1/business/config/payments/policy",
        headers=_auth_header(_token()),
        json={"policy": "deposit", "deposit_mode": "percent", "deposit_value": 101},
    )
    assert respuesta.status_code == 422
    assert "100" in respuesta.json()["detail"]


async def test_staff_ve_pero_no_escribe(
    http_client: AsyncClient,
    negocio_mp: None,
    set_tenant,
) -> None:
    """Leer configuracion de pagos es de cualquier miembro; escribir exige el scope."""
    await set_tenant(BUSINESS)

    vista = await http_client.get(
        "/api/v1/business/config/payments", headers=_auth_header(_token("STAFF"))
    )
    assert vista.status_code == 200

    respuesta = await http_client.put(
        "/api/v1/business/config/payments",
        headers=_auth_header(_token("STAFF")),
        json={"activo": True, "access_token": TOKEN_MP},
    )
    assert respuesta.status_code == 403
