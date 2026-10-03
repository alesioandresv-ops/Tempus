"""El envelope de error es un contrato, no un formato.

Dos reglas de este archivo importan mas que el resto:

1. **Ninguna excepcion llega al cliente.** Un `IntegrityError` de PostgreSQL trae
   el nombre de la tabla, el de la restriccion y a veces el valor que se violo. Con
   eso, cualquiera que pueda llamar a la API conoce el esquema de la base. Estos
   tests levantan excepciones con esos datos adentro a proposito: si alguien
   cambia el handler para "pasar el detalle real porque ayuda", el test lo frena.

2. **La referencia del 500 sirve para encontrar el log.** Si el `request_id` que
   recibe el usuario no aparece en el log del error, la referencia no sirve para
   nada, y el operador que la recibe no tiene por donde empezar.
"""

from __future__ import annotations

import json
import re

import pytest
from app.api.errors import (
    PROBLEM_CONTENT_TYPE,
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    SlotUnavailableError,
    ValidationError,
    problem_response,
    register_exception_handlers,
)
from app.main import RequestContextMiddleware
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

pytestmark = pytest.mark.unit


def _peticion() -> Request:
    """Un `Request` minimo para llamar a `problem_response` sin HTTP.

    La funcion lee `request.url.path` y nada mas. Armarla con un scope crudo evita
    levantar un servidor o un cliente solo para pasarle un objeto.
    """
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/choque",
            "raw_path": b"/choque",
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        }
    )


class Pedido(BaseModel):
    """Modelo a nivel de modulo, no dentro de la funcion del test.

    Con `from __future__ import annotations` las anotaciones son strings, y FastAPI
    las resuelve contra el **ambito del modulo**. Un modelo definido dentro de la
    funcion no se encuentra, FastAPI no lo reconoce como body y trata el parametro
    como query. El sintoma es un `field` que dice `query.cuerpo` y ningun error de
    por medio.
    """

    nombre: str
    cantidad: int


def _cliente(app: FastAPI) -> AsyncClient:
    """Cliente que deja que el handler de 500 corra.

    `ASGITransport` viene con `raise_app_exceptions=True`: relanza la excepcion del
    servidor en vez de devolver la respuesta que produjo el handler. Con eso, los
    tres tests del 500 no se pueden escribir. Ponerlo en `False` es lo que hace
    observable la respuesta que ve el cliente.
    """
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    )


@pytest.fixture
def app() -> FastAPI:
    """App minima con los handlers registrados y sin lifespan ni base de datos.

    No se usa `create_app` a proposito: esta suite prueba el envelope, y traer la
    app real agrega settings, logging y lifespan a cada test sin que ninguno lo
    necesite. Un error de esos haria fallar un test de formato.
    """
    aplicacion = FastAPI()
    register_exception_handlers(aplicacion)
    return aplicacion


class TestEnvelopeProblem:
    async def test_los_cinco_campos_de_rfc_9457(self, app: FastAPI) -> None:
        @app.get("/roto")
        async def roto() -> None:
            raise NotFoundError("Cliente 42 no existe")

        async with _cliente(app) as cliente:
            respuesta = await cliente.get("/roto")

        cuerpo = respuesta.json()
        assert respuesta.status_code == 404
        assert cuerpo["type"] == "about:blank"
        assert cuerpo["title"] == "No encontrado"
        assert cuerpo["status"] == 404
        assert cuerpo["detail"] == "Cliente 42 no existe"
        assert cuerpo["instance"] == "/roto"

    async def test_el_content_type_es_problem_json(self, app: FastAPI) -> None:
        """El media type es parte del contrato: sin el, el cliente no lo detecta."""

        @app.get("/roto")
        async def roto() -> None:
            raise NotFoundError("x")

        async with _cliente(app) as cliente:
            respuesta = await cliente.get("/roto")

        assert respuesta.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)

    async def test_instance_es_el_path_y_no_la_url_completa(self, app: FastAPI) -> None:
        """Con query string incluida, el `instance` no sirve para agrupar.

        `instance` identifica el recurso. Si fuera la URL entera, dos llamadas
        identicas con distinto query serian dos recursos distintos y ningun cliente
        podria deduplicar.
        """

        @app.get("/roto")
        async def roto() -> None:
            raise NotFoundError("x")

        async with _cliente(app) as cliente:
            respuesta = await cliente.get("/roto?tenant=1&debug=true")

        assert respuesta.json()["instance"] == "/roto"

    async def test_el_extra_entra_en_el_cuerpo(self, app: FastAPI) -> None:
        """`extra` es para datos estructurados de la peticion, no para apilar campos."""

        @app.get("/choque")
        async def choque() -> None:
            raise ConflictError("El slot ya esta ocupado", extra={"slot": "2026-06-15T20:30"})

        async with _cliente(app) as cliente:
            cuerpo = (await cliente.get("/choque")).json()

        assert cuerpo["slot"] == "2026-06-15T20:30"
        assert cuerpo["status"] == 409


