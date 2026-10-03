"""Job dispatcher: lee trabajos pendientes de la cola y los ejecuta.

La cola vive en PostgreSQL (tabla `jobs`), no en memoria. Esto garantiza que:
- Un worker que cae no pierde trabajos.
- Múltiples workers pueden correr en paralelo.
- Los trabajos sobreviven un restart.

El dispatcher usa leasing: un worker toma un trabajo con `locked_by` y
`locked_at`. Si el proceso muere, otro worker ve el lock vencido y lo
reintenta.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.time import now
from app.db.session import get_sessionmaker
from app.db.system_models import Job
from app.models.enums import JobStatus

logger = get_logger(__name__)

#: Tiempo máximo que un job puede estar locked antes de considerarse abandonado.
LOCK_TIMEOUT_SECONDS = 300  # 5 minutos

#: Intervalo entre polls de la cola.
POLL_INTERVAL_SECONDS = 5


Handler = Callable[[AsyncSession, Job], Coroutine[Any, Any, None]]


class JobDispatcher:
    """Dispatcher de jobs.

    Registra handlers por `kind` y ejecuta jobs pendientes.
    """

    def __init__(self, worker_id: str | None = None) -> None:
        self._handlers: dict[str, Handler] = {}
        #: Identidad del worker en la columna `locked_by`. Antes era el literal
        #: `"worker-1"` en el `UPDATE`, o sea que todos los procesos de un despliegue
        #: escribian el mismo valor y no habia forma de saber en el log que proceso
        #: estaba ejecutando que. Es diagnostico, no de correctitud: el `UPDATE`
        #: condicional sobre `status` ya evita la doble ejecucion.
        self._worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"

    def register(self, kind: str, handler: Handler) -> None:
        """Registra un handler para un tipo de job."""
        self._handlers[kind] = handler

    async def poll_once(self, session: AsyncSession) -> int:
        """Busca y ejecuta jobs pendientes. Retorna cuántos ejecutó.

        **Commitea al final y no por job.** Es lo unico que hace que este metodo
        funcione: sin el `commit`, `_mark_done` y `_mark_failed` quedan como
        cambios sin persistir en una sesion que se cierra, y el `rollback` del
        llamador los borra. El sintoma es desconcertante -- el handler se ejecuta,
        el log dice que se ejecuto, y el job vuelve a `pending` en la base para la
        proxima vuelta, indefinidamente. El trabajo se repite y nunca termina.

        Por lote y no por job: un `commit` por job multiplica las transacciones sin
        ganar nada, porque el estado de un job no depende del anterior.
        """
        # Buscar jobs listos para ejecutar
        result = await session.execute(
            select(Job)
            .where(
                Job.status == JobStatus.PENDING,
                Job.run_at <= now(),
                (Job.locked_at.is_(None))
                | (Job.locked_at < now() - dt.timedelta(seconds=LOCK_TIMEOUT_SECONDS)),
            )
            .order_by(Job.run_at)
            .limit(10)
        )
        jobs = list(result.scalars())

        if not jobs:
            return 0

        executed = 0
        for job in jobs:
            # Tomar el job con leasing
            locked = await session.execute(
                update(Job)
                .where(Job.id == job.id, Job.status == JobStatus.PENDING)
                .values(locked_at=now(), locked_by=self._worker_id, status=JobStatus.RUNNING)
                .returning(Job.id)
            )
            if not locked.scalar_one_or_none():
                continue  # Otro worker lo tomó

            # Ejecutar
            handler = self._handlers.get(job.kind.value)
            if not handler:
                logger.warning("job_sin_handler", job_id=str(job.id), kind=job.kind.value)
                self._mark_failed(job, f"No handler registered para kind={job.kind.value!r}")
                continue

            try:
                await handler(session, job)
                self._mark_done(job)
                executed += 1
            except Exception as e:
                logger.error("job_fallo", job_id=str(job.id), error=str(e))
                self._mark_failed(job, str(e))

        await session.commit()
        return executed

    def _mark_done(self, job: Job) -> None:
        """Marca un job como completado."""
        job.status = JobStatus.DONE
        job.locked_at = None
        job.locked_by = None

    def _mark_failed(self, job: Job, error: str) -> None:
        """Marca un job como fallido.

        Sin `async`: solo muta atributos y el `commit` de `poll_once` los persiste.
        Que fuera `async` sin un `await` adentro obligaba a todos los llamadores a
        hacer `await self._mark_failed(...)` y hacia creer --peor-- que la escritura
        ya estaba en la base cuando en realidadenia un cambio pendiente.
        """
        job.attempts += 1
        job.last_error = error
        job.locked_at = None
        job.locked_by = None

        if job.attempts >= job.max_attempts:
            job.status = JobStatus.DEAD
        else:
            job.status = JobStatus.PENDING
            # Reintentar con backoff exponencial
            job.run_at = now() + dt.timedelta(minutes=2**job.attempts)

    async def run_forever(self) -> None:
        """Loop principal del worker.

        Usa `session_scope()` y no `get_sessionmaker()` a mano: `poll_once` ya
        commitea, pero `session_scope` ademas hace el `rollback` ante una excepcion,
        y sin el una falla de red deja la sesion colgada con una transaccion abierta
        que el pool no devuelve nunca. Con muchos errores seguidos eso es el pool
        entero leaking hasta que la app deja de atender peticiones.
        """
        logger.info("worker_iniciando", worker_id=self._worker_id)
        sessionmaker = get_sessionmaker()

        while True:
            try:
                async with sessionmaker() as session:
                    executed = await self.poll_once(session)
                    if executed:
                        logger.info("worker_ejecuto", count=executed)
            except Exception as e:
                logger.error("worker_error", error=str(e))

            await asyncio.sleep(POLL_INTERVAL_SECONDS)


# Dispatcher global
dispatcher = JobDispatcher()


def register_handler(kind: str) -> Callable[[Handler], Handler]:
    """Decorator para registrar un handler."""

    def decorator(func: Handler) -> Handler:
        dispatcher.register(kind, func)
        return func

    return decorator


__all__ = [
    "JobDispatcher",
    "dispatcher",
    "register_handler",
]
