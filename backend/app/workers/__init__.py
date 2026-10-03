"""Ejecucion asincrona: la cola de jobs y el dreno de la outbox.

Importar este paquete **registra los handlers** y verifica que sus claves sean
valores de `JobKind`. No es una comodidad, es lo que hace que el chequeo exista: si
los handlers se importaran solo cuando alguien los necesita, un handler con una
clave inventada pasaria el linter, pasaria los tests que no lo toquen, y fallaria en
produccion cuando el primer job de ese tipo aparezca -- tres meses despues, sin
aviso.
"""

from app.workers import handlers as _handlers  # noqa: F401 - import por efecto
from app.workers.dispatcher import JobDispatcher, dispatcher, register_handler
from app.workers.scheduler import InlineScheduler

__all__ = [
    "InlineScheduler",
    "JobDispatcher",
    "dispatcher",
    "register_handler",
]