class TestCodigosPorTipo:
    """Cada clase de error tiene su status, y el status es lo que el cliente decide.

    Un 409 donde deberia haber un 400 hace que el cliente intente corregir el pedido
    cuando el problema es que el mundo cambio.
    """

    @pytest.mark.parametrize(
        ("error", "status_esperado"),
        [
            (NotFoundError("x"), 404),
            (ConflictError("x"), 409),
            (SlotUnavailableError("x"), 409),
            (ValidationError("x"), 422),
            (AuthenticationError("x"), 401),
            (AuthorizationError("x"), 403),
            (RateLimitError("x"), 429),
        ],
    )
    def test_codigos_correctos(self, error: AppError, status_esperado: int) -> None:
        assert error.status_code == status_esperado

    def test_slot_unavailable_es_409_y_no_400(self) -> None:
        """La peticion era valida; el mundo cambio entre consultarlo y reservarlo.

        El cliente tiene que repreguntar disponibilidad, no corregir el pedido. Un
        400 lo manda a corregir datos que ya estaban bien.
        """
        error = SlotUnavailableError("El horario dejo de estar libre")
        assert error.status_code == 409
        assert isinstance(error, ConflictError)
        assert error.title == "Horario no disponible"

    async def test_slot_unavailable_sale_como_409_en_la_api(self, app: FastAPI) -> None:
        @app.post("/reservar")
        async def reservar() -> None:
            raise SlotUnavailableError("El horario dejo de estar libre")

        async with _cliente(app) as cliente:
            respuesta = await cliente.post("/reservar")

        assert respuesta.status_code == 409
        assert respuesta.json()["title"] == "Horario no disponible"

    @pytest.mark.parametrize("campo", ["status", "title", "type", "instance"])
    def test_extra_no_pisa_los_campos_del_standard(self, campo: str) -> None:
        """`extra` no puede sobreescribir `status` ni `title`.

        Con `body.update(extra)` al final, un `extra={"status": 200}` manda una
        respuesta con HTTP 409 y un cuerpo que dice 200. Depende de que ningun
        modulo pase esas claves, y ese tipo de dependencia es justo la que un test
        de tres lineas vuelve permanente.

        Se rechaza al **construir** el error y no al serializar: en el handler el
        `ValueError` caeria en el handler de `Exception`, volveria un 500 y el
        mensaje original se perderia. Aca el error sale en el test que lo escribio.
        """
        with pytest.raises(ValueError, match="RFC 9457"):
            ConflictError("choque", extra={campo: "valor-malo"})

    def test_extra_si_puede_adornar_el_detail(self) -> None:
        """`detail` no está reservado: es el campo que se decora.

        `status`, `title`, `type` e `instance` son contrato. `detail` es texto libre
        que el dominio puede querer enriquecer, y bloquearlo sería una regla sin
        motivo.
        """
        error = ConflictError("choque", extra={"detail": "mas especifico"})
        assert error.extra == {"detail": "mas especifico"}

    def test_problem_response_tambien_gana_ante_un_extra_reservado(self) -> None:
        """La segunda capa, probada directo sobre `problem_response`.

        `AppError.__init__` ya rechaza los nombres reservados, así que la ruta que
        llega por HTTP no puede tenerlos y probarla por HTTP no probaría nada: la
        protección del `__init__` alcanzaría para tapar el orden equivocado de acá.

        `problem_response` es una función pública del módulo y se la puede llamar
        sin pasar por un `AppError`. Por eso este test existe: fija que el **body**
        gana siempre, aunque el `extra` se cuele. Sin él, la segunda capa es código
        que nadie puede ejecutar mal y que por lo tanto nadie sabe si funciona.
        """
        peticion = _peticion()
        respuesta = problem_response(
            status_code=409,
            title="Conflicto",
            detail="choque",
            request=peticion,
            extra={"status": 200, "title": "OK", "slot": "10:00"},
        )
        cuerpo = json.loads(bytes(respuesta.body))
        assert cuerpo["status"] == 409, "el extra no puede pisar el status del body"
        assert cuerpo["title"] == "Conflicto"
        assert respuesta.status_code == 409
        # Y lo que sí es propio del error entra.
        assert cuerpo["slot"] == "10:00"


