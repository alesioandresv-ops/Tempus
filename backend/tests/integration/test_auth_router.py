"""Tests de contrato para el router de autenticación.

Verifica el comportamiento HTTP de los endpoints de login y refresh,
incluyendo cookies, headers, códigos de estado y cuerpos de respuesta.

**Los correos de estos tests usan dominios reales a proposito.** `LoginRequest`
declara `email: EmailStr`, y `EmailStr` rechaza los dominios de uso especial
(`.test`, `.invalid`, `.localhost`, `.example`) salvo que se le pase
`test_environment=True`. Con `@negocio.test` el endpoint respondia 422 en vez de
200--o 401-- y todos los tests de este archivo fallaban por el mismo motivo, sin
que ninguno tocara el codigo que estaba probando: la validacion--correcta-- se
comia la respuesta. Los tests de servicio si pueden usar `.test`, porque no pasan
por el esquema.
"""

from __future__ import annotations

import uuid

import jwt as pyjwt
import pytest
from app.core.config import get_settings
from app.main import create_app
from app.models.enums import BusinessUserRole, MembershipStatus, PlatformRole
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _crear_usuario_negocio(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    email: str,
    password: str = "password123",
    role: BusinessUserRole = BusinessUserRole.ADMIN,
    status: MembershipStatus = MembershipStatus.ACTIVE,
) -> uuid.UUID:
    """Inserta un usuario de negocio con el rol de la app y el GUC puesto."""
    from app.core.security import hash_password
    from sqlalchemy import text

    await session.execute(
        text("SELECT set_config('app.current_business_id', :tenant, true)"),
        {"tenant": str(business_id)},
    )
    user_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO business_users
            (id, business_id, email, password_hash, full_name, role, status)
            VALUES (:id, :business_id, :email, :hash, 'Persona', :role, :status)
            """
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


@pytest.fixture
def business_a() -> uuid.UUID:
    """UUID fijo para negocio A."""
    return uuid.UUID("11111111-1111-7111-8111-111111111111")


@pytest.fixture
def platform_owner_uuid() -> uuid.UUID:
    """UUID fijo para operador de plataforma."""
    return uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


#: Operador de plataforma de los tests de este archivo. Fijo y no `uuid4()` para
#: que un fallo se pueda reproducir a mano copiando el `INSERT` de abajo.
PLATFORM_OWNER = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PLATFORM_OWNER_EMAIL = "owner@plataforma.com.ar"


async def _crear_usuario_plataforma(migration_database_url: str) -> None:
    """Siembra el operador de plataforma con el rol de DDL y lo commitea.

     Va por el rol de DDL y no por el de la app a proposito: `platform_users` esta
     en `FORBIDDEN_FOR_APP_ROLE`, asi que no hay otra forma de sembrarlo sin
     falsear la prueba. Por eso **commitea** en vez de dejar la fila pendiente como
     `_crear_usuario_negocio`: el login de plataforma no pasa por el GUC y su
    Lookup es una funcion `SECURITY DEFINER`, asi que puede ver la fila sin
     necesidad de que este test comparta transaccion con el router.

     **El `DELETE` va por `id`, no por `email`.** Con `email` el test pasaba mientras
     la fila con el `id` fijo no existiera, y en cuanto existio--con otro `email`,
     de una corrida anterior-- el `INSERT` reventaba con `pk_platform_users` y el
     error no decia que el problema era del propio test.
    """
    from app.core.security import hash_password
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(migration_database_url, poolclass=None, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM platform_users WHERE id = :id OR email = :email"),
                {"id": PLATFORM_OWNER, "email": PLATFORM_OWNER_EMAIL},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO platform_users
                    (id, email, password_hash, full_name, role, is_active)
                    VALUES (:id, :email, :hash, 'Owner', :role, true)
                    """
                ),
                {
                    "id": PLATFORM_OWNER,
                    "email": PLATFORM_OWNER_EMAIL,
                    "hash": hash_password("password123"),
                    "role": str(PlatformRole.OWNER),
                },
            )
    finally:
        await engine.dispose()


