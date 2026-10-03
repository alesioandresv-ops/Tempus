"""Router interno: endpoints de infraestructura.

Estos endpoints NO son públicos. Se llaman desde:
- Cloudflare Worker (cron trigger) para scheduler tick
- Health checks internos
- Monitoreo

El scheduler tick es lo que garantiza que los jobs se ejecuten
aunque el backend esté en un plan gratuito que se duerme.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.api.dependencies import get_settings_dep, limitar_tick
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.workers.dispatcher import dispatcher

logger = get_logger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post(
    "/scheduler/tick",
    status_code=status.HTTP_200_OK,
    summary="Ejecutar jobs pendientes",
    responses={
        200: {"description": "Jobs ejecutados"},
        401: {"description": "Token inválido"},
        429: {"description": "Demasiados ticks desde esta IP"},
    },
)
async def scheduler_tick(
    http_request: Request,
    x_internal_token: str = Header(..., description="Token interno del scheduler"),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, int]:
    """Ejecuta los jobs pendientes de la cola.

    Este endpoint es llamado por el Cloudflare Worker cada 30 segundos.
    El token interno (scheduler_tick_secret) es requerido para evitar
    que cualquiera pueda ejecutar jobs.

    Returns:
        dict con la cantidad de jobs ejecutados.
    """
    # El token se verifica **antes** del rate limit, y no al reves. Al reves--con el
    # limite como dependencia de ruta-- cualquiera que no tuviera el secreto gastaba el
    # cubo del Worker a proposito: le mandaba tres pedidos sin token y el Worker
    # legitimo, desde la misma IP, se comia un 429. Un limite que se puede disparar
    # sin credenciales no protege a quien si las tiene: leiona justo a quien lo tiene.
    #
    # Y tampoco es solo una cuestion de justicia: el 429 le confirma a quien no tiene el
    # token que el endpoint existe y que tiene un limite, que es informacion que el
    # 401 no le da.
    if x_internal_token != settings.scheduler_tick_secret.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token interno inválido",
        )

    await limitar_tick(request=http_request, settings=settings)

    # `session_scope()` y no `Depends(get_sessionmaker())`. Lo segundo era
    # `Depends` sobre el **sessionmaker ya instanciado**, que FastAPI invocaba para
    # obtener una sesion y no tenia a quien cerrarla: cada tick dejo una conexion
    # taken del pool para siempre. El Worker llama cada 30 segundos, asi que la
    # fuga era de unas 2.880 conexiones por dia y el pool--5 mas 10 de overflow-- se
    # agotaba en menos de tres horas, con el sintoma de que la base--y no solo este
    # endpoint-- empezaba a dar timeouts. `session_scope` cierra la sesion y ademas
    # commitea o deshace segun corresponda.
    async with session_scope() as session:
        executed = await dispatcher.poll_once(session)

    logger.info("scheduler_tick", executed=executed)

    return {"executed": executed}


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Health check interno",
)
async def internal_health() -> dict[str, str]:
    """Health check interno para monitoreo."""
    return {"status": "ok"}


__all__ = ["router"]
