"""Servicio de suscripciones: activar Pro, cancelar y aplicar el webhook de MP.

El mapeo de estados es **propio**, no el de MP: `authorized` y `active` de MP
caen los dos en `active`, y `suspended` cae en `paused`. El downgrade a `free`
por mora (`in_arrears`) o cancelacion se decide aca, actualizando
`businesses.plan`.

Las funciones reciben la `AsyncSession` del tenant ya con el GUC puesto: el
UPDATE sobre `businesses` pasa por la politica `businesses_write` de `0011` y
solo toca la fila del negocio actual. El privilegio de columna para `plan` lo
otorga la migracion `0019`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import now
from app.models.enums import BusinessPlan, MpSubscriptionStatus
from app.modules.businesses.models import Business
from app.modules.payments.config import decrypt_access_token, obtener_cuenta
from app.modules.payments.mercadopago import (
    MercadoPagoError,
    cancel_preapproval,
    create_preapproval,
)
from app.modules.payments.models import MpPaymentAccount, MpSubscription

#: Traduccion de estados de MP a los propios. Lo que no este aca se ignora: un
#: webhook con un estado desconocido no puede mover el plan por accidente.
_ESTADO_POR_MP: dict[str, MpSubscriptionStatus] = {
    "authorized": MpSubscriptionStatus.ACTIVE,
    "active": MpSubscriptionStatus.ACTIVE,
    "pending": MpSubscriptionStatus.PENDING,
    "paused": MpSubscriptionStatus.PAUSED,
    "suspended": MpSubscriptionStatus.PAUSED,
    "cancelled": MpSubscriptionStatus.CANCELLED,
    "in_arrears": MpSubscriptionStatus.IN_ARREARS,
}

#: Estados que dan de baja el plan Pro (moroso o cancelado).
_ESTADOS_QUE_BAJAN = frozenset({MpSubscriptionStatus.CANCELLED, MpSubscriptionStatus.IN_ARREARS})


async def _suscripcion(session: AsyncSession, business_id: uuid.UUID) -> MpSubscription | None:
    resultado = await session.execute(
        select(MpSubscription).where(MpSubscription.business_id == business_id)
    )
    return resultado.scalar_one_or_none()


async def _access_token(session: AsyncSession, business_id: uuid.UUID) -> str:
    resultado = await session.execute(
        select(MpPaymentAccount.access_token_encrypted).where(
            MpPaymentAccount.business_id == business_id
        )
    )
    encrypted = resultado.scalar_one_or_none()
    if not encrypted:
        raise ValueError("La cuenta de Mercado Pago no esta conectada para este negocio.")
    return decrypt_access_token(encrypted)


async def _set_plan(session: AsyncSession, business_id: uuid.UUID, plan: BusinessPlan) -> None:
    negocio = await session.get(Business, business_id)
    if negocio is not None:
        negocio.plan = plan


def _parse_fecha(valor: str | None) -> dt.datetime | None:
    if not valor:
        return None
    try:
        return dt.datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(valor: dt.datetime | None) -> str | None:
    return valor.isoformat() if valor is not None else None


async def estado_suscripcion(session: AsyncSession, business_id: uuid.UUID) -> dict[str, Any]:
    """Estado de la suscripcion tal como lo ve el panel.

    Con el plan salido de `businesses` y no del registro, porque es el plan el que
    la plataforma cobra y aplica: una fila de suscripcion `active` con plan `free`
    seria una contradiccion que aca se muestra tal cual, sin ocultarla.

    `status="none"` es el caso sin suscripcion (todavia no se activo Pro), que
    **no** es `pending` ni `cancelled`: ninguno de los dos dice "nunca existio".
    """
    negocio = await session.get(Business, business_id)
    suscripcion = await _suscripcion(session, business_id)
    return {
        "plan": str(negocio.plan if negocio is not None else BusinessPlan.FREE),
        "status": str(suscripcion.status) if suscripcion is not None else "none",
        "preapproval_id": suscripcion.preapproval_id if suscripcion is not None else None,
        "next_billing_at": _iso(suscripcion.next_billing_at if suscripcion else None),
        "activated_at": _iso(suscripcion.activated_at if suscripcion else None),
        "cancelled_at": _iso(suscripcion.cancelled_at if suscripcion else None),
    }


async def activar_plan_pro(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    back_url: str,
    transaction_amount: float,
    reason: str = "Suscripcion de plan Pro",
    frequency: int = 1,
    frequency_type: str = "months",
    currency: str = "ARS",
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Crea el preapproval de MP y deja la suscripcion en `pending`.

    Devuelve el `init_point` al que el admin tiene que ir para autorizar. El
    negocio pasa a `pro` cuando MP confirma (`authorized`/`active`) por webhook.
    """
    cuenta = await obtener_cuenta(session, business_id)
    if cuenta is None or not cuenta["activo"]:
        raise ValueError("La cuenta de Mercado Pago no esta conectada para este negocio.")

    existente = await _suscripcion(session, business_id)
    if existente is not None and existente.status is MpSubscriptionStatus.ACTIVE:
        raise ValueError("Este negocio ya tiene una suscripcion activa.")

    token = await _access_token(session, business_id)
    preapproval = await create_preapproval(
        token,
        client=client,
        reason=reason,
        back_url=back_url,
        frequency=frequency,
        frequency_type=frequency_type,
        transaction_amount=transaction_amount,
        currency_id=currency,
    )

    suscripcion = existente or MpSubscription(business_id=business_id)
    suscripcion.preapproval_id = preapproval.preapproval_id
    suscripcion.plan = BusinessPlan.PRO
    suscripcion.status = MpSubscriptionStatus.PENDING
    suscripcion.amount = Decimal(str(transaction_amount))
    suscripcion.currency = currency
    suscripcion.frequency = str(frequency)
    suscripcion.frequency_type = frequency_type
    suscripcion.next_billing_at = _parse_fecha(preapproval.next_billing_at)
    if existente is None:
        session.add(suscripcion)

    await session.flush()
    return {
        "subscription_id": preapproval.preapproval_id,
        "preapproval_id": preapproval.preapproval_id,
        "status": str(MpSubscriptionStatus.PENDING),
        "init_point": preapproval.init_point,
        "sandbox": cuenta["sandbox"],
        "amount": transaction_amount,
        "currency": currency,
        "next_billing_at": preapproval.next_billing_at,
    }


