"""Tests del panel administrativo web (`/admin`).

Lo que estos tests ejercitan y por que de la forma en que lo hacen:

1. **La sesion del admin es la de `get_admin_session`**, o sea el engine con el rol
   de migraciones (`tempus_owner`). No es un atajo: `admin_audit_log` no tiene
   grants para `tempus_app` a proposito, y `businesses` exige el GUC de tenant
   antes de cada `UPDATE`. Si el test usara la sesion de la app, el login fallaria
   al auditar y el exito de `activar` no probaria el mismo camino que produccion.
2. **La cookie es `Secure` y los tests no la relajan.** El cliente ASGI habla
   `https://test`, asi el cookie jar la envia igual que un navegador en
   produccion. Relajar la bandera para testear dejaria sin probar justo la cookie
   que se emite.
3. **El login se prueba por HTTP, no llamando al handler.** El formulario, el
   CSRF de double-submit y el 303 son parte del contrato: llamar a la funcion
   dejaria afuera lo que puede romperse.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from types import SimpleNamespace

import bcrypt
import pytest
from app.api.routers.admin import _verificar_secreto
from app.api.routers.admin.security import (
    CABECERAS_ADMIN,
    COOKIE_CSRF,
    COOKIE_SESION,
    crear_sesion,
    crear_token_csrf,
    credenciales_validas,
    csrf_de_sesion_valido,
    leer_sesion,
    panel_configurado,
    token_csrf_firmado_valido,
)
from app.core.config import get_settings
from app.core.time import now
from app.models.enums import BusinessPlan, BusinessStatus
from app.modules.admin.models import AdminAuditLog
from app.modules.admin.service import (
    ACCION_ACTIVAR,
    ACCION_BLOQUEAR,
    ACCION_DESBLOQUEAR,
    ACCION_LOGIN,
    FilaNegocio,
    estadisticas,
    estado_de,
    filtrar_filas,
    ip_de,
)
from app.modules.businesses.models import Business
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.conftest import BUSINESS_A, BUSINESS_B

pytestmark = pytest.mark.integration

#: Password de prueba. El hash se calcula con `rounds=4` a proposito: bcrypt con
#: el costo de produccion tarda ~100 ms por verificacion y el limite de login
#: dispara seis verificaciones en un solo test.
PASSWORD_ADMIN = "clave-de-prueba-del-panel"
SECRET_KEY_ADMIN = "secreto-de-sesion-del-panel-admin-1234567890"


@pytest.fixture(scope="module")
def admin_credentials() -> dict[str, str]:
    return {
        "user": "operador",
        "password": PASSWORD_ADMIN,
        "hash": bcrypt.hashpw(PASSWORD_ADMIN.encode(), bcrypt.gensalt(rounds=4)).decode(),
    }


@pytest.fixture(autouse=True)
def admin_env(monkeypatch: pytest.MonkeyPatch, admin_credentials: dict[str, str]) -> None:
    """Configura el panel por variables de entorno, como en produccion.

    `get_settings()` esta cacheado y `get_settings_dep` devuelve ese mismo objeto;
    si no se limpia la cache, la app creada en el test sigue viendo la
    configuracion anterior (sin panel). Se limpia al entrar y al salir para no
    contaminar los demas modulos.
    """
    monkeypatch.setenv("ADMIN_USER", admin_credentials["user"])
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", admin_credentials["hash"])
    monkeypatch.setenv("SECRET_KEY", SECRET_KEY_ADMIN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def admin_session(migration_database_url: str, seeded: None):
    """Sesion con el rol de migraciones, en una transaccion que se revierte.

    Es la misma que usa el panel en produccion. La transaccion externa convierte
    cada `commit()` del handler en la liberacion de un savepoint, asi un test no
    deja filas de auditoria ni negocios activados para el siguiente.
    """
    engine = create_async_engine(migration_database_url, poolclass=None)
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            session = AsyncSession(
                conn,
                expire_on_commit=False,
                autoflush=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                await _reset_negocios(session)
                yield session
            finally:
                await session.close()
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()


async def _reset_negocios(session: AsyncSession) -> None:
    """Deja A y B en el estado del seed antes de cada test.

    El seed se commitea y es compartido por toda la suite; un test que corriera
    despues de una corrida manual con `negocio-a` en Pro no deberia fallar por un
    dato viejo. El `UPDATE` va con el GUC de cada tenant, igual que el panel.
    """
    for business_id in (BUSINESS_A, BUSINESS_B):
        await session.execute(
            # `set_config` local a la transaccion, como en el service.
            text("SELECT set_config('app.current_business_id', :bid, true)"),
            {"bid": str(business_id)},
        )
        await session.execute(
            update(Business)
            .where(Business.id == business_id)
            .values(plan=BusinessPlan.FREE, status=BusinessStatus.TRIAL, pro_until=None)
        )
    await session.commit()


@pytest.fixture
async def admin_client(admin_session: AsyncSession):
    """Cliente HTTP con la app real y la sesion admin del test inyectada."""
    from app.db.session import get_admin_session
    from app.main import create_app

    settings = get_settings()
    app = create_app(settings)

    async def _usar_sesion_admin():
        yield admin_session

    app.dependency_overrides[get_admin_session] = _usar_sesion_admin

    transport = ASGITransport(app=app)
    # `https` para que el cookie jar mande las cookies `Secure` igual que un
    # navegador en produccion. El esquema no cambia nada del handler.
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        yield client, settings
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _token_csrf(html: str) -> str:
    encontrado = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert encontrado is not None, "el formulario no trae token CSRF"
    return encontrado.group(1)


def _cookie_de(response, nombre: str) -> str | None:
    """Valor de una cookie leida del `Set-Cookie` crudo."""
    for cabecera in response.headers.get_list("set-cookie"):
        if cabecera.startswith(f"{nombre}="):
            return cabecera.split(";", 1)[0].split("=", 1)[1]
    return None


def _autenticar(client: AsyncClient, settings) -> str:
    """Deja una sesion valida en el cookie jar y devuelve el CSRF de la sesion."""
    valor, principal = crear_sesion(settings, settings.admin_user)
    client.cookies.set(COOKIE_SESION, valor, path="/")
    return principal.csrf


async def _abrir_login(client: AsyncClient) -> str:
    """Carga el formulario y devuelve el token CSRF que quedo en el jar."""
    response = await client.get("/admin/login")
    assert response.status_code == 200
    token = _token_csrf(response.text)
    assert _cookie_de(response, COOKIE_CSRF) == token
    return token


async def _login(client: AsyncClient, token: str, *, password: str | None = None):
    return await client.post(
        "/admin/login",
        data={
            "csrf_token": token,
            "user": "operador",
            "password": PASSWORD_ADMIN if password is None else password,
        },
    )


# --------------------------------------------------------------------------- #
# Login, sesion y CSRF
# --------------------------------------------------------------------------- #


async def test_login_page_emite_csrf_y_cabeceras(admin_client):
    client, settings = admin_client

    response = await client.get("/admin/login")

    assert response.status_code == 200
    for nombre, valor in CABECERAS_ADMIN.items():
        assert response.headers[nombre] == valor
    token = _token_csrf(response.text)
    assert token_csrf_firmado_valido(settings, token)
    assert _cookie_de(response, COOKIE_CSRF) == token


async def test_dashboard_sin_sesion_redirige_al_login(admin_client):
    client, _settings = admin_client

    response = await client.get("/admin")

    assert response.status_code == 302
    assert response.headers["location"] == "/admin/login"
    for nombre, valor in CABECERAS_ADMIN.items():
        assert response.headers[nombre] == valor


async def test_api_admin_sin_sesion_ni_clave_redirige_al_login(admin_client):
    client, _settings = admin_client

    response = await client.get("/api/v1/admin/businesses")

    assert response.status_code == 302
    assert response.headers["location"] == "/admin/login"


async def test_api_admin_con_clave_legacy_apagada_es_401(admin_client):
    client, _settings = admin_client

    response = await client.get("/api/v1/admin/businesses?key=cualquier-cosa")

    assert response.status_code == 401


async def test_login_con_credenciales_invalidas_no_emite_sesion(admin_client):
    client, _settings = admin_client
    token = await _abrir_login(client)

    response = await _login(client, token, password="password-incorrecta")

    assert response.status_code == 401
    assert _cookie_de(response, COOKIE_SESION) is None
    assert "Usuario o contrasena incorrectos" in response.text


async def test_login_con_csrf_invalido_es_403(admin_client):
    client, _settings = admin_client
    await _abrir_login(client)

    response = await client.post(
        "/admin/login",
        data={"csrf_token": "token-falso", "user": "operador", "password": PASSWORD_ADMIN},
    )

    assert response.status_code == 403
    assert _cookie_de(response, COOKIE_SESION) is None


async def test_login_valido_emite_cookie_firmada_y_audita(admin_client, admin_session):
    client, settings = admin_client
    token = await _abrir_login(client)

    response = await _login(client, token)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    valor = _cookie_de(response, COOKIE_SESION)
    assert valor is not None
    sesion = leer_sesion(settings, valor)
    assert sesion is not None
    assert sesion.user == "operador"
    cabecera = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith(f"{COOKIE_SESION}=")
    ).lower()
    assert "httponly" in cabecera
    assert "secure" in cabecera
    assert "samesite=strict" in cabecera
    assert "max-age=1800" in cabecera

    filas = (
        (
            await admin_session.execute(
                select(AdminAuditLog).where(AdminAuditLog.action == ACCION_LOGIN)
            )
        )
        .scalars()
        .all()
    )
    assert len(filas) == 1
    assert filas[0].admin_user == "operador"
    assert filas[0].business_id is None
    assert filas[0].ip is not None


async def test_login_con_sesion_viva_redirige_al_panel(admin_client):
    client, settings = admin_client
    _autenticar(client, settings)

    response = await client.get("/admin/login")

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


async def test_logout_limpia_la_cookie(admin_client):
    client, settings = admin_client
    csrf = _autenticar(client, settings)

    response = await client.post("/admin/logout", data={"csrf_token": csrf})

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
    cabecera = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith(f"{COOKIE_SESION}=")
    ).lower()
    assert "max-age=0" in cabecera or "expires=" in cabecera


async def test_logout_sin_csrf_valido_es_403(admin_client):
    client, settings = admin_client
    _autenticar(client, settings)

    response = await client.post("/admin/logout", data={"csrf_token": "no-es-el-token"})

    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# Dashboard: filtros, busqueda y stats
# --------------------------------------------------------------------------- #


async def test_dashboard_filtra_y_busca(admin_client, admin_session):
    client, settings = admin_client
    from app.modules.admin import service as admin_service

    await admin_service.activar_pro(admin_session, BUSINESS_A, dias=30)
    await admin_service.bloquear(admin_session, BUSINESS_B)
    await admin_session.commit()

    _autenticar(client, settings)

    pro = await client.get("/admin?filtro=pro")
    assert pro.status_code == 200
    assert "negocio-a" in pro.text
    assert "negocio-b" not in pro.text

    bloqueados = await client.get("/admin?filtro=bloqueados")
    assert bloqueados.status_code == 200
    assert "negocio-b" in bloqueados.text
    assert "negocio-a" not in bloqueados.text

    buscado = await client.get("/admin?q=NEGOCIO-A")
    assert buscado.status_code == 200
    assert "negocio-a" in buscado.text
    assert "negocio-b" not in buscado.text

    # El 200 con la tabla vacia no distingue un filtro roto de "no hay filas".
    # La busqueda que no matchea tiene que decirlo explicitamente.
    vacio = await client.get("/admin?q=no-existe-este-negocio")
    assert "No hay negocios que coincidan" in vacio.text


async def test_dashboard_muestra_estadisticas(admin_client, admin_session):
    client, settings = admin_client
    from app.modules.admin import service as admin_service

    await admin_service.activar_pro(admin_session, BUSINESS_A, dias=30)
    await admin_session.commit()

    _autenticar(client, settings)
    response = await client.get("/admin")

    assert response.status_code == 200
    assert "Pro activos" in response.text
    assert "En mes gratis" in response.text
    assert "Pagos pendientes" in response.text


async def test_listar_negocios_lee_pagos_de_todos_los_tenants(admin_session):
    """El rol del panel ve `pro_payments` de todos los negocios.

    `pro_payments` tiene `FORCE RLS` y el rol administrativo es `NOBYPASSRLS`.
    Sin la politica `pro_payments_admin_read` de la migracion 0029 esta consulta
    solo devuelve los pagos del tenant que quedo en el GUC (o ninguno), y el
    email/pendientes del panel se muestran vacios sin ningun error.
    """
    from decimal import Decimal

    from app.modules.admin import service as admin_service
    from app.modules.payments.pro_models import ProPayment, ProPaymentStatus

    for business_id, email in (
        (BUSINESS_A, "negocio-a@example.com"),
        (BUSINESS_B, "negocio-b@example.com"),
    ):
        await admin_service.set_tenant_para_escritura(admin_session, business_id)
        admin_session.add(
            ProPayment(
                business_id=business_id,
                business_name=f"Negocio {business_id}",
                email=email,
                amount=Decimal("15000.00"),
                currency="ARS",
                status=ProPaymentStatus.PENDING,
                method="naranja_x",
            )
        )
        # Flush con el GUC de cada tenant: el `WITH CHECK` de la RLS compara el
        # `business_id` de la fila contra el GUC vigente al INSERT.
        await admin_session.flush()
    # El GUC quedo en BUSINESS_B: sin la politica, la lectura cruzada no puede
    # devolver el pago de A.
    await admin_session.commit()

    filas = {fila.id: fila for fila in await admin_service.listar_negocios(admin_session)}

    assert filas[BUSINESS_A].email == "negocio-a@example.com"
    assert filas[BUSINESS_B].email == "negocio-b@example.com"
    assert admin_service.estadisticas(list(filas.values()))["pendientes_pago"] >= 2


# --------------------------------------------------------------------------- #
# Acciones: activar, bloquear, desbloquear
# --------------------------------------------------------------------------- #


async def test_activar_pro_escribe_y_audita(admin_client, admin_session):
    client, settings = admin_client
    csrf = _autenticar(client, settings)

    response = await client.post(
        f"/admin/businesses/{BUSINESS_A}/activar",
        data={"csrf_token": csrf, "dias": "30"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin?aviso=pro"

    negocio = await admin_session.get(Business, BUSINESS_A)
    assert negocio is not None
    assert negocio.plan is BusinessPlan.PRO
    assert negocio.status is BusinessStatus.ACTIVE
    assert negocio.pro_until is not None
    assert negocio.pro_until > now()

    audit = (
        (
            await admin_session.execute(
                select(AdminAuditLog).where(
                    AdminAuditLog.action == ACCION_ACTIVAR,
                    AdminAuditLog.business_id == BUSINESS_A,
                )
            )
        )
        .scalars()
        .one()
    )
    assert audit.admin_user == "operador"


async def test_activar_renueva_sumando_sobre_la_vigencia(admin_client, admin_session):
    client, settings = admin_client
    csrf = _autenticar(client, settings)

    await client.post(
        f"/admin/businesses/{BUSINESS_A}/activar",
        data={"csrf_token": csrf, "dias": "30"},
    )
    primero = (await admin_session.get(Business, BUSINESS_A)).pro_until

    response = await client.post(
        f"/admin/businesses/{BUSINESS_A}/activar",
        data={"csrf_token": csrf, "dias": "10"},
    )

    assert response.status_code == 303
    segundo = (await admin_session.get(Business, BUSINESS_A)).pro_until
    assert primero is not None and segundo is not None
    assert segundo - primero == dt.timedelta(days=10)


async def test_bloquear_y_desbloquear_auditan(admin_client, admin_session):
    client, settings = admin_client
    csrf = _autenticar(client, settings)

    bloqueo = await client.post(
        f"/admin/businesses/{BUSINESS_A}/bloquear",
        data={"csrf_token": csrf},
    )
    assert bloqueo.status_code == 303
    assert bloqueo.headers["location"] == "/admin?aviso=bloqueado"
    assert (await admin_session.get(Business, BUSINESS_A)).status is BusinessStatus.SUSPENDED

    desbloqueo = await client.post(
        f"/admin/businesses/{BUSINESS_A}/desbloquear",
        data={"csrf_token": csrf},
    )
    assert desbloqueo.status_code == 303
    assert desbloqueo.headers["location"] == "/admin?aviso=desbloqueado"
    assert (await admin_session.get(Business, BUSINESS_A)).status is BusinessStatus.ACTIVE

    acciones = [
        fila.action
        for fila in (
            (
                await admin_session.execute(
                    select(AdminAuditLog).where(AdminAuditLog.business_id == BUSINESS_A)
                )
            )
            .scalars()
            .all()
        )
    ]
    assert acciones == [ACCION_BLOQUEAR, ACCION_DESBLOQUEAR]


async def test_activar_con_uuid_invalido_es_400(admin_client):
    client, settings = admin_client
    csrf = _autenticar(client, settings)

    response = await client.post(
        "/admin/businesses/no-es-un-uuid/activar",
        data={"csrf_token": csrf, "dias": "30"},
    )

    assert response.status_code == 400


@pytest.mark.parametrize("dias", ["0", "366", "-1", "abc", ""])
async def test_activar_con_dias_invalidos_es_400(admin_client, dias):
    client, settings = admin_client
    csrf = _autenticar(client, settings)

    response = await client.post(
        f"/admin/businesses/{BUSINESS_A}/activar",
        data={"csrf_token": csrf, "dias": dias},
    )

    assert response.status_code == 400


async def test_activar_sin_csrf_es_403(admin_client):
    client, settings = admin_client
    _autenticar(client, settings)

    response = await client.post(
        f"/admin/businesses/{BUSINESS_A}/activar",
        data={"dias": "30"},
    )

    assert response.status_code == 403


async def test_activar_negocio_inexistente_es_404(admin_client):
    client, settings = admin_client
    csrf = _autenticar(client, settings)

    response = await client.post(
        f"/admin/businesses/{uuid.uuid4()}/activar",
        data={"csrf_token": csrf, "dias": "30"},
    )

    assert response.status_code == 404


async def test_los_errores_de_accion_llevan_cabeceras(admin_client):
    client, settings = admin_client
    csrf = _autenticar(client, settings)

    response = await client.post(
        "/admin/businesses/no-es-un-uuid/activar",
        data={"csrf_token": csrf, "dias": "30"},
    )

    for nombre, valor_cabecera in CABECERAS_ADMIN.items():
        assert response.headers[nombre] == valor_cabecera


# --------------------------------------------------------------------------- #
# Limite de intentos
# --------------------------------------------------------------------------- #


async def test_sexto_intento_de_login_es_429(admin_client):
    client, _settings = admin_client

    for _ in range(5):
        token = await _abrir_login(client)
        response = await _login(client, token, password="incorrecta")
        assert response.status_code == 401

    # El GET del formulario no cuenta; el sexto POST si, y el limite es 5.
    token = await _abrir_login(client)
    bloqueado = await _login(client, token, password="incorrecta")

    assert bloqueado.status_code == 429
    assert "Demasiados intentos" in bloqueado.text


# --------------------------------------------------------------------------- #
# Primitivas de seguridad (unitarias)
# --------------------------------------------------------------------------- #


def test_panel_configurado_exige_las_tres_piezas(admin_env):
    settings = get_settings()

    assert panel_configurado(settings)
    assert not panel_configurado(settings.model_copy(update={"admin_user": ""}))
    assert not panel_configurado(settings.model_copy(update={"admin_password_hash": SecretStr("")}))
    assert not panel_configurado(settings.model_copy(update={"secret_key": SecretStr("")}))


def test_leer_sesion_rechaza_firma_invalida_y_usuario_distinto(admin_env):
    settings = get_settings()
    valor, sesion = crear_sesion(settings, settings.admin_user)

    assert leer_sesion(settings, valor) == sesion
    assert leer_sesion(settings, f"{valor}x") is None
    assert leer_sesion(settings, "") is None
    assert leer_sesion(settings.model_copy(update={"admin_user": "otro"}), valor) is None


def test_csrf_firmado_y_comparacion_de_sesion(admin_env):
    settings = get_settings()
    token = crear_token_csrf(settings)

    assert token_csrf_firmado_valido(settings, token)
    assert not token_csrf_firmado_valido(settings, f"{token}x")
    assert not token_csrf_firmado_valido(settings, None)

    _valor, sesion = crear_sesion(settings, settings.admin_user)
    assert csrf_de_sesion_valido(sesion, sesion.csrf)
    assert not csrf_de_sesion_valido(sesion, "otro-token")
    assert not csrf_de_sesion_valido(sesion, None)


def test_credenciales_validas_no_distingue_usuario_de_password(admin_env):
    settings = get_settings()

    assert credenciales_validas(settings, "operador", PASSWORD_ADMIN)
    assert not credenciales_validas(settings, "operador", "incorrecta")
    assert not credenciales_validas(settings, "otro", PASSWORD_ADMIN)


def test_clave_legacy_solo_se_acepta_con_flag(admin_env):
    settings = get_settings()
    request = SimpleNamespace(cookies={})

    with pytest.raises(HTTPException) as apagada:
        _verificar_secreto(request, settings, key="cualquier-cosa")
    assert apagada.value.status_code == 401

    with pytest.raises(HTTPException) as sin_clave:
        _verificar_secreto(request, settings, key=None)
    assert sin_clave.value.status_code == 302
    assert sin_clave.value.headers == {"Location": "/admin/login"}

    habilitada = settings.model_copy(
        update={
            "allow_legacy_admin_key": True,
            "admin_secret": SecretStr("clave-legacy"),
        }
    )
    assert _verificar_secreto(request, habilitada, key="clave-legacy") is None

    with pytest.raises(HTTPException) as clave_incorrecta:
        _verificar_secreto(request, habilitada, key="otra")
    assert clave_incorrecta.value.status_code == 401


# --------------------------------------------------------------------------- #
# Servicio: estado, filtros y stats
# --------------------------------------------------------------------------- #


def _negocio(
    *,
    plan: BusinessPlan = BusinessPlan.FREE,
    status: BusinessStatus = BusinessStatus.TRIAL,
    pro_until: dt.datetime | None = None,
    created_at: dt.datetime | None = None,
) -> Business:
    return Business(
        id=uuid.uuid4(),
        slug=f"negocio-{uuid.uuid4().hex[:8]}",
        name="Negocio de prueba",
        timezone="America/Argentina/Buenos_Aires",
        plan=plan,
        status=status,
        pro_until=pro_until,
        created_at=created_at or now(),
    )


def test_estado_de_deriva_las_cinco_situaciones(admin_env):
    ahora = now()

    assert estado_de(_negocio()) == "trial"
    assert estado_de(_negocio(created_at=ahora - dt.timedelta(days=400))) == "vencido"
    assert estado_de(_negocio(status=BusinessStatus.SUSPENDED)) == "bloqueado"
    assert (
        estado_de(
            _negocio(
                plan=BusinessPlan.PRO,
                status=BusinessStatus.ACTIVE,
                pro_until=ahora + dt.timedelta(days=5),
            )
        )
        == "activo"
    )
    assert (
        estado_de(
            _negocio(
                plan=BusinessPlan.PRO,
                status=BusinessStatus.ACTIVE,
                pro_until=ahora - dt.timedelta(days=1),
            )
        )
        == "pro_vencido"
    )


def _fila(estado: str, slug: str, nombre: str, pago: str | None = None) -> FilaNegocio:
    return FilaNegocio(
        id=uuid.uuid4(),
        business_name=nombre,
        slug=slug,
        email="",
        plan="pro" if estado in ("activo", "pro_vencido") else "free",
        estado=estado,
        pro_until=None,
        trial_ends_at=now(),
        trial_dias_restantes=0,
        ultimo_pago_status=pago,
    )


def test_filtrar_filas_por_estado_y_busqueda(admin_env):
    filas = [
        _fila("trial", "alfa", "Alfa"),
        _fila("activo", "beta", "Beta"),
        _fila("bloqueado", "gamma", "Gamma"),
        _fila("vencido", "delta", "Delta"),
        _fila("pro_vencido", "epsilon", "Epsilon"),
    ]

    assert [f.slug for f in filtrar_filas(filas, filtro="trial", q="")] == ["alfa"]
    assert [f.slug for f in filtrar_filas(filas, filtro="pro", q="")] == ["beta"]
    assert [f.slug for f in filtrar_filas(filas, filtro="bloqueados", q="")] == ["gamma"]
    assert [f.slug for f in filtrar_filas(filas, filtro="vencidos", q="")] == [
        "delta",
        "epsilon",
    ]
    assert [f.slug for f in filtrar_filas(filas, filtro="todos", q="")] == [
        "alfa",
        "beta",
        "gamma",
        "delta",
        "epsilon",
    ]
    assert [f.slug for f in filtrar_filas(filas, filtro="todos", q="BET")] == ["beta"]
    assert [f.slug for f in filtrar_filas(filas, filtro="todos", q="  alfa  ")] == ["alfa"]


def test_estadisticas_cuentan_pagos_pendientes(admin_env):
    filas = [
        _fila("trial", "alfa", "Alfa", pago="pending"),
        _fila("activo", "beta", "Beta"),
        _fila("bloqueado", "gamma", "Gamma"),
        _fila("pro_vencido", "delta", "Delta"),
    ]

    assert estadisticas(filas) == {
        "total": 4,
        "trial": 1,
        "pro_activos": 1,
        "bloqueados": 1,
        "vencidos": 1,
        "pendientes_pago": 1,
    }


def test_ip_de_ignora_lo_que_no_es_una_ip():
    assert ip_de(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))) == "127.0.0.1"
    assert ip_de(SimpleNamespace(client=SimpleNamespace(host="no-es-ip"))) is None
    assert ip_de(SimpleNamespace(client=None)) is None
