"""Test de integracion del endpoint publico de disponibilidad (GET).

El GET de `/businesses/{slug}/availability` lee por query params y
devuelve, ademas de los slots, la lista completa de `candidatos`:
quienes pueden atender cada horario, ordenados por la estrategia de
asignacion (menos carga del dia, desempate por `sort_order`).

**El seed es commiteado, con el rol de DDL, y esas dos cosas son la
misma decision.** El endpoint publico resuelve el negocio por slug en
una sesion propia (`session_scope`, no la de la request): no pasa por
las dependencias que `http_client` reemplaza. Si el negocio y su
horario vivieran en la transaccion que el fixture `connection` revierte
al final, el endpoint miraria otra conexion y no vera nada: el test
devolveria 404 sin haber probado nada. Por eso se siembra con el rol
de DDL--el unico que puede INSERT en `businesses` (ver `0012`)--y se
comitea, idempotente (`ON CONFLICT DO NOTHING`), igual que el seed
que `conftest` hace para `test-business`.

El negocio es propio y no se toca el seed compartido: los tests de
bookings crean su servicio y su profesional sobre `test-business` en
su transaccion, y nada de eso tiene que ver con la disponibilidad de
este archivo.

**El negocio se siembra `active`, y por el `status`, no por capricio.**
La columna tiene default `'trial'`, y `_resolve_business_by_slug`--el
unico lugar por donde pasa toda peticion publica-- filtra por
`status = 'active'`: un `trial` se configura pero no se publica. Un
negocio `trial` invisible para el endpoint publico daria 404 en vez de
la grilla de slots, y el test pasaria por un problema de visibilidad
cuando lo que esta en juego es el calculo de disponibilidad. Ojo con
el `ON CONFLICT`: hace falta `DO UPDATE SET status`, porque una fila
que una corrida anterior dejo en `trial` no se corrige sola con `DO
NOTHING` y el fallo seria dificil de ver--parece un endpoint roto.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from app.db.session import TENANT_GUC
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration

#: Slug del negocio de este archivo. Fijo: el seed es idempotente
#: por el, y dos corridas de la suite no pueden crear dos negocios
#: con el mismo nombre de URL.
SLUG = "negocio-disponibilidad"

#: IDs fijos estilo v7, como el resto de los datos de test: la
#: reproducibilidad no depende del orden de ejecucion.
BUSINESS = uuid.UUID("dddddddd-dddd-7ddd-8ddd-dddddddddddd")
SERVICE = uuid.UUID("aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa")
#: Servicio del mismo negocio al que **ningun** profesional esta
#: asignado: es el caso "nadie puede atenderlo hoy", que responde
#: lista vacia y no 404.
SERVICE_SIN_PROFESIONAL = uuid.UUID("11111111-1111-7111-8111-111111111111")

#: Ana y Beto atienden el servicio. Ana tiene `sort_order` 1 y Beto
#: 2, pero el UUID de Beto es **menor**: si el orden de los
#: candidatos fuera por id, Beto saldria primero. El test de la lista
#: de candidatos verifica que sale Ana, o sea que el desempate es por
#: `sort_order` como dice el contrato, y no por cualquier otro orden.
ANA = uuid.UUID("bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb")
BETO = uuid.UUID("ffffffff-ffff-7fff-8fff-ffffffffffff")

#: Bloqueo general de la prueba de regresion. Id fijo, como el resto de
#: los datos de test; el teardown lo borra por `reason`, asi una corrida
#: interrumpida no deja basura que choque con la siguiente.
BLOQUE_GENERAL = uuid.UUID("99999999-9999-7999-8999-9999999999a1")

TIMEZONE = "America/Argentina/Buenos_Aires"


async def _sembrar(conn: AsyncConnection) -> None:
    """Negocio, semana, dos profesionales y el vinculo servicio-profesional.

    Todo `ON CONFLICT DO NOTHING`: el seed corre antes de cada test
    y no es un error que las filas ya esten--son datos de referencia
    compartidos, igual que los de `_seed_tenants` en `conftest`. La
    unica excepcion es el negocio, que va con `DO UPDATE`: su `status`
    es parte de lo que el endpoint prueba y no puede quedar en el
    `trial` que lo creo una corrida anterior (ver el docstring del
    modulo).
    """
    # Las tablas de tenant exigen el GUC aun para el dueno: las
    # politicas corren con `FORCE RLS` (ver el fixture `seeded` de
    # `conftest` por la misma razon). Va **antes** de escribir, y no solo
    # por las tablas de tenant: la rama `DO UPDATE` del upsert de mas
    # abajo cae en `businesses_write`, que exige que el GUC sea
    # justamente este negocio.
    await conn.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(BUSINESS)},
    )

    await conn.execute(
        text(
            """
            INSERT INTO businesses (id, name, slug, timezone, status)
            VALUES (:id, 'Negocio Disponibilidad', :slug, :tz, 'active')
            ON CONFLICT (slug) DO UPDATE SET status = 'active'
            """
        ),
        {"id": BUSINESS, "slug": SLUG, "tz": TIMEZONE},
    )

    # Lunes a sabado, dos ventanas por dia. La jornada partida son
    # dos filas porque el modelo no tiene descanso dentro de una
    # ventana (§11). **Domingo sin filas**: la ausencia ya significa
    # "cerrado", y es lo que el test del domingo consulta.
    for weekday in range(6):
        for indice, (inicio, fin) in enumerate(
            ((dt.time(9, 0), dt.time(12, 0)), (dt.time(16, 0), dt.time(20, 0)))
        ):
            await conn.execute(
                text(
                    """
                    INSERT INTO business_hours
                        (business_id, weekday, window_index,
                         start_time, end_time, is_open)
                    VALUES (:business, :weekday, :indice,
                            :inicio, :fin, true)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "business": BUSINESS,
                    "weekday": weekday,
                    "indice": indice,
                    "inicio": inicio,
                    "fin": fin,
                },
            )

    await conn.execute(
        text(
            """
            INSERT INTO services (id, business_id, name, duration_minutes, price, currency)
            VALUES (:id, :business, 'Corte', 30, 1000.00, 'ARS')
            ON CONFLICT DO NOTHING
            """
        ),
        {"id": SERVICE, "business": BUSINESS},
    )
    await conn.execute(
        text(
            """
            INSERT INTO services (id, business_id, name, duration_minutes, price, currency)
            VALUES (:id, :business, 'Sin equipo', 45, 1500.00, 'ARS')
            ON CONFLICT DO NOTHING
            """
        ),
        {"id": SERVICE_SIN_PROFESIONAL, "business": BUSINESS},
    )

    await conn.execute(
        text(
            """
            INSERT INTO professionals (id, business_id, display_name, sort_order)
            VALUES (:ana, :business, 'Ana Perez', 1)
            ON CONFLICT DO NOTHING
            """
        ),
        {"ana": ANA, "business": BUSINESS},
    )
    await conn.execute(
        text(
            """
            INSERT INTO professionals (id, business_id, display_name, sort_order)
            VALUES (:beto, :business, 'Beto Gomez', 2)
            ON CONFLICT DO NOTHING
            """
        ),
        {"beto": BETO, "business": BUSINESS},
    )

    for profesional in (ANA, BETO):
        await conn.execute(
            text(
                """
                INSERT INTO professional_services
                    (professional_id, service_id, business_id, is_active)
                VALUES (:profesional, :servicio, :business, true)
                ON CONFLICT DO NOTHING
                """
            ),
            {"profesional": profesional, "servicio": SERVICE, "business": BUSINESS},
        )

    # Sin esto el GUC quedaria puesto para el resto de la conexion.
    # No cambia nada (la conexion se cierra al salir del `with`) pero
    # es la misma higiene que `_seed_tenants`.
    await conn.execute(text("SELECT set_config(:name, '', true)"), {"name": TENANT_GUC})


