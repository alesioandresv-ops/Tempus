"""Pagos de reserva via Mercado Pago (Fase 9.1).

El webhook de `payment` de MP llega sin GUC: resuelve el negocio por
`mercadopago_business_for_event` (0019) y despues aplica el pago dentro de la
sesion del tenant. Este modulo es la unica pieza de la Fase 9.1 que toca
`booking_payments` y `bookings` juntas, y por eso el mapeo de estados y la
transicion de la reserva viven aca y no en el cliente HTTP ni en el router.

**Un pago aprobado confirma el turno; uno rechazado lo libera.** El turno nace
`pending_payment` (ocupa la agenda, ver `OCCUPYING_STATUSES`) y el pago lo mueve
a `confirmed`. Si MP rechaza o cancela, el turno se cancela: un horario que nadie
va a pagar no puede seguir bloqueando la agenda. Un reembolso se registra en
`refunded_amount` pero **no** reabre el turno: devolver plata no es revivir una
reserva.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import now
from app.models.enums import BookingStatus, MpPaymentStatus
from app.modules.bookings.models import Booking
from app.modules.notifications.scheduler import schedule_for_booking
from app.modules.payments.models import BookingPayment

#: Traduccion de los estados de pago de MP a los propios. Lo que no este aca se
#: ignora: un webhook con un estado desconocido no puede mover una reserva por
#: accidente, que es el mismo criterio del mapeo de suscripciones.
_ESTADO_PAGO_POR_MP: dict[str, MpPaymentStatus] = {
    "pending": MpPaymentStatus.PENDING,
    "in_process": MpPaymentStatus.IN_PROCESS,
    "authorized": MpPaymentStatus.IN_PROCESS,
    "approved": MpPaymentStatus.APPROVED,
    "rejected": MpPaymentStatus.REJECTED,
    "cancelled": MpPaymentStatus.REJECTED,
    "refunded": MpPaymentStatus.REFUNDED,
    "charged_back": MpPaymentStatus.REFUNDED,
}

#: Estados que dan de baja la reserva esperando pago.
_ESTADOS_QUE_CANCELAN = frozenset({MpPaymentStatus.REJECTED})


async def aplicar_webhook_pago(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    payment_id: str | None,
    external_reference: str | None,
    mp_status: str,
    status_detail: str | None = None,
) -> dict[str, Any]:
    """Aplica al pago de reserva el estado que llego por webhook desde MP.

    Busca el `BookingPayment` por `payment_id` (reintentos de un pago ya visto) o
    por `external_reference` (la primera notificacion, que todavia no tiene el
    `payment_id` guardado). Sin fila devuelve `aplicado=False` en vez de fallar:
    un webhook de un pago que no es de este sistema se descarta, no se reintenta
    para siempre.
    """
    filtros = []
    if payment_id:
        filtros.append(BookingPayment.payment_id == payment_id)
    if external_reference:
        filtros.append(BookingPayment.external_reference == external_reference)
    if not filtros:
        return {"aplicado": False, "razon": "sin_identificador"}

    resultado = await session.execute(
        select(BookingPayment).where(
            BookingPayment.business_id == business_id,
            or_(*filtros),
        )
    )
    pago = resultado.scalar_one_or_none()
    if pago is None:
        return {"aplicado": False, "razon": "pago_no_encontrado"}

    nuevo = _ESTADO_PAGO_POR_MP.get(mp_status)
    if nuevo is None:
        return {"aplicado": False, "razon": f"estado_no_mapeado:{mp_status}"}

    if payment_id:
        pago.payment_id = payment_id
    pago.status = nuevo
    if status_detail is not None:
        pago.status_detail = status_detail
    if nuevo is MpPaymentStatus.REFUNDED and pago.refunded_at is None:
        pago.refunded_amount = pago.amount
        pago.refunded_at = now()

    # La transicion de la reserva solo aplica si todavia espera el pago: un turno
    # ya cancelado o confirmado a mano no se reabre por un webhook tardio.
    transicion: str | None = None
    reserva = await session.get(Booking, pago.booking_id)
    if reserva is not None and reserva.status is BookingStatus.PENDING_PAYMENT:
        if nuevo is MpPaymentStatus.APPROVED:
            reserva.status = BookingStatus.CONFIRMED
            transicion = "confirmada"
        elif nuevo in _ESTADOS_QUE_CANCELAN:
            # `cancelled` exige `cancelled_at` (ck_bookings_cancelled_flag_matches_timestamp):
            # un pago rechazado cancela el turno como lo haria el negocio a mano.
            reserva.status = BookingStatus.CANCELLED
            reserva.cancelled_at = now()
            reserva.cancel_reason = "Pago rechazado por Mercado Pago"
            transicion = "cancelada"

    if transicion == "confirmada" and reserva is not None:
        # La reserva nacio `pending_payment` y sin recordatorios: no se
        # le aviso al cliente nada porque el turno no estaba confirmado.
        # El pago aprobado es el momento de la confirmacion, y aca se
        # programan la confirmacion y los recordatorios. Es idempotente
        # (no duplica filas si ya estaban programadas).
        await schedule_for_booking(session, reserva)

    await session.flush()
    return {"aplicado": True, "pago_status": str(nuevo), "reserva": transicion}


__all__ = ["aplicar_webhook_pago"]
