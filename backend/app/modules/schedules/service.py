"""Servicio de horarios, feriados, ausencias y bloqueos.

Cubre las cinco tablas que definen cuando se puede reservar, y comparte el mismo
principio: **escriben conjuntos completos, no deltas.** El frontend manda "este es
el horario de la semana", el servicio reemplaza. La razon es que el estado desired
de un horario es una cuadricula, y un endpoint que agrega y quita filas obliga al
cliente a conocer el estado actual para calcular el diff. En un formulario con
guardar, un fallo de red en el medio deja la semana a medias y el admin no lo ve
hasta que un cliente no encuentra un horario.

Dos invariantes del modelo que el servicio tiene que respetar porque la base los
impone y no son negociables:

- `BusinessHour` y `ProfessionalSchedule` son **unicos por
  `(weekday, window_index)`**. Por eso el reemplazo borra y reescribe en vez de
  hacer upsert: la clave natural es la que define el orden de las ventanas y un
  delta la desordena.
- `weekday` va de **0=lunes a 6=domingo** (`WEEKDAY_MIN`/`WEEKDAY_MAX`), no de 0
  a 6 como el ISO de Python (`weekday()` da 0=lunes, pero `isoweekday()` da
  1=lunes). El servicio normaliza desde el nombre del dia para que el frontend no
  pueda mandar "Monday" y el servicio no lo guarde como 0 por error.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import NotFoundError, ValidationError
from app.modules.professionals.models import Professional
from app.modules.schedules.models import (
    WEEKDAY_MAX,
    WEEKDAY_MIN,
    Block,
    BusinessException,
    BusinessExceptionWindow,
    BusinessHour,
    Holiday,
    ProfessionalSchedule,
    TimeOff,
)

#: Nombres de dia en el idioma del panel. Se mapean a los indices del modelo
#: (0=lunes). El indice es el que va a la base; el nombre es lo que viaja por la
#: API porque un entero "3" en un JSON no dice si es miercoles o jueves.
DIAS_SEMANA: dict[str, int] = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}

DIAS_SEMANA_INVERSO: dict[int, str] = {v: k for k, v in DIAS_SEMANA.items()}


def _weekday_a_nombre(weekday: int) -> str:
    return DIAS_SEMANA_INVERSO.get(weekday, str(weekday))


def _validar_ventana(inicio: dt.time, fin: dt.time) -> tuple[dt.time, dt.time]:
    """Valida que una ventana tenga sentido. Devuelve `(inicio, fin)`."""
    if fin <= inicio:
        raise ValidationError("La ventana debe terminar despues de empezar.")
    return inicio, fin


def _validar_weekday(weekday: int) -> int:
    if not WEEKDAY_MIN <= weekday <= WEEKDAY_MAX:
        raise ValidationError(f"El dia de semana debe estar entre {WEEKDAY_MIN} y {WEEKDAY_MAX}.")
    return weekday


def _validar_sin_solapadas(ventanas: Sequence[Ventana]) -> None:
    """Dos ventanas del mismo dia no pueden pisarse.

    La base no lo restringe porque el orden de las ventanas lo define
    `window_index`: que dos de la misma semana se pisen es un error de
    la peticion, no de la escritura. El esquema del router ya lo
    rechaza; el servicio lo repite porque es la garantia para todo lo
    que no sea ese router (un worker, un import de datos, un test).
    """
    ordenadas = sorted(ventanas, key=lambda v: v.start)
    for anterior, siguiente in pairwise(ordenadas):
        if siguiente.start < anterior.end:
            raise ValidationError(
                f"Las ventanas {anterior.start}-{anterior.end} y "
                f"{siguiente.start}-{siguiente.end} del mismo dia se pisan."
            )


# --------------------------------------------------------------------------- #
# Horarios del negocio (BusinessHour)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Ventana:
    """Una ventana de horario: un tramo que abre y uno que cierra."""

    start: dt.time
    end: dt.time


@dataclass(slots=True)
class HorarioDia:
    """El horario completo de un dia: varias ventanas, en orden."""

    weekday: int
    #: Ventanas ordenadas por `start`. Sin lista vacia si el dia esta cerrado: un
    #: dia sin ventanas es un dia cerrado.
    windows: list[Ventana] = field(default_factory=list)


async def listar_horarios(session: AsyncSession, business_id: uuid.UUID) -> list[BusinessHour]:
    """Las ventanas de `business_hours` del negocio.

    Se devuelven filas crudas y las ordena el servicio para que el router no
    tenga que saber el `window_index`. Sin las ventanas de un dia, ese dia esta
    cerrado -- no hay una fila "cerrado", la ausencia es el cerrado.
    """
    result = await session.execute(
        select(BusinessHour)
        .where(BusinessHour.business_id == business_id)
        .order_by(BusinessHour.weekday, BusinessHour.window_index)
    )
    return list(result.scalars())


def agrupar_horarios_por_dia(horarios: Sequence[BusinessHour]) -> list[HorarioDia]:
    """Convierte filas en una semana completa de 7 dias.

    Siempre devuelve los 7 dias, con listas vacias para los que no tienen ventanas.
    Devolver solo los dias con filas obliga al frontend aumerical el resto, y cada
    cliente lo hace distinto: uno muestra los 7, otro esconde los vacios, otro
    rompe la semana en "habilitados" y "cerrados" en el orden que quiera. El
    backend entrega la semana completa y el frontend decide como la muestra.
    """
    por_dia: dict[int, list[Ventana]] = {}
    for fila in horarios:
        if fila.end_time is None:
            # `is_open=False`: una ventana cerrada explicitamente. No aporta nada
            # a la disponibilidad, asi que no entra en la semana; la semantica
            # util es "este dia no tiene horas".
            continue
        por_dia.setdefault(fila.weekday, []).append(Ventana(fila.start_time, fila.end_time))

    resultado: list[HorarioDia] = []
    for weekday in range(WEEKDAY_MIN, WEEKDAY_MAX + 1):
        ventanas = sorted(por_dia.get(weekday, []), key=lambda v: v.start)
        resultado.append(HorarioDia(weekday=weekday, windows=ventanas))
    return resultado


async def reemplazar_horarios(
    session: AsyncSession, business_id: uuid.UUID, semana: Sequence[HorarioDia]
) -> list[BusinessHour]:
    """Reemplaza la semana de horarios del negocio.

    Borra todas las ventanas y reinserta las que llegan. Es atomico: si una ventana
    de la lista esta mal, se levanta antes del borrado, asi que el negocio nunca
    queda sin horarios por un error de validacion en el pedido. Por eso la
    validacion completa va **antes** del `DELETE`.
    """
    for dia in semana:
        _validar_weekday(dia.weekday)
        for ventana in dia.windows:
            _validar_ventana(ventana.start, ventana.end)
        _validar_sin_solapadas(dia.windows)

    await session.execute(delete(BusinessHour).where(BusinessHour.business_id == business_id))
    await session.flush()

    filas: list[BusinessHour] = []
    for dia in semana:
        for indice, ventana in enumerate(sorted(dia.windows, key=lambda v: v.start)):
            fila = BusinessHour(
                business_id=business_id,
                weekday=dia.weekday,
                window_index=indice,
                start_time=ventana.start,
                end_time=ventana.end,
                is_open=True,
            )
            session.add(fila)
            filas.append(fila)
    await session.flush()
    return filas


# --------------------------------------------------------------------------- #
# Horarios del profesional (ProfessionalSchedule)
# --------------------------------------------------------------------------- #


async def listar_horarios_profesional(
    session: AsyncSession, professional_id: uuid.UUID, business_id: uuid.UUID
) -> list[ProfessionalSchedule]:
    """Las ventanas propias del profesional.

    Un profesional sin filas propias **hereda** el horario del negocio (ADR-0005,
    `is_override=false`). Por eso la ausencia de filas es un estado valido y
    significante, no un dato faltante.
    """
    await _verificar_profesional(session, professional_id, business_id)
    result = await session.execute(
        select(ProfessionalSchedule)
        .where(
            ProfessionalSchedule.professional_id == professional_id,
            ProfessionalSchedule.business_id == business_id,
        )
        .order_by(ProfessionalSchedule.weekday, ProfessionalSchedule.window_index)
    )
    return list(result.scalars())


async def reemplazar_horarios_profesional(
    session: AsyncSession,
    professional_id: uuid.UUID,
    business_id: uuid.UUID,
    semana: Sequence[HorarioDia],
    *,
    heredar: bool = False,
) -> list[ProfessionalSchedule]:
    """Reemplaza los horarios propios del profesional.

    `heredar=True` borra las filas propias y deja que el profesional siga el
    horario del negocio. Es el interruptor "no tengo horario propio", y se
    representa como cero filas, no como un flag: un flag "heredar" que convive
    con filas propias tiene tres estados posibles y solo dos son coherentes.
    """
    await _verificar_profesional(session, professional_id, business_id)
    for dia in semana:
        _validar_weekday(dia.weekday)
        for ventana in dia.windows:
            _validar_ventana(ventana.start, ventana.end)
        _validar_sin_solapadas(dia.windows)

    if heredar:
        await session.execute(
            delete(ProfessionalSchedule).where(
                ProfessionalSchedule.professional_id == professional_id,
                ProfessionalSchedule.business_id == business_id,
            )
        )
        await session.flush()
        return []

    await session.execute(
        delete(ProfessionalSchedule).where(
            ProfessionalSchedule.professional_id == professional_id,
            ProfessionalSchedule.business_id == business_id,
        )
    )
    await session.flush()

    filas: list[ProfessionalSchedule] = []
    for dia in semana:
        for indice, ventana in enumerate(sorted(dia.windows, key=lambda v: v.start)):
            fila = ProfessionalSchedule(
                professional_id=professional_id,
                business_id=business_id,
                weekday=dia.weekday,
                window_index=indice,
                start_time=ventana.start,
                end_time=ventana.end,
                is_override=True,
            )
            session.add(fila)
            filas.append(fila)
    await session.flush()
    return filas


async def _verificar_profesional(
    session: AsyncSession, professional_id: uuid.UUID, business_id: uuid.UUID
) -> Professional:
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


# --------------------------------------------------------------------------- #
# Excepciones de dia (BusinessException) y feriados (Holiday)
# --------------------------------------------------------------------------- #


async def listar_excepciones(
    session: AsyncSession, business_id: uuid.UUID
) -> list[BusinessException]:
    """Excepciones del negocio (feriados, cierres puntuales, horarios especiales)."""
    result = await session.execute(
        select(BusinessException)
        .where(BusinessException.business_id == business_id)
        .order_by(BusinessException.local_date)
    )
    return list(result.scalars())


async def crear_excepcion(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    local_date: dt.date,
    is_closed: bool,
    reason: str | None = None,
    windows: Sequence[Ventana] = (),
) -> BusinessException:
    """Crea una excepcion para un dia.

    Una excepcion **reemplaza** los horarios normales de ese dia: si tiene
    ventanas, el negocio abre solo esas; si `is_closed`, no abre. Por eso
    `windows` e `is_closed=True` juntos son una contradiccion y se rechaza.
    """
    excepcion = BusinessException(
        business_id=business_id,
        local_date=local_date,
        is_closed=is_closed,
        reason=reason,
    )
    session.add(excepcion)
    await session.flush()

    if not is_closed:
        for indice, ventana in enumerate(sorted(windows, key=lambda v: v.start)):
            _validar_ventana(ventana.start, ventana.end)
            session.add(
                BusinessExceptionWindow(
                    business_id=business_id,
                    exception_id=excepcion.id,
                    window_index=indice,
                    start_time=ventana.start,
                    end_time=ventana.end,
                )
            )
    elif windows:
        raise ValidationError("Una excepcion cerrada no puede tener ventanas de horario.")

    await session.flush()
    return excepcion


async def eliminar_excepcion(
    session: AsyncSession, business_id: uuid.UUID, exception_id: uuid.UUID
) -> None:
    """Borra una excepcion. Idempotente.

    Las ventanas de la excepcion caen por `CASCADE` de la FK
    `(exception_id, business_id)`.
    """
    await session.execute(
        delete(BusinessExceptionWindow).where(
            BusinessExceptionWindow.business_id == business_id,
            BusinessExceptionWindow.exception_id == exception_id,
        )
    )
    await session.execute(
        delete(BusinessException).where(
            BusinessException.business_id == business_id,
            BusinessException.id == exception_id,
        )
    )
    await session.flush()


async def listar_feriados(session: AsyncSession, business_id: uuid.UUID) -> list[Holiday]:
    """Feriados del negocio (dias no laborables cerrados)."""
    result = await session.execute(
        select(Holiday).where(Holiday.business_id == business_id).order_by(Holiday.local_date)
    )
    return list(result.scalars())


async def crear_feriado(
    session: AsyncSession, business_id: uuid.UUID, *, local_date: dt.date, name: str
) -> Holiday:
    """Crea un feriado.

    El nombre es obligatorio y no opcional: un feriado sin nombre muestra un dia
    tachado en el panel y el admin no sabe que esta cerrado. Es el dato que explica
    el cierre al que lo mira dos meses despues.
    """
    limpio = name.strip()
    if not limpio:
        raise ValidationError("El feriado necesita un nombre.")
    feriado = Holiday(business_id=business_id, local_date=local_date, name=limpio)
    session.add(feriado)
    await session.flush()
    return feriado


async def eliminar_feriado(
    session: AsyncSession, business_id: uuid.UUID, holiday_id: uuid.UUID
) -> None:
    """Borra un feriado. Idempotente."""
    await session.execute(
        delete(Holiday).where(
            Holiday.business_id == business_id,
            Holiday.id == holiday_id,
        )
    )
    await session.flush()


# --------------------------------------------------------------------------- #
# Ausencias (TimeOff) y bloqueos (Block)
# --------------------------------------------------------------------------- #


async def listar_ausencias(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    professional_id: uuid.UUID | None = None,
) -> list[TimeOff]:
    """Vacaciones y ausencias. Sin filtro, devuelve las de todo el negocio."""
    query = select(TimeOff).where(TimeOff.business_id == business_id)
    if professional_id is not None:
        query = query.where(TimeOff.professional_id == professional_id)
    result = await session.execute(query.order_by(TimeOff.starts_at))
    return list(result.scalars())


async def crear_ausencia(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    professional_id: uuid.UUID,
    starts_at: dt.datetime,
    ends_at: dt.datetime,
    kind: Literal["vacation", "leave", "sick", "absence"],
    status: Literal["pending", "approved", "rejected"] = "approved",
    reason: str | None = None,
) -> TimeOff:
    """Crea una ausencia o vacaciones.

    `status` arranca en `approved` por defecto: la ausencia creada desde el panel
    admin es una decision ya tomada. El `pending` existe para cuando la reporta el
    propio profesional y tiene que aprobarla alguien.
    """
    if ends_at <= starts_at:
        raise ValidationError("La ausencia debe terminar despues de empezar.")
    await _verificar_profesional(session, professional_id, business_id)
    ausencia = TimeOff(
        business_id=business_id,
        professional_id=professional_id,
        kind=kind,
        status=status,
        starts_at=starts_at,
        ends_at=ends_at,
        reason=reason,
    )
    session.add(ausencia)
    await session.flush()
    return ausencia


async def eliminar_ausencia(
    session: AsyncSession, business_id: uuid.UUID, time_off_id: uuid.UUID
) -> None:
    await session.execute(
        delete(TimeOff).where(
            TimeOff.business_id == business_id,
            TimeOff.id == time_off_id,
        )
    )
    await session.flush()


async def listar_bloqueos(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    professional_id: uuid.UUID | None = None,
) -> list[Block]:
    """Bloqueos de calendario. Un bloqueo con `professional_id=None` es de negocio."""
    query = select(Block).where(Block.business_id == business_id)
    if professional_id is not None:
        query = query.where(Block.professional_id == professional_id)
    result = await session.execute(query.order_by(Block.starts_at))
    return list(result.scalars())


async def crear_bloqueo(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    starts_at: dt.datetime,
    ends_at: dt.datetime,
    kind: Literal["unpaid", "private", "blocked"],
    professional_id: uuid.UUID | None = None,
    reason: str | None = None,
    created_by_user_id: uuid.UUID | None = None,
) -> Block:
    """Crea un bloqueo de calendario.

    `professional_id=None` bloquea a todo el negocio. `occupied_from`/`occupied_to`
    replican el rango con padding para que el indice `ix_blocks_occupied_range`
    sirva; se dejan iguales a `starts_at`/`ends_at` porque el padding real lo
    calcula el motor de disponibilidad, no el alta del bloqueo.
    """
    if ends_at <= starts_at:
        raise ValidationError("El bloqueo debe terminar despues de empezar.")
    if professional_id is not None:
        await _verificar_profesional(session, professional_id, business_id)
    bloqueo = Block(
        business_id=business_id,
        professional_id=professional_id,
        kind=kind,
        starts_at=starts_at,
        ends_at=ends_at,
        occupied_from=starts_at,
        occupied_to=ends_at,
        reason=reason,
        created_by_user_id=created_by_user_id,
    )
    session.add(bloqueo)
    await session.flush()
    return bloqueo


async def eliminar_bloqueo(
    session: AsyncSession, business_id: uuid.UUID, block_id: uuid.UUID
) -> None:
    await session.execute(
        delete(Block).where(Block.business_id == business_id, Block.id == block_id)
    )
    await session.flush()


__all__ = [
    "DIAS_SEMANA",
    "DIAS_SEMANA_INVERSO",
    "HorarioDia",
    "Ventana",
    "_weekday_a_nombre",
    "agrupar_horarios_por_dia",
    "crear_ausencia",
    "crear_bloqueo",
    "crear_excepcion",
    "crear_feriado",
    "eliminar_ausencia",
    "eliminar_bloqueo",
    "eliminar_excepcion",
    "eliminar_feriado",
    "listar_ausencias",
    "listar_bloqueos",
    "listar_excepciones",
    "listar_feriados",
    "listar_horarios",
    "listar_horarios_profesional",
    "reemplazar_horarios",
    "reemplazar_horarios_profesional",
]
