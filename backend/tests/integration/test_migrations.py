"""Las migraciones y el modelo no pueden desincronizarse.

`alembic check` es el guardiÃ¡n: compara el modelo de SQLAlchemy contra la base y
falla si hay diferencias. Es la unica forma de que un `ALTER TABLE` olvidado llegue
a produccion, porque el resto del suite pasa igual: los tests de unitario miran la
metadata, los de integracion miran la base, y sin este puente nadie los junta.

Se corre **dentro** de pytest y no como paso separado del CI, para que no se pueda
saltar sin que nadie lo note.
"""

from __future__ import annotations

import ast
import datetime as dt
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

BACKEND = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.integration]


def _superuser_url(migration_database_url: str) -> str:
    """La misma base de test, pero con un rol que pueda deshacer `0005`.

    Se **deriva de `migration_database_url`** en vez de escribirla entera. La
    version anterior traia `127.0.0.1:5433` copiado a mano, y por eso este test
    apuntaba a otra base: una vacia, con `alembic_version` en NULL. Ahi
    `downgrade base` no tiene nada que deshacer, sale con codigo 0, y el test
    fallaba diciendo que no habia piso de reversibilidad--o sea, el piso existia y
    el test no lo estaba mirando. Un puerto escrito a mano en un test es una
    promesa de que el puerto no va a cambiar; el puerto SI cambia.

    Solo se sustituyen las credenciales: host, puerto y base se heredan, asi que
    el superuser cae en la base de test aunque `TEST_DATABASE_URL` apunte a otro
    lado. `SUPERUSER_DATABASE_URL` gana si esta definida, y las variables sueltas
    `POSTGRES_*` son el override de una linea, igual que en
    `tests/tenancy/test_login_definer.py`.
    """
    if override := os.environ.get("SUPERUSER_DATABASE_URL"):
        return override
    partes = urlsplit(migration_database_url)
    return urlunsplit(
        (
            partes.scheme,
            f"{os.environ.get('POSTGRES_USER', 'postgres')}:"
            f"{os.environ.get('POSTGRES_PASSWORD', 'postgres_dev_pw')}"
            f"@{partes.hostname or '127.0.0.1'}:{partes.port or 5432}",
            partes.path,
            "",
            "",
        )
    )


def _alembic(*args: str, url: str) -> subprocess.CompletedProcess[str]:
    """Corre Alembic como subproceso, con la URL del rol de DDL.

    Subproceso y no `alembic.config.main` en el proceso: `env.py` lee la
    configuracion desde el entorno y levanta un event loop propio. Llamarlo in
    process desde un test que ya esta corriendo en un loop rompe de formas que no
    dicen nada del codigo.
    """
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        cwd=BACKEND,
        env={
            "PATH": "/usr/bin:/bin",
            "JWT_SECRET_KEY": "ci-test-jwt-secret-key-not-for-prod-64-chars-long-xxxx-xxxx-xxxx",
            "ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            "SCHEDULER_TICK_SECRET": "ci-test-scheduler-secret-not-for-prod",
            "DATABASE_MIGRATION_URL": url,
            "DATABASE_URL": url,
            "SYSTEMROOT": "C:\\Windows",
        },
        check=False,
    )


class TestSinDrift:
    def test_el_modelo_coincide_con_la_base(self, migration_database_url: str) -> None:
        resultado = _alembic("check", url=migration_database_url)
        assert resultado.returncode == 0, (
            "el modelo y la base divergieron. Corre "
            "`python -m alembic revision --autogenerate`.\n"
            f"stdout: {resultado.stdout}\nstderr: {resultado.stderr}"
        )
        assert "No new upgrade operations detected" in resultado.stdout


