"""Tests de integración para bookings con PostgreSQL real.

Estos tests necesitan TEST_DATABASE_URL apuntando a un PostgreSQL real.
Verifican:
1. Crear una reserva
2. Verificar que el horario está ocupado
3. Cancelar la reserva
4. Verificar que el horario está libre
5. Reprogramar la reserva
6. Prevención de doble reserva
7. Idempotencia
8. Reuso de cliente por teléfono

**Vivian en dos archivos, y eso era un problema.** `test_bookings_flow.py` y este
repetian la misma `_create_test_data` y los mismos casos con pequenas variaciones.
La consecuencia no era solo el doble de mantenimiento: el que quedo sin revisar--el
`flow`-- seguia con `pytest.raises(Exception)` y con fechas fijas ya vencidas, y
cualquiera que lo abriera creeria que el caso estaba cubierto.

Aqui esta todo. Los casos unicos del `flow`--el reuso de cliente por telefono-- se
mudaron, y el archivo se borro.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from app.models.enums import BookingStatus
from app.modules.bookings.admin import obtener_reserva
from app.modules.bookings.models import Booking, IdempotencyKey
from app.modules.bookings.service import (
    IdempotencyConflictError,
    SlotNoDisponibleError,
    cancel_booking,
    create_booking,
    reschedule_booking,
)

# Se importa por su efecto secundario, no para usarlo. Sin el modelo Business
# cargado en el metadata de SQLAlchemy, la FK de Service.business_id no tiene
# contra que resolverse y el mapper falla con NoReferencedTableError al armar la
# sesion. Quitarlo parece una limpieza y rompe los tests.
from app.modules.businesses.models import Business  # noqa: F401
from app.modules.customers.models import Customer
from app.modules.professionals.models import Professional
from app.modules.services.models import ProfessionalService, Service
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

#: Zona horaria del negocio `test-business`. Tiene que coincidir con la que se
#: siembra en `tests/conftest.py`, porque `local_date` se deriva de ella.
ZONA = ZoneInfo("America/Argentina/Buenos_Aires")

#: Un turno a las 10:00 UTC dentro de una semana, derivado del reloj.
#:
#: **La fecha no es fija, y esa es la parte importante.** Con `2026-01-15`
#: hardcodeada, estos tests funcionaron hasta que esa fecha paso. Lo que vino
#: despues no fue "el test caduco": fueron `La reserva ya comenzo` y `El nuevo
#: horario ya paso`--mensajes de negocio, de logica de reserva-- y `Ese horario
#: acaba de ser tomado` en el test de idempotencia. Todo eso es la fecha, pero
#: ninguno de esos mensajes dice que es la fecha. Un test que se pudre asi no
#: avisa: ensucia el diagnostico de todo lo demas y hace dudar de la logica que si
#: funciona.
#:
#: Una semana de margen evita que el reloj caiga justo sobre el turno, y la hora
#: es fija para que dos tests que comparten la fecha no se pisen entre si.
DIAS_ADELANTE = 7
HORA_INICIO = (dt.datetime.now(dt.UTC) + dt.timedelta(days=DIAS_ADELANTE)).replace(
    hour=10, minute=0, second=0, microsecond=0
)

#: `local_date` no puede ser la fecha UTC: el negocio es de Buenos Aires, y a las
#: 10:00 UTC todavia es el mismo dia local, pero con otro offset la fecha se corre
#: y la reserva queda con la fecha local equivocada. Se deriva de la zona.
FECHA_LOCAL = HORA_INICIO.astimezone(ZONA).date()


async def _create_test_data(
    session: AsyncSession, business_id: uuid.UUID
) -> tuple[Service, Professional]:
    """Crea el servicio y el profesional sobre el negocio dado.

    **No crea el negocio.** `businesses` es la tabla global y desde
    `0012_businesses_insert_policy` insertar en ella es una operacion de onboarding
    que corre con el rol de DDL: `tempus_app` no tiene permiso de INSERT a proposito,
    porque crear un tenant es la unica operacion del producto que necesita mas
    privilegios que operar dentro de uno. El negocio lo siembra `tests/conftest.py`
    con el rol que si puede, y se llega por el fixture `business_c`.

    Todo lo demas--servicio, profesional y su vinculo-- es tabla de tenant y se
    escribe desde la sesion de la app con el GUC puesto, que es exactamente como
    funciona en produccion.
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

    prof_service = ProfessionalService(
        professional_id=professional.id,
        service_id=service.id,
        business_id=business_id,
        is_active=True,
    )
    session.add(prof_service)
    await session.flush()

    return service, professional