@pytest_asyncio.fixture
async def negocio_disponibilidad(
    migration_database_url: str,
) -> AsyncIterator[str]:
    """Siembra y commitea el negocio de disponibilidad (ver docstring)."""
    owner_engine: AsyncEngine = create_async_engine(
        migration_database_url, poolclass=None, echo=False
    )
    try:
        async with owner_engine.begin() as conn:
            await _sembrar(conn)
        yield SLUG
    finally:
        await owner_engine.dispose()


@pytest_asyncio.fixture
async def bloqueo_general_de_martes(migration_database_url: str) -> AsyncIterator[dt.date]:
    """Bloqueo de todo el negocio (`professional_id` NULL) sobre un martes.

    Se siembra con el rol de DDL y commiteado, igual que el seed: el
    endpoint publico corre en su propia sesion (ver el docstring del
    modulo), asi que un bloqueo que viviera en la transaccion del test
    no lo veria nadie y la prueba no probaria nada.

    El teardown borra **por `reason`** y no por id: si una corrida
    anterior se interrumpio a mitad, la fila vieja (con la ventana de
    su propio martes) se borra junto con la actual, y el `ON CONFLICT`
    del alta deja el dia fresco listo.
    """
    tz = ZoneInfo(TIMEZONE)
    martes = _lunes_lejano() + dt.timedelta(days=1)
    inicio = dt.datetime.combine(martes, dt.time(9, 0), tzinfo=tz).astimezone(dt.UTC)
    fin = dt.datetime.combine(martes, dt.time(12, 0), tzinfo=tz).astimezone(dt.UTC)

    owner_engine: AsyncEngine = create_async_engine(
        migration_database_url, poolclass=None, echo=False
    )
    try:
        async with owner_engine.begin() as conn:
            await conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                {"tenant": str(BUSINESS)},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO blocks
                        (id, business_id, professional_id, kind,
                         starts_at, ends_at, occupied_from, occupied_to, reason)
                    VALUES
                        (:id, :business, NULL, 'blocked',
                         :inicio, :fin, :inicio, :fin, 'Bloqueo general de prueba')
                    ON CONFLICT (id) DO UPDATE SET
                        starts_at = EXCLUDED.starts_at,
                        ends_at = EXCLUDED.ends_at,
                        occupied_from = EXCLUDED.occupied_from,
                        occupied_to = EXCLUDED.occupied_to
                    """
                ),
                {
                    "id": BLOQUE_GENERAL,
                    "business": BUSINESS,
                    "inicio": inicio,
                    "fin": fin,
                },
            )
        yield martes
    finally:
        async with owner_engine.begin() as conn:
            await conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                {"tenant": str(BUSINESS)},
            )
            await conn.execute(
                text(
                    "DELETE FROM blocks WHERE business_id = :b AND reason = 'Bloqueo general de prueba'"
                ),
                {"b": BUSINESS},
            )
        await owner_engine.dispose()


def _lunes_lejano() -> dt.date:
    """Un lunes con margen de sobra para el lead time y para el reloj.

    Treinta dias y no una semana: el lead time minimo es de una hora,
    pero un test que corre a cualquier hora no puede depender de que
    "manana a las 09:00" este a mas de una hora de ahora. Con un mes
    de margen, el lunes sale siempre en el futuro y la grilla completa
    sobrevive al filtro de antelacion.
    """
    base = dt.datetime.now(ZoneInfo(TIMEZONE)).date() + dt.timedelta(days=30)
    return base + dt.timedelta(days=(-base.weekday()) % 7)


def _domingo_lejano() -> dt.date:
    """El domingo del mismo fin de semana que `_lunes_lejano`."""
    return _lunes_lejano() + dt.timedelta(days=6)


class TestGetDisponibilidadPublica:
    async def test_un_lunes_devuelve_los_slots_de_las_dos_ventanas(
        self, http_client: AsyncClient, negocio_disponibilidad: str
    ) -> None:
        """La grilla completa de un dia abierto, con los dos candidatos.

         El numero **exacto** de slots es parte de lo que se prueba:
         la grilla se ancla al inicio de la ventana y un slot solo vale
         si cabe entero (semiabierto). 09:00-12:00 son 11 arranques de
         30 minutos en pasos de 15--el ultimo, 11:30--y 16:00-20:00
         son 15--el ultimo, 19:30. Si la duracion, la grilla o el
        semiabierto se desvian, el total cambia y el test lo dice.
        """
        fecha = _lunes_lejano()
        respuesta = await http_client.get(
            f"/api/v1/public/businesses/{negocio_disponibilidad}/availability",
            params={"service_id": str(SERVICE), "date": fecha.isoformat()},
        )
        assert respuesta.status_code == 200, respuesta.text
        cuerpo = respuesta.json()

        # El sobre viaja con el contexto de la consulta, no solo con
        # los slots: es lo que permite cachear la respuesta por el
        # pedido que la genero.
        assert cuerpo["business_id"] == str(BUSINESS)
        assert cuerpo["service_id"] == str(SERVICE)
        assert cuerpo["date"] == fecha.isoformat()

        slots = cuerpo["slots"]
        assert len(slots) == 26

        # Los horarios viajan en UTC; lo que se verifica aca es la
        # hora **local** del negocio, que es lo que consulto el
        # cliente.
        tz = ZoneInfo(TIMEZONE)
        primero = dt.datetime.fromisoformat(slots[0]["starts_at"]).astimezone(tz)
        ultimo = dt.datetime.fromisoformat(slots[-1]["starts_at"]).astimezone(tz)
        assert (primero.hour, primero.minute) == (9, 0)
        assert (ultimo.hour, ultimo.minute) == (19, 30)

        # Los dos profesionales, ordenados por `sort_order`: Ana va
        # primero aunque el UUID de Beto sea menor (ver el comentario
        # de los IDs fijos, arriba).
        for slot in slots:
            assert slot["candidatos"] == [str(ANA), str(BETO)]

    async def test_domingo_cerrado_devuelve_lista_vacia(
        self, http_client: AsyncClient, negocio_disponibilidad: str
    ) -> None:
        """Sin filas de `business_hours` no hay ventanas: lista vacia.

        No es 404 ni error: el domingo cerrado es un estado valido del
        negocio, y "no hay horarios" es la respuesta correcta.
        """
        fecha = _domingo_lejano()
        respuesta = await http_client.get(
            f"/api/v1/public/businesses/{negocio_disponibilidad}/availability",
            params={"service_id": str(SERVICE), "date": fecha.isoformat()},
        )
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["slots"] == []

    async def test_servicio_sin_profesionales_asignados_devuelve_lista_vacia(
        self, http_client: AsyncClient, negocio_disponibilidad: str
    ) -> None:
        """ "Nadie puede atenderlo" es lista vacia, no 404.

        El servicio existe y es del negocio; lo que no hay es nadie
        asignado. Ese es un estado del negocio, no un error del
        pedido: el 404 quedaria reservado para "no existe".
        """
        fecha = _lunes_lejano()
        respuesta = await http_client.get(
            f"/api/v1/public/businesses/{negocio_disponibilidad}/availability",
            params={
                "service_id": str(SERVICE_SIN_PROFESIONAL),
                "date": fecha.isoformat(),
            },
        )
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["slots"] == []

    async def test_profesional_concreto_es_el_unico_candidato(
        self, http_client: AsyncClient, negocio_disponibilidad: str
    ) -> None:
        """Con `professional_id`, cada slot tiene a ese profesional solo.

        Beto tiene `sort_order` 2, pero concreto es concreto: la
        lista no se reordena ni se completa con Ana.
        """
        fecha = _lunes_lejano()
        respuesta = await http_client.get(
            f"/api/v1/public/businesses/{negocio_disponibilidad}/availability",
            params={
                "service_id": str(SERVICE),
                "date": fecha.isoformat(),
                "professional_id": str(BETO),
            },
        )
        assert respuesta.status_code == 200, respuesta.text
        slots = respuesta.json()["slots"]
        assert slots
        for slot in slots:
            assert slot["candidatos"] == [str(BETO)]

    async def test_negocio_inexistente_responde_404(
        self, http_client: AsyncClient, negocio_disponibilidad: str
    ) -> None:
        respuesta = await http_client.get(
            "/api/v1/public/businesses/no-existe-tal-negocio/availability",
            params={"service_id": str(SERVICE), "date": _lunes_lejano().isoformat()},
        )
        assert respuesta.status_code == 404, respuesta.text

    async def test_servicio_inexistente_responde_404(
        self, http_client: AsyncClient, negocio_disponibilidad: str
    ) -> None:
        respuesta = await http_client.get(
            f"/api/v1/public/businesses/{negocio_disponibilidad}/availability",
            params={
                "service_id": str(uuid.uuid4()),
                "date": _lunes_lejano().isoformat(),
            },
        )
        assert respuesta.status_code == 404, respuesta.text

    async def test_servicio_de_otro_negocio_responde_404(
        self, http_client: AsyncClient, negocio_disponibilidad: str
    ) -> None:
        """El servicio existe, pero en `negocio-a`, no en este.

        No confirmar que existe es la decision de diseño: un 403 o un
        409 le dirian a quien pregunta que el servicio esta en otro
        negocio. El `test-business` de `conftest` ya esta commiteado
        (el fixture `http_client` lo siembra) y este servicio no es
        suyo.
        """
        respuesta = await http_client.get(
            "/api/v1/public/businesses/test-business/availability",
            params={
                "service_id": str(SERVICE),
                "date": _lunes_lejano().isoformat(),
            },
        )
        assert respuesta.status_code == 404, respuesta.text

    async def test_fecha_mal_formada_responde_422(
        self, http_client: AsyncClient, negocio_disponibilidad: str
    ) -> None:
        """`date` es `YYYY-MM-DD`; lo que no parsea es 422, no 500."""
        respuesta = await http_client.get(
            f"/api/v1/public/businesses/{negocio_disponibilidad}/availability",
            params={"service_id": str(SERVICE), "date": "15/06/2026"},
        )
        assert respuesta.status_code == 422, respuesta.text

    async def test_un_bloqueo_general_quita_la_ventana_entera(
        self,
        http_client: AsyncClient,
        negocio_disponibilidad: str,
        bloqueo_general_de_martes: dt.date,
    ) -> None:
        """Un bloqueo `professional_id NULL` cierra el negocio **entero**.

        El bloqueo cubre la mañana del martes de 09:00 a 12:00: la
        consulta de ese dia tiene que perder los 11 slots de la mañana y
        conservar los 15 de la tarde (16:00-20:00) -- 15 arranques de 30
        minutos en una ventana de 240, el mismo numero que el resto del
        archivo usa para "dia abierto".

        Es la regresion del `or_(..., professional_id.is_(None))` de
        `_load_blocked_intervals`: sin ese brazo, un cierre general se
        ignoraba en silencio y el negocio seguia vendiendo turnos en un
        dia cerrado.
        """
        respuesta = await http_client.get(
            f"/api/v1/public/businesses/{negocio_disponibilidad}/availability",
            params={
                "service_id": str(SERVICE),
                "date": bloqueo_general_de_martes.isoformat(),
            },
        )
        assert respuesta.status_code == 200, respuesta.text
        slots = respuesta.json()["slots"]
        assert len(slots) == 15

        tz = ZoneInfo(TIMEZONE)
        for slot in slots:
            local = dt.datetime.fromisoformat(slot["starts_at"]).astimezone(tz)
            assert local.hour >= 16
