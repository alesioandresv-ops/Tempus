"""Logging estructurado con redaccion.

Dos cosas que este modulo resuelve y que importan:

1. **Redaccion real, no de palabra.** El §43 prohibe que los logs expongan
   contraseñas, tokens y credenciales. La lista `LOG_REDACT_KEYS` se aplica
   recorriendo la estructura del evento, no buscando cadenas: si un token viaja
   dentro de un dict anidado o de una lista, se redacta igual. Un filtro por
   regex sobre el texto deja pasar el valor con la clave cambiada de nombre.

2. **request_id en todas las lineas.** Sin correlacion, un error de las 3am se
   investiga adivinando. El request_id se propaga por el contexto, no se pasa de
   funcion en funcion.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any, cast

import structlog

REDACTED = "[redactado]"

# Contexto de la request en curso. Un ContextVar y no un global: con varios
# workers asyncio, un global se pisa entre tareas.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Request id de la peticion en curso, o None fuera de una peticion."""
    return request_id_var.get()


def set_request_id(value: str | None) -> None:
    request_id_var.set(value)


def _redact_value(_: Any) -> str:
    return REDACTED


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any], *, keys: frozenset[str]
) -> MutableMapping[str, Any]:
    """Reemplaza los valores de las claves sensibles, a cualquier profundidad.

    Se recorre en profundidad porque un token puede venir anidado
    (`{"request": {"headers": {"authorization": ...}}}`) y basta con que aparezca
    una vez para que termine en un archivo de logs.

    `MutableMapping` y no `dict`: es la firma que structlog declara en su protocolo
    `EventProcessor`, y un processor con `dict` no es assignable ahi.
    """
    if not keys:
        return event_dict

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: (_redact_value(v) if k.lower() in keys else walk(v)) for k, v in value.items()
            }
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, tuple):
            return tuple(walk(item) for item in value)
        return value

    # `walk` es recursiva y devuelve la misma forma que recibe, asi que no se puede
    # anotar como `dict` sin mentir. El `cast` deja el contrato de la funcion
    # explicito en vez de dejar que mypy infiera `Any` para todo el arbol de logs.
    return cast("dict[str, Any]", walk(event_dict))


def add_request_id(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Agrega `request_id` a la linea si hay una request en curso."""
    request_id = request_id_var.get()
    if request_id is not None:
        event_dict.setdefault("request_id", request_id)
    return event_dict


def configure_logging(*, log_format: str, level: str, redact_keys: frozenset[str]) -> None:
    """Configura structlog y el logging estandar de la biblioteca.

    Los logs de SQLAlchemy y asyncio van al mismo pipeline: si el pipeline
    AnythingElse se queda en texto plano, se pierde la redaccion justo en la capa
    que mas probable es filtrar un valor.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(stream=sys.stdout, level=numeric_level, force=True)

    if log_format == "json":
        renderer: Any = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_request_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # La redaccion va despues de merge_contextvars para que tambien cubra lo
            # que viene del contexto, y antes de cualquier renderer: despues del
            # renderer ya se emitio texto con el secreto adentro.
            _make_redact(redact_keys),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _make_redact(redact_keys: frozenset[str]) -> Any:
    """Adaptador que fija `keys` para que `redact_processor` sea un processor."""

    def processor(
        _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        return redact_processor(_logger, _method, event_dict, keys=redact_keys)

    return processor


def get_logger(name: str | None = None) -> Any:
    """Logger de structlog."""
    return structlog.get_logger(name)
