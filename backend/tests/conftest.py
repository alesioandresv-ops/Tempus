"""Fixtures compartidas.

Dos decisiones que conviene tener presentes al leer los tests:

1. **Los tests que tocan la base usan `tempus_test`, nunca `tempus`.** La URL sale
   de `TEST_DATABASE_URL` y, si no esta, de `DATABASE_URL`. Escribir en la base de
   desarrollo desde un test es la forma mas rapida de perder un dia de trabajo, asi
   que el default es deliberadamente conservador: si no hay URL de test, los tests
   de integracion fallan con un mensaje claro en vez de conectarse a lo que haya.

2. **Los datos de prueba se siembran por el rol con DDL.** `tempus_owner` esta
   sujeto a RLS por `FORCE ROW LEVEL SECURITY`, igual que la app. Sembrar exige
   poner el GUC de tenant en cada `INSERT`, que es lo que hace `_seed_tenant`.
   Apareceria mas comodo un rol con `BYPASSRLS`, pero ese rol seria un
   equivalente de superusuario en manos de los tests, que es exactamente el tipo
   de atajo que despues nadie recuerda por que existia.
"""

from __future__ import annotations

import os
import pathlib
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.db.session import TENANT_GUC
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine

#: Archivo que escribe `preparar_base_test.py`.
ENV_TESTING = pathlib.Path(__file__).resolve().parents[1] / ".env.testing"


def _cargar_env_testing() -> None:
    """Carga `backend/.env.testing` en el entorno, sin pisar lo que ya este.

    Existe por una razon concreta. Los tests de integracion, concurrencia y
    tenancy **se saltan en silencio** si `TEST_DATABASE_URL` no esta definido: no
    fallan, aparecen como `s`. Un `pytest` que dice "909 passed" sin mencionar los
    167 que no corrieron es la clase de verde que hace creer que algo ya esta
    probado cuando no lo esta--y en este proyecto, exactamente esos 167 son los
    que sostienen la RLS, la exclusion anti-doble-reserva y el aislamiento entre
    negocios.

    Con este archivo, `python preparar_base_test.py` deja el `pytest` siguiente
    completo. Que se lea aqui y no en un plugin es a proposito: `conftest.py` se
    importa antes de que cualquier test o fixture mire las variables, y no agrega
    una dependencia.

    **Nunca pisa una variable que ya existe.** La del shell gana sobre el archivo:
    quien exporta `TEST_DATABASE_URL` a mano para apuntar a otra base--un caso
    real en la maquina de desarrollo-- no debe que el archivo lo deshaga.
    """
    if not ENV_TESTING.is_file():
        return

    for linea in ENV_TESTING.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        if clave and clave not in os.environ:
            os.environ[clave] = valor


_cargar_env_testing()


def _apuntar_la_app_a_la_base_de_test() -> None:
    """Fuerza `DATABASE_URL` a la base de tests, aunque `.env` diga otra cosa.

    **Este es el arreglo de un agujero real, no una comodidad de tests.** La app que
    se levanta dentro de pytest--la del fixture `http_client`-- lee su URL de
    `DATABASE_URL`, y `backend/.env` la tiene apuntando a la base de **desarrollo**.
    Los tests de integracion del router escribian en `tempus_test` y leian de
    `tempus`: el login insertaba un usuario y la app no lo encontraba, el test
    recibia 401, y los casos que **esperaban** 401 pasaban por el motivo equivocado.
    Cuatrocientas lineas de contrato HTTP verdes sin haber probado nada.

    Peor que el falso verde es lo que habilita: cualquier test de router que
    escribiera de verdad--una reprogramacion, un alta de usuario-- habria tocado los
    datos de desarrollo. La suite no puede tener ese permiso.

    Por eso no es "no pisar lo que ya este", como en `_cargar_env_testing`: ahi la
    variable del shell gana porque el archivo solo trae defaults. Aca el objetivo
    es el contrario--**impedir** que la sesion de tests pueda alcanzar la base de
    desarrollo-- y el unico que decide a que base se apuntan es `TEST_DATABASE_URL`.
    """
    test_url = os.environ.get("TEST_DATABASE_URL")
    if not test_url:
        # Sin base de test no hay nada que apuntar: los tests que la necesitan se
        # van a saltar solos con el mensaje de `_require_test_database_url`.
        return
    if os.environ.get("DATABASE_URL") != test_url:
        os.environ["DATABASE_URL"] = test_url
    from app.core.config import get_settings

    get_settings.cache_clear()


