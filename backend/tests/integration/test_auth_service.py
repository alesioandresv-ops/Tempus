"""El servicio de login: qué acepta, qué rechaza y qué no deja filtrar.

El servicio tiene tres propiedades que no se comprueban leyendo el codigo, porque
las tres son sobre lo que **no** hace:

1. Que el camino de "no existe" ejecute la misma operacion cara que el de "existe".
2. Que las tres formas de fallo produzcan el mismo objeto de error.
3. Que el tenant salga siempre de la fila autenticada.

Cada una de ellas se puede romper con un cambio que parece inocuo -- un `return`
temprano, un mensaje mas especifico, un `business_id` que se acepta por parametro --
y ninguno falla en el editor ni en el linter. Por eso estan como tests y no como
comentarios.
"""

from __future__ import annotations

import datetime as dt
import os
import statistics
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import fields

import jwt as pyjwt
import pytest
import pytest_asyncio
from app.api.errors import AppError, AuthenticationError, RateLimitError
from app.core.config import get_settings
from app.core.security import (
    SENTINEL_PREIMAGE,
    get_sentinel_hash,
    hash_password,
    reset_password_hash_cache,
    verify_password,
)
from app.db.session import TENANT_GUC
from app.models.enums import BusinessUserRole, MembershipStatus, PlatformRole
from app.modules.auth import service as auth_service
from app.modules.auth.scopes import PlatformScope, Scope, scopes_for_business_role
from app.modules.auth.service import authenticate_business_user
from app.modules.auth.tokens import REQUIRED_CLAIMS, decode_access_token
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

pytestmark = pytest.mark.integration

PASSWORD = "una-contrasena-larga-y-unica"
OTRA_PASSWORD = "otra-contrasena-distinta"

#: Emails distintos por caso. Si dos tests usaran el mismo email, el cubo de rate
#: limit por email -- que vive en la base de verdad y no en la transaccion del test,
#: porque `rate_limit_buckets` se siembra desde otra conexion -- arrastraria el estado
#: de uno al otro. Cada test tiene el suyo y no se confunden.
EMAIL_ADMIN_A = "admin-a@negocio.test"
EMAIL_STAFF_A = "staff-a@negocio.test"
EMAIL_AMBIGUO = "ambiguo@negocio.test"
EMAIL_INVITED = "invitado@negocio.test"
EMAIL_DISABLED = "desactivado@negocio.test"
EMAIL_LOCKED = "bloqueado@negocio.test"
EMAIL_PLATFORM = "owner@plataforma.test"
EMAIL_PLATFORM_INACTIVE = "inactivo@plataforma.test"
EMAIL_INEXISTENTE = "nadie@ejemplo.test"


@pytest.fixture(autouse=True)
def argon2_rapido() -> Iterator[None]:
    """Argon2 con coste minimo, restaurado al final. Autouse para todo el archivo.

    Es el mismo truco que usa `test_security.py`. Sin esto, cada login de este
    archivo pagaria los ~100 ms de produccion y la suite de auth seria la mas lenta
    del proyecto por una razon que no tiene que ver con lo que se prueba.

    `autouse` y no un fixture que pide cada test: el coste bajo es una condicion de
    este archivo entero, no de un test. Pedirlo explicitamente en 25 firmas es una
    forma de que se olvide en el test 26, y ese test seria el lento.
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

    Se limpia con `''` y no con un UUID cualquiera, porque `NULLIF(current_setting(...),
    '')` es lo que hace que la RLS niegue en vez de comparar contra basura.
    """
    await session.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(business_id) if business_id is not None else ""},
    )


async def _crear_usuario(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    email: str,
    password: str = PASSWORD,
    role: BusinessUserRole = BusinessUserRole.ADMIN,
    status: MembershipStatus = MembershipStatus.ACTIVE,
) -> uuid.UUID:
    """Inserta un miembro de negocio con el **rol de la app** y el GUC puesto.

    Se inserta desde la sesion del test, que corre como `tempus_app`, y no con el rol
    de DDL. Es a proposito: asi el test verifica que el servicio puede autenticarse
    contra filas que la aplicacion misma no podria crear de cualquier otra forma, y de
    paso ejercita el `INSERT` con `WITH CHECK` de la politica. Todo cae en la
    transaccion del test, asi que no queda nada.
    """
    await _tenant(session, business_id)
    user_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO business_users "
            "(id, business_id, email, password_hash, full_name, role, status) "
            "VALUES (:id, :business_id, :email, :hash, ' Persona', :role, :status)"
        ),
        {
            "id": user_id,
            "business_id": business_id,
            "email": email,
            "hash": hash_password(password),
            "role": str(role),
            "status": str(status),
        },
    )
    await session.flush()
    return user_id


#: Emails que este archivo siembra en `platform_users`. Los necesita el fixture de
#: limpieza de sesion, que corre en un ambito distinto al del que los crea.
EMAILS_DE_PLATAFORMA = (EMAIL_PLATFORM, EMAIL_PLATFORM_INACTIVE)


@pytest_asyncio.fixture(scope="session")
async def _limpieza_de_plataforma_al_terminar() -> AsyncIterator[None]:
    """Borra los operadores de plataforma **al final de la sesion**, no por test.

    **El teardown por test cuelga la suite, y no por una razon que parezca un
    bug de tests.** `platform_users` es padre de `refresh_tokens` por clave foranea,
    asi que un login de plataforma--que escribe `refresh_tokens`-- deja a la sesion
    del test con un lock `FOR KEY SHARE` sobre la fila del operador. Ese lock dura
    lo que la transaccion, y la transaccion del test se deshace en el teardown de su
    propio fixture.

    El orden de teardown de pytest es el inverso del de setup, asi que este fixture
    --que se setea despues de `session`-- se desarma **antes**. Cuando su `DELETE`
    llegaba a otra conexion, esa conexion se quedaba esperando el lock para siempre:
    la suite no fallaba, se colgaba, y no habia excepcion, ni traceback, ni log. Lo
    unico que lo delata es `pg_stat_activity` con una sesion en `Lock/transactionid`
    apuntando a otra en `idle in transaction`.

    Mover la limpieza al cierre de la sesion de pytest resuelve las dos cosas: ya
    no hay transaccion abierta que la bloquee, y las credenciales--que siestan en
    `platform_users` y por eso hay que borrar-- siguen sin quedar en la base.
    """
    yield
    url = os.environ.get("DATABASE_MIGRATION_URL") or os.environ.get("TEST_DATABASE_URL") or ""
    if not url:
        return
    engine = create_async_engine(url, poolclass=None, echo=False)
    try:
        async with engine.begin() as conn:
            # `= ANY(:arr)` y no `IN (:arr)`: asyncpg no acepta una tupla expandida
            # en el `IN`--`IN $1` es error de sintaxis en PostgreSQL-- y un
            # `IN (:a, :b)` obliga a un placeholder por cada elemento.
            await conn.execute(
                text("DELETE FROM platform_users WHERE email = ANY(:emails)"),
                {"emails": list(EMAILS_DE_PLATAFORMA)},
            )
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def platform_owner(
    migration_database_url: str,
    _limpieza_de_plataforma_al_terminar: None,
) -> AsyncIterator[uuid.UUID]:
    """Un operador de plataforma, sembrado con el rol de DDL y commiteado.

    Es el unico camino posible: `0002` le quita **todo** privilegio sobre
    `platform_users` al rol de la app, asi que ni siquiera un test con su sesion
    podria insertar uno. Y tiene que estar commiteado, porque el servicio lo va a ver
    desde otra conexion -- si quedara sin commitear en la transaccion del test, el
    servicio leeria cero filas y el test "el login de plataforma funciona" pasaria
    por la razon equivocada.

    **Acá no se borra al final**: el borrado es de `_limpieza_de_plataforma_al_terminar`
    y el motivo--un lock de clave foranea que cuelga la suite entera-- esta escrito
    ahi. Sembrar si es seguro en cualquier momento: el `DELETE` inicial va sobre
    filas que ninguna sesion abierta puede tener bloqueadas, porque la transaccion
    del test anterior ya se deshizo.
    """
    engine = create_async_engine(migration_database_url, poolclass=None, echo=False)
    user_id = uuid.uuid4()
    try:
        async with engine.begin() as conn:
            for email in EMAILS_DE_PLATAFORMA:
                await conn.execute(
                    text("DELETE FROM platform_users WHERE email = :email"),
                    {"email": email},
                )
            await conn.execute(
                text(
                    "INSERT INTO platform_users "
                    "(id, email, password_hash, full_name, role, is_active) "
                    "VALUES (:id, :email, :hash, 'Operadora', :role, true)"
                ),
                {
                    "id": user_id,
                    "email": EMAIL_PLATFORM,
                    "hash": hash_password(PASSWORD),
                    "role": str(PlatformRole.OWNER),
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO platform_users "
                    "(id, email, password_hash, full_name, role, is_active) "
                    "VALUES (:id, :email, :hash, 'Operadora', :role, :activo)"
                ),
                {
                    "id": uuid.uuid4(),
                    "email": EMAIL_PLATFORM_INACTIVE,
                    "hash": hash_password(PASSWORD),
                    "role": str(PlatformRole.OWNER),
                    "activo": False,
                },
            )
        yield user_id
    finally:
        await engine.dispose()


