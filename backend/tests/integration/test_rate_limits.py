"""Los limites del §10.5, de punta a punta, por HTTP.

La tabla del §10.5 define cinco clases de endpoint. La del login ya tenia tests--en
`test_auth_router.py` y `test_auth_service.py`-- y estas son las otras cuatro, que
hasta ahora no tenian ninguna: los settings existian en `config.py` y ningun codigo
los leia, asi que la superficie publica--la que cobra costo de Meta-- no tenia freno.

**Por que los limites de estos tests son chicos y no los del §10.5.** Un test que
dispara 121 peticiones para comprobar las 120/min tarda--y lo que esta probando es
que la dependencia este colgada del router y use el limite configurado, no el numero
exacto. El numero exacto esta fijado en `test_los_limites_del_10_5_son_los_de_la_tabla`,
que lee los defaults de `Settings`. Juntos, los dos tests dicen lo que importa: que el
limite existe, que esta en el router correcto y que vale lo que dice la tabla.

**El limite se cuenta aunque la peticion falle.** Todos los tests de esta archivo mandan
cuerpos que el handler despues rechaza con 404. Es a proposito: un limitador que solo
cuenta los exitos--que es como estaba antes-- no frena nada, porque el intento que un
atacante quiere hacer es justamente el que falla.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from app.api.dependencies import get_session, get_settings_dep, get_tenant_session
from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.main import create_app
from app.models.enums import BusinessUserRole, MembershipStatus
from app.modules.auth.tokens import create_access_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

#: Un `service_id` que no existe. Los handlers van a responder 404 y da lo mismo: la
#: dependencia del rate limit corre antes que el handler, asi que el intento cuenta.
SERVICIO_INEXISTENTE = uuid.UUID("99999999-9999-7999-8999-999999999999")

FUTURO = "2099-01-01T15:00:00Z"
FUTURO_FIN = "2099-01-01T15:30:00Z"


@dataclass
class Prueba:
    """Cliente HTTP con los limites que el test necesita.

    `limites` se muta **antes** de la primera peticion y la dependencia de settings lo
    lee en cada request, asi que el test decide cuanto exagerar el limite.
    """

    client: AsyncClient
    limites: dict[str, Any] = field(default_factory=dict)

    async def pedir(self, metodo: str, ruta: str, **kwargs: Any) -> Any:
        return await self.client.request(metodo, ruta, **kwargs)

    async def codigos(self, veces: int, metodo: str, ruta: str, **kwargs: Any) -> list[int]:
        """Los primeros `veces` codigos de estado, cortando en el primer 429."""
        salida: list[int] = []
        for _ in range(veces):
            r = await self.client.request(metodo, ruta, **kwargs)
            salida.append(r.status_code)
            if r.status_code == 429:
                break
        return salida


@pytest_asyncio.fixture
async def prueba(session: AsyncSession) -> AsyncIterator[Prueba]:
    """App real, sesion del test y settings con los limites que pida el test."""
    app = create_app(get_settings())

    async def _sesion_del_test() -> AsyncIterator[AsyncSession]:
        yield session

    ajustes: dict[str, Any] = {}

    async def _settings_del_test() -> Settings:
        return get_settings().model_copy(update=ajustes) if ajustes else get_settings()

    app.dependency_overrides[get_session] = _sesion_del_test
    app.dependency_overrides[get_tenant_session] = _sesion_del_test
    app.dependency_overrides[get_settings_dep] = _settings_del_test

    transporte = ASGITransport(app=app)
    async with AsyncClient(transport=transporte, base_url="http://test") as cliente:
        yield Prueba(client=cliente, limites=ajustes)


@pytest_asyncio.fixture
async def negocio_activo(migration_database_url: str, business_c: uuid.UUID) -> AsyncIterator[None]:
    """Pone en `active` el negocio sembrado, para que el endpoint publico lo sirva.

    `businesses.status` tiene `server_default 'trial'` y el router publico solo
    publica los `active`. Sin esto, los tests de esta archivo verian 404 en cada
    peticion--y pasarian igual, porque lo que miden es el limite y no el handler.
    Peor: un 404 por todos lados es indistinguible de un router roto, asi que un
    error de ahi pasaria desapercibido.

    **Va por `migration_database_url` y no por la sesion del test.** El rol de la app
    no tiene permiso de `UPDATE` sobre `businesses`--es una tabla de onboarding-- y la
    sesion del test corre con ese rol: `InsufficientPrivilegeError`. Por eso ademas
    hay que **commitear** y por eso hay que **restaurar** al final: la sesion del test
    se deshace sola, esta escritura no.

    Sin el `finally`, un test que fallara a mitad de camino dejaria el negocio en
    `active` para el resto de la corrida, y los tests de reserva--que asumen que el
    negocio no existe porque esta en `trial`-- empezarian a fallar por un motivo que
    no tiene que ver con lo que prueban.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    motor = create_async_engine(migration_database_url, poolclass=None)
    try:
        async with motor.begin() as conn:
            await conn.execute(
                text("UPDATE businesses SET status = 'active' WHERE id = :id"),
                {"id": str(business_c)},
            )
        yield
    finally:
        async with motor.begin() as conn:
            await conn.execute(
                text("UPDATE businesses SET status = 'trial' WHERE id = :id"),
                {"id": str(business_c)},
            )
        await motor.dispose()


