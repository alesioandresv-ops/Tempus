"""Tests de integracion del programador de notificaciones de una reserva (Fase 9).

`app.modules.notifications.scheduler.schedule_for_booking` decide **que** se le
manda a quien y **cuando**, y nada mas: escribe filas en `notification_requests` y
devuelve. Quien las manda es `app.workers.outbox`, que esta aparte y no se toca
aca.

Lo que estos tests fijan, y por que importa cada uno:

1. Un turno lejano programa los tres avisos con el instante exacto de cada uno. Un
   `scheduled_for` corrido no es un detalle menor: el worker manda lo que tiene
   `scheduled_for <= now()`, asi que "casi la hora correcta" es "la hora
   equivocada", y un recordatorio de 24 horas que sale 25 horas despues es peor
   que uno que no sale.
2. Un turno cercano programa **solo** la confirmacion. Es la regla que evita
   mandar "tu turno es manana" a alguien que reservo para dentro de una hora.
3. El walk-in no programa nada, porque el cliente esta en el mostrador.
4. El cliente dado de baja no programa nada, y por lo tanto no queda ni una fila
   que alguien tenga que filtrar despues.
5. Programar dos veces no duplica: es lo que permite reprocesar una reserva sin
   duplicar mensajes, que es la razon principal por la que un cliente bloquea a
   un negocio.
6. Cancelar mata los pendientes y el worker deja de verlos. La segunda mitad es la
   que importa: "quedaron en `cancelled`" no dice nada mientras el `WHERE` del
   worker los siga admitiendo.

**Todos usan el servicio de dominio, no el endpoint HTTP.** Lo que se prueba es el
contrato del programador, y hacerlo por HTTP agrega la variable del
`min_lead_minutes` y del horario del negocio: para construir "un turno dentro de
una hora" habria que esperar a que el reloj caiga en un hueco concreto, y un test
que depende de la hora del dia es un test que se pudre. El wiring de HTTP se
reduce a `if notificar and source is not WALKIN`, y esa linea se lee.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import NamedTuple
from zoneinfo import ZoneInfo

import pytest
from app.core.time import now
from app.models.enums import (
    BookingSource,
    BookingStatus,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
)
from app.modules.bookings.admin import cancelar_desde_panel
from app.modules.bookings.models import Booking
from app.modules.bookings.service import BookingResult, cancel_booking, create_booking

# Se importa por su efecto secundario: sin `Business` en el metadata, la FK
# `Service.business_id` no tiene contra que resolverse y el mapper falla al armar
# la sesion. Quitarlo parece una limpieza y rompe los tests.
from app.modules.businesses.models import Business  # noqa: F401
from app.modules.customers.models import Customer
from app.modules.notifications.models import NotificationRequest
from app.modules.notifications.scheduler import schedule_for_booking
from app.modules.professionals.models import Professional
from app.modules.services.models import ProfessionalService, Service

# `_reclamar` es privado a proposito: es la sentencia que decide que se manda, y
# por eso mismo es la que hay que ejercitar. Replicar su `WHERE` a mano en el test
# verificaria la copia, no el codigo que corre en produccion.
from app.workers.outbox import _reclamar
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

#: Zona horaria del negocio `test-business`. Tiene que coincidir con la que siembra
#: `tests/conftest.py`, porque `local_date` se deriva de ella.
ZONA = ZoneInfo("America/Argentina/Buenos_Aires")

#: Margen del turno lejano. Tiene que ser mayor que 24 horas o el recordatorio de
#: 24 horas caeria en el pasado y el test probaria el caso equivocado.
DIAS_ADELANTE = 2

#: Margen del turno cercano. Menor que 2 horas, asi que los dos recordatorios ya
#: pasaron y solo queda la confirmacion.
HORAS_CERCANO = 1

#: Telefonos distintos por test. El cliente se identifica por telefono y se reusa,
#: asi que compartir uno haria que el opt-out de un test contaminara al siguiente.
PREFIJO_TELEFONO = "+54911000000"


def _telefono(indice: int) -> str:
    """Telefono E.164 valido y unico por test."""
    return f"{PREFIJO_TELEFONO}{indice:02d}"


class _Reserva(NamedTuple):
    """Lo que devuelve `_reservar`: la fila y el token en claro.

    El secure token se guarda **hasheado** en la base y no se puede invertir, asi
    que la unica forma de tener el valor en claro es tomarlo del resultado de
    `create_booking` en el momento. Por eso viaja junto a la fila.
    """

    booking: Booking
    secure_token: str


async def _crear_servicio_y_profesional(
    session: AsyncSession, business_id: uuid.UUID
) -> tuple[Service, Professional]:
    """Servicio y profesional sobre el negocio dado.

    **No crea el negocio.** `businesses` es la tabla global y desde
    `0012_businesses_insert_policy` insertar en ella es una operacion de onboarding
    que corre con el rol de DDL: `tempus_app` no tiene INSERT a proposito. El
    negocio lo siembra `tests/conftest.py` con el rol que si puede, y se llega por
    el fixture `business_c`.

    Todo lo demas es tabla de tenant y se escribe desde la sesion de la app con el
    GUC puesto, que es exactamente como funciona en produccion.
    """
    service = Service(
        business_id=business_id,
        name="Corte",
        duration_minutes=60,
        price=Decimal("1000.00"),
        currency="ARS",
    )
    session.add(service)
    await session.flush()

    professional = Professional(
        business_id=business_id,
        display_name="Gabriel",
        is_active=True,
    )
    session.add(professional)
    await session.flush()

    session.add(
        ProfessionalService(
            professional_id=professional.id,
            service_id=service.id,
            business_id=business_id,
            is_active=True,
        )
    )
    await session.flush()

    return service, professional


async def _reservar(
    session: AsyncSession,
    business_id: uuid.UUID,
    service: Service,
    professional: Professional,
    *,
    empieza_en: dt.datetime,
    telefono: str,
    source: BookingSource = BookingSource.PUBLIC,
    notificar: bool = True,
) -> _Reserva:
    """Crea una reserva con el servicio y el profesional dados.

    `agenda=None` deja el motor de disponibilidad afuera a proposito: lo que se
    quiere controlar aca es el reloj del programador-- "faltan 48 horas", "falta una
    hora" -- y el motor, con su `min_lead_minutes`, rechazaria justamente el turno
    cercano que hay que probar.
    """
    resultado: BookingResult = await create_booking(
        session,
        business_id=business_id,
        service_id=service.id,
        professional_id=professional.id,
        customer_first_name="Lucia",
        customer_last_name="Ferreyra",
        customer_phone_e164=telefono,
        starts_at=empieza_en,
        ends_at=empieza_en + dt.timedelta(hours=1),
        duration_minutes=60,
        price=Decimal("1000.00"),
        currency="ARS",
        local_date=empieza_en.astimezone(ZONA).date(),
        agenda=None,
        source=source,
        notificar=notificar,
    )
    booking = await session.get(Booking, resultado.booking_id)
    assert booking is not None
    return _Reserva(booking=booking, secure_token=resultado.secure_token)


async def _filas(session: AsyncSession, booking_id: uuid.UUID) -> list[NotificationRequest]:
    return list(
        (
            await session.execute(
                select(NotificationRequest)
                .where(NotificationRequest.booking_id == booking_id)
                .order_by(NotificationRequest.kind)
            )
        )
        .scalars()
        .all()
    )


async def _vencer_confirmacion(session: AsyncSession, booking_id: uuid.UUID) -> None:
    """Adelanta a un instante pasado el `scheduled_for` de **solo** la confirmacion.

    **Hace falta, y no es cosmetico.** `outbox._reclamar` compara contra el `now()`
    **de PostgreSQL**, que es el *inicio de la transaccion*. La del test arranca
    antes de que el programador escriba su `now()` de Python, asi que una
    confirmacion recien creada todavia no esta vencida: el `WHERE` del worker no
    matchea nada y el test pasa por el motivo equivocado, igual que pasaria si el
    filtro estuviera roto.

    Solo toca la confirmacion a proposito. Los recordatorios de 24 y 2 horas tienen
    que seguir en el futuro: si tambien se los venciera, el `WHERE` traeria tres
    filas de la reserva viva y el test dejaria de medir lo unico que importa, que
    es si las de la reserva cancelada aparecen o no.

    En produccion no hay que fabricar nada: cada tick del worker corre en su propia
    transaccion, su `now()` ya quedo atras y lo vencido esta vencido. Aca hay que
    mover el reloj, y moverlo a proposito es mejor que esperar.
    """
    await session.execute(
        text(
            "UPDATE notification_requests "
            "SET scheduled_for = now() - interval '1 hour' "
            "WHERE booking_id = :booking_id AND kind = 'booking_confirmed'"
        ),
        {"booking_id": booking_id},
    )


async def test_turno_lejano_programa_confirmacion_y_dos_recordatorios(
    session: AsyncSession, business_c: uuid.UUID, set_tenant
) -> None:
    """48 horas de anticipacion: los tres avisos, cada uno en su instante."""
    await set_tenant(business_c)
    service, professional = await _crear_servicio_y_profesional(session, business_c)

    empieza_en = now() + dt.timedelta(days=DIAS_ADELANTE)
    reserva = await _reservar(
        session,
        business_c,
        service,
        professional,
        empieza_en=empieza_en,
        telefono=_telefono(1),
    )

    filas = await _filas(session, reserva.booking.id)
    por_tipo = {f.kind: f for f in filas}

    assert set(por_tipo) == {
        NotificationKind.BOOKING_CONFIRMED,
        NotificationKind.REMINDER_24H,
        NotificationKind.REMINDER_2H,
    }

    assert por_tipo[NotificationKind.REMINDER_24H].scheduled_for == empieza_en - dt.timedelta(
        hours=24
    )
    assert por_tipo[NotificationKind.REMINDER_2H].scheduled_for == empieza_en - dt.timedelta(
        hours=2
    )

    # La confirmacion no espera: tiene que estar vencida para que el worker la tome
    # en el proximo tick. Se afirma "ya paso" y no "es igual a ahora" porque el
    # instante exacto lo eligio el programador, no el test.
    assert por_tipo[NotificationKind.BOOKING_CONFIRMED].scheduled_for <= now()

    # Todo pendiente, sin reintentos gastados, y por WhatsApp.
    assert all(f.status == NotificationStatus.PENDING for f in filas)
    assert all(f.attempts == 0 and f.max_attempts == 3 for f in filas)
    assert all(f.channel == NotificationChannel.WHATSAPP for f in filas)


async def test_turno_cercano_solo_programa_la_confirmacion(
    session: AsyncSession, business_c: uuid.UUID, set_tenant
) -> None:
    """Dentro de una hora: los dos recordatorios ya pasaron y no se crean.

    Es la regla que evita el mensaje absurdo-- "mañana a las 10" para un turno que
    es en una hora -- y por eso el instante del aviso decide si la fila existe.
    """
    await set_tenant(business_c)
    service, professional = await _crear_servicio_y_profesional(session, business_c)

    reserva = await _reservar(
        session,
        business_c,
        service,
        professional,
        empieza_en=now() + dt.timedelta(hours=HORAS_CERCANO),
        telefono=_telefono(2),
    )

    tipos = {f.kind for f in await _filas(session, reserva.booking.id)}

    assert tipos == {NotificationKind.BOOKING_CONFIRMED}


async def test_walkin_no_programa_nada(
    session: AsyncSession, business_c: uuid.UUID, set_tenant
) -> None:
    """Walk-in: cero filas, aunque el llamador pida notificar.

    Se pasa `notificar=True` a proposito. `registrar_walkin` manda
    `notificar=False`, asi que probar el walk-in por esa via solo mediria que el
    flag llego; y el flag es exactamente lo que un dia se olvida.

    Por eso, ademas de mirar que no quedo ninguna fila, se llama al programador
    **directamente** sobre la reserva del walk-in. Sin esa llamada el test pasaria
    aunque el filtro por `source` no existiera, porque el filtro del llamador ya
    impidio la llamada: estaria verde midiendo el guard equivocado.
    """
    await set_tenant(business_c)
    service, professional = await _crear_servicio_y_profesional(session, business_c)

    reserva = await _reservar(
        session,
        business_c,
        service,
        professional,
        empieza_en=now() + dt.timedelta(days=DIAS_ADELANTE),
        telefono=_telefono(3),
        source=BookingSource.WALKIN,
        notificar=True,
    )

    # El llamador no programo nada.
    assert await _filas(session, reserva.booking.id) == []

    # Y el programador se niega igual si se lo piden.
    assert await schedule_for_booking(session, reserva.booking) == []
    assert await _filas(session, reserva.booking.id) == []


async def test_cliente_dado_de_baja_no_programa_nada(
    session: AsyncSession, business_c: uuid.UUID, set_tenant
) -> None:
    """Opt-out: no hay ni una fila que alguien tenga que filtrar despues."""
    await set_tenant(business_c)
    service, professional = await _crear_servicio_y_profesional(session, business_c)

    session.add(
        Customer(
            business_id=business_c,
            first_name="Lucia",
            last_name="Ferreyra",
            phone_e164=_telefono(4),
            is_opted_out=True,
        )
    )
    await session.flush()

    reserva = await _reservar(
        session,
        business_c,
        service,
        professional,
        empieza_en=now() + dt.timedelta(days=DIAS_ADELANTE),
        telefono=_telefono(4),
    )

    assert await _filas(session, reserva.booking.id) == []


async def test_programar_dos_veces_no_duplica(
    session: AsyncSession, business_c: uuid.UUID, set_tenant
) -> None:
    """Reprocesar la misma reserva no agrega filas.

    Es la propiedad que hace barato un reintento: el cliente que reintenta la
    reserva-- por una caida de red, por un doble toque -- no recibe un segundo
    juego de avisos.
    """
    await set_tenant(business_c)
    service, professional = await _crear_servicio_y_profesional(session, business_c)

    reserva = await _reservar(
        session,
        business_c,
        service,
        professional,
        empieza_en=now() + dt.timedelta(days=DIAS_ADELANTE),
        telefono=_telefono(5),
    )

    # `create_booking` ya programo: una segunda pasada no debe cambiar el total, y
    # debe devolver vacio porque no creo nada.
    repetidas = await schedule_for_booking(session, reserva.booking)

    assert repetidas == []

    filas = await _filas(session, reserva.booking.id)
    assert len(filas) == 3
    assert {f.kind for f in filas} == {
        NotificationKind.BOOKING_CONFIRMED,
        NotificationKind.REMINDER_24H,
        NotificationKind.REMINDER_2H,
    }


async def test_cancelar_mata_los_pendientes_y_el_worker_no_los_toma(
    session: AsyncSession, business_c: uuid.UUID, set_tenant
) -> None:
    """Cancelar deja los avisos en `cancelled` y el worker deja de verlos.

    Se cubren los dos caminos de cancelacion-- el del cliente con secure token y el
    del panel -- porque son entradas distintas al mismo estado, y lo que importa es
    que ninguna deje un aviso vivo.

    La segunda mitad usa el `_reclamar` **real** del worker. Un test que replica su
    `WHERE` a mano verifica la copia, no el codigo que corre en produccion. Y se
    compara contra una reserva viva al lado, para que un `[]` producido por una
    consulta rota no se lea como "el cancelado funciono".
    """
    await set_tenant(business_c)
    service, professional = await _crear_servicio_y_profesional(session, business_c)

    lejos = now() + dt.timedelta(days=DIAS_ADELANTE)

    # Camino del cliente: secure token.
    por_token = await _reservar(
        session, business_c, service, professional, empieza_en=lejos, telefono=_telefono(6)
    )
    # Camino del panel: principal, sin token.
    por_panel = await _reservar(
        session,
        business_c,
        service,
        professional,
        empieza_en=lejos + dt.timedelta(hours=3),
        telefono=_telefono(7),
    )
    # Control: queda viva. Es la que el worker tiene que seguir viendo.
    control = await _reservar(
        session,
        business_c,
        service,
        professional,
        empieza_en=lejos + dt.timedelta(hours=6),
        telefono=_telefono(8),
    )

    cancelada = await cancel_booking(
        session,
        booking_id=por_token.booking.id,
        secure_token=por_token.secure_token,
        cancellation_window_minutes=120,
    )
    assert cancelada.status == BookingStatus.CANCELLED

    cancelada_panel = await cancelar_desde_panel(
        session,
        por_panel.booking.id,
        business_id=business_c,
        cancellation_window_minutes=120,
        motivo="prueba",
    )
    assert cancelada_panel.status == BookingStatus.CANCELLED

    await session.flush()

    # Los dos caminos dejan lo mismo: nada vivo.
    for reserva in (por_token, por_panel):
        estados = {f.status for f in await _filas(session, reserva.booking.id)}
        assert estados == {NotificationStatus.CANCELLED}

    # Vencemos la confirmacion de las tres reservas para que el worker tenga trabajo
    # real que hacer. Sin esto el `WHERE` no matchea nada y la asercion final seria
    # tautologica: probaria que el worker no reclama, no que no reclama lo cancelado.
    for reserva in (por_token, por_panel, control):
        await _vencer_confirmacion(session, reserva.booking.id)
    await session.flush()

    # Las dos canceladas tienen su confirmacion vencida igual que la del control, asi
    # que lo unico que las distingue es el `status`. Por eso la del control tiene que
    # aparecer: un `[]` vacio seria indistinguible de un worker roto.
    control_filas = {f.kind: f for f in await _filas(session, control.booking.id)}
    confirmacion_control = control_filas[NotificationKind.BOOKING_CONFIRMED]

    reclamadas = await _reclamar(session, business_c, 50)

    # `_reclamar` usa `text()` crudo, que no le dice a psycopg que la columna es
    # `uuid`: los ids vuelven como texto y hay que normalizarlos antes de comparar.
    assert {uuid.UUID(str(n.id)) for n in reclamadas} == {confirmacion_control.id}
