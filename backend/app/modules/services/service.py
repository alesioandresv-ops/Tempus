"""Servicio de servicios del negocio: alta, edicion y baja logica.

**Archivar, no borrar.** `Service.archived_at` existe y `is_offered` depende de el
por una razon concreta: hay `bookings.service_id` con FK `RESTRICT`. Un `DELETE`
físico de un servicio que tiene reservas historicas falla con un `IntegrityError`,
y el `IntegrityError` traducido a 500 le dice al administrador que el sistema esta
roto cuando lo que paso es que el servicio se uso. Archivar deja la historia
intacta y saca el servicio de la pagina publica, que es lo que el quiere.

Tambien se desactiva con `is_active`, que es distinto de archivar: desactivar
deja el servicio cargado en el panel y lo saca de la reserva online; archivar ademas
lo saca del panel. Las dos cosas se ofrecen porque son decisiones distintas y
mezclarlas obliga al administrador a elegir entre "no se puede reservar" y "no se
ve".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import NotFoundError, ValidationError
from app.core.time import now
from app.modules.services.models import Service

#: Estados que cuentan como "la reserva ocupa lugar": los unicos que bloquean el
#: horario y los que un reporte de facturacion tendria que mirar. Canceladas y
#: `no_show` no, y por eso no impiden archivar.
#:
#: Se deja el dato documentado pero no se usa todavia: `archivar_servicio` no
#: consulta reservas, y no debe empezar a hacerlo por el solo hecho de que este
#: comentario mentione el criterio. Si alguna vez hace falta, la consulta va con
#: estos estados.
OCUPYING_STATUSES: tuple[str, ...] = ("confirmed", "pending_hold")

MAX_NAME_LENGTH = 160
MAX_DESCRIPTION_LENGTH = 1024


@dataclass(frozen=True, slots=True)
class ServiceCreate:
    """Datos de alta de un servicio."""

    name: str
    duration_minutes: int
    price: Decimal
    currency: str
    description: str | None = None
    color: str | None = None
    sort_order: int = 0


@dataclass(frozen=True, slots=True)
class ServiceUpdate:
    """Cambios parciales de un servicio. Los `None` se ignoran (PATCH)."""

    name: str | None = None
    description: str | None = None
    duration_minutes: int | None = None
    price: Decimal | None = None
    currency: str | None = None
    color: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


def _validar_nombre(nombre: str) -> str:
    limpio = nombre.strip()
    if not limpio:
        raise ValidationError("El nombre del servicio no puede estar vacio.")
    if len(limpio) > MAX_NAME_LENGTH:
        raise ValidationError(f"El nombre no puede pasar de {MAX_NAME_LENGTH} caracteres.")
    return limpio


async def _siguiente_sort_order(session: AsyncSession, business_id: uuid.UUID) -> int:
    """Proximo `sort_order` al final de la lista.

    Maximo actual mas uno, y no count: con `sort_order` libres, `count` reutiliza
    numeros y el servicio nuevo se inserta en medio de la lista sin que nadie lo
    haya pedido. El panel manda el orden completo igual, asi que el valor por
    defecto solo importa en la creacion desde la API.
    """
    result = await session.execute(
        select(func.coalesce(func.max(Service.sort_order), 0)).where(
            Service.business_id == business_id
        )
    )
    return int(result.scalar_one()) + 1


async def listar_servicios(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    incluir_archivados: bool = False,
    incluir_inactivos: bool = False,
) -> list[Service]:
    """Servicios del negocio, ordenados como los muestra el panel.

    Los archivados se excluyen por defecto. El filtro por `business_id` es
    redundante con la RLS y va igual a proposito: la RLS limita por GUC, el filtro
    limita por intencion, y si alguien llega a esta sesion sin GUC lo que quiere el
    codigo es cero filas, no todas.
    """
    query = select(Service).where(Service.business_id == business_id)
    if not incluir_archivados:
        query = query.where(Service.archived_at.is_(None))
    if not incluir_inactivos:
        query = query.where(Service.is_active.is_(True))
    result = await session.execute(query.order_by(Service.sort_order, Service.name))
    return list(result.scalars())


async def obtener_servicio(
    session: AsyncSession, service_id: uuid.UUID, *, business_id: uuid.UUID
) -> Service:
    """Un servicio del negocio, o `NotFoundError`.

    `business_id` va por parametro y no se deduce de la RLS. Un 404 para un
    servicio de otro tenant es lo correcto -- no se le confirma que existe -- pero
    para que el `business_id` llegue, el router tiene que tomarlo del principal y
    pasarlo explicitamente. Es la unica forma de que el filtro quede a la vista.
    """
    result = await session.execute(
        select(Service).where(Service.id == service_id, Service.business_id == business_id)
    )
    service = result.scalar_one_or_none()
    if service is None:
        raise NotFoundError("Servicio no encontrado.")
    return service


async def crear_servicio(
    session: AsyncSession, business_id: uuid.UUID, datos: ServiceCreate
) -> Service:
    """Crea un servicio y devuelve el registro ya persistido.

    `flush()` y no `commit()`: el commit es de la dependencia HTTP. Lo mismo que en
    el resto del servicio, pero aqui importa mas porque el `id` se devuelve en la
    respuesta y sin `flush` no habria id.
    """
    nombre = _validar_nombre(datos.name)
    if datos.duration_minutes <= 0:
        raise ValidationError("La duracion debe ser mayor a cero minutos.")
    if datos.price < 0:
        raise ValidationError("El precio no puede ser negativo.")

    service = Service(
        business_id=business_id,
        name=nombre,
        description=datos.description,
        duration_minutes=datos.duration_minutes,
        price=datos.price,
        currency=datos.currency.strip().upper(),
        color=datos.color,
        is_active=True,
        sort_order=datos.sort_order or await _siguiente_sort_order(session, business_id),
    )
    session.add(service)
    await session.flush()
    return service


async def actualizar_servicio(
    session: AsyncSession,
    service_id: uuid.UUID,
    business_id: uuid.UUID,
    cambios: ServiceUpdate,
) -> Service:
    """Aplica cambios parciales. Los campos `None` se dejan como estan."""
    service = await obtener_servicio(session, service_id, business_id=business_id)

    if cambios.name is not None:
        service.name = _validar_nombre(cambios.name)
    if cambios.description is not None:
        service.description = cambios.description
    if cambios.color is not None:
        service.color = cambios.color
    if cambios.is_active is not None:
        service.is_active = cambios.is_active
    if cambios.sort_order is not None:
        service.sort_order = cambios.sort_order
    if cambios.currency is not None:
        service.currency = cambios.currency.strip().upper()
    if cambios.duration_minutes is not None:
        if cambios.duration_minutes <= 0:
            raise ValidationError("La duracion debe ser mayor a cero minutos.")
        # La duracion no se cambia de Reservas hechas: `bookings.duration_minutes`
        # es un snapshot y el `bookings.ends_at` ya esta calculado. Cambiar el
        # servicio no puede reescribir el pasado; el admin decide reprogramar.
        service.duration_minutes = cambios.duration_minutes
    if cambios.price is not None:
        if cambios.price < 0:
            raise ValidationError("El precio no puede ser negativo.")
        service.price = cambios.price

    await session.flush()
    return service


async def archivar_servicio(
    session: AsyncSession, service_id: uuid.UUID, business_id: uuid.UUID
) -> Service:
    """Archiva un servicio. Idempotente.

    Archivar un servicio ya archivado no es error: es el resultado que el cliente
    quiere y repetir el click no deberia cambiar la respuesta.
    """
    service = await obtener_servicio(session, service_id, business_id=business_id)
    if service.archived_at is None:
        service.archived_at = now()
        # Desactivar ademas es parte del archivado. Un servicio archivado que queda
        # `is_active=True` desaparece del panel y sigue vivo en la API: el
        # cliente ve que lo borro y sigue apareciendo en un listado.
        service.is_active = False
        await session.flush()
    return service


__all__ = [
    "MAX_DESCRIPTION_LENGTH",
    "MAX_NAME_LENGTH",
    "OCUPYING_STATUSES",
    "ServiceCreate",
    "ServiceUpdate",
    "actualizar_servicio",
    "archivar_servicio",
    "crear_servicio",
    "listar_servicios",
    "obtener_servicio",
]
