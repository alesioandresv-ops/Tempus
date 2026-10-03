"""Entorno de Alembic.

Tres decisiones que importan, y una trampa que ya costo una migracion entera.

1. **La URL sale de la configuracion de la app**, de `DATABASE_MIGRATION_URL`, no
   de `alembic.ini`. La migracion necesita el rol con DDL; la app necesita el rol
   sin DDL. Si compartieran la URL, bastaria un error de despliegue para que la
   aplicacion empiece a correr con permiso de modificar el schema.

2. **La RLS se apaga solo para comparar, y solo si el comando es autogenerate.**
   §7 exige `FORCE ROW LEVEL SECURITY`, y `FORCE` somete tambien al dueno de la
   tabla. El rol de migraciones ES el dueno, asi que al comparar el schema no ve
   sus propias filas y el autogenere propone cambios que no existen.

   La lista de tablas se captura **antes** de apagarlas y se usa para reencenderlas.
   Volver a leerla de `pg_class` en cada sentido rompe el ciclo y deja la base sin
   RLS de forma permanente; el detalle esta en `_set_rls`. Vale la pena dejarlo
   escrito arriba porque el sintoma (todo se ve bien en el catalogo, y el
   aislamiento no existe) no lleva a ningun lado.

3. **Nada se ejecuta en la conexion antes de `context.configure()`.** Esta es la
   trampa. En SQLAlchemy 2.0, ejecutar cualquier sentencia en la conexion dispara
   un "autobegin": la transaccion queda abierta antes de que Alembic la vea. Cuando
   eso pasa, `context.begin_transaction()` se anida en una transaccion que Alembic
   no controla, y al salir del `with engine.connect()` de abajo SQLAlchemy hace
   **rollback** de todo. El sintoma es el peor posible: Alembic informa
   "Running upgrade" para cada migracion, no da ningun error, y la base sigue
   vacia. Se diagnostico como si el problema fuera de permisos.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.base import Base

# Los modelos se importan para que queden registrados en `Base.metadata`. Sin
# esto, `target_metadata` esta vacio y el autogenere propone borrar todo.
#
# **Importar el paquete no alcanza**: `app/models/__init__.py` reexporta de forma
# perezosa para no construirse a medias, y `import app.models` no importa ni un
# solo dominio. Con la metadata vacia, `alembic check` informaba `remove_table`
# para las 27 tablas y `test_el_modelo_coincide_con_la_base` fallaba sin que
# hubiera ningun cambio real en el esquema. `registrar_modelos()` es la llamada
# explicita que arregla eso.
from app.models import registrar_modelos

registrar_modelos()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """URL del rol con DDL, con el SSL de la configuracion aplicado."""
    settings = get_settings()
    if not settings.database_migration_url:
        raise RuntimeError(
            "DATABASE_MIGRATION_URL no esta definido. Las migraciones necesitan el rol "
            "con permisos de DDL, que no es el mismo que usa la aplicacion."
        )
    return settings.sqlalchemy_url(for_migrations=True)


def _is_autogenerate() -> bool:
    """`True` si el comando actual es `revision --autogenerate`."""
    opts = getattr(context.config, "cmd_opts", None)
    return bool(opts is not None and "autogenerate" in opts)


def _tables_with_rls(connection: Connection) -> list[str]:
    """Nombres de las tablas que tienen RLS activa."""
    rows = connection.execute(
        text(
            "SELECT c.relname "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity"
        )
    ).scalars()
    return list(rows)


def _set_rls(connection: Connection, tables: list[str], enabled: bool) -> None:
    """Apaga o prende la RLS de una lista **dada** de tablas.

     Se usa `ALTER TABLE ... DISABLE/ENABLE ROW LEVEL SECURITY` y no
     `SET session_replication_role = replica` porque lo segundo es `SUSET`: solo un
     superusuario puede cambiarlo, y darle superusuario al rol de migraciones
     anularia por completo la separacion de permisos de §7. Lo primero lo puede
     hacer cualquiera que sea dueno de la tabla, que es el caso.

     La lista se recibe como parametro y **no se vuelve a leer de `pg_class`**. Es
     lo unico que hace que esto sea seguro: leerla cada vez rompe el ciclo, porque
     despues de apagar, ninguna tabla tiene `relrowsecurity`, el SELECT devuelve
     vacio, y el reencendido no reencendia nada.

    Ese bug era real y grave: un unico `alembic revision --autogenerate` dejaba la
     base de datos **sin RLS de forma permanente**, con `FORCE` intacto y las
     20 politicas en su sitio. El aspecto era perfecto y el aislamiento entre
     tenants no existia. Se manifesto como tests de tenancy que de pronto veian las
     filas de los dos tenants, y la primera hipotesis fue el seed.
    """
    verb = "ENABLE" if enabled else "DISABLE"
    for table in tables:
        connection.execute(text(f'ALTER TABLE "{table}" {verb} ROW LEVEL SECURITY'))


def run_migrations_offline() -> None:
    """Genera el SQL sin conectarse. Para revisar que hace una migracion."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Configura Alembic y corre las migraciones.

    Notese que no hay ninguna sentencia antes de `context.configure()`. Ver la
    nota 3 del docstring del modulo.
    """
    is_autogenerate = _is_autogenerate()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # `compare_type` es lo que hace que el autogenere detecte un cambio de
        # `text` a `citext` o de `varchar` a `numeric`. Sin esto, el diff miente.
        compare_type=True,
        compare_server_default=True,
        # Sin esto, Alembic propone restrict names con sufijo numerico y el diff
        # es ilegible.
        include_schemas=False,
    )
    with context.begin_transaction():
        # A partir de aca la transaccion es de Alembic y el commit es suyo.
        connection.execute(text("SET LOCAL lock_timeout = '10s'"))
        if not is_autogenerate:
            context.run_migrations()
            return

        connection.execute(text("SET LOCAL statement_timeout = '120s'"))
        # La lista se captura **antes** de apagar y se usa para el reencendido. Ver
        # la nota de `_set_rls`.
        affected = _tables_with_rls(connection)
        _set_rls(connection, affected, enabled=False)
        try:
            context.run_migrations()
        finally:
            _set_rls(connection, affected, enabled=True)


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = get_url()
    engine = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
            # Red de seguridad, no el mecanismo principal. Alembic ya cerro su
            # transaccion con commit; este commit no hace nada si no quedo nada
            # pendiente, y salva el caso en que una sentencia ejecutada por el
            # hook de post_write quede fuera de la transaccion de Alembic.
            await connection.commit()
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
