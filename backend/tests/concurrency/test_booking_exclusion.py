"""Doble reserva bajo concurrencia real.

La restriccion `EXCLUDE USING gist` de §7 existe por una razon concreta: dos
requests que confirman el mismo horario al mismo tiempo. Un chequeo con
`SELECT ... FOR UPDATE` no alcanza, porque entre el SELECT y el UPDATE las dos
transacciones todavia no se ven.

Por eso el test maneja las transacciones a mano y **commitea la primera antes de
resolver la segunda**. Ese es el punto: si las dos stays abiertas, la segunda se
queda esperando el lock del indice y el test se cuelga en vez de fallar. El
escenario real es "la primera request ya confirmo y commiteo; la segunda llega
despues", y es exactamente el que se reproduce.

Instantes fijos, sin `now()`: un test que depende del reloj falla un dia de cada
miles y se convierte en ruido. Se pasan como `datetime` con tz explicita y no como
string porque asyncpg castea los parametros segun el tipo de la columna: un string
contra un `timestamptz` falla con `invalid input for query argument`, que no dice
nada sobre lo que se esta probando.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import pytest
from app.db.session import TENANT_GUC
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from tests.conftest import BUSINESS_A, CUSTOMER_A, PROFESSIONAL_A, PROFESSIONAL_A2, SERVICE_A

pytestmark = [pytest.mark.concurrency, pytest.mark.integration]

SLOT_INICIO = datetime(2026, 3, 10, 14, 0, tzinfo=UTC)
SLOT_FIN = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)
SIGUIENTE_INICIO = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)
SIGUIENTE_FIN = datetime(2026, 3, 10, 16, 0, tzinfo=UTC)
MEDIO_INICIO = datetime(2026, 3, 10, 14, 30, tzinfo=UTC)
MEDIO_FIN = datetime(2026, 3, 10, 15, 30, tzinfo=UTC)


@asynccontextmanager
async def _txn(url: str) -> AsyncIterator[tuple[AsyncEngine, AsyncConnection]]:
    """Transaccion propia con su contexto de tenant.

    Cada transaccion va en su propia conexion, y no en el pool compartido de los
    tests: dos transacciones concurrentes que comparten una conexion son la misma
    transaccion, y el test probaria lo que quiere pero por el motivo equivocado.
    """
    engine = create_async_engine(url, poolclass=None)
    conn = await engine.connect()
    try:
        transaction = await conn.begin()
        await conn.execute(
            text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
            {"tenant": str(BUSINESS_A)},
        )
        yield engine, conn
        # Solo si sigue activa: los tests hacen `commit()` explicito, y sobre un
        # `Transaction` ya commiteado el rollback dispara un SAWarning que, con
        # `filterwarnings = ["error"]`, aborta el test.
        if transaction.is_active:
            await transaction.rollback()
    finally:
        await conn.close()
        await engine.dispose()


async def _insertar(
    conn: AsyncConnection, inicio: datetime, fin: datetime, professional_id: uuid.UUID
) -> None:
    """Reserva el slot. Lanza `DBAPIError` si la EXCLUDE lo rechaza.

    Se rellena a mano lo que la aplicacion calcularia: `local_date`, `source`,
    `secure_token_hash`, `version` y `confirmed_at`. Son `NOT NULL` sin default
    porque las deriva el codigo de dominio, no la base, y dejarlos para que los
    calcule un trigger habria movido logica de negocio al DDL.
    """
    await conn.execute(
        text(
            """
            INSERT INTO bookings (
                id, business_id, customer_id, service_id, professional_id,
                status, source, starts_at, ends_at, duration_minutes,
                occupied_from, occupied_to, local_date, price_snapshot, currency,
                secure_token_hash, version, confirmed_at
            )
            VALUES (
                gen_random_uuid(), :business_id, :customer_id, :service_id,
                :professional_id, 'confirmed', 'admin', :inicio, :fin, 60,
                :inicio, :fin, :local_date, 0, 'ARS',
                :secure_token_hash, 1, :inicio
            )
            """
        ),
        {
            "business_id": BUSINESS_A,
            "customer_id": CUSTOMER_A,
            "service_id": SERVICE_A,
            "professional_id": professional_id,
            "inicio": inicio,
            "fin": fin,
            # El turno arranca a las 14:00 UTC, que en Argentina son las 11:00 del
            # mismo dia: el `local_date` no se puede derivar con `date(utc)`.
            "local_date": date(2026, 3, 10),
            # Bytes aleatorios, no un hash. Lo que importa para estos tests es que
            # sea unico, y pedirle a Postgres un SHA-256 exigiria instalar pgcrypto
            # solo por un fixture.
            "secure_token_hash": uuid.uuid4().bytes,
        },
    )


class TestExclusion:
    @pytest.fixture(autouse=True)
    async def _sin_reservas_previas(
        self, migration_database_url: str, seeded: None
    ) -> AsyncIterator[None]:
        """Borra las reservas de los profesionales de prueba antes de cada test.

        Hace falta porque, a diferencia del resto de la suite, estos tests
        **commitean**: es la unica forma de que la EXCLUDE pueda verse. El precio es
        que las filas sobreviven al test, y sin esta limpieza el segundo test del
        archivo chocaria contra el primero con un `no_overlap` que no viene de lo que
        esta probando.

        Se borra con el rol de DDL y el GUC de tenant puesto, porque `FORCE RLS`
        alcanza tambien al dueno de las tablas.
        """
        engine = create_async_engine(migration_database_url, poolclass=None)
        try:
            async with engine.connect() as conn:
                transaction = await conn.begin()
                await conn.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                    {"tenant": str(BUSINESS_A)},
                )
                await conn.execute(
                    text("DELETE FROM bookings WHERE professional_id = ANY(:ids)"),
                    {"ids": [PROFESSIONAL_A, PROFESSIONAL_A2]},
                )
                await transaction.commit()
        finally:
            await engine.dispose()
        yield

    async def test_el_segundo_intento_choca_con_el_primero(self, test_database_url: str) -> None:
        """El caso central: el mismo slot no se puede vender dos veces.

        La primera transaccion inserta y commitea. La segunda intenta lo mismo y
        tiene que chocar contra la EXCLUDE. Si la EXCLUDE no estuviera, la segunda
        insertaria sin problema y habria dos reservas para el mismo profesional en
        el mismo minuto.
        """
        async with _txn(test_database_url) as (_engine_a, conn_a):
            await _insertar(conn_a, SLOT_INICIO, SLOT_FIN, PROFESSIONAL_A)
            await conn_a.commit()

        with pytest.raises(DBAPIError) as excinfo:
            async with _txn(test_database_url) as (_engine_b, conn_b):
                await _insertar(conn_b, SLOT_INICIO, SLOT_FIN, PROFESSIONAL_A)

        mensaje = str(excinfo.value.orig or excinfo.value).lower()
        assert "no_overlap" in mensaje or "exclusion" in mensaje, (
            f"el rechazo deberia venir de la EXCLUDE, no de otra restriccion: {mensaje}"
        )

    async def test_turnos_contiguos_coexisten(self, test_database_url: str) -> None:
        """14:00-15:00 y 15:00-16:00 conviven.

        Es el test que justifica el `'[)'` del `tstzrange`. Con `[]` el segundo
        turno se rechazaria por compartir el instante de las 15:00, y el calendario
        perderia la mitad de los turnos de la tarde.
        """
        async with _txn(test_database_url) as (_engine_a, conn_a):
            await _insertar(conn_a, SLOT_INICIO, SLOT_FIN, PROFESSIONAL_A)
            await conn_a.commit()

        async with _txn(test_database_url) as (_engine_b, conn_b):
            await _insertar(conn_b, SIGUIENTE_INICIO, SIGUIENTE_FIN, PROFESSIONAL_A)
            await conn_b.commit()

    async def test_solapamiento_parcial_se_rechaza(self, test_database_url: str) -> None:
        """14:00-15:00 y 14:30-15:30 se pisan por un cuarto de hora.

        Un `CHECK` que solo comparara los extremos no lo detectaria: hace falta la
        comparacion de rangos, que es justamente lo que hace el operador `&&`.
        """
        async with _txn(test_database_url) as (_engine_a, conn_a):
            await _insertar(conn_a, SLOT_INICIO, SLOT_FIN, PROFESSIONAL_A)
            await conn_a.commit()

        with pytest.raises(DBAPIError):
            async with _txn(test_database_url) as (_engine_b, conn_b):
                await _insertar(conn_b, MEDIO_INICIO, MEDIO_FIN, PROFESSIONAL_A)

    async def test_otro_profesional_puede_reservar_el_mismo_horario(
        self, test_database_url: str
    ) -> None:
        """La EXCLUDE es por profesional, no global.

        Si el indice no incluyera `professional_id`, dos profesionales del mismo
        negocio no podrian tener turno a la vez y el negocio no podria atender a dos
        clientes en paralelo.

        El segundo profesional es del **mismo** tenant a proposito. Con uno de otro
        negocio, la FK compuesta `fk_bookings_professional` lo rechazaria antes de
        llegar a la EXCLUDE, y el test pasaria por el motivo equivocado.
        """
        async with _txn(test_database_url) as (_engine_a, conn_a):
            await _insertar(conn_a, SLOT_INICIO, SLOT_FIN, PROFESSIONAL_A)
            await conn_a.commit()

        async with _txn(test_database_url) as (_engine_b, conn_b):
            await _insertar(conn_b, SLOT_INICIO, SLOT_FIN, PROFESSIONAL_A2)
            await conn_b.commit()

    async def test_cancelar_libera_el_horario(self, test_database_url: str) -> None:
        """Una reserva `cancelled` no bloquea el slot.

        Si `cancelled` no estuviera fuera del predicado de la EXCLUDE, un turno
        cancelado seguiria ocupando la agenda para siempre: el cliente que cancela
        deja de poder volver a reservar y el profesional pierde el hueco.
        """
        async with _txn(test_database_url) as (_engine_a, conn_a):
            await _insertar(conn_a, SLOT_INICIO, SLOT_FIN, PROFESSIONAL_A)
            await conn_a.commit()

        async with _txn(test_database_url) as (_engine_c, conn_c):
            await conn_c.execute(
                text(
                    "UPDATE bookings SET status = 'cancelled', cancelled_at = now() "
                    "WHERE starts_at = :inicio"
                ),
                {"inicio": SLOT_INICIO},
            )
            await conn_c.commit()

        async with _txn(test_database_url) as (_engine_b, conn_b):
            await _insertar(conn_b, SLOT_INICIO, SLOT_FIN, PROFESSIONAL_A)
            await conn_b.commit()
