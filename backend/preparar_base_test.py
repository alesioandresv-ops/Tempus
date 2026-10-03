"""Prepara la base de tests: la crea si no existe y aplica las migraciones.

Existe como script--y no como instruccion en un README-- por una razon concreta:
los tests de integracion, concurrencia y tenancy se saltan silenciosamente si
`TEST_DATABASE_URL` no esta definido, y un `pytest` que dice "909 passed" sin
mencionar que otros 167 no corrieron es exactamente el tipo de verde que hace
creer que algo mas no se probo.

Un test que necesita una base y no la tiene tiene que decir "no me ejecuto", y
ese aviso tiene que ser visible. Este script deja el comando en un solo lugar en
vez de en la memoria de cada uno.

El borrado de la base es opcional y explicito (`--drop`). Sin `--drop` no se toca
nada existente: `CREATE DATABASE` es idempotente en la practica porque se
comprueba antes con `pg_database`, y un `DROP DATABASE` accidental seria la forma
mas rapida de perder un dia de trabajo.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys

from app.core.config import get_settings
from sqlalchemy import text

BASE_DEFECTO = "tempus_test"


def _servidor_de(url: str) -> dict:
    """Parte una URL de SQLAlchemy en lo que hace falta para `CREATE DATABASE`.

    No hay constantes de rol porque los usuarios se leen de la URL. Fijar
    `tempus_app` y `tempus_owner` aqui seria una segunda fuente de verdad que se
    desincroniza en cuanto el despliegue usa otros nombres--que es exactamente lo
    que pasa en cualquier entorno que no sea la maquina de desarrollo.
    """
    from sqlalchemy.engine import make_url

    u = make_url(url)
    return {
        "host": u.host or "localhost",
        "port": u.port or 5432,
        "user": u.username or "postgres",
        "password": u.password or "",
        "database": u.database or "",
    }


async def _existe(server: dict, base: str) -> bool:
    """Conecta a `postgres`--que siempre existe-- y pregunta por `base`."""
    from sqlalchemy.ext.asyncio import create_async_engine

    admin = create_async_engine(
        f"postgresql+asyncpg://{server['user']}:{server['password']}"
        f"@{server['host']}:{server['port']}/postgres",
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with admin.connect() as conn:
            fila = (
                await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"),
                    {"n": base},
                )
            ).first()
        return fila is not None
    finally:
        await admin.dispose()


async def _crear(server: dict, base: str) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    admin = create_async_engine(
        f"postgresql+asyncpg://{server['user']}:{server['password']}"
        f"@{server['host']}:{server['port']}/postgres",
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with admin.connect() as conn:
            # `IF NOT EXISTS` de CREATE DATABASE es Postgres 15+. Este proyecto
            # asume algo anterior, asi que la comprobacion va aparte y el nombre
            # va entre comillas dobles por si trae mayusculas.
            await conn.execute(text(f'CREATE DATABASE "{base}"'))
        print(f"  base creada: {base}")
    finally:
        await admin.dispose()


async def _borrar(server: dict, base: str) -> None:
    """La borra cortando conexiones abiertas, o no la borra nunca.

    Sin esto, un `DROP DATABASE` falla con "is being accessed by other users" si
    el pool del server de desarrollo quedo con una conexion viva--y el mensaje
    no dice que hay que terminar esas conexiones primero.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    admin = create_async_engine(
        f"postgresql+asyncpg://{server['user']}:{server['password']}"
        f"@{server['host']}:{server['port']}/postgres",
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with admin.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": base},
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{base}"'))
        print(f"  base borrada: {base}")
    finally:
        await admin.dispose()


async def _verificar(url_app: str, url_mig: str, base: str) -> list[str]:
    """Conecta con las dos URLs y devuelve los problemas, no una excepcion.

    Comprueba tres cosas de las que dependen los tests de integracion, y las tres
    fallan de forma distinta:

    1. Que la URL del rol de app conecte.
    2. Que esa conexion vea el esquema--`to_regclass('bookings')`-- y no una base
       vacia. Sin esto, una base creada pero no migrada pasa por valida.
    3. Que el rol de app **no** pueda escribir el esquema, que es la garantia de
       RLS que los tests de tenancy dan por sentado.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    problemas: list[str] = []

    motor = create_async_engine(url_app)
    try:
        async with motor.connect() as conn:
            await conn.execute(text("SELECT 1"))
            existe = (await conn.execute(text("SELECT to_regclass('public.bookings')"))).scalar()
            if existe is None:
                problemas.append(
                    f"el rol de app conecta pero no ve la tabla `bookings`: "
                    f"{base} no quedo migrada."
                )

            ddl = (
                await conn.execute(
                    text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
                )
            ).scalar()
            if ddl:
                problemas.append(
                    "el rol de app tiene CREATE sobre el esquema `public`. "
                    "Deberia ser solo USAGE: los tests de tenancy esperan que "
                    "no pueda crear tablas ni desactivar la RLS."
                )
    except Exception as exc:
        problemas.append(f"el rol de app no puede conectarse: {type(exc).__name__}: {exc}")
    finally:
        await motor.dispose()

    motor_mig = create_async_engine(url_mig)
    try:
        async with motor_mig.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        problemas.append(f"el rol de DDL no puede conectarse: {type(exc).__name__}: {exc}")
    finally:
        await motor_mig.dispose()

    return problemas


def _escribir_env_testing(url_app: str, url_mig: str) -> pathlib.Path | None:
    """Escribe `.env.testing` para que `pytest` funcione sin exportar nada.

    Va al archivo y no solo a pantalla porque exportar variables en PowerShell se
    pierde al cerrar la terminal, y el estado que sobrevive entre corridas es el
    que hace que un `pytest` un dia ejecute 909 tests y al otro 76, sin que nadie
    sepa por que.

    El archivo lleva contrasenas, asi que **no se sube**: `_agregar_a_gitignore`
    lo deja anotado. Un `.env` con claves en el repositorio es de las pocas
    cosas que no tienen arreglo despues.
    """
    destino = pathlib.Path(__file__).resolve().parent / ".env.testing"
    contenido = (
        "# Generado por `preparar_base_test.py`. No editar a mano, no subir.\n"
        "#\n"
        "# `tests/conftest.py` lee este archivo al arrancar y exporta lo que\n"
        "# encuentre, salvo lo que ya este definido en el entorno. Por eso los\n"
        "# tests de integracion no se saltan en silencio: si borra este archivo,\n"
        "# el conftest lo dice con un mensaje que dice que volver a crearlo.\n"
        f"TEST_DATABASE_URL={url_app}\n"
        f"DATABASE_MIGRATION_URL={url_mig}\n"
    )
    destino.write_text(contenido, encoding="utf-8")
    _agregar_a_gitignore(destino.name)
    return destino


def _agregar_a_gitignore(nombre: str) -> None:
    """Anota el archivo en `.gitignore` si todavia no esta."""
    raiz = pathlib.Path(__file__).resolve().parent
    for candidato in (raiz / ".gitignore", raiz.parent / ".gitignore"):
        if not candidato.is_file():
            continue
        texto = candidato.read_text(encoding="utf-8")
        if nombre in texto:
            return
        separador = "" if texto.endswith("\n") else "\n"
        candidato.write_text(
            f"{texto}{separador}{nombre}\n",
            encoding="utf-8",
        )
        print(f"  {nombre} agregado a {candidato.name}")
        return


def _correr_migraciones(url_migracion: str) -> int:
    """Aplica `alembic upgrade head` en un subproceso.

    En subproceso y no en el proceso actual porque Alembic usa `sys.argv` y
    `Config.set_main_option`, y meterlo en medio de pytest deja el estado global
    contaminado para el resto de la suite.
    """
    import subprocess

    env = {**os.environ, "DATABASE_MIGRATION_URL": url_migracion}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:], file=sys.stderr)
    return proc.returncode


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--drop",
        action="store_true",
        help="Borra la base antes de crearla. DESTRUCTIVO.",
    )
    ap.add_argument(
        "--base",
        default=os.environ.get("TEST_DATABASE_NAME", BASE_DEFECTO),
        help=f"Nombre de la base (default: {BASE_DEFECTO})",
    )
    args = ap.parse_args()

    settings = get_settings()

    # **Cada rol toma su propia contrasena de su propia variable.**
    #
    # `tempus_app` y `tempus_owner` no comparten contrasena, y la URL de
    # migraciones--que es de la que salen host, puerto y usuario-- trae la del
    # owner. Reusar esa para el rol de app produce una base de tests migrada
    # correctamente y despues inaccesible: el error aparece como
    # `password authentication failed` en el primer test de integracion, que no
    # dice nada de contrasenas.
    url_app = settings.sqlalchemy_url(for_migrations=False)
    url_mig_dev = settings.sqlalchemy_url(for_migrations=True)

    app = _servidor_de(url_app)
    owner = _servidor_de(url_mig_dev)

    # El nombre de la base es lo unico que cambia: mismo servidor, mismo puerto,
    # mismos roles, mismas contrasenas.
    app["database"] = args.base
    owner["database"] = args.base

    url_test = (
        f"postgresql+asyncpg://{app['user']}:{app['password']}"
        f"@{app['host']}:{app['port']}/{args.base}"
    )
    url_mig = (
        f"postgresql+asyncpg://{owner['user']}:{owner['password']}"
        f"@{owner['host']}:{owner['port']}/{args.base}"
    )

    print(f"Servidor: {owner['host']}:{owner['port']}  Base: {args.base}")
    print(f"  rol de app:    {app['user']}")
    print(f"  rol de DDL:    {owner['user']}")

    # Para crear y borrar la base hay que poder conectarse a `postgres`, y ahi
    # rigen las credenciales del owner: es el rol con permisos de DDL.
    if args.drop:
        await _borrar(owner, args.base)
        print("  (--drop)")

    if await _existe(owner, args.base):
        print(f"  la base ya existe: {args.base}")
    else:
        await _crear(owner, args.base)

    print("\nAplicando migraciones...")
    codigo = _correr_migraciones(url_mig)
    if codigo != 0:
        print("\nLas migraciones fallaron. Revisar arriba.", file=sys.stderr)
        return codigo
    print("  migraciones al dia")

    # **Comprobar que las dos URLs funcionan antes de imprimirlas.**
    #
    # Sin esto, el script miente: dice "Listo" y entrega una URL que no conecta.
    # El sintoma aparece un comando despues, en el primer test de integracion, y
    # como `password authentication failed`--que no dice "la contrasena del script
    # estaba mal" sino "el rol app no puede entrar", que es otra cosa. Verificar
    # aca cuesta un segundo y convierte un fallo difuso en uno obvio.
    problemas = await _verificar(url_test, url_mig, args.base)
    if problemas:
        print("\n" + "=" * 78)
        print("La base se migro pero NO sirve para los tests:\n")
        for p in problemas:
            print(f"  - {p}")
        return 1

    escrito = _escribir_env_testing(url_test, url_mig)

    print("\n" + "=" * 78)
    print("Listo. Las dos URLs conectan y la base esta migrada.")
    print()
    if escrito:
        print(f"Escrito {escrito}")
        print("A partir de ahora `pytest` alcanza los tests de integracion, de")
        print("concurrencia y de tenancy sin exportar nada: `tests/conftest.py`")
        print("carga ese archivo al arrancar.")
        print()
    print("Si preferis las variables en el shell:\n")
    print(f"  $env:TEST_DATABASE_URL = '{url_test}'")
    print(f"  $env:DATABASE_MIGRATION_URL = '{url_mig}'")
    print()
    print("El archivo no se sube al control de versiones: esta en `.gitignore`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
