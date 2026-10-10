"""Pagos de reserva en el flujo publico (Fase 9.1b).

Dos pasos separados a proposito:

1. `calcular_pago_reserva` dice **si** una reserva de este negocio
   cobra por adelantado y cuanto. Es puro: lee la politica del
   negocio y el precio del servicio, y no toca nada. El endpoint
   publico lo llama **antes** de crear la reserva para decidir si
   el turno nace `pending_payment` o `confirmed`.

2. `crear_pago_reserva` crea el `BookingPayment` y la `preference`
   de Mercado Pago **despues** de que la reserva existe (el
   `external_reference` es el id de la reserva, y la FK compuesta
   necesita la fila). Si MP falla, la excepcion sube y la
   transaccion del endpoint se deshace: no quedan turnos
   `pending_payment` sin preference ni preferencias huerfanas.

El `init_point` solo se conoce al crear la preference, y no se
guarda: es la URL de checkout de MP, que caduca. La respuesta de
creacion la trae; el GET por token muestra el estado del pago.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.businesses.models import Business
from app.modules.payments.config import (
    calcular_monto_servicio,
    decrypt_access_token,
    obtener_cuenta,
    obtener_politica_pago,
)
from app.modules.payments.mercadopago import create_preference
from app.modules.payments.models import BookingPayment, MpPaymentAccount
from app.modules.services.models import Service

_SIN_CUENTA = (
    "El negocio exige pago por adelantado pero no tiene una cuenta "
    "de Mercado Pago activa. Configurala en el panel antes de "
    "cobrar online."
)


async def calcular_pago_reserva(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    service_price: Decimal,
) -> dict[str, Any] | None:
    """Politica y monto de una reserva, o `None` si no se cobra.

    `None` cubre los dos casos en que la reserva se confirma en el
    acto: politica `none` (o sin politica) y `deposit` con monto
    calculado en 0 (no deberia pasar, pero el tope del monto al
    total la hace imposible de confundir con un cobro).
    """
    politica = await obtener_politica_pago(session, business_id)
    if politica is None:
        return None
    monto = calcular_monto_servicio(
        base_amount=float(service_price),
        policy=politica["policy"],
        deposit_mode=politica["deposit_mode"],
        deposit_value=politica["deposit_value"],
        deposit_non_refundable=politica["deposit_non_refundable"],
    )
    if monto["deposit_amount"] <= 0:
        return None
    return {
        **monto,
        "policy": politica["policy"],
        "deposit_mode": politica["deposit_mode"],
    }


async def _access_token(session: AsyncSession, business_id: uuid.UUID) -> str:
    """Access token de la cuenta activa del negocio, o error explicativo."""
    cifrado = await session.scalar(
        select(MpPaymentAccount.access_token_encrypted).where(
            MpPaymentAccount.business_id == business_id,
            MpPaymentAccount.is_active.is_(True),
        )
    )
    if not cifrado:
        raise ValueError(_SIN_CUENTA)
    return decrypt_access_token(cifrado)


async def crear_pago_reserva(
    session: AsyncSession,
    *,
    business: Business,
    service: Service,
    booking_id: uuid.UUID,
    secure_token: str,
    currency: str,
    pago: dict[str, Any],
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Crea el registro de pago de una reserva y la preference de MP.

    Devuelve `preference_id` y el `init_point` (sandbox o produccion
    segun el flag de la cuenta) que el cliente tiene que abrir para
    pagar. El resto del monto queda en el local: se cobra al
    cliente en el turno.
    """
    cuenta = await obtener_cuenta(session, business.id)
    if cuenta is None or not cuenta["activo"]:
        raise ValueError(_SIN_CUENTA)
    token = await _access_token(session, business.id)

    pago_fila = BookingPayment(
        business_id=business.id,
        booking_id=booking_id,
        policy=pago["policy"],
        deposit_mode=pago["deposit_mode"],
        amount=Decimal(str(pago["deposit_amount"])),
        currency=currency,
        external_reference=str(booking_id),
    )
    session.add(pago_fila)
    # El `flush` es lo que da el id a la fila y lo deja visible para
    # el `create_preference` de abajo, sin cometer nada todavia.
    await session.flush()

    sitio = get_settings().public_site_base_url.rstrip("/")
    gestion = f"{sitio}/r/{secure_token}"
    preferencia = await create_preference(
        token,
        client=client,
        items=[
            {
                "title": f"{service.name} - {business.name}",
                "quantity": 1,
                "unit_price": pago["deposit_amount"],
                "currency_id": currency,
            }
        ],
        external_reference=str(booking_id),
        back_urls={
            "success": f"{gestion}?pago=aprobado",
            "failure": f"{gestion}?pago=rechazado",
            "pending": gestion,
        },
        description=f"Reserva de {service.name} en {business.name}",
    )
    pago_fila.preference_id = preferencia.preference_id
    await session.flush()

    # MP devuelve los dos puntos (sandbox y produccion) y la cuenta
    # del negocio dice cual corresponde a esta cuenta.
    init_point = (
        preferencia.sandbox_init_point if cuenta["sandbox"] else None
    ) or preferencia.init_point
    return {
        "preference_id": preferencia.preference_id,
        "init_point": init_point,
        "sandbox": cuenta["sandbox"],
    }


__all__ = [
    "calcular_pago_reserva",
    "crear_pago_reserva",
]
