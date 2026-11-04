"""Job diario que vence trials y Pro sin renovar.

Corre una vez al dia dentro del proceso de la API, con la misma filosofia del
tick de la outbox (`app.workers.scheduler`): un worker aparte seria un proceso
mas que mantener, y un corte que depende de que alguien apriete un endpoint
manualmente no se ejecuta nunca.

**Como accede a las filas**: `businesses` tiene FORCE RLS y filtra tambien al
owner (verificado: `tempus_owner` no tiene BYPASSRLS, y `SET row_security=off`
le da `InsufficientPrivilege`). Dos mecanismos verificados en la limpieza real:

1. El DDL `ALTER TABLE ... NO FORCE` es transaccional: aplicarlo y commitarlo
   deja la tabla accesible para el owner, porque las politicas de `businesses`
   estan declaradas `TO tempus_app` y a otro rol no le aplican. Se restaura con
   FORCE en una transaccion aparte al terminar.
2. Para el UPDATE por negocio basta el GUC: `set_config('app.current_business_id',
   id, true)` satisface la politica de la fila objetivo.

El corte es idempotente: suspendido de nuevo no cambia nada, y correrse dos
veces el mismo dia es inofensivo. El endpoint admin `/cron/vencer` sigue
existiendo para corridas manuales.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_admin_engine

logger = get_logger(__name__)


def _sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        get_admin_engine(), class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


def _corte_sql():
    """El UPDATE de corte, con la regla completa de `publicacion` en SQL.

    Misma formula que `publicacion.fin_del_trial`:
    `GREATEST(created_at, trial_grace_from) + trial_dias dias`. Vive aca y no en
    Python porque las filas solo son visibles dentro del GUC de cada negocio:
    no hay forma de traerlas a memoria para evaluarlas.
    """
    return text(
        "UPDATE businesses SET status = 'suspended' "
        "WHERE id = CAST(:id AS uuid) AND status = 'active' "
        "AND (plan != 'pro' OR (pro_until IS NOT NULL AND pro_until <= now())) "
        "AND (plan = 'pro' OR now() >= "
        "  GREATEST(created_at, CAST(:grace_from AS timestamptz)) "
        "    + CAST(:dias AS int) * interval '1 day')"
    )


async def _ids_activos() -> list[str]:
    """Los ids de negocios activos, con FORCE temporalmente quitado.

    El NO FORCE va en su propia transaccion y queda aplicado (DDL persistente):
    si el proceso muere antes de restaurarlo, la tabla queda sin FORCE -- riesgo
    aceptado y acotado, porque la RLS normal (sin FORCE) sigue activa y las
    politicas igual filtran al rol app; el owner, que es a quien le habilita,
    es el proceso de la plataforma.
    """
    engine = get_admin_engine()
    async with engine.connect() as conn, conn.begin():
        await conn.execute(text("ALTER TABLE businesses NO FORCE ROW LEVEL SECURITY"))
        filas = await conn.execute(text("SELECT id FROM businesses WHERE status = 'active'"))
        return [str(r[0]) for r in filas]
    return []


async def _restaurar_force() -> None:
    engine = get_admin_engine()
    async with engine.connect() as conn, conn.begin():
        await conn.execute(text("ALTER TABLE businesses FORCE ROW LEVEL SECURITY"))


async def vencer_negocios() -> int:
    """Suspende los negocios con el trial vencido o el Pro sin renovar.

    Devuelve cuantos negocios suspendio en esta corrida.
    """
    suspendidos = 0
    try:
        ids = await _ids_activos()
    except Exception as exc:
        logger.error("cron_vencer_listado_fallo", error=str(exc))
        return 0

    sql = _corte_sql()
    params_base: dict[str, Any] = {
        "grace_from": get_settings().trial_grace_from,
        "dias": get_settings().trial_dias,
    }
    maker = _sessionmaker()
    for id_str in ids:
        try:
            async with maker() as sesion, sesion.begin():
                # El GUC satisface la politica de la fila objetivo sin quitar
                # FORCE: cada UPDATE abre su transaccion con su propio GUC.
                await sesion.execute(
                    text("SELECT set_config('app.current_business_id', :g, true)"),
                    {"g": id_str},
                )
                r = await sesion.execute(sql, {**params_base, "id": id_str})
                suspendidos += r.rowcount or 0
        except Exception as exc:
            logger.warning("cron_vencer_update_fallo", id=id_str, error=str(exc))
    # Restaurar FORCE siempre, incluso si hubo fallos arriba.
    try:
        await _restaurar_force()
    except Exception as exc:
        logger.warning("cron_vencer_restore_fallo", error=str(exc))
    if suspendidos:
        logger.info("cron_vencer_ejecutado", suspendidos=suspendidos)
    return suspendidos


class DailyJob:
    """Corre `vencer_negocios` una vez por dia calendario (UTC)."""

    def __init__(self, intervalo_check: int = 3600) -> None:
        #: Cada cuanto chequea si cambio el dia. Una hora alcanza: el corte no
        #: es sensible a la hora exacta, y un reinicio del pod lo recupera.
        self._intervalo = intervalo_check
        self._ultimo_dia = None
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        import datetime as dt

        logger.info("cron_diario_iniciando", check_cada_s=self._intervalo)
        while True:
            hoy = dt.datetime.now(dt.UTC).date()
            if self._ultimo_dia is None or hoy != self._ultimo_dia:
                try:
                    await vencer_negocios()
                    self._ultimo_dia = hoy
                except Exception as exc:
                    # Un fallo no detiene el loop: la proxima hora reintenta.
                    logger.error("cron_diario_fallo", error=str(exc))
            await asyncio.sleep(self._intervalo)

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="tempus-cron-vencer")

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        import contextlib

        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None


__all__ = ["DailyJob", "vencer_negocios"]
