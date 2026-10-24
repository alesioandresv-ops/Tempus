"""Pagos del plan Pro por transferencia (cobro manual, sin pasarela).

El flujo completo:

1. El dueno del negocio transfiere $45.000 a la cuenta Naranja X de la plataforma.
2. Abre el checkout (`/register?plan=pro`), confirma sus datos y aprieta
   "Enviar comprobante por WhatsApp". Aca se crea la fila `pro_payments`
   `pending` (`POST /api/v1/payments/intent`).
3. El comprobante viaja por WhatsApp al soporte, que lo cruza con la fila.
4. El admin aprueba en `/admin`: el negocio pasa a `pro` con `pro_until =
   now() + 30 dias`. Rechazar no toca el negocio: sigue vencido y puede volver
   a mandar un comprobante.

El admin decide, la maquina ejecuta. La activacion es un humano mirando el
comprobante del banco, no un webhook: por eso no hay firma ni token de pasarela,
pero si hay registro (`pro_payments`) de cada activacion y rechazo.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.time import now
from app.models.enums import BusinessPlan, BusinessStatus
from app.modules.businesses.models import Business
from app.modules.payments.pro_models import ProPayment, ProPaymentStatus

logger = get_logger(__name__)

#: Duracion de la activacion manual. Un mes redondo, igual que la suscripcion de MP.
DIAS_DE_PRO = 30


def datos_de_transferencia() -> dict[str, Any]:
    """Los datos de cobro que el checkout muestra. Vienen de configuracion.

    Salen de `Settings` y no de constantes a proposito: si cambia el CBU, el
    cambio es una variable de entorno y no un deploy.
    """
    s = get_settings()
    return {
        "banco": s.pro_transfer_bank,
        "cbu": s.pro_transfer_cbu,
        "alias": s.pro_transfer_alias,
        "titular": s.pro_transfer_titular,
        "monto": s.pro_transfer_price_ars,
        "moneda": "ARS",
        "whatsapp_soporte": s.pro_support_whatsapp,
    }


async def crear_intento(
    session: AsyncSession,
    business: Business,
    *,
    email: str,
    fecha_transferencia: str | None = None,
) -> ProPayment:
    """Registra el intento de pago en `pending`, identificado por el email.

    El `email` es el identificador que el admin usa para cruzar comprobante con
    cuenta; no se valida contra nada porque el que paga puede no ser el dueno del
    correo que declara -- y eso es justo lo que el admin va a verificar.
    """
    settings = get_settings()
    pago = ProPayment(
        business_id=business.id,
        business_name=business.name,
        email=email,
        amount=Decimal(str(settings.pro_transfer_price_ars)),
        currency="ARS",
        status=ProPaymentStatus.PENDING,
        method="naranja_x",
        notes=fecha_transferencia,
    )
    session.add(pago)
    await session.flush()
    logger.info(
        "pro_payment_intent_creado",
        payment_id=str(pago.id),
        business_id=str(business.id),
        email=email,
    )
    return pago


async def aprobar(
    session: AsyncSession,
    payment_id: uuid.UUID,
    *,
    dias: int = DIAS_DE_PRO,
) -> Business:
    """Aprueba un pago y activa `dias` de Pro para su negocio.

    La fila pasa a `approved` con `approved_at`, y el negocio a `plan=pro` con
    `pro_until`. Si el negocio ya estaba en Pro, la nueva vigencia **extiende**
    desde la existente (un pago encima de otro suma meses) en vez de pisarla.
    """
    pago = await session.get(ProPayment, payment_id)
    if pago is None:
        raise ValueError("Pago no encontrado")
    if pago.status is not ProPaymentStatus.PENDING:
        raise ValueError(f"El pago ya esta {pago.status.value}")

    negocio = await session.get(Business, pago.business_id)
    if negocio is None:
        raise ValueError("Negocio del pago no encontrado")

    pago.status = ProPaymentStatus.APPROVED
    pago.approved_at = now()

    # Pago sobre Pro vigente: extiende desde `pro_until`, no desde ahora.
    base = negocio.pro_until if negocio.plan == BusinessPlan.PRO and negocio.pro_until and negocio.pro_until > now() else now()
    negocio.plan = BusinessPlan.PRO
    negocio.pro_until = base + dt.timedelta(days=dias)
    # Reactivar por si estaba suspendido: un pago aprobado es una decision
    # comercial de la plataforma, y el status es estado administrativo.
    if negocio.status != BusinessStatus.ACTIVE:
        negocio.status = BusinessStatus.ACTIVE

    await session.flush()
    logger.info(
        "pro_payment_aprobado",
        payment_id=str(payment_id),
        business_id=str(negocio.id),
        pro_until=negocio.pro_until.isoformat(),
    )
    return negocio


async def rechazar(session: AsyncSession, payment_id: uuid.UUID, motivo: str | None = None) -> ProPayment:
    """Rechaza un comprobante. El negocio queda igual (vencido si lo estaba)."""
    pago = await session.get(ProPayment, payment_id)
    if pago is None:
        raise ValueError("Pago no encontrado")
    if pago.status is not ProPaymentStatus.PENDING:
        raise ValueError(f"El pago ya esta {pago.status.value}")

    pago.status = ProPaymentStatus.REJECTED
    if motivo:
        pago.notes = motivo
    await session.flush()
    logger.info("pro_payment_rechazado", payment_id=str(payment_id), motivo=motivo)
    return pago


async def ultimos_pagos(session: AsyncSession, business_id: uuid.UUID, limite: int = 5) -> list[ProPayment]:
    """Ultimos intentos de pago de un negocio, para el panel y el admin."""
    result = await session.execute(
        select(ProPayment)
        .where(ProPayment.business_id == business_id)
        .order_by(desc(ProPayment.created_at))
        .limit(limite)
    )
    return list(result.scalars().all())


async def ultimo_pago_pendiente(session: AsyncSession, business_id: uuid.UUID) -> ProPayment | None:
    """El intento `pending` mas reciente, si hay. Sirve para no duplicar filas."""
    result = await session.execute(
        select(ProPayment)
        .where(ProPayment.business_id == business_id, ProPayment.status == ProPaymentStatus.PENDING)
        .order_by(desc(ProPayment.created_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


def enlace_whatsapp_comprobante(business_name: str, email: str) -> str:
    """El mensaje pre-armado que abre WhatsApp al apretar el boton del checkout."""
    s = get_settings()
    mensaje = (
        "Hola Tempus PRO! 🚀\n"
        f"Acabo de transferir $45.000\n\n"
        f"Negocio: {business_name}\n"
        f"Email: {email}\n"
        f"CBU destino: {s.pro_transfer_cbu}\n"
        f"Alias: {s.pro_transfer_alias}\n\n"
        "Adjunto comprobante aquí abajo 👇"
    )
    from urllib.parse import quote

    return f"https://wa.me/{s.pro_support_whatsapp}?text={quote(mensaje)}"


__all__ = [
    "DIAS_DE_PRO",
    "aprobar",
    "crear_intento",
    "datos_de_transferencia",
    "enlace_whatsapp_comprobante",
    "rechazar",
    "ultimo_pago_pendiente",
    "ultimos_pagos",
]