@pytest.mark.asyncio
async def test_create_booking(session: AsyncSession, business_c: uuid.UUID, set_tenant) -> None:
    """Test: crear una reserva."""
    await set_tenant(business_c)
    service, professional = await _create_test_data(session, business_c)

    starts_at = HORA_INICIO
    ends_at = HORA_INICIO + dt.timedelta(hours=1)

    result = await create_booking(
        session,
        business_id=business_c,
        service_id=service.id,
        professional_id=professional.id,
        customer_first_name="Juan",
        customer_last_name="Pérez",
        customer_phone_e164="+5491112345678",
        starts_at=starts_at,
        ends_at=ends_at,
        duration_minutes=60,
        price=Decimal("1000.00"),
        currency="ARS",
        local_date=FECHA_LOCAL,
    )

    assert result.booking_id is not None
    assert result.secure_token is not None
    assert result.status == BookingStatus.CONFIRMED

    # Verificar en la base
    booking_result = await session.execute(select(Booking).where(Booking.id == result.booking_id))
    booking = booking_result.scalar_one()
    # La fila viene de la base, asi que el estado es el miembro del enum leido del
    # `booking_status` de PostgreSQL.
    assert booking.status == BookingStatus.CONFIRMED
    assert booking.occupies_calendar

    # `ends_at` es un dato derivado: se recalcula desde `starts_at` mas la duracion,
    # y lo que mando el cliente se descarta. Aqui coincide, asi que el test verifica
    # el caso de acuerdo, no el de desacuerdo.
    assert booking.ends_at == starts_at + dt.timedelta(minutes=60)


@pytest.mark.asyncio
async def test_double_booking_prevention(
    session: AsyncSession, business_c: uuid.UUID, set_tenant
) -> None:
    """El `EXCLUDE` de la base es la autoridad: el segundo turno se cae, no el codigo.

    El test pide `SlotNoDisponibleError` y no `Exception`. Con `Exception` el test
    pasaba tambien si el segundo `create_booking` fallaba por cualquier otra cosa
    --una FK mal, un import roto, un `None`--, que es la forma mas comun de que un
    test de este tipo no compruebe nada: sigue verde mientras lo que protege deja de
    funcionar.

    La excepcion que se espera es la que traduce el `ExclusionViolationError` de
    PostgreSQL, asi que ademas de comprobar que no hay doble reserva, este test
    comprueba que la autoridad es la base y no una validacion previa.
    """
    await set_tenant(business_c)
    service, professional = await _create_test_data(session, business_c)

    starts_at = HORA_INICIO
    ends_at = HORA_INICIO + dt.timedelta(hours=1)

    # Primera reserva
    await create_booking(
        session,
        business_id=business_c,
        service_id=service.id,
        professional_id=professional.id,
        customer_first_name="Juan",
        customer_last_name="Pérez",
        customer_phone_e164="+5491112345678",
        starts_at=starts_at,
        ends_at=ends_at,
        duration_minutes=60,
        price=Decimal("1000.00"),
        currency="ARS",
        local_date=FECHA_LOCAL,
    )

    # Una sola fila para ese profesional. Se cuenta **antes** del intento que se
    # supone rechazado: el `EXCLUDE` dispara durante el `flush`, y un flush fallido
    # deja la sesion en `PendingRollbackError`--toda consulta posterior falla con un
    # error que no dice nada del turno. En produccion no importa: la excepcion sube
    # hasta el router y `session_scope` hace el rollback.
    total = (
        await session.execute(
            select(func.count())
            .select_from(Booking)
            .where(Booking.professional_id == professional.id)
        )
    ).scalar_one()
    assert total == 1

    # Segunda reserva en el mismo horario debe fallar
    with pytest.raises(SlotNoDisponibleError, match="acaba de ser tomado") as excinfo:
        await create_booking(
            session,
            business_id=business_c,
            service_id=service.id,
            professional_id=professional.id,
            customer_first_name="María",
            customer_last_name="Gómez",
            customer_phone_e164="+5491187654321",
            starts_at=starts_at,
            ends_at=ends_at,
            duration_minutes=60,
            price=Decimal("1000.00"),
            currency="ARS",
            local_date=FECHA_LOCAL,
        )

    # El error viene del `EXCLUDE` de PostgreSQL, no de una comparacion en Python:
    # la excepcion encadena la causa original.
    causa = excinfo.value.__cause__
    assert causa is not None, "la excepcion no encadena la causa de PostgreSQL"
    assert "no_overlap" in str(causa)


