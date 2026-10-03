"""Invariantes del esquema que se verifican sin tocar la base.

Estos tests son la red de seguridad mas barata del proyecto: corren en
milisegundos y detectan la mayor parte de los errores de modelado antes de que
alguien levante un contenedor. Lo que se chequea aca es **estructura**; el
comportamiento (RLS efectiva, EXCLUDE bajo concurrencia) vive en `tests/tenancy` y
`tests/concurrency`.
"""

from __future__ import annotations

import pytest
from app.db.base import Base
from app.models import GLOBAL_TABLES, NO_RLS_TABLES, TENANT_TABLES
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.schema import CreateTable
from sqlalchemy.types import Uuid

#: Numero de tablas de §7. La arquitectura decia 26; el numero real es 27, porque
#: el conteo original de la lista se saltó `webhook_events` (la unica tabla global
#: que no es de plataforma). El desvio se documenta en `ARCHITECTURE.md`; aca se
#: fija el valor que el codigo implementa, para que el desync falle en un test y
#: no en una review.
EXPECTED_TABLE_COUNT = 27

#: Las tablas que §7 nombra una por una. Comparar contra la lista en vez de
#: contar es lo que hace util el test: al agregar una tabla sin updating la
#: arquitectura, el nombre falta y el error dice cual.
EXPECTED_TABLES = {
    # Globales (7)
    "idempotency_keys",
    "businesses",
    "platform_users",
    "refresh_tokens",
    "webhook_events",
    "slug_reservations",
    "rate_limit_buckets",
    "jobs",
    # De tenant (20)
    "audit_log",
    "blocks",
    "booking_events",
    "bookings",
    "business_exception_windows",
    "business_exceptions",
    "business_hours",
    "business_users",
    "customers",
    "holidays",
    "media",
    "notification_requests",
    "professional_schedules",
    "professional_services",
    "professionals",
    "services",
    "time_off",
    "whatsapp_connections",
    "whatsapp_templates",
}

#: Tablas de tenant cuya clave unica no es `UNIQUE (id, business_id)`.
#:
#: `professional_services` y `business_exception_windows` tienen PK compuesta, que
#: ya las hace referenciables. `jobs` esta en RLS pero ninguna FK compuesta la
#: apunta: el dispatcher la busca por `status`, no por id de negocio.
SIN_CLAVE_COMPUESTA = {"professional_services", "business_exception_windows", "jobs"}


def _dialect() -> Dialect:
    """Dialecto de renderizado. `metadata.bind` ya no existe en SQLAlchemy 2.0."""
    from sqlalchemy.dialects import postgresql

    # `postgresql.dialect()` no tiene anotacion de retorno en los stubs de
    # SQLAlchemy, asi que mypy lo ve como una llamada sin tipar. Se declara aqui el
    # tipo que devuelve en realidad, que es lo que pide `compile(dialect=...)`.
    dialect: Dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    return dialect


def _table_ddl(name: str) -> str:
    return str(CreateTable(Base.metadata.tables[name]).compile(dialect=_dialect()))


class TestTableCount:
    def test_tablas_totales(self) -> None:
        assert len(Base.metadata.tables) == EXPECTED_TABLE_COUNT

    def test_las_tablas_de_arquitectura_existen(self) -> None:
        assert set(Base.metadata.tables) == EXPECTED_TABLES

    def test_tenant_mas_global_cubre_todas(self) -> None:
        assert set(Base.metadata.tables) == TENANT_TABLES | GLOBAL_TABLES

    def test_tenant_y_global_no_se_solapan(self) -> None:
        assert not (TENANT_TABLES & GLOBAL_TABLES)

    def test_el_conteo_de_documentacion_no_difiere(self) -> None:
        """El comentario del modulo de modelos declara el mismo numero.

        Si alguien agrega una tabla y actualiza el codigo pero no el comentario, el
        error sale aca y no tres meses despues en la documentacion.
        """
        import app.models as modelos

        declarado = modelos.TABLE_COUNT
        assert declarado == EXPECTED_TABLE_COUNT == len(Base.metadata.tables)


