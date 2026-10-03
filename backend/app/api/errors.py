"""Errores de la API con envelope RFC 9457.

Se usa `application/problem+json` (RFC 9457) en lugar de un envelope inventado,
porque es un formato que ya define `type`, `title`, `status`, `detail` e
`instance`, y las herramientas de cliente lo entienden.

Regla de este modulo: **el `detail` de cara al cliente nunca contiene una
excepcion**. Un `IntegrityError` de PostgreSQL dice el nombre de la tabla, el
nombre de la restriccion y a veces el valor que se violo. Eso es informacion de la
base para cualquiera que pueda llamar a la API, asi que se registra en el log y se
devuelve un mensaje generico con el `request_id` para poder correlacionar.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, get_request_id

logger = get_logger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"


class AppError(Exception):
    """Error de dominio con su forma de respuesta ya decidida.

    Los modulos levantan esto. La capa HTTP lo traduce. Un modulo de dominio que
    necesita saber de codigos HTTP es un modulo de dominio acoplado a FastAPI.
    """

    status_code: int = status.HTTP_400_BAD_REQUEST
    title: str = "Error"
    error_type: str = "about:blank"

    #: Campos que `extra` no puede tocar. `detail` NO esta en la lista a proposito:
    #: es libre por diseno, y un error de dominio puede querer adornar el mensaje.
    RESERVADOS: ClassVar[frozenset[str]] = frozenset({"type", "title", "status", "instance"})

    def __init__(self, detail: str, *, extra: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        # `extra` se valida **aca**, al construir el error, y no en la capa HTTP.
        #
        # `extra={"status": 200}` produce un body que dice 200 con un HTTP 409: el
        # cuerpo y el status dejan de contar la misma historia, y el cliente que
        # decide por el body cree que la operacion salio bien. Que se rechace en el
        # `__init__` y no al serializar importa por dos razones: el error aparece en
        # el test que lo escribio, con un mensaje que dice cual fue la clave; y en
        # la capa HTTP el `ValueError` caeria en el handler de `Exception` y volveria
        # un 500, con el mensaje original devuelto al cliente.
        if extra:
            pisados = sorted(self.RESERVADOS & set(extra))
            if pisados:
                raise ValueError(
                    f"{type(self).__name__}.extra no puede pisar los campos de RFC 9457 "
                    f"{pisados}. Usar un nombre propio, por ejemplo 'slot' o 'code'."
                )
        self.extra = extra or {}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    title = "No encontrado"
    error_type = "about:blank"


class ConflictError(AppError):
    """Conflicto de estado. El slot ocupado cae aca (ADR-0008)."""

    status_code = status.HTTP_409_CONFLICT
    title = "Conflicto"
    error_type = "about:blank"


class SlotUnavailableError(ConflictError):
    """El horario dejo de estar disponible entre que se pidio y se reservo.

    Es un 409 y no un 400: la peticion era valida, el mundo cambio. El cliente debe
    repreguntar disponibilidad, no corregir el pedido.
    """

    title = "Horario no disponible"


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    title = "Datos invalidos"
    error_type = "about:blank"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    title = "No autenticado"
    error_type = "about:blank"


class AuthorizationError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    title = "Sin permiso"
    error_type = "about:blank"


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    title = "Demasiadas peticiones"
    error_type = "about:blank"


def problem_response(
    *,
    status_code: int,
    title: str,
    detail: str,
    request: Request,
    error_type: str = "about:blank",
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    """Construye la respuesta de problema."""
    body: dict[str, Any] = {
        "type": error_type,
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": str(request.url.path),
    }
    if extra:
        # Defensa en profundidad, y no el mecanismo principal: `AppError` ya
        # rechaza los nombres reservados al construirse. Igual el orden es
        # `{**extra, **body}` y no `body.update(extra)`.
        #
        # Con el `update` al final, un `extra` con `status` deja un body que
        # contradice el status HTTP, y el cliente que decide por el body cree que
        # la operación salió bien. Ningun test de status lo detectaría: el status
        # HTTP seguiría siendo el correcto. Acá el body gana siempre.
        body = {**extra, **body}
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type=PROBLEM_CONTENT_TYPE,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Registra los handlers en la app."""

    @app.exception_handler(AuthenticationError)
    async def _auth_error(request: Request, exc: AuthenticationError) -> JSONResponse:
        logger.warning(
            "error_de_aplicacion",
            error_type=type(exc).__name__,
            status=exc.status_code,
            detail=exc.detail,
            path=str(request.url.path),
        )
        response = problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=exc.detail,
            request=request,
            error_type=exc.error_type,
            extra=exc.extra,
        )
        response.headers["WWW-Authenticate"] = 'Bearer realm="tempus"'
        return response

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "error_de_aplicacion",
            error_type=type(exc).__name__,
            status=exc.status_code,
            detail=exc.detail,
            path=str(request.url.path),
        )
        response = problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=exc.detail,
            request=request,
            error_type=exc.error_type,
            extra=exc.extra,
        )
        # `Retry-After` en el header, no solo en el body. RFC 6585 lo pide para el
        # 429, y la razon de que sea obligatorio no es la norma: los clientes--y
        # los proxies-- leen **headers**, no el cuerpo de un
        # `application/problem+json`. Un `429` sin el header es indistinguible de
        # "volve a intentar ya", que es justo lo contrario de lo que significa, y el
        # bucle de reintentos lo paga el servidor. `retry_after` ya venia calculado
        # en `extra` desde antes; lo que faltaba era sacarlo a la cabecera.
        if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            retry_after = exc.extra.get("retry_after")
            if retry_after is not None:
                response.headers["Retry-After"] = str(int(retry_after))
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Los errores de validacion de pydantic si son seguros de devolver: traen
        # el nombre del campo y por que fallo, no datos del negocio.
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="Datos invalidos",
            detail="El pedido no cumple el esquema.",
            request=request,
            extra={"errors": _safe_validation_errors(exc)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Error."
        return problem_response(
            status_code=exc.status_code,
            title="Error",
            detail=detail,
            request=request,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # El error va al log con su tipo y su traceback; la respuesta al cliente no
        # lo incluye. El request_id es el puente entre los dos.
        #
        # De donde se saca el id, en orden:
        #   1. `request.state`, que pone `RequestContextMiddleware`.
        #   2. el contextvar, por si el handler se usa sin middleware.
        #   3. uno nuevo, para no publicar "Referencia: None".
        #
        # El contextvar **no** alcanza como fuente principal y esa es la parte que
        # cuesta entender. El handler de `Exception` corre en
        # `ServerErrorMiddleware`, que es el middleware mas externo: cuando la
        # excepcion se propaga hacia el, `RequestContextMiddleware` ya limpio su
        # contextvar en el `except`. O sea que el id ya no esta ahi. Y antes de
        # arreglarlo, este handler generaba su propio `uuid4()`, con lo cual la
        # referencia que recibia el usuario no aparecia en ninguna linea de log: el
        # puente que el comentario de arriba promete no existia, y buscar la
        # referencia en los logs no encontraba nada.
        #
        # `request.state` si sobrevive, porque viaja con el objeto `Request` por
        # toda la cadena de middlewares.
        request_id = getattr(request.state, "request_id", None) or get_request_id()
        if not request_id:
            request_id = str(uuid.uuid4())
        logger.exception(
            "error_no_manejado",
            error_type=type(exc).__name__,
            path=str(request.url.path),
            method=request.method,
            request_id=request_id,
        )
        return problem_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Error interno",
            detail=f"Ocurrio un error. Referencia: {request_id}",
            request=request,
        )


def _safe_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Reduce los errores de pydantic a lo publicable."""
    safe: list[dict[str, Any]] = []
    for err in exc.errors():
        safe.append(
            {
                "field": ".".join(str(part) for part in err.get("loc", ())),
                "message": err.get("msg", ""),
                "type": err.get("type", ""),
            }
        )
    return safe
