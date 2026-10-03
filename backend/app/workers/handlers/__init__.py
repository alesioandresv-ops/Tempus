"""Handlers de jobs. Importarlos los registra en el dispatcher global.

El orden importa: `notifications` debe importarse antes de que alguien use el
dispatcher, y por eso este `__init__` lo hace en vez de dejar que cada modulo lo
importe por su cuenta.
"""

from app.workers.handlers import notifications as _notifications  # noqa: F401

__all__: list[str] = []
