"""Turnstile cableado en los dos endpoints publicos que lo exigen.

Los tests de `test_turnstile.py` prueban la funcion de verificacion en aislamiento.
Lo que estos prueban es otra cosa: que el endpoint **la llame**. Un `verificar_turnstile`
perfecto que nadie invoca deja el endpoint abierto, y eso no lo ve una prueba de unidad.

La bandera se prende sobre la instancia de `Settings` que el `http_client` ya uso para
crear la app--es la misma que devuelve `get_settings()` por el `lru_cache`--, asi que el
request ve el cambio sin reconstruir la app. `monkeypatch` la restaura al final.
"""

from __future__ import annotations

import pytest
from app.core.config import get_settings
from fastapi import status
from httpx import AsyncClient

pytestmark = pytest.mark.integration

#: Reservado por `0015`: el alta lo rechaza con 409 sin crear nada, que es lo que
#: hace al caso "apagado" determinista y sin efectos.
SLUG_RESERVADO = "admin"

ALTA = "/api/v1/auth/register-business"
RESERVA = "/api/v1/public/bookings"

CUERPO_RESERVA = {
    "slug": "negocio-que-no-existe",
    "service_id": "00000000-0000-0000-0000-0000000000aa",
    "customer_first_name": "Ana",
    "customer_last_name": "Perez",
    "customer_phone_e164": "+541100000099",
    "starts_at": "2026-10-20T12:00:00Z",
    "ends_at": "2026-10-20T12:30:00Z",
    "local_date": "2026-10-20",
}

CUERPO_ALTA = {
    "business_name": "Turnstile Test",
    "slug": SLUG_RESERVADO,
    "owner_name": "Ana Perez",
    "email": "ana.turnstile@peluqueria.com",
    "password": "password-largo-123",
    "timezone": "America/Argentina/Buenos_Aires",
}


def _con_turnstile(monkeypatch: pytest.MonkeyPatch, *, activo: bool) -> None:
    """Prende o apaga la bandera en la instancia que ve el router."""
    monkeypatch.setattr(get_settings(), "turnstile_enabled", activo)


async def test_reserva_sin_token_es_403(
    http_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con Turnstile activo, reservar sin token no llega ni a resolver el negocio."""
    _con_turnstile(monkeypatch, activo=True)
    respuesta = await http_client.post(RESERVA, json=CUERPO_RESERVA)
    assert respuesta.status_code == status.HTTP_403_FORBIDDEN


async def test_reserva_apagado_no_la_bloquea(
    http_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apagado, la misma reserva pasa la barrera y llega al 404 del negocio inexistente.

    El slug no existe a proposito: asi el resultado depende del flujo de negocio y no
    de haber creado filas. Un 403 aca delataria que la bandera no se esta respetando.
    """
    _con_turnstile(monkeypatch, activo=False)
    respuesta = await http_client.post(RESERVA, json=CUERPO_RESERVA)
    assert respuesta.status_code == status.HTTP_404_NOT_FOUND


async def test_alta_sin_token_es_403(
    http_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con Turnstile activo, el alta sin token se corta antes de tocar la base."""
    _con_turnstile(monkeypatch, activo=True)
    respuesta = await http_client.post(ALTA, json=CUERPO_ALTA)
    assert respuesta.status_code == status.HTTP_403_FORBIDDEN


async def test_alta_apagada_no_la_bloquea(
    http_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apagado, el alta llega a su propio rechazo (el slug reservado da 409)."""
    _con_turnstile(monkeypatch, activo=False)
    respuesta = await http_client.post(ALTA, json=CUERPO_ALTA)
    assert respuesta.status_code == status.HTTP_409_CONFLICT