class TestNoSeFiltranInternos:
    """La regla del modulo: el `detail` de cara al cliente nunca lleva una excepcion."""

    async def test_un_integrity_error_no_by_pasa_nada(self, app: FastAPI) -> None:
        """El caso que da nombre a la regla.

        Se arma un `IntegrityError` con la tabla, la restriccion y el valor que se
        violo, que es exactamente lo que PostgreSQL pone en el mensaje. Si algo de
        eso aparece en la respuesta, la API le esta regalando el esquema a quien la
        pueda llamar.
        """
        secreto = "uq_bookings_business_id_professional_id_start_at"
        tabla = "bookings"

        @app.post("/crear")
        async def crear() -> None:
            raise IntegrityError(
                f'INSERT INTO "{tabla}" violates unique constraint "{secreto}"',
                params=None,
                orig=Exception("duplicate key value violates unique constraint"),
            )

        async with _cliente(app) as cliente:
            respuesta = await cliente.post("/crear")

        cuerpo = respuesta.json()
        assert respuesta.status_code == 500
        texto = respuesta.text
        assert secreto not in texto
        assert tabla not in texto
        assert "duplicate key" not in texto
        assert "IntegrityError" not in texto
        assert cuerpo["title"] == "Error interno"

    async def test_el_500_no_expone_la_excepcion_pero_si_una_referencia(self, app: FastAPI) -> None:
        @app.get("/explota")
        async def explota() -> None:
            raise RuntimeError("detalle interno que no debe salir")

        async with _cliente(app) as cliente:
            respuesta = await cliente.get("/explota")

        cuerpo = respuesta.json()
        assert respuesta.status_code == 500
        assert "detalle interno" not in respuesta.text
        assert "RuntimeError" not in respuesta.text
        # La referencia si tiene que estar: es lo que permite investigar.
        assert "Referencia:" in cuerpo["detail"]

    async def test_la_referencia_del_500_es_el_request_id_real(self, app: FastAPI) -> None:
        """El puente entre la referencia del cliente y el log tiene que existir.

        Este es el test que obliga a que el 500 reutilice el `request_id` de la
        peticion en vez de generar uno propio. Si vuelve a hacer `uuid4()`, la
        referencia que recibe el usuario no aparece en ninguna linea de log y el
        operador se queda sin por donde empezar, que es el peor resultado posible
        para una funcion cuyo unico proposito es dar un punto de partida.

        Por eso se registra `RequestContextMiddleware` y no se prueba contra una app
        desnuda: el `request_id` lo pone el middleware, y sin el, la assertion
        probaria el fallback y no el comportamiento.
        """
        app.add_middleware(RequestContextMiddleware)

        @app.get("/explota")
        async def explota() -> None:
            raise RuntimeError("boom")

        async with _cliente(app) as cliente:
            respuesta = await cliente.get("/explota", headers={"x-request-id": "req-abc-123"})

        referencia = re.search(r"Referencia:\s*(\S+)", respuesta.json()["detail"])
        assert referencia is not None, "el detail no trae referencia"
        assert referencia.group(1) == "req-abc-123", (
            "la referencia no coincide con el request_id de la peticion: el puente "
            "entre el cliente y el log no existe"
        )

    async def test_sin_middleware_cae_al_id_generado(self, app: FastAPI) -> None:
        """Sin middleware no hay id de peticion, y el 500 genera uno.

        Es el camino de respaldo y tiene que existir: un handler que metiera
        `get_request_id()` en el `detail` sin fallback publicaria
        "Referencia: None" en vez de una referencia utilizable.
        """

        @app.get("/explota")
        async def explota() -> None:
            raise RuntimeError("boom")

        async with _cliente(app) as cliente:
            respuesta = await cliente.get("/explota")

        referencia = re.search(r"Referencia:\s*(\S+)", respuesta.json()["detail"])
        assert referencia is not None
        assert referencia.group(1) not in {"None", ""}


class TestErroresDeValidacion:
    async def test_los_errores_de_esquema_si_se_devuelven(self, app: FastAPI) -> None:
        """A diferencia de las excepciones, estos si son seguros.

        Traen el nombre del campo y por que fallo, no datos del negocio. Sin ellos el
        formulario no puede decirle al usuario que corregir.
        """

        @app.post("/pedido")
        async def pedido(cuerpo: Pedido) -> dict[str, str]:
            return {"ok": "1"}

        async with _cliente(app) as cliente:
            respuesta = await cliente.post("/pedido", json={"nombre": "x", "cantidad": "abc"})

        cuerpo = respuesta.json()
        assert respuesta.status_code == 422
        assert cuerpo["title"] == "Datos invalidos"
        errores = cuerpo["errors"]
        assert len(errores) == 1
        assert "cantidad" in errores[0]["field"]
        assert {"field", "message", "type"} <= set(errores[0])

    async def test_el_loc_se_aplana_con_puntos(self, app: FastAPI) -> None:
        """`body.cantidad` y no `('body', 'cantidad')`.

        El path plano es lo que un cliente puede mostrar junto al campo del
        formulario. La tupla de pydantic hay que traducirla en cada consumidor.
        """

        @app.post("/pedido")
        async def pedido(cuerpo: Pedido) -> dict[str, str]:
            return {"ok": "1"}

        async with _cliente(app) as cliente:
            cuerpo = (await cliente.post("/pedido", json={"nombre": "x"})).json()

        # Solo falta `cantidad`: mandando `{}` el primer faltante seria `nombre` y
        # el test probaria otra cosa.
        assert cuerpo["errors"][0]["field"] == "body.cantidad"


