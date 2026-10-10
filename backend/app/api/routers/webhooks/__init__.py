"""Router de webhooks: recepción de eventos de Meta WhatsApp y Mercado Pago.

Meta envía webhooks cuando un cliente responde, un mensaje se entrega o se lee,
o cambia el estado de una plantilla. Mercado Pago envía webhooks cuando cambia el
estado de un preapproval (suscripción) o de un pago de reserva.

El webhook de Meta se guarda y se encola; el de Mercado Pago se aplica en el acto
porque es el único camino por el que el plan `pro` y las reservas pagas cambian
de estado. Los dos comparten `webhook_events` como registro idempotente.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_settings_dep
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.time import now
from app.db.session import session_scope, tenant_session
from app.models.enums import WebhookProvider, WebhookStatus
from app.modules.notifications.models import WebhookEvent
from app.modules.payments.config import decrypt_access_token
from app.modules.payments.mercadopago import get_payment, get_preapproval
from app.modules.payments.models import MpPaymentAccount
from app.modules.payments.pagos import aplicar_webhook_pago
from app.modules.payments.subscriptions import aplicar_webhook_suscripcion

#: Unico provider aceptado. El enum `webhook_provider` tiene **solo** `meta`, asi
#: que pasar `"whatsapp"` --que era lo que hacia este codigo-- no era un valor
#: alternativo: era un `IntegrityError` en cada webhook recibido, porque la columna
#: es un enum de PostgreSQL y no acepta un string fuera del conjunto. El sintoma era
#: Meta reintentando el webhook para siempre y nunca guardando el evento.
PROVIDER = WebhookProvider.META

#: Provider de Mercado Pago (valor agregado al enum por `0019`).
MP_PROVIDER = WebhookProvider.MERCADOPAGO

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


# --------------------------------------------------------------------------- #
# Mercado Pago (Fase 9.1)
# --------------------------------------------------------------------------- #

#: Tipos de evento de MP que este endpoint sabe procesar. `subscription_preapproval`
#: es el nombre nuevo del topico de preapproval; los dos caen en lo mismo.
_PREAPPROVAL_TIPOS = frozenset({"preapproval", "subscription_preapproval"})
_PAGO_TIPOS = frozenset({"payment"})


def _mp_manifest(data_id: str, request_id: str | None, ts: str) -> str:
    """Cadena que firma MP: `id:{data.id};request-id:{header};ts:{ts};`."""
    return f"id:{data_id};request-id:{(request_id or '').lower()};ts:{ts};"


def _verificar_firma_mp(
    payload: dict[str, Any],
    x_signature: str | None,
    x_request_id: str | None,
    secret: str,
) -> bool:
    """Valida el header `x-signature` (`ts=...,v1=...`) de Mercado Pago.

    La firma cubre un manifest con el `data.id` del cuerpo, el `x-request-id` y el
    `ts` del propio header. Se compara con `compare_digest` para no filtrar por
    tiempo. Sin `v1` o sin `ts` no hay nada que comparar y se rechaza.
    """
    if not x_signature:
        return False
    partes: dict[str, str] = {}
    for trozo in x_signature.split(","):
        if "=" in trozo:
            clave, valor = trozo.split("=", 1)
            partes[clave.strip()] = valor.strip()
    ts = partes.get("ts")
    v1 = partes.get("v1")
    if not ts or not v1:
        return False
    data = payload.get("data") or {}
    manifest = _mp_manifest(str(data.get("id", "")), x_request_id, ts)
    esperado = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, v1)


def _clasificar_mp_event(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Devuelve `(kind, external_id)` segun el tipo de evento de MP.

    `kind` es `preapproval` o `payment`; cualquier otro topico se ignora (devuelve
    `(None, None)`). El `id` sale de `data.id`, con el formato viejo
    (`topic`/`resource`) como respaldo.
    """
    tipo = str(payload.get("type") or payload.get("topic") or "").lower()
    data = payload.get("data") or {}
    externo = data.get("id") or payload.get("id") or payload.get("resource")
    if not externo:
        return (None, None)
    external_id = str(externo)
    if tipo in _PREAPPROVAL_TIPOS:
        return ("preapproval", external_id)
    if tipo in _PAGO_TIPOS:
        return ("payment", external_id)
    return (None, None)


async def _resolver_negocio(kind: str, *candidatos: str | None) -> uuid.UUID | None:
    """Resuelve el negocio de un evento de MP con la funcion de pre-tenant.

    Prueba cada candidato (el id del recurso y, si vino, el `external_reference`)
    contra `mercadopago_business_for_event`, que corre como el rol BYPASSRLS: es la
    unica forma de saber de que negocio es un pago antes de tener el GUC.
    """
    for candidato in candidatos:
        if not candidato:
            continue
        async with session_scope() as session:
            encontrado = await session.scalar(
                text("SELECT mercadopago_business_for_event(:kind, :ext)"),
                {"kind": kind, "ext": candidato},
            )
        if encontrado is not None:
            return encontrado if isinstance(encontrado, uuid.UUID) else uuid.UUID(str(encontrado))
    return None


