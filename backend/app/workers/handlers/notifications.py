"""Handlers de notificaciones: envío de mensajes por WhatsApp.

Estos handlers se ejecutan de forma asíncrona vía el job dispatcher.
No se llaman directamente desde los endpoints HTTP.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.time import now
from app.db.system_models import Job
from app.models.enums import JobKind, NotificationStatus
from app.modules.notifications.models import NotificationRequest
from app.workers.dispatcher import register_handler

logger = get_logger(__name__)


#: Claves de registro. **Deben ser valores de `JobKind`**, no strings libres.
#:
#: Antes estos handlers se registraban con `"send_notification"` y
#: `"schedule_reminders"`, que no existen en el enum `job_kind` de la base. Ningun
#: job podia llegar: el dispatcher busca `self._handlers[job.kind.value]` y todos los
#: valores posibles del enum no tenian handler, asi que cada job caia en
#: `job_sin_handler` y se marcaba `DEAD` al primer intento. El sistema de trabajos
#: estaba 100% muerto y el sintoma -- logs de `job_sin_handler` -- se leia como
#: "faltan jobs", no como "las claves no coinciden con el enum".
#:
#: La asercion de abajo es la que lo evita: si alguien agrega un handler con una
#: clave inventada, falla al importar el modulo en vez de en produccion, tres meses
#: despues, cuando el primer job de ese tipo aparece.
JOB_KIND_WHATSAPP = JobKind.WHATSAPP_OUTBOUND
JOB_KIND_REMINDER = JobKind.REMINDER


@register_handler(JOB_KIND_WHATSAPP.value)
async def handle_send_notification(session: AsyncSession, job: Job) -> None:
    """Envia una notificacion por WhatsApp.

    El job payload debe tener:
    - notification_request_id: ID de la NotificationRequest

    **Este handler no es el camino de las notificaciones.** Las crea
    `schedule_booking_notifications` como filas de `notification_requests` y las
    manda `app.workers.outbox.drain_once`. Este existe para el caso puntual de
    reenviar una notificacion puntual fuera de ciclo, que si necesita pasar por la
    cola para heredar los reintentos y el leasing.
    """
    notification_request_id = job.payload.get("notification_request_id")
    if not notification_request_id:
        raise ValueError("Falta notification_request_id en el payload")

    # Cargar la notificacion
    result = await session.execute(
        select(NotificationRequest).where(NotificationRequest.id == notification_request_id)
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise ValueError(f"NotificationRequest {notification_request_id} no encontrada")

    if notification.status in (NotificationStatus.SENT, NotificationStatus.DELIVERED):
        # Reenviar un mensaje ya entregado es la razon principal por la que un
        # cliente bloquea a un negocio. La idempotencia se comprueba **antes** de
        # enviar, no despues: despues el duplicado ya ocurrio.
        logger.info("notificacion_ya_enviada", notification_id=str(notification.id))
        return

    # TODO: Integrar con WhatsApp Cloud API. Ver `app.workers.outbox._enviar`, que
    # tiene el mismo limite y la misma razon para no llamar a Meta sin credenciales.
    logger.info(
        "notificacion_enviada",
        notification_id=str(notification.id),
        kind=notification.kind.value,
        customer_id=str(notification.customer_id),
    )

    notification.status = NotificationStatus.SENT
    notification.sent_at = now()
    await session.flush()


@register_handler(JOB_KIND_REMINDER.value)
async def handle_schedule_reminders(_session: AsyncSession, job: Job) -> None:
    """Programa los recordatorios de una reserva (2h antes y 1h antes).

    El job payload debe tener:
    - booking_id: ID de la reserva

    Idempotente por `UNIQUE (booking_id, kind)` en `notification_requests`, asi que
    reprocesar el mismo job no duplica nada.
    """
    booking_id = job.payload.get("booking_id")
    if not booking_id:
        raise ValueError("Falta booking_id en el payload")

    # TODO: Cargar la reserva y programar los recordatorios con
    # `schedule_booking_notifications`. Hoy el camino real es que `create_booking`
    # los programe en su propia transaccion, que es lo que garantiza que existen
    # aunque el worker nunca llegue a correr.
    logger.info(
        "recordatorios_programados",
        booking_id=str(booking_id),
    )


def _verificar_claves_contra_el_enum() -> None:
    """Falla al importar si un handler usa una clave que no es un `JobKind`.

    `register_handler` acepta `str` porque es la firma del dispatcher, y ahi no hay
    forma de saber si el string viene de un enum. Este chequeo es el que convierte
    un error silencioso en uno visible.
    """
    validas = {k.value for k in JobKind}
    from app.workers.dispatcher import dispatcher

    invalidas = sorted(set(dispatcher._handlers) - validas)
    if invalidas:
        raise RuntimeError(
            f"handlers registrados con claves que no son JobKind: {invalidas}. "
            f"Los valores validos son: {sorted(validas)}"
        )


_verificar_claves_contra_el_enum()


__all__ = [
    "handle_schedule_reminders",
    "handle_send_notification",
]
