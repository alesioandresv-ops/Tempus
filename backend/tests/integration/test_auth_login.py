"""El login de negocio con `business_slug` como desambiguador, y lo que no deja ver.

El caso que motiva el campo es legitimo y simple: una persona que trabaja para dos
negocios tiene el mismo email en los dos, y sin algo que diga cual es "este" negocio,
el login no puede elegir. Elegir "el primero" seria entrar a un tenant arbitrario, que
es justo el dano que el ADR-0010 quiere cerrar; asi que sin slug el login falla.

Pero agregar un campo que diga "a que tenant entro" en un endpoint que antes no lo
aceptaba merece los mismos mirones que un parametro de tenant. Por eso estos tests
miden las dos mitades:

1. **Que sirva para desambiguar**-- que con dos membresias y el slug correcto entre, y
   que el `tid` del token sea el del negocio pedido.
2. **Que no sirva para nada mas**-- que no elija un negocio sin membresia verificada,
   que no saltee la verificacion de la contrasena, y que todo lo que falla responda
   con el mismo 401.

La segunda mitad es la importante. Un desambiguador que se puede usar para recorrer
tenants probando nombres seria peor que la ambiguedad que vino a resolver.

**Los emails de estos tests usan dominios reales a proposito.** `BusinessLoginRequest`
declara `email: EmailStr`, y `EmailStr` rechaza los dominios de uso especial (`.test`,
`.invalid`, `.localhost`, `.example`). Con `@negocio.test` el endpoint responderia 422
en vez de 200 --o 401-- y todos los tests de este archivo fallarian por el mismo
motivo, sin que ninguno tocara el codigo que estaba probando: la validacion, que es
correcta, se comia la respuesta.

**Cada test usa un email distinto** porque el cubo de rate limit por email vive en la
base de verdad, no en la transaccion del test. Dos tests con el mismo email se
contarian los intentos del otro.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from app.core.config import get_settings
from app.core.security import hash_password, reset_password_hash_cache
from app.db.session import TENANT_GUC
from app.models.enums import BusinessUserRole
from app.modules.auth.tokens import decode_access_token
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PASSWORD = "una-contrasena-larga-y-unica"

#: El detalle de un 401 de credenciales. Unico para todos los fallos, y la razon por la
#: que un slug equivocado no puede tener su propio mensaje.
DETALLE_401 = "Credenciales invalidas."

#: Slugs que existen en la base del seed (`_seed_tenants`) y los que no.
SLUG_A = "negocio-a"
SLUG_B = "negocio-b"
SLUG_C = "test-business"
SLUG_INEXISTENTE = "no-existe-jamas"


@pytest.fixture(autouse=True)
def argon2_rapido() -> Iterator[None]:
    """Argon2 con coste minimo, restaurado al final.

    El mismo trick que usan `test_auth_service.py` y `test_security.py`. Autouse
    porque es una condicion de todo el archivo, no de un test: pedirlo en cada
    firma es una forma de que se olvide en el test que mas lo necesitaba.
    """
    settings = get_settings()
    original = (
        settings.argon2_time_cost,
        settings.argon2_memory_cost,
        settings.argon2_parallelism,
    )
    settings.argon2_time_cost = 1
    settings.argon2_memory_cost = 8
    settings.argon2_parallelism = 1
    reset_password_hash_cache()
    try:
        yield
    finally:
        (
            settings.argon2_time_cost,
            settings.argon2_memory_cost,
            settings.argon2_parallelism,
        ) = original
        reset_password_hash_cache()


async def _tenant(session: AsyncSession, business_id: uuid.UUID | None) -> None:
    """Pone o limpia el GUC de tenant.

    Se limpia con `''` y no con un UUID cualquiera, porque es `NULLIF(current_setting
    (...), '')` lo que hace que la RLS niegue en vez de comparar contra basura.
    """
    await session.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(business_id) if business_id is not None else ""},
    )


async def _crear_miembro(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    email: str,
    password: str = PASSWORD,
    role: BusinessUserRole = BusinessUserRole.ADMIN,
) -> uuid.UUID:
    """Inserta un miembro de negocio con el **rol de la app** y el GUC puesto.

    Se inserta desde la sesion del test, que corre como `tempus_app`, y no con el rol
    de DDL. Es a proposito: asi el test verifica que el login funciona contra filas que
    la aplicacion misma no podria crear de otra forma, y de paso ejercita el `INSERT`
    con `WITH CHECK` de la politica. Todo cae en la transaccion del test.
    """
    await _tenant(session, business_id)
    user_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO business_users "
            "(id, business_id, email, password_hash, full_name, role, status) "
            "VALUES (:id, :business_id, :email, :hash, ' Persona', :role, 'active')"
        ),
        {
            "id": user_id,
            "business_id": business_id,
            "email": email,
            "hash": hash_password(password),
            "role": str(role),
        },
    )
    await session.flush()
    return user_id


async def _miembros_en_dos_negocios(
    session: AsyncSession, business_a: uuid.UUID, business_b: uuid.UUID, email: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """El mismo email, miembro activo de A y de B. Devuelve los dos `user_id`."""
    en_a = await _crear_miembro(session, business_id=business_a, email=email)
    en_b = await _crear_miembro(session, business_id=business_b, email=email)
    return en_a, en_b


async def _login(http_client: AsyncClient, **cuerpo: object) -> tuple[int, dict[str, object]]:
    """POST de login y devuelve `(status, cuerpo)`.

    El helper existe para que los tests que comparan 401 comparen **respuestas
    completas** y no un `status_code` suelto: lo que hay que comprobar es que el
    cliente no puede distinguir un motivo de otro, y eso vive en el body.
    """
    respuesta = await http_client.post("/api/v1/auth/login", json=dict(cuerpo))
    try:
        return respuesta.status_code, respuesta.json()
    except ValueError:  # pragma: no cover - solo si la respuesta no fuera JSON
        return respuesta.status_code, {}


# --------------------------------------------------------------------------- #
# Que sirva para desambiguar
# --------------------------------------------------------------------------- #


class TestElSlugDesambigua:
    """Con el mismo email en dos negocios, el slug decide a cual se entra."""

    async def test_entra_al_negocio_del_slug(
        self,
        http_client: AsyncClient,
        session: AsyncSession,
        business_a: uuid.UUID,
        business_b: uuid.UUID,
    ) -> None:
        """Dos membresias, el slug de A: el token sale con el `tid` de A."""
        email = "desambigua-a@negocio.com.ar"
        en_a, _ = await _miembros_en_dos_negocios(session, business_a, business_b, email)

        status, cuerpo = await _login(
            http_client, email=email, password=PASSWORD, business_slug=SLUG_A
        )

        assert status == 200, cuerpo
        principal = decode_access_token(str(cuerpo["access_token"]))
        assert principal.business_id == business_a
        assert principal.user_id == en_a

    async def test_el_slug_elige_el_otro_negocio_y_no_el_primero(
        self,
        http_client: AsyncClient,
        session: AsyncSession,
        business_a: uuid.UUID,
        business_b: uuid.UUID,
    ) -> None:
        """El slug de B entra a B.

        Importa por lo que **no** hace: si el codigo ignorara el slug y tomara la
        primera membresia, este test daria 200 con el `tid` de A y pasaria el de A.
        Comparar los dos lados es lo que lo distingue.
        """
        email = "desambigua-b@negocio.com.ar"
        _, en_b = await _miembros_en_dos_negocios(session, business_a, business_b, email)

        status, cuerpo = await _login(
            http_client, email=email, password=PASSWORD, business_slug=SLUG_B
        )

        assert status == 200, cuerpo
        principal = decode_access_token(str(cuerpo["access_token"]))
        assert principal.business_id == business_b
        assert principal.user_id == en_b

    async def test_sin_slug_no_se_adivina(
        self,
        http_client: AsyncClient,
        session: AsyncSession,
        business_a: uuid.UUID,
        business_b: uuid.UUID,
    ) -> None:
        """Dos membresias y ningun slug: 401. No "el primero", no una eleccion."""
        email = "desambigua-sin-slug@negocio.com.ar"
        await _miembros_en_dos_negocios(session, business_a, business_b, email)

        status, cuerpo = await _login(http_client, email=email, password=PASSWORD)

        assert status == 401, cuerpo
        assert cuerpo["detail"] == DETALLE_401

    async def test_el_slug_se_ignora_habiendo_una_sola_membresia(
        self,
        http_client: AsyncClient,
        session: AsyncSession,
        business_a: uuid.UUID,
    ) -> None:
        """Una membresia y un slug equivocado: entra igual.

        A proposito, y es la decision que mas conviene que quede escrita: cuando el
        email ya determina el tenant, el slug no puede cambiar el resultado, asi que no
        se valida. Exigir que coincidia seria un 401 a alguien que tiene las
        credenciales correctas solo por escribir mal la URL.
        """
        email = "un-negocio-ignora-slug@negocio.com.ar"
        await _crear_miembro(session, business_id=business_a, email=email)

        status, cuerpo = await _login(
            http_client, email=email, password=PASSWORD, business_slug=SLUG_INEXISTENTE
        )

        assert status == 200, cuerpo
        principal = decode_access_token(str(cuerpo["access_token"]))
        assert principal.business_id == business_a

    async def test_el_slug_se_canoniza_como_el_alta(
        self,
        http_client: AsyncClient,
        session: AsyncSession,
        business_a: uuid.UUID,
        business_b: uuid.UUID,
    ) -> None:
        """El slug se canoniza antes de comparar: mayusculas y acentos dan lo mismo.

        El navegador pone en la barra de direcciones lo que el usuario escribio, no lo
        que quedo guardado. Si el login comparara crudo, `Negocio-A` fallaria donde
        `negocio-a` entra, y el desambiguador seria una fuente desupport tickets.
        """
        email = "desambigua-canonico@negocio.com.ar"
        await _miembros_en_dos_negocios(session, business_a, business_b, email)

        status, cuerpo = await _login(
            http_client, email=email, password=PASSWORD, business_slug="  Negocio-A  "
        )

        assert status == 200, cuerpo
        assert decode_access_token(str(cuerpo["access_token"])).business_id == business_a


# --------------------------------------------------------------------------- #
# Que no sirva para nada mas
# --------------------------------------------------------------------------- #


class TestElSlugNoAbreUnTenant:
    """Lo que el slug **no** puede hacer, que es la mitad que importa."""

    async def test_no_elige_un_negocio_sin_membresia_verificada(
        self,
        http_client: AsyncClient,
        session: AsyncSession,
        business_a: uuid.UUID,
        business_b: uuid.UUID,
        business_c: uuid.UUID,
    ) -> None:
        """El slug de un tercero, que no tiene a esta persona, no entra.

        Este es el test que separa "desambiguador" de "selector de tenant". El email
        esta en A y en B; se manda el slug de C, que **existe**. Un `business_id` por
        parametro daria 200 con el `tid` de C, y con el slug tambien daria lo mismo si
        la implementacion buscara el negocio antes que las membresias. Aqui se verifica
        que no: C no esta entre las filas verificadas, asi que no hay nada que elegir.
        """
        email = "tercero@negocio.com.ar"
        await _miembros_en_dos_negocios(session, business_a, business_b, email)

        status, cuerpo = await _login(
            http_client, email=email, password=PASSWORD, business_slug=SLUG_C
        )

        assert status == 401, (
            f"el slug de un negocio sin membresia devolvio {status}: el slug esta "
            "eligiendo tenants, no desambiguando"
        )
        assert cuerpo["detail"] == DETALLE_401

    async def test_no_salta_la_verificacion_de_la_contrasena(
        self,
        http_client: AsyncClient,
        session: AsyncSession,
        business_a: uuid.UUID,
        business_b: uuid.UUID,
    ) -> None:
        """Slug correcto y contrasena incorrecta: 401.

        El orden de las operaciones--verificar primero, desambiguar despues-- es lo
        que hace que esto falle. Si se desambiguara antes, el slug seria una forma de
        preguntar "este email es miembro del negocio X" sin tener la contrasena.
        """
        email = "sin-contrasena@negocio.com.ar"
        await _miembros_en_dos_negocios(session, business_a, business_b, email)

        status, cuerpo = await _login(
            http_client, email=email, password="no-es-la-contrasena", business_slug=SLUG_A
        )

        assert status == 401, cuerpo
        assert cuerpo["detail"] == DETALLE_401

    async def test_todas_las_causas_dan_el_mismo_401(
        self,
        http_client: AsyncClient,
        session: AsyncSession,
        business_a: uuid.UUID,
        business_b: uuid.UUID,
    ) -> None:
        """Cinco motivos de fallo, un solo cuerpo de respuesta.

        Si un motivo tuviera su propio `detail` o su propio `type`, la respuesta
        confirmaria al atacante que el slug existe pero no es suyo, o que el email esta
        en el negocio equivocado. El log separa los motivos; la respuesta no.

        **Cinco, y no mas, porque cinco es el limite por IP** (`rate_limit_login_per_ip`)
        y el limitador cuenta los intentos, no solo los fallidos. Un sexto motivo
        devolveria 429 en vez de 401 y el test pasaria por el motivo equivocado; si hace
        falta uno mas, partir en dos tests con emails distintos.
        """
        email = "indistinguible@negocio.com.ar"
        await _miembros_en_dos_negocios(session, business_a, business_b, email)

        motivos: dict[str, dict[str, object]] = {
            "sin_slug": {"email": email, "password": PASSWORD},
            "slug_de_otro": {"email": email, "password": PASSWORD, "business_slug": SLUG_C},
            "slug_inexistente": {
                "email": email,
                "password": PASSWORD,
                "business_slug": SLUG_INEXISTENTE,
            },
            "contrasena_mala": {"email": email, "password": "no-es-la-contrasena"},
            "email_inexistente": {"email": "nadie-aqui@negocio.com.ar", "password": PASSWORD},
        }

        cuerpos: dict[str, tuple[int, dict[str, object]]] = {}
        for nombre, cuerpo in motivos.items():
            status, respuesta = await _login(http_client, **cuerpo)
            cuerpos[nombre] = (status, respuesta)
            assert status == 401, f"{nombre} devolvio {status} en vez de 401"

        referencia = cuerpos["sin_slug"]
        for nombre, respuesta in cuerpos.items():
            # `instance` y `trace_id` son por peticion, no por motivo: no se comparan.
            limpio = {k: v for k, v in respuesta[1].items() if k not in ("instance", "trace_id")}
            assert limpio == {
                k: v for k, v in referencia[1].items() if k not in ("instance", "trace_id")
            }, f"el motivo '{nombre}' tiene un cuerpo distinto: {limpio}"

    async def test_no_hay_404_para_un_slug_inexistente(
        self,
        http_client: AsyncClient,
        session: AsyncSession,
        business_a: uuid.UUID,
        business_b: uuid.UUID,
    ) -> None:
        """Un slug que no existe no se distingue de uno equivocado: los dos 401.

        El 404 seria el oraculo. Un 404 para "no existe este negocio" y un 401 para "el
        negocio existe pero tu no sos" dicen, entre los dos, cuantos negocios hay y
        cuales tienen usuarios. El slug llega del navegador de un usuario legitimo, y
        por eso tiene que ser barato ignorarlo cuando no corresponde.
        """
        email = "sin-404@negocio.com.ar"
        await _miembros_en_dos_negocios(session, business_a, business_b, email)

        inexistente, cuerpo_inexistente = await _login(
            http_client, email=email, password=PASSWORD, business_slug=SLUG_INEXISTENTE
        )
        equivocado, cuerpo_equivocado = await _login(
            http_client, email=email, password=PASSWORD, business_slug=SLUG_C
        )

        assert inexistente == 401, cuerpo_inexistente
        assert equivocado == 401, cuerpo_equivocado
        assert cuerpo_inexistente["detail"] == cuerpo_equivocado["detail"]


# --------------------------------------------------------------------------- #
# El GUC y la RLS despues del login
# --------------------------------------------------------------------------- #
#
# Los tests de arriba usan el fixture `http_client`, que overridea `get_tenant_session`
# con la sesion del test. Sirven para el contrato HTTP del login, pero **no** pueden
# decir nada sobre el GUC: con el override, el GUC lo puso el test, no la app.
#
# Los de abajo van por la app sin overrides, contra el `session_scope` de verdad, que
# es el que corre en produccion. Por eso necesitan un miembro **commiteado**: el login
# real abre otra conexion y no veria lo que esta sin commitear en la transaccion del
# test.


async def _borrar_miembro(conn: object, *, business_id: uuid.UUID, email: str) -> None:
    """Borra un miembro y sus refresh tokens, con el GUC de **su** tenant puesto.

    El GUC no es opcional. `business_users` esta en `FORCE RLS`, asi que un `DELETE` sin
    el `set_config` no da error: **borra cero filas y dice que bien**. La primera version
    de esta limpieza no lo llevaba y por eso no limpio nada--los miembros de la corrida
    quedaron en la base, y la siguiente se comio un `uq_business_users_business_id_email`
    que no señalaba el problema real: decia "duplicado", cuando lo que habia era una
    fila vieja de una corrida anterior.

    Los `refresh_tokens` van primero porque la FK los referencia al usuario.
    """
    await conn.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(business_id)},
    )
    await conn.execute(
        text(
            "DELETE FROM refresh_tokens WHERE user_id IN "
            "(SELECT id FROM business_users WHERE email = :email)"
        ),
        {"email": email},
    )
    await conn.execute(
        text("DELETE FROM business_users WHERE email = :email"),
        {"email": email},
    )


class _MiembrosVisibles:
    """Miembros de negocio **commiteados**, con limpieza garantizada.

    El nombre dice lo que compra: sembrar aqui es lo unico que permite que un test
    hable con la app de verdad, porque el login real abre otra conexion y no veria lo
    que esta sin commitear en la transaccion del test.

    Se siembra con el rol de DDL y el GUC del tenant, porque `FORCE RLS` alcanza tambien
    al dueno de las tablas: sin el `set_config`, el `INSERT` rebotaria con `new row
    violates row-level security policy`.

    Cada `agregar` borra antes de insertar, con el GUC puesto. Es lo que hace que esto
    sea idempotente frente a una corrida anterior que dejo filas: el `email` es unico
    por negocio, y un `INSERT` a ciegas reventaria con `uq_business_users_business_id_email`,
    un error que no dice que el problema es del propio test.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._sembrados: list[tuple[uuid.UUID, str]] = []

    async def agregar(
        self, *, business_id: uuid.UUID, email: str, password: str = PASSWORD
    ) -> uuid.UUID:
        """Inserta, commitea y devuelve el `user_id`."""
        from sqlalchemy.ext.asyncio import create_async_engine

        user_id = uuid.uuid4()
        engine = create_async_engine(self._url, poolclass=None, echo=False)
        try:
            async with engine.begin() as conn:
                await _borrar_miembro(conn, business_id=business_id, email=email)
                await conn.execute(
                    text(
                        "INSERT INTO business_users "
                        "(id, business_id, email, password_hash, full_name, role, status) "
                        "VALUES (:id, :business_id, :email, :hash, ' Persona', 'admin', 'active')"
                    ),
                    {
                        "id": user_id,
                        "business_id": business_id,
                        "email": email,
                        "hash": hash_password(password),
                    },
                )
                await conn.execute(text(f"SELECT set_config('{TENANT_GUC}', '', true)"))
        finally:
            await engine.dispose()
        self._sembrados.append((business_id, email))
        return user_id

    async def limpiar(self) -> None:
        """Deshace todo lo que se sembro, tenant por tenant.

        Sin esto los miembros sobreviven a la corrida y el `pytest` siguiente de la misma
        base falla en el `INSERT`. El borrado necesita el GUC de cada tenant, y como los
        miembros viven en negocios distintos no hay un solo `set_config` que los alcance
        a todos.
        """
        from sqlalchemy.ext.asyncio import create_async_engine

        if not self._sembrados:
            return
        engine = create_async_engine(self._url, poolclass=None, echo=False)
        try:
            async with engine.begin() as conn:
                for business_id, email in self._sembrados:
                    await _borrar_miembro(conn, business_id=business_id, email=email)
                await conn.execute(text(f"SELECT set_config('{TENANT_GUC}', '', true)"))
        finally:
            await engine.dispose()


