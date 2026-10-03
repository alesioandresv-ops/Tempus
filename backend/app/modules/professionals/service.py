"""Servicio de profesionales: alta, edicion, archivado y servicios que realiza.

Dos cosas que no son obvias y por eso estan comentadas en el sitio donde se
deciden:

1. **Archivar, no borrar.** Igual que en servicios: `bookings.professional_id` tiene
   FK `RESTRICT` y `professionals.user_id` apunta a `business_users` con
   `SET NULL`. Un borrado físico de alguien que atendió deja las reservas sin autor
   o falla; el archivado deja la historia y saca a la persona de la reserva online.
   La diferencia con servicios es que aca el archivado **no** puede tocar
   `user_id`: el login sobrevive al archivado a proposito (ver abajo).

2. **`user_id` no se borra al archivar.** Un profesional que dejo de trabajar
   todavia puede necesitar entrar a ver su historia, y el `CheckConstraint`
   `login_requires_not_archived` exige que un usuario con login **no** este
   archivado. Son dos estados distintos: "no atiende mas" y "no tiene cuenta". Por
   eso archivar no toca `user_id`; desvincular la cuenta es una operacion aparte y
   explicita (`desvincular_usuario`), porque tiene una consecuencia de seguridad --
   esa persona deja de poder entrar.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import NotFoundError, ValidationError
from app.core.time import now
from app.modules.professionals.models import Professional
from app.modules.services.models import ProfessionalService, Service

MAX_NAME_LENGTH = 160
MAX_BIO_LENGTH = 2048


@dataclass(frozen=True, slots=True)
class ProfessionalCreate:
    """Datos de alta de un profesional."""

    display_name: str
    bio: str | None = None
    color: str | None = None
    sort_order: int | None = None
    user_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ProfessionalUpdate:
    """Cambios parciales (PATCH). Los `None` se ignoran."""

    display_name: str | None = None
    bio: str | None = None
    color: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


def _validar_nombre(nombre: str) -> str:
    limpio = nombre.strip()
    if not limpio:
        raise ValidationError("El nombre no puede estar vacio.")
    if len(limpio) > MAX_NAME_LENGTH:
        raise ValidationError(f"El nombre no puede pasar de {MAX_NAME_LENGTH} caracteres.")
    return limpio


async def _siguiente_sort_order(session: AsyncSession, business_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.coalesce(func.max(Professional.sort_order), 0)).where(
            Professional.business_id == business_id
        )
    )
    return int(result.scalar_one()) + 1


async def listar_profesionales(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    incluir_archivados: bool = False,
    incluir_inactivos: bool = False,
    service_id: uuid.UUID | None = None,
) -> list[Professional]:
    """Profesionales del negocio, en el orden del panel.

    `service_id` filtra por los que pueden hacer ese servicio, que es la pregunta
    que hace el selector del frontend. El filtro va sobre `professional_services`
    (no sobre una subconsulta de availability) porque la disponibilidad ademas
    depende del horario del dia: un profesional puede estar habilitado para un
    servicio y no tener ventana ese martes. Esto responde "puede", no "puede hoy".
    """
    query = select(Professional).where(Professional.business_id == business_id)
    if not incluir_archivados:
        query = query.where(Professional.archived_at.is_(None))
    if not incluir_inactivos:
        query = query.where(Professional.is_active.is_(True))
    if service_id is not None:
        query = query.join(
            ProfessionalService,
            (ProfessionalService.professional_id == Professional.id)
            & (ProfessionalService.service_id == service_id)
            & (ProfessionalService.is_active.is_(True)),
        )
    result = await session.execute(
        query.order_by(Professional.sort_order, Professional.display_name)
    )
    return list(result.scalars())


async def obtener_profesional(
    session: AsyncSession, professional_id: uuid.UUID, *, business_id: uuid.UUID
) -> Professional:
    """Un profesional del negocio, o `NotFoundError`."""
    result = await session.execute(
        select(Professional).where(
            Professional.id == professional_id,
            Professional.business_id == business_id,
        )
    )
    professional = result.scalar_one_or_none()
    if professional is None:
        raise NotFoundError("Profesional no encontrado.")
    return professional


async def crear_profesional(
    session: AsyncSession, business_id: uuid.UUID, datos: ProfessionalCreate
) -> Professional:
    """Crea un profesional."""
    professional = Professional(
        business_id=business_id,
        display_name=_validar_nombre(datos.display_name),
        bio=datos.bio,
        color=datos.color,
        user_id=datos.user_id,
        is_active=True,
        sort_order=(
            datos.sort_order
            if datos.sort_order is not None
            else await _siguiente_sort_order(session, business_id)
        ),
    )
    session.add(professional)
    await session.flush()
    return professional


async def actualizar_profesional(
    session: AsyncSession,
    professional_id: uuid.UUID,
    business_id: uuid.UUID,
    cambios: ProfessionalUpdate,
) -> Professional:
    """Aplica cambios parciales."""
    professional = await obtener_profesional(session, professional_id, business_id=business_id)
    if cambios.display_name is not None:
        professional.display_name = _validar_nombre(cambios.display_name)
    if cambios.bio is not None:
        professional.bio = cambios.bio
    if cambios.color is not None:
        professional.color = cambios.color
    if cambios.is_active is not None:
        professional.is_active = cambios.is_active
    if cambios.sort_order is not None:
        professional.sort_order = cambios.sort_order
    await session.flush()
    return professional


async def archivar_profesional(
    session: AsyncSession, professional_id: uuid.UUID, business_id: uuid.UUID
) -> Professional:
    """Archiva un profesional. Idempotente. No toca `user_id` (ver docstring)."""
    professional = await obtener_profesional(session, professional_id, business_id=business_id)
    if professional.archived_at is None:
        professional.archived_at = now()
        professional.is_active = False
        await session.flush()
    return professional


async def desvincular_usuario(
    session: AsyncSession, professional_id: uuid.UUID, business_id: uuid.UUID
) -> Professional:
    """Saca el login del profesional. Es la operacion con consecuencia de seguridad.

    A diferencia de archivar, esto si deja a la persona sin acceso: es el "dar de
    baja la cuenta" de verdad. Se separa de archivar porque son dos decisiones
    distintas y mezclar las dos haria que archivar a alguien le cerrara la cuenta
    sin avisar.
    """
    professional = await obtener_profesional(session, professional_id, business_id=business_id)
    professional.user_id = None
    await session.flush()
    return professional


async def listar_servicios_de(
    session: AsyncSession, professional_id: uuid.UUID, business_id: uuid.UUID
) -> list[ProfessionalService]:
    """Los servicios asignados a un profesional, con sus overrides.

    Devuelve las filas de `professional_services`, no los `Service`: el panel
    necesita el precio customizado cuando existe, que es justo lo que vive en esa
    tabla.
    """
    result = await session.execute(
        select(ProfessionalService)
        .where(
            ProfessionalService.professional_id == professional_id,
            ProfessionalService.business_id == business_id,
        )
        .order_by(ProfessionalService.service_id)
    )
    return list(result.scalars())


async def asignar_servicios(
    session: AsyncSession,
    professional_id: uuid.UUID,
    business_id: uuid.UUID,
    asignaciones: list[tuple[uuid.UUID, int | None, str | None]],
) -> list[ProfessionalService]:
    """Reemplaza el conjunto de servicios del profesional.

    `asignaciones` es una lista de `(service_id, custom_duration_minutes,
    custom_price)`. Es un reemplazo completo, no un delta: el frontend manda la
    lista entera que quiere y el resultado es exactamente esa lista. Un delta
    (agregar/quitar) obliga al frontend a conocer el estado actual para calcular
    el diff, y en un formulario con "guardar" asi un fallo de red deja servicios
    cambiados a medias sin que el usuario lo note.

    Los `service_id` tienen que ser del mismo negocio. La FK compuesta
    `(service_id, business_id)` lo garantiza en la base, pero se valida antes para
    que el error sea un 422 con el id en el mensaje y no un `IntegrityError` con
    el nombre de la restriccion.
    """
    await obtener_profesional(session, professional_id, business_id=business_id)

    if asignaciones:
        ids = [service_id for service_id, _, _ in asignaciones]
        if len(set(ids)) != len(ids):
            raise ValidationError("Hay servicios repetidos en la lista.")
        encontrados = await session.execute(
            select(Service.id).where(Service.id.in_(ids), Service.business_id == business_id)
        )
        validos = {row[0] for row in encontrados}
        faltantes = sorted(str(i) for i in set(ids) - validos)
        if faltantes:
            raise ValidationError(
                f"Servicios que no son del negocio: {', '.join(faltantes)}.",
                extra={"servicios_invalidos": faltantes},
            )

    # Borrar y recrear es correcto aca y mas simple que un diff: son pocas filas
    # (los servicios de un negocio son decenas, no miles) y la operacion es una
    # sola transaccion. Un upsert diferencial seria mas trabajo para la misma
    # garantia.
    await session.execute(
        ProfessionalService.__table__.delete().where(
            ProfessionalService.professional_id == professional_id,
            ProfessionalService.business_id == business_id,
        )
    )
    await session.flush()

    filas: list[ProfessionalService] = []
    for service_id, custom_duration, custom_price in asignaciones:
        fila = ProfessionalService(
            professional_id=professional_id,
            business_id=business_id,
            service_id=service_id,
            is_active=True,
            custom_duration_minutes=custom_duration,
            custom_price=custom_price,
        )
        session.add(fila)
        filas.append(fila)
    await session.flush()
    return filas


__all__ = [
    "MAX_BIO_LENGTH",
    "MAX_NAME_LENGTH",
    "ProfessionalCreate",
    "ProfessionalUpdate",
    "actualizar_profesional",
    "archivar_profesional",
    "asignar_servicios",
    "crear_profesional",
    "desvincular_usuario",
    "listar_profesionales",
    "listar_servicios_de",
    "obtener_profesional",
]