class TestTenancyClassification:
    def test_jobs_es_la_unica_excepcion(self) -> None:
        """`jobs` tiene `business_id` pero no lleva RLS.

        El dispatcher procesa filas de todos los tenants en la misma transaccion.
        Con RLS tendria que hacer un `SET LOCAL` por negocio, lo cual lo obliga a
        abrir una transaccion por fila y destruye el batching que justifica que
        exista. La columna esta para poder filtrar en la aplicacion, no para que la
        base proteja.
        """
        assert frozenset({"jobs"}) == NO_RLS_TABLES

    @pytest.mark.parametrize("table", sorted(TENANT_TABLES))
    def test_toda_tabla_con_rls_tiene_business_id(self, table: str) -> None:
        assert "business_id" in Base.metadata.tables[table].c

    @pytest.mark.parametrize("table", sorted(GLOBAL_TABLES))
    def test_las_tablas_globales_no_declaran_business_id_obligatorio(self, table: str) -> None:
        """Una columna `business_id` global tiene que ser nullable.

        `jobs` es el caso real: el dispatcher procesa los trabajos de plataforma,
        que no pertenecen a ningun negocio, y los de un negocio concreto, que si.
        Con la columna `NOT NULL` los trabajos de plataforma no se podrian guardar.
        """
        columnas = Base.metadata.tables[table].c
        if "business_id" in columnas:
            assert columnas["business_id"].nullable, (
                f"{table}.business_id deberia ser nullable: las tablas globales tambien "
                "atienden trabajo que no pertenece a un negocio"
            )


class TestPrimaryKeys:
    @pytest.mark.parametrize(
        ("table", "expected"),
        [
            ("bookings", ["id"]),
            ("customers", ["id"]),
            ("services", ["id"]),
            # Compuesta: un servicio pertenece a un solo profesional.
            ("professional_services", ["professional_id", "service_id"]),
            # Estas dos son 1:1 con su padre, asi que la PK es propia.
            ("business_exception_windows", ["id"]),
            ("professional_schedules", ["id"]),
        ],
    )
    def test_pk(self, table: str, expected: list[str]) -> None:
        assert list(Base.metadata.tables[table].primary_key.columns.keys()) == expected

    @pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
    def test_toda_tabla_tiene_pk(self, table: str) -> None:
        assert list(Base.metadata.tables[table].primary_key.columns.keys()), (
            f"{table} no tiene primary key"
        )


class TestTenantUniqueKey:
    """`UNIQUE (id, business_id)` en toda tabla referenciada por FK compuesta.

    Sin esta clave, la FK compuesta de §7 no se puede crear: Postgres exige que las
    columnas referenciadas formen una clave unica. El error es `there is no unique
    constraint matching given keys for referenced table`, y aparece al aplicar la
    migracion, no al escribir el modelo.
    """

    @pytest.mark.parametrize("table", sorted(TENANT_TABLES - SIN_CLAVE_COMPUESTA))
    def test_tabla_referenciada_tiene_clave_compuesta(self, table: str) -> None:
        uniques = {
            frozenset(uc.columns.keys())
            for uc in Base.metadata.tables[table].constraints
            if isinstance(uc, UniqueConstraint)
        }
        assert frozenset({"id", "business_id"}) in uniques, (
            f"{table} se referencia por FK compuesta y necesita UNIQUE (id, business_id)"
        )

    def test_toda_fk_compuesta_tiene_clave_al_otro_lado(self) -> None:
        """Recorre las FKs reales y valida el destino.

        Mas fuerte que contar tablas: si alguien agrega una FK compuesta a una
        tabla sin la clave, el test dice exactamente cual.
        """
        faltantes: list[str] = []
        for nombre, tabla in Base.metadata.tables.items():
            for fk in tabla.foreign_key_constraints:
                elementos = list(fk.elements)
                remota = {e.column.name for e in elementos}
                destino = Base.metadata.tables[elementos[0].column.table.name]
                tiene_unica = any(
                    set(uc.columns.keys()) == remota
                    for uc in destino.constraints
                    if isinstance(uc, UniqueConstraint)
                )
                tiene_pk = set(destino.primary_key.columns.keys()) == remota
                if not (tiene_unica or tiene_pk):
                    faltantes.append(f"{nombre} -> {destino.name} {sorted(remota)}")
        assert not faltantes, f"FKs sin clave unica al otro lado: {faltantes}"

    @pytest.mark.parametrize("table", sorted(TENANT_TABLES))
    def test_las_claves_primarias_y_foraneas_son_uuid_nativos(self, table: str) -> None:
        """`UUID` de Postgres en toda clave, no `CHAR(36)`.

        Se chequean las claves y las columnas que aparecen en alguna FK, no todo lo
        que termina en `_id`: los ids que vienen de Meta (`phone_number_id`,
        `provider_message_id`) son strings opacos y **deben** serlo, no se pueden
        inventar como UUID.
        """
        tabla = Base.metadata.tables[table]
        columnas_por_nombre = {c.name: c for c in tabla.c}
        a_chequear = set(tabla.primary_key.columns.keys())
        for fk in tabla.foreign_key_constraints:
            a_chequear.update(c.name for c in fk.columns)

        for nombre in sorted(a_chequear):
            assert isinstance(columnas_por_nombre[nombre].type, Uuid), (
                f"{table}.{nombre} es {columnas_por_nombre[nombre].type!r}; "
                "se espera UUID nativo de Postgres"
            )


