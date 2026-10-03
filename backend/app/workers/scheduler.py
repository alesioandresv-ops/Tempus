"""El tick que drena la outbox.

Es un `asyncio.Task` que corre dentro del proceso de la API, con dos motivos y no
por comodidad:

1. **En desarrollo y en el despliegue de un solo proceso**, un worker aparte seria
   un proceso mas que arrancar, monitorear y matar. Con el task inline el
   `uvicorn` de siempre manda recordatorios.
2. **Un recordatorio que depende de un cron externo no llega nunca si el cron no
   esta configurado.** Es el modo de fallo mas caro y mas silencioso: el sistema
   encola, la base muestra todo pendiente, y nadie se entera hasta que un cliente
   reclama que no le avisaron.

Por eso el tick corre **por defecto** (`ENABLE_INLINE_SCHEDULER=true`) y el endpoint
`/internal/scheduler/tick` existe como alternativa para despliegues que corren
varios procesos: si ademas hay un cron externo, los dos tickean la misma cola y no se
duplican mensajes, porque el claim es un `UPDATE` condicional y el que pierde no
toca la fila.

**Un worker por proceso.** Con N replicas, cada una corre su propio tick. No es un
problema: `drain_once` no reserva ni bloquea entre ticks, y la exclusion por fila
esta en el `UPDATE` del claim. Lo que si habria que evitar es un tick por *thread*
dentro del mismo proceso, porque ahi si se comparten las transacciones y el `UPDATE`
del claim de uno podria pisar el de otro.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from app.core.logging import get_logger
from app.workers.outbox import drain_once

logger = get_logger(__name__)

#: Fallos seguidos antes de avisar.
#:
#: El tick es corto y deberia ir vacio la mayor parte del tiempo -- solo hay trabajo
#: cuando hay recordatorios vencidos. Asi queMany ticks vacios es normal y no es una
#: alarma. Lo que si seria una alarma es un tick que falla siempre: por eso el
#: contador es de *fallos*, no de ejecuciones.
CONSECUTIVE_FAILURES_WARN = 5


class InlineScheduler:
    """Loop de ticks. Se arranca en el lifespan y se cancela en el shutdown.

    `tick` es inyectable para que los tests puedan contar invocaciones sin tocar la
    base. El default es el dreno real.
    """

    def __init__(
        self,
        interval_seconds: int,
        tick: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._interval = interval_seconds
        self._tick: Callable[[], Awaitable[None]] = tick or _tick_por_defecto
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        logger.info("scheduler_iniciando", intervalo_s=self._interval)
        fallos = 0

        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                # El cancelamiento del shutdown **no** es un fallo del tick. Sin este
                # `raise`, el loop seguiria y el `sleep` de abajo lanzaria otra vez
                # `CancelledError` en el mismo punto, escribiendo un error espurio en
                # cada apagado limpio.
                raise
            except Exception as exc:
                fallos += 1
                logger.error(
                    "scheduler_tick_fallo",
                    error=str(exc),
                    consecutivos=fallos,
                )
                if fallos >= CONSECUTIVE_FAILURES_WARN:
                    logger.warning(
                        "scheduler_tick_fallos_seguidos",
                        consecutivos=fallos,
                        detalle=(
                            "El tick falla de forma sostenida. Lo mas probable es la "
                            "conexion con la base, no la cola: si la base no entra, "
                            "tampoco entran los recordatorios, y reintentar mas "
                            "seguido no lo arregla."
                        ),
                    )
            else:
                if fallos:
                    logger.info("scheduler_tick_recuperado", fallos_previos=fallos)
                fallos = 0

            await asyncio.sleep(self._interval)

    def start(self) -> None:
        """Arranca el loop. Idempotente."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="tempus-outbox-scheduler")

    async def stop(self) -> None:
        """Cancela el loop y espera a que termine. Idempotente."""
        if self._task is None:
            return
        self._task.cancel()
        # El `suppress` importa: un task cancelado **siempre** lanza `CancelledError`
        # al awaitarlo, y sin envolverlo el shutdown termina en una excepcion que el
        # usuario ve como un fallo de arranque cuando en realidad es un apagado
        # limpio.
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("scheduler_detenido")

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()


async def _tick_por_defecto() -> None:
    """El dreno real, encapsulado para que el loop no dependa de la outbox."""
    await drain_once()


__all__ = ["CONSECUTIVE_FAILURES_WARN", "InlineScheduler"]
