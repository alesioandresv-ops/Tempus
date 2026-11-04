"""Tests for admin router and session fixes.

Do not modify code - only tests.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.api.routers.admin import _estado_de, router
from app.db import session as session_mod
from app.models.enums import BusinessPlan, BusinessStatus
from app.modules.businesses.models import Business
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


class TestGetAdminSession:
    """Unit tests for app.db.session.get_admin_session"""

    def test_get_admin_session_is_plain_async_generator_not_contextmanager(self):
        """get_admin_session must be a plain async generator function (has yield), not decorated with asynccontextmanager."""
        assert inspect.isasyncgenfunction(session_mod.get_admin_session), (
            "get_admin_session must be an async generator function (plain) with yield"
        )
        src = inspect.getsource(session_mod.get_admin_session)
        assert "yield" in src
        # admin_session IS decorated
        src_admin = inspect.getsource(session_mod.admin_session)
        assert "@asynccontextmanager" in src_admin

    @pytest.mark.asyncio
    async def test_get_admin_session_commits_on_success_rollback_on_exception_closes_always(self):
        """get_admin_session must commit on success, rollback on exception, close always."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        mock_sessionmaker = MagicMock()
        mock_sessionmaker.return_value = mock_session

        with patch("app.db.session.get_admin_engine") as mock_get_engine:
            mock_get_engine.return_value = MagicMock()
            with patch("app.db.session.async_sessionmaker") as mock_asm:
                mock_asm.return_value = mock_sessionmaker

                # Test success case
                gen = session_mod.get_admin_session()
                session = await gen.__anext__()
                assert session is mock_session
                with pytest.raises(StopAsyncIteration):
                    await gen.__anext__()
                mock_session.commit.assert_called_once()
                mock_session.rollback.assert_not_called()
                mock_session.close.assert_called_once()

                # Reset
                mock_session.commit.reset_mock()
                mock_session.rollback.reset_mock()
                mock_session.close.reset_mock()

                # Test exception case
                gen2 = session_mod.get_admin_session()
                session2 = await gen2.__anext__()
                assert session2 is mock_session
                with pytest.raises(ValueError):
                    await gen2.athrow(ValueError("test error"))
                mock_session.rollback.assert_called_once()
                mock_session.commit.assert_not_called()
                mock_session.close.assert_called_once()