class TestBookingExclusion:
    """La restriccion EXCLUDE que impide la doble reserva."""

    def test_existe_exactamente_una_exclusion(self) -> None:
        exclusiones = [
            c
            for c in Base.metadata.tables["bookings"].constraints
            if isinstance(c, ExcludeConstraint)
        ]
        assert len(exclusiones) == 1

    def test_el_ddl_usa_rango_medio_abierto(self) -> None:
        """`tstzrange(occupied_from, occupied_to, '[)')`.

        Con `[)` una reserva que termina a las 10:00 y otra que empieza a las 10:00
        **no** se solapan. Con `[]` si, y el sistema rechazaria un turno de 30
        minutos que arranca justo cuando termina el anterior: la diferencia entre
        un calendario usable y uno que pierde la mitad de la tarde.
        """
        ddl = _table_ddl("bookings")
        assert "tstzrange(occupied_from, occupied_to, '[)')" in ddl

    def test_el_ddl_bloquea_por_profesional(self) -> None:
        ddl = _table_ddl("bookings")
        assert "EXCLUDE USING gist" in ddl
        assert "professional_id" in ddl

    def test_solo_bloquea_los_estados_ocupantes(self) -> None:
        """La predicado incluye `confirmed` y `pending_hold`, no los demas.

        Una reserva `cancelled` libera el horario: si `cancelled` no estuviera
        fuera del predicado, un turno cancelado seguiria bloqueando la agenda para
        siempre, que es el bug clasico de un calendario con cancelacion.
        """
        ddl = _table_ddl("bookings")
        assert "confirmed" in ddl
        assert "pending_hold" in ddl
        # Los estados que no ocupan no pueden aparecer.
        assert "cancelled'" not in ddl.split("EXCLUDE USING gist")[1].split(")")[0]

    def test_requiere_ocupacion_abierta(self) -> None:
        """`occupied_from` / `occupied_to` no pueden ser nulos.

        Son los extremos del rango de la EXCLUDE. Un `tstzrange` con un extremo
        nulo es un rango infinito, y dos rangos infinitos se solapan siempre: un
        solo booking sin `occupied_to` bloquearia la agenda del profesional para
        siempre.
        """
        columnas = Base.metadata.tables["bookings"].c
        assert columnas["occupied_from"].nullable is False
        assert columnas["occupied_to"].nullable is False


class TestCheckConstraints:
    @pytest.mark.parametrize(
        ("table", "fragmento"),
        [
            ("bookings", "duration_minutes > 0"),
            ("bookings", "ends_at > starts_at"),
            ("bookings", "price_snapshot >= 0"),
            ("bookings", "occupied_from <= starts_at"),
            ("services", "duration_minutes > 0"),
            ("services", "price >= 0"),
        ],
    )
    def test_check_de_negocio_presente(self, table: str, fragmento: str) -> None:
        checks = [
            c for c in Base.metadata.tables[table].constraints if isinstance(c, CheckConstraint)
        ]
        assert any(fragmento in str(c.sqltext) for c in checks), (
            f"{table} deberia tener un CHECK con {fragmento!r}"
        )

    def test_cancelled_coherente_con_timestamp(self) -> None:
        """`status = 'cancelled'` si y solo si `cancelled_at` esta puesto.

        Sin esta equivalencia, un endpoint que cancela puede olvidar el timestamp y
        la reserva queda en un estado que la agenda no reconoce.
        """
        checks = [
            str(c.sqltext)
            for c in Base.metadata.tables["bookings"].constraints
            if isinstance(c, CheckConstraint)
        ]
        assert any("cancelled_at IS NOT NULL" in texto for texto in checks)