@pytest_asyncio.fixture
async def miembros_visibles(migration_database_url: str) -> AsyncIterator[_MiembrosVisibles]:
    """Sembrador de miembros que la app real puede ver, limpio al final del test."""
    sembrador = _MiembrosVisibles(migration_database_url)
    try:
        yield sembrador
    finally:
        await sembrador.limpiar()


@pytest_asyncio.fixture
async def cliente_de_produccion() -> AsyncIterator[AsyncClient]:
    """Cliente HTTP contra la app **sin overrides**.

    Es la diferencia entre "el router responde esto" y "la app en produccion responde
    esto". Con los overrides de `http_client`, `get_tenant_session` no se ejecuta: el
    GUC lo pone el test. Este cliente deja que la app abra su sesion, fije el GUC con
    el `business_id` del token y haga su consulta.
    """
    from app.main import create_app
    from httpx import ASGITransport

    app = create_app(get_settings())
    transporte = ASGITransport(app=app)
    async with AsyncClient(transport=transporte, base_url="http://test") as cliente:
        yield cliente


class TestElGucDespuesDelLogin:
    """Login, GUC y RLS: lo que pasa en la request siguiente."""

    async def test_el_login_setea_el_guc_y_la_rls_filtra_por_tenant(
        self,
        cliente_de_produccion: AsyncClient,
        seeded: None,
        miembros_visibles: _MiembrosVisibles,
        business_a: uuid.UUID,
        business_b: uuid.UUID,
        customer_a: uuid.UUID,
        customer_b: uuid.UUID,
    ) -> None:
        """Tras el login, `GET /clientes` devuelve los clientes de su tenant y no los otros.

        La prueba no es que el endpoint responda 200: es que **el filtro cambia con el
        token**. La misma consulta, con el mismo rol, devuelve una fila para el token de
        A y otra para el de B. Si el GUC no se pusiera, `customers` -- que tiene RLS
        `FORCE`-- devolveria cero filas para los dos, y el test veria "todo vacio" sin
        saber por que.
        """
        await miembros_visibles.agregar(business_id=business_a, email="guc-probe-a@negocio.com.ar")
        await miembros_visibles.agregar(business_id=business_b, email="guc-probe-b@negocio.com.ar")

        ids_por_login = {}
        for etiqueta, email in (
            ("a", "guc-probe-a@negocio.com.ar"),
            ("b", "guc-probe-b@negocio.com.ar"),
        ):
            login = await cliente_de_produccion.post(
                "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
            )
            assert login.status_code == 200, login.text
            token = login.json()["access_token"]

            clientes = await cliente_de_produccion.get(
                "/api/v1/business/clientes", headers={"Authorization": f"Bearer {token}"}
            )
            assert clientes.status_code == 200, clientes.text
            ids_por_login[etiqueta] = {c["id"] for c in clientes.json()}

        # `str()` en las dos partes: el `id` del JSON es un string y `customer_a` es un
        # `uuid.UUID`, y un `in` entre un UUID y un set de strings da `False` siempre.
        # Un assert que nunca puede pasarse no es una cobertura: es decoracion.
        de_a = {str(i) for i in ids_por_login["a"]}
        de_b = {str(i) for i in ids_por_login["b"]}
        assert str(customer_a) in de_a, f"el token de A no ve su propio cliente: {de_a}"
        assert str(customer_b) not in de_a, f"el token de A ve clientes de B: {de_a}"
        assert str(customer_b) in de_b, f"el token de B no ve su propio cliente: {de_b}"
        assert str(customer_a) not in de_b, f"el token de B ve clientes de A: {de_b}"

    async def test_el_tenant_no_se_filtra_a_la_request_siguiente(
        self,
        cliente_de_produccion: AsyncClient,
        seeded: None,
        miembros_visibles: _MiembrosVisibles,
        business_a: uuid.UUID,
        business_b: uuid.UUID,
        customer_a: uuid.UUID,
        customer_b: uuid.UUID,
    ) -> None:
        """Dos logins seguidos en el mismo proceso: cada uno ve lo suyo.

        El riesgo es R-02 y es de pool, no de codigo: `SET` de sesion sobrevive al
        `COMMIT` y el pool reusa conexiones, asi que un login de B podia dejar el GUC
        de B puesto y el siguiente request de A--que corre en la conexion reciclada--
        leeria el tenant equivocado. Con `SET LOCAL` cada transaccion empieza limpia.

        El orden importa: primero A, despues B. Si el GUC se filtrara, la segunda
        lectura--la de A--seria la que lo veria, porque es la que corre sobre la
        conexion que B acaba de usar.
        """
        await miembros_visibles.agregar(business_id=business_a, email="guc-probe-a@negocio.com.ar")
        await miembros_visibles.agregar(business_id=business_b, email="guc-probe-b@negocio.com.ar")

        async def _clientes_de(email: str) -> set[str]:
            login = await cliente_de_produccion.post(
                "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
            )
            assert login.status_code == 200, login.text
            respuesta = await cliente_de_produccion.get(
                "/api/v1/business/clientes",
                headers={"Authorization": f"Bearer {login.json()['access_token']}"},
            )
            assert respuesta.status_code == 200, respuesta.text
            return {c["id"] for c in respuesta.json()}

        de_a = await _clientes_de("guc-probe-a@negocio.com.ar")
        de_b = await _clientes_de("guc-probe-b@negocio.com.ar")
        de_a_otra_vez = await _clientes_de("guc-probe-a@negocio.com.ar")

        assert str(customer_a) in de_a
        assert str(customer_b) not in de_a
        assert str(customer_b) in de_b
        assert str(customer_a) not in de_b
        # La parte que detecta la fuga: la misma request de A, repetida.
        assert de_a_otra_vez == de_a, (
            "la segunda vuelta de A devolvio otra cosa: el GUC del login de B quedo "
            "puesto en la conexion"
        )
        assert str(customer_b) not in de_a_otra_vez

    async def test_el_rol_que_sirve_requests_no_esta_privilegiado(
        self, session: AsyncSession
    ) -> None:
        """El rol con el que corren las queries no es superuser ni saltea RLS.

        Es el soporte de los dos tests anteriores: si `DATABASE_URL` apuntara a un rol
        con `BYPASSRLS`--o al superuser, como en un postgres de desarrollo-- las RLS
        se verian bien sin que nadie las haya configurado. Los tests de RLS pasan
        contra un rol sin privilegios y fallan contra uno que los tiene, que es al reves
        de lo que sirve.

        `current_user` y no el rol del fixture: lo que se comprueba es el rol con el
        que la app habla con la base, que es el que decide si las politicas se aplican.

        `mappings()` y no indices: una fila de SQLAlchemy con `text()` crudo no se indexa
        por nombre, y `fila["rol"]` falla con un `TypeError` que no dice nada del rol.
        """
        fila = (
            (
                await session.execute(
                    text(
                        "SELECT current_user AS rol, "
                        "       r.rolsuper AS es_superuser, "
                        "       r.rolbypassrls AS saltea_rls "
                        "FROM pg_roles r WHERE r.rolname = current_user"
                    )
                )
            )
            .mappings()
            .one()
        )

        assert fila["rol"] != "postgres", (
            "las queries de la app estan corriendo como el superuser: cualquier RLS "
            "pasaria sin probarse"
        )
        assert fila["es_superuser"] is False, f"el rol {fila['rol']} es superuser"
        assert fila["saltea_rls"] is False, f"el rol {fila['rol']} tiene BYPASSRLS"

    async def test_la_rls_de_la_sesion_de_la_app_sigue_forzada(
        self, session: AsyncSession, business_a: uuid.UUID, business_b: uuid.UUID
    ) -> None:
        """Con el GUC de B, la sesion de la app no ve los miembros de A.

        El complemento directo del anterior: no alcanza con que el rol no tenga
        `BYPASSRLS`, tiene que ser que la politica este puesta. Con el GUC del tenant
        equivocado, `business_users`--con `FORCE RLS`-- tiene que devolver cero filas
        del tenant ajeno, y `business_users` es justamente la tabla que el login acaba
        de leer a traves del definer.

        El `INSERT` va con el GUC de A y con el rol de la app, asi que las filas existen
        para esta sesion y el cero que se mide no es "no hay nada".
        """
        email = "rls-forneceada@negocio.com.ar"
        user_a = await _crear_miembro(session, business_id=business_a, email=email)

        await _tenant(session, business_b)
        ajenas = await session.execute(
            text("SELECT id FROM business_users WHERE business_id = :negocio"),
            {"negocio": business_a},
        )
        assert [str(f) for f in ajenas.scalars()] == [], (
            "la sesion de la app leyo filas de otro tenant: la RLS no esta forzada"
        )

        await _tenant(session, business_a)
        propias = await session.execute(
            text("SELECT id FROM business_users WHERE id = :id"),
            {"id": user_a},
        )
        assert [str(f) for f in propias.scalars()] == [str(user_a)]
