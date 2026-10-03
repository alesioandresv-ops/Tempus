"""Esquemas de entrada y salida del panel del negocio.

Tres reglas que hacen que estos esquemas no sean solo "el modelo con `Optional`":

1. **`extra="forbid"` en todo lo que llega del cliente.** Un typo en el nombre de
   un campo (`"duracion_minutes"` en vez de `"duration_minutes"`) con la
   configuracion por defecto de Pydantic se ignora en silencio: la peticion vuelve
   200 y el campo nunca se guarda. Prohibir los extras convierte el typo en un 422
   con el nombre del campo, que es justo el error que se puede corregir.

2. **`None` significa "no lo toques", no "borralo".** Es lo que hace que un `PATCH`
   del panel pueda mandar solo los campos que el usuario toco. Borrar un campo es
   una accion distinta, porque es destructiva y no deberia ser el efecto
   secundario de editar otra cosa.

3. **Los `field_validator` repiten lo que valida el servicio, y esta bien.** El
   servicio valida porque es la garantia; el esquema valida porque es la
   respuesta rapida y el mensaje util. Que esten los dos no es duplicacion
   inutil: si alguien llama al servicio desde un worker, desde un import de datos o
   desde un test, la validacion sigue estando.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from itertools import pairwise

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    BlockKind,
    BookingEventType,
    BookingSource,
    BookingStatus,
    TimeOffKind,
    TimeOffStatus,
)

# --------------------------------------------------------------------------- #
# Negocio
# --------------------------------------------------------------------------- #


class NegocioPatch(BaseModel):
    """Cambios parciales de la configuracion del negocio."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2048)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    locale: str | None = Field(default=None, min_length=2, max_length=16)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    phone_e164: str | None = Field(default=None, min_length=8, max_length=20)
    address_text: str | None = Field(default=None, max_length=512)
    brand_color: str | None = Field(default=None, max_length=32)
    slot_interval_minutes: int | None = Field(default=None, ge=5, le=240)
    min_lead_minutes: int | None = Field(default=None, ge=0, le=720)
    max_advance_days: int | None = Field(default=None, ge=1, le=365)
    cancellation_window_minutes: int | None = Field(default=None, ge=0, le=10080)
    slug: str | None = Field(default=None, min_length=3, max_length=48)

    @field_validator("name")
    @classmethod
    def _nombre(cls, v: str | None) -> str | None:
        if v is None:
            return v
        limpio = v.strip()
        if not limpio:
            raise ValueError("el nombre no puede quedar vacio despues de espacios")
        return limpio

    @field_validator("currency")
    @classmethod
    def _moneda(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return v.strip().upper()


class NegocioOut(BaseModel):
    """Configuracion del negocio, tal como la ve su administrador."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    timezone: str
    locale: str
    currency: str
    phone_e164: str | None
    address_text: str | None
    brand_color: str | None
    slot_interval_minutes: int
    min_lead_minutes: int
    max_advance_days: int
    cancellation_window_minutes: int
    status: str
    created_at: dt.datetime


class SlugDisponibleOut(BaseModel):
    """Respuesta del chequeo de slug, para el selector del onboarding."""

    slug: str
    disponible: bool


# --------------------------------------------------------------------------- #
# Servicios
# --------------------------------------------------------------------------- #


class ServicioCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1024)
    duration_minutes: int = Field(ge=5, le=480)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    color: str | None = Field(default=None, max_length=32)
    sort_order: int | None = Field(default=None, ge=0, le=10_000)

    @field_validator("currency")
    @classmethod
    def _moneda(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("price")
    @classmethod
    def _precio(cls, v: Decimal) -> Decimal:
        # Sin esto, un `Decimal` de la base como `Decimal('30.5')` y el que manda el
        # cliente como `Decimal('30.50')` se guardan distinto y el panel ve un
        # cambio de precio que no existio.
        return v.quantize(Decimal("0.01"))


class ServicioPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1024)
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    color: str | None = Field(default=None, max_length=32)
    is_active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)

    @field_validator("currency")
    @classmethod
    def _moneda(cls, v: str | None) -> str | None:
        return v.strip().upper() if v is not None else None

    @field_validator("price")
    @classmethod
    def _precio(cls, v: Decimal | None) -> Decimal | None:
        return v.quantize(Decimal("0.01")) if v is not None else None


class ServicioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    duration_minutes: int
    price: Decimal
    currency: str
    color: str | None
    is_active: bool
    sort_order: int
    archived_at: dt.datetime | None


# --------------------------------------------------------------------------- #
# Profesionales
# --------------------------------------------------------------------------- #


class ProfesionalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=160)
    bio: str | None = Field(default=None, max_length=2048)
    color: str | None = Field(default=None, max_length=32)
    sort_order: int | None = Field(default=None, ge=0, le=10_000)


class ProfesionalPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    bio: str | None = Field(default=None, max_length=2048)
    color: str | None = Field(default=None, max_length=32)
    is_active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)


class ProfesionalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    bio: str | None
    color: str | None
    is_active: bool
    sort_order: int
    archived_at: dt.datetime | None


class AsignacionServicioIn(BaseModel):
    """Un servicio que puede hacer un profesional, con overrides opcionales."""

    model_config = ConfigDict(extra="forbid")

    service_id: uuid.UUID
    custom_duration_minutes: int | None = Field(default=None, ge=5, le=480)
    custom_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)

    @field_validator("custom_price")
    @classmethod
    def _precio(cls, v: Decimal | None) -> Decimal | None:
        return v.quantize(Decimal("0.01")) if v is not None else None


class AsignacionesIn(BaseModel):
    """Conjunto completo de servicios de un profesional. Reemplaza, no agrega."""

    model_config = ConfigDict(extra="forbid")

    servicios: list[AsignacionServicioIn] = Field(default_factory=list, max_length=200)


class AsignacionServicioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service_id: uuid.UUID
    is_active: bool
    custom_duration_minutes: int | None
    custom_price: Decimal | None


# --------------------------------------------------------------------------- #
# Horarios
# --------------------------------------------------------------------------- #


class VentanaIn(BaseModel):
    """Una ventana de horario dentro de un dia."""

    model_config = ConfigDict(extra="forbid")

    start: dt.time
    end: dt.time

    @field_validator("end")
    @classmethod
    def _termina_despues(cls, v: dt.time, info) -> dt.time:
        inicio = info.data.get("start")
        if inicio is not None and v <= inicio:
            raise ValueError("la ventana tiene que terminar despues de empezar")
        return v


class DiaHorarioIn(BaseModel):
    """El horario de un dia: cero o mas ventanas.

    Un dia sin ventanas es un dia cerrado. No hay un `abierto: bool`: la ausencia de
    ventanas ya lo dice, y un flag mas es un segundo lugar donde la respuesta puede
    ser contradictoria (`abierto=true` con cero ventanas).
    """

    model_config = ConfigDict(extra="forbid")

    #: 0 = lunes ... 6 = domingo. El mismo indice que usa la base, y no la
    #: enumeracion de Python, que no coincide.
    weekday: int = Field(ge=0, le=6)
    windows: list[VentanaIn] = Field(default_factory=list, max_length=12)

    @field_validator("windows")
    @classmethod
    def _sin_solapadas(cls, v: list[VentanaIn]) -> list[VentanaIn]:
        """Rechaza ventanas que se pisan.

        Sin este chequeo el backend acepta `09:00-13:00` y `11:00-15:00` y guarda dos
        filas que, leidas de vuelta, producen una semana con huecos imposibles. La
        base no lo restringe porque el orden de las ventanas lo define
        `window_index`; que dos de ellas se pisen es un error de la peticion.
        """
        ordenadas = sorted(v, key=lambda w: w.start)
        for anterior, siguiente in pairwise(ordenadas):
            if siguiente.start < anterior.end:
                raise ValueError(
                    f"las ventanas {anterior.start}-{anterior.end} y "
                    f"{siguiente.start}-{siguiente.end} se pisan"
                )
        return ordenadas


class SemanaHorarioIn(BaseModel):
    """La semana completa. Reemplaza todo lo que hubiera."""

    model_config = ConfigDict(extra="forbid")

    dias: list[DiaHorarioIn] = Field(default_factory=list, max_length=7)

    @field_validator("dias")
    @classmethod
    def _sin_dias_repetidos(cls, v: list[DiaHorarioIn]) -> list[DiaHorarioIn]:
        vistos = [d.weekday for d in v]
        if len(set(vistos)) != len(vistos):
            raise ValueError("hay dias repetidos en la semana")
        return v


class VentanaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start: dt.time
    end: dt.time


class DiaHorarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    weekday: int
    windows: list[VentanaOut]


class SemanaHorarioOut(BaseModel):
    dias: list[DiaHorarioOut]


class HorarioProfesionalOut(BaseModel):
    """El horario propio del profesional y si esta heredando el del negocio.

    `hereda` viaja explicito ademas del estado de las ventanas. El frontend no puede
    deducirlo de "no hay ventanas": cero ventanas tambien es un horario valido --
    un profesional que no atiende ningun dia. Sin el campo, el panel no puede
    distinguir "no tengo horario propio" de "atiendo cero horas".
    """

    hereda: bool
    dias: list[DiaHorarioOut]


# --------------------------------------------------------------------------- #
# Excepciones, feriados, ausencias y bloqueos
# --------------------------------------------------------------------------- #


class ExcepcionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_date: dt.date
    is_closed: bool
    reason: str | None = Field(default=None, max_length=200)
    windows: list[VentanaIn] = Field(default_factory=list, max_length=12)

    @field_validator("windows")
    @classmethod
    def _coherente(cls, v: list[VentanaIn], info) -> list[VentanaIn]:
        if info.data.get("is_closed") is True and v:
            raise ValueError("un dia cerrado no puede tener ventanas de horario")
        return sorted(v, key=lambda w: w.start)


class ExcepcionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    local_date: dt.date
    is_closed: bool
    reason: str | None


class FeriadoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_date: dt.date
    name: str = Field(min_length=1, max_length=200)


class FeriadoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    local_date: dt.date
    name: str


class AusenciaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    professional_id: uuid.UUID
    kind: TimeOffKind = TimeOffKind.VACATION
    status: TimeOffStatus = TimeOffStatus.APPROVED
    starts_at: dt.datetime
    ends_at: dt.datetime
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("ends_at")
    @classmethod
    def _termina_despues(cls, v: dt.datetime, info) -> dt.datetime:
        inicio = info.data.get("starts_at")
        if inicio is not None and v <= inicio:
            raise ValueError("la ausencia tiene que terminar despues de empezar")
        return v


class AusenciaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    professional_id: uuid.UUID
    kind: TimeOffKind
    status: TimeOffStatus
    starts_at: dt.datetime
    ends_at: dt.datetime
    reason: str | None


class BloqueoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: dt.datetime
    ends_at: dt.datetime
    kind: BlockKind = BlockKind.BLOCKED
    #: `None` bloquea a todo el negocio, no a un profesional.
    professional_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("ends_at")
    @classmethod
    def _termina_despues(cls, v: dt.datetime, info) -> dt.datetime:
        inicio = info.data.get("starts_at")
        if inicio is not None and v <= inicio:
            raise ValueError("el bloqueo tiene que terminar despues de empezar")
        return v


class BloqueoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    professional_id: uuid.UUID | None
    kind: BlockKind
    starts_at: dt.datetime
    ends_at: dt.datetime
    reason: str | None


# --------------------------------------------------------------------------- #
# Reservas
# --------------------------------------------------------------------------- #


class ReservaOut(BaseModel):
    """Una reserva para el listado del panel."""

    id: uuid.UUID
    status: BookingStatus
    source: BookingSource
    starts_at: dt.datetime
    ends_at: dt.datetime
    local_date: dt.date
    duration_minutes: int
    price_snapshot: Decimal
    currency: str
    service_id: uuid.UUID
    professional_id: uuid.UUID
    customer_id: uuid.UUID
    notes: str | None
    cancel_reason: str | None
    cancelled_at: dt.datetime | None
    created_at: dt.datetime
    # Nombres desnormalizados para pintar la fila sin una consulta por reserva.
    servicio_nombre: str | None = None
    profesional_nombre: str | None = None
    cliente_nombre: str | None = None
    cliente_telefono: str | None = None


class ReservaPagina(BaseModel):
    """Pagina de reservas con el total, para que el panel pueda paginar de verdad."""

    items: list[ReservaOut]
    total: int
    limite: int
    offset: int


class CancelarDesdePanelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str | None = Field(default=None, max_length=500)


class EstadoFinalIn(BaseModel):
    """Cierre de turno. Solo `completed` y `no_show` son validos."""

    model_config = ConfigDict(extra="forbid")

    estado: BookingStatus

    @field_validator("estado")
    @classmethod
    def _solo_cierres(cls, v: BookingStatus) -> BookingStatus:
        if v not in (BookingStatus.COMPLETED, BookingStatus.NO_SHOW):
            raise ValueError("solo se admiten 'completed' o 'no_show'")
        return v


class WalkinIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: uuid.UUID
    professional_id: uuid.UUID | None = None
    customer_first_name: str = Field(min_length=1, max_length=160)
    customer_last_name: str = Field(default="", max_length=160)
    customer_phone_e164: str = Field(min_length=8, max_length=20)
    starts_at: dt.datetime
    notas: str | None = Field(default=None, max_length=1000)


class EventoReservaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: BookingEventType
    actor_type: str
    from_starts_at: dt.datetime | None
    to_starts_at: dt.datetime | None
    payload: dict | None
    created_at: dt.datetime


# --------------------------------------------------------------------------- #
# Clientes
# --------------------------------------------------------------------------- #


class ClientePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, min_length=1, max_length=160)
    last_name: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=4096)
    is_opted_out: bool | None = None
    marketing_opt_in: bool | None = None


class ClienteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str | None
    phone_e164: str
    notes: str | None
    is_opted_out: bool
    marketing_opt_in: bool
    created_at: dt.datetime


class ClienteListadoOut(ClienteOut):
    """Cliente con su historial agregado.

    `total_reservas` y `ultima_reserva` son agregados sobre `bookings` hechos en la
    misma consulta, no columnas: duplicarlos en `customers` seria una segunda fuente
    de verdad que se desincroniza en la primera cancelacion.
    """

    total_reservas: int
    ultima_reserva: dt.datetime | None


__all__ = [
    "AsignacionServicioIn",
    "AsignacionServicioOut",
    "AsignacionesIn",
    "AusenciaIn",
    "AusenciaOut",
    "BloqueoIn",
    "BloqueoOut",
    "CancelarDesdePanelIn",
    "ClienteListadoOut",
    "ClienteOut",
    "ClientePatch",
    "DiaHorarioIn",
    "DiaHorarioOut",
    "EstadoFinalIn",
    "EventoReservaOut",
    "ExcepcionIn",
    "ExcepcionOut",
    "FeriadoIn",
    "FeriadoOut",
    "HorarioProfesionalOut",
    "NegocioOut",
    "NegocioPatch",
    "ProfesionalCreate",
    "ProfesionalOut",
    "ProfesionalPatch",
    "ReservaOut",
    "ReservaPagina",
    "SemanaHorarioIn",
    "SemanaHorarioOut",
    "ServicioCreate",
    "ServicioOut",
    "ServicioPatch",
    "SlugDisponibleOut",
    "VentanaIn",
    "VentanaOut",
    "WalkinIn",
]