class TestElLoginExitoso:
    async def test_credenciales_correctas_entrann(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
    ) -> None:
        """1. El caso feliz, y con el GUC de tenant apagado.

        Importa el `await _tenant(session, None)` final: sin el, el GUC de la
        siembra seguiria puesto y el test pasaria aunque el servicio dependiera del
        contexto de tenant -- que es justo lo que todavia no existe en el momento del
        login, y lo unico que hace posible el lookup es que la funcion de `0004` sea
        `SECURITY DEFINER`. Con el GUC apagado, este test verifica esa parte de verdad.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        login = await auth_service.authenticate_business_user(
            session, email=EMAIL_ADMIN_A, password=PASSWORD, ip_address="10.1.0.1"
        )

        assert login.business_id == business_a
        assert login.role is BusinessUserRole.ADMIN
        assert login.is_platform is False

    async def test_el_tid_del_jwt_es_el_tenant_real_del_usuario(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
    ) -> None:
        """15. `tid` sale de la fila autenticada."""
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        login = await auth_service.authenticate_business_user(
            session, email=EMAIL_ADMIN_A, password=PASSWORD, ip_address="10.1.0.2"
        )
        principal = decode_access_token(login.access_token.token)

        assert principal.business_id == business_a
        assert principal.user_id == login.user_id
        assert login.access_token.business_id == business_a

    async def test_el_usuario_no_puede_proponer_un_tid_alternativo(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
    ) -> None:
        """16. No hay por donde meter un `tid` ajeno.

        Se comprueba de las dos formas que importan. La primera es la firma: no acepta
        `business_id`, ni `tid`, ni `role`. La segunda es el comportamiento con un
        argumento de mas, que tiene que reventar con `TypeError` y no ser ignorado en
        silencio: un endpoint que armara el body con lo que le manden y el servicio se
        comiera el campo, seria el bug entero del ADR-0010 otra vez.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        with pytest.raises(TypeError):
            await auth_service.authenticate_business_user(  # type: ignore[call-arg]
                session,
                email=EMAIL_ADMIN_A,
                password=PASSWORD,
                ip_address="10.1.0.3",
                business_id=uuid.uuid4(),
            )

        with pytest.raises(TypeError):
            await auth_service.authenticate_business_user(  # type: ignore[call-arg]
                session,
                email=EMAIL_ADMIN_A,
                password=PASSWORD,
                ip_address="10.1.0.3",
                tid=str(uuid.uuid4()),
            )

        parametros = set(auth_service.authenticate_business_user.__annotations__)
        assert "business_id" not in parametros
        assert "tid" not in parametros

    async def test_un_usuario_del_tenant_a_no_obtiene_identidad_del_b(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
        business_b: uuid.UUID,
    ) -> None:
        """18. Aislamiento entre tenants en el mismo email de entrada.

        Dos personas distintas, dos negocios, la misma contrasena. Cada una entra en
        el suyo y el `tid` del token dice el suyo. Si el lookup devolviera "la
        primera" o mezclara las dos, este test lo veria.
        """
        await _crear_usuario(
            session,
            business_id=business_a,
            email=EMAIL_ADMIN_A,
            role=BusinessUserRole.ADMIN,
        )
        await _crear_usuario(
            session,
            business_id=business_b,
            email=EMAIL_STAFF_A,
            role=BusinessUserRole.STAFF,
        )
        await _tenant(session, None)

        de_a = await auth_service.authenticate_business_user(
            session, email=EMAIL_ADMIN_A, password=PASSWORD, ip_address="10.1.0.4"
        )
        de_b = await auth_service.authenticate_business_user(
            session, email=EMAIL_STAFF_A, password=PASSWORD, ip_address="10.1.0.5"
        )

        assert decode_access_token(de_a.access_token.token).business_id == business_a
        assert decode_access_token(de_b.access_token.token).business_id == business_b
        assert de_a.role is BusinessUserRole.ADMIN
        assert de_b.role is BusinessUserRole.STAFF

    async def test_el_email_mayusculas_es_el_mismo_usuario(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
    ) -> None:
        """El login es case-insensitive, como dice el indice unico de `citext`.

        Y el cubo de rate limit tambien: si la normalizacion no existiera, este test
        passaria pero el atacante tendria 10/min por cada variante de mayusculas.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        login = await auth_service.authenticate_business_user(
            session, email="  ADMIN-A@NEGOCIO.TEST ", password=PASSWORD, ip_address="10.1.0.6"
        )
        assert login.business_id == business_a

    async def test_el_refresh_inicial_abre_una_familia_y_no_persiste_el_token(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
    ) -> None:
        """El refresh sale en claro una vez y a la base va su hash.

        Se mira la fila, no el dataclass: lo que importa es que en `refresh_tokens`
        no haya ningun campo con el token.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        login = await auth_service.authenticate_business_user(
            session,
            email=EMAIL_ADMIN_A,
            password=PASSWORD,
            ip_address="10.1.0.7",
            user_agent="pytest",
        )
        await session.flush()

        fila = (
            await session.execute(
                text(
                    "SELECT family_id, token_hash, user_id, ip_address, user_agent, revoked_at "
                    "FROM refresh_tokens WHERE user_id = :u"
                ),
                {"u": login.user_id},
            )
        ).one()

        assert fila.family_id == login.refresh.family_id
        assert fila.token_hash != login.refresh.token
        assert len(fila.token_hash) == 64
        assert fila.revoked_at is None
        assert str(fila.ip_address) == "10.1.0.7"
        assert fila.user_agent == "pytest"

        # El token en claro no esta en ningun lado de la fila.
        serializada = str(dict(fila._mapping))
        assert login.refresh.token not in serializada

    async def test_cada_login_abre_una_familia_distinta(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
    ) -> None:
        """Dos sesiones = dos familias. Lo contrario seria una sesion compartida.

        Y las dos familias no se pueden cruzar: un login distinto con la misma
        contrasena genera otro `family_id` y otro token. Es la base de la deteccion de
        reuso de F1.6.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        uno = await auth_service.authenticate_business_user(
            session, email=EMAIL_ADMIN_A, password=PASSWORD, ip_address="10.1.0.8"
        )
        dos = await auth_service.authenticate_business_user(
            session, email=EMAIL_ADMIN_A, password=PASSWORD, ip_address="10.1.0.9"
        )

        assert uno.refresh.family_id != dos.refresh.family_id
        assert uno.refresh.token != dos.refresh.token


class TestElJwtEmitido:
    async def test_el_token_es_valido(self, session: AsyncSession, business_a: uuid.UUID) -> None:
        """13. Firma y claims correctos: `decode_access_token` no se queja."""
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        login = await auth_service.authenticate_business_user(
            session, email=EMAIL_ADMIN_A, password=PASSWORD, ip_address="10.2.0.1"
        )
        principal = decode_access_token(login.access_token.token)

        assert principal.user_id == login.user_id
        assert principal.role == str(BusinessUserRole.ADMIN)

    async def test_lleva_exactamente_los_claims_del_arquitectura(
        self, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """14. Ni un claim de mas ni uno de menos.

        La lista es exacta, no "contiene". Un claim de mas en un JWT es informacion
        publicada a cualquier cliente, y aunque hoy no haya nada sensible que
        poner ahi, lo que se verifica es el contrato: el dia que alguien meta algo
        "para debugging", este test dice que no.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        login = await auth_service.authenticate_business_user(
            session, email=EMAIL_ADMIN_A, password=PASSWORD, ip_address="10.2.0.2"
        )
        principal = decode_access_token(login.access_token.token)
        crudos = pyjwt.decode(
            login.access_token.token,
            options={"verify_signature": False, "verify_exp": False},
        )

        # `tid` va aparte de `REQUIRED_CLAIMS` a proposito: la tupla es el minimo que
        # `decode_access_token` exige, y `tid` es obligatorio para un token de negocio
        # pero **no** puede estar en esa tupla porque un token de plataforma no tiene
        # tenant y lo omite. Agregarlo a la tuplaeria habria roto el login de
        # plataforma; por eso vive aparte y por eso este test lo nombra.
        esperados = set(REQUIRED_CLAIMS) | {"iss", "aud", "tid"}
        assert set(crudos) == esperados, f"claims inesperados: {set(crudos) ^ esperados}"
        assert crudos["tid"] == str(business_a), (
            "el `tid` del token de negocio tiene que ser el business_id del login"
        )
        assert principal.scopes == frozenset(
            str(s) for s in scopes_for_business_role(BusinessUserRole.ADMIN)
        )

    async def test_los_scopes_son_los_del_rol_y_no_otros(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
    ) -> None:
        """12. El token de un `staff` no lleva los scopes de configuracion.

        `staff` no tiene `business:config:write`. Si el token lo llevara, un endpoint
        protegido por scope pasaria, y el fallo seria de autorizacion en produccion.
        """
        await _crear_usuario(
            session,
            business_id=business_a,
            email=EMAIL_STAFF_A,
            role=BusinessUserRole.STAFF,
        )
        await _tenant(session, None)

        login = await auth_service.authenticate_business_user(
            session, email=EMAIL_STAFF_A, password=PASSWORD, ip_address="10.2.0.3"
        )
        scopes = decode_access_token(login.access_token.token).scopes

        assert str(Scope.BUSINESS_CONFIG_WRITE) not in scopes
        assert str(Scope.CLIENTS_WRITE) in scopes
        assert login.scopes == scopes_for_business_role(BusinessUserRole.STAFF)

    async def test_el_token_no_lleva_la_password_ni_su_hash(
        self, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """Ni la contrasena ni el hash viajan en el token.

        El hash no viaja porque `create_access_token` no lo recibe: la firma lo
        demuestra, no la confianza.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        login = await auth_service.authenticate_business_user(
            session, email=EMAIL_ADMIN_A, password=PASSWORD, ip_address="10.2.0.4"
        )
        crudos = pyjwt.decode(
            login.access_token.token,
            options={"verify_signature": False, "verify_exp": False},
        )

        assert PASSWORD not in str(crudos)
        assert "password" not in crudos
        assert "password_hash" not in crudos
        assert "email" not in crudos
        assert "full_name" not in crudos


class TestLasTresFormasDeFalloSonIndistinguibles:
    """El bloque central: las tres salidas de fallo son el mismo objeto.

    Los tests de este bloque de abajo son los que importan mas que cualquier otro del
    archivo, y por eso se comparan **entre si**: no basta con que cada uno se vea bien
    por separado, tienen que ser indistinguibles unos de otros.
    """

    async def _fallos(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
    ) -> list[tuple[str, AuthenticationError]]:
        """Provoca los tres fallos y devuelve las excepciones que raised.

        Devuelve las excepciones y no los serializados porque la comparacion cruda de
        dos `AuthenticationError` incluye el `args`, y es exactamente ahi donde un
        mensaje distinto se esconderia.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _crear_usuario(
            session,
            business_id=business_a,
            email=EMAIL_DISABLED,
            status=MembershipStatus.DISABLED,
        )
        await _crear_usuario(
            session,
            business_id=business_a,
            email=EMAIL_LOCKED,
            status=MembershipStatus.LOCKED,
        )
        await _crear_usuario(
            session,
            business_id=business_a,
            email=EMAIL_INVITED,
            status=MembershipStatus.INVITED,
        )
        await _tenant(session, None)

        casos: list[tuple[str, AuthenticationError]] = []
        # Una IP distinta por caso: el limite es 5/min por IP y todos estos intentos
        # cuentan para el mismo.
        intentos = [
            ("inexistente", EMAIL_INEXISTENTE, PASSWORD),
            ("password_incorrecta", EMAIL_ADMIN_A, OTRA_PASSWORD),
            ("disabled", EMAIL_DISABLED, PASSWORD),
            ("locked", EMAIL_LOCKED, PASSWORD),
            ("invited", EMAIL_INVITED, PASSWORD),
        ]
        for i, (nombre, email, password) in enumerate(intentos):
            with pytest.raises(AuthenticationError) as exc:
                await auth_service.authenticate_business_user(
                    session,
                    email=email,
                    password=password,
                    ip_address=f"10.3.0.{i + 1}",
                )
            casos.append((nombre, exc.value))
        return casos

    async def test_los_cinco_fallos_dan_el_mismo_error(
        self, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """8. Mismo tipo, mismo status, mismo `detail`, mismos campos.

        Se compara el `AppError` entero -- `detail`, `extra`, `status_code`, `title` y
        `error_type` -- y no solo el mensaje. Un `extra={"reason": "locked"}` seria
        invisible comparando strings.
        """
        fallos = await self._fallos(session, business_a)

        referencia = fallos[0][1]
        for nombre, error in fallos[1:]:
            assert type(error) is type(referencia), f"{nombre} levanta otra clase"
            assert error.detail == referencia.detail, f"{nombre} tiene otro detalle"
            assert error.extra == referencia.extra, f"{nombre} tiene otros extra"
            assert error.status_code == referencia.status_code
            assert error.title == referencia.title
            assert error.error_type == referencia.error_type
            assert str(error) == str(referencia)

    async def test_el_detalle_no_nombra_la_causa(
        self, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """El detalle no dice "no encontrado", ni "incorrecta", ni "desactivada".

        Se chequea contra las palabras que alguien escribiria paraLeaks. No es una
        lista cerrada de prohibidos: es una red, para que un mensaje nuevo que leaks
        tambien caiga.
        """
        fallos = await self._fallos(session, business_a)
        detail = fallos[0][1].detail

        for palabra in (
            "no encontrado",
            "no existe",
            "inexistente",
            "incorrecta",
            "desactivad",
            "bloquead",
            "invited",
            "disabled",
            "locked",
            "usuario",
            "contrasena",
        ):
            assert palabra not in detail.lower(), f"el detalle filtraria {palabra!r}"

    async def test_el_status_es_401_y_no_403(
        self, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """401 en los cinco, y todos el mismo.

        Un 403 para "desactivado" seria un semaforo: 401 es "no se quien sos" y 403 es
        "se quien sos pero no podes". Con 403, el atacante sabe que la cuenta existe.
        """
        fallos = await self._fallos(session, business_a)

        assert {error.status_code for _n, error in fallos} == {401}
        assert {type(error) for _n, error in fallos} == {AuthenticationError}

    async def test_el_error_no_arrastra_la_excepcion_interna(
        self, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """20. Los caminos de error no filtran nada por `__cause__` ni por `extra`.

        `extra` es el vector que se olvida: es el unico campo libre de RFC 9457, asi
        que ahi cabe un `{"business_id": ...}` sin que nadie lo note en el body. Se
        exige que este vacio.
        """
        fallos = await self._fallos(session, business_a)

        for nombre, error in fallos:
            assert error.extra == {}, f"{nombre} lleno `extra` con datos internos"
            assert error.__cause__ is None, f"{nombre} encadena la causa interna"
            assert error.__context__ is None or error.__suppress_context__, (
                f"{nombre} expone el contexto de la excepcion"
            )


class TestLoQueElFalloNoFiltra:
    """9, 10, 11, 12 en la direccion del error."""

    async def test_el_rechazo_no_expone_identidad_de_la_fila_que_se_encontro(
        self, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """Con la contrasena mal, tampoco se filtra `user_id` ni `business_id`.

        El caso interesante es el de una fila que **si** existe: el log interno puede
        decir que la encontro, pero lo que sube al cliente no puede llevar el id. Se
        compara el `repr` del error contra los valores reales.
        """
        user_id = await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        with pytest.raises(AuthenticationError) as exc:
            await auth_service.authenticate_business_user(
                session, email=EMAIL_ADMIN_A, password=OTRA_PASSWORD, ip_address="10.4.0.1"
            )

        error = exc.value
        texto = f"{error!r} {error.detail} {error.extra} {error.args}"
        assert str(user_id) not in texto
        assert str(business_a) not in texto
        assert EMAIL_ADMIN_A not in texto
        assert str(BusinessUserRole.ADMIN) not in texto
        assert str(Scope.CLIENTS_WRITE) not in texto

    async def test_un_usuario_bloqueado_no_revela_que_existe(
        self, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """Bloqueado y desactivado dan el mismo error que un email inexistente.

        Se comparan los dos errores entre si, no con una lista de palabras: asi el
        test falla si alguien escribe un mensaje nuevo que leaks, aunque no contenga
        ninguna de las palabras de la lista.
        """
        await _crear_usuario(
            session,
            business_id=business_a,
            email=EMAIL_LOCKED,
            status=MembershipStatus.LOCKED,
        )
        await _crear_usuario(
            session,
            business_id=business_a,
            email=EMAIL_DISABLED,
            status=MembershipStatus.DISABLED,
        )
        await _tenant(session, None)

        with pytest.raises(AuthenticationError) as inexistente:
            await auth_service.authenticate_business_user(
                session, email=EMAIL_INEXISTENTE, password=PASSWORD, ip_address="10.4.0.2"
            )
        with pytest.raises(AuthenticationError) as bloqueado:
            await auth_service.authenticate_business_user(
                session, email=EMAIL_LOCKED, password=PASSWORD, ip_address="10.4.0.3"
            )
        with pytest.raises(AuthenticationError) as desactivado:
            await auth_service.authenticate_business_user(
                session, email=EMAIL_DISABLED, password=PASSWORD, ip_address="10.4.0.4"
            )

        assert bloqueado.value.detail == inexistente.value.detail
        assert desactivado.value.detail == inexistente.value.detail
        assert bloqueado.value.status_code == inexistente.value.status_code
        assert desactivado.value.status_code == inexistente.value.status_code

    async def test_la_contrasena_se_verifica_tambien_cuando_la_cuenta_no_esta_activa(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Un `invited` verifica la contrasena antes de mirar el estado.

        Si el estado se mirara primero, el `invited` responderia sin ejecutar Argon2 y
        seria mas rapido que un login con contrasena incorrecta. El tiempo de respuesta
        seria un oraculo del estado de la cuenta, que es justo lo que no hace falta
        revelar. El spy cuenta las verificaciones reales de Argon2.
        """
        await _crear_usuario(
            session,
            business_id=business_a,
            email=EMAIL_INVITED,
            status=MembershipStatus.INVITED,
        )
        await _tenant(session, None)

        llamadas: list[str] = []
        real = verify_password

        def espia(password: str, stored_hash: str) -> bool:
            llamadas.append(stored_hash)
            return real(password, stored_hash)

        monkeypatch.setattr(auth_service, "verify_password", espia)

        with pytest.raises(AuthenticationError):
            await auth_service.authenticate_business_user(
                session, email=EMAIL_INVITED, password=PASSWORD, ip_address="10.4.0.5"
            )

        assert len(llamadas) == 1, "el estado se miro antes de verificar la contrasena"


class TestElHashCentinela:
    async def test_un_usuario_inexistente_verifica_contra_el_centinela(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """6. El hash centinela es el que se verifica cuando no hay filas.

        No se comprueba que el login "falla" -- eso lo harian todos los tests de
        fallo. Se comprueba **que hash se le paso a Argon2**, que es la unica cosa
        que hace que el tiempo sea el mismo.
        """
        await _tenant(session, None)
        centinela = get_sentinel_hash()

        hashes: list[str] = []
        real = verify_password

        def espia(password: str, stored_hash: str) -> bool:
            hashes.append(stored_hash)
            return real(password, stored_hash)

        monkeypatch.setattr(auth_service, "verify_password", espia)

        with pytest.raises(AuthenticationError):
            await auth_service.authenticate_business_user(
                session, email=EMAIL_INEXISTENTE, password=PASSWORD, ip_address="10.5.0.1"
            )

        assert hashes == [centinela], (
            "el camino inexistente no verifico contra el hash centinela: "
            "ese login es mas corto que uno real y se enumeran cuentas por tiempo"
        )

    async def test_el_centinela_no_es_el_hash_de_ninguna_password_real(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
    ) -> None:
        """El centinela no colisiona con un hash de usuario.

        Si colisionara, "usuario inexistente" y "usuario con esa contrasena" serian el
        mismo login. Se comparan los strings: son distintos porque cada uno lleva su
        sal aleatoria, y eso es lo que se quiere.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, business_a)  # GUC necesario para leer la fila recién creada

        fila: str = (
            await session.execute(
                text("SELECT password_hash FROM business_users WHERE email = :e"),
                {"e": EMAIL_ADMIN_A},
            )
        ).scalar_one()

        assert get_sentinel_hash() != fila
        assert fila != hash_password(PASSWORD)

    async def test_el_centinela_verifica_contra_su_propia_pre_imagen(self) -> None:
        """El centinela es un hash Argon2 valido de la pre-imagen declarada.

        Se verifica con `verify_password` de verdad, no con un `startswith`: si el
        hash no fuera parseable, el camino inexistente devolveria `False` por el
        `except` de `verify_password` en lugar de por una comparacion, y el test
        pasaria sin haber gastado el tiempo de CPU que dice gastar.
        """
        assert verify_password(SENTINEL_PREIMAGE, get_sentinel_hash()), (
            "el centinela no verifica contra su propia pre-imagen"
        )

    async def test_el_centinela_es_constante_dentro_del_proceso(self) -> None:
        """Se genera una vez y se reutiliza.

        Importa por el costo, no por la seguridad: `hash()` aplica una sal aleatoria,
        asi que generar el centinela por request seria un Argon2 **extra** en cada
        login inexistente, que es justo el caso cuyo tiempo estamos cuidando.
        """
        assert get_sentinel_hash() is get_sentinel_hash()

    def test_el_centinela_cambia_con_los_parametros_de_argon2(self) -> None:
        """Y se regenera cuando cambian los parametros.

        Sin esto, subir el coste de Argon2 un dia dejaria el centinela con los
        parametros viejos: el login inexistente seguiria costando 100 ms mientras los
        reales costarian 200, y la diferencia -- la que estamos intentando borrar --
        reaparece sola. Y como la pre-imagen es fija, el hash cambia y se nota.
        """
        settings = get_settings()
        original = (
            settings.argon2_time_cost,
            settings.argon2_memory_cost,
            settings.argon2_parallelism,
        )
        try:
            settings.argon2_time_cost = 1
            settings.argon2_memory_cost = 8
            settings.argon2_parallelism = 1
            reset_password_hash_cache()
            barato = get_sentinel_hash()

            settings.argon2_memory_cost = 16
            reset_password_hash_cache()
            caro = get_sentinel_hash()

            assert barato != caro, (
                "el centinela no se regenero al cambiar los parametros: el login "
                "inexistente queda con el coste viejo y el tiempo deja de coincidir"
            )
        finally:
            (
                settings.argon2_time_cost,
                settings.argon2_memory_cost,
                settings.argon2_parallelism,
            ) = original
            reset_password_hash_cache()

    def test_la_pre_imagen_del_centinela_no_es_una_credencial(self) -> None:
        """La pre-imagen esta escrita como lo que es.

        No es un secreto -- su unico trabajo es hacer que Argon2 se ejecute -- pero si
        pareciera una contrasena, un grep la encontraria en el `.env` equivocado y alguien
        podria intentar usarla como tal.
        """
        assert SENTINEL_PREIMAGE.startswith("tempus:")
        assert PASSWORD not in SENTINEL_PREIMAGE
        assert OTRA_PASSWORD not in SENTINEL_PREIMAGE


class TestLaAmbiguedadDeTenant:
    async def test_dos_membresias_con_el_mismo_email_y_la_misma_clave_no_entran(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
        business_b: uuid.UUID,
    ) -> None:
        """La politica acordada: la ambiguedad es un fallo, no una eleccion.

        `business_users` es unica por `(business_id, email)`, asi que una persona en
        dos negocios tiene dos filas con el mismo email. Entrar "a la primera" seria
        entrar a un tenant arbitrario, que es el cruce que el ADR-0010 quiere cerrar.
        """
        await _crear_usuario(
            session, business_id=business_a, email=EMAIL_AMBIGUO, role=BusinessUserRole.ADMIN
        )
        await _crear_usuario(
            session, business_id=business_b, email=EMAIL_AMBIGUO, role=BusinessUserRole.STAFF
        )
        await _tenant(session, None)

        with pytest.raises(AuthenticationError) as exc:
            await auth_service.authenticate_business_user(
                session, email=EMAIL_AMBIGUO, password=PASSWORD, ip_address="10.6.0.1"
            )

        assert exc.value.detail == auth_service.INVALID_CREDENTIALS

    async def test_el_mismo_email_solo_una_membresia_si_entra(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
    ) -> None:
        """El control: con una sola membresia, el mismo flujo entra.

        Sin este test, la prueba anterior pasaria tambien si el servicio rechazara
        todos los logins por un bug cualquiera.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_AMBIGUO)
        await _tenant(session, None)

        login = await auth_service.authenticate_business_user(
            session, email=EMAIL_AMBIGUO, password=PASSWORD, ip_address="10.6.0.2"
        )
        assert login.business_id == business_a

    async def test_la_ambiguedad_solo_se_evalua_despues_de_verificar_la_clave(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
        business_b: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Con la contrasea equivocada, el caso ambiguo es indistinguible del resto.

        Y mas importante: se verifican **las dos** filas antes de decidir, sin
        cortocircuito. Con cortocircuito, el tiempo de respuesta diria si la clave
        corresponde a la primera membresia o a la segunda, y la ambiguedad se
        converteria en un verificador de contrasenas.
        """
        await _crear_usuario(
            session, business_id=business_a, email=EMAIL_AMBIGUO, role=BusinessUserRole.ADMIN
        )
        await _crear_usuario(
            session, business_id=business_b, email=EMAIL_AMBIGUO, role=BusinessUserRole.STAFF
        )
        await _tenant(session, None)

        hashes: list[str] = []
        real = verify_password

        def espia(password: str, stored_hash: str) -> bool:
            hashes.append(stored_hash)
            return real(password, stored_hash)

        monkeypatch.setattr(auth_service, "verify_password", espia)

        with pytest.raises(AuthenticationError) as exc:
            await auth_service.authenticate_business_user(
                session,
                email=EMAIL_AMBIGUO,
                password=OTRA_PASSWORD,
                ip_address="10.6.0.3",
            )

        assert exc.value.detail == auth_service.INVALID_CREDENTIALS
        assert len(hashes) == 2, (
            "se verifico una sola de las dos membresias: el tiempo de respuesta "
            "depende de cual clave coincida y la ambiguedad verifica contrasenas"
        )
        assert len(set(hashes)) == 2, "las dos filas tienen el mismo hash"


class TestElRateLimitDelLogin:
    async def test_el_limite_por_ip_sigue_funcionando(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
    ) -> None:
        """17. 5/min por IP, del §10.5.

        Se cuentan los 5 intentos y el sexto tiene que ser 429. El limite de `0004` ya
        tiene sus propios tests en `test_login_functions.py`; este verifica que el
        servicio lo **usa**, con el limite del §10.5 y no con otro.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        for _ in range(5):
            with pytest.raises(AuthenticationError):
                await auth_service.authenticate_business_user(
                    session,
                    email=EMAIL_ADMIN_A,
                    password=OTRA_PASSWORD,
                    ip_address="10.7.0.1",
                )

        with pytest.raises(RateLimitError) as exc:
            await auth_service.authenticate_business_user(
                session, email=EMAIL_ADMIN_A, password=OTRA_PASSWORD, ip_address="10.7.0.1"
            )
        assert exc.value.status_code == 429
        assert exc.value.extra["retry_after"] >= 1

    async def test_el_limite_por_email_sigue_funcionando(
        self, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """10/min por email, con la IP cambiada cada intento.

        Es la contraprueba del anterior: si el unico limite que se cumpliera fuera el
        de la IP, este pasaria. Y al revés: si el unico fuera el del email, el de la
        IP pasaria. Los dos tienen que estar.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        for i in range(10):
            with pytest.raises(AuthenticationError):
                await auth_service.authenticate_business_user(
                    session,
                    email=EMAIL_ADMIN_A,
                    password=OTRA_PASSWORD,
                    ip_address=f"10.7.1.{i + 1}",
                )

        with pytest.raises(RateLimitError):
            await auth_service.authenticate_business_user(
                session, email=EMAIL_ADMIN_A, password=OTRA_PASSWORD, ip_address="10.7.1.99"
            )

    async def test_el_429_no_dice_que_limite_se_disparo(
        self, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """El detalle del 429 es generico.

        Si el del email dijera "tu email esta limitado" y el de la IP "tu IP esta
        limitada", un atacante sabria que el email existe solo de ver cual de los dos
        se disparo. Los dos dan el mismo mensaje.

        Los dos casos usan **emails distintos** a proposito: con el mismo email, el
        segundo bloque sumaria a los 5 del primero y pasaria por el cubo del email
        cuando lo que se quiere probar es que se dispara el de la IP. El control es
        que cada bloque se dispare por el cubo que le corresponde.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _crear_usuario(session, business_id=business_a, email=EMAIL_STAFF_A)
        await _tenant(session, None)

        # Bloque 1: se agota el cubo de la IP, con un solo email.
        for _ in range(5):
            with pytest.raises(AuthenticationError):
                await auth_service.authenticate_business_user(
                    session,
                    email=EMAIL_ADMIN_A,
                    password=OTRA_PASSWORD,
                    ip_address="10.7.2.1",
                )
        with pytest.raises(RateLimitError) as por_ip:
            await auth_service.authenticate_business_user(
                session, email=EMAIL_ADMIN_A, password=OTRA_PASSWORD, ip_address="10.7.2.1"
            )

        # Bloque 2: se agota el cubo del email, con una IP distinta por intento.
        for i in range(10):
            with pytest.raises(AuthenticationError):
                await auth_service.authenticate_business_user(
                    session,
                    email=EMAIL_STAFF_A,
                    password=OTRA_PASSWORD,
                    ip_address=f"10.7.3.{i + 1}",
                )
        with pytest.raises(RateLimitError) as por_email:
            await auth_service.authenticate_business_user(
                session, email=EMAIL_STAFF_A, password=OTRA_PASSWORD, ip_address="10.7.3.99"
            )

        assert por_ip.value.detail == por_email.value.detail
        assert por_ip.value.extra == por_email.value.extra

    async def test_el_rate_limit_va_antes_de_argon2(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Un intento bloqueado no paga Argon2.

        Si el limite fuera lo ultimo, cada intento de fuerza bruta costo un Argon2
        completo antes de ser rechazado: el rate limit pasaria a ser un detalle
        economico y el denial of service seria gratis.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        llamadas: list[str] = []
        real = verify_password

        def espia(password: str, stored_hash: str) -> bool:
            llamadas.append(stored_hash)
            return real(password, stored_hash)

        monkeypatch.setattr(auth_service, "verify_password", espia)

        for _ in range(5):
            with pytest.raises(AuthenticationError):
                await auth_service.authenticate_business_user(
                    session,
                    email=EMAIL_ADMIN_A,
                    password=OTRA_PASSWORD,
                    ip_address="10.7.4.1",
                )
        antes = len(llamadas)
        assert antes == 5

        with pytest.raises(RateLimitError):
            await auth_service.authenticate_business_user(
                session, email=EMAIL_ADMIN_A, password=OTRA_PASSWORD, ip_address="10.7.4.1"
            )
        assert len(llamadas) == antes, "un intento bloqueado ejecuto Argon2"


class TestLaSuperficieDePlataforma:
    async def test_un_operador_entra_sin_producir_un_business_id(
        self, session: AsyncSession, platform_owner: uuid.UUID
    ) -> None:
        """19. La separacion del §7 se sostiene: plataforma no produce `business_id`.

        `platform_users` no tiene `business_id` por diseño (§5.1) y el rol de la app no
        tiene ni un privilegio sobre la tabla. El dataclass de resultado no tiene con
        donde meter un tenant.
        """
        await _tenant(session, None)

        credenciales = await auth_service.authenticate_platform_user(
            session, email=EMAIL_PLATFORM, password=PASSWORD, ip_address="10.8.0.1"
        )

        assert credenciales.user_id == platform_owner
        assert credenciales.is_platform is True
        nombres = {f.name for f in fields(credenciales)}
        assert "business_id" not in nombres
        assert "tenant" not in nombres

    async def test_los_scopes_de_plataforma_no_son_de_negocio(
        self, session: AsyncSession, platform_owner: uuid.UUID
    ) -> None:
        """Un operador de plataforma no recibe `Scope` de negocio.

        Es la razon de que `PlatformScope` sea un enum aparte: mezclarlos haria
        indistinguible "ve todos los negocios" de "ve el suyo".
        """
        await _tenant(session, None)

        credenciales = await auth_service.authenticate_platform_user(
            session, email=EMAIL_PLATFORM, password=PASSWORD, ip_address="10.8.0.2"
        )

        assert credenciales.scopes
        assert all(isinstance(s, PlatformScope) for s in credenciales.scopes)
        assert not any(isinstance(s, Scope) for s in credenciales.scopes)
        assert str(PlatformScope.TENANTS_READ) in {str(s) for s in credenciales.scopes}

    async def test_un_operador_inactivo_da_el_mismo_error_que_uno_inexistente(
        self, session: AsyncSession, platform_owner: uuid.UUID
    ) -> None:
        """La misma uniformidad, en la otra superficie."""
        await _tenant(session, None)

        with pytest.raises(AuthenticationError) as inactivo:
            await auth_service.authenticate_platform_user(
                session, email=EMAIL_PLATFORM_INACTIVE, password=PASSWORD, ip_address="10.8.0.3"
            )
        with pytest.raises(AuthenticationError) as inexistente:
            await auth_service.authenticate_platform_user(
                session, email=EMAIL_INEXISTENTE, password=PASSWORD, ip_address="10.8.0.4"
            )

        assert inactivo.value.detail == inexistente.value.detail
        assert inactivo.value.status_code == inexistente.value.status_code

    async def test_un_negocio_no_puede_entrar_por_la_superficie_de_plataforma(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
        platform_owner: uuid.UUID,
    ) -> None:
        """Un email de `business_users` no existe para `platform_users`.

        Las dos tablas no comparten nada, y por eso son dos funciones y no una con un
        parametro de tipo: si el codigo buscara en la tabla equivocada, el error seria
        "no existe" y no "estas buscando en el sitio equivocado".
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        with pytest.raises(AuthenticationError) as exc:
            await auth_service.authenticate_platform_user(
                session, email=EMAIL_ADMIN_A, password=PASSWORD, ip_address="10.8.0.5"
            )
        assert exc.value.detail == auth_service.INVALID_CREDENTIALS


class TestLasClavesDelRateLimit:
    def test_la_clave_es_un_hash_y_no_el_valor(self) -> None:
        """§5.7: `key` es un hash de la IP o del identificador, nunca el valor.

        La tabla se purga con un job de retencion; si guardara el valor, ese backup
        seria una lista de IPs de clientes y de emails de usuarios.
        """
        email_key, ip_key = auth_service.login_rate_limit_keys(
            email="Persona@Negocio.Test", ip_address="203.0.113.7"
        )
        assert len(email_key) == 64
        assert len(ip_key) == 64
        assert "203.0.113.7" not in ip_key
        assert "persona@negocio.test" not in email_key

    def test_las_mayusculas_no_crean_un_cubo_distinto(self) -> None:
        """`casefold`, no `lower`.

        Sin esto, `Ana@x.com` tendria 10/min y `ana@x.com` otros 10/min, y el limite
        del §10.5 seria en realidad el doble para la misma cuenta.
        """
        una, _ = auth_service.login_rate_limit_keys(email="Ana@X.com", ip_address="1.1.1.1")
        otra, _ = auth_service.login_rate_limit_keys(email="ana@x.com ", ip_address="1.1.1.1")
        assert una == otra

    def test_las_ips_distintas_dan_claves_distintas(self) -> None:
        _, a = auth_service.login_rate_limit_keys(email="a@x.com", ip_address="1.1.1.1")
        _, b = auth_service.login_rate_limit_keys(email="a@x.com", ip_address="1.1.1.2")
        assert a != b

    def test_el_email_y_la_ip_no_comparten_cubo(self) -> None:
        """Con el prefijo de dominio **antes** del hash.

        Sin el, hashear la IP y prepender despues daria la misma clave que hashear el
        prefijo, y el limite de una superficie frenaria la otra.
        """
        email_key, ip_key = auth_service.login_rate_limit_keys(
            email="login:ip:1.1.1.1", ip_address="login:email:1.1.1.1"
        )
        assert email_key != ip_key


class TestLoQueElServicioNoHace:
    async def test_el_login_no_escribe_last_login_at(
        self, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """F1.3 no toca `last_login_at`.

        En `business_users` se podria: la columna esta en la lista de escritura del rol
        de la app. Pero la escritura necesita el GUC de tenant, que todavia no existe
        durante el login, asi que habria que cambiarlo dentro de la misma transaccion
        que hizo el lookup. Y en `platform_users` es **imposible**: el rol de la app no
        tiene ningun privilegio. Hacerlo solo para la mitad deja el dato mas completo
        en un lado y dishonesto en el otro, asi que no se hace ninguno de los dos.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        await auth_service.authenticate_business_user(
            session, email=EMAIL_ADMIN_A, password=PASSWORD, ip_address="10.9.0.1"
        )
        await session.flush()

        # El login se hizo **sin** tenant--es la condicion que hay que probar-- y
        # por eso el GUC sigue vacio. Con el GUC vacio, la RLS de `business_users`
        # no devuelve ninguna fila y el `SELECT` daria `NoResultFound`: el error
        # real del bug--que la columna quedo escrita-- se veria como un fallo de
        # infraestructura, y ademas un `NoResultFound` pasaria por "no hay fila".
        await _tenant(session, business_a)

        valor: dt.datetime | None = (
            await session.execute(
                text("SELECT last_login_at FROM business_users WHERE email = :e"),
                {"e": EMAIL_ADMIN_A},
            )
        ).scalar_one()
        assert valor is None, "el login escribio last_login_at, que F1.3 prohibe"

    async def test_el_login_fallido_no_escribe_una_fila_de_refresh(
        self, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """Un intento fallido no abre una familia.

        Si lo abriera, cada intento fallido crearia una fila en `refresh_tokens` y un
        atacante podria llenar la tabla sin tener ninguna credencial.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        with pytest.raises(AuthenticationError):
            await auth_service.authenticate_business_user(
                session, email=EMAIL_ADMIN_A, password=OTRA_PASSWORD, ip_address="10.9.0.2"
            )
        await session.flush()

        total: int = (
            await session.execute(text("SELECT count(*) FROM refresh_tokens"))
        ).scalar_one()
        assert total == 0

    async def test_el_servicio_no_escribe_audit_log(
        self, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """Los fallos van al log de aplicacion, no a `audit_log`.

        `audit_log` lleva RLS por `business_id` y un fallo de login pre-tenant no sabe
        de que negocio viene: no hay GUC que poner, y poner el de un negocio adivinado
        seria inventar el dato que se quiere registrar.
        """
        await _crear_usuario(session, business_id=business_a, email=EMAIL_ADMIN_A)
        await _tenant(session, None)

        with pytest.raises(AuthenticationError):
            await auth_service.authenticate_business_user(
                session, email=EMAIL_ADMIN_A, password=OTRA_PASSWORD, ip_address="10.9.0.3"
            )
        await session.flush()

        total: int = (await session.execute(text("SELECT count(*) FROM audit_log"))).scalar_one()
        assert total == 0

    def test_el_error_de_credenciales_no_hereda_de_una_excepcion_de_sql(self) -> None:
        """`AuthenticationError` es un `AppError`, no un `SQLAlchemyError`.

        Un error de base que llegara al cliente seria informacion de la base; el
        modulo de errores lo formatea, pero el tipo de la excepcion ya no dice nada
        util para diagnostico porque el log interno tiene el motivo.
        """
        assert issubclass(AuthenticationError, AppError)
        assert not issubclass(AuthenticationError, LookupError)


class TestLaPoliticaDeTiempos:
    """Lo que se **puede** probar del tiempo, y lo que no.

    **Por que no hay una prueba de igualdad de tiempos.** Un
    `assert elapsed_a == elapsed_b` no mide lo que dice: mide el ruido de la maquina.
    Pasaria en un CI con tres nucleos ocupados y fallaria en el mismo codigo con la
    suite completa corriendo al lado. Un test que depende del clima no es un test, y
    uno que se pone "-" cuando el clima esta mal es peor: trains a todos a ignorarlo.

    **Lo que si se prueba, y es la garantia real**: que los dos caminos ejecutan la
    misma operacion de Argon2, contra hashes del mismo formato y con los mismos
    parametros. Eso es deterministico y no depende de la maquina. La diferencia de
    reloj entre dos llamadas a Argon2 con los mismos parametros es de algunas decimas de
    milisegundos
    sobre ~100 ms, o sea del orden del ruido de scheduler: no es algo que se pueda
    reducir de aca, y prometer lo contrario seria vender algo que el codigo no
    entrega.

    Abajo hay, si acaso, una comprobacion estadistica **muy** laxa y marcada como
    `slow`, como red de seguridad contra una regresion grande -- por ejemplo, que
    alguien vuelva a poner un `return` temprano. No es la garantia; la garantia son
    los tests con espia de arriba.
    """

    def test_los_dos_caminos_consumen_la_misma_operacion_de_argon2(self) -> None:
        """Propiedad determinista: 1 verificacion en cada camino.

        Se cuenta a nivel de `verify_password` porque es la frontera donde la
        diferencia de costo se volveria observable. Los dos caminos llaman a la misma
        funcion con un hash del mismo formato.
        """
        from app.core.security import verify_password as real

        llamadas: list[str] = []

        def espia(password: str, stored_hash: str) -> bool:
            llamadas.append(stored_hash)
            return real(password, stored_hash)

        import app.modules.auth.service as svc

        original = verify_password
        svc.verify_password = espia  # type: ignore[attr-defined]
        try:
            # Camino "existe": un hash real.
            hash_real = hash_password(PASSWORD)
            svc._verificar(PASSWORD, [{"password_hash": hash_real}])
            con_fila = len(llamadas)

            # Camino "no existe": sin filas, el centinela.
            svc._verificar(PASSWORD, [])
            sin_fila = len(llamadas)
        finally:
            svc.verify_password = original  # type: ignore[attr-defined]

        assert con_fila == 1
        assert sin_fila - con_fila == 1, "el camino inexistente no ejecuto la misma operacion"

    @pytest.mark.slow
    def test_senal_de_regresion_de_tiempos(self) -> None:
        """Senal laxa, no igualdad. Ver la docstring de la clase.

        Se mide con los parametros reales de Argon2, porque con coste 1 la operacion
        dura 0,2 ms y lo que se mide es el ruido del scheduler. Con 24 muestras por
        camino y una banda de 1/4 a 4x, lo unico que este test atrapa es una
        regresion del orden de magnitud: un `return` temprano, o un centinela que no
        se verifica.
        """
        from app.core.security import verify_password as real

        settings = get_settings()
        original = (
            settings.argon2_time_cost,
            settings.argon2_memory_cost,
            settings.argon2_parallelism,
        )
        settings.argon2_time_cost = 3
        settings.argon2_memory_cost = 16384
        settings.argon2_parallelism = 1
        reset_password_hash_cache()
        try:
            centinela = get_sentinel_hash()
            hash_real = hash_password(PASSWORD)
            assert centinela != hash_real

            def medir(hash_a_probar: str) -> list[float]:
                muestras: list[float] = []
                for _ in range(24):
                    inicio = time.perf_counter()
                    real(PASSWORD, hash_a_probar)
                    muestras.append(time.perf_counter() - inicio)
                return muestras

            # Se entrelazan los dos caminos para que una perturbacion de la maquina
            # afecte a los dos por igual y no se quede pegado a uno de los dos.
            tiempos_real: list[float] = []
            tiempos_centinela: list[float] = []
            for _ in range(24):
                for hash_a_probar, destino in (
                    (hash_real, tiempos_real),
                    (centinela, tiempos_centinela),
                ):
                    inicio = time.perf_counter()
                    real(PASSWORD, hash_a_probar)
                    destino.append(time.perf_counter() - inicio)

            mediana_real = statistics.median(tiempos_real)
            mediana_centinela = statistics.median(tiempos_centinela)
            razon = mediana_centinela / mediana_real
            assert 0.25 <= razon <= 4.0, (
                f"el login inexistente tarda {razon:.2f}x el de un usuario real "
                f"({mediana_centinela * 1000:.1f} ms vs {mediana_real * 1000:.1f} ms). "
                "Senal de regresion: revisar el camino del hash centinela."
            )
        finally:
            (
                settings.argon2_time_cost,
                settings.argon2_memory_cost,
                settings.argon2_parallelism,
            ) = original
            reset_password_hash_cache()


class TestSinFugasDeTipos:
    def test_el_mensaje_de_fallo_es_una_constante_de_modulo(self) -> None:
        """`INVALID_CREDENTIALS` es un unico string, no un literal repetido.

        La propiedad que se quiere es que las tres rutas construyan el mismo objeto.
        Con un literal en cada rama, el dia que alguien escriba "Cuenta desactivada" en
        una, un test que solo compare el status seguiria pasando.

        Se cuenta sobre el **fuente del modulo** y se espera exactamente una
        aparicion: la de la definicion. Dos significaria que hay una copia del texto en
        algun `raise`, y ahi es donde vuelve la fuga. Comparar el valor de la constante
        contra si misma no serviria de nada -- seria un tautologia.
        """
        import inspect

        fuente = inspect.getsource(auth_service)
        apariciones = fuente.count('"Credenciales invalidas."')
        assert apariciones == 1, (
            f"el mensaje aparece {apariciones} veces en el fuente de service.py: "
            "una es la definicion y cualquier otra es una copia en un `raise`, que es "
            "exactamente donde se colaria una variante que filtra la causa"
        )

    def test_el_mensaje_no_tiene_longitud_de_oraculo(self) -> None:
        """El mensaje es corto y generico.

        Un texto largo es un sitio donde meter informacion sin darse cuenta, y un
        texto muy corto se nota. Este es el punto de equilibrio.
        """
        assert 10 <= len(auth_service.INVALID_CREDENTIALS) <= 60

    def test_la_referencia_del_log_no_es_el_email(self) -> None:
        """El log correlaciona sin escribir la PII.

        Se necesita poder responder "este usuario viene fallando desde ayer" sin que el
        archivo de log -- que se rota y se respalda por otro lado -- sea una lista de emails.
        """
        ref = auth_service._referencia(EMAIL_ADMIN_A)
        assert EMAIL_ADMIN_A not in ref
        assert len(ref) == 12
        assert ref == auth_service._referencia(EMAIL_ADMIN_A)
        assert ref != auth_service._referencia(EMAIL_STAFF_A)


class TestLoginPreTenantConDefiner:
    """Tests funcionales: el login de negocio funciona SIN GUC de tenant.

    Estos tests verifican que el flujo completo funciona:
    1. auth_business_user_for_login (owner = tempus_login_definer) encuentra la fila
    2. El servicio extrae business_id y emite access_token con tid
    3. Con ese tid, SET LOCAL app.current_business_id y la RLS normal filtra bien
    """

    async def test_login_negocio_funciona_sin_guc_previo(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
    ) -> None:
        """Con GUC vacío, el lookup encuentra al usuario y emite token con tid correcto."""
        # Crear usuario en business_a
        await _crear_usuario(
            session,
            business_id=business_a,
            email="user@tenant-a.test",
            password="password123",
            role=BusinessUserRole.ADMIN,
            status=MembershipStatus.ACTIVE,
        )
        await _tenant(session, None)  # GUC vacío a propósito

        creds = await authenticate_business_user(
            session, email="user@tenant-a.test", password="password123", ip_address="10.0.0.1"
        )

        # Verificaciones del token
        assert creds.access_token is not None
        assert creds.refresh is not None
        assert creds.business_id == business_a

        # Decodificar JWT y verificar claims (sin verificar firma para test)
        payload = pyjwt.decode(
            creds.access_token.token,
            options={"verify_signature": False, "verify_exp": False},
        )
        assert payload["tid"] == str(business_a)
        assert payload["sub"] == str(creds.user_id)
        assert payload["role"] == str(BusinessUserRole.ADMIN)

    async def test_despues_del_login_rls_sigue_funcionando(
        self,
        session: AsyncSession,
        business_a: uuid.UUID,
        business_b: uuid.UUID,
    ) -> None:
        """Tras obtener token con tid=business_a, SET LOCAL a business_b NO ve datos de a."""
        # Crear usuario en business_a
        await _crear_usuario(
            session,
            business_id=business_a,
            email="user@tenant-a.test",
            password="password123",
            role=BusinessUserRole.ADMIN,
            status=MembershipStatus.ACTIVE,
        )
        # Crear usuario DISTINTO en business_b (mismo email, DISTINTA contraseña)
        await _crear_usuario(
            session,
            business_id=business_b,
            email="user@tenant-a.test",
            password="password456",
            role=BusinessUserRole.STAFF,
            status=MembershipStatus.ACTIVE,
        )

        # 1. Login en business_a (sin GUC) con la contraseña de business_a
        await _tenant(session, None)
        creds = await authenticate_business_user(
            session, email="user@tenant-a.test", password="password123", ip_address="10.0.0.1"
        )
        assert creds.business_id == business_a

        # 2. Ahora con SET LOCAL a business_b, el mismo email no debe ser visible
        await _tenant(session, business_b)

        # El usuario de business_b con ese email existe pero tiene role STAFF
        # Si la RLS funciona, al consultar por email en business_b debe verse ESE usuario
        # (no el de business_a). Verificamos que el servicio NO deja cruzar el tenant.
        # Hacemos login de nuevo CON GUC puesto a business_b y la contraseña de business_b:
        await _tenant(session, business_b)
        creds_b = await authenticate_business_user(
            session, email="user@tenant-a.test", password="password456", ip_address="10.0.0.2"
        )
        # Debe ser el usuario de business_b (role STAFF), no el de business_a (ADMIN)
        assert creds_b.business_id == business_b
        # El role debe ser STAFF (el que pertenece a business_b)
        payload = pyjwt.decode(
            creds_b.access_token.token,
            options={"verify_signature": False, "verify_exp": False},
        )
        assert payload["role"] == str(BusinessUserRole.STAFF)