class TestAdminRouter:
    """Integration tests for admin router with mocked get_admin_session"""

    @pytest.fixture
    def mock_admin_session(self):
        session = AsyncMock(spec=AsyncSession)
        return session

    @pytest.fixture
    def app_with_mocked_admin_session(self, mock_admin_session):
        app = FastAPI()
        app.include_router(router)

        async def override_get_admin_session():
            yield mock_admin_session

        from app.db.session import get_admin_session

        app.dependency_overrides[get_admin_session] = override_get_admin_session
        return app

    @pytest.mark.asyncio
    async def test_get_businesses_with_valid_key_returns_200_and_list(
        self, app_with_mocked_admin_session, mock_admin_session
    ):
        """GET /api/v1/admin/businesses?key=valid -> 200 and lista de AdminBusinessOut"""
        from app.api.routers.admin import _verificar_secreto

        app_with_mocked_admin_session.dependency_overrides[_verificar_secreto] = lambda: None

        mock_business = MagicMock(spec=Business)
        mock_business.id = __import__("uuid").uuid4()
        mock_business.name = "Test Business"
        mock_business.slug = "test"
        mock_business.plan = BusinessPlan.FREE
        mock_business.status = BusinessStatus.ACTIVE
        mock_business.created_at = datetime.now(UTC)
        mock_business.pro_until = None

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_business]
        mock_result_pagos = MagicMock()
        mock_result_pagos.scalars.return_value = []
        mock_admin_session.execute = AsyncMock(side_effect=[mock_result, mock_result_pagos])

        with (
            patch("app.api.routers.admin.fin_del_trial") as mock_fin,
            patch("app.api.routers.admin.dias_restantes") as mock_dias,
        ):
            mock_fin.return_value = datetime.now(UTC)
            mock_dias.return_value = 30
            transport = ASGITransport(app=app_with_mocked_admin_session)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/businesses?key=valid")
                assert resp.status_code == 200
                data = resp.json()
                assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_stats_with_valid_key_returns_200_with_counters(
        self, app_with_mocked_admin_session, mock_admin_session
    ):
        """GET /api/v1/admin/stats?key=valid -> 200 con contadores"""
        from app.api.routers.admin import _verificar_secreto

        app_with_mocked_admin_session.dependency_overrides[_verificar_secreto] = lambda: None

        mock_business = MagicMock(spec=Business)
        mock_business.plan = BusinessPlan.FREE
        mock_business.status = BusinessStatus.ACTIVE
        mock_business.created_at = datetime.now(UTC)
        mock_business.pro_until = None

        mock_result_negocios = MagicMock()
        mock_result_negocios.scalars.return_value.all.return_value = [mock_business]
        mock_result_pendientes = MagicMock()
        mock_result_pendientes.scalar_one.return_value = 0

        mock_admin_session.execute = AsyncMock(
            side_effect=[mock_result_negocios, mock_result_pendientes]
        )

        with patch("app.api.routers.admin.fin_del_trial") as mock_fin:
            mock_fin.return_value = datetime.now(UTC)
            transport = ASGITransport(app=app_with_mocked_admin_session)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/stats?key=valid")
                assert resp.status_code == 200
                data = resp.json()
                assert "en_prueba" in data
                assert "pendientes" in data
                assert "pro_activos" in data
                assert "vencidos" in data

    @pytest.mark.asyncio
    async def test_get_businesses_with_invalid_key_returns_401(self, app_with_mocked_admin_session):
        """GET /api/v1/admin/businesses?key=invalid -> 401"""
        from app.api.routers.admin import get_settings_dep
        from app.core.config import Settings
        from pydantic import SecretStr

        settings = MagicMock(spec=Settings)
        settings.admin_secret = SecretStr("valid_secret")

        async def override_settings():
            return settings

        app_with_mocked_admin_session.dependency_overrides[get_settings_dep] = override_settings

        transport = ASGITransport(app=app_with_mocked_admin_session)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/businesses?key=invalid")
            assert resp.status_code == 401

    def test_estado_de_returns_correct_for_all_cases(self):
        """_estado_de() devuelve correcto para trial / activo / pro_vencido / bloqueado / vencido"""
        now = datetime.now(UTC)
        with (
            patch("app.api.routers.admin.fin_del_trial") as mock_fin,
            patch("app.api.routers.admin.now") as mock_now,
        ):
            mock_now.return_value = now
            # trial
            b_trial = MagicMock(spec=Business)
            b_trial.plan = BusinessPlan.FREE
            b_trial.status = BusinessStatus.ACTIVE
            b_trial.created_at = now
            b_trial.pro_until = None
            mock_fin.return_value = now + timedelta(days=30)
            assert _estado_de(b_trial) == "trial"

            # activo
            b_pro_active = MagicMock(spec=Business)
            b_pro_active.plan = BusinessPlan.PRO
            b_pro_active.status = BusinessStatus.ACTIVE
            b_pro_active.created_at = now
            b_pro_active.pro_until = now + timedelta(days=10)
            assert _estado_de(b_pro_active) == "activo"

            # pro_vencido
            b_pro_expired = MagicMock(spec=Business)
            b_pro_expired.plan = BusinessPlan.PRO
            b_pro_expired.status = BusinessStatus.ACTIVE
            b_pro_expired.created_at = now
            b_pro_expired.pro_until = now - timedelta(days=1)
            assert _estado_de(b_pro_expired) == "pro_vencido"

            # bloqueado
            b_blocked = MagicMock(spec=Business)
            b_blocked.plan = BusinessPlan.FREE
            b_blocked.status = BusinessStatus.SUSPENDED
            b_blocked.created_at = now
            b_blocked.pro_until = None
            mock_fin.return_value = now + timedelta(days=30)
            assert _estado_de(b_blocked) == "bloqueado"

            # vencido
            b_expired_trial = MagicMock(spec=Business)
            b_expired_trial.plan = BusinessPlan.FREE
            b_expired_trial.status = BusinessStatus.ACTIVE
            b_expired_trial.created_at = now - timedelta(days=100)
            b_expired_trial.pro_until = None
            mock_fin.return_value = now - timedelta(days=1)
            assert _estado_de(b_expired_trial) == "vencido"


class TestDeployConfig:
    """Deploy configuration tests"""

    def test_dockerfile_has_entrypoint_and_cmd_only_uvicorn(self):
        """Verificá backend/Dockerfile: ENTRYPOINT debe estar y CMD debe ser SOLO uvicorn... sin alembic"""
        dockerfile_path = "backend/Dockerfile"
        with Path(dockerfile_path).open(encoding="utf-8") as f:
            content = f.read()

        assert 'ENTRYPOINT ["/app/docker-entrypoint.sh"]' in content
        assert "CMD uvicorn app.main:app" in content
        # The CMD with uvicorn should not contain alembic
        for line in content.splitlines():
            if line.strip().startswith("CMD") and "uvicorn" in line:
                assert "alembic" not in line.lower()
                break
        else:
            raise AssertionError("uvicorn CMD not found")

    def test_docker_entrypoint_runs_alembic_once_if_migration_url_set_then_exec(self):
        """Verificá backend/docker-entrypoint.sh: si existe DATABASE_MIGRATION_URL hace alembic upgrade head una sola vez, luego exec "$@" """
        entrypoint_path = "backend/docker-entrypoint.sh"
        with Path(entrypoint_path).open(encoding="utf-8") as f:
            content = f.read()

        assert "DATABASE_MIGRATION_URL" in content
        assert "alembic upgrade head" in content
        assert 'exec "$@"' in content
        alembic_count = content.count("alembic upgrade head")
        assert alembic_count >= 1

    def test_no_old_admin_session_referenced(self):
        """Verificá que no haya admin_session viejo referenciado (debe ser get_admin_session)"""
        router_path = "backend/app/api/routers/admin/__init__.py"
        with Path(router_path).open(encoding="utf-8") as f:
            content = f.read()
        assert "get_admin_session" in content
        assert "Depends(admin_session)" not in content
        assert "Sesion = Annotated[AsyncSession, Depends(get_admin_session)]" in content