class TestErroresHttp:
    async def test_un_404_de_starlette_tambien_es_problem_json(self, app: FastAPI) -> None:
        """Un 404 de routing que devuelve HTML rompe el parsing del cliente.

        El cliente que asume `application/problem+json` recibe HTML y no puede
        mostrar un mensaje. Un handler unico cubre tambien lo que lanza Starlette.
        """

        async with _cliente(app) as cliente:
            respuesta = await cliente.get("/no-existe")

        assert respuesta.status_code == 404
        assert respuesta.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
        assert respuesta.json()["status"] == 404

    async def test_un_405_conserva_su_status(self, app: FastAPI) -> None:
        @app.get("/solo-get")
        async def solo_get() -> dict[str, bool]:
            return {"ok": True}

        async with _cliente(app) as cliente:
            respuesta = await cliente.post("/solo-get")

        assert respuesta.status_code == 405
        assert respuesta.json()["status"] == 405


class TestRegistroDeHandlers:
    def test_todos_los_handlers_de_app_error_comparten_el_mismo_camino(self, app: FastAPI) -> None:
        """Todas las subclases de `AppError` pasan por el mismo handler.

        Si cada clase tuviera su propio handler, bastaria con que uno se olvidara de
        llamar a `problem_response` para que ese endpoint empiece a devolver otra
        cosa. Con un solo handler registrado para la clase base, el formato no se
        puede olvidar por endpoint.
        """
        from starlette._exception_handler import wrap_app_handling_exceptions

        manejadores = wrap_app_handling_exceptions
        assert manejadores is not None
        # `app.exception_handlers` no es publico en todas las versiones; se accede
        # por el dict que Starlette guarda en la instancia.
        registrados = getattr(app, "exception_handlers", {})
        assert AppError in registrados
        assert Exception in registrados

    def test_los_errores_de_app_cargan_el_detalle_y_el_extra(self) -> None:
        error = ConflictError("mensaje", extra={"a": 1})
        assert error.detail == "mensaje"
        assert error.extra == {"a": 1}
        assert str(error) == "mensaje"

    def test_extra_por_defecto_es_un_dict_vacio(self) -> None:
        """No `None`: el handler hace `if extra`, y un `None` unexpected seria un
        bug de tipo en un camino de error, que es el peor lugar para uno."""
        assert AppError("x").extra == {}
        assert AppError("x", extra=None).extra == {}


class TestLogDelError:
    async def test_el_error_de_aplicacion_se_registra_con_su_tipo(
        self, app: FastAPI, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """El log lleva el tipo de la excepcion, no solo el mensaje.

        El mensaje de un error de dominio suele ser identico entre dos llamadas con
        causas distintas. El nombre de la clase es lo que permite distinguirlas.

        Se lee con `capsys` y no con `caplog` porque la app configura structlog con
        `PrintLoggerFactory`: no pasa por el `logging` de la libreria estandar, asi
        que `caplog` no ve nada. Un test escrito con `caplog` fallaria siempre con
        la implementacion correcta, que es como se learns estos tests: el falla te
        dice que el mecanismo esta mal, no que el codigo este mal.
        """

        @app.get("/choque")
        async def choque() -> None:
            raise SlotUnavailableError("El horario dejo de estar libre")

        async with _cliente(app) as cliente:
            await cliente.get("/choque")

        salida = capsys.readouterr().out
        assert "error_de_aplicacion" in salida
        assert "SlotUnavailableError" in salida
        assert "409" in salida
        assert "/choque" in salida

    async def test_el_500_registra_el_mismo_id_que_entrega(
        self, app: FastAPI, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """El log y la respuesta llevan la misma referencia.

        Es el otro lado del test que verifica que el 500 reutiliza el `request_id`:
        que el id aparezca en los dos lugares, y no solo que la respuesta tenga
        algo con forma de referencia.
        """
        app.add_middleware(RequestContextMiddleware)

        @app.get("/explota")
        async def explota() -> None:
            raise RuntimeError("boom")

        async with _cliente(app) as cliente:
            respuesta = await cliente.get("/explota", headers={"x-request-id": "req-xyz-789"})

        assert "req-xyz-789" in respuesta.json()["detail"]
        assert "req-xyz-789" in capsys.readouterr().out
