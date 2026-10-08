"""Reportes mensuales del panel: exportacion de las reservas del mes.

Va en un archivo aparte de `admin.py` por la misma razon que `admin.py` esta
apartado de `service.py`: la forma de paginar. El listado pagina de a 50-100 y
corta en `MAX_LIMITE` (200); una exportacion **no pagina** -- un archivo que se
descarga entero no tiene paginas, y uno truncado a 200 filas presentaria el mes
incompleto sin que nadie lo note. La consulta es la misma forma que
`listar_reservas` (joins con los nombres desnormalizados) pero sin
`LIMIT`/`OFFSET`.

El CSV usa `;` como separador y BOM UTF-8, la convencion que Excel es-AR
espera: con coma, un reporte abierto en Excel argentino llega entero en la
primera columna. El XLSX es un libro de dos hojas --"Reservas" (las filas) y
"Resumen" (totales e ingresos)-- escrito con openpyxl en memoria.
"""

from __future__ import annotations

import calendar
import csv
import io
import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ValidationError
from app.models.enums import BookingStatus
from app.modules.bookings.admin import ReservaDetalle
from app.modules.bookings.models import Booking
from app.modules.businesses.models import Business
from app.modules.customers.models import Customer
from app.modules.professionals.models import Professional
from app.modules.services.models import Service

#: El encabezado del CSV y de la hoja "Reservas" del XLSX. El orden importa: es
#: el mismo en los dos archivos y el que ve quien abre la fila 1.
COLUMNAS = (
    "fecha",
    "hora",
    "cliente_nombre",
    "cliente_telefono",
    "profesional_nombre",
    "servicio_nombre",
    "precio",
    "estado",
)

#: Ingresos son lo que entro de verdad: una reserva cancelada no es dinero
#: recibido. Es el mismo criterio que `customers.service` usa para
#: `total_gastado` en el historial del cliente (Fase D-1).
ESTADOS_PAGADOS = (BookingStatus.CONFIRMED, BookingStatus.COMPLETED)

PATRON_MES = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _mes_a_dias(mes: str) -> tuple[date, date]:
    """Primer y ultimo dia del mes `YYYY-MM`, validando el formato."""
    if not PATRON_MES.match(mes):
        raise ValidationError("El mes debe tener formato YYYY-MM, por ejemplo 2026-10.")
    anio, numero = (int(p) for p in mes.split("-"))
    _, ultimo = calendar.monthrange(anio, numero)
    return date(anio, numero, 1), date(anio, numero, ultimo)


async def reservas_del_mes(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    mes: str,
    professional_id: uuid.UUID | None = None,
    estado: BookingStatus | None = None,
) -> tuple[list[ReservaDetalle], ZoneInfo]:
    """Todas las reservas del mes pedido, mas el timezone del negocio.

    La ventana va por `local_date`, como todo el panel: filtrar por `starts_at`
    en UTC dejaria afuera el turno de las 23:30 del ultimo dia del mes, que en
    Buenos Aires ya es el primero del mes siguiente.

    `professional_id` y `estado` son filtros opcionales sobre ese subconjunto.
    El timezone del negocio se devuelve junto con las filas para que el formato
    de la hora local sea una sola lectura al inicio y no una consulta por fila.
    """
    desde, hasta = _mes_a_dias(mes)

    consulta = (
        select(Booking)
        .outerjoin(Service, Service.id == Booking.service_id)
        .outerjoin(Professional, Professional.id == Booking.professional_id)
        .outerjoin(Customer, Customer.id == Booking.customer_id)
        .where(
            Booking.business_id == business_id,
            Booking.local_date >= desde,
            Booking.local_date <= hasta,
        )
        .add_columns(
            Service.name, Professional.display_name, Customer.first_name, Customer.phone_e164
        )
        .order_by(Booking.starts_at, Booking.id)
    )
    if professional_id is not None:
        consulta = consulta.where(Booking.professional_id == professional_id)
    if estado is not None:
        consulta = consulta.where(Booking.status == estado)

    tz = await session.scalar(select(Business.timezone).where(Business.id == business_id))
    zona = ZoneInfo(tz) if tz else ZoneInfo("UTC")

    filas = await session.execute(consulta)
    return (
        [
            ReservaDetalle(
                booking=fila[0],
                servicio_nombre=fila[1],
                profesional_nombre=fila[2],
                cliente_nombre=fila[3],
                cliente_telefono=fila[4],
            )
            for fila in filas.all()
        ],
        zona,
    )


