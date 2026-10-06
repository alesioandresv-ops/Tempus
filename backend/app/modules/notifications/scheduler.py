"""Programacion de las notificaciones de una reserva.

Este modulo **escribe filas y nada mas**: decide que hay que mandarle a quien y
cuando, y deja que `app.workers.outbox` las mande. La separacion no es purismo:
un envio puede fallar --Meta caido, token vencido-- y un `INSERT` fallar nunca
debe dejar un turno sin recordatorio.

Tres reglas, y las tres son decisiones, no detalles:

1. **El walk-in no genera nada.** El cliente esta parado en el mostrador: un
   recordatorio de "tu turno es a las 10" para alguien que ya llego es
   directamente falso, y el mensaje cuesta plata. Se filtra aca y no en el
   worker para que la fila ni exista-- una fila que nunca se va a mandar es
   ruido que un dia alguien tiene que explicar.

2. **El opt-out se respeta al programar, no solo al enviar.** El worker tambien
   lo revisa (`outbox._destinatario`), y esa segunda comprobacion se queda: es la
   que cubre al cliente que se da de baja *despues* de reservar. Programar sin
   mirar el opt-out dejaria tres filas muertas que nadie va a mirar nunca.

3. **Un recordatorio cuyo momento ya paso no se crea.** Si el cliente reservo
   para dentro de una hora, el aviso de "24 horas antes" no tiene a que ser
   "ahora": seria un mensaje sobre un turno que empieza en una hora, recibido en
   este momento, y el cliente lo lee como que se confundieron. La confirmacion se
   manda igual, y es lo unico que se manda.

**Idempotencia por `SELECT` y no por `ON CONFLICT`.** `notification_requests` ya
tiene `UNIQUE (booking_id, kind)`, asi que la base es la autoridad final y una
colision simultanea no puede duplicar nada. El `SELECT` de aca es lo que hace el
reprocesamiento barato y explicable --"ya estaba programado" es un `return` y no
una excepcion-- y evita el `ON CONFLICT ... DO NOTHING`, que ademas requiere
nombrar el constraint y falla si el nombre cambia.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import now
from app.models.enums import (
    BookingSource,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
)
from app.modules.bookings.models import Booking
from app.modules.customers.models import Customer
from app.modules.notifications.models import NotificationRequest

#: Ventanas de recordatorio, en horas antes del turno.
#:
#: Dos ventanas y no tres: `REMINDER_1H` existe en el enum porque el modelo lo
#: anticipo, pero no se programa. Mandar tres avisos de un mismo turno es la forma
#: rapida de que un cliente bloquee al negocio, y el ultimo no agrega informacion
#: que el anterior no haya dado.
REMINDER_OFFSETS: tuple[tuple[NotificationKind, int], ...] = (
    (NotificationKind.REMINDER_24H, 24),
    (NotificationKind.REMINDER_2H, 2),
)

#: Intentos por defecto de cada fila. Tres es el numero que aguanta un outage de
#: Meta sin que el turno quede sin avisar, y el mas bajo que sobrevive a un
#: reintento con el token a punto de vencer.
DEFAULT_MAX_ATTEMPTS = 3


async def _esta_dado_de_baja(session: AsyncSession, customer_id: uuid.UUID) -> bool:
    """Si el cliente no acepta mensajes.

    `bool(...)` y no `is True` por un motivo puntual: `is_opted_out` es nullable, y
    un `NULL`-- cliente sin el dato seteado-- tiene que contar como *no* dado de
    baja. Con `bool` el `NULL` cae en `False` solo, que es la unica lectura
    sensata; con `NOT is_opted_out` en SQL el `NULL` daria `NULL`, que no es
    verdadero ni falso y obliga a escribir el `IS NOT TRUE` a mano.
    """
    resultado = await session.execute(
        select(Customer.is_opted_out).where(Customer.id == customer_id)
    )
    return bool(resultado.scalar_one_or_none())


async def _programar(
    session: AsyncSession,
    *,
    booking: Booking,
    kind: NotificationKind,
    scheduled_for: dt.datetime,
) -> NotificationRequest | None:
    """Crea la fila de un `kind`, o `None` si ya estaba programada.

    El `None` es la senal de "ya existia" y no un error: la funcion que llama la
    saltea y sigue. Es lo que hace que reprocesar una reserva--por ejemplo tras
    un fallo de red en el cliente-- no produzca un segundo juego de recordatorios.
    """
    ya_existe = await session.execute(
        select(NotificationRequest.id).where(
            NotificationRequest.booking_id == booking.id,
            NotificationRequest.kind == kind,
        )
    )
    if ya_existe.scalar_one_or_none() is not None:
        return None

    fila = NotificationRequest(
        business_id=booking.business_id,
        booking_id=booking.id,
        customer_id=booking.customer_id,
        kind=kind,
        channel=NotificationChannel.WHATSAPP,
        scheduled_for=scheduled_for,
        status=NotificationStatus.PENDING,
        attempts=0,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        payload={
            "booking_id": str(booking.id),
            "starts_at": booking.starts_at.isoformat(),
            "ends_at": booking.ends_at.isoformat(),
            "service_id": str(booking.service_id),
            "professional_id": str(booking.professional_id),
        },
    )
    session.add(fila)
    return fila


async def schedule_for_booking(
    session: AsyncSession, booking: Booking
) -> list[NotificationRequest]:
    """Programa la confirmacion y los recordatorios de una reserva.

    Deja hasta tres filas en `notification_requests`:

    - `booking_confirmed`, para envio inmediato.
    - `reminder_24h`, en `starts_at - 24h`.
    - `reminder_2h`, en `starts_at - 2h`.

    Los dos recordatorios solo se crean si su momento **todavia esta en el
    futuro**. Un turno para dentro de una hora no tiene aviso de 24 horas: el
    momento del aviso ya paso, y mandarlo de todas formas produce un mensaje que
    el cliente lee como un error del negocio.

    No encola nada para el walk-in ni para un cliente dado de baja; en los dos
    casos devuelve lista vacia.

    Es idempotente: volver a llamarla sobre la misma reserva no agrega filas.
    Devuelve **solo las que creo en esta llamada**, que es lo que un test puede
    afirmar sin tener que contar lo que habia antes.

    Args:
        session: Sesion del mismo tenant que la reserva. `notification_requests` y
            `customers` tienen RLS con `FORCE`, asi que sin GUC no ve ni escribe
            nada-- y no da error: devuelve cero filas y parece una reserva sin
            recordatorios.
        booking: La reserva ya persistida. Necesita `booking.id`, asi que el
            llamador tiene que haber hecho flush antes.

    Returns:
        Las filas creadas en esta llamada, en orden.
    """
    # El walk-in se filtra por `source` y no por un flag del llamador. El flag
    # (`notificar=False`) lo pone `registrar_walkin`, y un filtro que depende de
    # que el llamador se acuerde es un filtro que un dia se olvida.
    if booking.source == BookingSource.WALKIN:
        return []

    if await _esta_dado_de_baja(session, booking.customer_id):
        return []

    referencia = now()
    creadas: list[NotificationRequest] = []

    # La confirmacion va siempre y para ya: es lo que el cliente esta mirando en
    # la pantalla de "reservaste".
    confirmacion = await _programar(
        session,
        booking=booking,
        kind=NotificationKind.BOOKING_CONFIRMED,
        scheduled_for=referencia,
    )
    if confirmacion is not None:
        creadas.append(confirmacion)

    for kind, horas in REMINDER_OFFSETS:
        momento = booking.starts_at - dt.timedelta(hours=horas)
        # Se queda solo lo que esta **estrictamente** en el futuro. El `>=` mas
        # generoso se descarta porque el worker toma lo que tenga
        # `scheduled_for <= now()`: un aviso que toca justo este instante ya esta
        # vencido y no seria "de 24 horas antes", seria "de 23 horas y 59".
        if momento <= referencia:
            continue
        fila = await _programar(session, booking=booking, kind=kind, scheduled_for=momento)
        if fila is not None:
            creadas.append(fila)

    # Flush explicito porque la sesion va con `autoflush=False`: sin esto las filas
    # quedan pendientes en el sessionmaker. `create_booking` no vuelve a flushear
    # despues de programar, asi que sin esta linea los avisos existen en memoria
    # pero no en la base-- y el turno queda "programado" hasta el commit de otro.
    if creadas:
        await session.flush()

    return creadas


__all__ = ["DEFAULT_MAX_ATTEMPTS", "REMINDER_OFFSETS", "schedule_for_booking"]