@pytest.mark.asyncio
async def test_cancel_and_reschedule(
    session: AsyncSession, business_c: uuid.UUID, set_tenant
) -> None:
    """Cancelar libera el horario, y reprogramar mueve el turno.

    **Son dos reservas distintas, no dos operaciones sobre la misma.** La version
    anterior cancelaba y despues reprogramaba la misma fila, lo que el servicio
    rechaza con `No se puede reprogramar una reserva cancelada`. El test fallaba
    con un error de negocio correcto y, peor, el mensaje daba a entender que
    reprogramar estaba roto cuando lo que estaba mal era el test.

    La razon por la que dos reservas es ademas lo que hay que probar: la segunda
    tiene que poder ocupar el horario que la primera libero. Si se usara una sola
    fila, ese caso--reutilizar un horario cancelado-- no quedaria cubierto.
    """
    await set_tenant(business_c)
    service, professional = await _create_test_data(session, business_c)

    starts_at = HORA_INICIO
    ends_at = HORA_INICIO + dt.timedelta(hours=1)

    # Primera reserva, la que se cancela.
    primera = await create_booking(
        session,
        business_id=business_c,
        service_id=service.id,
        professional_id=professional.id,
        customer_first_name="Juan",
        customer_last_name="Pérez",
        customer_phone_e164="+5491112345678",
        starts_at=starts_at,
        ends_at=ends_at,
        duration_minutes=60,
        price=Decimal("1000.00"),
        currency="ARS",
        local_date=FECHA_LOCAL,
    )

    # La fila recien creada todavia no hizo un viaje a la base, asi que su `status`
    # es el miembro del enum tal como lo asigno el servicio, no el texto.
    assert primera.status == BookingStatus.CONFIRMED
    assert primera.booking is not None and primera.booking.occupies_calendar

    # Cancelar
    cancelled = await cancel_booking(
        session,
        booking_id=primera.booking_id,
        secure_token=primera.secure_token,
    )
    assert cancelled.status == BookingStatus.CANCELLED
    assert cancelled.cancelled_at is not None

    # La fila cancelada ya no ocupa la agenda.
    fila = await session.get(Booking, primera.booking_id)
    assert fila is not None
    assert not fila.occupies_calendar

    # El horario quedo libre: otra reserva puede tomarlo.
    segunda = await create_booking(
        session,
        business_id=business_c,
        service_id=service.id,
        professional_id=professional.id,
        customer_first_name="María",
        customer_last_name="Gómez",
        customer_phone_e164="+5491187654321",
        starts_at=starts_at,
        ends_at=ends_at,
        duration_minutes=60,
        price=Decimal("1000.00"),
        currency="ARS",
        local_date=FECHA_LOCAL,
    )
    assert segunda.booking_id != primera.booking_id

    # Y reprogramar la segunda la mueve, dejando el horario viejo libre otra vez.
    new_starts_at = HORA_INICIO + dt.timedelta(hours=4)
    new_ends_at = HORA_INICIO + dt.timedelta(hours=5)

    rescheduled = await reschedule_booking(
        session,
        booking_id=segunda.booking_id,
        secure_token=segunda.secure_token,
        new_starts_at=new_starts_at,
        new_ends_at=new_ends_at,
        new_duration_minutes=60,
    )
    assert rescheduled.starts_at == new_starts_at
    assert rescheduled.ends_at == new_ends_at
    assert rescheduled.status == BookingStatus.CONFIRMED
    assert rescheduled.occupies_calendar

    # Se reprogramo **la misma fila**, no una nueva: el token de gestion sigue
    # apuntando a un turno real en vez de a un fantasma.
    assert rescheduled.id == segunda.booking_id
    assert rescheduled.secure_token_hash is not None

    # Y el horario viejo quedo libre de verdad.
    total = (
        await session.execute(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.professional_id == professional.id,
                Booking.status.in_(("confirmed", "pending_hold")),
            )
        )
    ).scalar_one()
    assert total == 1


