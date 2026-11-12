"""La IP del cliente para el rate limiting, en cada topologia.

El limitador cubetea por IP: si la IP que llega al cubo no es la del cliente real,
el limite se rompe en alguna de las dos direcciones posibles, y las dos se vivieron:

- **Cubo compartido** (el incidente de 2026-11): detras de Cloudflare el peer es el
  edge, y sin resolver la cabecera todos los usuarios caen en un mismo cubo. El
  sintoma fue 429 en el login y en la pagina publica **desde cualquier IP de
  origen**--el cubo lo llenaba el trafico agregado de la plataforma.
- **Cubo falsificable**: leer una cabecera sin validar quien la manda deja que un
  atacante se invente una distinta en cada pedido y esquive el limite entero.

Estos tests fijan el contrato de `client_ip`: `CF-Connecting-IP` solo se cree si el
peer inmediato es un edge de Cloudflare; en cualquier otra topologia, la IP es la
del peer.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.core.rate_limit import _peer_es_cloudflare, client_ip
from starlette.requests import Request

#: Un edge de Cloudflare tipico (rango 104.16.0.0/13).
EDGE_CF = "104.16.132.229"
#: Una IP de visitante tipica.
CLIENTE = "190.55.12.3"


def _request(peer: str | None, cf: str | None = None, xff: str | None = None) -> Request:
    """Arma un Request con la topologia descrita, sin pasar por una conexion real."""
    headers: list[tuple[bytes, bytes]] = []
    if cf is not None:
        headers.append((b"cf-connecting-ip", cf.encode()))
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "query_string": b"",
        "client": (peer, 12345) if peer else None,
        "server": ("127.0.0.1", 80),
        "scheme": "https",
    }
    return Request(scope)


class TestPeerEsCloudflare:
    def test_un_edge_de_cloudflare_es_cloudflare(self) -> None:
        assert _peer_es_cloudflare(EDGE_CF) is True

    def test_una_ip_de_cliente_no_es_cloudflare(self) -> None:
        assert _peer_es_cloudflare(CLIENTE) is False

    def test_el_router_de_render_no_es_cloudflare(self) -> None:
        # 216.24.57.x es Render: si CF-Connecting-IP llegara con ese peer, no se cree.
        assert _peer_es_cloudflare("216.24.57.16") is False

    def test_ip_invalida_no_es_cloudflare(self) -> None:
        assert _peer_es_cloudflare("no-soy-ip") is False

    def test_sin_peer_no_es_cloudflare(self) -> None:
        assert _peer_es_cloudflare(None) is False


class TestClientIp:
    def test_tras_cloudflare_usa_la_cabecera(self) -> None:
        r = _request(EDGE_CF, cf=CLIENTE)
        assert client_ip(r) == CLIENTE

    def test_tras_cloudflare_sin_cabecera_cae_al_peer(self) -> None:
        r = _request(EDGE_CF)
        # El peor desenlace posible es un cubo compartido de edge, no un cubo
        # falsificado: sin cabecera confiable no se inventa una IP.
        assert client_ip(r) == EDGE_CF

    def test_tras_cloudflare_con_cabecera_basura_cae_al_peer(self) -> None:
        r = _request(EDGE_CF, cf="no-soy-ip")
        assert client_ip(r) == EDGE_CF

    def test_tras_cloudflare_con_cabecera_vacia_cae_al_peer(self) -> None:
        r = _request(EDGE_CF, cf="   ")
        assert client_ip(r) == EDGE_CF

    def test_cliente_directo_ignora_la_cabecera(self) -> None:
        # La clave anti-spoofing: un atacante que llega directo a Render puede
        # mandar la cabecera que quiera, pero su peer no es CF y no se le cree.
        r = _request(CLIENTE, cf="1.2.3.4")
        assert client_ip(r) == CLIENTE

    def test_sin_peer_devuelve_desconocida(self) -> None:
        r = _request(None, cf="1.2.3.4")
        assert client_ip(r) == "desconocida"

    def test_ignora_x_forwarded_for_aun_tras_cf(self) -> None:
        # `X-Forwarded-For` se puede encadenar y falsear; la app nunca lo lee por
        # su cuenta (uvicorn podria reescribir client.host con el, y ese caso queda
        # cubierto por la validacion del peer: la cabecera CF gana).
        r = _request(EDGE_CF, cf=CLIENTE, xff="6.6.6.6")
        assert client_ip(r) == CLIENTE


@pytest.mark.parametrize(
    ("cidr", "esperado"),
    [
        ("172.64.0.1", True),  # dentro de 172.64.0.0/13
        ("172.71.255.255", True),
        ("172.72.0.0", False),  # fuera del /13
        ("198.41.192.0", True),  # dentro de 198.41.128.0/17
        ("198.42.0.0", False),
    ],
)
def test_limites_de_los_rangos(cidr: str, esperado: bool) -> None:
    assert _peer_es_cloudflare(cidr) is esperado
