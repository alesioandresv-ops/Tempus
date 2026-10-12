"""Pruebas masivas de validacion del sistema (post-Fase 10).

Tres suites que cubren el camino que se valido a mano despues de la Fase
10--pagina publica con ubicacion, configuracion del mapa en el panel y el
flujo de reserva--, para usarlas de andamio antes de escalar a la suite
completa de 1349.

**Que se siembra y por que se commitea.** Las rutas publicas
(`GET /public/businesses/{slug}`, la disponibilidad y `POST /public/bookings`)
resuelven el negocio por slug y operan en una sesion propia
(`session_scope`/`tenant_session`), que **no** pasa por el override de
dependencias de `http_client`. Por eso los negocios de las suites A y C se
siembran con el rol de DDL (`migration_database_url`) y se **commitean**: una
fila que viviera en la transaccion que el fixture `connection` revierte al
final seria invisible para el endpoint y el test devolveria 404 sin haber
probado nada (ver el docstring de `test_availability_public.py`).

La suite B, en cambio, ejercita endpoints del panel autenticados: esos usan las
dependencias que `http_client` reemplaza por la sesion del test, asi que el
negocio se crea con el alta de onboarding dentro de la misma transaccion y no
deja residuo entre corridas.

**`0,0` es "sin ubicacion", pero del lado del cliente.** La API publica
devuelve `latitude`/`longitude` tal cual esten en la fila: `null` si nunca se
setearon y `0.0` si se guardo el cero explicito. Tratar `0,0` como "sin
ubicacion" es una decision del frontend (el mapa solo aparece con coords
distintas de cero); aca se verifica que el contrato HTTP exponga ambos casos
sin esconderlos.
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
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

pytestmark = pytest.mark.integration

TIMEZONE = "America/Argentina/Buenos_Aires"
TZ = ZoneInfo(TIMEZONE)

#: Coordenadas de Paso de los Libres: el default que usa el mapa del panel
#: cuando el negocio todavia no seteo su ubicacion (`CENTRO_DEFECTO`).
LIBRES_LAT = -29.6868
LIBRES_LNG = -57.0976

LUNES = 0
DOMINGO = 6


# --------------------------------------------------------------------------- #
# Suite A: informacion publica del negocio
# --------------------------------------------------------------------------- #

SLUG_COMPLETO = "massive-completo"
SLUG_SIN_COORDS = "massive-sin-coords"
SLUG_CERO = "massive-cero"
SLUG_TRIAL = "massive-trial"

#: IDs fijos estilo v7, como el resto de los datos de test.
BUSINESS_COMPLETO = uuid.UUID("aa000000-0000-7000-8000-0000000000a1")
BUSINESS_SIN_COORDS = uuid.UUID("aa000000-0000-7000-8000-0000000000a2")
BUSINESS_CERO = uuid.UUID("aa000000-0000-7000-8000-0000000000a3")
BUSINESS_TRIAL = uuid.UUID("aa000000-0000-7000-8000-0000000000a4")

ADDRESS_COMPLETO = "Av. San Martin 123, Paso de los Libres"

#: (id, slug, nombre, status, descripcion, direccion, lat, lng)
_PERFILES_PUBLICOS = (
    (
        BUSINESS_COMPLETO,
        SLUG_COMPLETO,
        "Massive Completo",
        "active",
        "Barberia con ubicacion",
        ADDRESS_COMPLETO,
        LIBRES_LAT,
        LIBRES_LNG,
    ),
    (BUSINESS_SIN_COORDS, SLUG_SIN_COORDS, "Massive Sin Coords", "active", None, None, None, None),
    (BUSINESS_CERO, SLUG_CERO, "Massive Cero", "active", None, None, 0.0, 0.0),
    (BUSINESS_TRIAL, SLUG_TRIAL, "Massive Trial", "trial", None, None, LIBRES_LAT, LIBRES_LNG),
)


async def _sembrar_publicos(conn: AsyncConnection) -> None:
    """Siembra los cuatro negocios de la suite A, commiteados.

    Cada upsert fija el GUC a **su propio** id antes de la sentencia: la rama
    `DO UPDATE` cae en la politica `businesses_write`, que exige que
    `app.current_business_id` sea el del negocio que se esta escribiendo.
    """
    for business_id, slug, nombre, status, desc, address, lat, lng in _PERFILES_PUBLICOS:
        await conn.execute(
            text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
            {"tenant": str(business_id)},
        )
        await conn.execute(
            text(
                """
                INSERT INTO businesses
                    (id, name, slug, timezone, status, description,
                     address_text, latitude, longitude)
                VALUES (:id, :name, :slug, :tz, :status, :desc, :address, :lat, :lng)
                ON CONFLICT (slug) DO UPDATE SET
                    status = EXCLUDED.status,
                    description = EXCLUDED.description,
                    address_text = EXCLUDED.address_text,
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude
                """
            ),
            {
                "id": business_id,
                "name": nombre,
                "slug": slug,
                "tz": TIMEZONE,
                "status": status,
                "desc": desc,
                "address": address,
                "lat": lat,
                "lng": lng,
            },
        )
    await conn.execute(text("SELECT set_config(:name, '', true)"), {"name": TENANT_GUC})


@pytest_asyncio.fixture
async def publicos(migration_database_url: str) -> AsyncIterator[None]:
    owner_engine: AsyncEngine = create_async_engine(
        migration_database_url, poolclass=None, echo=False
    )
    try:
        async with owner_engine.begin() as conn:
            await _sembrar_publicos(conn)
        yield
    finally:
        await owner_engine.dispose()


class TestPublicBusinessInfo:
    """`GET /api/v1/public/businesses/{slug}`: el contrato de la pagina publica."""

    async def test_negocio_activo_devuelve_200(
        self, http_client: AsyncClient, publicos: None
    ) -> None:
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_COMPLETO}")
        assert respuesta.status_code == 200, respuesta.text

    async def test_devuelve_el_id(self, http_client: AsyncClient, publicos: None) -> None:
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_COMPLETO}")
        assert respuesta.json()["id"] == str(BUSINESS_COMPLETO)

    async def test_devuelve_el_nombre(self, http_client: AsyncClient, publicos: None) -> None:
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_COMPLETO}")
        assert respuesta.json()["name"] == "Massive Completo"

    async def test_devuelve_la_descripcion(self, http_client: AsyncClient, publicos: None) -> None:
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_COMPLETO}")
        assert respuesta.json()["description"] == "Barberia con ubicacion"

    async def test_devuelve_address_text(self, http_client: AsyncClient, publicos: None) -> None:
        """La direccion del negocio (Fase 10) viaja en la info publica."""
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_COMPLETO}")
        assert respuesta.json()["address_text"] == ADDRESS_COMPLETO

    async def test_devuelve_latitude(self, http_client: AsyncClient, publicos: None) -> None:
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_COMPLETO}")
        assert respuesta.json()["latitude"] == pytest.approx(LIBRES_LAT)

    async def test_devuelve_longitude(self, http_client: AsyncClient, publicos: None) -> None:
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_COMPLETO}")
        assert respuesta.json()["longitude"] == pytest.approx(LIBRES_LNG)

    async def test_las_claves_de_coordenadas_estan_presentes(
        self, http_client: AsyncClient, publicos: None
    ) -> None:
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_COMPLETO}")
        cuerpo = respuesta.json()
        assert "latitude" in cuerpo
        assert "longitude" in cuerpo
        assert "address_text" in cuerpo

    async def test_devuelve_timezone(self, http_client: AsyncClient, publicos: None) -> None:
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_COMPLETO}")
        assert respuesta.json()["timezone"] == TIMEZONE

    async def test_devuelve_el_contrato_publico_completo(
        self, http_client: AsyncClient, publicos: None
    ) -> None:
        """Los campos que la pagina de reserva necesita para arrancar."""
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_COMPLETO}")
        cuerpo = respuesta.json()
        for clave in (
            "id",
            "name",
            "timezone",
            "locale",
            "currency",
            "slot_interval_minutes",
            "min_lead_minutes",
        ):
            assert clave in cuerpo, clave

    async def test_sin_coordenadas_devuelve_null(
        self, http_client: AsyncClient, publicos: None
    ) -> None:
        """Nunca seteadas: `null`, que es lo que el front interpreta como "sin mapa"."""
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_SIN_COORDS}")
        cuerpo = respuesta.json()
        assert cuerpo["latitude"] is None
        assert cuerpo["longitude"] is None

    async def test_sin_direccion_devuelve_null(
        self, http_client: AsyncClient, publicos: None
    ) -> None:
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_SIN_COORDS}")
        assert respuesta.json()["address_text"] is None

    async def test_las_claves_siguen_presentes_sin_coords(
        self, http_client: AsyncClient, publicos: None
    ) -> None:
        """`null` explicito, no claves ausentes: el cliente las lee siempre."""
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_SIN_COORDS}")
        cuerpo = respuesta.json()
        assert "latitude" in cuerpo
        assert "longitude" in cuerpo

    async def test_coordenadas_cero_se_exponen(
        self, http_client: AsyncClient, publicos: None
    ) -> None:
        """`0,0` guardado vuelve como `0.0`, no como `null`.

        El contrato no esconde el cero: es el cliente el que decide tratarlo
        como "sin ubicacion" (coords validas en el mapa son distintas de cero).
        """
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_CERO}")
        cuerpo = respuesta.json()
        assert cuerpo["latitude"] == 0.0
        assert cuerpo["longitude"] == 0.0

    async def test_negocio_inexistente_responde_404(
        self, http_client: AsyncClient, publicos: None
    ) -> None:
        respuesta = await http_client.get("/api/v1/public/businesses/no-existe-massive")
        assert respuesta.status_code == 404, respuesta.text

    async def test_negocio_trial_no_se_publica(
        self, http_client: AsyncClient, publicos: None
    ) -> None:
        """Un negocio `trial` se configura pero no se publica: 404, no 200."""
        respuesta = await http_client.get(f"/api/v1/public/businesses/{SLUG_TRIAL}")
        assert respuesta.status_code == 404, respuesta.text


# --------------------------------------------------------------------------- #
# Suite B: configuracion del mapa en el panel
# --------------------------------------------------------------------------- #

URL_ALTA = "/api/v1/auth/register-business"
URL_LOGIN = "/api/v1/auth/login"
URL_CONFIG = "/api/v1/business/config/whatsapp"

EMAIL_CONFIG = "Massive.Suite@Peluqueria.com"
PASSWORD_CONFIG = "una-contrasena-larga-de-prueba"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _cuerpo_alta() -> dict[str, object]:
    return {
        "business_name": "Massive Config",
        "slug": "massive-config",
        "owner_name": "Ana Perez",
        "email": EMAIL_CONFIG,
        "password": PASSWORD_CONFIG,
        "phone": "+54 9 11 2345-6789",
        "timezone": TIMEZONE,
    }


async def _poner_tenant(session: AsyncSession, business_id: uuid.UUID) -> None:
    await session.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(business_id)},
    )


async def _guardar(
    http_client: AsyncClient,
    token: str,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
) -> object:
    """`PUT /config/whatsapp` con solo las coordenadas que se quieran mandar.

    Los campos ausentes viajan como `None`, y el servicio los interpreta como
    "conservar lo que haya": es la semantica que la suite verifica.
    """
    cuerpo: dict[str, object] = {"activo": False}
    if latitude is not None:
        cuerpo["latitude"] = latitude
    if longitude is not None:
        cuerpo["longitude"] = longitude
    return await http_client.put(URL_CONFIG, json=cuerpo, headers=_auth(token))


@pytest_asyncio.fixture
async def negocio_config(http_client: AsyncClient, session: AsyncSession) -> AsyncIterator[str]:
    """Alta + login del dueno, con el GUC del tenant ya puesto.

    El override de `http_client` entrega la sesion del test **sin** el GUC que
    `get_tenant_session` pone en produccion: sin fijarlo, la RLS devuelve cero
    filas y el panel de configuracion parece vacio aunque el alta haya ido bien
    (ver `test_fase4_panel.py`).
    """
    alta = await http_client.post(URL_ALTA, json=_cuerpo_alta())
    assert alta.status_code == 201, alta.text
    await _poner_tenant(session, uuid.UUID(str(alta.json()["business_id"])))

    login = await http_client.post(
        URL_LOGIN, json={"email": EMAIL_CONFIG, "password": PASSWORD_CONFIG}
    )
    assert login.status_code == 200, login.text
    yield login.json()["access_token"]


class TestConfiguracionMapa:
    """`GET/PUT /api/v1/business/config/whatsapp`: las coords del negocio."""

    async def test_config_inicial_esta_inactivo(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        respuesta = await http_client.get(URL_CONFIG, headers=_auth(negocio_config))
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["activo"] is False

    async def test_config_inicial_no_tiene_coordenadas(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        """Sin ubicacion guardada: `None`; el mapa arranca en el default (Libres)."""
        respuesta = await http_client.get(URL_CONFIG, headers=_auth(negocio_config))
        cuerpo = respuesta.json()
        assert cuerpo["latitude"] is None
        assert cuerpo["longitude"] is None

    async def test_guardar_coordenadas_responde_200(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        respuesta = await _guardar(
            http_client, negocio_config, latitude=LIBRES_LAT, longitude=LIBRES_LNG
        )
        assert respuesta.status_code == 200, respuesta.text

    async def test_guardar_coordenadas_las_devuelve(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        respuesta = await _guardar(
            http_client, negocio_config, latitude=LIBRES_LAT, longitude=LIBRES_LNG
        )
        cuerpo = respuesta.json()
        assert cuerpo["latitude"] == pytest.approx(LIBRES_LAT)
        assert cuerpo["longitude"] == pytest.approx(LIBRES_LNG)

    async def test_get_despues_de_guardar_devuelve_las_coordenadas(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        await _guardar(http_client, negocio_config, latitude=LIBRES_LAT, longitude=LIBRES_LNG)
        respuesta = await http_client.get(URL_CONFIG, headers=_auth(negocio_config))
        cuerpo = respuesta.json()
        assert cuerpo["latitude"] == pytest.approx(LIBRES_LAT)
        assert cuerpo["longitude"] == pytest.approx(LIBRES_LNG)

    async def test_default_de_libres_hace_round_trip(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        """Las coordenadas exactas del default del mapa (Paso de los Libres)."""
        await _guardar(http_client, negocio_config, latitude=LIBRES_LAT, longitude=LIBRES_LNG)
        respuesta = await http_client.get(URL_CONFIG, headers=_auth(negocio_config))
        assert respuesta.json()["latitude"] == LIBRES_LAT
        assert respuesta.json()["longitude"] == LIBRES_LNG

    async def test_guardar_solo_latitud_conserva_longitud(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        await _guardar(http_client, negocio_config, latitude=30.0, longitude=40.0)
        await _guardar(http_client, negocio_config, latitude=31.0)
        respuesta = await http_client.get(URL_CONFIG, headers=_auth(negocio_config))
        cuerpo = respuesta.json()
        assert cuerpo["latitude"] == 31.0
        assert cuerpo["longitude"] == 40.0

    async def test_guardar_solo_longitud_conserva_latitud(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        await _guardar(http_client, negocio_config, latitude=30.0, longitude=40.0)
        await _guardar(http_client, negocio_config, longitude=41.0)
        respuesta = await http_client.get(URL_CONFIG, headers=_auth(negocio_config))
        cuerpo = respuesta.json()
        assert cuerpo["latitude"] == 30.0
        assert cuerpo["longitude"] == 41.0

    async def test_latitud_fuera_de_rango_responde_422(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        respuesta = await _guardar(http_client, negocio_config, latitude=100.0)
        assert respuesta.status_code == 422, respuesta.text

    async def test_longitud_fuera_de_rango_responde_422(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        respuesta = await _guardar(http_client, negocio_config, longitude=200.0)
        assert respuesta.status_code == 422, respuesta.text

    async def test_latitud_limite_positivo_se_acepta(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        respuesta = await _guardar(http_client, negocio_config, latitude=90.0)
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["latitude"] == 90.0

    async def test_latitud_limite_negativo_se_acepta(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        respuesta = await _guardar(http_client, negocio_config, latitude=-90.0)
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["latitude"] == -90.0

    async def test_longitud_limite_positivo_se_acepta(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        respuesta = await _guardar(http_client, negocio_config, longitude=180.0)
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["longitude"] == 180.0

    async def test_longitud_limite_negativo_se_acepta(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        respuesta = await _guardar(http_client, negocio_config, longitude=-180.0)
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["longitude"] == -180.0

    async def test_sin_token_no_se_autoriza(self, http_client: AsyncClient) -> None:
        """Sin credenciales el panel no se toca (401/403 segun el middleware)."""
        respuesta = await http_client.get(URL_CONFIG)
        assert respuesta.status_code in (401, 403), respuesta.text

    async def test_guardar_coordenadas_no_enciende_whatsapp(
        self, http_client: AsyncClient, negocio_config: str
    ) -> None:
        """Guardar el mapa no cambia el estado de los recordatorios."""
        await _guardar(http_client, negocio_config, latitude=LIBRES_LAT, longitude=LIBRES_LNG)
        respuesta = await http_client.get(URL_CONFIG, headers=_auth(negocio_config))
        assert respuesta.json()["activo"] is False


# --------------------------------------------------------------------------- #
# Suite C: flujo de reserva publico
# --------------------------------------------------------------------------- #

SLUG_BOOKING = "massive-booking"
BUSINESS_BOOKING = uuid.UUID("bb000000-0000-7000-8000-0000000000b1")
SERVICE_BOOKING = uuid.UUID("bb000000-0000-7000-8000-0000000000b2")
PROF_BOOKING = uuid.UUID("bb000000-0000-7000-8000-0000000000b3")

DURACION = 30


def _proximo(weekday: int, hora: int) -> dt.datetime:
    """Proximo `weekday` (0=lunes) a las `hora` local del negocio, en UTC.

    Con un mes de margen: el lead time minimo del negocio es 0 por default,
    pero el margen aleja el slot de cualquier ventana de cancelacion y de la
    grilla que otro test pudo dejar ocupada.
    """
    base = dt.datetime.now(TZ).date() + dt.timedelta(days=30)
    dia = base + dt.timedelta(days=(weekday - base.weekday()) % 7)
    local = dt.datetime(dia.year, dia.month, dia.day, hora, 0, tzinfo=TZ)
    return local.astimezone(dt.UTC)


def _cuerpo_reserva(
    *,
    starts_at: dt.datetime,
    telefono: str,
    professional_id: uuid.UUID | None = PROF_BOOKING,
    service_id: uuid.UUID = SERVICE_BOOKING,
    slug: str = SLUG_BOOKING,
) -> dict[str, object]:
    return {
        "slug": slug,
        "service_id": str(service_id),
        "professional_id": None if professional_id is None else str(professional_id),
        "customer_first_name": "Ana",
        "customer_last_name": "Perez",
        "customer_phone_e164": telefono,
        "starts_at": starts_at.isoformat().replace("+00:00", "Z"),
        "ends_at": (starts_at + dt.timedelta(minutes=DURACION)).isoformat().replace("+00:00", "Z"),
        "local_date": starts_at.astimezone(TZ).date().isoformat(),
    }


async def _sembrar_booking_migration(conn: AsyncConnection) -> None:
    await conn.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(BUSINESS_BOOKING)},
    )
    await conn.execute(
        text(
            """
            INSERT INTO businesses (id, name, slug, timezone, status)
            VALUES (:id, 'Massive Booking', :slug, :tz, 'active')
            ON CONFLICT (slug) DO UPDATE SET status = 'active'
            """
        ),
        {"id": BUSINESS_BOOKING, "slug": SLUG_BOOKING, "tz": TIMEZONE},
    )


async def _sembrar_booking_app(conn: AsyncConnection) -> None:
    await conn.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(BUSINESS_BOOKING)},
    )

    # Lunes a viernes, jornada partida. El sabado y el domingo sin filas:
    # "cerrado" es la ausencia de ventana, y el test del domingo lo consulta.
    for weekday in range(5):
        for indice, (inicio, fin) in enumerate(
            ((dt.time(9, 0), dt.time(12, 0)), (dt.time(16, 0), dt.time(20, 0)))
        ):
            await conn.execute(
                text(
                    """
                    INSERT INTO business_hours
                        (business_id, weekday, window_index, start_time, end_time, is_open)
                    VALUES (:business, :weekday, :indice, :inicio, :fin, true)
                    ON CONFLICT (business_id, weekday, window_index) DO UPDATE SET
                        start_time = EXCLUDED.start_time,
                        end_time = EXCLUDED.end_time,
                        is_open = true
                    """
                ),
                {
                    "business": BUSINESS_BOOKING,
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
            VALUES (:id, :business, 'Corte', :dur, 1500.00, 'ARS')
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, duration_minutes = EXCLUDED.duration_minutes,
                price = EXCLUDED.price, currency = EXCLUDED.currency, is_active = true
            """
        ),
        {"id": SERVICE_BOOKING, "business": BUSINESS_BOOKING, "dur": DURACION},
    )

    await conn.execute(
        text(
            """
            INSERT INTO professionals (id, business_id, display_name, sort_order)
            VALUES (:id, :business, 'Profe', 1)
            ON CONFLICT (id) DO UPDATE SET
                display_name = EXCLUDED.display_name, sort_order = EXCLUDED.sort_order,
                is_active = true
            """
        ),
        {"id": PROF_BOOKING, "business": BUSINESS_BOOKING},
    )

    await conn.execute(
        text(
            """
            INSERT INTO professional_services (professional_id, service_id, business_id, is_active)
            VALUES (:prof, :serv, :business, true)
            ON CONFLICT (professional_id, service_id) DO UPDATE SET is_active = true
            """
        ),
        {"prof": PROF_BOOKING, "serv": SERVICE_BOOKING, "business": BUSINESS_BOOKING},
    )

    await conn.execute(text(f"SELECT set_config('{TENANT_GUC}', '', true)"))