async def cancelar_suscripcion(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Da de baja la suscripcion en MP y deja el negocio en plan `free`."""
    suscripcion = await _suscripcion(session, business_id)
    if suscripcion is None:
        raise ValueError("No hay suscripcion para cancelar.")

    try:
        token = await _access_token(session, business_id)
        await cancel_preapproval(token, suscripcion.preapproval_id, client=client)
    except MercadoPagoError:
        # Aun si MP falla, se corta el cobro local: mejor quedar sin suscripcion
        # que seguir mostrando una que no se puede cancelar.
        pass

    suscripcion.status = MpSubscriptionStatus.CANCELLED
    suscripcion.cancelled_at = now()
    await _set_plan(session, business_id, BusinessPlan.FREE)
    await session.flush()
    return {
        "subscription_id": suscripcion.preapproval_id,
        "status": str(suscripcion.status),
        "cancelled_at": suscripcion.cancelled_at.isoformat(),
        "plan": BusinessPlan.FREE.value,
    }


async def aplicar_webhook_suscripcion(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    mp_status: str,
    mp_next_billing_at: str | None = None,
) -> dict[str, Any]:
    """Aplica al negocio el estado que llego por webhook desde MP."""
    suscripcion = await _suscripcion(session, business_id)
    if suscripcion is None:
        return {"aplicado": False, "razon": "sin_suscripcion"}

    nuevo = _ESTADO_POR_MP.get(mp_status)
    if nuevo is None:
        return {"aplicado": False, "razon": f"estado_no_mapeado:{mp_status}"}

    anterior = suscripcion.status
    suscripcion.status = nuevo
    next_billing = _parse_fecha(mp_next_billing_at)
    if next_billing is not None:
        suscripcion.next_billing_at = next_billing

    if nuevo is MpSubscriptionStatus.ACTIVE:
        suscripcion.activated_at = suscripcion.activated_at or now()
        await _set_plan(session, business_id, BusinessPlan.PRO)
    elif nuevo in _ESTADOS_QUE_BAJAN:
        await _set_plan(session, business_id, BusinessPlan.FREE)

    await session.flush()
    return {
        "aplicado": True,
        "status_anterior": str(anterior),
        "status": str(nuevo),
    }


__all__ = [
    "activar_plan_pro",
    "aplicar_webhook_suscripcion",
    "cancelar_suscripcion",
    "estado_suscripcion",
]
