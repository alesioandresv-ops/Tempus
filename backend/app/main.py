"""Composicion de la aplicacion.

Este es el unico lugar que conoce las piezas: configuracion, logging, base de
datos, middlewares y routers. El resto de la app recibe sus dependencias por
inyeccion, y por eso se puede testear sin levantar el servidor.
"""

from __future__ import annotations

import time as _time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.errors import register_exception_handlers
from app.api.routers import health
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger, set_request_id
from app.db.session import dispose_engine, get_engine

# Importar `app.workers` (y no `app.workers.scheduler`) a proposito: el paquete es
# el que registra los handlers y verifica que sus claves sean valores de `JobKind`.
# Importar el submodulo directo saltaria ese registro y el dispatcher arrancaria con
# la tabla de handlers vacia.
from app.workers import InlineScheduler

logger = get_logger(__name__)

REQUEST_ID_HEADER = "x-request-id"
REQUEST_ID_CONTEXT_KEY = "request_id"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Arranque y apagado.

    El orden importa: la configuracion se valida **antes** de configurar el
    logging, porque si la configuracion es invalida el proceso tiene que morir con
    un mensaje claro, y no con un traceback de structlog a medio configurar.
    """
    settings: Settings = app.state.settings
    configure_logging(
        log_format=settings.log_format,
        level=settings.log_level,
        redact_keys=settings.redaction_keys_lower,
    )
    logger.info(
        "iniciando",
        environment=settings.environment.value,
        database_host=_safe_host(settings.database_url),
        app_role=settings.db_app_role,
    )

    # No se hace ninguna consulta de negocio en el arranque. Un /readyz que depende
    # de que haya migraciones aplicadas convierte un despliegue ordenado en un
    # arranque que falla por un detalle de orden.
    app.state.engine = get_engine()

    # El tick arranca **despues** de crear el engine y no antes: el scheduler usa el
    # mismo pool, y arrancarlo primero significaria que su primer tick compite con
    # el propio arranque por conexiones.
    scheduler: InlineScheduler | None = None
    if settings.enable_inline_scheduler:
        scheduler = InlineScheduler(settings.scheduler_tick_seconds)
        scheduler.start()
        app.state.scheduler = scheduler
    else:
        # Se deja el atributo en `None` y no ausente a proposito: un `hasattr` que
        # devuelve `False` y un `None` son indistinguibles para el codigo que
        # chequea, y el chequeo va a ser "si hay scheduler, bajalo".
        app.state.scheduler = None

    try:
        yield
    finally:
        logger.info("apagando")
        # El scheduler se baja **antes** del engine. Al reves, el tick que este
        # corriendo en ese instante recibe una sesion sobre un pool cerrado y el
        # apagado termina en una excepcion que se lee como un fallo del worker.
        if scheduler is not None:
            await scheduler.stop()
        await dispose_engine()


def _safe_host(url: str) -> str:
    """Host de la URL de la base, sin usuario ni password."""
    try:
        _, _, rest = url.partition("://")
        authority = rest.split("/", 1)[0]
        return authority.rpartition("@")[2]
    except Exception:
        return "desconocido"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Propaga un `request_id` por todos los logs de la peticion.

    Sin esto, investigar un error reportado por un usuario significa buscar en el
    log a mano, y con el traffic de un tenant cruzando el del otro, no hay forma
    de saber cuales lineas son de la misma peticion.

    Acepta el `x-request-id` entrante: es lo que permiten las plataformas de
    hosting para correlacionar, y si el cliente no manda ninguno, se genera.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = _sanitize_request_id(incoming) or str(uuid.uuid4())
        set_request_id(request_id)
        request.state.request_id = request_id
        started = _time.perf_counter()
        try:
            response = await call_next(request)
        except BaseException:
            # El handler de `Exception` que registra la app vive en
            # `ServerErrorMiddleware`, que es el middleware **mas externo**: corre
            # despues de que este salio. Si la excepcion se propaga sin limpiar, el
            # contextvar queda con el id de una peticion que ya termino, y el
            # handler del 500 lo leeria como si fuera suyo.
            set_request_id(None)
            raise

        elapsed_ms = (_time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        try:
            # El log va **antes** de limpiar el contextvar. Al reves, esta linea
            # salia sin `request_id`: es la unica con status y duracion, o sea la
            # que se usa para encontrar el resto de la peticion.
            logger.info(
                "peticion",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                elapsed_ms=round(elapsed_ms, 2),
            )
        finally:
            set_request_id(None)
        return response


def _sanitize_request_id(value: str | None) -> str | None:
    """Acepta el id entrante solo si es seguro de reflejar en un header.

    Sin este filtro, un cliente puede mandar un id con saltos de linea y escribir
    lineas falsas en el log. Es un log injection chico, pero es un log injection.
    """
    if not value:
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 128:
        return None
    if not all(ch.isalnum() or ch in "-_." for ch in candidate):
        return None
    return candidate


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la app. Los tests la llaman con su propia configuracion."""
    resolved = settings or get_settings()

    app = FastAPI(
        title="Tempus API",
        version="0.1.0",
        summary="API de gestion de turnos",
        # El prefijo es `/api/v1`, con version dentro, y no solo `/api`. Dos
        # razones, y la segunda es la que duele si se ignora.
        #
        # 1. `PROJECT_MASTER`, `ARCHITECTURE` §... y los ADR nombran `/api/v1/...` en
        #    cada ruta. Un codigo en `/api` obliga a editar mas de noventa lineas de
        #    documentacion para que cuadren.
        # 2. `COOKIE_PATH` y `VITE_API_BASE_URL` apuntan a `/api/v1`, asi que el
        #    prefijo corto hace que la cookie de refresh se envie en TODAS las
        #    peticiones en vez de solo las de autenticacion. Eso no es un detalle
        #    cosmético: es exactamente la mitigacion de superficie que ese campo
        #    declara hacer, y queda anulada sin que nada falle.
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        # Sin esto FastAPI sirve `/redoc` en la raiz, que es una segunda
        # documentacion en una ruta que el proyecto no documenta y que nadie mantiene.
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved

    configure_logging(
        log_format=resolved.log_format,
        level=resolved.log_level,
        redact_keys=resolved.redaction_keys_lower,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER, "Idempotency-Key"],
        expose_headers=[REQUEST_ID_HEADER],
        # `allow_origins=["*"]` con credenciales es invalido en el protocolo; el
        # config lo rechaza y el default es explicito para que no se llegue a putter.
        max_age=600,
    )

    register_exception_handlers(app)

    # Health checks fuera del prefijo de version: las plataformas de hosting los
    # piden en la raiz y versionarlos seria una friccion sin beneficio.
    app.include_router(health.router)

    # Routers de la API v1
    from app.api.routers import auth as auth_router
    from app.api.routers import business as business_router
    from app.api.routers import internal as internal_router
    from app.api.routers import onboarding as onboarding_router
    from app.api.routers import public as public_router
    from app.api.routers import webhooks as webhooks_router

    app.include_router(auth_router.router, prefix="/api/v1")
    app.include_router(onboarding_router.router, prefix="/api/v1")
    app.include_router(public_router.router, prefix="/api/v1")
    app.include_router(internal_router.router, prefix="/api/v1")
    app.include_router(webhooks_router.router, prefix="/api/v1")
    # El panel va ultimo a proposito: es el unico router donde todos los handlers
    # exigen un principal, y registrarlo al final deja claro en esta lista que es
    # la superficie autenticada del producto.
    app.include_router(business_router.router, prefix="/api/v1")

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "name": "tempus",
            "docs": "/api/v1/docs",
            "health": "/healthz",
        }

    return app


app = create_app()
