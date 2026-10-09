"""Tests de integracion de los recordatorios por WhatsApp (Fase D-3).

Lo que se prueba aca es el camino completo, con la base de por medio:

1. **Sin conexion de WhatsApp** (el default de todo negocio), el
   drain salta las notificaciones: no se llama a Meta ni una vez, y
   la fila queda `skipped` con el motivo. Es la garantia de que un
   negocio que nunca configuro nada no manda nada.
2. **Con la conexion activa**, el drain manda **una sola vez** la
   plantilla de 24 horas con las variables de la plantilla (cliente,
   servicio, fecha, hora, profesional, negocio), la fila queda
   `sent` con el `message_id` de Meta, y una segunda corrida no
   reenvia nada: la fila ya es terminal.
3. **Las plantillas son por negocio**: una conexion con otros
   nombres de plantilla manda esos nombres, no los defaults.
4. **El token viaja descifrado al cliente y cifrado en reposo**: el
   fake recibe el token en claro (que es lo que Meta necesita) y la
   base guarda el Fernet.
5. **El endpoint de configuracion**: default inactivo, guardar
   activa (con token cifrado y enmascarado en la respuesta),
   conservar el token al guardarlo sin pegarlo de nuevo, desactivar
   sin borrar credenciales, y `staff` no puede guardar (es configur
   de dueno).

El drain corre contra filas **commiteadas de verdad**: abre su propia
sesion con el GUC del tenant, y una sesion dentro de la transaccion
del test no ve lo que no se commito. Por eso el test commitea la
transaccion del fixture (`connection.commit()`) antes de drenar, y
lee el estado final por una conexion aparte con su propio GUC.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from app.core.encryption import decrypt_string, encrypt_string
from app.core.time import now
from app.db.session import TENANT_GUC
from app.integrations.whatsapp.client import WhatsAppMessage
from app.models.enums import NotificationStatus, WhatsAppConnectionStatus
from app.modules.auth.scopes import Scope
from app.modules.auth.tokens import create_access_token
from app.modules.bookings.service import BookingResult, create_booking

# Se importa por su efecto secundario: sin `Business` en el metadata, la FK
# `Service.business_id` no tiene contra que resolverse y el mapper falla al armar
# la sesion. Quitarlo parece una limpieza y rompe los tests.
from app.modules.businesses.models import Business  # noqa: F401
from app.modules.notifications.models import WhatsAppConnection
from app.workers import outbox
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

pytestmark = pytest.mark.integration

#: Zona del negocio que siembra este archivo. Tiene que coincidir con
#: la del fixture `negocio_whatsapp`: la fecha y la hora de las
#: variables de la plantilla salen de ella.
ZONA = ZoneInfo("America/Argentina/Buenos_Aires")

SLUG = "test-whatsapp"
BUSINESS_WS = uuid.UUID("dddddddd-dddd-7ddd-8ddd-ddddddddd001")
SERVICE_WS = uuid.UUID("dddddddd-dddd-7ddd-8ddd-ddddddddd002")
PROFESSIONAL_WS = uuid.UUID("dddddddd-dddd-7ddd-8ddd-ddddddddd003")
USER_WS = uuid.UUID("dddddddd-dddd-7ddd-8ddd-ddddddddd004")

PHONE_NUMBER_ID_WS = "123456789012345"
TOKEN_WS = "EAAGm0pseudoTokenDePrueba-000000"
PHONE_WS = "+5491100000777"
MENSAJE_ID_FAKE = "wamid.fake-0001"

#: Lo que el fake de cliente registra. Es global a proposito: el
#: factory del outbox lo llena sin devolver nada, y cada test lo
#: limpia al empezar.
LLAMADAS: list[dict[str, Any]] = []


class _ClienteWhatsAppFake:
    """Fake de `WhatsAppClient`: registra y finge el `2xx` de Meta."""

    def __init__(self, phone_number_id: str, access_token: str) -> None:
        self.phone_number_id = phone_number_id
        self.access_token = access_token

    async def send_template(
        self,
        to: str,
        template_name: str,
        language: str = "es_AR",
        components: list[dict[str, Any]] | None = None,
    ) -> WhatsAppMessage:
        LLAMADAS.append(
            {
                "phone_number_id": self.phone_number_id,
                "access_token": self.access_token,
                "to": to,
                "template_name": template_name,
                "language": language,
                "components": components,
            }
        )
        return WhatsAppMessage(message_id=MENSAJE_ID_FAKE, to=to, status="sent")

    async def close(self) -> None:
        pass


@pytest_asyncio.fixture
async def negocio_whatsapp(
    migration_database_url: str, test_database_url: str
) -> AsyncIterator[None]:
    """El negocio de este archivo, con servicio y profesional.

    Un negocio **propio** y no `business_c`: los tests del drain
    commitean de verdad, y `business_c` es del archivo del
    programador de notificaciones. Limpieza en **setup** por la
    misma razon que `negocio_admin`: lo que un drain commitio en una
    corrida pasada sobrevive al rollback y chocaria con la EXCLUDE
    o con la unicidad del telefono en la corrida siguiente.
    """
    mig_engine: AsyncEngine = create_async_engine(
        migration_database_url, poolclass=None, echo=False
    )
    app_engine: AsyncEngine = create_async_engine(test_database_url, poolclass=None, echo=False)
    try:
        async with mig_engine.begin() as conn:
            await conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                {"tenant": str(BUSINESS_WS)},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO businesses (id, name, slug, timezone, status)
                    VALUES (:id, 'Test WhatsApp', :slug, :tz, 'active')
                    ON CONFLICT (slug) DO UPDATE SET status = 'active'
                    """
                ),
                {"id": BUSINESS_WS, "slug": SLUG, "tz": ZONA.key},
            )
            # `notification_requests` va por cascade en `bookings`.
            await conn.execute(
                text("DELETE FROM bookings WHERE business_id = :b"),
                {"b": str(BUSINESS_WS)},
            )
            await conn.execute(
                text("DELETE FROM customers WHERE business_id = :b"),
                {"b": str(BUSINESS_WS)},
            )
            await conn.execute(
                text("DELETE FROM whatsapp_connections WHERE business_id = :b"),
                {"b": str(BUSINESS_WS)},
            )
        async with app_engine.begin() as conn:
            await conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                {"tenant": str(BUSINESS_WS)},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO services
                        (id, business_id, name, duration_minutes, price, currency)
                    VALUES (:id, :b, 'Corte', 30, 1500.00, 'ARS')
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name, is_active = true
                    """
                ),
                {"id": SERVICE_WS, "b": str(BUSINESS_WS)},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO professionals (id, business_id, display_name, sort_order)
                    VALUES (:id, :b, 'Profe WhatsApp', 1)
                    ON CONFLICT (id) DO UPDATE SET
                        display_name = EXCLUDED.display_name, is_active = true
                    """
                ),
                {"id": PROFESSIONAL_WS, "b": str(BUSINESS_WS)},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO professional_services
                        (professional_id, service_id, business_id, is_active)
                    VALUES (:p, :s, :b, true)
                    ON CONFLICT (professional_id, service_id) DO UPDATE SET
                        is_active = true
                    """
                ),
                {"p": str(PROFESSIONAL_WS), "s": str(SERVICE_WS), "b": str(BUSINESS_WS)},
            )
        yield
    finally:
        await app_engine.dispose()
        await mig_engine.dispose()


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _token_admin() -> str:
    """Token de dueno del negocio, con los scopes de configuracion."""
    return create_access_token(
        user_id=USER_WS,
        business_id=BUSINESS_WS,
        role="ADMIN",
        scopes=[
            Scope.BUSINESS_CONFIG_READ,
            Scope.BUSINESS_CONFIG_WRITE,
        ],
    ).token


def _token_staff() -> str:
    """Token de staff: ve el equipo, no toca la configuracion."""
    return create_access_token(
        user_id=USER_WS,
        business_id=BUSINESS_WS,
        role="STAFF",
        scopes=[
            Scope.TEAM_READ,
            Scope.TEAM_WRITE,
            Scope.BOOKINGS_READ_ANY,
            Scope.BOOKINGS_WRITE_ANY,
            Scope.BOOKINGS_WALKIN,
        ],
    ).token


async def _reservar_manana(
    session: AsyncSession,
) -> tuple[BookingResult, dt.datetime]:
    """Reserva para `ahora + 24h + 5 minutos`.

    El recordatorio de 24 horas vence en cinco minutos: el test lo
    vence a proposito (el `now()` del claim es el de PostgreSQL, que
    ya paso) y el de 2 horas queda en el futuro, donde debe quedarse.
    Devuelve tambien el instante de inicio, que es lo que las
    variables de la plantilla tienen que reflejar.
    """
    empieza = now() + dt.timedelta(hours=24, minutes=5)
    resultado = await create_booking(
        session,
        business_id=BUSINESS_WS,
        service_id=SERVICE_WS,
        professional_id=PROFESSIONAL_WS,
        customer_first_name="Lucia",
        customer_last_name="Ferreyra",
        customer_phone_e164=PHONE_WS,
        starts_at=empieza,
        ends_at=empieza + dt.timedelta(hours=1),
        duration_minutes=60,
        price=Decimal("1500.00"),
        currency="ARS",
        local_date=empieza.astimezone(ZONA).date(),
        agenda=None,
        notificar=True,
    )
    return resultado, empieza


async def _vencer(session: AsyncSession, booking_id: uuid.UUID, kind: str) -> None:
    """Pone el `scheduled_for` de un `kind` en el pasado.

    Sin esto el claim no matchea: `scheduled_for` de una reserva
    recien creada es "ahora" o futuro, y el claim compara contra el
    `now()` de **PostgreSQL** dentro de su propia transaccion.
    """
    await session.execute(
        text(
            "UPDATE notification_requests "
            "SET scheduled_for = now() - interval '1 minute' "
            "WHERE booking_id = :b AND kind = :kind"
        ),
        {"b": str(booking_id), "kind": kind},
    )


async def _empujar_al_futuro(session: AsyncSession, booking_id: uuid.UUID, kind: str) -> None:
    """Deja un `kind` fuera del alcance del claim a proposito."""
    await session.execute(
        text(
            "UPDATE notification_requests "
            "SET scheduled_for = now() + interval '1 hour' "
            "WHERE booking_id = :b AND kind = :kind"
        ),
        {"b": str(booking_id), "kind": kind},
    )


async def _leer_filas(engine: AsyncEngine, booking_id: uuid.UUID) -> list[dict[str, Any]]:
    """Filas de notificacion de una reserva, leidas por fuera.

    El drain escribe desde su propia sesion y committea; leer por la
    del test --cuya transaccion ya se cerro con el commit-- usaria
    savepoints colgados. Conexion aparte con su GUC es la lectura
    limpia, y es la misma separacion que tiene el drain.
    """
    async with engine.connect() as conn:
        await conn.execute(
            text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
            {"tenant": str(BUSINESS_WS)},
        )
        resultado = await conn.execute(
            text(
                "SELECT kind, status, error, provider_message_id, sent_at "
                "FROM notification_requests WHERE booking_id = :booking_id"
            ),
            {"booking_id": str(booking_id)},
        )
        return [dict(fila._mapping) for fila in resultado.fetchall()]


async def test_whatsapp_inactivo_no_envia_nada(
    session: AsyncSession,
    connection: AsyncConnection,
    engine: AsyncEngine,
    negocio_whatsapp: None,
    set_tenant,
    monkeypatch,
) -> None:
    """Sin conexion de WhatsApp (el default), el drain no llama a Meta.

    La confirmacion y el recordatorio de 24 horas estan vencidos,
    y los dos salen `skipped` con el motivo de "no esta activo":
    es un salto terminal y honesto, no un reintento que nunca va a
    servir porque la configuracion no cambiaria sola.
    """
    LLAMADAS.clear()
    monkeypatch.setattr(
        outbox,
        "whatsapp_client_factory",
        lambda phone_number_id, access_token: _ClienteWhatsAppFake(phone_number_id, access_token),
    )

    await set_tenant(BUSINESS_WS)
    resultado, _ = await _reservar_manana(session)
    await _vencer(session, resultado.booking_id, "booking_confirmed")
    await _vencer(session, resultado.booking_id, "reminder_24h")
    await session.commit()
    await connection.commit()

    resumen = await outbox._drain_business(BUSINESS_WS, 50)

    assert LLAMADAS == []
    assert resumen.skipped == 2
    assert resumen.sent == 0

    por_tipo = {f["kind"]: f for f in await _leer_filas(engine, resultado.booking_id)}
    for kind in ("booking_confirmed", "reminder_24h"):
        assert por_tipo[kind]["status"] == NotificationStatus.SKIPPED.value
        assert "WhatsApp no activo" in (por_tipo[kind]["error"] or "")
    # El recordatorio de 2 horas sigue en el futuro: no se toco.
    assert por_tipo["reminder_2h"]["status"] == "pending"


async def test_whatsapp_activo_envia_plantilla_24h_una_vez(
    session: AsyncSession,
    connection: AsyncConnection,
    engine: AsyncEngine,
    negocio_whatsapp: None,
    set_tenant,
    monkeypatch,
) -> None:
    """Con la conexion activa, el drain manda la plantilla de 24 horas.

    Se verifica el mensaje completo (numero de Meta, token descifrado,
    `to` en E.164 sin `+`, nombre de plantilla y las seis variables
    en el orden de la plantilla), el estado final de la fila (`sent`
    con el `message_id` de Meta) y que una segunda corrida no reenvia:
    la fila ya es terminal y la garantia de no-envio-duplicado es la
    UNIQUE sobre `(booking_id, kind)` mas el estado de la fila.
    """
    LLAMADAS.clear()
    monkeypatch.setattr(
        outbox,
        "whatsapp_client_factory",
        lambda phone_number_id, access_token: _ClienteWhatsAppFake(phone_number_id, access_token),
    )

    await set_tenant(BUSINESS_WS)
    session.add(
        WhatsAppConnection(
            business_id=BUSINESS_WS,
            phone_number_id=PHONE_NUMBER_ID_WS,
            access_token_encrypted=encrypt_string(TOKEN_WS),
            is_active=True,
            status=WhatsAppConnectionStatus.ACTIVE,
        )
    )
    resultado, empieza = await _reservar_manana(session)
    await _vencer(session, resultado.booking_id, "reminder_24h")
    # La confirmacion no es parte de la fase (no hay plantilla de
    # plataforma configurada): fuera del alcance del claim a
    # proposito, para que lo que se manda es **solo** el recordatorio.
    await _empujar_al_futuro(session, resultado.booking_id, "booking_confirmed")
    await session.commit()
    await connection.commit()

    resumen = await outbox._drain_business(BUSINESS_WS, 50)

    assert resumen.sent == 1
    assert len(LLAMADAS) == 1
    llamada = LLAMADAS[0]
    assert llamada["phone_number_id"] == PHONE_NUMBER_ID_WS
    # El token llega descifrado al cliente: es lo que Meta pide en el
    # header, y es la unica forma de que el envio funcione.
    assert llamada["access_token"] == TOKEN_WS
    assert llamada["to"] == PHONE_WS.lstrip("+")
    assert llamada["template_name"] == "recordatorio_24h"
    assert llamada["language"] == "es_AR"

    local = empieza.astimezone(ZONA)
    variables = [
        "Lucia",
        "Corte",
        local.date().isoformat(),
        local.strftime("%H:%M"),
        "Profe WhatsApp",
        "Test WhatsApp",
    ]
    assert llamada["components"] == [
        {"type": "body", "parameters": [{"type": "text", "text": v} for v in variables]}
    ]

    por_tipo = {f["kind"]: f for f in await _leer_filas(engine, resultado.booking_id)}
    assert por_tipo["reminder_24h"]["status"] == NotificationStatus.SENT.value
    assert por_tipo["reminder_24h"]["provider_message_id"] == MENSAJE_ID_FAKE
    assert por_tipo["reminder_24h"]["sent_at"] is not None
    assert por_tipo["reminder_24h"]["error"] is None

    # Segunda corrida: nada que reclamar, nada que reenviar.
    resumen_dos = await outbox._drain_business(BUSINESS_WS, 50)
    assert resumen_dos.sent == 0
    assert resumen_dos.claimed == 0
    assert len(LLAMADAS) == 1


async def test_plantillas_son_por_negocio(
    session: AsyncSession,
    connection: AsyncConnection,
    engine: AsyncEngine,
    negocio_whatsapp: None,
    set_tenant,
    monkeypatch,
) -> None:
    """La conexion guarda los nombres de plantilla del negocio.

    El negocio puede haber registrado `mi_recordatorio_24h` en Meta
    (por ejemplo, porque aprobo la plantilla con otro nombre): es
    ese nombre el que viaja, no el default de la aplicacion.
    """
    LLAMADAS.clear()
    monkeypatch.setattr(
        outbox,
        "whatsapp_client_factory",
        lambda phone_number_id, access_token: _ClienteWhatsAppFake(phone_number_id, access_token),
    )

    await set_tenant(BUSINESS_WS)
    session.add(
        WhatsAppConnection(
            business_id=BUSINESS_WS,
            phone_number_id=PHONE_NUMBER_ID_WS,
            access_token_encrypted=encrypt_string(TOKEN_WS),
            is_active=True,
            status=WhatsAppConnectionStatus.ACTIVE,
            reminder_24h_template="mi_recordatorio_24h",
            reminder_2h_template="mi_recordatorio_2h",
        )
    )
    resultado, _ = await _reservar_manana(session)
    await _vencer(session, resultado.booking_id, "reminder_24h")
    await _empujar_al_futuro(session, resultado.booking_id, "booking_confirmed")
    await session.commit()
    await connection.commit()

    await outbox._drain_business(BUSINESS_WS, 50)

    assert len(LLAMADAS) == 1
    assert LLAMADAS[0]["template_name"] == "mi_recordatorio_24h"


async def test_config_whatsapp_default_inactivo(
    http_client,
    negocio_whatsapp: None,
    set_tenant,
) -> None:
    """Sin configuracion, el estado es inactivo con los defaults."""
    await set_tenant(BUSINESS_WS)
    respuesta = await http_client.get(
        "/api/v1/business/config/whatsapp", headers=_auth_header(_token_admin())
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["activo"] is False
    assert cuerpo["phone_number_id"] is None
    assert cuerpo["token_ultimos"] is None
    assert cuerpo["reminder_24h_template"] == "recordatorio_24h"
    assert cuerpo["reminder_2h_template"] == "recordatorio_2h"


async def test_config_whatsapp_guarda_activa_y_cifra_el_token(
    http_client: AsyncClient,
    session: AsyncSession,
    negocio_whatsapp: None,
    set_tenant,
) -> None:
    """Guardar activa los recordatorios y el token no queda en claro.

    La respuesta muestra el estado y los ultimos cuatro caracteres
    del token --lo que alcanza para reconocer la cuenta-- y la base
    guarda el Fernet: el token en claro no existe en ningun lado
    salvo en el header de la llamada a Meta.
    """
    await set_tenant(BUSINESS_WS)
    respuesta = await http_client.put(
        "/api/v1/business/config/whatsapp",
        headers=_auth_header(_token_admin()),
        json={
            "activo": True,
            "phone_number_id": PHONE_NUMBER_ID_WS,
            "access_token": TOKEN_WS,
            "reminder_24h_template": "mi_recordatorio_24h",
            "reminder_2h_template": "mi_recordatorio_2h",
        },
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["activo"] is True
    assert cuerpo["phone_number_id"] == PHONE_NUMBER_ID_WS
    assert cuerpo["token_ultimos"] == "••••" + TOKEN_WS[-4:]
    assert cuerpo["reminder_24h_template"] == "mi_recordatorio_24h"
    assert cuerpo["reminder_2h_template"] == "mi_recordatorio_2h"

    # El GET devuelve lo mismo (y sigue sin mostrar el token).
    respuesta_get = await http_client.get(
        "/api/v1/business/config/whatsapp", headers=_auth_header(_token_admin())
    )
    assert respuesta_get.status_code == 200
    assert respuesta_get.json() == cuerpo

    # En la base, el token esta cifrado y se puede descifrar.
    conexion = (
        (
            await session.execute(
                select(WhatsAppConnection).where(WhatsAppConnection.business_id == BUSINESS_WS)
            )
        )
        .scalars()
        .one()
    )
    assert conexion.access_token_encrypted != TOKEN_WS
    assert decrypt_string(conexion.access_token_encrypted) == TOKEN_WS
    assert conexion.is_active is True


async def test_config_whatsapp_guardar_sin_token_conserva_el_actual(
    http_client: AsyncClient,
    session: AsyncSession,
    negocio_whatsapp: None,
    set_tenant,
) -> None:
    """Volver a guardar sin pegar el token conserva el que hay.

    Es lo que permite reactivar con un click: apagar los
    recordatorios no borra las credenciales, y encender de nuevo
    no pide volver a pegar el secreto.
    """
    await set_tenant(BUSINESS_WS)
    primera = await http_client.put(
        "/api/v1/business/config/whatsapp",
        headers=_auth_header(_token_admin()),
        json={
            "activo": True,
            "phone_number_id": PHONE_NUMBER_ID_WS,
            "access_token": TOKEN_WS,
        },
    )
    assert primera.status_code == 200

    segunda = await http_client.put(
        "/api/v1/business/config/whatsapp",
        headers=_auth_header(_token_admin()),
        json={"activo": False},
    )
    assert segunda.status_code == 200
    assert segunda.json()["activo"] is False
    # El token sigue ahi, enmascarado: apagar no borra credenciales.
    assert segunda.json()["token_ultimos"] == "••••" + TOKEN_WS[-4:]

    tercera = await http_client.put(
        "/api/v1/business/config/whatsapp",
        headers=_auth_header(_token_admin()),
        json={"activo": True},
    )
    assert tercera.status_code == 200
    assert tercera.json()["activo"] is True
    assert tercera.json()["token_ultimos"] == "••••" + TOKEN_WS[-4:]


async def test_config_whatsapp_encender_sin_credenciales_es_error(
    http_client: AsyncClient,
    negocio_whatsapp: None,
    set_tenant,
) -> None:
    """Encender sin Phone Number ID ni token no configura nada.

    Una conexion "activa" sin credenciales crearia recordatorios
    que el worker saltea siempre y un panel que dice "Activo"
    mientras nada sale: mejor el 422 que esa mentira.
    """
    await set_tenant(BUSINESS_WS)
    respuesta = await http_client.put(
        "/api/v1/business/config/whatsapp",
        headers=_auth_header(_token_admin()),
        json={"activo": True},
    )
    assert respuesta.status_code == 422


async def test_config_whatsapp_staff_no_puede_guardar(
    http_client: AsyncClient,
    negocio_whatsapp: None,
    set_tenant,
) -> None:
    """`staff` no guarda la configuracion: es decision del dueno."""
    await set_tenant(BUSINESS_WS)
    respuesta = await http_client.put(
        "/api/v1/business/config/whatsapp",
        headers=_auth_header(_token_staff()),
        json={
            "activo": True,
            "phone_number_id": PHONE_NUMBER_ID_WS,
            "access_token": TOKEN_WS,
        },
    )
    assert respuesta.status_code == 403


async def test_config_whatsapp_staff_puede_ver(
    http_client: AsyncClient,
    negocio_whatsapp: None,
    set_tenant,
) -> None:
    """`staff` sí lee el estado (diagnostico), con `config:read`."""
    await set_tenant(BUSINESS_WS)
    token_staff = create_access_token(
        user_id=USER_WS,
        business_id=BUSINESS_WS,
        role="STAFF",
        scopes=[
            Scope.BUSINESS_CONFIG_READ,
            Scope.TEAM_READ,
        ],
    ).token
    respuesta = await http_client.get(
        "/api/v1/business/config/whatsapp", headers=_auth_header(token_staff)
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["activo"] is False
