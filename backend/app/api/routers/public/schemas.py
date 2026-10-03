"""Schemas Pydantic para el router público."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class BusinessPublicInfo(BaseModel):
    """Información pública de un negocio."""

    id: UUID
    name: str
    description: str | None = None
    timezone: str
    locale: str
    currency: str
    slot_interval_minutes: int
    min_lead_minutes: int
    brand_color: str | None = None


class ServicePublicInfo(BaseModel):
    """Servicio público de un negocio."""

    id: UUID
    name: str
    description: str | None = None
    duration_minutes: int
    price: Decimal
    currency: str


class ProfessionalPublicInfo(BaseModel):
    """Profesional público de un negocio."""

    id: UUID
    display_name: str
    bio: str | None = None
    color: str | None = None


class AvailabilityRequest(BaseModel):
    """Request para calcular disponibilidad."""

    service_id: UUID
    professional_id: UUID | None = Field(
        default=None,
        description="ID del profesional, o null para 'cualquier profesional'",
    )
    date: dt.date = Field(description="Fecha local del negocio")


class AvailabilitySlot(BaseModel):
    """Un horario offered.

    `professional_id` viaja en cada slot y no solo en la consulta, porque en
    "cualquier profesional" cada horario puede ser de una persona distinta. Si el
    backend devolviera los slots sin decir de quien son, el cliente no puede
    elegir: reservaria "las 09:00" y el backend le asignaria la primera.
    """

    starts_at: dt.datetime
    ends_at: dt.datetime
    professional_id: UUID | None = None


class AvailabilityResponse(BaseModel):
    """Response con slots disponibles."""

    slots: list[AvailabilitySlot] = Field(
        description="Lista de slots con starts_at y ends_at en ISO format",
    )


class BookingCreateRequest(BaseModel):
    """Request para crear una reserva."""

    slug: str = Field(description="Slug del negocio")
    service_id: UUID
    professional_id: UUID | None = Field(
        default=None,
        description="ID del profesional, o null para asignación automática",
    )
    customer_first_name: str
    customer_last_name: str
    customer_phone_e164: str = Field(description="Teléfono en formato E.164")
    starts_at: dt.datetime = Field(description="Inicio del servicio (UTC)")
    ends_at: dt.datetime = Field(description="Fin del servicio (UTC)")
    local_date: dt.date = Field(description="Fecha local del negocio")
    idempotency_key: str | None = Field(
        default=None,
        description="Clave de idempotencia (opcional)",
    )


class RescheduleRequest(BaseModel):
    """Request para reprogramar una reserva.

    Los tres campos van juntos porque la reprogramacion no es "mover el inicio":
    el fin se recalcula a partir de la duracion real del servicio, y mandarlos
    sueltos habilita el caso de un turno de 90 minutos con `ends_at` a 60.
    """

    new_starts_at: dt.datetime = Field(description="Nuevo inicio (UTC)")
    new_ends_at: dt.datetime = Field(description="Nuevo fin (UTC)")
    new_duration_minutes: int = Field(
        gt=0,
        description="Duracion real del servicio",
    )


class BookingResponse(BaseModel):
    """Response con información de una reserva.

    **No expone datos personales del cliente.** La página de gestión muestra
    nombre y un código, nunca email ni teléfono: el README lo fija como requisito
    de privacidad y la URL `/r/{token}` es lo que el cliente tiene, asi que
    devolver el teléfono en la respuesta lo expone a cualquiera que tenga el
    enlace.
    """

    booking_id: UUID
    secure_token: str = Field(description="Token para gestionar la reserva")
    starts_at: dt.datetime
    ends_at: dt.datetime
    professional_id: UUID
    status: str
    service_name: str | None = None
    professional_name: str | None = None
    customer_first_name: str | None = Field(
        default=None,
        description="Solo nombre. El apellido y el telefono no se exponen.",
    )
    business_name: str | None = None
    business_phone: str | None = None
    business_slug: str | None = None
    local_date: dt.date | None = None
    price: Decimal | None = None
    currency: str | None = None
    cancelled_at: dt.datetime | None = None


__all__ = [
    "AvailabilityRequest",
    "AvailabilityResponse",
    "AvailabilitySlot",
    "BookingCreateRequest",
    "BookingResponse",
    "BusinessPublicInfo",
    "ProfessionalPublicInfo",
    "RescheduleRequest",
    "ServicePublicInfo",
]