class TestBusinessLogin:
    """Tests para POST /api/v1/auth/login (negocio)."""

    async def test_login_exitoso_retorna_access_token_y_cookie(
        self, http_client: AsyncClient, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """Login exitoso retorna access_token en JSON y refresh_token en cookie HttpOnly."""
        await _crear_usuario_negocio(session, business_id=business_a, email="user-a@negocio.com.ar")

        response = await http_client.post(
            "/api/v1/auth/login",
            json={"email": "user-a@negocio.com.ar", "password": "password123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "expires_in" in data
        assert isinstance(data["expires_in"], int)
        assert data["expires_in"] > 0

        # Verificar cookie refresh_token
        cookies = response.cookies
        assert "refresh_token" in cookies
        refresh_cookie = cookies["refresh_token"]
        assert refresh_cookie is not None

    async def test_login_invalido_retorna_401_rfc9457(
        self, http_client: AsyncClient, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """Credenciales inválidas retornan 401 con body RFC 9457 y WWW-Authenticate."""
        await _crear_usuario_negocio(session, business_id=business_a, email="user-a@negocio.com.ar")

        response = await http_client.post(
            "/api/v1/auth/login",
            json={"email": "user-a@negocio.com.ar", "password": "wrongpassword"},
        )

        assert response.status_code == 401
        assert response.headers.get("content-type") == "application/problem+json"
        assert response.headers.get("WWW-Authenticate") == 'Bearer realm="tempus"'

        data = response.json()
        assert data["status"] == 401
        assert data["title"] == "No autenticado"
        assert data["detail"] == "Credenciales invalidas."
        assert data["instance"] == "/api/v1/auth/login"
        assert data["type"] == "about:blank"

    async def test_login_rate_limit_por_ip(
        self, http_client: AsyncClient, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """5 intentos fallidos por IP -> 6to es 429 con Retry-After.

        **Este test no alcanza para detectar que el limitador no contaba.** Pasa por
        el fixture `http_client`, que overridea `get_session` con la sesion del test,
        y esa sesion no hace rollback cuando el handler levanta. El hit del cubo
        sobrevive porque nadie lo deshace, no porque el codigo lo haga sobrevivir.
        Contra el servidor real, donde la dependencia es `session_scope`, el mismo
        intento se perdia. Para eso esta
        `test_el_limite_sobrevive_al_rollback_de_la_peticion`, que va por la app sin
        overrides.
        """
        await _crear_usuario_negocio(session, business_id=business_a, email="user-a@negocio.com.ar")

        for _ in range(5):
            response = await http_client.post(
                "/api/v1/auth/login",
                json={"email": "user-a@negocio.com.ar", "password": "wrongpassword"},
            )
            assert response.status_code == 401

        response = await http_client.post(
            "/api/v1/auth/login",
            json={"email": "user-a@negocio.com.ar", "password": "wrongpassword"},
        )
        assert response.status_code == 429
        assert "Retry-After" in response.headers
        retry_after = int(response.headers["Retry-After"])
        assert retry_after >= 1

    async def test_el_limite_sobrevive_al_rollback_de_la_peticion(self) -> None:
        """El intento fallido tiene que quedar contado despues del rollback.

        Este es el test que Habria detectado que el rate limit no limitaba. Va **sin
        overrides a proposito**: la app usa su `session_scope` de verdad, el mismo que
        corre en produccion, y ese `session_scope` responde con `rollback` a la
        excepcion del login fallido.

        El bug era que `rate_limit_hit` escribia en la transaccion de la peticion, asi
        que el hit se iba con el rollback: el limitador contaba unicamente los logins
        **exitosos**, que es lo contrario de lo que debe hacer. Diez intentos con
        contrasena mala desde la misma IP daban diez 401 y ningun 429, y el cubo del
        email quedaba sin una sola fila.

        Con el override de `http_client` el test anterior daba verde igual, porque la
        sesion del test no deshace nada. Por eso este construye su propio cliente.

        No siembra nada a proposito: un email que no existe tambien pasa por el
        limitador--que corre antes del lookup-- y asi el test no depende de que el
        override vea las filas sin comitear.
        """
        from httpx import ASGITransport
        from httpx import AsyncClient as _AsyncClient

        app = create_app(get_settings())
        transporte = ASGITransport(app=app)

        async with _AsyncClient(transport=transporte, base_url="http://test") as cliente:
            codigos = []
            for _ in range(7):
                respuesta = await cliente.post(
                    "/api/v1/auth/login",
                    json={
                        "email": "nadie-aqui@negocio.com.ar",
                        "password": "contrasena-equivocada",
                    },
                )
                codigos.append(respuesta.status_code)
                if respuesta.status_code == 429:
                    break

        assert 401 in codigos, f"un login con contrasena mala tiene que ser 401: {codigos}"
        assert 429 in codigos, (
            "el limite por IP no llego a dispararse: los intentos fallidos no se "
            f"estaban contando. Codigos={codigos}"
        )
        # Y el 429 tiene que venir con el tiempo de espera, no ser un error generico.
        assert respuesta.headers.get("Retry-After"), "el 429 no trae Retry-After"

    async def test_login_no_envia_refresh_en_json(
        self, http_client: AsyncClient, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """El refresh token NO aparece en el cuerpo JSON, solo en cookie."""
        await _crear_usuario_negocio(session, business_id=business_a, email="user-a@negocio.com.ar")

        response = await http_client.post(
            "/api/v1/auth/login",
            json={"email": "user-a@negocio.com.ar", "password": "password123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "refresh_token" not in data
        assert "refresh_token" not in str(response.json())


class TestPlatformLogin:
    """Tests para POST /api/v1/auth/platform/login."""

    async def test_platform_login_exitoso(
        self, http_client: AsyncClient, migration_database_url: str
    ) -> None:
        """Login de plataforma exitoso retorna access_token con claims correctos."""
        await _crear_usuario_plataforma(migration_database_url)

        response = await http_client.post(
            "/api/v1/auth/platform/login",
            json={"email": PLATFORM_OWNER_EMAIL, "password": "password123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

        # Verificar claims del token de plataforma
        claims = pyjwt.decode(
            data["access_token"],
            options={"verify_signature": False, "verify_exp": False},
        )
        assert claims["is_platform"] is True
        assert "tid" not in claims or claims.get("tid") is None
        assert claims["role"] == "owner"
        assert "scopes" in claims
        assert "platform:tenants:read" in claims["scopes"]

    async def test_platform_login_invalido_retorna_401(
        self, http_client: AsyncClient, migration_database_url: str
    ) -> None:
        """Credenciales inválidas en plataforma retornan 401 uniforme."""
        response = await http_client.post(
            "/api/v1/auth/platform/login",
            json={"email": "owner@plataforma.com.ar", "password": "wrongpassword"},
        )

        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == 'Bearer realm="tempus"'
        data = response.json()
        assert data["detail"] == "Credenciales invalidas."

    async def test_platform_token_no_tiene_tid(
        self, http_client: AsyncClient, migration_database_url: str
    ) -> None:
        """Token de plataforma NO tiene claim tid."""
        await _crear_usuario_plataforma(migration_database_url)

        response = await http_client.post(
            "/api/v1/auth/platform/login",
            json={"email": PLATFORM_OWNER_EMAIL, "password": "password123"},
        )
        assert response.status_code == 200, response.text

        claims = pyjwt.decode(
            response.json()["access_token"],
            options={"verify_signature": False, "verify_exp": False},
        )
        assert "tid" not in claims


class TestRefreshEndpoint:
    """Tests para POST /api/v1/auth/refresh."""

    async def test_refresh_sin_cookie_retorna_401(self, http_client: AsyncClient) -> None:
        """Con CSRF y Origin validos pero sin cookie, el refresh da 401.

        Los headers van porque `verify_csrf_and_origin` corre **antes** que el
        handler. Sin ellos la respuesta es 403 por CSRF y el test pasaria por el
        motivo equivocado: estaria comprobando dos veces el control de CSRF y
        nunca el "no hay token" del refresh. El orden de las dependencias importa
        aca, y por eso el test lo dice.
        """
        response = await http_client.post(
            "/api/v1/auth/refresh",
            headers={
                "X-CSRF-Token": "valid-token",
                "Origin": "http://localhost:5173",
            },
        )
        assert response.status_code == 401

    async def test_refresh_sin_csrf_retorna_403(self, http_client: AsyncClient) -> None:
        """Sin header X-CSRF-Token retorna 403."""
        response = await http_client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": "http://localhost:5173"},
        )
        assert response.status_code == 403
        data = response.json()
        assert "CSRF token" in data["detail"]

    async def test_refresh_sin_origin_retorna_403(self, http_client: AsyncClient) -> None:
        """Sin header Origin retorna 403."""
        response = await http_client.post(
            "/api/v1/auth/refresh",
            headers={"X-CSRF-Token": "valid-token"},
        )
        assert response.status_code == 403

    async def test_refresh_origin_no_permitido_retorna_403(self, http_client: AsyncClient) -> None:
        """Origin no en cors_origins retorna 403."""
        response = await http_client.post(
            "/api/v1/auth/refresh",
            headers={
                "X-CSRF-Token": "valid-token",
                "Origin": "http://malicioso.com",
            },
        )
        assert response.status_code == 403
        data = response.json()
        assert "Origin no permitido" in data["detail"]

    async def test_refresh_csrf_invalido_retorna_403(self, http_client: AsyncClient) -> None:
        """CSRF token con formato inválido retorna 403."""
        response = await http_client.post(
            "/api/v1/auth/refresh",
            headers={
                "X-CSRF-Token": "token@con!caracteres#invalidos",
                "Origin": "http://localhost:5173",
            },
        )
        assert response.status_code == 403

    async def test_refresh_rota_el_token_y_detecta_el_reuso(
        self, http_client: AsyncClient, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """La rotacion emite un token nuevo y el viejo deja de servir.

        Este test reemplaza a uno que pedia 501 y saying que la rotacion estaba
        pendiente. Ya esta implementada--y hacia falta que lo estuviera: con un
        `refresh` que devolviera 501, una sesion de 15 minutos obligaba al
        usuario a volver a poner su contrasena-- asi que el test seguia
        describiendo un mundo que no existia y en verde.

        Lo que se comprueba es la propiedad que hace que la rotacion sirva de algo:
        el token viejo se revoca, y **re-presentarlo** no es un 401 cualquiera sino
        el detector de reuso, que corta la familia entera. Un token viejo que
        devuelve 401 sin tocar nada deja viva la sesion del atacante, que es el
        fallo que la rotacion existe para cerrar.
        """
        await _crear_usuario_negocio(session, business_id=business_a, email="user-a@negocio.com.ar")

        login = await http_client.post(
            "/api/v1/auth/login",
            json={"email": "user-a@negocio.com.ar", "password": "password123"},
        )
        assert login.status_code == 200
        token_viejo = login.cookies["refresh_token"]

        headers = {"X-CSRF-Token": "valid-token", "Origin": "http://localhost:5173"}

        rotado = await http_client.post("/api/v1/auth/refresh", headers=headers)
        assert rotado.status_code == 200, rotado.text
        assert "access_token" in rotado.json()
        token_nuevo = rotado.cookies["refresh_token"]
        assert token_nuevo != token_viejo, "la rotacion devolvio el mismo token"

        # El token viejo vuelve: es reuso, y revoca la familia.
        #
        # La cookie se pone **en el cliente**, no en el `cookies=` del request: httpx
        # lo deprecia--y `filterwarnings = error` convierte ese warning en un fallo--
        # porque no esta claro si un cookie por request debe reemplazar el jar o
        # mezclarse con el. Ademas el jar ya tiene el token viejo de la cookie del
        # login, asi que de todos modos hay que pisarlo a proposito.
        http_client.cookies.set("refresh_token", token_viejo)
        reuso = await http_client.post("/api/v1/auth/refresh", headers=headers)
        assert reuso.status_code == 401
        assert "familia" in reuso.json()["detail"].lower(), (
            f"el reuso no corto la familia: {reuso.json()['detail']}"
        )

        # Y ahora el token nuevo tampoco sirve: la familia esta muerta.
        http_client.cookies.set("refresh_token", token_nuevo)
        con_el_nuevo = await http_client.post("/api/v1/auth/refresh", headers=headers)
        assert con_el_nuevo.status_code == 401


class TestCookieAttributes:
    """Tests para verificar atributos de la cookie refresh_token."""

    async def test_cookie_httponly_secure_lasx(
        self, http_client: AsyncClient, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """La cookie de refresh sale HttpOnly, SameSite y con Path acotado.

        Los tres atributos se comprueban contra el `Set-Cookie` crudo y no contra
        `response.cookies`: el diccionario de httpx solo expone nombre y valor, asi
        que assertar ahi daria verde con la cookie sin `HttpOnly`--que es
        exactamente el atributo que impide que un XSS se lleve la sesion--.
        """
        await _crear_usuario_negocio(session, business_id=business_a, email="user-a@negocio.com.ar")

        response = await http_client.post(
            "/api/v1/auth/login",
            json={"email": "user-a@negocio.com.ar", "password": "password123"},
        )
        assert response.status_code == 200

        cabeceras = response.headers.get_list("set-cookie")
        refresh = next((c for c in cabeceras if c.startswith("refresh_token=")), None)
        assert refresh is not None, f"no se fijo la cookie de refresh: {cabeceras}"

        atributos = {par.split("=", 1)[0].strip().lower() for par in refresh.split(";")[1:]}
        assert "httponly" in atributos, f"la cookie de refresh no es HttpOnly: {refresh}"
        assert get_settings().cookie_samesite.lower() in refresh.lower(), (
            f"SameSite={get_settings().cookie_samesite} no aparece en la cookie: {refresh}"
        )
        assert f"Path={get_settings().cookie_path}" in refresh, (
            f"la cookie no esta acotada a {get_settings().cookie_path}: {refresh}"
        )

    async def test_cookie_path_limitado_a_auth(
        self, http_client: AsyncClient, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """El `Path` de la cookie acota su alcance a `/api/v1/auth`.

        Con `Path=/` la cookie viaja en cada peticion--incluidas las estaticas del
        frontend-- y el radio de impacto de un token robado es toda la origin. Con
        `Path=/api/v1/auth` el navegador solo la manda al refresh.

        **Lo que este test NO puede comprobar, y por eso lo dice.** Que el navegador
        respete `Path` es comportamiento del cliente HTTP, y httpx no implementa el
        `Path` de las cookies: `http.cookiejar` guarda el atributo pero no lo usa
        para decidir a que URLs se envia. Cualquier test que "comprobara" que la
        cookie no viaja a `/api/v1/...` estaria mirando una propiedad que el cliente
        de test no tiene, y pasaria siempre. Lo unico honesto es leer el `Path` del
        `Set-Cookie` crudo--que es el contrato que el servidor emite-- y verificar
        aca que el refresh sin cookie no cuela.
        """
        await _crear_usuario_negocio(session, business_id=business_a, email="user-a@negocio.com.ar")

        response = await http_client.post(
            "/api/v1/auth/login",
            json={"email": "user-a@negocio.com.ar", "password": "password123"},
        )
        assert response.status_code == 200

        cabeceras = response.headers.get_list("set-cookie")
        refresh = next((c for c in cabeceras if c.startswith("refresh_token=")), None)
        assert refresh is not None, f"no se fijo la cookie de refresh: {cabeceras}"
        assert f"Path={get_settings().cookie_path}" in refresh, (
            f"el Path emitido no es {get_settings().cookie_path}: {refresh}"
        )

        path = get_settings().cookie_path
        assert path.startswith("/api/v1/auth"), (
            f"COOKIE_PATH={path} sale del prefijo de auth: la cookie se envia de mas"
        )
        assert path != "/", "COOKIE_PATH=/ hace que la cookie viaje en cada peticion"

        # El jar del cliente conservo la cookie del login--httpx es un navegador de
        # verdad-- y por eso el "sin cookie" hay que provocarlo vaciando el jar. Sin
        # este `clear()`, el refresh tendria cookie, devolveria 200, y el test
        # estaria comprobando la rotacion con otro nombre.
        http_client.cookies.clear()
        refresco = await http_client.post(
            "/api/v1/auth/refresh",
            headers={
                "X-CSRF-Token": "valid-token",
                "Origin": "http://localhost:5173",
            },
        )
        assert refresco.status_code == 401, (
            f"sin la cookie el refresh tiene que dar 401, dio "
            f"{refresco.status_code}: alguien esta aceptando refresh sin token"
        )


class TestBusinessIdNoAceptado:
    """Verifica que el cliente no puede forzar business_id/tenant."""

    async def test_business_id_en_body_ignorado(
        self, http_client: AsyncClient, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """Enviar business_id en el body NO cambia el tenant del login."""

        await _crear_usuario_negocio(session, business_id=business_a, email="user-a@negocio.com.ar")

        response = await http_client.post(
            "/api/v1/auth/login",
            json={
                "email": "user-a@negocio.com.ar",
                "password": "password123",
                "business_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        # FastAPI rechaza campos extra por defecto (extra='ignore' en config)
        # El login debe funcionar normalmente
        assert response.status_code == 200


class TestPlatformLoginScopes:
    """Verifica que el token de plataforma no tiene scopes de negocio."""

    async def test_platform_token_sin_scopes_negocio(
        self, http_client: AsyncClient, migration_database_url: str
    ) -> None:
        """Token de plataforma NO contiene scopes de negocio (bookings:*, clients:*, etc.)."""
        await _crear_usuario_plataforma(migration_database_url)

        response = await http_client.post(
            "/api/v1/auth/platform/login",
            json={"email": PLATFORM_OWNER_EMAIL, "password": "password123"},
        )
        assert response.status_code == 200, response.text

        claims = pyjwt.decode(
            response.json()["access_token"],
            options={"verify_signature": False, "verify_exp": False},
        )
        # Scopes de plataforma
        assert "platform:tenants:read" in claims["scopes"]
        assert "platform:tenants:write" in claims["scopes"]
        # NO scopes de negocio
        for scope in claims.get("scopes", []):
            assert not scope.startswith("business:")
            assert not scope.startswith("bookings:")
            assert not scope.startswith("clients:")
            assert not scope.startswith("team:")
            assert not scope.startswith("availability:")


class TestWWWAuthenticateHeader:
    """Verifica header WWW-Authenticate en errores 401."""

    async def test_www_authenticate_en_401_login(self, http_client: AsyncClient) -> None:
        """Respuesta 401 incluye WWW-Authenticate: Bearer realm=tempus."""
        response = await http_client.post(
            "/api/v1/auth/login",
            json={"email": "nonexistent@test.com", "password": "wrong"},
        )
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == 'Bearer realm="tempus"'

    async def test_www_authenticate_en_401_platform(self, http_client: AsyncClient) -> None:
        """Respuesta 401 en platform/login también incluye WWW-Authenticate."""
        response = await http_client.post(
            "/api/v1/auth/platform/login",
            json={"email": "nonexistent@test.com", "password": "wrong"},
        )
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == 'Bearer realm="tempus"'