@pytest.mark.asyncio
async def test_idempotencia_replay(
    session: AsyncSession, business_c: uuid.UUID, set_tenant
) -> None:
    """La misma clave con el mismo cuerpo devuelve la misma reserva.

    Esto es lo que un cliente ve cuando reintenta un `POST` que ya llego: la misma
    reserva y **el mismo `secure_token`**, no una segunda reserva y no un token
    vacio.

    El `flush` entre las dos llamadas es lo que hace que el test signifique algo.
    El fixture `session` va con `autoflush=False`, y sin ese flush la fila de
    idempotencia de la primera llamada sigue pendiente en la sesion: el `SELECT` del
    reintento no la ve, el replay no ocurre, y la segunda peticion choca contra el
    `EXCLUDE` de la primera con `Ese horario acaba de ser tomado`. Ese es el error
    que daba este test antes, y no hacia falta tocar el servicio para arreglarlo:
    dos peticiones HTTP son dos transacciones--la primera commitea--y aqui se
    simulan las dos en una sola transaccion, asi que el `flush` es el `commit`.
    """
    await set_tenant(business_c)
    service, professional = await _create_test_data(session, business_c)

    starts_at = HORA_INICIO
    ends_at = HORA_INICIO + dt.timedelta(hours=1)
    clave = f"replay-{uuid.uuid4()}"

    comun = {
        "business_id": business_c,
        "service_id": service.id,
        "professional_id": professional.id,
        "customer_first_name": "Juan",
        "customer_last_name": "Pérez",
        "customer_phone_e164": "+5491112345678",
        "starts_at": starts_at,
        "ends_at": ends_at,
        "duration_minutes": 60,
        "price": Decimal("1000.00"),
        "currency": "ARS",
        "local_date": FECHA_LOCAL,
        "idempotency_key": clave,
    }

    primera = await create_booking(session, **comun)
    await session.flush()

    segunda = await create_booking(session, **comun)

    assert segunda.booking_id == primera.booking_id
    assert segunda.secure_token == primera.secure_token
    assert not segunda.replay_sin_token
    assert segunda.starts_at == primera.starts_at

    # Y sigue habiendo una sola reserva: el replay no escribio nada nuevo.
    total = (
        await session.execute(
            select(func.count())
            .select_from(Booking)
            .where(Booking.professional_id == professional.id)
        )
    ).scalar_one()
    assert total == 1


@pytest.mark.asyncio
async def test_idempotencia_misma_clave_otro_cuerpo(
    session: AsyncSession, business_c: uuid.UUID, set_tenant
) -> None:
    """La misma clave con un cuerpo distinto es un conflicto, no un reintento.

    No es un caso teorico: es un cliente que mando un turno, se le perdio la
    respuesta, y al reintentar eligio otro horario. Devolverle la primera reserva
    seria responderle "listo" a algo que no pidio.

    Lo que cambia respecto del test anterior es `starts_at`, y por eso el hash
    cambia: `_hash_de_peticion` mira servicio, profesional, inicio, telefono,
    nombre y fecha local. Con todo eso igual la peticion es la misma; con el
    inicio distinto es otra peticion.
    """
    await set_tenant(business_c)
    service, professional = await _create_test_data(session, business_c)

    clave = f"conflicto-{uuid.uuid4()}"
    comun = {
        "business_id": business_c,
        "service_id": service.id,
        "professional_id": professional.id,
        "customer_first_name": "Juan",
        "customer_last_name": "Pérez",
        "customer_phone_e164": "+5491112345678",
        "duration_minutes": 60,
        "price": Decimal("1000.00"),
        "currency": "ARS",
        "local_date": FECHA_LOCAL,
        "idempotency_key": clave,
    }

    await create_booking(
        session,
        starts_at=HORA_INICIO,
        ends_at=HORA_INICIO + dt.timedelta(hours=1),
        **comun,
    )
    await session.flush()

    otro = HORA_INICIO + dt.timedelta(hours=4)
    with pytest.raises(IdempotencyConflictError, match="idempotencia"):
        await create_booking(
            session,
            starts_at=otro,
            ends_at=otro + dt.timedelta(hours=1),
            **comun,
        )


