"""Health checks.

`/healthz` y `/readyz` no son lo mismo, y la diferencia importa:

- `/healthz` responde si el **proceso** esta vivo. No toca la base. Si la base se
  cae, `/healthz` sigue en 200, que es lo correcto: reiniciar el proceso no
  arregla una base caida y solo convierte un incidente en dos.
- `/readyz` responde si el proceso **puede atender**. Si la base no responde, da
  503 y la plataforma saca el contenedor de rotacion.

En el perfil gratuito de Render esto importa mas que en produccion normal: el
health check es lo que decide si la instancia recibe trafico.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status

from app.core import time
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import check_database

logger = get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness: el proceso responde")
async def healthz() -> dict[str, Any]:
    """Liveness. No consulta la base a proposito."""
    return {"status": "ok", "time": time.now().isoformat()}


@router.get("/readyz", summary="Readiness: el proceso puede atender")
async def readyz(response: Response) -> dict[str, Any]:
    """Readiness. Comprueba la base y devuelve 503 si no responde."""
    settings: Settings = get_settings()
    database_ok = await check_database()
    if not database_ok:
        # Sin log de warning en cada probe: el health check corre cada pocos
        # segundos y llenaria el log de ruido. Ademas la plataforma ya esta
        # avisando por su cuenta cuando un health check falla.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "database": "down"}
    return {
        "status": "ok",
        "database": "ok",
        "environment": settings.environment.value,
        "time": time.now().isoformat(),
    }