os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-64-chars-long-xxxx-xxxx-xxxx-xxxx-xxxx-xxxx-xxxx")
os.environ.setdefault("ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
os.environ.setdefault("SCHEDULER_TICK_SECRET", "test-scheduler-secret-32-chars-long")
_apuntar_la_app_a_la_base_de_test()

#: Tenants de prueba. IDs fijos para que un fallo se pueda reproducir a mano
#: copiando el `psql` de abajo. El nibble de version v7 esta a proposito: el
#: default de `uuid4()` en el resto del codigo de test seria v4, y aqui se quiere
#: probar el mismo camino que usa la app.
BUSINESS_A = uuid.UUID("11111111-1111-7111-8111-111111111111")
BUSINESS_B = uuid.UUID("22222222-2222-7222-8222-222222222222")
#: Tercer negocio, con el slug `test-business` que usan los tests de integracion de
#: bookings. Se siembra aqui y no en el test por una razon concreta: desde
#: `0012_businesses_insert_policy`, insertar en `businesses` es una operacion
#: **de onboarding** y corre con el rol de DDL. `tempus_app` no tiene permiso de
#: INSERT sobre la tabla, y no por accidente: crear un tenant es la unica operacion
#: del producto que necesita mas privilegios que operar dentro de uno.
#:
#: Los tests que necesitan un servicio o un profesional propios los siguen creando
#: en su propia transaccion--esas tablas son del tenant y el rol de app si puede
#: escribir en ellas con el GUC puesto. Lo unico que no puede hacer desde la sesion de
#: la app es el negocio, y el test tiene que reflejar esa frontera en vez de
#: esquivarla con un rol mas permisivo.
BUSINESS_C = uuid.UUID("cccccccc-cccc-7ccc-8ccc-cccccccccccc")
CUSTOMER_A = uuid.UUID("33333333-3333-7333-8333-333333333333")
CUSTOMER_B = uuid.UUID("44444444-4444-7444-8444-444444444444")
SERVICE_A = uuid.UUID("55555555-5555-7555-8555-555555555555")
SERVICE_B = uuid.UUID("66666666-6666-7666-8666-666666666666")
PROFESSIONAL_A = uuid.UUID("77777777-7777-7777-8777-777777777777")
PROFESSIONAL_A2 = uuid.UUID("99999999-9999-7999-8999-999999999998")
PROFESSIONAL_B = uuid.UUID("88888888-8888-7888-8888-888888888888")


def _require_test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail(
            "TEST_DATABASE_URL no esta definido. Los tests de integracion necesitan "
            "una base propia: no se conectan a la de desarrollo por default a "
            "proposito.\n"
            "La forma facil es dejar una base de tests preparada, y no tener que "
            "exportar nada:\n"
            "  python preparar_base_test.py\n"
            "Eso escribe `backend/.env.testing`, que este conftest carga solo.\n"
            "Si preferis las variables en el shell:\n"
            "  $env:TEST_DATABASE_URL = "
            "'postgresql+asyncpg://tempus_app:<pw>@127.0.0.1:5433/tempus_test'\n"
            "Y las migraciones, con el rol de DDL:\n"
            "  $env:DATABASE_MIGRATION_URL = "
            "'postgresql+asyncpg://tempus_owner:<pw>@127.0.0.1:5433/tempus_test'\n"
            "  python -m alembic upgrade head\n"
            "\n"
            f"({'esta' if ENV_TESTING.is_file() else 'NO esta'} presente: "
            f"{ENV_TESTING})"
        )
    return url


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """URL de la base de tests. Sesion-scoped porque el pool es caro."""
    return _require_test_database_url()


@pytest.fixture(scope="session")
def migration_database_url() -> str:
    """URL con el rol de DDL, para sembrar.

    Sale de `DATABASE_MIGRATION_URL` porque es el mismo rol que usan las
    migraciones. Si no esta, se cae a `TEST_DATABASE_URL`, que en un entorno bien
    armado ya apunta a la base de test.
    """
    return os.environ.get("DATABASE_MIGRATION_URL") or _require_test_database_url()


@pytest_asyncio.fixture(scope="session")
async def engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    """Engine de la app: mismo rol, mismo RLS que en produccion.

    A proposito se usa `tempus_app` y no un rol de pruebas sin RLS. Un test que
    pasa contra un rol privilegiado no prueba nada sobre el aislamiento entre
    tenants, que es justo lo que se quiere verificar.
    """
    engine = create_async_engine(test_database_url, poolclass=None, echo=False)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def connection(engine: AsyncEngine, seeded: None) -> AsyncIterator[AsyncConnection]:
    """Conexion con rollback al final.

    Se usa una transaccion por test en vez de borrar tablas entre tests: no deja
    residuos si un test falla a mitad, y no depende del orden de ejecucion.

    Depende de `seeded` a proposito. Hacer que cada test pida los datos
    explicitamente es una opcion que se olvida: un test sin el fixture pasa con
    una tabla vacia y falla con `rowcount == 0`, que no dice nada sobre RLS.

    **El rollback se hace solo si la transaccion sigue viva.** Cuando un test levanta
    una excepcion a mitad de camino, el cierre del fixture `session` ya desasociÃ³ la
    transaccion--SQLAlchemy la marca como cerrada-- y el `rollback` final la
    encuentra en ese estado. Con `filterwarnings = error` ese `SAWarning` deja de
    ser una nota y pasa a ser un error, asi que el test se reporta como fallido en el
    *teardown* cuando lo que fallo fue otra cosa. Peor: el error del teardown pisa al
    fallo real en el resumen, y el sintoma que uno ve es "la transaccion ya estaba
    desasociada", que no dice nada del negocio que se estaba probando.

    Se pregunta `is_active` en vez de tragarse la excepcion porque una transaccion
    que no quedo activa significa que *alguien* la cerro, y ese alguien--el cierre de
    la sesion-- ya hizo lo que tenia que hacer. No es un fallo que haya que reportar.
    """
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            yield conn
        finally:
            if transaction.is_active:
                await transaction.rollback()


@pytest_asyncio.fixture
async def seeded(migration_database_url: str) -> AsyncIterator[None]:
    """Dos negocios con un cliente, un servicio y un profesional cada uno.

    Se siembra con el rol de DDL y un GUC por tenant, porque `FORCE RLS` alcanza
    tambien al dueno de las tablas.

    El seed es idempotente (`ON CONFLICT DO NOTHING`) y **commitea de verdad**:
    son datos de referencia compartidos entre tests, no estado de un test. Por eso
    vive en un engine aparte en vez de en la transaccion que se revierte.
    """
    owner_engine = create_async_engine(migration_database_url, poolclass=None, echo=False)
    try:
        async with owner_engine.begin() as conn:
            await _seed_tenants(conn)
        yield
    finally:
        await owner_engine.dispose()


async def _set_tenant(conn: AsyncConnection, business_id: uuid.UUID) -> None:
    await conn.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(business_id)},
    )


