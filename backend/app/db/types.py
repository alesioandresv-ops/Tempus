"""Tipos de dominio para las columnas de la base.

Centralizar los tipos aqui tiene una razon concreta: `uuidv7()` es nativo de
PostgreSQL 18, pero si cada modelo escribiera el `server_default` a mano, la
decision de ADR-0003 quedaria repartida en 26 lugares y nadie podria cambiarla.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.types import TypeDecorator

# Convencion de nombres. Sin esto, Alembic genera nombres de restriccion con
# sufijo aleatorio y cada autogenerate produce un diff distinto.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
    "ex": "ex_%(table_name)s_%(constraint_name)s",
}

# Layout de UUID v7 segun RFC 9562, seccion 5.7.
_UUID7_TIMESTAMP_MASK = (1 << 48) - 1
_UUID7_VERSION = 0x7
_UUID7_RAND_A_MASK = (1 << 12) - 1
_UUID7_VARIANT = 0b10
_UUID7_RAND_B_MASK = (1 << 62) - 1


def new_uuid() -> uuid.UUID:
    """UUID v7 en Python.

    El servidor usa `uuidv7()` (nativo en PG 18). Esta funcion cubre el caso en
    que el id se genere en la aplicacion: en tests, en la generacion de la cola, o
    si algun dia se corre contra una version anterior de PostgreSQL.

    No usa `uuid.uuid4()` a proposito: los UUIDv4 no tienen orden temporal, y el
    indice se fragmenta. Es el motivo de ADR-0003.

    Reparto de bits, de mas alto a mas bajo:

        48 bits  unix_ts_ms
         4 bits  version = 0b0111
        12 bits  rand_a
         2 bits  variant = 0b10
        62 bits  rand_b

    La entropia sale de `secrets`, no de `uuid.uuid4()` ni de la MAC: una
    direccion de hardware repetida en varios tenants es informacion de red
    filtrada en cada id.
    """
    timestamp_ms = int(dt.datetime.now(dt.UTC).timestamp() * 1000) & _UUID7_TIMESTAMP_MASK
    rand_a = secrets.randbits(12) & _UUID7_RAND_A_MASK
    rand_b = secrets.randbits(62) & _UUID7_RAND_B_MASK
    value = timestamp_ms << 80
    value |= _UUID7_VERSION << 76
    value |= rand_a << 64
    value |= _UUID7_VARIANT << 62
    value |= rand_b
    return uuid.UUID(int=value)


class UUIDv7(TypeDecorator[uuid.UUID]):
    """UUID v7 que se genera en PostgreSQL si no se le pasa valor.

    `server_default=text("uuidv7()")` hace que el id lo ponga la base, que es lo
    correcto: si dos procesos insertan al mismo tiempo, ambos tienen ids ordenados
    y unicos sin que la aplicacion tenga que coordinarlos.
    """

    impl = PGUUID(as_uuid=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:  # noqa: ARG002
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        raise TypeError(f"UUIDv7 espera un uuid.UUID, recibio {type(value).__name__}")


class UtcDateTime(TypeDecorator[dt.datetime]):
    """`timestamptz` que siempre entra y sale en UTC con timezone.

    El `DateTime` de SQLAlchemy con `timezone=False` devuelve datetimes naive, y un
    datetime naive no sabe que dia es. Con el horario de verano de Argentina, esa
    ambiguedad son dos horas de reservas mal ubicadas. Ver ADR-0004.

    Los `dialect` sin usar llevan `# noqa: ARG002` porque `TypeDecorator` los exige
    en la firma. No se pueden renombrar a `_dialect`: mypy rechaza el override si el
    nombre del parametro no coincide, ya que un caller podria llamarlo por keyword.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:  # noqa: ARG002
        if value is None:
            return None
        if not isinstance(value, dt.datetime):
            raise TypeError(f"UtcDateTime espera un datetime, recibio {type(value).__name__}")
        if value.tzinfo is None:
            raise ValueError(
                "Se recibio un datetime naive. Un datetime sin timezone no sabe que "
                "hora es: use datetime.now(UTC) o una funcion de core.time."
            )
        return value.astimezone(dt.UTC)

    def process_result_value(self, value: Any, dialect: Any) -> Any:  # noqa: ARG002
        if value is None:
            return None
        return value.astimezone(dt.UTC) if value.tzinfo else value


__all__ = [
    "NAMING_CONVENTION",
    "UUIDv7",
    "UtcDateTime",
    "new_uuid",
]
