"""Router de webhooks: recepción de eventos de Meta WhatsApp.

Meta envía webhooks cuando:
- Un cliente responde un mensaje
- Un mensaje es entregado
- Un mensaje es leído
- Cambia el estado de una plantilla

Estos webhooks se procesan de forma asíncrona (se encolan como jobs).
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select

from app.api.dependencies import get_settings_dep
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models.enums import WebhookProvider, WebhookStatus
from app.modules.notifications.models import WebhookEvent

#: Unico provider aceptado. El enum `webhook_provider` tiene **solo** `meta`, asi
#: que pasar `"whatsapp"` --que era lo que hacia este codigo-- no era un valor
#: alternativo: era un `IntegrityError` en cada webhook recibido, porque la columna
#: es un enum de PostgreSQL y no acepta un string fuera del conjunto. El sintoma era
#: Meta reintentando el webhook para siempre y nunca guardando el evento.
PROVIDER = WebhookProvider.META

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get(
    "/whatsapp",
    status_code=status.HTTP_200_OK,
    summary="Verificación de webhook de Meta",
    include_in_schema=False,
)
async def verify_webhook(
    hub_mode: str = "",
    hub_verify_token: str = "",
    hub_challenge: str = "",
    settings: Settings = Depends(get_settings_dep),
) -> str:
    """Verificación inicial del webhook de Meta.

    Meta llama a este endpoint con ?hub.mode=subscribe&hub.verify_token=...
    Si el token coincide, respondemos con el hub.challenge.
    """
    if hub_mode != "subscribe":
        raise HTTPException(status_code=400, detail="Modo inválido")

    expected_token = settings.meta_webhook_verify_token.get_secret_value()
    if hub_verify_token != expected_token:
        raise HTTPException(status_code=403, detail="Token inválido")

    return hub_challenge


@router.post(
    "/whatsapp",
    status_code=status.HTTP_200_OK,
    summary="Recibir webhook de Meta",
    responses={
        200: {"description": "Webhook recibido"},
        400: {"description": "Firma inválida"},
    },
)
async def receive_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, str]:
    """Recibe y procesa un webhook de Meta WhatsApp.

    1. Verifica la firma del webhook (X-Hub-Signature-256). **Obligatoria.**
    2. Guarda el evento en la tabla webhook_events.
    3. Encola un job para procesar el evento.

    Returns:
        {"status": "ok"} para que Meta reintente si falla.

    Abre su propia `session_scope()` y no recibe una por `Depends` a proposito: el
    `webhook_events` es `GlobalBase`, o sea global sin RLS, y `get_sessionmaker()`
    no commitea nunca. Con una sesion inyectada, el `flush` de abajo mandaba la fila
    a la base y el cierre de la sesion la borraba: el evento se perdia y la
    idempotencia no podia funcionar porque no habia nada que deduplicar.
    """
    body = await request.body()

    # **La firma es obligatoria, no opcional.** Antes el chequeo era
    # `if x_hub_signature_256:` y si el header no venia, el endpoint aceptaba el
    # payload sin verificar nada. Eso convierte un endpoint publico en una via libre
    # para escribir en `webhook_events` con el contenido que uno quiera -- incluido
    # falsificar "el cliente leyo tu mensaje" o "la plantilla fue aprobada". Meta
    # **siempre** manda `X-Hub-Signature-256`, asi que exigirlo no rompe la
    # integracion legitima; lo unico que rompe es a un atacante, que es el objetivo.
    #
    # Se responde 400 y no 403 a proposito: el endpoint no devuelve 2xx y Meta deja de
    # reintentar, que es lo que se quiere de un payload sin autenticar.
    if not x_hub_signature_256:
        logger.warning("webhook_sin_firma")
        raise HTTPException(status_code=400, detail="Firma ausente")

    expected_signature = _compute_signature(body, settings.meta_app_secret.get_secret_value())
    if not hmac.compare_digest(f"sha256={expected_signature}", x_hub_signature_256):
        logger.warning("webhook_firma_invalida")
        raise HTTPException(status_code=400, detail="Firma inválida")

    # Parsear payload
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc

    # Extraer información del webhook
    entry = payload.get("entry", [])
    if not entry:
        return {"status": "ok"}

    changes = entry[0].get("changes", [])
    if not changes:
        return {"status": "ok"}

    value = changes[0].get("value", {})
    event_type = value.get("messaging_product", "unknown")

    # Guardar evento en la base (idempotencia por provider + external_id)
    external_id = entry[0].get("id", "")
    if not external_id:
        # Sin id externo no hay contra que deduplicar, y guardarlo seria meter filas
        # que Meta va a reenviar porque no pudo matchear nada. Se responde 2xx para
        # que no entre en un ciclo de reintentos de algo que nunca se va a poder
        # identificar.
        logger.warning("webhook_sin_external_id")
        return {"status": "ok"}

    # Verificar si ya existe (idempotencia)
    async with session_scope() as session:
        existing = await session.execute(
            select(WebhookEvent).where(
                WebhookEvent.provider == PROVIDER,
                WebhookEvent.external_id == external_id,
            )
        )
        if existing.scalar_one_or_none():
            logger.info("webhook_duplicado", external_id=external_id)
            return {"status": "ok"}

        # Guardar evento
        session.add(
            WebhookEvent(
                provider=PROVIDER,
                external_id=external_id,
                event_type=event_type,
                payload=payload,
                status=WebhookStatus.RECEIVED,
            )
        )

    # TODO: Encolar job para procesar el webhook
    # Por ahora, solo log
    logger.info(
        "webhook_recibido",
        external_id=external_id,
        event_type=event_type,
    )

    return {"status": "ok"}


def _compute_signature(payload: bytes, app_secret: str) -> str:
    """Computa la firma HMAC-SHA256 del webhook."""
    return hmac.new(
        app_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()


__all__ = ["router"]
