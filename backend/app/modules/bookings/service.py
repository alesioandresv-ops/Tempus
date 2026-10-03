"""Servicio de bookings: crear, cancelar y reprogramar reservas.

**Reglas críticas:**
1. `professional_id` NUNCA es NULL. Si el cliente pide "cualquier profesional",
   el backend asigna uno **de entre los que están libres a esa hora**, no el
   primero de la lista.
2. La reserva nace `confirmed`, no `pending_hold`.
3. El secure_token se hashea (SHA-256) antes de guardarse. Nunca se persiste
   en claro. La excepción acotada es `idempotency_keys.response_body`, que lo
   guarda 24 horas para que un reintento reciba el token original en vez de uno
   vacío.
4. La creación es transaccional con idempotencia, y el reintento reproduce la
   respuesta original byte a byte.
5. La restricción EXCLUDE de la base es la autoridad anti-doble-reserva: la
   última línea, no la primera. Antes de llegar a ella se revalida contra el
   motor de disponibilidad, que es lo que descarta los turnos fuera de agenda.
6. `ends_at` es un dato derivado: se recalcula desde `starts_at` y la duración
   del servicio. El valor que manda el cliente no se usa.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_token, hash_token_bytes
from app.core.time import now
from app.models.enums import AuditActorType, BookingSource, BookingStatus, NotificationStatus
from app.modules.bookings.models import Booking, BookingEvent, IdempotencyKey
from app.modules.customers.models import Customer
from app.modules.professionals.models import Professional
from app.modules.services.models import ProfessionalService


@dataclass(frozen=True, slots=True)
class BookingResult:
    """Resultado de crear una reserva."""

    booking_id: uuid.UUID
    secure_token: str
    starts_at: dt.datetime
    ends_at: dt.datetime
    professional_id: uuid.UUID
    status: str
    #: La fila recien creada. La lleva el resultado para que el router pueda armar
    #: la respuesta completa --servicio, profesional, negocio, precio-- sin volver
    #: a consultarla. Antes el `POST /public/bookings` armaba un `BookingResponse`
    #: a mano con seis campos y devolvia `null` en todos los demas, mientras el
    #: `GET` de la misma reserva los llenaba: el cliente recien al reservar veia una
    #: confirmacion sin nombre de servicio ni de profesional.
    booking: Booking | None = None
    #: El replay de una fila de idempotencia escrita antes de que existiera
    #: `response_body` no puede devolver el token: no hay de donde sacarlo, porque
    #: en `bookings` solo vive su hash. Se marca para que el router responda con un
    #: aviso explicito en vez de un 201 con un token vacio que el cliente guardaria
    #: como si fuera valido.
    replay_sin_token: bool = False


@dataclass(frozen=True, slots=True)
class ReglasAgenda:
    """Las reglas del negocio que la reserva tiene que respetar.

    Van como parametros y no se releen de `businesses` adentro del servicio porque
    `create_booking` ya recibe todo lo demas del negocio y releerlo seria una
    consulta mas por cada intento de reserva, en el camino caliente del producto.

    `timezone` no es un detalle: `local_date` se deriva de el. Con UTC, un turno de
    las 23:30 en Buenos Aires se archiva en el dia siguiente y la agenda del dia
    real no lo muestra.
    """

    timezone: str = "UTC"
    slot_interval_minutes: int = 15
    min_lead_minutes: int = 60

    @classmethod
    def de_negocio(
        cls,
        business: object,
        *,
        min_lead_minutes: int | None = None,
    ) -> ReglasAgenda:
        """Construye las reglas desde una fila de `businesses`.

        Se toma `object` y no `Business` para no importar el modelo de negocios acá:
        `businesses.service` importa este modulo, y la importacion cruzada entre
        modelos de dominio y servicios es el tipo de acoplamiento que hace que un
        test de un modulo necesite el otro.

        `min_lead_minutes` se puede forzar a `0` a proposito. La antelacion minima
        existe para que un cliente que reserva por la web no tenga que levantarse
        al dia siguiente: es una restriccion sobre el **canal**, no sobre la
        agenda. El walk-in es el caso contrario--la persona esta parada en el
        mostrador-- y aplicar la misma anticipacion le rechazaria el turno mas
        comun de todos, el de ahora mismo.
        """
        return cls(
            timezone=getattr(business, "timezone", "UTC") or "UTC",
            slot_interval_minutes=getattr(business, "slot_interval_minutes", 15) or 15,
            min_lead_minutes=(
                min_lead_minutes
                if min_lead_minutes is not None
                else (getattr(business, "min_lead_minutes", 0) or 0)
            ),
        )


class SlotNoDisponibleError(ValueError):
    """El horario pedido no se puede tomar.

    Es una excepcion propia y no un `ValueError` generico porque el router la
    traduce a **409 Conflict**, y no a 400. La diferencia no es academica: 400
    dice "tu peticion esta mal, Corregila"; 409 dice "tu peticion era valida, pero
    el mundo se movio, probá otro horario". Un cliente bien escrito reintenta
    ante un 409 y aborta ante un 400, asi que confundirlos hace que un horario
    que simplemente seocupo se reporte como un error del cliente.

    Hereda de `ValueError` para que el `except ValueError` de los routers --que
    ya existe y traduce a 409-- la siga cubriendo, y para que ningun llamador
    que todavia no la conozca rompa al atrapar `ValueError`.

    Abarca mas casos que el solapamiento con otra reserva: tambien la cubre el
    horario fuera de la agenda, el profesional de vacaciones, un bloqueo y un
    feriado. Todos son el mismo hecho desde el punto de vista del cliente --no se
    puede este horario, proba otro-- y distinguirlos exigiria que el cliente
    entendiera el calendario del negocio.
    """


class IdempotencyConflictError(ValueError):
    """La `Idempotency-Key` ya se uso con un cuerpo distinto.

    Tambien 409, por la misma razon que `SlotNoDisponibleError`: la peticion del
    cliente es valida, el conflicto es con el historial de esa clave. Hereda de
    `ValueError` por la misma compatibilidad.
    """


def _generate_secure_token() -> str:
    """Genera un secure token criptográficamente seguro.

    256 bits de entropía, no secuencial, no derivable del booking_id.
    """
    return generate_token()


def _calculate_occupied_interval(
    starts_at: dt.datetime,
    ends_at: dt.datetime,
    buffer_minutes: int = 0,
) -> tuple[dt.datetime, dt.datetime]:
    """Calcula el intervalo ocupado incluyendo buffers.

    `occupied_from` / `occupied_to` son distintos de `starts_at` / `ends_at`:
    el primero incluye el buffer entre turnos, el segundo es el servicio.
    """
    occupied_from = starts_at - dt.timedelta(minutes=buffer_minutes)
    occupied_to = ends_at + dt.timedelta(minutes=buffer_minutes)
    return occupied_from, occupied_to


async def _get_or_create_customer(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    first_name: str,
    last_name: str,
    phone_e164: str,
) -> Customer:
    """Busca o crea un cliente por su teléfono.

    La unicidad es `(business_id, phone_e164)`. Dos personas que comparten
    teléfono comparten registro (decisión consciente, ver modelo).
    """
    result = await session.execute(
        select(Customer).where(
            Customer.business_id == business_id,
            Customer.phone_e164 == phone_e164,
        )
    )
    customer = result.scalar_one_or_none()
    if customer:
        return customer

    customer = Customer(
        business_id=business_id,
        first_name=first_name,
        last_name=last_name,
        phone_e164=phone_e164,
    )
    session.add(customer)
    await session.flush()
    return customer


async def _verificar_profesional_elegible(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    service_id: uuid.UUID,
    professional_id: uuid.UUID,
) -> Professional:
    """Comprueba que el profesional pedido existe, esta activo y puede el servicio.

    Son las tres condiciones del §9 que no dependen del horario. Las otras cinco
    --trabaja a esa hora, no esta de vacaciones, sin bloqueo, sin turno, con
    ventana continua-- las comprueba el motor de disponibilidad, porque dependen de
    la hora y no del servicio.

    Se separa de la eleccion del profesional a proposito: el mensaje de error de
    "este profesional no hace este servicio" es accionable para el cliente, y el de
    "no esta disponible a esa hora" tambien. Devolver "no encontrado" para los dos
    casos obliga a adivinar por que fallo.
    """
    result = await session.execute(
        select(Professional).where(
            Professional.id == professional_id,
            Professional.business_id == business_id,
            Professional.is_active.is_(True),
            Professional.archived_at.is_(None),
        )
    )
    professional = result.scalar_one_or_none()
    if not professional:
        raise ValueError("Profesional no encontrado o no disponible")

    ps_result = await session.execute(
        select(ProfessionalService).where(
            ProfessionalService.professional_id == professional.id,
            ProfessionalService.service_id == service_id,
            ProfessionalService.is_active.is_(True),
        )
    )
    if not ps_result.scalar_one_or_none():
        raise ValueError("El profesional no puede realizar este servicio")

    return professional


async def _elegir_profesional_para_el_slot(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    service_id: uuid.UUID,
    profesional_preferido: uuid.UUID | None,
    starts_at: dt.datetime,
    local_date: dt.date,
    duration_minutes: int,
    agenda: ReglasAgenda,
) -> Professional:
    """Elige a quien atiende el turno pedido, **revalidando contra el motor**.

    Este es el punto donde el §8.9 ("el backend vuelve a verificar disponibilidad")
    se cumple de verdad. Antes de este cambio la eleccion era "el primero de la
    lista por `sort_order`", sin mirar la hora, y la unica proteccion real era la
    EXCLUDE. Las dos consecuencias, y las dos son fallos de producto:

    - Un turno con el negocio cerrado, en feriado, o fuera del horario del
      profesional **se aceptaba**. La EXCLUDE solo mira solapamientos con otras
      reservas, no con el calendario. El §48 dice que si el negocio esta cerrado no
      se muestra disponibilidad; accepting la reserva es la misma mentira, solo que
      un paso mas tarde y sin avisar.
    - En "cualquier profesional" se podia ofrecer un horario que era de Beto y
      ended up asignado a Ana, que estaba ocupada: la EXCLUDE rechazaba el INSERT y
      el cliente recibia un 409 sobre una hora que el propio backend le habia
      mostrado segundos antes.

    La revalidacion va contra `availability.service.buscar_slot`, que es el mismo
    motor puro que responde el endpoint de disponibilidad. Una sola fuente de verdad
    (§32): si aqui se calculara distinto, "se muestra" y "se puede" dejarian de
    coincidir, que es la unica forma segura de que dejen de coincidir.

    Devuelve el profesional con la fila cargada, para no hacer una segunda consulta
    por el `id` que el motor ya conoce.
    """
    from app.modules.availability.service import buscar_slot

    slot = await buscar_slot(
        session,
        business_id=str(business_id),
        professional_id=str(profesional_preferido) if profesional_preferido else None,
        service_id=str(service_id),
        service_duration_minutes=duration_minutes,
        slot_interval_minutes=agenda.slot_interval_minutes,
        local_date=local_date,
        timezone=agenda.timezone,
        starts_at=starts_at,
        min_lead_minutes=agenda.min_lead_minutes,
    )

    if slot is None:
        raise SlotNoDisponibleError("Ese horario ya no esta disponible. Elegi otro.")

    elegido = profesional_preferido or slot.candidatos[0]
    if elegido not in slot.candidatos:
        raise SlotNoDisponibleError(
            "Ese horario ya no esta disponible para el profesional elegido."
        )

    result = await session.execute(
        select(Professional).where(
            Professional.id == elegido,
            Professional.business_id == business_id,
        )
    )
    professional = result.scalar_one_or_none()
    if professional is None:
        raise ValueError("Profesional no encontrado o no disponible")
    return professional


def _hash_de_peticion(
    *,
    service_id: uuid.UUID,
    professional_id: uuid.UUID | None,
    starts_at: dt.datetime,
) -> str:
    """Huella del contenido que define una reserva.

    Es lo que distingue un **reintento** (misma key, mismo cuerpo) de un **abuso**
    (misma key, cuerpo distinto). Lo segundo no es un reintento que se quedo sin
    respuesta: es un cliente que colgado una reserva y despues quiere otra con el
    mismo identificador. Devolverle la primera reserva seria responderle "listo" a
    algo que no pidio, y no crear la que pidio.
    """
    return hashlib.sha256(
        f"{service_id}:{professional_id}:{starts_at.isoformat()}".encode()
    ).hexdigest()


async def _replay_idempotent(
    session: AsyncSession,
    business_id: uuid.UUID,
    idempotency_key: str,
    request_hash: str,
) -> BookingResult | None:
    """Devuelve la reserva original si la `Idempotency-Key` ya se usó.

    **Rechaza la key reutilizada con otro cuerpo.** `IdempotencyKey.request_hash`
    existe justamente para eso y no se comparaba: la misma key con una segunda
    reserva distinta devolvia un 201 con una reserva que el cliente no pidio.

    **Reproduce el `secure_token` original.** El token en claro no se persiste --
    en `bookings` solo vive su SHA-256-- asi que el camino trivial era devolverlo
    vacio. Eso es peor que devolver un error: el cliente guardaba un token vacio,
    lo guardaba en su estado, y cuando queria cancelar o reprogramar se encontraba
    con un enlace muerto y ningun mensaje que lo explicara.

    Por eso existe `IdempotencyKey.response_body`: guarda la respuesta ya
    serializada durante la ventana de vida de la key. Es la unica copia del token
    en claro que existe fuera de la respuesta original, vive acotada a 24 horas por
    `expires_at`, y es lo que convierte el reintento en un replay real en vez de en
    una reserva sin gestionar.

    Devolver `None` significa "no hay reintento, seguí": es el caso normal y el
    único que debe escribir la fila de idempotencia. Si la fila existe pero su
    `response_body` es NULL, la petición anterior murió antes de completar y el
    reintento tiene que poder avanzar; distinguir los dos casos es la razón de
    mirar el cuerpo y no solo la existencia de la fila.
    """
    existing = await session.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.business_id == business_id,
            IdempotencyKey.scope == "booking",
            IdempotencyKey.key == idempotency_key,
        )
    )
    row = existing.scalar_one_or_none()
    if row is None:
        return None

    if row.request_hash != request_hash:
        raise IdempotencyConflictError(
            "Esa clave de idempotencia ya se uso con otra reserva. Usa una clave nueva."
        )

    # **El token viene de `response_body`; todo lo demas, de la fila.**
    #
    # Son dos fuentes y la division no es arbitraria. El token en claro solo
    # existe ahi: en `bookings` vive su hash y no hay forma de revertirlo. En
    # cambio los datos del turno--inicio, fin, profesional, estado-- se leen de la
    # reserva, que es la fuente de verdad y esta actualizada: si el turno se
    # reprogramo o se cancelo entre el intento original y el reintento, el replay
    # tiene que reflejar eso, no devolver una foto de hace una hora.
    #
    # Armar el `BookingResult` entero desde `response_body` --que es lo que hacia
    # esta version-- ademas rompia el router: `booking` quedaba en `None` y el
    # endpoint tomaba eso por una fila deprecada sin cuerpo y respondia 409. El
    # sintoma era "el reintento siempre falla" en un replay que en realidad tenia
    # toda la informacion.
    secure_token = str((row.response_body or {}).get("secure_token") or "")

    if row.entity_id is not None:
        replayed = await session.execute(select(Booking).where(Booking.id == row.entity_id))
        booking = replayed.scalar_one_or_none()
        if booking is not None:
            return BookingResult(
                booking_id=booking.id,
                secure_token=secure_token,
                starts_at=booking.starts_at,
                ends_at=booking.ends_at,
                professional_id=booking.professional_id,
                status=str(booking.status),
                booking=booking,
                # Fila escrita por una version anterior, que guardaba la key sin
                # el cuerpo. No hay de donde sacar el token y se dice con este
                # indicador en vez de devolver un 201 con un token vacio que el
                # cliente guardaria como enlace de gestion y no abriria nunca.
                replay_sin_token=not secure_token,
            )

    # Fila a medio escribir de un intento fallido: se libera la clave para que
    # el reintento pueda completar en vez de quedar bloqueado hasta el TTL.
    await session.delete(row)
    await session.flush()
    return None


async def _elegir_profesional_sin_revalidar(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    service_id: uuid.UUID,
    professional_id: uuid.UUID | None,
) -> Professional:
    """Elige profesional **sin** mirar el calendario. Solo para callers sin `agenda`.

    Existe unicamente para los tests de integracion, que no siembran `business_hours`:
    sin agenda no hay contra que revalidar, y sembrar un calendario completo en cada
    test para probar la EXCLUDE agrega ruido sin agregar cobertura -- la
    revalidacion tiene sus propios tests, que si lo tienen.

    En cuanto a un camino de produccion use esto, deja de ser un atajo de test y
    pasa a ser un agujero: por eso no tiene default y el unico llamador de la
    aplicacion esta obligado a pasar `agenda`.
    """
    if professional_id is not None:
        return await _verificar_profesional_elegible(
            session,
            business_id=business_id,
            service_id=service_id,
            professional_id=professional_id,
        )

    from app.modules.availability.service import _load_eligible_professionals

    candidatos = await _load_eligible_professionals(session, str(business_id), str(service_id))
    if not candidatos:
        raise ValueError("No hay profesionales disponibles para este servicio")

    result = await session.execute(select(Professional).where(Professional.id == candidatos[0]))
    professional = result.scalar_one_or_none()
    if professional is None:
        raise ValueError("No hay profesionales disponibles para este servicio")
    return professional


async def create_booking(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    service_id: uuid.UUID,
    professional_id: uuid.UUID | None,
    customer_first_name: str,
    customer_last_name: str,
    customer_phone_e164: str,
    starts_at: dt.datetime,
    ends_at: dt.datetime,
    duration_minutes: int,
    price: Decimal,
    currency: str,
    local_date: dt.date,
    agenda: ReglasAgenda | None = None,
    buffer_minutes: int = 0,
    idempotency_key: str | None = None,
    source: BookingSource = BookingSource.PUBLIC,
    notificar: bool = True,
    actor_user_id: uuid.UUID | None = None,
) -> BookingResult:
    """Crea una reserva de forma transaccional.

    Args:
        session: Sesión de BD (debe estar en transacción).
        business_id: ID del negocio.
        service_id: ID del servicio.
        professional_id: ID del profesional, o None para asignación automática.
        customer_first_name: Nombre del cliente.
        customer_last_name: Apellido del cliente.
        customer_phone_e164: Teléfono del cliente en E.164.
        starts_at: Inicio del servicio (UTC).
        ends_at: Fin del servicio (UTC). **Se ignora y se recalcula** desde
            `starts_at` y `duration_minutes`; ver la nota mas abajo.
        duration_minutes: Duración del servicio.
        price: Precio del servicio.
        currency: Moneda (ISO 4217).
        local_date: Fecha local del negocio.
        agenda: Reglas del negocio (timezone, grilla, antelacion). Con esto la
            reserva se revalida contra el motor de disponibilidad. **Sin esto no se
            revalida**, y es una opcion deliberadamente explicita: los dos
            llamadores reales --el endpoint publico y el walk-in-- la pasan.
        buffer_minutes: Minutos de buffer entre turnos.
        idempotency_key: Clave de idempotencia (opcional).
        source: De donde viene la reserva. `public` es una reserva online; `walkin`
            es un cliente que llego al mostrador. No cambia como se ocupa el
            horario ni la validacion -- cambia de donde viene y si tiene sentido
            avisarle por WhatsApp.
        notificar: Si se programan los recordatorios. Se apaga para el walk-in: el
            cliente esta parado en el mostrador, asi que un "tu turno quedo
            confirmado" por WhatsApp y un "tu turno es en 2 horas" para un turno
            que empieza en cinco minutos son ruido, no servicio. Un recordatorio de
            dos horas para una persona que ya llego es directamente falso.
        actor_user_id: Usuario del panel que la registro, si la registro alguien.
            Va al evento de auditoria para que la historia diga *quien* agrego el
            turno a mano, y no solo que existe.

    Returns:
        BookingResult con el ID de la reserva, el secure token y la fila creada.

    Raises:
        SlotNoDisponibleError: El horario no se puede tomar, sea porque otro turno
            lo ocupa, porque esta fuera de la agenda, o porque el profesional no
            puede hacerlo.
        IdempotencyConflictError: La `Idempotency-Key` ya se uso con otro cuerpo.
        ValueError: El profesional no existe, no esta activo, o no hace el servicio.
    """
    # `ends_at` se recalcula desde `starts_at` y la duracion real del servicio, y el
    # valor que manda el cliente se descarta. Un `ends_at` de 60 minutos para un
    # servicio de 90 no es un dato levemente equivocado: es un turno que se guarda
    # con 30 minutos menos de los que se le vendieron, y como `occupied_to` sale de
    # `ends_at`, ademas libera 30 minutos de agenda que el turno real deberia
    # seguir ocupando. Un cliente que manda el `ends_at` corto puede encadenar dos
    # servicios de 90 uno detras del otro sin que se solapen.
    #
    # Es un dato derivado. Confiar en el del cliente nunca hace falta.
    ends_at = starts_at + dt.timedelta(minutes=duration_minutes)

    request_hash = _hash_de_peticion(
        service_id=service_id,
        professional_id=professional_id,
        starts_at=starts_at,
    )

    # Idempotencia: un reintento de red o un doble clic recupera la reserva
    # original en vez de crear una segunda. ADR-0008 capa 4.
    if idempotency_key:
        replay = await _replay_idempotent(session, business_id, idempotency_key, request_hash)
        if replay is not None:
            return replay

    # Obtener o crear cliente
    customer = await _get_or_create_customer(
        session,
        business_id=business_id,
        first_name=customer_first_name,
        last_name=customer_last_name,
        phone_e164=customer_phone_e164,
    )

    # Elegir profesional **revalidando contra el motor de disponibilidad**.
    #
    # Las dos ramas se resuelven con una sola llamada, y esa es la parte que
    # importa: si viene un profesional concreto se pregunta por el, y si no se
    # pregunta por "cualquiera". En los dos casos la respuesta es la lista de quien
    # puede de verdad, no un `ORDER BY` que elige al primero de la lista sin mirar la
    # hora.
    if professional_id is not None:
        await _verificar_profesional_elegible(
            session,
            business_id=business_id,
            service_id=service_id,
            professional_id=professional_id,
        )

    if agenda is None:
        professional = await _elegir_profesional_sin_revalidar(
            session,
            business_id=business_id,
            service_id=service_id,
            professional_id=professional_id,
        )
    else:
        professional = await _elegir_profesional_para_el_slot(
            session,
            business_id=business_id,
            service_id=service_id,
            profesional_preferido=professional_id,
            starts_at=starts_at,
            local_date=local_date,
            duration_minutes=duration_minutes,
            agenda=agenda,
        )

    # Calcular intervalo ocupado
    occupied_from, occupied_to = _calculate_occupied_interval(starts_at, ends_at, buffer_minutes)

    # Generar secure token
    secure_token = _generate_secure_token()
    # Crudo, no hexadecimal: la columna es `BYTEA(32)`. Ver `hash_token_bytes`.
    secure_token_hash = hash_token_bytes(secure_token)

    # Crear reserva
    booking = Booking(
        business_id=business_id,
        customer_id=customer.id,
        professional_id=professional.id,
        service_id=service_id,
        # El **miembro del enum**, no el texto. La columna esta declarada como
        # `Mapped[BookingStatus]` y SQLAlchemy no convierte en la asignacion: si
        # aca se escribe `"confirmed"`, el objeto en memoria tiene un `str` y el
        # mismo objeto recien leido de la base tiene un `BookingStatus`. No es
        # cosmético--`Booking.occupies_calendar` hace `self.status.value` y revienta
        # con `AttributeError: 'str' object has no attribute 'value'` sobre una fila
        # recien creada, que es justo cuando se consulta. `cancel_booking` y el
        # panel ya usaban el enum; este era el unico que no.
        status=BookingStatus.CONFIRMED,
        source=source,
        created_by_user_id=actor_user_id,
        starts_at=starts_at,
        ends_at=ends_at,
        occupied_from=occupied_from,
        occupied_to=occupied_to,
        local_date=local_date,
        duration_minutes=duration_minutes,
        price_snapshot=price,
        currency=currency,
        secure_token_hash=secure_token_hash,
    )
    session.add(booking)
    # El `EXCLUDE` de la base es la autoridad anti-doble-reserva (ADR-0008). Este
    # `flush` es donde se decide: si dos clientes piden las 10:00 a la vez, uno
    # entra y el otro recibe `ExclusionViolationError` de PostgreSQL, sin que
    # ningun codigo de aplicacion haya decidido nada.
    #
    # Por eso el error se traduce aca y no se deja subir. Un `IntegrityError`
    # crudo llega al cliente como un 500, que dice "el servidor se rompio" y no
    # "ese horario ya no esta". Para el cliente son dos problemas distintos: uno
    # se reintenta, el otro elige otro horario.
    try:
        await session.flush()
    except IntegrityError as exc:
        if "no_overlap" in str(exc.orig) or "exclusion" in str(exc.orig).lower():
            raise SlotNoDisponibleError("Ese horario acaba de ser tomado. Elegí otro.") from exc
        raise

    # Crear evento de auditoría
    event = BookingEvent(
        business_id=business_id,
        booking_id=booking.id,
        type="created",
        # El actor de una reserva online es el cliente; el de una que registro
        # alguien desde el panel es ese usuario. Sin esto, la auditoria de un
        # walk-in diria que el cliente se reservo solo, y el panel no tendria forma
        # de saber que turno se agrego a mano.
        actor_type=(AuditActorType.USER if actor_user_id else AuditActorType.CUSTOMER),
        actor_id=actor_user_id if actor_user_id else customer.id,
        payload={
            "service_id": str(service_id),
            "professional_id": str(professional.id),
            "starts_at": starts_at.isoformat(),
        },
    )
    session.add(event)

    # Guardar idempotencia
    if idempotency_key:
        idem = IdempotencyKey(
            business_id=business_id,
            scope="booking",
            key=idempotency_key,
            # El mismo `request_hash` que se comparo al entrar. Se calculo una vez
            # y se usa en los dos lados a proposito: si el check y el INSERT
            # calcularan el hash por su cuenta, cualquier divergencia entre ambos
            # --un campo de mas, un formato distinto-- haria que todo reintento
            # pareciera un conflicto.
            request_hash=request_hash,
            response_status=201,
            entity_id=booking.id,
            expires_at=now() + dt.timedelta(hours=24),
        )
        session.add(idem)

    # Programar los recordatorios. Va despues del flush porque `booking.id` lo
    # genera la base, y `notification_requests.booking_id` es FK: antes del id no
    # hay fila que pueda referenciar.
    #
    # Import local y no arriba del modulo: `notifications.service` importa
    # `bookings.models`, asi que importar el servicio de notificaciones en el tope
    # de `bookings.service` cerraria el ciclo. El import diferido lo rompe sin
    # pagar el costo en cada arranque.
    from app.modules.notifications.service import schedule_booking_notifications

    if notificar:
        await schedule_booking_notifications(session, booking)

    result = BookingResult(
        booking_id=booking.id,
        secure_token=secure_token,
        starts_at=starts_at,
        ends_at=ends_at,
        professional_id=professional.id,
        status=BookingStatus.CONFIRMED,
        booking=booking,
    )

    # Guardar la respuesta en la fila de idempotencia. Es lo que permite que un
    # reintento devuelva **el mismo** `secure_token` en vez de uno vacio, y es la
    # razon por la que la columna existe desde el primer dia y nunca se escribio.
    # Va al final, cuando el resultado ya esta completo.
    if idempotency_key:
        idem.response_body = {
            "booking_id": str(result.booking_id),
            "secure_token": result.secure_token,
            "starts_at": result.starts_at.isoformat(),
            "ends_at": result.ends_at.isoformat(),
            "professional_id": str(result.professional_id),
            "status": result.status,
        }

    return result


async def cancel_booking(
    session: AsyncSession,
    *,
    booking_id: uuid.UUID,
    secure_token: str,
    reason: str | None = None,
    cancellation_window_minutes: int = 120,
) -> Booking:
    """Cancela una reserva.

    Valida el secure_token y cambia el estado a 'cancelled'.
    El horario se libera automáticamente (la restricción EXCLUDE solo aplica
    a estados 'confirmed' y 'pending_hold').

    Las reglas viven acá y no en el router, para que el endpoint público y el
    panel apliquen exactamente la misma: `ARCHITECTURE.md` §11 pide que la
    validación se revalide contra la base en cada request.
    """
    # Buscar reserva por token
    token_hash = hash_token_bytes(secure_token)
    result = await session.execute(select(Booking).where(Booking.secure_token_hash == token_hash))
    booking = result.scalar_one_or_none()
    if not booking:
        raise ValueError("Reserva no encontrada")

    if booking.id != booking_id:
        raise ValueError("Token no corresponde a la reserva")

    _assert_cancellable(booking, cancellation_window_minutes)

    # Cancelar
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = now()
    booking.cancel_reason = reason

    # Evento de auditoría
    event = BookingEvent(
        business_id=booking.business_id,
        booking_id=booking.id,
        type="cancelled",
        actor_type="customer",
        payload={"reason": reason} if reason else {},
    )
    session.add(event)

    # Los recordatorios pendientes mueren con la reserva, en la MISMA
    # transaccion: si quedaran vivos, un recordatorio de las 2 horas podría
    # dispararse para un turno que ya no existe. Ver ADR-0012.
    await _cancel_pending_notifications(session, booking.id)

    await session.flush()
    return booking


def _assert_cancellable(booking: Booking, cancellation_window_minutes: int) -> None:
    """Reglas de cancelación revalidadas contra el estado actual.

    Rejecta explícitamente los estados terminales en lugar de enumerar los que
    sí se pueden cancelar. Un estado nuevo agregado al enum queda del lado
    seguro por defecto, que es la única dirección razonable en la que fallar.
    """
    if booking.status == "cancelled":
        raise ValueError("La reserva ya está cancelada")
    if booking.status in ("completed", "no_show"):
        raise ValueError(f"No se puede cancelar una reserva {booking.status}")

    current = now()
    if booking.starts_at <= current:
        raise ValueError("La reserva ya comenzó")

    deadline = booking.starts_at - dt.timedelta(minutes=cancellation_window_minutes)
    if current > deadline:
        raise ValueError(
            "La ventana de cancelación ya cerró. Contactate directamente con el negocio."
        )


async def _cancel_pending_notifications(session: AsyncSession, booking_id: uuid.UUID) -> None:
    """Pasa a `cancelled` los recordatorios vivos de una reserva.

    `notification_requests` tiene `UNIQUE (booking_id, kind)`, asi que el
    UPDATE es idempotente y no necesita leer antes de escribir. `updated_at` lo
    pone el trigger `set_updated_at`, no este codigo.
    """
    from app.modules.notifications.models import NotificationRequest

    await session.execute(
        update(NotificationRequest)
        .where(
            NotificationRequest.booking_id == booking_id,
            NotificationRequest.status == NotificationStatus.PENDING,
        )
        .values(status=NotificationStatus.CANCELLED)
    )


async def reschedule_booking(
    session: AsyncSession,
    *,
    booking_id: uuid.UUID,
    secure_token: str,
    new_starts_at: dt.datetime,
    new_ends_at: dt.datetime,
    new_duration_minutes: int,
    new_professional_id: uuid.UUID | None = None,
    buffer_minutes: int = 0,
    timezone: str = "UTC",
) -> Booking:
    """Reprograma una reserva.

    Cambia el horario de la reserva. La restricción EXCLUDE garantiza
    que no se solape con otras reservas.

    La reserva se reprograma **en su misma fila**, no creando una nueva: el
    modelo tiene `rescheduled_from_id` para el caso de una cadena, pero
    duplicar la fila dejaría el token viejo apuntando a un turno fantasma y
    los recordatorios en un estado que nadie puede reconciliar.

    `timezone` es el del negocio y no un parametro opcional de conveniencia:
    `local_date` se recalcula a partir de el, y con UTC el calculo da el dia
    equivocado para cualquier negocio de LatAm. Un turno un martes a las 22:00
    en Buenos Aires es el miercoles 01:00 UTC, asi que la reserva quedaria
    archivada en el dia siguiente, y la agenda del dia real no la mostraria.
    """
    # Buscar reserva por token
    token_hash = hash_token_bytes(secure_token)
    result = await session.execute(select(Booking).where(Booking.secure_token_hash == token_hash))
    booking = result.scalar_one_or_none()
    if not booking:
        raise ValueError("Reserva no encontrada")

    if booking.id != booking_id:
        raise ValueError("Token no corresponde a la reserva")

    if booking.status == "cancelled":
        raise ValueError("No se puede reprogramar una reserva cancelada")
    if booking.status in ("completed", "no_show"):
        raise ValueError(f"No se puede reprogramar una reserva {booking.status}")
    if new_starts_at <= now():
        raise ValueError("El nuevo horario ya pasó")
    if new_ends_at <= new_starts_at:
        raise ValueError("El fin del turno no puede ser anterior al inicio")

    # Guardar valores anteriores para auditoría
    old_starts_at = booking.starts_at
    old_ends_at = booking.ends_at

    # Actualizar horario
    booking.starts_at = new_starts_at
    booking.ends_at = new_ends_at
    booking.duration_minutes = new_duration_minutes
    booking.local_date = new_starts_at.astimezone(ZoneInfo(timezone)).date()

    # Recalcular intervalo ocupado
    occupied_from, occupied_to = _calculate_occupied_interval(
        new_starts_at, new_ends_at, buffer_minutes
    )
    booking.occupied_from = occupied_from
    booking.occupied_to = occupied_to

    # Cambiar profesional si se especifica
    if new_professional_id:
        booking.professional_id = new_professional_id

    # Los recordatorios del horario viejo quedaron pegados a un turno que ya no
    # existe. Se cancelan y los nuevos se crean aparte.
    await _cancel_pending_notifications(session, booking.id)

    # Evento de auditoría
    event = BookingEvent(
        business_id=booking.business_id,
        booking_id=booking.id,
        type="rescheduled",
        actor_type="customer",
        from_starts_at=old_starts_at,
        to_starts_at=new_starts_at,
        payload={
            "from_starts_at": old_starts_at.isoformat(),
            "to_starts_at": new_starts_at.isoformat(),
            "from_ends_at": old_ends_at.isoformat(),
            "to_ends_at": new_ends_at.isoformat(),
        },
    )
    session.add(event)

    # Outbox de notificaciones. Va en la misma transaccion que la reserva: si
    # fallara el insert y no la reserva, el cliente tendria un turno sin ningun
    # recordatorio y nadie se enteraria hasta el dia del turno.
    from app.modules.notifications.service import schedule_booking_notifications

    await schedule_booking_notifications(session, booking)

    await session.flush()
    return booking


__all__ = [
    "BookingResult",
    "IdempotencyConflictError",
    "ReglasAgenda",
    "SlotNoDisponibleError",
    "cancel_booking",
    "create_booking",
    "reschedule_booking",
]
