"""Programacion de notificaciones de una reserva.

La outbox de `notification_requests` es la que decide que se manda y cuando.
Este modulo no envia nada: escribe las filas y deja que el worker las drene. La
separacion importa porque un envio puede fallar (Meta caido, token vencido) y un
`INSERT` fallar nunca deberia dejar el turno sin recordatorio.

Las reglas:

1. `UNIQUE (booking_id, kind)` hace que un recordatorio de 2 horas exista **una
   vez por reserva, para siempre**. La garantia no depende de que el worker
   recuerde que mando: depende de la base.
2. Cancelar o reprogramar pasa los pendientes a `cancelled` en la misma
   transaccion que el cambio de estado.
3. **Toda reserva genera los dos recordatorios**, sin importar cuantos dias de
   anticipacion tenga (PROJECT_MASTER §20: "Cada reserva debe generar
   automaticamente dos recordatorios: 2 horas antes, 1 hora antes"). No hay
   ventana maxima: un turno dentro de dos semanas se programa para dentro de dos
   horas, y se manda a las 2 horas antes como cualquier otro.
4. Si el momento del recordatorio ya paso --porque el cliente reservo con menos de
   dos horas de anticipacion-- se manda de inmediato en vez de descartarse. Perder
   el recordatorio es peor que mandarlo tarde: el turno existe y el cliente tiene
   que enterarse.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import now
from app.models.enums import NotificationKind
from app.modules.bookings.models import Booking
from app.modules.notifications.models import NotificationRequest

# Ventanas de recordatorio, en horas antes del turno.
#
# Dos valores, no tres: `REMINDER_24H` existe en el enum porque el modelo lo
# anticipo, pero el producto solo pide 2h y 1h (PROJECT_MASTER §20). No se programa
# el de 24 horas hasta que exista la plantilla aprobada en Meta: mandar una
# plantilla no aprobada es un rechazo seguro, y el cliente que recibe un
# error de plantilla cree que el negocio fallo con el turno.
REMINDER_OFFSETS: tuple[tuple[NotificationKind, int], ...] = (
    (NotificationKind.REMINDER_2H, 2),
    (NotificationKind.REMINDER_1H, 1),
)


async def schedule_booking_notifications(
    session: AsyncSession,
    booking: Booking,
    *,
    immediate: bool = True,
) -> list[NotificationRequest]:
    """Programa las notificaciones de una reserva recien creada.

    Args:
        immediate: Si es True, encola tambien la confirmacion para envio ya
            (que es lo que corresponde a una reserva recien creada).

    Returns:
        Las filas creadas. Si un `ON CONFLICT DO NOTHING` colisiona, la fila no
        se incluye en la lista: es el mecanismo que hace idempotente el
        reprocesamiento.
    """
    customer_id = await _resolve_customer_id(session, booking.id)
    if customer_id is None:
        # Sin `customer_id` la FK de `notification_requests` no se puede
        # satisfacer. No es un caso teorico: una reserva creada por panel puede
        # no tener cliente resuelto todavia, y fallar aqui abortaria el turno.
        return []

    queued: list[NotificationRequest] = []
    reference = now()

    if immediate:
        created = await _enqueue(
            session,
            booking=booking,
            customer_id=customer_id,
            kind=NotificationKind.BOOKING_CONFIRMED,
            scheduled_for=reference,
        )
        queued.extend(created)

    for kind, hours in REMINDER_OFFSETS:
        # `starts_at - offset` es el momento teorico. Si ya paso, se manda igual y
        # de una: el `kind` no cambia, asi que la unicidad sigue impidiendo que
        # un reprocesamiento lo duplique.
        target = max(
            booking.starts_at - dt.timedelta(hours=hours),
            reference,
        )
        created = await _enqueue(
            session,
            booking=booking,
            customer_id=customer_id,
            kind=kind,
            scheduled_for=target,
        )
        queued.extend(created)

    return queued


async def _enqueue(
    session: AsyncSession,
    *,
    booking: Booking,
    customer_id: uuid.UUID,
    kind: NotificationKind,
    scheduled_for: dt.datetime,
) -> list[NotificationRequest]:
    """Inserta una notificacion. `ON CONFLICT DO NOTHING` sobre (booking, kind).

    El `RETURNING` no devuelve filas cuando hay conflicto, asi que el retorno
    vacio es exactamente la senal de "ya estaba programada" y la funcion que
    llama no la trata como error.
    """
    payload: dict[str, object] = {
        "booking_id": str(booking.id),
        "starts_at": booking.starts_at.isoformat(),
        "ends_at": booking.ends_at.isoformat(),
        "service_id": str(booking.service_id),
        "professional_id": str(booking.professional_id),
    }

    stmt = (
        pg_insert(NotificationRequest)
        .values(
            business_id=booking.business_id,
            booking_id=booking.id,
            customer_id=customer_id,
            kind=kind,
            channel="whatsapp",
            scheduled_for=scheduled_for,
            status="pending",
            attempts=0,
            max_attempts=3,
            payload=payload,
        )
        .on_conflict_do_nothing(constraint="uq_notification_requests_booking_id_kind")
        .returning(NotificationRequest)
    )
    result = await session.execute(stmt)
    return list(result.scalars())


async def _resolve_customer_id(session: AsyncSession, booking_id: uuid.UUID) -> uuid.UUID | None:
    """`customer_id` de una reserva. `NULL` si la reserva no lo tiene resuelto."""
    from sqlalchemy import select

    result = await session.execute(select(Booking.customer_id).where(Booking.id == booking_id))
    return result.scalar_one_or_none()


__all__ = ["REMINDER_OFFSETS", "schedule_booking_notifications"]