def _a_linea(fila: ReservaDetalle, zona: ZoneInfo) -> list[str]:
    """Una reserva a su fila de exportacion: texto, ya formateado.

    La fecha sale de `local_date` (ya calculada con el timezone del negocio) y
    la hora de convertir `starts_at` a ese mismo timezone: el turno de las
    23:30 en Buenos Aires es de las 02:30 UTC, y quien lee el reporte quiere la
    hora a la que atiende el negocio, no la del servidor.
    """
    b = fila.booking
    return [
        b.local_date.isoformat(),
        b.starts_at.astimezone(zona).strftime("%H:%M"),
        fila.cliente_nombre or "",
        fila.cliente_telefono or "",
        fila.profesional_nombre or "",
        fila.servicio_nombre or "",
        str(b.price_snapshot),
        b.status.value,
    ]


def generar_csv(filas: list[ReservaDetalle], zona: ZoneInfo) -> bytes:
    """CSV con BOM UTF-8 y `;` como separador (convencion Excel es-AR).

    El BOM es lo que hace que Excel reconozca el archivo como UTF-8 y no
    muestre "Corte" como "Corte" con caracteres raros; el `;` es el separador
    que el locale es-AR espera. `\r\n` como fin de linea, tambien lo que
    espera Excel.
    """
    salida = io.StringIO()
    salida.write("\ufeff")
    escritor = csv.writer(salida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(COLUMNAS)
    for fila in filas:
        escritor.writerow(_a_linea(fila, zona))
    return salida.getvalue().encode("utf-8")


@dataclass(frozen=True, slots=True)
class TotalesMensuales:
    """Los numeros de la hoja "Resumen" del XLSX."""

    total_reservas: int
    total_ingresos: Decimal
    canceladas: int
    por_profesional: dict[str, Decimal]
    por_servicio: dict[str, Decimal]


def _totales(filas: list[ReservaDetalle]) -> TotalesMensuales:
    """Agrega las filas del mes.

    `total_ingresos` y los desgloses suman solo `confirmed`/`completed`; una
    cancelada cuenta en `total_reservas` y en `canceladas`, no en el dinero.
    """
    pagadas = [f for f in filas if f.booking.status in ESTADOS_PAGADOS]
    por_profesional: dict[str, Decimal] = {}
    por_servicio: dict[str, Decimal] = {}
    for fila in pagadas:
        profesional = fila.profesional_nombre or "Sin profesional"
        servicio = fila.servicio_nombre or "Sin servicio"
        por_profesional[profesional] = (
            por_profesional.get(profesional, Decimal("0")) + fila.booking.price_snapshot
        )
        por_servicio[servicio] = (
            por_servicio.get(servicio, Decimal("0")) + fila.booking.price_snapshot
        )
    return TotalesMensuales(
        total_reservas=len(filas),
        total_ingresos=sum((f.booking.price_snapshot for f in pagadas), Decimal("0")),
        canceladas=sum(1 for f in filas if f.booking.status == BookingStatus.CANCELLED),
        por_profesional=por_profesional,
        por_servicio=por_servicio,
    )


def generar_xlsx(filas: list[ReservaDetalle], zona: ZoneInfo) -> bytes:
    """Libro de dos hojas: "Reservas" (las filas) y "Resumen" (totales).

    El resumen va como pares etiqueta/valor: para un Excel que alguien va a
    abrir, leer y quizas copiar, una tabla plana es mas util que un grafico y
    mucho mas simple de sostener. Los totales salen de `_totales`, que ya deja
    afuera del dinero a las canceladas.

    openpyxl se importa aca y no arriba para que el arranque de la API no pague
    la importacion (abre varios MB de modulos ZIP) por un archivo que solo se
    genera cuando alguien pide el Excel.
    """
    from openpyxl import Workbook

    libro = Workbook()
    # Un `Workbook()` nuevo trae siempre su hoja activa ("Sheet"): el assert es
    # para el tipado, no una rama de runtime--los stubs la tipan como optional.
    reservas = libro.active
    assert reservas is not None
    reservas.title = "Reservas"
    reservas.append(list(COLUMNAS))
    for fila in filas:
        reservas.append(_a_linea(fila, zona))

    totales = _totales(filas)
    resumen = libro.create_sheet("Resumen")
    resumen.append(["total_reservas", totales.total_reservas])
    resumen.append(["total_ingresos", totales.total_ingresos])
    resumen.append(["canceladas", totales.canceladas])
    resumen.append([])
    resumen.append(["Ingresos por profesional"])
    resumen.append(["profesional", "ingresos"])
    for nombre, monto in totales.por_profesional.items():
        resumen.append([nombre, monto])
    resumen.append([])
    resumen.append(["Ingresos por servicio"])
    resumen.append(["servicio", "ingresos"])
    for nombre, monto in totales.por_servicio.items():
        resumen.append([nombre, monto])

    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()


__all__ = [
    "COLUMNAS",
    "ESTADOS_PAGADOS",
    "generar_csv",
    "generar_xlsx",
    "reservas_del_mes",
]