@pytest_asyncio.fixture
async def negocio_booking(
    migration_database_url: str, test_database_url: str
) -> AsyncIterator[str]:
    """Negocio + agenda + servicio + profesional, commiteados.

    La limpieza de sobras va en **setup**: `POST /public/bookings` commitea con
    sesion propia, asi que los turnos creados por cada test sobreviven al
    rollback y se acumulan de corrida en corrida. Si no se limpiaran, el
    segundo intento sobre el mismo horario chocaria con la EXCLUDE.
    """
    mig_engine: AsyncEngine = create_async_engine(
        migration_database_url, poolclass=None, echo=False
    )
    app_engine: AsyncEngine = create_async_engine(test_database_url, poolclass=None, echo=False)
    try:
        async with mig_engine.begin() as conn:
            await _sembrar_booking_migration(conn)
        async with mig_engine.begin() as conn:
            await conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                {"tenant": str(BUSINESS_BOOKING)},
            )
            await conn.execute(
                text("DELETE FROM bookings WHERE business_id = :b"),
                {"b": str(BUSINESS_BOOKING)},
            )
            await conn.execute(
                text("DELETE FROM customers WHERE business_id = :b"),
                {"b": str(BUSINESS_BOOKING)},
            )
        async with app_engine.begin() as conn:
            await _sembrar_booking_app(conn)
        yield SLUG_BOOKING
    finally:
        await app_engine.dispose()
        await mig_engine.dispose()