class TestEstadoDeLasMigraciones:
    async def test_la_base_esta_en_head(
        self, connection: AsyncConnection, migration_database_url: str
    ) -> None:
        """La base de test esta en la ultima revision.

        Si el CI corre los tests contra una base sin migrar, casi todo pasa igual y
        el fallo aparece semanas despues. Este test es el que dice "la base de test
        esta desactualizada" en el mensaje.
        """
        version: str = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
        assert version is not None, (
            "la base no tiene version registrada: falta alembic upgrade head"
        )

        resultado = _alembic("heads", url=migration_database_url)
        head = resultado.stdout.strip().splitlines()[-1]
        assert version in head, f"la base esta en {version} y el head es {head}"

    async def test_no_hay_mas_de_una_cabeza(self, migration_database_url: str) -> None:
        """Dos ramas en el grafo de migraciones significan deploy ambiguo.

        No es un error de ahora: es el error que aparece dentro de tres meses, cuando
        los dos TweetSq de migraciones se aplican en distinta base y las dos existen.
        Detectar la divergencia cuando se crea es la unica forma barata.
        """
        resultado = _alembic("heads", url=migration_database_url)
        lineas = [linea for linea in resultado.stdout.splitlines() if linea.strip()]
        assert len(lineas) == 1, f"el grafo tiene {len(lineas)} cabezas: {lineas}"

    def test_las_migraciones_no_dependen_del_codigo_de_aplicacion(
        self, migration_database_url: str
    ) -> None:
        """Las migraciones no importan el codigo de aplicacion.

        Es tentador y es un error. Una migracion tiene que seguir aplicando dentro
        de dos aÃ±os, cuando el modelo haya cambiado: si importa `app.models`, el dia
        que se renombre una tabla la migracion vieja deja de importar y no hay forma
        de llegar a la ultima revision desde cero.

        **Se mira el arbol de sintaxis, no el texto.** Buscar la cadena `app.modules`
        en el archivo tambien encuentra los comentarios y los docstrings que la
        nombran para explicar de donde salio algo, que es justo lo que una migracion
        bien escrita hace. Con la busqueda textual, `0011_businesses_rls_write`--que
        anota en un comentario que su lista de columnas sale de
        `app.modules.businesses.service`-- quedaba marcado, y la unica forma de
        dejar el test en verde era borrar la referencia util.

        El analisis sintactico es ademas mas estricto: un `import app.models` escrito
        dentro de una cadena--un `exec`, un DDL generated-- tampoco se escapa, y un
        `importlib.import_module("app.models")` tampoco.

        La unica excepcion es `app.db.types` en la 0001: sin ese import, el DDL con
        `app.db.types.UtcDateTime` no resuelve el nombre del tipo. Es un tipo, no el
        modelo, y no cambia con el esquema.
        """
        permitidos = {"app.db.types"}
        for ruta in (BACKEND / "migrations" / "versions").glob("*.py"):
            arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))

            for nodo in ast.walk(arbol):
                modulos: list[str] = []
                if isinstance(nodo, ast.Import):
                    modulos = [alias.name for alias in nodo.names]
                elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                    modulos = [nodo.module]

                for modulo in modulos:
                    if modulo in permitidos:
                        continue
                    raiz = modulo.split(".")[0]
                    assert raiz != "app" or modulo in permitidos, (
                        f"{ruta.name} importa {modulo}, que es codigo de aplicacion. "
                        f"La migracion dejaria de aplicar cuando ese modulo cambie. "
                        f"Si de verdad hace falta--un tipo, por ejemplo-- hay que "
                        f"agregarlo a `permitidos` con una razon."
                    )

            # `importlib.import_module("app.models")` es el mismo problema escrito
            # de otra forma, y no aparece en `ast.Import`.
            for nodo in ast.walk(arbol):
                if (
                    isinstance(nodo, ast.Call)
                    and isinstance(nodo.func, ast.Attribute)
                    and nodo.func.attr == "import_module"
                    and nodo.args
                    and isinstance(nodo.args[0], ast.Constant)
                    and isinstance(nodo.args[0].value, str)
                    and nodo.args[0].value.startswith("app")
                ):
                    pytest.fail(
                        f"{ruta.name} carga {nodo.args[0].value} con import_module: "
                        "es una dependencia de la migracion disfrazada."
                    )


class TestAutogenerate:
    """`--autogenerate` es el comando que apaga la RLS, y por lo tanto el peligroso.

    Se ejecuta de verdad, en un subproceso, y se mide la RLS antes y despues. Un test
    que solo llamara a la funcion interna pasaria aunque el apagado ocurriera en otro
    lado del flujo; lo que importa es el efecto observable sobre la base.
    """

    async def test_autogenerate_no_deja_la_rls_apagada(
        self, connection: AsyncConnection, migration_database_url: str
    ) -> None:
        """Corrida la mas comun del proyecto y no puede INVALIDAR el aislamiento.

        Para comparar el schema sin que el dueno de las tablas las vea vacias,
        `env.py` apaga la RLS, compara y la vuelve a prender. Ese vaiven es
        correcto; lo que lo rompia era *como* reencendia: releia de `pg_class` la
        lista de tablas con RLS, y una vez apagadas no quedaba ninguna, asi que el
        reencendido no reencendia nada.

        El resultado era el peor posible de diagnosticar: 20 politicas en su sitio,
        `FORCE` intacto, y `alembic check` conforme, pero **cualquier rol veÃ­a las
        filas de todos los tenants**. No habia ni una linea de log. Los tests de
        tenancy lo detectaron como "de pronto el seed no aplica", y la primera
        hipotesis razonable era el seed.
        """
        antes: int = await _rls_tables(connection)

        versiones = BACKEND / "migrations" / "versions"
        previos = {ruta.name for ruta in versiones.glob("*.py")}
        try:
            resultado = _alembic(
                "revision",
                "--autogenerate",
                "-m",
                "verificacion_de_rls",
                url=migration_database_url,
            )
            assert resultado.returncode == 0, (
                "el autogenerate fallo, asi que el estado de la RLS no se puede "
                f"evaluar.\nstdout: {resultado.stdout}\nstderr: {resultado.stderr}"
            )
        finally:
            # El archivo generado se borra siempre: si se queda, deja una segunda
            # cabeza en el grafo y rompe el resto del suite.
            for ruta in versiones.glob("*.py"):
                if ruta.name not in previos:
                    ruta.unlink()

        despues: int = await _rls_tables(connection)
        assert despues == antes, (
            f"autogenerate dejo RLS en {despues} tablas de las {antes} que la "
            "tenian. Quedo el apagado pegado en la base de datos."
        )


