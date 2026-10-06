"""Los cubos del rate limit no se superan bajo concurrencia real (§7.6).

`test_rate_limits.py` prueba que los limites del §10.5 existen y cuentan en serie.
Lo que ahi no se puede ver es el caso que la tabla promete: que el cubo siga siendo
exacto cuando los intentos llegan **al mismo tiempo**. Un limitador que en serie
deja pasar 5 de 5 pero bajo una rafaga deja pasar 11 de 10 no esta limitando nada.

La garantia no es del framework: es de `rate_limit_hit` (migracion `0013`). El
`INSERT ... ON CONFLICT (key, window_start) DO UPDATE` toma el lock de la fila del
cubo, asi que las transacciones concurrentes se serializan en esa fila y la cuenta
que cada una ve incluye todos los intentos commiteados antes del suyo. Por eso, con
limite L y N intentos simultaneos (`N > L`), pasan exactamente L.

Por que por HTTP y con `asyncio.gather`: el checklist §7.6 pide los cubos de punta
a punta--la dependencia real del router, el `session_scope` propio de
`enforce_rate_limit` y conexiones del pool real, no una sesion compartida. Los
cuerpos llevan `service_id` inexistente a proposito (igual que `test_rate_limits.py`):
el intento cuenta aunque el handler responda 404, y cualquier codigo que no sea
404/429 es un fallo de la dependencia, no del limitador.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from app.api.dependencies import get_session, get_settings_dep, get_tenant_session
from app.core.config import Settings, get_settings
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.integration.test_rate_limits import Prueba, _cuerpo_reserva

pytestmark = [pytest.mark.concurrency, pytest.mark.integration]


@pytest_asyncio.fixture
async def prueba_concurrente(session: AsyncSession) -> AsyncIterator[Prueba]:
    """Igual que `prueba` de `test_rate_limits`, con timeout generoso.

    Bajo una rafaga de 12 requests el pool de la app (5 + overflow) alcanza para
    todos, pero un request puede esperar una conexion mientras otro termina: con el
    timeout por defecto de httpx (5s) esa espera podria convertirse en un error de
    transporte, y el test fallaria por el motivo equivocado.
    """
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
    async with AsyncClient(transport=transporte, base_url="http://test", timeout=60.0) as cliente:
        yield Prueba(client=cliente, limites=ajustes)


@pytest_asyncio.fixture
async def negocio_activo(migration_database_url: str, business_c: uuid.UUID) -> AsyncIterator[None]:
    """Pone en `active` el negocio sembrado, para que el endpoint publico lo sirva.

    Copia local de la fixture homonima de `test_rate_limits.py` (la importacion
    colisionaria con el parametro del test y ruff la marca F401/F811). Los porques
    completos--rol de migraciones, GUC del tenant, commit, restaurar en `finally`--
    estan explicados en el docstring de la original; son los mismos de ahi.
    """
    motor = create_async_engine(migration_database_url, poolclass=None)

    async def _poner_estado(estado: str) -> None:
        async with motor.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_business_id', :id, true)"),
                {"id": str(business_c)},
            )
            await conn.execute(
                text("UPDATE businesses SET status = :estado WHERE id = :id"),
                {"id": str(business_c), "estado": estado},
            )

    try:
        await _poner_estado("active")
        yield
    finally:
        await _poner_estado("trial")
        await motor.dispose()


class TestConcurrenciaEnLosCubos:
    async def test_con_limite_5_y_12_intentos_pasan_exactamente_5(
        self, prueba_concurrente: Prueba, negocio_activo: None
    ) -> None:
        """El cubo de 5/min por IP no deja pasar ni uno de mas bajo la rafaga."""
        prueba_concurrente.limites["rate_limit_general_per_ip"] = 1000
        prueba_concurrente.limites["rate_limit_public_booking_per_ip"] = 5
        prueba_concurrente.limites["rate_limit_public_booking_per_phone_hourly"] = 1000

        codigos = await asyncio.gather(
            *[
                prueba_concurrente.pedir(
                    "POST",
                    "/api/v1/public/bookings",
                    json=_cuerpo_reserva(telefono=f"+54911{i:08d}", clave=f"concurrente-{i}"),
                )
                for i in range(12)
            ]
        )
        recibidos = [r.status_code for r in codigos]

        assert set(recibidos) <= {404, 429}, f"respuesta inesperada de la dependencia: {recibidos}"
        assert recibidos.count(404) == 5, (
            f"el cubo dejo pasar de mas bajo concurrencia: {recibidos}"
        )
        assert recibidos.count(429) == 7, f"el cubo reboto de mas bajo concurrencia: {recibidos}"

    async def test_con_limite_1_el_unico_que_pasa_es_el_primero(
        self, prueba_concurrente: Prueba, negocio_activo: None
    ) -> None:
        """El caso extremo: limite de 1 y 5 intentos simultaneos.

        Es el que mas le exige a la primitiva: si el conteo no fuera exacto bajo
        concurrencia, el desvio mas visible seria aqui--tres, cuatro, cinco exitos
        a la vez en vez de uno.
        """
        prueba_concurrente.limites["rate_limit_general_per_ip"] = 1000
        prueba_concurrente.limites["rate_limit_public_booking_per_ip"] = 1
        prueba_concurrente.limites["rate_limit_public_booking_per_phone_hourly"] = 1000

        codigos = await asyncio.gather(
            *[
                prueba_concurrente.pedir(
                    "POST",
                    "/api/v1/public/bookings",
                    json=_cuerpo_reserva(telefono=f"+54912{i:08d}", clave=f"exacto-{i}"),
                )
                for i in range(5)
            ]
        )
        recibidos = [r.status_code for r in codigos]

        assert set(recibidos) <= {404, 429}, f"respuesta inesperada de la dependencia: {recibidos}"
        assert recibidos.count(404) == 1, (
            f"con limite 1 bajo concurrencia se espera exactamente 1 que pase: {recibidos}"
        )
        assert recibidos.count(429) == 4, (
            f"con limite 1 bajo concurrencia se esperan 4 rebotados: {recibidos}"
        )