def _cuerpo_reserva(*, telefono: str, clave: str) -> dict[str, Any]:
    return {
        "slug": "test-business",
        "service_id": str(SERVICIO_INEXISTENTE),
        "customer_first_name": "Ana",
        "customer_last_name": "Prueba",
        "customer_phone_e164": telefono,
        "starts_at": FUTURO,
        "ends_at": FUTURO_FIN,
        "local_date": "2099-01-01",
        "idempotency_key": clave,
    }


# --------------------------------------------------------------------------- #
# Techo general de la superficie publica: 120/min por IP
# --------------------------------------------------------------------------- #


class TestElTechoPublico:
    async def test_el_resto_publico_tiene_techo_por_ip(
        self, prueba: Prueba, negocio_activo: None
    ) -> None:
        prueba.limites["rate_limit_general_per_ip"] = 3

        codigos = await prueba.codigos(4, "GET", "/api/v1/public/businesses/test-business")

        assert codigos[:3] == [200, 200, 200], f"los tres primeros tienen que pasar: {codigos}"
        assert codigos[3] == 429, f"el cuarto tiene que rebotar: {codigos}"

    async def test_el_techo_publico_trae_retry_after(
        self, prueba: Prueba, negocio_activo: None
    ) -> None:
        """Sin el header, un 429 no le sirve de nada al cliente.

        El unico motivo practico por el que un cliente respeta un 429 es que sepa
        cuando volver a probar. Sin `Retry-After` le queda adivinar.
        """
        prueba.limites["rate_limit_general_per_ip"] = 1

        await prueba.pedir("GET", "/api/v1/public/businesses/test-business")
        r = await prueba.pedir("GET", "/api/v1/public/businesses/test-business")

        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) >= 1

    async def test_el_techo_publico_no_toca_los_endpoints_de_otra_clase(
        self, prueba: Prueba, negocio_activo: None
    ) -> None:
        """Agotar el cubo publico no puede derribar la superficie interna.

        Cada clase tiene su cubo y su `scope`. Si compartieran cubo, un cliente que
        haga 120 pedidos publicos--o un crawler-- le dejaria el scheduler y la sonda
        de salud inaccesibles a todos los demas, que es un denegacion de servicio
        desde adentro. Y `/internal/health` en particular no puede depender de la
        buena voluntad de un visitante: es la que decide si hay que reiniciar algo.
        """
        prueba.limites["rate_limit_general_per_ip"] = 1

        await prueba.pedir("GET", "/api/v1/public/businesses/test-business")
        agotado = await prueba.pedir("GET", "/api/v1/public/businesses/test-business")
        assert agotado.status_code == 429

        interno = await prueba.pedir("GET", "/api/v1/internal/health")
        assert interno.status_code == 200, (
            f"el techo publico no deberia afectar a /internal/health: {interno.status_code}"
        )

    async def test_con_el_rate_limit_apagado_no_hay_techo(
        self, prueba: Prueba, negocio_activo: None
    ) -> None:
        """`RATE_LIMIT_ENABLED=false` tiene que apagar **todas** las clases.

        Es el ajuste de emergencia--una base lenta, un despliegue que se quiere
        verificar sin limites-- y solo sirve si apaga de verdad. Un flag que apaga la
        mitad es peor que no tener flag.
        """
        prueba.limites["rate_limit_enabled"] = False
        prueba.limites["rate_limit_general_per_ip"] = 1

        codigos = await prueba.codigos(4, "GET", "/api/v1/public/businesses/test-business")

        assert 429 not in codigos, f"con el rate limit apagado no deberia haber 429: {codigos}"


# --------------------------------------------------------------------------- #
# POST /public/bookings: 10/min por IP y 5/hora por telefono
# --------------------------------------------------------------------------- #


