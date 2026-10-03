"""Tests de catálogo para el rol tempus_login_definer.

Verifican que el bypass de RLS esté acotado exactamente a lo necesario:
- rol existe con BYPASSRLS, NOLOGIN
- NO tiene miembros (ni tempus_app ni tempus_owner)
- SIN privilegio de tabla; SOLO SELECT por columna sobre `business_users`,
  y solo sobre las columnas que las funciones de login leen de verdad
- las funciones de login son SECURITY DEFINER, owner = tempus_login_definer
- search_path fijado, sin pg_temp
- PUBLIC sin EXECUTE; tempus_app con EXECUTE
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine


@pytest_asyncio.fixture(scope="session")
async def superuser_engine() -> AsyncIterator[AsyncEngine]:
    """Engine con el superusuario (postgres) para tests de catálogo que lo requieren.

    Usa las mismas credenciales que el init script: postgres / postgres_dev_pw.
    """
    url = (
        os.environ.get("SUPERUSER_DATABASE_URL")
        or "postgresql+asyncpg://postgres:postgres_dev_pw@127.0.0.1:5433/postgres"
    )
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
    """Engine con el rol de DDL (tempus_owner) para tests que necesitan ver privilegios."""
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
    """El rol dedicado existe y tiene exactamente los atributos pedidos."""

    async def test_rol_existe_con_bypassrls_sin_login(
        self, superuser_connection: AsyncConnection
    ) -> None:
        fila = (
            await superuser_connection.execute(
                text(
                    "SELECT rolname, rolbypassrls, rolcanlogin, rolinherit "
                    "FROM pg_roles WHERE rolname = 'tempus_login_definer'"
                )
            )
        ).one()
        assert fila.rolname == "tempus_login_definer"
        assert fila.rolbypassrls is True, "el rol debe tener BYPASSRLS"
        assert fila.rolcanlogin is False, "NOLOGIN: no debe poder conectarse"
        assert fila.rolinherit is False, "NOINHERIT: no hereda privilegios"

    async def test_tempus_app_sin_bypassrls(self, superuser_connection: AsyncConnection) -> None:
        fila = (
            await superuser_connection.execute(
                text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'tempus_app'")
            )
        ).one()
        assert fila.rolbypassrls is False

    async def test_tempus_owner_sin_bypassrls(self, superuser_connection: AsyncConnection) -> None:
        fila = (
            await superuser_connection.execute(
                text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'tempus_owner'")
            )
        ).one()
        assert fila.rolbypassrls is False

    async def test_tempus_app_no_es_miembro_del_definer(
        self, superuser_connection: AsyncConnection
    ) -> None:
        filas = (
            await superuser_connection.execute(
                text(
                    "SELECT 1 FROM pg_auth_members m "
                    "JOIN pg_roles r ON r.oid = m.member "
                    "JOIN pg_roles g ON g.oid = m.roleid "
                    "WHERE g.rolname = 'tempus_login_definer' "
                    "AND r.rolname = 'tempus_app'"
                )
            )
        ).all()
        assert len(filas) == 0, "tempus_app no debe ser miembro de tempus_login_definer"

    async def test_tempus_owner_no_es_miembro_del_definer(
        self, superuser_connection: AsyncConnection
    ) -> None:
        filas = (
            await superuser_connection.execute(
                text(
                    "SELECT 1 FROM pg_auth_members m "
                    "JOIN pg_roles r ON r.oid = m.member "
                    "JOIN pg_roles g ON g.oid = m.roleid "
                    "WHERE g.rolname = 'tempus_login_definer' "
                    "AND r.rolname = 'tempus_owner'"
                )
            )
        ).all()
        assert len(filas) == 0, "tempus_owner no debe ser miembro de tempus_login_definer"


class TestPrivilegiosDelDefiner:
    """El rol tiene SELECT **por columna** en business_users y nada mas.

    El privilegio minimo es por columna, no de tabla, y esto no es un detalle de
    redaccion: que un rol con `BYPASSRLS` pueda leer `business_users` completa
    significa que puede leer cualquier columna que se agregue en el futuro--un
    `mfa_secret`, un `recovery_code`-- sin que nadie lo decida. La migracion
    `0008` documento justo eso cuando le agrego `email`: el permiso de columna es
    por columna *tocada*, y el `WHERE` tambien cuenta.
    """

    async def test_no_tiene_privilegio_de_tabla_en_business_users(
        self, owner_connection: AsyncConnection
    ) -> None:
        """Cero privilegios de tabla. Este es el que protege la superficie pre-tenant.

        `information_schema.table_privileges` solo lista privilegios de tabla, asi
        que un `GRANT SELECT ON business_users` a mano apareceria aca y haria fallar
        el test. Es la mitad del invariante; la otra mitad es que las columnas
        concedidas sean exactamente las que se usan, y va en el test de abajo.
        """
        filas = (
            await owner_connection.execute(
                text(
                    "SELECT privilege_type "
                    "FROM information_schema.table_privileges "
                    "WHERE table_name = 'business_users' "
                    "AND grantee = 'tempus_login_definer'"
                )
            )
        ).all()
        privilegios = {f.privilege_type for f in filas}
        assert privilegios == set(), (
            f"privilegios de tabla: {privilegios}; el minimo privilegio es por columna "
            "(0005/0008). Un SELECT de tabla sobre business_users le da a un rol con "
            "BYPASSRLS lectura de cualquier columna futura."
        )

    async def test_las_columnas_concedidas_son_exactamente_las_que_la_funcion_lee(
        self, owner_connection: AsyncConnection
    ) -> None:
        """Las columnas se derivan del cuerpo de la funcion, no de una lista escrita.

        Es el test que evita volver a commitear el bug de `0008`. Ahi el `SELECT`
        devuelto era el que se contaba y el `WHERE u.email = p_email` no; la funcion
        fallaba con "permiso denegado" y el login devolvia 500 para todos los
        usuarios. Aqui la lista se lee de `pg_get_functiondef`, asi que agregar una
        columna al cuerpo sin concederla--o conceder una que ya no se usa-- rompe
        este test y no el login.
        """
        cuerpo: str = (
            await owner_connection.execute(
                text(
                    "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' "
                    "AND p.proname = 'auth_business_user_for_login'"
                )
            )
        ).scalar_one()
        usadas = set(re.findall(r"\bu\.(\w+)", cuerpo))
        assert usadas, "no se pudo leer el cuerpo de la funcion: el regex cambio"

        concedidas: set[str] = {
            f.column_name
            for f in (
                await owner_connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.column_privileges "
                        "WHERE table_name = 'business_users' "
                        "AND grantee = 'tempus_login_definer' "
                        "AND privilege_type = 'SELECT'"
                    )
                )
            ).all()
        }

        faltantes = usadas - concedidas
        sobrantes = concedidas - usadas
        assert not faltantes, (
            f"columnas que la funcion lee y el rol no tiene: {sorted(faltantes)}. "
            "El login va a fallar con 'permiso denegado' para todos los usuarios."
        )
        assert not sobrantes, (
            f"columnas concedidas que la funcion ya no lee: {sorted(sobrantes)}. "
            "Cada columna de mas es superficie de lectura para un rol con BYPASSRLS."
        )

    async def test_sin_privilegios_de_escritura_en_business_users(
        self, owner_connection: AsyncConnection
    ) -> None:
        filas = (
            await owner_connection.execute(
                text(
                    "SELECT privilege_type "
                    "FROM information_schema.table_privileges "
                    "WHERE table_name = 'business_users' "
                    "AND grantee = 'tempus_login_definer'"
                    "AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')"
                )
            )
        ).all()
        assert len(filas) == 0, (
            f"tempus_login_definer no debe tener privilegios de escritura en business_users: "
            f"{[f.privilege_type for f in filas]}"
        )

    async def test_usage_en_schema_public_y_nada_mas_global(
        self, owner_connection: AsyncConnection
    ) -> None:
        # PostgreSQL no tiene information_schema.schema_privileges; usamos has_schema_privilege
        fila = (
            await owner_connection.execute(
                text(
                    "SELECT has_schema_privilege('tempus_login_definer', 'public', 'USAGE') AS has_usage, "
                    "has_schema_privilege('tempus_login_definer', 'public', 'CREATE') AS has_create"
                )
            )
        ).one()
        assert fila.has_usage is True
        assert fila.has_create is False


class TestFuncionAuthBusinessUserForLogin:
    """La función pertenece al definer y tiene la configuración correcta."""

    async def test_owner_es_tempus_login_definer(self, owner_connection: AsyncConnection) -> None:
        fila = (
            await owner_connection.execute(
                text(
                    "SELECT pg_get_userbyid(p.proowner) AS owner "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' "
                    "AND p.proname = 'auth_business_user_for_login'"
                )
            )
        ).one()
        assert fila.owner == "tempus_login_definer"

    async def test_es_security_definer(self, owner_connection: AsyncConnection) -> None:
        fila = (
            await owner_connection.execute(
                text(
                    "SELECT prosecdef FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' "
                    "AND p.proname = 'auth_business_user_for_login'"
                )
            )
        ).one()
        assert fila.prosecdef is True

    async def test_search_path_fijado_sin_pg_temp(self, owner_connection: AsyncConnection) -> None:
        fila = (
            await owner_connection.execute(
                text(
                    "SELECT proconfig FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' "
                    "AND p.proname = 'auth_business_user_for_login'"
                )
            )
        ).one()
        config = fila.proconfig or []
        config_str = ",".join(config)
        # El search_path puede venir con o sin espacios: 'search_path=pg_catalog,public'
        # o 'search_path=pg_catalog, public' o 'search_path = pg_catalog, public'
        assert "search_path" in config_str and "pg_catalog" in config_str and "public" in config_str
        assert "pg_temp" not in config_str, f"pg_temp no debe estar en proconfig: {config_str}"


class TestACLDeLaFuncion:
    """ACLs: PUBLIC no ejecuta; tempus_app sí."""

    async def test_public_sin_execute(self, connection: AsyncConnection) -> None:
        fila = (
            await connection.execute(
                text(
                    "SELECT 1 FROM information_schema.routine_privileges "
                    "WHERE routine_name = 'auth_business_user_for_login' "
                    "AND grantee = 'PUBLIC' "
                    "AND privilege_type = 'EXECUTE'"
                )
            )
        ).all()
        assert len(fila) == 0, "PUBLIC no debe tener EXECUTE en la funcion"

    async def test_tempus_app_tiene_execute(self, connection: AsyncConnection) -> None:
        fila = (
            await connection.execute(
                text(
                    "SELECT has_function_privilege('tempus_app', "
                    "'auth_business_user_for_login(citext)', 'EXECUTE')"
                )
            )
        ).one()
        assert fila[0] is True


class TestAuthPlatformUserSinCambios:
    """auth_platform_user_for_login mantiene owner tempus_owner (platform_users sin RLS)."""

    async def test_owner_sigue_siendo_tempus_owner(self, owner_connection: AsyncConnection) -> None:
        fila = (
            await owner_connection.execute(
                text(
                    "SELECT pg_get_userbyid(p.proowner) AS owner "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' "
                    "AND p.proname = 'auth_platform_user_for_login'"
                )
            )
        ).one()
        assert fila.owner == "tempus_owner"
