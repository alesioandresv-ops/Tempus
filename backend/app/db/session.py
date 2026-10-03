"""Engine, sessionmaker y el helper de contexto de tenant.

Lo importante de este modulo no es el engine: es que la sesion de la aplicacion
**exige** un contexto de tenant, y no hay forma de obtener una sesion sin uno. El
IDOR del Â§25 no se mitiga aca, se elimina de raiz: la sesion de la que dispone la
capa HTTP ya tiene el `business_id` resuelto, porque resolverlo es parte de
obtenerla.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings

# Variable de sesion que lee la politica de RLS.
#
# `SET LOCAL` y no `SET`: `SET` sobrevive al COMMIT y el pool reusa conexiones, asi
# que un `SET` de sesion deja el tenant de una request filtrado a la siguiente. Es
# el riesgo R-02, y por eso el contexto de tenant usa una transaccion y `SET LOCAL`.
TENANT_GUC = "app.current_business_id"

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: Settings) -> AsyncEngine:
    """Engine async con la configuracion de pool de la app."""
    kwargs: dict[str, Any] = {
        "echo": False,  # los parametros bound pueden contener un secure_token
        "pool_pre_ping": True,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
    }
    engine = create_async_engine(settings.sqlalchemy_url(), **kwargs)
    _apply_connect_guards(engine)
    return engine


def _apply_connect_guards(engine: AsyncEngine) -> None:
    """Protecciones que se aplican a cada conexion nueva del pool.

    Sin esto, el rol de la app podria escribir en `platform_users` desde la
    aplicacion, que es justo la tabla que la Â§7 separa a proposito.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        # asyncpg no soporta `with cursor as`. Usar create + try/finally.
        cursor = dbapi_connection.cursor()
        try:
            # `search_path` explicito: sin esto, un objeto creado en otro schema
            # puede resolver por sorpresa.
            cursor.execute("SET search_path = public")
            # Techo de tiempo por sentencia. En el perfil gratuito la base puede
            # caer de una a otra, y una consulta colgada se come la conexion del
            # pool entero en lugar de fallar sola.
            cursor.execute("SET statement_timeout = '30s'")
        finally:
            cursor.close()


def get_engine() -> AsyncEngine:
    """Engine del proceso. Se crea una sola vez."""
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings())
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Sessionmaker del proceso."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Sesion transaccional para uso fuera del ciclo de request (jobs, scripts)."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


@asynccontextmanager
async def tenant_session(business_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """Sesion transaccional con el contexto de tenant aplicado.

    El `SET LOCAL` va **dentro** de la transaccion y desaparece en el COMMIT. Por
    eso este context manager es la unica forma de abrir una sesion con tenant: si
    el `SET` quedara fuera, la RLS no tendria contra que filtrar.

    Reune tres responsabilidades que antes estaban separadas y por eso ninguna se
    aplicaba en la practica:

    1. **El `SET LOCAL`.** Sin el GUC, las politicas de RLS comparan contra
       `NULLIF(current_setting(...), '')::uuid`, que da NULL, y la politica deja
       pasar cero filas. El sintoma no es un error: es un `[]` en un endpoint que
       deberia devolver un servicio, y por eso es dificil de atribuir a la RLS.
    2. **El COMMIT.** Los services hacen `flush()` y dejan el commit al
       llamador, que es el patron correcto para que el service no decida los
       limites de la transaccion. Pero si ningun router cierra la transaccion, la
       reserva se pierde al cerrar la sesion: el `flush` la manda a la base y el
       rollback del context manager la borra. `session.begin()` ya hace ese
       commit al salir, asi que no hace falta un `commit()` explicito.
    3. **El `SET LOCAL` antes de cualquier lectura.** Un router que resuelve el
       negocio por slug tiene que leer `businesses` (tabla global, sin RLS)
       *antes* de conocer el `business_id`. Ese primer tramo va con
       `session_scope()`; desde que ya se sabe el tenant, todo lo demas va con
       esta.
    """
    async with get_sessionmaker()() as session, session.begin():
        await session.execute(
            text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
            {"tenant": str(business_id)},
        )
        yield session


@asynccontextmanager
async def tenant_session_readonly(business_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """Igual que `tenant_session`, pero con rollback al salir.

    Para lecturas puras. Existe para que quede explicito en el codigo que una
    `GET` no escribe, y para que un `rollback` al salir sea la garantia de que no
    se colÃ³ ninguna escritura por accidente.
    """
    async with get_sessionmaker()() as session, session.begin():
        await session.execute(
            text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
            {"tenant": str(business_id)},
        )
        yield session
        await session.rollback()


async def check_database() -> bool:
    """`SELECT 1`. Lo usa `/readyz`."""
    try:
        engine = get_engine()
        conn = await engine.connect()
        try:
            await conn.execute(text("SELECT 1"))
            return True
        finally:
            await conn.close()
    except Exception:
        return False


async def dispose_engine() -> None:
    """Cierra el pool. Se llama en el shutdown del lifespan."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