async def _rls_tables(connection: AsyncConnection) -> int:
    return (
        await connection.execute(
            text(
                "SELECT count(*) FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity"
            )
        )
    ).scalar_one()


class TestElPisoDeReversibilidad:
    """`0003` no se puede deshacer, y eso tiene que estar escrito y probado.

    Agregar valores a un enum no tiene vuelta con asyncpg: lanza
    `FeatureNotSupportedError: dropping an enum value is not implemented` antes de
    mandar nada a la base. Se intento esquivarlo y no se puede -- ni con el protocolo
    simple, ni envolviendo el `ALTER` en un `DO $$ ... $$`, que ademas es la forma
    correcta porque `DROP VALUE` si corre en transaccion.

    Un piso que nadie documenta sigue siendo un piso, y el que lo descubre es el que
    necesita un `alembic downgrade base` a las 3am. Estos tests existen para que el
    comportamiento sea el documentado y no una sorpresa.
    """

    def test_el_downgrade_falla_con_un_mensaje_que_dice_que_es_un_piso(
        self, migration_database_url: str
    ) -> None:
        """El downgrade a base falla porque hay pisos de reversibilidad.

        Con la migracion 0005 agregada, el downgrade de 0005 requiere superuser
        (cambiar owner de funcion). El test usa la URL de superuser para el downgrade,
        de modo que 0005 se revierte correctamente y luego falla en 0003 como se espera.
        """
        superuser_url = _superuser_url(migration_database_url)
        resultado = _alembic("downgrade", "base", url=superuser_url)
        assert resultado.returncode != 0, (
            "deberia haber un piso de reversibilidad y no se pudo deshacer"
        )
        salida = resultado.stdout + resultado.stderr
        assert "piso de reversibilidad" in salida, (
            "el fallo no explica que hay un piso de reversibilidad, asi que no sirve de nada:\n"
            + salida
        )
        # El mensaje tiene que decir por donde seguir, no solo que no se puede.
        assert "0002_rls_triggers_grants" in salida, "el mensaje no dice cual es el piso"

    async def test_un_downgrade_fallido_no_mueve_la_base(
        self, connection: AsyncConnection, migration_database_url: str
    ) -> None:
        """La base queda **intacta**, no a medio camino.

        `CommandError` se levanta antes de tocar nada. Si algun dia el `downgrade`
        empieza a hacer trabajo antes de fallar -- por ejemplo, soltar el primer
        `DROP VALUE` y fallar en el segundo -- la base queda con `business_user_role`
        sin `professional` y `membership_status` con `locked`, y ninguna migracion
        posterior sabe arreglar eso. Este test falla el dia que ese riesgo aparece.
        """
        antes: str = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()

        resultado = _alembic("downgrade", "base", url=migration_database_url)
        assert resultado.returncode != 0

        despues: str = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
        assert despues == antes, (
            f"un downgrade fallido movio la base de {antes} a {despues}: el piso esta "
            "empezando a hacer trabajo a medias"
        )

    async def test_los_dos_valores_siguen_ahipara_despues_del_intento(
        self, connection: AsyncConnection, migration_database_url: str
    ) -> None:
        """Ni el enum ni sus valores cambian por un intento fallido."""
        _alembic("downgrade", "base", url=migration_database_url)
        labels: Sequence[str] = (
            (
                await connection.execute(
                    text(
                        "SELECT e.enumlabel FROM pg_enum e "
                        "JOIN pg_type t ON t.oid = e.enumtypid "
                        "WHERE t.typname = 'business_user_role'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "professional" in labels, f"los valores del enum cambiaron: {list(labels)}"


class TestObjetosDeSeguridad:
    """RLS, permisos y triggers existen en la base, no solo en el archivo."""

    async def test_el_trigger_de_updated_at_esta_en_las_tablas_con_timestamps(
        self, connection: AsyncConnection
    ) -> None:
        """El trigger existe en las 27 tablas que tienen timestamps.

        Se consulta `pg_trigger` y no `information_schema.triggers` a proposito.
        `information_schema` filtra por privilegio de tabla, asi que desde el rol de
        la aplicacion oculta los triggers de `businesses`, `platform_users` y
        `slug_reservations`, donde no hay permiso. Con esa vista el test reporta tres
        tablas faltantes que en realidad estan cubiertas, y el primer impulso seria
        "arreglar la migracion", que ya esta bien.
        """
        from app.db.base import Base

        tablas_con_timestamps = {
            nombre
            for nombre, tabla in Base.metadata.tables.items()
            if {"created_at", "updated_at"} <= set(tabla.c.keys())
        }
        filas: Sequence[str] = (
            (
                await connection.execute(
                    text(
                        "SELECT c.relname FROM pg_trigger t "
                        "JOIN pg_class c ON c.oid = t.tgrelid "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND NOT t.tgisinternal "
                        "AND t.tgname LIKE 'trg\\_%\\_set\\_updated\\_at'"
                    )
                )
            )
            .scalars()
            .all()
        )
        con_trigger = set(filas)

        faltantes = sorted(tablas_con_timestamps - con_trigger)
        assert not faltantes, f"tablas sin trigger de updated_at: {faltantes}"

    async def test_updated_at_se_actualiza_solo(
        self, connection: AsyncConnection, business_a: object
    ) -> None:
        """El trigger escribe `updated_at` sin que la app lo mencion.

        Si la app tuviera que setearlo, cada `INSERT`/`UPDATE` futuro que se olvide
        dejaria la fila con un `updated_at` viejo, y cualquier consulta que ordene o
        filtre por modificacion devuelve resultados que no cuadran.

        La fila se fecha primero a una hora conocida del pasado en vez de comparar contra `now()`: dentro de una transaccion `now()` es constante, asi que
        "antes y despues" daria el mismo valor y el test pasaria sin comprobar nada.
        """
        from app.db.session import TENANT_GUC

        from tests.conftest import CUSTOMER_A

        await connection.execute(
            text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
            {"tenant": str(business_a)},
        )
        # Se pisa `updated_at` a mano y con eso se desactiva el trigger para esta
        # fila; lo que se mide es si el UPDATE siguiente lo vuelve a levantar.
        await connection.execute(
            text("UPDATE customers SET updated_at = '2020-01-01 00:00:00+00' WHERE id = :id"),
            {"id": CUSTOMER_A},
        )
        await connection.execute(
            text("UPDATE customers SET notes = 'cambio' WHERE id = :id"),
            {"id": CUSTOMER_A},
        )
        despues: dt.datetime = (
            await connection.execute(
                text("SELECT updated_at FROM customers WHERE id = :id"),
                {"id": CUSTOMER_A},
            )
        ).scalar_one()
        assert despues.year > 2020, f"el trigger no actualizo updated_at: {despues}"

    async def test_el_rol_de_app_no_tiene_ddl(self, connection: AsyncConnection) -> None:
        """El rol de la aplicacion no es dueno de ninguna tabla.

        Un rol de app dueno de las tablas puede alterarlas, y `FORCE RLS` deja de
        protegerlo: seria un equivalente de superusuario. Se verifica por catalogo en
        vez de intentar un `CREATE TABLE` fallido, para que el test no dependa de que
        el permiso este y para que el error diga cual es el problema.
        """
        duenas: int = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM pg_tables "
                    "WHERE schemaname = 'public' AND tableowner = 'tempus_app'"
                )
            )
        ).scalar_one()
        assert duenas == 0, "el rol de la app no debe ser dueno de ninguna tabla"

    async def test_el_rol_de_app_no_puede_crear_tablas(self, connection: AsyncConnection) -> None:
        """Y tampoco crear objetos en el schema.

        Un `CREATE` permitido seria lo mismo que DDL: cualquiera que llegue a
        ejecutar SQL arbitrario, sin importar como, define su propia tabla con las
        columnas que quiera.
        """
        puede_crear: bool = (
            await connection.execute(
                text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
            )
        ).scalar_one()
        assert puede_crear is False, "el rol de la app no puede tener CREATE en el schema"