class TestElTechoDeLaReserva:
    async def test_la_reserva_tiene_techo_por_telefono(self, prueba: Prueba) -> None:
        """5/hora por telefono: es el cubo que protege el costo de Meta.

        El limite va por hora y no por minuto porque el costo--el mensaje de WhatsApp
        --se paga cuando se **envia**, no cuando se reserva. Un limite de 5 por minuto
        no frenaria nada: alcanza con esperar sesenta segundos.
        """
        prueba.limites["rate_limit_general_per_ip"] = 1000
        prueba.limites["rate_limit_public_booking_per_ip"] = 1000
        prueba.limites["rate_limit_public_booking_per_phone_hourly"] = 2

        codigos = []
        for i in range(3):
            r = await prueba.pedir(
                "POST",
                "/api/v1/public/bookings",
                json=_cuerpo_reserva(telefono="+5491100009999", clave=f"k{i}"),
            )
            codigos.append(r.status_code)

        assert 429 not in codigos[:2], f"los dos primeros tienen que pasar: {codigos}"
        assert codigos[2] == 429, f"el tercero con el mismo telefono tiene que rebotar: {codigos}"

    async def test_el_techo_por_telefono_no_afecta_a_otros_numeros(self, prueba: Prueba) -> None:
        """Contraprueba del anterior: el cubo es del telefono, no del cliente entero.

        Si el limite fuera global o por IP, este)--un telefono distinto--tambien
        rebotaria, y el limite de "5 por telefono" no estaria limitando lo que dice.
        """
        prueba.limites["rate_limit_general_per_ip"] = 1000
        prueba.limites["rate_limit_public_booking_per_ip"] = 1000
        prueba.limites["rate_limit_public_booking_per_phone_hourly"] = 1

        for i in range(2):
            await prueba.pedir(
                "POST",
                "/api/v1/public/bookings",
                json=_cuerpo_reserva(telefono="+5491100009999", clave=f"a{i}"),
            )

        r = await prueba.pedir(
            "POST",
            "/api/v1/public/bookings",
            json=_cuerpo_reserva(telefono="+5491100008888", clave="b0"),
        )
        assert r.status_code != 429, (
            f"un telefono distinto tiene que tener su propio cubo: respondio {r.status_code}"
        )

    async def test_la_reserva_tiene_techo_por_ip(self, prueba: Prueba) -> None:
        """10/min por IP, con telefonos distintos en cada intento.

        Al reves del caso del telefono: aca se agota el cubo de la IP mandando
        telefonos nuevos, que es el volumen bruto. Si el limite por IP no existiera,
        este test pasaria sin dar cuenta--por eso telefonos distintos en cada intento.
        """
        prueba.limites["rate_limit_general_per_ip"] = 1000
        prueba.limites["rate_limit_public_booking_per_ip"] = 2
        prueba.limites["rate_limit_public_booking_per_phone_hourly"] = 1000

        codigos = []
        for i in range(3):
            r = await prueba.pedir(
                "POST",
                "/api/v1/public/bookings",
                json=_cuerpo_reserva(telefono=f"+54911000000{i:02d}", clave=f"c{i}"),
            )
            codigos.append(r.status_code)

        assert 429 not in codigos[:2], f"los dos primeros tienen que pasar: {codigos}"
        assert codigos[2] == 429, f"el tercero desde la misma IP tiene que rebotar: {codigos}"

    async def test_el_techo_de_la_reserva_no_toca_el_resto_del_publico(
        self, prueba: Prueba, negocio_activo: None
    ) -> None:
        """Agotar el cubo de reservas no cierra la pagina.

        El cubo de `booking:ip` y el de `public:ip` son distintos a proposito: si
        compartieran clave, un visitante que agote el limite de reservas--o un
        atacante que lo haga a proposito-- dejaria sin poder ni ver el negocio ni
        pedir un turno. Es el mismo criterio que con el panel.
        """
        prueba.limites["rate_limit_general_per_ip"] = 1000
        prueba.limites["rate_limit_public_booking_per_ip"] = 1
        prueba.limites["rate_limit_public_booking_per_phone_hourly"] = 1

        await prueba.pedir(
            "POST",
            "/api/v1/public/bookings",
            json=_cuerpo_reserva(telefono="+5491100007777", clave="d0"),
        )
        agotado = await prueba.pedir(
            "POST",
            "/api/v1/public/bookings",
            json=_cuerpo_reserva(telefono="+5491100006666", clave="d1"),
        )
        assert agotado.status_code == 429

        pagina = await prueba.pedir("GET", "/api/v1/public/businesses/test-business")
        assert pagina.status_code == 200, (
            "agotar el cubo de reservas no puede cerrar la pagina del negocio: "
            f"respondio {pagina.status_code}"
        )


# --------------------------------------------------------------------------- #
# /internal/scheduler/tick: 2/min por IP
# --------------------------------------------------------------------------- #


