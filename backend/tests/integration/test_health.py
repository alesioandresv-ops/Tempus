"""Health checks y arranque de la aplicacion.

`/healthz` y `/readyz` existen para que el orquestador decida cosas distintas: el
primero dice "el proceso responde" y el segundo "puede atender traffic". Por eso
`/readyz` toca la base y `/healthz` no, y por eso `/readyz` devuelve 503 en vez de
500 cuando la base no esta.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.api.routers import health as health_module
from app.main import app
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.integration]


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Cliente HTTP contra la app in-process.

    `ASGITransport` y no `TestClient`: el segundo es sync y obliga a correr la app
    en otro hilo, lo que rompe los fixtures async. No se levanta el lifespan, asi
    que estos tests no dependen del pool; el lifespan tiene su propio test.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def base_caida(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fuerza `check_database()` a fallar.

    Se parchea el simbolo **en el modulo del router**, que es donde se usa. Parchear
    `app.db.session.check_database` no tendria efecto: el router lo importo con
    `from ... import`, asi que tiene su propia referencia. Es el detalle que hace
    fallar silenciosamente este tipo de test.
    """

    async def _caida() -> bool:
        return False

    monkeypatch.setattr(health_module, "check_database", _caida)


class TestHealthz:
    async def test_responde_ok(self, client: AsyncClient) -> None:
        respuesta = await client.get("/healthz")
        assert respuesta.status_code == 200
        cuerpo: dict[str, Any] = respuesta.json()
        assert cuerpo["status"] == "ok"

    async def test_no_toca_la_base(self, client: AsyncClient, base_caida: None) -> None:
        """`/healthz` tiene que responder aunque la base este caida.

        Si tocara la base, un incidente de Postgres sacaria el servicio del balance
        por un chequeo que no necesita la base para responder.
        """
        respuesta = await client.get("/healthz")
        assert respuesta.status_code == 200

    async def test_no_filtra_detalles_del_entorno(self, client: AsyncClient) -> None:
        """`/healthz` es publico: no expone configuracion.

        Va sin autenticacion, asi que cualquier campo de mas queda expuesto a
        internet. El environment no es un secreto, pero publicarlo en un endpoint
        abierto le dice a un escaner que tipo de despliegue es.
        """
        cuerpo = (await client.get("/healthz")).json()
        assert set(cuerpo) <= {"status", "time"}


class TestReadyz:
    async def test_responde_ok_con_la_base_arriba(self, client: AsyncClient) -> None:
        respuesta = await client.get("/readyz")
        assert respuesta.status_code == 200
        cuerpo: dict[str, Any] = respuesta.json()
        assert cuerpo["status"] == "ok"
        assert cuerpo["database"] == "ok"

    async def test_responde_503_con_la_base_caida(
        self, client: AsyncClient, base_caida: None
    ) -> None:
        """503, no 500.

        La diferencia importa: 503 le dice al balanceador "no me mandes traffic,
        vuelvo a probar", y 500 dice "rompiste, no te toco mas". Un 500 por una base
        momentaneamente caida saca la instancia del balance de forma permanente.
        """
        respuesta = await client.get("/readyz")
        assert respuesta.status_code == 503
        cuerpo: dict[str, Any] = respuesta.json()
        assert cuerpo["database"] == "down"


class TestRequestId:
    async def test_se_genera_un_request_id(self, client: AsyncClient) -> None:
        respuesta = await client.get("/healthz")
        assert respuesta.headers.get("x-request-id")

    async def test_se_propaga_el_que_llega(self, client: AsyncClient) -> None:
        respuesta = await client.get("/healthz", headers={"x-request-id": "abc-123"})
        assert respuesta.headers.get("x-request-id") == "abc-123"

    async def test_no_acepta_un_id_inventado_demasiado_largo(self, client: AsyncClient) -> None:
        """Un `x-request-id` de 5 KB no va entero a los logs.

        El header viene del cliente. Sin un tope, un endpoint acepta un string
        arbitrariamente largo y lo escribe en cada linea de log: un solo request
        puede inflar el almacenamiento de logs y el costo de la cuenta.
        """
        respuesta = await client.get("/healthz", headers={"x-request-id": "x" * 5000})
        generado = respuesta.headers.get("x-request-id", "")
        assert len(generado) <= 200, "el request id deberia estar acotado"


class TestOpenApi:
    async def test_el_esquema_se_genera(self, client: AsyncClient) -> None:
        """El contrato de la API se puede leer.

        Un backend sin esquema exportable es un backend donde el cliente se entera
        de los cambios al romperese.
        """
        # `app.openapi_url` esta declarado `str | None` en FastAPI, y la ruta vacia
        # haria que el test pegue contra la raiz y pase por el 404 del router. El
        # assert deja el motivo del fallo explicito.
        ruta = app.openapi_url
        assert ruta is not None, "la app no declara openapi_url: no hay contrato que leer"
        respuesta = await client.get(ruta)
        assert respuesta.status_code == 200
        esquema = respuesta.json()
        assert "/healthz" in esquema["paths"]
        assert "/readyz" in esquema["paths"]

    async def test_el_esquema_va_bajo_el_prefijo_api(self) -> None:
        """La documentacion cuelga de `/api/v1`, no de la raiz.

        Es una decision deliberada: las rutas de la aplicacion viven bajo `/api`, con
        la version adentro, para que los paths de la API nunca choquen con los de una
        landing o un panel que se sume despues. El test la fija para que nadie lo
        "simplifique" moviendo el esquema a la raiz sin darse cuenta de que cambia la
        URL publica.

        La version forma parte del prefijo y no es un detalle: `COOKIE_PATH` y
        `VITE_API_BASE_URL` apuntan a `/api/v1`, asi que con el prefijo corto la
        cookie de refresh se envia en todas las peticiones en lugar de solo en las de
        autenticacion. La mitigacion de superficie que ese campo declara hacer queda
        anulada, y ningun test de comportamiento lo detectaria: la cookie se sigue
        enviando, solo que a todos lados.
        """
        assert app.openapi_url == "/api/v1/openapi.json"
        assert app.docs_url == "/api/v1/docs"

    async def test_no_hay_documentacion_en_la_raiz(self) -> None:
        """Y en la raiz no hay nada.

        FastAPI sirve `/redoc` por defecto, en la raiz y con la version equivocada:
        una segunda documentacion en una ruta que nadie mantiene. Se apaga
        explicitamente para que la API tenga un solo lugar del que leer.
        """
        assert app.redoc_url is None, "redoc queda sirviendo documentacion en la raiz"
        assert app.openapi_url is not None
        assert app.openapi_url.startswith("/api/"), "el esquema debe vivir bajo /api"