async def _reservar(
    http_client: AsyncClient,
    *,
    starts_at: dt.datetime,
    telefono: str,
    professional_id: uuid.UUID | None = PROF_BOOKING,
    service_id: uuid.UUID = SERVICE_BOOKING,
    slug: str = SLUG_BOOKING,
) -> object:
    return await http_client.post(
        "/api/v1/public/bookings",
        json=_cuerpo_reserva(
            starts_at=starts_at,
            telefono=telefono,
            professional_id=professional_id,
            service_id=service_id,
            slug=slug,
        ),
    )


class TestBookingFlow:
    """Disponibilidad -> reserva -> gestion por token, contra Postgres real."""

    async def test_disponibilidad_de_un_lunes_devuelve_slots(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        fecha = _proximo(LUNES, 9).astimezone(TZ).date()
        respuesta = await http_client.get(
            f"/api/v1/public/businesses/{negocio_booking}/availability",
            params={"service_id": str(SERVICE_BOOKING), "date": fecha.isoformat()},
        )
        assert respuesta.status_code == 200, respuesta.text
        assert len(respuesta.json()["slots"]) > 0

    async def test_los_slots_tienen_candidatos(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        fecha = _proximo(LUNES, 9).astimezone(TZ).date()
        respuesta = await http_client.get(
            f"/api/v1/public/businesses/{negocio_booking}/availability",
            params={"service_id": str(SERVICE_BOOKING), "date": fecha.isoformat()},
        )
        for slot in respuesta.json()["slots"]:
            assert slot["candidatos"] == [str(PROF_BOOKING)]

    async def test_el_primer_slot_es_el_inicio_de_la_ventana(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        fecha = _proximo(LUNES, 9).astimezone(TZ).date()
        respuesta = await http_client.get(
            f"/api/v1/public/businesses/{negocio_booking}/availability",
            params={"service_id": str(SERVICE_BOOKING), "date": fecha.isoformat()},
        )
        primero = dt.datetime.fromisoformat(respuesta.json()["slots"][0]["starts_at"])
        local = primero.astimezone(TZ)
        assert (local.hour, local.minute) == (9, 0)

    async def test_crear_reserva_responde_201(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        respuesta = await _reservar(
            http_client, starts_at=_proximo(LUNES, 9), telefono="+54911000010"
        )
        assert respuesta.status_code == 201, respuesta.text

    async def test_la_reserva_devuelve_booking_id(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        respuesta = await _reservar(
            http_client, starts_at=_proximo(LUNES, 9), telefono="+54911000011"
        )
        assert uuid.UUID(respuesta.json()["booking_id"])

    async def test_la_reserva_devuelve_secure_token(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        respuesta = await _reservar(
            http_client, starts_at=_proximo(LUNES, 9), telefono="+54911000012"
        )
        assert respuesta.json()["secure_token"]

    async def test_la_reserva_nace_confirmada(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        respuesta = await _reservar(
            http_client, starts_at=_proximo(LUNES, 9), telefono="+54911000013"
        )
        assert respuesta.json()["status"] == "confirmed"

    async def test_la_reserva_devuelve_el_servicio(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        respuesta = await _reservar(
            http_client, starts_at=_proximo(LUNES, 9), telefono="+54911000014"
        )
        assert respuesta.json()["service_name"] == "Corte"

    async def test_la_reserva_devuelve_el_profesional(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        respuesta = await _reservar(
            http_client, starts_at=_proximo(LUNES, 9), telefono="+54911000015"
        )
        assert respuesta.json()["professional_name"] == "Profe"

    async def test_leer_la_reserva_por_token(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        creada = await _reservar(http_client, starts_at=_proximo(LUNES, 9), telefono="+54911000016")
        token = creada.json()["secure_token"]
        respuesta = await http_client.get(f"/api/v1/public/bookings/{token}")
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["status"] == "confirmed"

    async def test_cancelar_la_reserva_por_token(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        creada = await _reservar(http_client, starts_at=_proximo(LUNES, 9), telefono="+54911000017")
        token = creada.json()["secure_token"]
        respuesta = await http_client.post(f"/api/v1/public/bookings/{token}/cancel")
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["status"] == "cancelled"

    async def test_reprogramar_la_reserva_por_token(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        creada = await _reservar(http_client, starts_at=_proximo(LUNES, 9), telefono="+54911000018")
        token = creada.json()["secure_token"]
        nuevo = _proximo(LUNES, 10)
        cuerpo = {
            "new_starts_at": nuevo.isoformat().replace("+00:00", "Z"),
            "new_ends_at": (nuevo + dt.timedelta(minutes=DURACION))
            .isoformat()
            .replace("+00:00", "Z"),
            "new_duration_minutes": DURACION,
        }
        respuesta = await http_client.post(
            f"/api/v1/public/bookings/{token}/reschedule", json=cuerpo
        )
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["status"] == "confirmed"
        assert respuesta.json()["starts_at"] == cuerpo["new_starts_at"]

    async def test_servicio_inexistente_responde_404(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        respuesta = await _reservar(
            http_client,
            starts_at=_proximo(LUNES, 9),
            telefono="+54911000019",
            service_id=uuid.uuid4(),
        )
        assert respuesta.status_code == 404, respuesta.text

    async def test_slug_inexistente_responde_404(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        respuesta = await _reservar(
            http_client,
            starts_at=_proximo(LUNES, 9),
            telefono="+54911000025",
            slug="no-existe-massive",
        )
        assert respuesta.status_code == 404, respuesta.text

    async def test_doble_reserva_del_mismo_slot_responde_409(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        """El mismo horario y profesional, dos veces: la segunda es 409."""
        starts_at = _proximo(LUNES, 9)
        primera = await _reservar(http_client, starts_at=starts_at, telefono="+54911000020")
        assert primera.status_code == 201, primera.text
        segunda = await _reservar(http_client, starts_at=starts_at, telefono="+54911000021")
        assert segunda.status_code == 409, segunda.text

    async def test_reserva_en_domingo_cerrado_responde_409(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        """Sin ventana para el domingo, el motor no ofrece el slot: 409."""
        respuesta = await _reservar(
            http_client, starts_at=_proximo(DOMINGO, 9), telefono="+54911000022"
        )
        assert respuesta.status_code == 409, respuesta.text

    async def test_reserva_fuera_de_horario_responde_409(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        """Las 03:00 de un lunes quedan fuera de las ventanas del negocio."""
        respuesta = await _reservar(
            http_client, starts_at=_proximo(LUNES, 3), telefono="+54911000023"
        )
        assert respuesta.status_code == 409, respuesta.text

    async def test_reserva_sin_profesional_asigna_uno(
        self, http_client: AsyncClient, negocio_booking: str
    ) -> None:
        """En "cualquier profesional" el backend elige al unico disponible."""
        respuesta = await _reservar(
            http_client,
            starts_at=_proximo(LUNES, 9),
            telefono="+54911000024",
            professional_id=None,
        )
        assert respuesta.status_code == 201, respuesta.text
        assert respuesta.json()["professional_id"] == str(PROF_BOOKING)