@pytest.mark.asyncio
async def test_reuso_de_cliente_por_telefono(
    session: AsyncSession, business_c: uuid.UUID, set_tenant
) -> None:
    """Dos turnos del mismo telefono son dos filas y un solo cliente.

    La unicidad es `(business_id, phone_e164)`, y es una decision consciente--ver el
    modelo--: compartir telefono es normal en una familia o en un grupo de amigos, y
    crear un cliente por turno daria un historial de reservas partido justo para las
    personas que vuelven mas.

    Se comprueban las dos mitades. Que los `customer_id` coincidan es lo que dice el
    nombre del test; que los `booking_id` **no** es lo que evita que el reuso se
    confunda con "se creo una sola reserva": dos turnos tienen que ser dos turnos.
    """
    await set_tenant(business_c)
    service, professional = await _create_test_data(session, business_c)

    comun = {
        "business_id": business_c,
        "service_id": service.id,
        "professional_id": professional.id,
        "customer_first_name": "Juan",
        "customer_last_name": "Pérez",
        "customer_phone_e164": "+5491112345678",
        "duration_minutes": 60,
        "price": Decimal("1000.00"),
        "currency": "ARS",
        "local_date": FECHA_LOCAL,
    }

    primero = await create_booking(
        session,
        starts_at=HORA_INICIO,
        ends_at=HORA_INICIO + dt.timedelta(hours=1),
        **comun,
    )

    segundo = await create_booking(
        session,
        starts_at=HORA_INICIO + dt.timedelta(hours=4),
        ends_at=HORA_INICIO + dt.timedelta(hours=5),
        **comun,
    )

    assert primero.booking_id != segundo.booking_id
    assert primero.booking is not None
    assert segundo.booking is not None
    assert primero.booking.customer_id == segundo.booking.customer_id

    # Y hay exactamente un cliente con ese telefono en el negocio, no dos.
    clientes = (
        await session.execute(
            select(func.count())
            .select_from(Customer)
            .where(
                Customer.business_id == business_c,
                Customer.phone_e164 == "+5491112345678",
            )
        )
    ).scalar_one()
    assert clientes == 1


@pytest.mark.asyncio
async def test_cada_reserva_conserva_el_nombre_con_el_que_se_reservo(
    session: AsyncSession, business_c: uuid.UUID, set_tenant
) -> None:
    """Dos reservas con el mismo telefono y nombres distintos no se renombran.

    Es el bug reportado: un telefono reusado --la madre reserva para una hija y
    despues para otra-- hacia que la segunda reserva mostrara el nombre de la
    primera. `customers` es unico por telefono, asi que el nombre tiene que vivir
    tambien en la reserva.

    Se comprueban las dos mitades de la decision: cada turno conserva **su**
    nombre (el snapshot) y la ficha del cliente queda con el ultimo usado (el
    nombre canonico para la pestana Clientes).
    """
    await set_tenant(business_c)
    service, professional = await _create_test_data(session, business_c)

    comun = {
        "business_id": business_c,
        "service_id": service.id,
        "professional_id": professional.id,
        "customer_phone_e164": "+54911000111",
        "duration_minutes": 60,
        "price": Decimal("1000.00"),
        "currency": "ARS",
        "local_date": FECHA_LOCAL,
    }

    primera = await create_booking(
        session,
        customer_first_name="Kemily",
        customer_last_name="Ledezma",
        starts_at=HORA_INICIO,
        ends_at=HORA_INICIO + dt.timedelta(hours=1),
        **comun,
    )
    segunda = await create_booking(
        session,
        customer_first_name="Alan",
        customer_last_name="Ledezma",
        starts_at=HORA_INICIO + dt.timedelta(hours=4),
        ends_at=HORA_INICIO + dt.timedelta(hours=5),
        **comun,
    )
    await session.flush()

    # Cada fila guarda el nombre con el que se reservo, no el ultimo de la ficha.
    primera_fila = await session.get(Booking, primera.booking_id)
    segunda_fila = await session.get(Booking, segunda.booking_id)
    assert primera_fila is not None and segunda_fila is not None
    assert primera_fila.customer_first_name == "Kemily"
    assert segunda_fila.customer_first_name == "Alan"
    assert primera_fila.customer_id == segunda_fila.customer_id

    # La ficha del cliente sigue al ultimo uso.
    customer = await session.get(Customer, primera_fila.customer_id)
    assert customer is not None
    assert customer.first_name == "Alan"

    # Y el panel muestra el nombre de cada reserva, no el de la ficha.
    detalle_primera = await obtener_reserva(session, primera.booking_id, business_id=business_c)
    detalle_segunda = await obtener_reserva(session, segunda.booking_id, business_id=business_c)
    assert detalle_primera.cliente_nombre == "Kemily"
    assert detalle_segunda.cliente_nombre == "Alan"


