"""Tests de catálogo para el rol tempus_login_definer."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine


@pytest_asyncio.fixture(scope="session")
async def superuser_engine(migration_database_url: str) -> AsyncIterator[AsyncEngine]:
    """Engine con el rol de DDL para tests de catálogo.

    Sale de la **misma** fuente que siembra el resto de la suite
    (`migration_database_url`) y no de `POSTGRES_USER`/`POSTGRES_PORT`
    reconstruidos a mano: esos defaults son los del compose (5433) y se
    rompen en cuanto el Postgres local escucha en otro puerto, que es
    exactamente lo que pasaba--diez tests que no llegaban ni a correr.

    Lo que se consulta aca es `pg_roles` y `pg_auth_members`, que son
    catálogo de cluster: se ven igual desde cualquier base, asi que no
    hace falta conectarse a la de mantenimiento. `SUPERUSER_DATABASE_URL`
    queda como override explicito para cuando si haga falta otra.
    """
    url = os.environ.get("SUPERUSER_DATABASE_URL") or migration_database_url
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
    async def test_rol_existe_con_bypassrls_sin_login(
        self, superuser_connection: AsyncConnection
    ) -> None:
        fila = (
            await superuser_connection.execute(
                text(
                    "SELECT rolname, rolbypassrls, rolcanlogin, rolinherit FROM pg_roles WHERE rolname = 'tempus_login_definer'"
                )
            )
        ).one()
        assert fila.rolname == "tempus_login_definer"
        assert fila.rolbypassrls is True
        assert fila.rolcanlogin is False
        assert fila.rolinherit is False

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
                    "SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member JOIN pg_roles g ON g.oid = m.roleid WHERE g.rolname = 'tempus_login_definer' AND r.rolname = 'tempus_app'"
                )
            )
        ).all()
        assert len(filas) == 0

    async def test_tempus_owner_no_es_miembro_del_definer(
        self, superuser_connection: AsyncConnection
    ) -> None:
        filas = (
            await superuser_connection.execute(
                text(
                    "SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member JOIN pg_roles g ON g.oid = m.roleid WHERE g.rolname = 'tempus_login_definer' AND r.rolname = 'tempus_owner'"
                )
            )
        ).all()
        assert len(filas) == 0


class TestElRolPuente:
    """La topologia de tres roles que permite transferir la propiedad al definer.

    El puente existe porque `ALTER FUNCTION ... OWNER TO tempus_login_definer` exige
    poder hacer `SET ROLE` al rol destino, y `tempus_owner`--`NOSUPERUSER` en el init
    de docker-- no puede ser miembro permanente de un rol `BYPASSRLS`. Lo que hay es
    un rol intermedio, `tempus_migrator`, al que las migraciones recurren por dentro
    de su propia transaccion para otorgar esa membresia a tiempo y revocarla.

    Estos tests miran el estado **fuera** de las migraciones, o sea lo que queda. La
    garantia de que nada se filtra durante la migracion la da el hecho de que el
    `GRANT` y el `REVOKE` van en la misma transaccion--si algo falla, el rollback se
    lleva los dos-- y aca se comprueba que al terminar no hay rastro.
    """

    async def test_el_puente_existe_y_no_puede_conectarse(
        self, superuser_connection: AsyncConnection
    ) -> None:
        """`NOLOGIN` de entrada: el puente existe para ser un `SET ROLE`, no un acceso.

        Si pudiera conectarse, bastaria una credencial filtrada para convertirse en el
        rol que sabe administrar la membresia del definer.
        """
        fila = (
            await superuser_connection.execute(
                text(
                    "SELECT rolsuper, rolinherit, rolbypassrls, rolcanlogin, rolcreaterole"
                    " FROM pg_roles WHERE rolname = 'tempus_migrator'"
                )
            )
        ).one()
        assert fila.rolcanlogin is False
        assert fila.rolsuper is False
        assert fila.rolcreaterole is False
        # Sin BYPASSRLS: el puente administra la membresia, no lee datos.
        assert fila.rolbypassrls is False
        # Sin INHERIT: tampoco arrastra el BYPASSRLS del definer por su cuenta.
        assert fila.rolinherit is False

    async def test_solo_el_puente_tiene_admin_option_sobre_el_definer(
        self, superuser_connection: AsyncConnection
    ) -> None:
        """`ADMIN OPTION` sobre el definer es lo que permite otorgar la membresia.

        Y tiene que estar en un rol `NOLOGIN` que no sea el de DDL: si `tempus_owner`
        lo tuviera, pourrait concesser el definer a quien quisiera sin que quedara
        rastro en `pg_auth_members` como membresia--que es justo lo que este
        diseno evita.
        """
        filas = (
            await superuser_connection.execute(
                text(
                    "SELECT r.rolname AS miembro FROM pg_auth_members m"
                    " JOIN pg_roles r ON r.oid = m.member JOIN pg_roles g ON g.oid = m.roleid"
                    " WHERE g.rolname = 'tempus_login_definer' AND m.admin_option"
                )
            )
        ).all()
        assert [f.miembro for f in filas] == ["tempus_migrator"]

    async def test_el_dueno_usa_el_puente_sin_poder_administrarlo(
        self, superuser_connection: AsyncConnection
    ) -> None:
        """`tempus_owner` puede usar el puente, pero no Ampliar su membresia.

        Sin `ADMIN OPTION` en esta relacion, el rol de DDL puede entrar al puente para
        lo que las migraciones necesitan y nada mas: no puede agregar terceros al
        definer.
        """
        fila = (
            await superuser_connection.execute(
                text(
                    "SELECT m.admin_option FROM pg_auth_members m"
                    " JOIN pg_roles r ON r.oid = m.member JOIN pg_roles g ON g.oid = m.roleid"
                    " WHERE g.rolname = 'tempus_migrator' AND r.rolname = 'tempus_owner'"
                )
            )
        ).one()
        assert fila.admin_option is False

    async def test_tempus_owner_no_hereda_los_privilegios_del_definer(
        self, superuser_connection: AsyncConnection
    ) -> None:
        """La garantia de fondo: `NOINHERIT` + sin membresia = sin BYPASSRLS efectivo.

        Los tests de arriba comprueban que no hay membresia *directa*, que es lo
        visible en `pg_auth_members`. Este comprueba lo que de verdad importa: que
        `tempus_owner` no **tiene** los privilegios del definer ni por herencia.

        PostgreSQL puede resolver la pertenencia de forma transitiva, y la herencia de
        privilegios tambien: con `INHERIT`, ser miembro--directa o indirectamente--del
        definer de BYPASSRLS daria el privilege a la sesion del rol de DDL sin que
        ninguna consulta a `pg_auth_members` lo delatara. Por eso las dos mitades van
        en el mismo test: la membresia que este comprueba es la directa, y el
        `NOINHERIT` es lo que cierra la indirecta.
        """
        tiene_herencia = (
            await superuser_connection.execute(
                text("SELECT rolinherit FROM pg_roles WHERE rolname = 'tempus_owner'")
            )
        ).scalar_one()
        assert tiene_herencia is False, (
            "tempus_owner con INHERIT arrastra el BYPASSRLS del definer por la cadena"
            " tempus_owner -> tempus_migrator -> tempus_login_definer, y ademas"
            " pg_auth_members no lo delata"
        )

        # `USAGE` de un rol = "puede usar sus privilegios", sea por membresia directa
        # o heredada. Es la pregunta que responde si el BYPASSRLS esta al alcance.
        assert (
            await superuser_connection.execute(
                text("SELECT pg_has_role('tempus_owner', 'tempus_login_definer', 'USAGE')")
            )
        ).scalar_one() is False

    async def test_tempus_app_no_alcanza_el_definer(
        self, superuser_connection: AsyncConnection
    ) -> None:
        """El rol de la app no llega al definer por ningun camino.

        Es la diferencia entre este rol puente y el `GRANT` que se quito: el rol que
        atiende peticiones HTTP publicas no puede hacer `SET ROLE` al BYPASSRLS ni
        directa ni transitivamente. Un `SET ROLE` de mas en un pool de conexiones
        compartido dejariaRequests viendo filas de otros tenants, y ese fallo no sale
        en ningun test de endpoint.
        """
        assert (
            await superuser_connection.execute(
                text("SELECT pg_has_role('tempus_app', 'tempus_login_definer', 'USAGE')")
            )
        ).scalar_one() is False


class TestPrivilegiosDelDefiner:
    async def test_no_tiene_privilegio_de_tabla_en_business_users(
        self, owner_connection: AsyncConnection
    ) -> None:
        filas = (
            await owner_connection.execute(
                text(
                    "SELECT privilege_type FROM information_schema.table_privileges WHERE table_name = 'business_users' AND grantee = 'tempus_login_definer'"
                )
            )
        ).all()
        privilegios = {f.privilege_type for f in filas}
        assert privilegios == set()

    async def test_las_columnas_concedidas_son_exactamente_las_que_la_funcion_lee(
        self, owner_connection: AsyncConnection
    ) -> None:
        cuerpo: str = (
            await owner_connection.execute(
                text(
                    "SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.proname = 'auth_business_user_for_login'"
                )
            )
        ).scalar_one()
        usadas = set(re.findall(r"\bu\.(\w+)", cuerpo))
        assert usadas
        concedidas: set[str] = {
            f.column_name
            for f in (
                await owner_connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.column_privileges WHERE table_name = 'business_users' AND grantee = 'tempus_login_definer' AND privilege_type = 'SELECT'"
                    )
                )
            ).all()
        }
        faltantes = usadas - concedidas
        sobrantes = concedidas - usadas
        assert not faltantes, f"faltan: {sorted(faltantes)}"
        assert not sobrantes, f"sobran: {sorted(sobrantes)}"

    async def test_sin_privilegios_de_escritura_en_business_users(
        self, owner_connection: AsyncConnection
    ) -> None:
        filas = (
            await owner_connection.execute(
                text(
                    "SELECT privilege_type FROM information_schema.table_privileges WHERE table_name = 'business_users' AND grantee = 'tempus_login_definer' AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')"
                )
            )
        ).all()
        assert len(filas) == 0

    async def test_usage_en_schema_public_y_nada_mas_global(
        self, owner_connection: AsyncConnection
    ) -> None:
        fila = (
            await owner_connection.execute(
                text(
                    "SELECT has_schema_privilege('tempus_login_definer', 'public', 'USAGE') AS has_usage, has_schema_privilege('tempus_login_definer', 'public', 'CREATE') AS has_create"
                )
            )
        ).one()
        assert fila.has_usage is True
        assert fila.has_create is False


class TestFuncionAuthBusinessUserForLogin:
    async def test_owner_es_tempus_login_definer(self, owner_connection: AsyncConnection) -> None:
        fila = (
            await owner_connection.execute(
                text(
                    "SELECT pg_get_userbyid(p.proowner) AS owner FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.proname = 'auth_business_user_for_login'"
                )
            )
        ).one()
        assert fila.owner == "tempus_login_definer"

    async def test_es_security_definer(self, owner_connection: AsyncConnection) -> None:
        fila = (
            await owner_connection.execute(
                text(
                    "SELECT prosecdef FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.proname = 'auth_business_user_for_login'"
                )
            )
        ).one()
        assert fila.prosecdef is True

    async def test_search_path_fijado_sin_pg_temp(self, owner_connection: AsyncConnection) -> None:
        fila = (
            await owner_connection.execute(
                text(
                    "SELECT proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.proname = 'auth_business_user_for_login'"
                )
            )
        ).one()
        config = fila.proconfig or []
        config_str = ",".join(config)
        assert "search_path" in config_str and "pg_catalog" in config_str and "public" in config_str
        assert "pg_temp" not in config_str


class TestACLDeLaFuncion:
    async def test_public_sin_execute(self, connection: AsyncConnection) -> None:
        fila = (
            await connection.execute(
                text(
                    "SELECT 1 FROM information_schema.routine_privileges WHERE routine_name = 'auth_business_user_for_login' AND grantee = 'PUBLIC' AND privilege_type = 'EXECUTE'"
                )
            )
        ).all()
        assert len(fila) == 0

    async def test_tempus_app_tiene_execute(self, connection: AsyncConnection) -> None:
        fila = (
            await connection.execute(
                text(
                    "SELECT has_function_privilege('tempus_app', 'auth_business_user_for_login(citext)', 'EXECUTE')"
                )
            )
        ).one()
        assert fila[0] is True


class TestAuthPlatformUserSinCambios:
    async def test_owner_sigue_siendo_tempus_owner(self, owner_connection: AsyncConnection) -> None:
        fila = (
            await owner_connection.execute(
                text(
                    "SELECT pg_get_userbyid(p.proowner) AS owner FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.proname = 'auth_platform_user_for_login'"
                )
            )
        ).one()
        assert fila.owner == "tempus_owner"
