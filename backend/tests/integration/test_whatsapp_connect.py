"""Tests del flujo de Conectar WhatsApp (Embedded Signup, Fase 0.5).

Lo que se prueba aca es el endpoint `POST /business/config/whatsapp/connect`,
con la base de por medio y **sin** llamar a Meta: el intercambio de code y la
lectura de WABAs se monkeypatchean, porque sin la app de Meta real (y sin el
chip para registrar el numero) no hay llamada HTTP posible.

1. **Sin app de Meta configurada** la plataforma responde 422 con un mensaje
   claro: el flujo esta apagado de la misma forma que los demas features sin
   configuracion, no falla con un 500 raro.
2. **Code invalido**: Meta rechaza el intercambio y el endpoint traduce el
   error de dominio a un 422 honesto, sin filtrar el detalle de Meta.
3. **Sin WABA**: el usuario no tiene cuenta de WhatsApp Business, y el
   endpoint responde `sin_waba` con la guia de crearla. Nada se guarda.
4. **Connect feliz sin numero**: devuelve el WABA, guarda la conexion como
   `pending` e inactiva (la outbox la saltea), con el token **cifrado** en la
   base. Es el caso real de esta etapa, bloqueado por la falta del chip.
5. **Reconnect**: repetir el connect sobre el mismo WABA actualiza la misma
   fila, no duplica; y refrescar el token de una conexion **activa** no la
   apaga.
6. **Aislamiento**: staff ve la configuracion pero no puede conectar.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.core.config import get_settings
from app.core.encryption import decrypt_string
from app.db.session import TENANT_GUC
from app.models.enums import WhatsAppConnectionStatus
from app.modules.auth.scopes import Scope
from app.modules.auth.tokens import create_access_token
from app.modules.notifications import meta_signup
from app.modules.notifications.config import TEMPLATE_2H_DEFAULT, TEMPLATE_24H_DEFAULT
from app.modules.notifications.meta_signup import TokenInfo, WabaInfo
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

pytestmark = pytest.mark.integration

SLUG = "test-whatsapp-connect"
BUSINESS_WS = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee01")
USER_WS = uuid.UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeee02")
WABA_WS = "102290129340398"
WABA_DISPLAY = "+54 9 11 5555-0199"
PHONE_NUMBER_WS = "112233445566778899"
TOKEN_META = "EAAGm0-pseudoUserTokenFase05-000001"
TOKEN_META_2 = "EAAGm0-pseudoUserTokenFase05-000002"


@pytest_asyncio.fixture
async def negocio_connect(
    migration_database_url: str, test_database_url: str
) -> AsyncIterator[None]:
    """El negocio de este archivo: solo `businesses`, que es lo que toca el connect.

    A diferencia del fixture de los recordatorios no necesita servicio ni
    profesional: el endpoint escribe una unica fila en `whatsapp_connections`.
    La limpieza va en **setup** por la misma razon que en `negocio_admin`: un
    connect de una corrida anterior sobrevive al rollback del test (la sesion
    del handler no es la del test) y hay que arrancar limpio.
    """
    mig_engine: AsyncEngine = create_async_engine(
        migration_database_url, poolclass=None, echo=False
    )
    try:
        async with mig_engine.begin() as conn:
            await conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                {"tenant": str(BUSINESS_WS)},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO businesses (id, name, slug, timezone, status)
                    VALUES (:id, 'Peluqueria Connect', :slug, 'America/Argentina/Buenos_Aires', 'active')
                    ON CONFLICT (slug) DO UPDATE SET status = 'active'
                    """
                ),
                {"id": BUSINESS_WS, "slug": SLUG},
            )
            # Un connect de una corrida pasada persiste de verdad (la sesion del
            # handler commitea); borrarlo aca garantiza el estado inicial.
            await conn.execute(
                text("DELETE FROM whatsapp_connections WHERE business_id = :b"),
                {"b": str(BUSINESS_WS)},
            )
        yield
    finally:
        await mig_engine.dispose()


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _token(role: str = "ADMIN") -> str:
    """Token del dueno del negocio con los scopes de configuracion."""
    scopes = {
        "ADMIN": [
            Scope.BUSINESS_CONFIG_READ,
            Scope.BUSINESS_CONFIG_WRITE,
        ],
        "STAFF": [
            Scope.TEAM_READ,
            Scope.BUSINESS_CONFIG_READ,
        ],
    }[role]
    return create_access_token(
        user_id=USER_WS,
        business_id=BUSINESS_WS,
        role=role,
        scopes=scopes,
    ).token