@pytest.fixture
def set_tenant(connection: AsyncConnection):
    """Fija el GUC de tenant para el resto de la transaccion del test.

    Se expone como fixture y no como helper suelto porque "usar el negocio de
    prueba" son dos pasos inseparables: pedir el fixture `business_c` **y** decir
    que se opera como ese tenant. Si se olvida el segundo, el test falla con
    `new row violates row-level security policy`, que no dice "te faltÃ³ el
    `set_config`" sino "tu politica no deja escribir".

    Va antes de cada `INSERT` de tabla de tenant, porque las politicas miran el
    GUC en el momento de la sentencia y no despues.
    """

    async def _fijar(business_id: uuid.UUID) -> None:
        await _set_tenant(connection, business_id)

    return _fijar


async def _seed_tenants(conn: AsyncConnection) -> None:
    """Crea el set minimo de filas que los tests de tenancy necesitan."""
    await conn.execute(
        text(
            """
            INSERT INTO businesses (id, name, slug, timezone)
            VALUES
                (:a, 'Negocio A', 'negocio-a', 'America/Argentina/Buenos_Aires'),
                (:b, 'Negocio B', 'negocio-b', 'America/Argentina/Buenos_Aires'),
                (:c, 'Test Business', 'test-business', 'America/Argentina/Buenos_Aires')
            ON CONFLICT (slug) DO NOTHING
            """
        ),
        {"a": BUSINESS_A, "b": BUSINESS_B, "c": BUSINESS_C},
    )

    for (
        business_id,
        customer_id,
        service_id,
        professional_id,
        second_professional,
        first,
        last,
        suffix,
    ) in (
        (
            BUSINESS_A,
            CUSTOMER_A,
            SERVICE_A,
            PROFESSIONAL_A,
            PROFESSIONAL_A2,
            "Ana",
            "Perez",
            "a",
        ),
        (BUSINESS_B, CUSTOMER_B, SERVICE_B, PROFESSIONAL_B, None, "Beto", "Gomez", "b"),
    ):
        # `set_config(..., true)` y no `SET LOCAL ... = :param`: `SET` no acepta
        # parametros bindeados, y el error es un `syntax error at or near "$1"`
        # que no tiene nada que ver con lo que se esta probando.
        await _set_tenant(conn, business_id)
        await conn.execute(
            text(
                """
                INSERT INTO customers (id, business_id, first_name, last_name, phone_e164)
                VALUES (:id, :business_id, :first, :last, :phone)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": customer_id,
                "business_id": business_id,
                "first": first,
                "last": last,
                "phone": f"+54911000000{suffix}",
            },
        )
        await conn.execute(
            text(
                """
                INSERT INTO services (id, business_id, name, duration_minutes, price, currency)
                VALUES (:id, :business_id, 'Corte', 30, 1000, 'ARS')
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": service_id, "business_id": business_id},
        )
        await conn.execute(
            text(
                """
                INSERT INTO professionals (id, business_id, display_name)
                VALUES (:id, :business_id, :display)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": professional_id,
                "business_id": business_id,
                "display": f"Profesional {suffix.upper()}",
            },
        )
        if second_professional is not None:
            # Un segundo profesional del mismo negocio. Lo necesita el test que
            # verifica que la EXCLUDE es por profesional y no global: sin el, la
            # unica forma de tener "otro" profesional seria pedir uno de otro
            # negocio, y la FK compuesta lo rechaza antes de llegar a la EXCLUDE.
            await conn.execute(
                text(
                    """
                    INSERT INTO professionals (id, business_id, display_name)
                    VALUES (:id, :business_id, :display)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": second_professional,
                    "business_id": business_id,
                    "display": "Profesional A2",
                },
            )
    await conn.execute(text("SELECT set_config(:name, '', true)"), {"name": TENANT_GUC})


@pytest_asyncio.fixture
async def session(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """`AsyncSession` sobre la conexion transaccional del test.

    La sesion **no** abre su propia transaccion: se apoya en la del fixture
    `connection`. Asi el rollback final deshace tambien lo que escribio el test.
    """
    session = AsyncSession(connection, expire_on_commit=False, autoflush=False)
    try:
        yield session
    finally:
        await session.close()


@pytest_asyncio.fixture
async def http_client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP asincrono para los routers, sobre la **misma** sesion del test.

    Que el router use la sesion del test no es una comodidad: es la unica forma de
    que vea lo que el test acaba de sembrar. El fixture `connection`--del que sale
    `session`-- abre una transaccion y la deshace al final, asi que sus filas nunca
    se comitean. Si el app abriera su propia sesion--que es lo que hacia antes de
    este override--, miraria otra conexion y no veria nada de lo sembrado: el login
    devolvia 401 y los tests que **esperaban** 401 pasaban sin haber probado el
    camino del acierto.

    El override es por dependencia, no por URL, asi que el router se ejercita tal
    cual va a produccion: mismos handlers, mismo codigo de error, mismo
    `session_scope`. Lo unico que cambia es de donde sale la conexion.

    `get_tenant_session` se overridea tambien, y con el mismo criterio: los routers
    de negocio la reciben por `Depends`, y sin esto un endpoint con token de
    negocio leeria de la base de la app en vez de la del test. La sesion que se
    pasa ya tiene el GUC del tenant puesto por quien la creo.
    """
    from collections.abc import AsyncIterator as _AsyncIterator

    from app.api.dependencies import get_session, get_tenant_session
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    async def _usar_la_sesion_del_test() -> _AsyncIterator[AsyncSession]:
        yield session

    settings = get_settings()
    app = create_app(settings)
    app.dependency_overrides[get_session] = _usar_la_sesion_del_test
    app.dependency_overrides[get_tenant_session] = _usar_la_sesion_del_test

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def _motor_de_limpieza(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    """Engine para los fixtures de limpieza.

    Sesion-scoped a proposito: los fixtures de limpieza corren en cada test y
    construir un engine por test es trabajo que no hace falta--`poolclass=None` igual
    abre una conexion nueva por uso, asi que el pool no es lo que se esta saveando.

    Usa la misma URL que `engine`, con el rol de la app. `tempus_app` puede borrar de
    `rate_limit_buckets`--hace falta el `INSERT` y el `UPDATE` de `rate_limit_hit` y el
    `DELETE` esta concedido junto-- asi que no hace falta traer el rol de DDL, que
    pondria una exigencia nueva--`DATABASE_MIGRATION_URL`-- sobre **todos** los tests,
    incluso los unitarios que no tocan la base.
    """
    motor = create_async_engine(test_database_url, poolclass=None)
    try:
        yield motor
    finally:
        await motor.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _cubos_de_rate_limit_limpios(
    _motor_de_limpieza: AsyncEngine,
) -> AsyncIterator[None]:
    """Deja `rate_limit_buckets` vacia antes de cada test.

    Hace falta desde que `_enforce_rate_limit` usa su propia transaccion. Antes el
    hit vivia en la transaccion del test--la que se deshace al final-- y no quedaba
    nada; ahora se commitea a proposito, porque si no el intento fallido se perdia
    con el rollback del 401 y el limitador no limitaba.

    El costo de que se commitee es que el estado **pasa de un test al siguiente**:
    sin este fixture, el test que agota el cubo de una IP deja los cinco hits
    puestos y el test siguiente, que hace login con la misma IP que usa
    `ASGITransport`--siempre `127.0.0.1`-- arranca ya cerca del limite y falla por un
    motivo que no tiene con lo que prueba. Por eso borra **antes**, y no despues: si
    un test fallara a mitad de camino, un borrado posterior no se ejecutaria y
    contaminaria al siguiente igual.
    """
    async with _motor_de_limpieza.begin() as conn:
        await conn.execute(text("DELETE FROM rate_limit_buckets"))
    yield


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Saltea los tests de base si no hay `TEST_DATABASE_URL`.

    Un `pytest` sin base de datos deberia correr igual los tests de unidad y
    avisar que se saltearon los de integracion, no reventar con un error de
    conexion a mitad de la sesion.
    """
    if os.environ.get("TEST_DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL no definido: se requiere PostgreSQL real")
    for item in items:
        if (
            "integration" in item.keywords
            or "tenancy" in item.keywords
            or "concurrency" in item.keywords
        ):
            item.add_marker(skip)


@pytest.fixture
def business_a() -> uuid.UUID:
    return BUSINESS_A


@pytest.fixture
def business_b() -> uuid.UUID:
    return BUSINESS_B


@pytest.fixture
def business_c() -> uuid.UUID:
    """Negocio `test-business`, sembrado por el rol de onboarding.

    Es el que usan los tests de integracion de bookings, que crean su propio
    servicio y profesional pero no pueden crear el negocio: insertar en
    `businesses` es una operacion de onboarding. Ver `BUSINESS_C`.
    """
    return BUSINESS_C


@pytest.fixture
def customer_a() -> uuid.UUID:
    return CUSTOMER_A


@pytest.fixture
def customer_b() -> uuid.UUID:
    return CUSTOMER_B


@pytest.fixture
def service_a() -> uuid.UUID:
    return SERVICE_A


@pytest.fixture
def professional_a() -> uuid.UUID:
    return PROFESSIONAL_A


@pytest.fixture
def professional_b() -> uuid.UUID:
    return PROFESSIONAL_B


def pytest_configure(config: pytest.Config) -> None:
    """Registra los marcadores que usan los tests, y que `--strict-markers` exige."""
    for marker in ("integration", "concurrency", "tenancy"):
        config.addinivalue_line("markers", f"{marker}: prueba contra PostgreSQL real")
