"""Tests de catálogo para el rol tempus_login_definer."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

@pytest_asyncio.fixture(scope="session")
async def superuser_engine() -> AsyncIterator[AsyncEngine]:
    """Engine con el superusuario para tests de catálogo.

    En local y CI el superusuario es tempus_owner (POSTGRES_USER), no postgres.
    """
    user = os.environ.get("POSTGRES_USER", "tempus_owner")
    pw = os.environ.get("POSTGRES_PASSWORD", "tempus_owner_dev_pw")
    port = os.environ.get("POSTGRES_PORT", "5433")
    url = os.environ.get("SUPERUSER_DATABASE_URL") or f"postgresql+asyncpg://{user}:{pw}@127.0.0.1:{port}/postgres"
    engine = create_async_engine(url, poolclass=None, echo=False)
    try:
        yield engine
    finally:
        await engine.dispose()

@pytest_asyncio.fixture
async def superuser_connection(superuser_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    async with superuser_engine.connect() as conn:
        yield conn

@pytest_asyncio.fixture(scope="session")
async def owner_engine(migration_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migration_database_url, poolclass=None, echo=False)
    try:
        yield engine
    finally:
        await engine.dispose()

@pytest_asyncio.fixture
async def owner_connection(owner_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    async with owner_engine.connect() as conn:
        yield conn

class TestTempusLoginDefinerRole:
    async def test_rol_existe_con_bypassrls_sin_login(self, superuser_connection: AsyncConnection) -> None:
        fila = (await superuser_connection.execute(text("SELECT rolname, rolbypassrls, rolcanlogin, rolinherit FROM pg_roles WHERE rolname = 'tempus_login_definer'"))).one()
        assert fila.rolname == "tempus_login_definer"
        assert fila.rolbypassrls is True
        assert fila.rolcanlogin is False
        assert fila.rolinherit is False

    async def test_tempus_app_sin_bypassrls(self, superuser_connection: AsyncConnection) -> None:
        fila = (await superuser_connection.execute(text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'tempus_app'"))).one()
        assert fila.rolbypassrls is False

    async def test_tempus_owner_sin_bypassrls(self, superuser_connection: AsyncConnection) -> None:
        fila = (await superuser_connection.execute(text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'tempus_owner'"))).one()
        assert fila.rolbypassrls is False

    async def test_tempus_app_no_es_miembro_del_definer(self, superuser_connection: AsyncConnection) -> None:
        filas = (await superuser_connection.execute(text("SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member JOIN pg_roles g ON g.oid = m.roleid WHERE g.rolname = 'tempus_login_definer' AND r.rolname = 'tempus_app'"))).all()
        assert len(filas) == 0

    async def test_tempus_owner_no_es_miembro_del_definer(self, superuser_connection: AsyncConnection) -> None:
        filas = (await superuser_connection.execute(text("SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member JOIN pg_roles g ON g.oid = m.roleid WHERE g.rolname = 'tempus_login_definer' AND r.rolname = 'tempus_owner'"))).all()
        assert len(filas) == 0

class TestPrivilegiosDelDefiner:
    async def test_no_tiene_privilegio_de_tabla_en_business_users(self, owner_connection: AsyncConnection) -> None:
        filas = (await owner_connection.execute(text("SELECT privilege_type FROM information_schema.table_privileges WHERE table_name = 'business_users' AND grantee = 'tempus_login_definer'"))).all()
        privilegios = {f.privilege_type for f in filas}
        assert privilegios == set()

    async def test_las_columnas_concedidas_son_exactamente_las_que_la_funcion_lee(self, owner_connection: AsyncConnection) -> None:
        cuerpo: str = (await owner_connection.execute(text("SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.proname = 'auth_business_user_for_login'"))).scalar_one()
        usadas = set(re.findall(r"\bu\.(\w+)", cuerpo))
        assert usadas
        concedidas: set[str] = {f.column_name for f in (await owner_connection.execute(text("SELECT column_name FROM information_schema.column_privileges WHERE table_name = 'business_users' AND grantee = 'tempus_login_definer' AND privilege_type = 'SELECT'"))).all()}
        faltantes = usadas - concedidas
        sobrantes = concedidas - usadas
        assert not faltantes, f"faltan: {sorted(faltantes)}"
        assert not sobrantes, f"sobran: {sorted(sobrantes)}"

    async def test_sin_privilegios_de_escritura_en_business_users(self, owner_connection: AsyncConnection) -> None:
        filas = (await owner_connection.execute(text("SELECT privilege_type FROM information_schema.table_privileges WHERE table_name = 'business_users' AND grantee = 'tempus_login_definer' AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')"))).all()
        assert len(filas) == 0

    async def test_usage_en_schema_public_y_nada_mas_global(self, owner_connection: AsyncConnection) -> None:
        fila = (await owner_connection.execute(text("SELECT has_schema_privilege('tempus_login_definer', 'public', 'USAGE') AS has_usage, has_schema_privilege('tempus_login_definer', 'public', 'CREATE') AS has_create"))).one()
        assert fila.has_usage is True
        assert fila.has_create is False

class TestFuncionAuthBusinessUserForLogin:
    async def test_owner_es_tempus_login_definer(self, owner_connection: AsyncConnection) -> None:
        fila = (await owner_connection.execute(text("SELECT pg_get_userbyid(p.proowner) AS owner FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.proname = 'auth_business_user_for_login'"))).one()
        assert fila.owner == "tempus_login_definer"

    async def test_es_security_definer(self, owner_connection: AsyncConnection) -> None:
        fila = (await owner_connection.execute(text("SELECT prosecdef FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.proname = 'auth_business_user_for_login'"))).one()
        assert fila.prosecdef is True

    async def test_search_path_fijado_sin_pg_temp(self, owner_connection: AsyncConnection) -> None:
        fila = (await owner_connection.execute(text("SELECT proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.proname = 'auth_business_user_for_login'"))).one()
        config = fila.proconfig or []
        config_str = ",".join(config)
        assert "search_path" in config_str and "pg_catalog" in config_str and "public" in config_str
        assert "pg_temp" not in config_str

class TestACLDeLaFuncion:
    async def test_public_sin_execute(self, connection: AsyncConnection) -> None:
        fila = (await connection.execute(text("SELECT 1 FROM information_schema.routine_privileges WHERE routine_name = 'auth_business_user_for_login' AND grantee = 'PUBLIC' AND privilege_type = 'EXECUTE'"))).all()
        assert len(fila) == 0

    async def test_tempus_app_tiene_execute(self, connection: AsyncConnection) -> None:
        fila = (await connection.execute(text("SELECT has_function_privilege('tempus_app', 'auth_business_user_for_login(citext)', 'EXECUTE')"))).one()
        assert fila[0] is True

class TestAuthPlatformUserSinCambios:
    async def test_owner_sigue_siendo_tempus_owner(self, owner_connection: AsyncConnection) -> None:
        fila = (await owner_connection.execute(text("SELECT pg_get_userbyid(p.proowner) AS owner FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.proname = 'auth_platform_user_for_login'"))).one()
        assert fila.owner == "tempus_owner"