@pytest.mark.asyncio
async def test_idempotencia_misma_clave_otra_persona_es_conflicto(
    session: AsyncSession, business_c: uuid.UUID, set_tenant
) -> None:
    """La misma key, el mismo horario y **otra persona** no es un reintento.

    Antes el hash solo miraba servicio, profesional e inicio: un telefono que
    reservaba para dos personas a la misma hora recibia como "replay" la reserva
    de la otra, con el nombre y el telefono equivocados. La identidad del cliente
    forma parte de la peticion.
    """
    await set_tenant(business_c)
    service, professional = await _create_test_data(session, business_c)

    clave = f"otra-persona-{uuid.uuid4()}"
    comun = {
        "business_id": business_c,
        "service_id": service.id,
        "professional_id": professional.id,
        "starts_at": HORA_INICIO,
        "ends_at": HORA_INICIO + dt.timedelta(hours=1),
        "duration_minutes": 60,
        "price": Decimal("1000.00"),
        "currency": "ARS",
        "local_date": FECHA_LOCAL,
        "idempotency_key": clave,
    }

    await create_booking(
        session,
        customer_first_name="Ana",
        customer_last_name="Perez",
        customer_phone_e164="+54911000222",
        **comun,
    )
    await session.flush()

    with pytest.raises(IdempotencyConflictError, match="idempotencia"):
        await create_booking(
            session,
            customer_first_name="Beto",
            customer_last_name="Gomez",
            customer_phone_e164="+54911000333",
            **comun,
        )


@pytest.mark.asyncio
async def test_replay_sin_cuerpo_no_devuelve_token_vacio(
    session: AsyncSession, business_c: uuid.UUID, set_tenant
) -> None:
    """Una fila de idempotencia vieja se marca en vez de devolver token vacio.

    `response_body` es la unica copia del token en claro. Una fila escrita por una
    version anterior no la tiene: el reintento no puede inventar el enlace de
    gestion, y el router tiene que responder 409 en vez de un 201 con
    `secure_token=""` que el cliente guardaria como valido.
    """
    await set_tenant(business_c)
    service, professional = await _create_test_data(session, business_c)

    clave = f"legacy-{uuid.uuid4()}"
    primera = await create_booking(
        session,
        business_id=business_c,
        service_id=service.id,
        professional_id=professional.id,
        customer_first_name="Ana",
        customer_last_name="Perez",
        customer_phone_e164="+54911000444",
        starts_at=HORA_INICIO,
        ends_at=HORA_INICIO + dt.timedelta(hours=1),
        duration_minutes=60,
        price=Decimal("1000.00"),
        currency="ARS",
        local_date=FECHA_LOCAL,
        idempotency_key=clave,
    )
    await session.flush()

    # Simula la fila de una version anterior: hay `entity_id` pero no cuerpo.
    await session.execute(
        update(IdempotencyKey)
        .where(
            IdempotencyKey.business_id == business_c,
            IdempotencyKey.key == clave,
        )
        .values(response_body=None)
    )
    await session.flush()

    replay = await create_booking(
        session,
        business_id=business_c,
        service_id=service.id,
        professional_id=professional.id,
        customer_first_name="Ana",
        customer_last_name="Perez",
        customer_phone_e164="+54911000444",
        starts_at=HORA_INICIO,
        ends_at=HORA_INICIO + dt.timedelta(hours=1),
        duration_minutes=60,
        price=Decimal("1000.00"),
        currency="ARS",
        local_date=FECHA_LOCAL,
        idempotency_key=clave,
    )

    assert replay.booking_id == primera.booking_id
    assert replay.replay_sin_token is True
    assert replay.secure_token == ""
