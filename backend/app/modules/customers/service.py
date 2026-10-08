"""Servicio de clientes: consulta y edicion.

Lo que un negocio necesita de un cliente es lo que ya esta en la tabla: nombre,
telefono, si recibe mensajes, y cuando vino por ultima vez. Lo que **no** esta es
un historial de visitas, porque ese historial son las reservas y se consulta
agregando por `customer_id` -- duplicarlo en una columna seria una segunda fuente
de verdad que se desincroniza de la primera en el primer error de escritura.

Sobre el opt-out: `is_opted_out` es la unica palanca real de "no me escribas mas".
Se expone como un `PATCH` de una linea y deliberadamente no se combina con nada
mas, porque es la accion que un cliente pide por WhatsApp y tiene que estar a un
click. Un opt-out escondido en un formulario de edicion de notas no se usa.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import NotFoundError, ValidationError
from app.models.enums import BookingStatus
from app.modules.bookings.models import Booking
from app.modules.customers.models import Customer
from app.modules.professionals.models import Professional

MAX_NAME_LENGTH = 160
MAX_NOTES_LENGTH = 4096

#: Estados que cuentan como "gasto realizado" para `total_gastado`. Un turno
#: cancelado o `no_show` no es dinero que entro aunque la reserva exista.
ESTADOS_PAGADOS = (BookingStatus.CONFIRMED, BookingStatus.COMPLETED)

#: Un telefono E.164 empieza siempre por mas y tiene entre 8 y 15 digitos. Se valida
#: la forma porque el envio a WhatsApp depende de eso, y un numero con espacios o
#: un prefijo local no llega. No se valida que exista: eso solo lo sabe Meta, y
#: rechazar un numero valido en el alta seria peor que acceptarlo y que falle el
#: envio con un error claro.
PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


def validar_telefono(telefono: str) -> str:
    """Valida un telefono E.164 y devuelve el canonico."""
    limpio = telefono.strip().replace(" ", "")
    if not PHONE_PATTERN.match(limpio):
        raise ValidationError(
            "El telefono debe estar en formato E.164, por ejemplo +5491122334455."
        )
    return limpio


@dataclass(frozen=True, slots=True)
class CustomerUpdate:
    """Cambios parciales de un cliente."""

    first_name: str | None = None
    last_name: str | None = None
    notes: str | None = None
    is_opted_out: bool | None = None
    marketing_opt_in: bool | None = None


@dataclass(frozen=True, slots=True)
class CustomerListItem:
    """Cliente resumido para el listado del panel.

    `booking_count`, `last_booking_at`, `total_gastado` y
    `profesional_mas_frecuente` salen de agregados sobre `bookings` en la misma
    consulta. Se traen aca y no como columnas de `customers` para no mantener un
    contador que cada cancelacion o reprogramacion tiene que mantener al dia.
    """

    customer: Customer
    booking_count: int
    last_booking_at: object | None
    total_gastado: Decimal | None
    profesional_mas_frecuente: str | None


async def listar_clientes(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    busqueda: str | None = None,
    solo_optout: bool = False,
    limite: int = 50,
    offset: int = 0,
) -> list[CustomerListItem]:
    """Clientes del negocio, con el total de reservas de cada uno.

    `busqueda` filtra por nombre o telefono. La busqueda es por coincidencia
    parcial y sin distincion de mayusculas, que es lo que espera alguien que
    escribe "juan" esperando encontrar a "Juan Perez". Sin `ILIKE` el admin
    tendria que escribir el nombre exacto, y eso no lo hace nadie.
    El orden es por ultima visita (**agregado** sobre `bookings`, no la columna
    `customers.last_booking_at`, que nadie mantiene y por lo tanto es siempre
    `NULL`): el cliente que vino hace un rato arriba, el que no vino nunca abajo.
    `total`, `ultima`, `gastado` y `prof` son **subconsultas escalares
    correlacionadas**, no una sola con varias columnas. La version anterior
    armaba una subconsulta de dos columnas y la declaraba `scalar_subquery()`,
    que le dice a SQLAlchemy "esto trae una columna": PostgreSQL rechaza la
    consulta entera con `la subconsulta debe retornar solo una columna` y
    `GET /business/clientes` devuelve 500 para todos los tenants. Subconsultas
    escalares de una columna cada una es la forma que corresponde; la
    alternativa--una subconsulta derivada con `.subquery()`-- evita ese error
    pero introduce un producto cartesiano entre la tabla derivada y
    `customers`, que SQLAlchemy marca como `SAWarning` y el proyecto trata como
    error.
    """
    total_reservas = (
        select(func.count(Booking.id))
        .where(Booking.customer_id == Customer.id)
        .correlate(Customer)
        .scalar_subquery()
        .label("total")
    )
    ultima_reserva = (
        select(func.max(Booking.starts_at))
        .where(Booking.customer_id == Customer.id)
        .correlate(Customer)
        .scalar_subquery()
        .label("ultima")
    )
    #: Solo los estados pagados cuentan como gasto: una cancelada no es dinero
    #: que entro, aunque el turno haya existido.
    total_gastado = (
        select(func.coalesce(func.sum(Booking.price_snapshot), 0))
        .where(
            Booking.customer_id == Customer.id,
            Booking.status.in_(ESTADOS_PAGADOS),
        )
        .correlate(Customer)
        .scalar_subquery()
        .label("gastado")
    )
    #: El profesional con mas reservas del cliente. Desempata por nombre para
    #: que el resultado sea determinista y no dependa del plan de ejecucion.
    profesional_frecuente = (
        select(Professional.display_name)
        .join(Booking, Booking.professional_id == Professional.id)
        .where(Booking.customer_id == Customer.id)
        .group_by(Professional.id, Professional.display_name)
        .order_by(func.count(Booking.id).desc(), Professional.display_name)
        .limit(1)
        .correlate(Customer)
        .scalar_subquery()
        .label("prof")
    )

    consulta = (
        select(
            Customer,
            total_reservas,
            ultima_reserva,
            total_gastado,
            profesional_frecuente,
        )
        .where(Customer.business_id == business_id)
        .order_by(ultima_reserva.desc().nulls_last(), Customer.first_name)
        .limit(limite)
        .offset(offset)
    )

    if busqueda and busqueda.strip():
        patron = f"%{busqueda.strip()}%"
        consulta = consulta.where(
            Customer.first_name.ilike(patron)
            | Customer.last_name.ilike(patron)
            | Customer.phone_e164.ilike(patron)
        )
    if solo_optout:
        consulta = consulta.where(Customer.is_opted_out.is_(True))

    result = await session.execute(consulta)
    filas = []
    for customer, total, ultima, gastado, prof in result.all():
        filas.append(
            CustomerListItem(
                customer=customer,
                booking_count=int(total or 0),
                last_booking_at=ultima,
                total_gastado=gastado,
                profesional_mas_frecuente=prof,
            )
        )
    return filas


async def obtener_cliente(
    session: AsyncSession, customer_id: uuid.UUID, *, business_id: uuid.UUID
) -> Customer:
    """Un cliente del negocio, o `NotFoundError`."""
    result = await session.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.business_id == business_id,
        )
    )
    customer = result.scalar_one_or_none()
    if customer is None:
        raise NotFoundError("Cliente no encontrado.")
    return customer


async def actualizar_cliente(
    session: AsyncSession,
    customer_id: uuid.UUID,
    business_id: uuid.UUID,
    cambios: CustomerUpdate,
) -> Customer:
    """Aplica cambios parciales a un cliente."""
    customer = await obtener_cliente(session, customer_id, business_id=business_id)

    if cambios.first_name is not None:
        limpio = cambios.first_name.strip()
        if not limpio:
            raise ValidationError("El nombre no puede estar vacio.")
        customer.first_name = limpio[:MAX_NAME_LENGTH]
    if cambios.last_name is not None:
        customer.last_name = cambios.last_name.strip()[:MAX_NAME_LENGTH]
    if cambios.notes is not None:
        customer.notes = cambios.notes[:MAX_NOTES_LENGTH]

    # El opt-out manda sobre el marketing: `CheckConstraint opt_out_clears_marketing`
    # dice que un cliente dado de baja tiene `marketing_opt_in = false`. Ajustar los
    # dos juntos es lo unico que no produce un `IntegrityError`:
    #
    # - dar de baja con marketing en true -> viola el check si se guarda tal cual;
    # - sacar el opt-out con marketing en false -> se revive sin permiso de marketing.
    #
    # Por eso se escriben en este orden y no como dos campos independientes.
    if cambios.is_opted_out is not None:
        customer.is_opted_out = cambios.is_opted_out
        if cambios.is_opted_out:
            customer.marketing_opt_in = False
        elif cambios.marketing_opt_in is None:
            # Salir del opt-out no reactiva el marketing por su cuenta. Volver a dar
            # permiso de marketing es otra decision, y por eso tiene su propio
            # campo en el mismo pedido.
            customer.marketing_opt_in = False
    if cambios.marketing_opt_in is not None and not cambios.is_opted_out:
        if cambios.marketing_opt_in and customer.is_opted_out:
            raise ValidationError("No se puede activar marketing en un cliente dado de baja.")
        customer.marketing_opt_in = cambios.marketing_opt_in

    await session.flush()
    return customer


__all__ = [
    "MAX_NAME_LENGTH",
    "MAX_NOTES_LENGTH",
    "PHONE_PATTERN",
    "CustomerListItem",
    "CustomerUpdate",
    "actualizar_cliente",
    "listar_clientes",
    "obtener_cliente",
    "validar_telefono",
]