def _waba_fake(
    *,
    waba_id: str = WABA_WS,
    display_phone: str | None = WABA_DISPLAY,
    phone_number_id: str | None = None,
    quality_rating: str = "GREEN",
    tier: str = "TIER_1K",
) -> WabaInfo:
    return WabaInfo(
        waba_id=waba_id,
        name="WhatsApp Business Tempus",
        display_phone=display_phone,
        quality_rating=quality_rating,
        messaging_limit_tier=tier,
        phone_number_id=phone_number_id,
    )


async def _configurar_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deja la app de Meta "configurada" para el endpoint.

    El endpoint lee `get_settings()` en cada request; con el cache limpio y
    las variables en el entorno, la configuracion que ve es la que el test
    quiere, sin tocar `.env.testing`.
    """
    monkeypatch.setenv("META_APP_ID", "123456789012345")
    monkeypatch.setenv("META_APP_SECRET", "meta-app-secret-de-prueba")
    get_settings.cache_clear()


async def test_connect_sin_app_meta_configurada_es_422(
    http_client: AsyncClient,
    negocio_connect: None,
    set_tenant,
) -> None:
    """La plataforma sin META_APP_ID/META_APP_SECRET tiene el flujo apagado.

    Lo dice con un 422 y un mensaje que apunta a la configuracion, no con un
    500: falta algo del lado de la plataforma, no del pedido.
    """
    await set_tenant(BUSINESS_WS)
    respuesta = await http_client.post(
        "/api/v1/business/config/whatsapp/connect",
        headers=_auth_header(_token()),
        json={"code": "AQBcCodeDeUnSoloUso-000001"},
    )
    assert respuesta.status_code == 422
    assert "META_APP_ID" in respuesta.json()["detail"]


async def test_connect_code_rechazado_es_422_honesto(
    http_client: AsyncClient,
    negocio_connect: None,
    set_tenant,
    monkeypatch,
) -> None:
    """Meta rechaza el code (vencido, usado, invalido): 422 con mensaje propio.

    El detalle de Meta (`{"error": {...}}`) no viaja al cliente: el endpoint
    lo traduce a un mensaje de dominio. Tampoco queda guardada ninguna
    conexion.
    """
    await _configurar_meta(monkeypatch)

    async def _rechaza(*args, **kwargs):
        raise meta_signup.MetaSignupError("Meta rechazo el code. Volve a intentar el flujo.")

    monkeypatch.setattr(meta_signup, "intercambiar_code", _rechaza)

    await set_tenant(BUSINESS_WS)
    respuesta = await http_client.post(
        "/api/v1/business/config/whatsapp/connect",
        headers=_auth_header(_token()),
        json={"code": "AQBcCodeDeUnSoloUso-000002"},
    )
    assert respuesta.status_code == 422
    assert "Meta" in respuesta.json()["detail"]


async def test_connect_usuario_sin_waba_responde_sin_waba(
    http_client: AsyncClient,
    session: AsyncSession,
    negocio_connect: None,
    set_tenant,
    monkeypatch,
) -> None:
    """El usuario de Meta no tiene WABA: guia para crearlo y nada guardado.

    El token se obtuvo, pero sin WABA no hay nada que persistir: la tabla de
    conexion sigue vacia (el estado no puede ser "conectado" sin cuenta).
    """
    await _configurar_meta(monkeypatch)

    async def _intercambia(code: str, redirect_uri: str | None = None, **kwargs) -> TokenInfo:
        return TokenInfo(access_token=TOKEN_META, expires_in=5184000, token_type="bearer")

    async def _sin_wabas(access_token: str, **kwargs) -> list[WabaInfo]:
        assert access_token == TOKEN_META
        return []

    monkeypatch.setattr(meta_signup, "intercambiar_code", _intercambia)
    monkeypatch.setattr(meta_signup, "listar_wabas", _sin_wabas)

    await set_tenant(BUSINESS_WS)
    respuesta = await http_client.post(
        "/api/v1/business/config/whatsapp/connect",
        headers=_auth_header(_token()),
        json={"code": "AQBcCodeDeUnSoloUso-000003"},
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "sin_waba"
    assert cuerpo["waba_id"] is None
    assert cuerpo["activo"] is False

    filas = (
        await session.execute(
            text("SELECT count(*) AS n FROM whatsapp_connections WHERE business_id = :b"),
            {"b": str(BUSINESS_WS)},
        )
    ).scalar_one()
    assert filas == 0


async def test_connect_feliz_guarda_pending_con_token_cifrado(
    http_client: AsyncClient,
    session: AsyncSession,
    negocio_connect: None,
    set_tenant,
    monkeypatch,
) -> None:
    """El connect feliz de esta etapa: WABA sin numero, conexion `pending`.

    Es el caso real bloqueado por el chip: el WABA no tiene numeros
    (`phone_number_id=None`), la conexion queda `pending` e inactiva --la
    outbox la saltea-- y el token duerme cifrado con Fernet. El cuerpo de la
    respuesta dice que falta registrar el numero, sin filtrar el token.
    """
    await _configurar_meta(monkeypatch)

    async def _intercambia(code: str, redirect_uri: str | None = None, **kwargs) -> TokenInfo:
        assert redirect_uri is None or redirect_uri.startswith("https://")
        return TokenInfo(access_token=TOKEN_META, expires_in=5184000, token_type="bearer")

    async def _waba(waba_id: str, **kwargs):
        return [_waba_fake(phone_number_id=None)]

    monkeypatch.setattr(meta_signup, "intercambiar_code", _intercambia)
    monkeypatch.setattr(meta_signup, "listar_wabas", _waba)

    await set_tenant(BUSINESS_WS)
    respuesta = await http_client.post(
        "/api/v1/business/config/whatsapp/connect",
        headers=_auth_header(_token()),
        json={
            "code": "AQBcCodeDeUnSoloUso-000004",
            "redirect_uri": "https://panel.tempus.app/admin",
        },
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "connected"
    assert cuerpo["waba_id"] == WABA_WS
    assert cuerpo["waba_display_phone"] == WABA_DISPLAY
    assert cuerpo["phone_number_id"] is None
    assert cuerpo["activo"] is False
    assert "chip" in cuerpo["mensaje"]

    # En la base: una fila, pendiente, inactiva, token cifrado.
    fila = (
        await session.execute(
            text(
                "SELECT phone_number_id, waba_id, access_token_encrypted, "
                "is_active, status FROM whatsapp_connections WHERE business_id = :b"
            ),
            {"b": str(BUSINESS_WS)},
        )
    ).fetchone()
    assert fila is not None
    assert fila[0] == ""  # phone_number_id vacio: sin numero registrado
    assert fila[1] == WABA_WS
    assert fila[2] != TOKEN_META  # cifrado en reposo
    assert decrypt_string(fila[2]) == TOKEN_META
    assert fila[3] is False  # is_active: la outbox la saltea
    assert fila[4] == WhatsAppConnectionStatus.PENDING.value


async def test_connect_reconectar_no_duplica_y_respeta_activa(
    http_client: AsyncClient,
    session: AsyncSession,
    negocio_connect: None,
    set_tenant,
    monkeypatch,
) -> None:
    """Reconnect: misma fila, y una conexion activa no se apaga.

    Primero el connect feliz (el WABA ya tiene numero), despues el negocio
    activa por el PUT --el camino de Fase D-3-- y un segundo connect solo
    refresca el token: la fila sigue siendo una, y `is_active` y el status
    `active` sobreviven al reconnect.
    """
    await _configurar_meta(monkeypatch)

    async def _intercambia(code: str, redirect_uri: str | None = None, **kwargs) -> TokenInfo:
        token = TOKEN_META_2 if code.endswith("000005") else TOKEN_META
        return TokenInfo(access_token=token, expires_in=5184000, token_type="bearer")

    async def _waba(waba_id: str, **kwargs):
        return [_waba_fake(phone_number_id=PHONE_NUMBER_WS)]

    monkeypatch.setattr(meta_signup, "intercambiar_code", _intercambia)
    monkeypatch.setattr(meta_signup, "listar_wabas", _waba)

    await set_tenant(BUSINESS_WS)

    primera = await http_client.post(
        "/api/v1/business/config/whatsapp/connect",
        headers=_auth_header(_token()),
        json={"code": "AQBcCodeDeUnSoloUso-000004"},
    )
    assert primera.status_code == 200
    # El WABA tiene numero: la fila queda pendiente (hasta que el switch la
    # active), pero el numero ya quedo registrado.
    assert primera.json()["phone_number_id"] == PHONE_NUMBER_WS
    assert primera.json()["activo"] is False

    activa = await http_client.put(
        "/api/v1/business/config/whatsapp",
        headers=_auth_header(_token()),
        json={"activo": True},
    )
    assert activa.status_code == 200
    assert activa.json()["activo"] is True

    segunda = await http_client.post(
        "/api/v1/business/config/whatsapp/connect",
        headers=_auth_header(_token()),
        json={"code": "AQBcCodeDeUnSoloUso-000005"},
    )
    assert segunda.status_code == 200
    assert segunda.json()["activo"] is True
    assert "sigue activo" in segunda.json()["mensaje"]

    filas = (
        await session.execute(
            text(
                "SELECT phone_number_id, waba_id, is_active, status, "
                "access_token_encrypted FROM whatsapp_connections "
                "WHERE business_id = :b"
            ),
            {"b": str(BUSINESS_WS)},
        )
    ).fetchall()
    assert len(filas) == 1
    fila = filas[0]
    assert fila[0] == PHONE_NUMBER_WS
    assert fila[1] == WABA_WS
    assert fila[2] is True
    assert fila[3] == WhatsAppConnectionStatus.ACTIVE.value
    assert decrypt_string(fila[4]) == TOKEN_META_2


async def test_connect_staff_no_puede_pero_si_ve(
    http_client: AsyncClient,
    negocio_connect: None,
    set_tenant,
    monkeypatch,
) -> None:
    """Staff ve el estado, no conecta: la escritura pide el scope de config.

    El endpoint exige `BUSINESS_CONFIG_WRITE`; el token de staff no lo tiene,
    y la API responde 403 antes de llamar a Meta (no se hace ningun
    intercambio).
    """
    await _configurar_meta(monkeypatch)

    async def _explotar(*args, **kwargs):
        raise AssertionError("no deberia llamarse a Meta")

    monkeypatch.setattr(meta_signup, "intercambiar_code", _explotar)

    await set_tenant(BUSINESS_WS)
    respuesta = await http_client.post(
        "/api/v1/business/config/whatsapp/connect",
        headers=_auth_header(_token("STAFF")),
        json={"code": "AQBcCodeDeUnSoloUso-000006"},
    )
    assert respuesta.status_code == 403

    # Y el GET de configuracion sigue funcionando para staff (no se rompio
    # nada del camino D-3 con los campos nuevos del response).
    vista = await http_client.get(
        "/api/v1/business/config/whatsapp", headers=_auth_header(_token("STAFF"))
    )
    assert vista.status_code == 200
    assert vista.json()["reminder_24h_template"] == TEMPLATE_24H_DEFAULT
    assert vista.json()["reminder_2h_template"] == TEMPLATE_2H_DEFAULT
    assert "meta_app_id" in vista.json()
    assert "connect_config_id" in vista.json()