class TestElTechoDelScheduler:
    async def test_el_tick_tiene_techo_por_ip(self, prueba: Prueba) -> None:
        """2/min, ademas del token interno.

        El Worker llama cada 30 segundos. Si el tick falla por un motivo del motor y
        el Worker reintenta en bucle, sin este limite eso es trabajo infinito contra la
        base; con el, el reintento se corta solo.
        """
        prueba.limites["rate_limit_scheduler_tick"] = 2
        token = get_settings().scheduler_tick_secret.get_secret_value()

        codigos = []
        for _ in range(3):
            r = await prueba.pedir(
                "POST", "/api/v1/internal/scheduler/tick", headers={"X-Internal-Token": token}
            )
            codigos.append(r.status_code)

        assert codigos[:2] == [200, 200], f"los dos primeros ticks tienen que pasar: {codigos}"
        assert codigos[2] == 429, f"el tercero tiene que rebotar: {codigos}"

    async def test_el_techo_del_tick_no_reemplaza_al_token(self, prueba: Prueba) -> None:
        """Con el token equivocado sigue siendo 401, no 429.

        El orden importa: el limite frena a quien ya tiene el token y esta reintentando;
        no puede ser una forma de cambiar la respuesta de "no tenes el secreto" por
        "espera un rato". Y un 401 sigue siendo un 401 con el cubo lleno.
        """
        prueba.limites["rate_limit_scheduler_tick"] = 2
        token = get_settings().scheduler_tick_secret.get_secret_value()

        for _ in range(2):
            await prueba.pedir(
                "POST", "/api/v1/internal/scheduler/tick", headers={"X-Internal-Token": token}
            )

        r = await prueba.pedir(
            "POST", "/api/v1/internal/scheduler/tick", headers={"X-Internal-Token": "incorrecto"}
        )
        assert r.status_code == 401, (
            f"sin el token tiene que ser 401 aunque el cubo este lleno: {r.status_code}"
        )


# --------------------------------------------------------------------------- #
# Panel autenticado: 600/min por usuario
# --------------------------------------------------------------------------- #


async def _token_de_panel(session: AsyncSession, *, business_id: uuid.UUID, email: str) -> str:
    """Crea un admin activo y devuelve su access token."""
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
            VALUES (:id, :business_id, :email, :hash, 'Panel', :role, :status)
            """
        ),
        {
            "id": user_id,
            "business_id": business_id,
            "email": email,
            "hash": hash_password("password123"),
            "role": str(BusinessUserRole.ADMIN),
            "status": str(MembershipStatus.ACTIVE),
        },
    )
    await session.flush()
    token = create_access_token(
        user_id=user_id,
        business_id=business_id,
        role=str(BusinessUserRole.ADMIN),
        scopes=["bookings:read", "bookings:write", "clients:read"],
    )
    return token.token


class TestElTechoDelPanel:
    async def test_el_panel_tiene_techo_por_usuario(
        self, prueba: Prueba, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        prueba.limites["rate_limit_admin_per_user"] = 2
        token = await _token_de_panel(session, business_id=business_a, email="panel@negocio.com.ar")
        cabeceras = {"Authorization": f"Bearer {token}"}

        codigos = []
        for _ in range(3):
            r = await prueba.pedir("GET", "/api/v1/business/me", headers=cabeceras)
            codigos.append(r.status_code)

        assert codigos[:2] == [200, 200], f"los dos primeros tienen que pasar: {codigos}"
        assert codigos[2] == 429, f"el tercero tiene que rebotar: {codigos}"

    async def test_el_techo_es_de_ese_usuario_y_no_del_resto_del_panel(
        self, prueba: Prueba, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """El cubo es del `user_id`, no global del panel.

        Con un cubo compartido, un script que colgara de una sesion--una robada, o
        un cliente con un bug en loop-- le cerraria el panel a todos los usuarios de
        todos los negocios que hubiera en el servidor.
        """
        prueba.limites["rate_limit_admin_per_user"] = 1
        uno = await _token_de_panel(session, business_id=business_a, email="uno@negocio.com.ar")
        otro = await _token_de_panel(session, business_id=business_a, email="otro@negocio.com.ar")

        await prueba.pedir("GET", "/api/v1/business/me", headers={"Authorization": f"Bearer {uno}"})
        agotado = await prueba.pedir(
            "GET", "/api/v1/business/me", headers={"Authorization": f"Bearer {uno}"}
        )
        assert agotado.status_code == 429

        r = await prueba.pedir(
            "GET", "/api/v1/business/me", headers={"Authorization": f"Bearer {otro}"}
        )
        assert r.status_code == 200, (
            "otro usuario del mismo negocio no puede quedar afuera por el cubo del "
            f"primero: respondio {r.status_code}"
        )