async def _token_cuenta(session: AsyncSession, business_id: uuid.UUID) -> str | None:
    """Access token descifrado de la cuenta activa del negocio, o `None`."""
    cifrado = await session.scalar(
        select(MpPaymentAccount.access_token_encrypted).where(
            MpPaymentAccount.business_id == business_id,
            MpPaymentAccount.is_active.is_(True),
        )
    )
    return decrypt_access_token(cifrado) if cifrado else None


async def _aplicar_evento(business_id: uuid.UUID, kind: str, external_id: str) -> dict[str, object]:
    """Consulta el recurso en MP y lo aplica dentro de la sesion del tenant."""
    async with tenant_session(business_id) as session:
        token = await _token_cuenta(session, business_id)
        if token is None:
            return {"aplicado": False, "razon": "sin_cuenta"}
        if kind == "preapproval":
            info = await get_preapproval(token, external_id)
            return await aplicar_webhook_suscripcion(
                session,
                business_id,
                mp_status=info.status,
                mp_next_billing_at=info.next_billing_at,
            )
        pago = await get_payment(token, external_id)
        return await aplicar_webhook_pago(
            session,
            business_id,
            payment_id=pago.payment_id,
            external_reference=pago.external_reference,
            mp_status=pago.status,
        )


@router.post(
    "/mercadopago",
    status_code=status.HTTP_200_OK,
    summary="Recibir webhook de Mercado Pago",
    responses={
        200: {"description": "Webhook recibido y aplicado"},
        400: {"description": "Firma o JSON invalidos"},
        500: {"description": "Error transitorio; MP reintenta"},
    },
)
async def receive_mercadopago_webhook(
    request: Request,
    x_signature: Annotated[str | None, Header(alias="x-signature")] = None,
    x_request_id: Annotated[str | None, Header(alias="x-request-id")] = None,
    settings: Settings = Depends(get_settings_dep),
) -> Any:
    """Recibe y aplica un webhook de Mercado Pago.

    1. Verifica la firma HMAC-SHA256 **si** hay `MERCADOPAGO_WEBHOOK_SECRET`
       configurado (en local puede ir vacio y se acepta sin verificar).
    2. Registra el evento en `webhook_events` para idempotencia.
    3. Resuelve el negocio con la funcion de pre-tenant y aplica el cambio con la
       sesion del tenant: el plan `pro` y la confirmacion de reservas pasan por aca.

    Devuelve 500 ante un error de procesamiento **a proposito**: MP reintenta, y el
    reintento reusa la fila `FAILED` (no esta `PROCESSED`) para volver a aplicar. Un
    evento ya `PROCESSED` se responde 200 sin tocar nada.
    """
    body = await request.body()
    try:
        payload = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc

    secret = settings.mercadopago_webhook_secret.get_secret_value()
    if secret and not _verificar_firma_mp(payload, x_signature, x_request_id, secret):
        logger.warning("mp_webhook_firma_invalida")
        raise HTTPException(status_code=400, detail="Firma inválida")

    kind, external_id = _clasificar_mp_event(payload)
    if kind is None or external_id is None:
        logger.info("mp_webhook_ignorado")
        return {"status": "ok"}

    data = payload.get("data") or {}
    external_reference = data.get("external_reference") or payload.get("external_reference")
    # El hash del cuerpo distingue un reintento exacto de un cambio de estado: dos
    # webhooks del mismo pago con estados distintos (pending y approved) no pueden
    # compartir external_id, y un reintento identico si.
    firma = hashlib.sha256(body).hexdigest()[:32]
    event_external_id = f"{kind}:{external_id}:{firma}"

    async with session_scope() as session:
        evento = await session.scalar(
            select(WebhookEvent).where(
                WebhookEvent.provider == MP_PROVIDER,
                WebhookEvent.external_id == event_external_id,
            )
        )
        if evento is not None and evento.status == WebhookStatus.PROCESSED:
            logger.info("mp_webhook_duplicado", external_id=event_external_id)
            return {"status": "ok"}
        if evento is None:
            evento = WebhookEvent(
                provider=MP_PROVIDER,
                external_id=event_external_id,
                event_type=kind,
                payload=payload,
                status=WebhookStatus.RECEIVED,
            )
            session.add(evento)

        business_id = await _resolver_negocio(kind, external_id, external_reference)
        if business_id is None:
            logger.info("mp_webhook_sin_negocio", kind=kind, external_id=external_id)
            evento.status = WebhookStatus.IGNORED
            return {"status": "ok"}

        try:
            resultado = await _aplicar_evento(business_id, kind, external_id)
        except Exception as exc:
            logger.exception("mp_webhook_error", external_id=event_external_id)
            evento.status = WebhookStatus.FAILED
            evento.error = str(exc)[:2000]
            return JSONResponse(status_code=500, content={"status": "error"})

        evento.status = WebhookStatus.PROCESSED
        evento.processed_at = now()
        logger.info("mp_webhook_procesado", kind=kind, external_id=external_id)
        return {"status": "ok", "resultado": {k: str(v) for k, v in resultado.items()}}


__all__ = ["router"]
