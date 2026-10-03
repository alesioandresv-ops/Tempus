"""Base declarativa y mixins comunes.

Los mixins existen para que las reglas que no se negocian no dependan de que
alguien se acuerde de escribirlas: el id es UUIDv7 generado por la base, el
`business_id` es obligatorio en toda tabla de tenant, y los timestamps los
mantiene un trigger del servidor.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    MetaData,
    Table,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import NAMING_CONVENTION, UtcDateTime

RLS_INFO_KEY = "rls"


# Marca en `Table.info` para decir que la tabla lleva RLS.
#
# Vive en el propio modelo y no en una lista aparte a mano, porque una lista
# aparte se desactualiza en silencio: alguien agrega una tabla con `business_id`,
# se olvida de la lista, y el test de RLS sigue pasando. Con la marca en el
# modelo, el test deriva de la realidad.
class Base(DeclarativeBase):
    """Base declarativa de todos los modelos.

    La convencion de nombres vive en `Base.metadata` y no en el motor, para que
    Alembic la vea sin configuracion adicional y los nombres de restriccion sean
    estables entre entornos.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@event.listens_for(Table, "after_parent_attach")
def _mark_tenant_tables(table: Table, _metadata: MetaData) -> None:
    """Marca en `table.info` las tablas que llevan `business_id`.

    No hay una lista de tablas de tenant escrita a mano. La lista se deriva de la
    columna: si un modelo nuevo declara `business_id`, esta Funcion lo marca solo y
    la migracion le pone RLS sin que nadie se acuerde de anotarlo. Una lista manual
    se desactualiza en silencio, y el sintoma es una tabla de tenant sin RLS, que
    es la peor falla posible en este sistema.

    El segundo argumento lo impone la firma del evento de SQLAlchemy; no se usa y
    por eso lleva el prefijo de underscore.
    """
    if "business_id" in table.c:
        table.info[RLS_INFO_KEY] = True


class UUIDPrimaryKeyMixin:
    """`id` UUIDv7 generado por la base.

    El default es de servidor a proposito: el id lo decide PostgreSQL, no Python.
    Ver ADR-0003. Si dos procesos insertan a la vez, ambos ids quedan ordenados y
    unicos sin que la aplicacion tenga que coordinar nada.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("uuidv7()"),
    )


class TenantMixin:
    """`business_id` en toda tabla de tenant.

    `nullable=False` y sin default. Que sea obligatorio en el modelo y no en la
    aplicacion es lo que hace imposible olvidar el filtro: el INSERT falla.
    """

    business_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)


class TimestampsMixin:
    """`created_at` y `updated_at` en UTC.

    `updated_at` lo actualiza un trigger (`set_updated_at`). No se escribe desde
    la aplicacion a proposito: un UPDATE hecho por un worker con SQL crudo pasaria
    por alto el `onupdate` de SQLAlchemy y dejaria la fila con una fecha vieja,
    que es justo el dato con el que se investiga un incidente.
    """

    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), server_default=text("now()"), nullable=False
    )


class TenantBase(UUIDPrimaryKeyMixin, TenantMixin, TimestampsMixin):
    """Base de las tablas de tenant: id + business_id + timestamps."""


class OptionalTenantBase(UUIDPrimaryKeyMixin, TimestampsMixin):
    """Base de las tablas de tenant cuyo `business_id` puede ser NULL.

    Existe para un caso concreto: `whatsapp_templates`. Una plantilla con
    `business_id = NULL` es una plantilla de plataforma, disponible para todos los
    negocios; con id de negocio es una plantilla del cliente.

    No se resuelve poniendo `nullable=True` sobre `TenantMixin.business_id`: eso es
    un override de tipo invalido (el mixin promete `UUID`, la subclase `UUID | None`),
    mypy lo rechaza, y ademas dejaria abierta la puerta a que el proximo modelo
    repita el override sin que nadie lo note. La nullability va declarada en la clase
    que la necesita.

    La RLS sigue aplicando: `app.db.base` marca las tablas por la **existencia** de
    la columna `business_id`, no por la clase base. Y tiene que seguir aplicando,
    porque la politica cubre las dos filas: con `business_id = NULL` no hay tenant que
    pueda verlas, y con id solo lo ve su negocio.
    """

    business_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)


class GlobalBase(UUIDPrimaryKeyMixin, TimestampsMixin):
    """Base de las tablas globales, que no tienen `business_id`."""


def tenant_unique_key(table: str) -> UniqueConstraint:
    """`UNIQUE (id, business_id)` que exige la §7 como Capa 1 del aislamiento.

    Sin este par unico, ninguna FK compuesta puede referenciar `(id, business_id)`.
    """
    return UniqueConstraint("id", "business_id", name=f"uq_{table}_id_business_key")


__all__ = [
    "RLS_INFO_KEY",
    "Base",
    "GlobalBase",
    "OptionalTenantBase",
    "TenantBase",
    "TenantMixin",
    "TimestampsMixin",
    "UUIDPrimaryKeyMixin",
    "tenant_unique_key",
]
