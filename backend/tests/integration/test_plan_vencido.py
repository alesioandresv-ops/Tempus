"""Guardia de plan del panel: el mes gratis no puede bloquear la lectura y la suscripcion.

Este test cubre el 402 que dispara `gating.exigir_plan_vigente` cuando un negocio
con plan `free` ya paso el mes gratis y se intenta hacer una **escritura** en el
panel. Lee, sin embargo, tiene que seguir andando para que el dueno vea lo que
pasa y llegue a la pantalla de pago.

Se usa la ruta real: `POST /auth/register-business` crea el negocio (en la
transaccion del test), se obtiene un `access_token` por login, y luego se
adelanta el reloj de `app.modules.businesses.publicacion` para simular el
vencimiento.
"""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import UTC

import pytest
from app.modules.businesses import publicacion
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

EMAIL = "Duenio.PlanVencido@Peluqueria.com"
PASSWORD = "una-contrasena-larga-de-prueba"


def _cuerpo_alta(**cambios: object) -> dict[str, object]:
    cuerpo: dict[str, object] = {
        "business_name": "Peluqueria Vencida",
        "slug": "peluqueria-vencida",
        "owner_name": "Ana Perez",
        "email": EMAIL,
        "password": PASSWORD,
        "phone": "+54 9 11 2345-6789",
        "timezone": "America/Argentina/Buenos_Aires",
    }
    cuerpo.update(cambios)
    return cuerpo


async def _alta(http_client: AsyncClient) -> dict[str, object]:
    respuesta = await http_client.post("/api/v1/auth/register-business", json=_cuerpo_alta())
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


async def _poner_tenant(session: AsyncSession, business_id: uuid.UUID) -> None:
    from app.db.session import TENANT_GUC

    await session.execute(
        text("SELECT set_config(:guc, :v, true)"),
        {"guc": TENANT_GUC, "v": str(business_id)},
    )


async def _login(http_client: AsyncClient) -> str:
    respuesta = await http_client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestPlanVencido:
    async def test_lectura_andando_suscripcion_sin_guardia(
        self, http_client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cuerpo = await _alta(http_client)
        bid = uuid.UUID(str(cuerpo["business_id"]))
        await _poner_tenant(session, bid)
        token = await _login(http_client)

        # 1. Dentro del mes: GET /business/negocio anda (lectura siempre pasa)
        r = await http_client.get("/api/v1/business/negocio", headers=_auth(token))
        assert r.status_code == 200, r.text

        # 2. Fuerzo el vencimiento: +40 días
        ahora = dt.datetime.now(UTC)
        monkeypatch.setattr(publicacion, "now", lambda: ahora + dt.timedelta(days=40))

        # 3. La lectura sigue andando
        r = await http_client.get("/api/v1/business/negocio", headers=_auth(token))
        assert r.status_code == 200, r.text

        # 4. GET /business/suscripcion expone la ventana (billing_router sin guardia)
        r = await http_client.get("/api/v1/business/suscripcion", headers=_auth(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["es_publicable"] is False
        assert body["dias_restantes"] == 0
        assert body["checkout_url"].endswith("/admin?tab=pagos")
        assert body["trial_ends_at"] is not None

        # 5. POST /business/suscripcion/activar NO es 402 (es la salida)
        r = await http_client.post(
            "/api/v1/business/suscripcion/activar",
            json={"back_url": "http://localhost/admin?tab=pagos"},
            headers=_auth(token),
        )
        assert r.status_code != 402, r.text

    async def test_escritura_vencida_da_402(
        self, http_client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cuerpo = await _alta(http_client)
        bid = uuid.UUID(str(cuerpo["business_id"]))
        await _poner_tenant(session, bid)
        token = await _login(http_client)

        # Dentro del mes: PATCH pasa
        r = await http_client.patch(
            "/api/v1/business/negocio",
            json={"name": "Mi Negocio"},
            headers=_auth(token),
        )
        assert r.status_code == 200, r.text

        # Vencido: PATCH de escritura debe dar 402
        ahora = dt.datetime.now(UTC)
        monkeypatch.setattr(publicacion, "now", lambda: ahora + dt.timedelta(days=40))
        r = await http_client.patch(
            "/api/v1/business/negocio",
            json={"name": "Mi Negocio 2"},
            headers=_auth(token),
        )
        assert r.status_code == 402, r.text
        body = r.json()
        assert body["code"] == "plan_vencido"
        assert body["dias_restantes"] == 0
        assert body["checkout_url"].endswith("/admin?tab=pagos")
        assert body["trial_ends_at"] is not None

    async def test_pro_no_bloquea_escritura(
        self, http_client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cuerpo = await _alta(http_client)
        bid = uuid.UUID(str(cuerpo["business_id"]))
        await _poner_tenant(session, bid)

        # Poner plan PRO directamente en la DB para el test
        await session.execute(
            text("UPDATE businesses SET plan = 'pro' WHERE id = :id"),
            {"id": bid},
        )
        token = await _login(http_client)

        # Vencido artificialmente, pero con plan PRO
        ahora = dt.datetime.now(UTC)
        monkeypatch.setattr(publicacion, "now", lambda: ahora + dt.timedelta(days=40))

        # Escritura no debe dar 402
        r = await http_client.patch(
            "/api/v1/business/negocio",
            json={"name": "Pro Negocio"},
            headers=_auth(token),
        )
        assert r.status_code != 402, r.text
