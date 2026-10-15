"""Verificacion server-side de Cloudflare Turnstile.

Lo que se prueba es sobre todo la **matriz de fallos**, que es donde un anti-bot
mal cableado rompe el producto en silencio:

- apagado, un token ausente no puede romper nada (el widget no esta en la pagina);
- encendido, la falta de token es 403 y no 500;
- si Cloudflare no responde, es 503 y **no** 403: no es que la persona sea un bot,
  es que no se pudo comprobar, y tratarlo como rechazo seria mentirle.

No se toca la red: se reemplaza `httpx` dentro del modulo por un doble.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest
from app.core import turnstile
from app.core.config import Settings
from app.core.turnstile import verificar_turnstile
from fastapi import HTTPException

pytestmark = pytest.mark.unit

#: Lo minimo para que `Settings()` no falle por campos obligatorios. Mismo criterio
#: que `test_config.py`: `_env_file=None` para no depender del `.env` de la maquina.
BASE: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://tempus_app:pw@localhost:5432/tempus",
    "database_migration_url": "postgresql+asyncpg://tempus_owner:pw@localhost:5432/tempus",
    "jwt_secret_key": "x" * 40,
    "encryption_key": base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode(),
    "scheduler_tick_secret": "tick-" + "y" * 40,
}


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **{**BASE, **overrides})


class _Respuesta:
    """Respuesta de mentira: solo lo que `verificar_turnstile` lee."""

    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _ClienteFalso:
    """`httpx.AsyncClient` de mentira: contexto async + `post` async."""

    #: Intentos registrados como `(url, data)`.
    llamadas: ClassVar[list[tuple[str, dict[str, Any]]]] = []
    #: Payload que devuelve `json()`; puede ser una excepcion para simular fallos.
    payload: ClassVar[Any] = {"success": True}
    #: Si no es `None`, `post` lo levanta.
    error: ClassVar[Exception | None] = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _ClienteFalso:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def post(self, url: str, data: dict[str, Any] | None = None) -> _Respuesta:
        type(self).llamadas.append((url, data or {}))
        if type(self).error is not None:
            raise type(self).error
        return _Respuesta(type(self).payload)


@pytest.fixture
def httpx_falso(monkeypatch: pytest.MonkeyPatch) -> type[_ClienteFalso]:
    """Reemplaza `httpx` dentro del modulo por el doble y limpia el estado."""
    _ClienteFalso.llamadas = []
    _ClienteFalso.payload = {"success": True}
    _ClienteFalso.error = None
    monkeypatch.setattr(
        turnstile,
        "httpx",
        SimpleNamespace(AsyncClient=_ClienteFalso, HTTPError=httpx.HTTPError),
    )
    return _ClienteFalso


async def test_apagado_no_hace_nada() -> None:
    """Con la bandera apagada, ni con token ni sin token se toca la red."""
    await verificar_turnstile(None, settings=_settings(), remote_ip="10.0.0.1")
    await verificar_turnstile("token", settings=_settings(), remote_ip="10.0.0.1")


async def test_apagado_no_llama_a_cloudflare(httpx_falso: type[_ClienteFalso]) -> None:
    await verificar_turnstile(None, settings=_settings(), remote_ip="10.0.0.1")
    assert httpx_falso.llamadas == []


async def test_encendido_sin_token_rechaza_con_403() -> None:
    with pytest.raises(HTTPException) as exc:
        await verificar_turnstile(
            None, settings=_settings(turnstile_enabled=True), remote_ip="10.0.0.1"
        )
    assert exc.value.status_code == 403


async def test_encendido_token_vacio_rechaza_con_403() -> None:
    with pytest.raises(HTTPException) as exc:
        await verificar_turnstile("", settings=_settings(turnstile_enabled=True), remote_ip=None)
    assert exc.value.status_code == 403


async def test_encendido_token_valido_pasa(httpx_falso: type[_ClienteFalso]) -> None:
    await verificar_turnstile(
        "token-bueno", settings=_settings(turnstile_enabled=True), remote_ip="10.0.0.1"
    )
    assert len(httpx_falso.llamadas) == 1


async def test_encendido_token_invalido_rechaza_con_403(httpx_falso: type[_ClienteFalso]) -> None:
    httpx_falso.payload = {"success": False, "error-codes": ["invalid-input-response"]}
    with pytest.raises(HTTPException) as exc:
        await verificar_turnstile(
            "token-malo", settings=_settings(turnstile_enabled=True), remote_ip="10.0.0.1"
        )
    assert exc.value.status_code == 403


async def test_respuesta_no_es_dict_rechaza_con_403(httpx_falso: type[_ClienteFalso]) -> None:
    """Una respuesta que no es un objeto JSON no puede leerse como exito."""
    httpx_falso.payload = ["no", "es", "dict"]
    with pytest.raises(HTTPException) as exc:
        await verificar_turnstile("t", settings=_settings(turnstile_enabled=True), remote_ip=None)
    assert exc.value.status_code == 403


async def test_cloudflare_caido_responde_503_y_no_403(httpx_falso: type[_ClienteFalso]) -> None:
    httpx_falso.error = httpx.ConnectError("sin ruta a Cloudflare")
    with pytest.raises(HTTPException) as exc:
        await verificar_turnstile(
            "token", settings=_settings(turnstile_enabled=True), remote_ip="10.0.0.1"
        )
    assert exc.value.status_code == 503


async def test_cuerpo_no_json_responde_503(httpx_falso: type[_ClienteFalso]) -> None:
    httpx_falso.payload = ValueError("Expecting value")
    with pytest.raises(HTTPException) as exc:
        await verificar_turnstile(
            "token", settings=_settings(turnstile_enabled=True), remote_ip=None
        )
    assert exc.value.status_code == 503


async def test_manda_secret_y_remote_ip(httpx_falso: type[_ClienteFalso]) -> None:
    """El secreto va en el cuerpo y la IP del cliente se propaga si existe."""
    await verificar_turnstile(
        "token-bueno",
        settings=_settings(turnstile_enabled=True, turnstile_secret="secreto-de-prueba"),
        remote_ip="203.0.113.7",
    )
    url, data = httpx_falso.llamadas[0]
    assert url == _settings().turnstile_verify_url
    assert data["secret"] == "secreto-de-prueba"
    assert data["response"] == "token-bueno"
    assert data["remoteip"] == "203.0.113.7"


async def test_sin_remote_ip_no_manda_la_clave(httpx_falso: type[_ClienteFalso]) -> None:
    """`remoteip` es opcional: sin IP conocida no se manda la clave vacia."""
    await verificar_turnstile(
        "token-bueno", settings=_settings(turnstile_enabled=True), remote_ip=None
    )
    _, data = httpx_falso.llamadas[0]
    assert "remoteip" not in data
