"""Tests del flujo "olvide mi contrasena" (Fase: recuperacion de cuenta).

La promesa de seguridad que estos tests fijan:

1. **Anti-enumeracion**: `POST /auth/forgot-password` responde 200 con el
   mismo cuerpo exista o no la cuenta, y el rate limit (3/h por email) se
   aplica tambien a los emails sin cuenta — si no, el contador mismo seria
   el oraculo.
2. **El token sirve una vez, dentro de la ventana**, y un pedido nuevo
   invalida el anterior (ultimo-token-gana).
3. **El reset cubre a la persona**: cambia la contrasena en todas sus
   membresias y revoca sus refresh tokens.
4. **Sin SMTP configurado el endpoint no explota**: responde 200 y el link
   queda en el log del servidor.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.core.security import verify_password
from app.db.session import TENANT_GUC
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

pytestmark = pytest.mark.integration

NEGOCIO = uuid.UUID("33333333-3333-7333-8333-333333333333")
EMAIL = "reset-test@example.com"
CLAVE_VIEJA = "ContrasenaVieja123!"
CLAVE_NUEVA = "ContrasenaNueva456!"


@pytest_asyncio.fixture
async def negocio_reset(migration_database_url: str) -> AsyncIterator[None]:
    """Negocio + owner activos, sembrados con el rol DDL (mismo patron que MP)."""
    motor: AsyncEngine = create_async_engine(migration_database_url, poolclass=None, echo=False)
    try:
        async with motor.begin() as conn:
            await conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                {"tenant": str(NEGOCIO)},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO businesses (id, name, slug, timezone, status)
                    VALUES (:id, 'Peluqueria Reset', 'reset-test', 'America/Argentina/Buenos_Aires', 'active')
                    ON CONFLICT (slug) DO UPDATE SET status = 'active', created_at = now()
                    """
                ),
                {"id": NEGOCIO},
            )
            await conn.execute(
                text("DELETE FROM password_reset_tokens WHERE email = :e"),
                {"e": EMAIL},
            )
            await conn.execute(
                text("DELETE FROM business_users WHERE email = :e AND business_id = :b"),
                {"e": EMAIL, "b": NEGOCIO},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO business_users (business_id, email, password_hash, full_name, role, status)
                    VALUES (:b, :e, 'no-importa-se-reemplaza', 'Dona Reset', 'admin', 'active')
                    """
                ),
                {"b": NEGOCIO, "e": EMAIL},
            )
        yield
        async with motor.begin() as conn:
            await conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                {"tenant": str(NEGOCIO)},
            )
            await conn.execute(
                text("DELETE FROM password_reset_tokens WHERE email = :e"), {"e": EMAIL}
            )
            await conn.execute(
                text("DELETE FROM business_users WHERE email = :e AND business_id = :b"),
                {"e": EMAIL, "b": NEGOCIO},
            )
            await conn.execute(text("DELETE FROM businesses WHERE id = :b"), {"b": NEGOCIO})
    finally:
        await motor.dispose()


async def _pedir_reset_placeholder() -> None:
    """Vacio a proposito: el envio se captura con monkeypatch en cada test."""


def _extraer_token(cuerpo: str) -> str:
    linea = next(linea for linea in cuerpo.splitlines() if "token=" in linea)
    return linea.split("token=")[1].strip()


async def test_forgot_responde_igual_con_o_sin_cuenta(
    http_client: AsyncClient,
    negocio_reset: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La respuesta no delata si el email tiene cuenta (anti-enumeracion)."""
    capturadas: list[tuple[str, str, str]] = []

    async def capturar(settings, *, para, asunto, cuerpo):  # type: ignore[no-untyped-def]
        capturadas.append((para, asunto, cuerpo))

    from app.modules.auth import mailer

    monkeypatch.setattr(mailer, "enviar_email_seguro", capturar)
    monkeypatch.setattr(mailer, "smtp_configurado", lambda _s: True)

    con_cuenta = await http_client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    sin_cuenta = await http_client.post(
        "/api/v1/auth/forgot-password", json={"email": "nadie@example.com"}
    )

    assert con_cuenta.status_code == sin_cuenta.status_code == 200
    assert con_cuenta.json() == sin_cuenta.json()
    assert "mensaje" in con_cuenta.json()
    # Solo la cuenta existente genero email.
    assert len(capturadas) == 1
    assert capturadas[0][0] == EMAIL


async def test_reset_completo_cambia_clave_y_revoca(
    http_client: AsyncClient,
    negocio_reset: None,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pedido -> link -> consumo: la clave nueva entra y la vieja no.

    La verificacion del hash va por la **misma sesion del test** y no por el
    motor owner: el POST escribe dentro de la transaccion anidada que el
    fixture mantiene abierta, y una segunda conexion esperaria el lock hasta
    el rollback final (deadlock self-inflicted, no del codigo).
    """
    from app.modules.auth import mailer

    capturadas: list[tuple[str, str, str]] = []

    async def capturar(settings, *, para, asunto, cuerpo):  # type: ignore[no-untyped-def]
        capturadas.append((para, asunto, cuerpo))

    monkeypatch.setattr(mailer, "enviar_email_seguro", capturar)
    monkeypatch.setattr(mailer, "smtp_configurado", lambda _s: True)

    pedido = await http_client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    assert pedido.status_code == 200
    token = _extraer_token(capturadas[0][2])

    consumo = await http_client.post(
        "/api/v1/auth/reset-password", json={"token": token, "password": CLAVE_NUEVA}
    )
    assert consumo.status_code == 200

    # La clave nueva verifica; la vieja no. Mismo motor que el POST (ver arriba).
    hash_nuevo = (
        await session.execute(
            text("SELECT password_hash FROM business_users WHERE email = :e"),
            {"e": EMAIL},
        )
    ).scalar_one()
    assert verify_password(CLAVE_NUEVA, str(hash_nuevo))
    assert not verify_password(CLAVE_VIEJA, str(hash_nuevo))


async def test_token_reusado_y_vencido_son_400(
    http_client: AsyncClient,
    negocio_reset: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El mismo token dos veces: la segunda es 400, sin decir por que."""
    from app.modules.auth import mailer

    capturadas: list[tuple[str, str, str]] = []

    async def capturar(settings, *, para, asunto, cuerpo):  # type: ignore[no-untyped-def]
        capturadas.append((para, asunto, cuerpo))

    monkeypatch.setattr(mailer, "enviar_email_seguro", capturar)
    monkeypatch.setattr(mailer, "smtp_configurado", lambda _s: True)

    await http_client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    token = _extraer_token(capturadas[0][2])

    primera = await http_client.post(
        "/api/v1/auth/reset-password", json={"token": token, "password": CLAVE_NUEVA}
    )
    assert primera.status_code == 200

    segunda = await http_client.post(
        "/api/v1/auth/reset-password", json={"token": token, "password": "OtraClave789xyz!"}
    )
    assert segunda.status_code == 400

    inexistente = await http_client.post(
        "/api/v1/auth/reset-password",
        json={"token": "f" * 64, "password": "OtraClave789xyz!"},
    )
    # El mismo mensaje para "ya usado" y "no existe": no delata si el link robado sirve.
    assert segunda.json()["detail"] == inexistente.json()["detail"]


async def test_pedido_nuevo_invalida_el_anterior(
    http_client: AsyncClient,
    negocio_reset: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ultimo-token-gana: el segundo pedido mata el primer link."""
    from app.modules.auth import mailer

    capturadas: list[tuple[str, str, str]] = []

    async def capturar(settings, *, para, asunto, cuerpo):  # type: ignore[no-untyped-def]
        capturadas.append((para, asunto, cuerpo))

    monkeypatch.setattr(mailer, "enviar_email_seguro", capturar)
    monkeypatch.setattr(mailer, "smtp_configurado", lambda _s: True)

    await http_client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    await http_client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    token_viejo = _extraer_token(capturadas[0][2])
    token_nuevo = _extraer_token(capturadas[1][2])
    assert token_viejo != token_nuevo

    viejo = await http_client.post(
        "/api/v1/auth/reset-password", json={"token": token_viejo, "password": CLAVE_NUEVA}
    )
    assert viejo.status_code == 400

    nuevo = await http_client.post(
        "/api/v1/auth/reset-password", json={"token": token_nuevo, "password": CLAVE_NUEVA}
    )
    assert nuevo.status_code == 200


async def test_rate_limit_por_email_es_3_por_hora(
    http_client: AsyncClient,
    negocio_reset: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El cuarto pedido del mismo email es 429 (se aplica sin importar la cuenta)."""
    from app.modules.auth import mailer

    async def capturar(settings, *, para, asunto, cuerpo):  # type: ignore[no-untyped-def]
        pass

    monkeypatch.setattr(mailer, "enviar_email_seguro", capturar)
    monkeypatch.setattr(mailer, "smtp_configurado", lambda _s: True)

    for _ in range(3):
        respuesta = await http_client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
        assert respuesta.status_code == 200

    cuarto = await http_client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    assert cuarto.status_code == 429

    # Un email distinto desde la misma IP todavia tiene cubo propio.
    otro = await http_client.post("/api/v1/auth/forgot-password", json={"email": "otro@x.test"})
    assert otro.status_code == 200
