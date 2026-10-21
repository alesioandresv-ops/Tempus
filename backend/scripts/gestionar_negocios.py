"""Lista y borra negocios (con sus usuarios) de la base que apuntan las env vars.

Nacio de una necesidad concreta: en produccion un negocio existia en el panel pero
su pagina publica daba 404, y no habia forma de mirar la fila real--`status`, `plan`,
`created_at`--sin abrir la consola de SQL del proveedor. Este script muestra eso, y
ademas permite borrar para volver a registrar desde cero.

**Usa el rol de migraciones (`DATABASE_MIGRATION_URL`) por defecto.** No es capricho:
`businesses` esta bajo RLS forzada y `tempus_app` no puede ni insertar ni borrar ahi.
El rol de la app serviria solo para listar.

**El borrado desactiva la RLS de forma explicita y acotada.** No existe ninguna
politica `FOR DELETE` sobre `businesses` (solo SELECT, UPDATE e INSERT), asi que un
`DELETE` es un no-op silencioso incluso para el owner: borraria cero filas y diria
que salio bien. Por eso el script hace `ALTER TABLE ... DISABLE ROW LEVEL SECURITY`
dentro de la misma transaccion que el borrado, y la vuelve a habilitar antes de
commitear. Es la unica forma de que borrar de verdad y de que el conteo diga la
verdad.

Ejemplos:

    # que hay (la salida por defecto; no toca nada)
    python scripts/gestionar_negocios.py

    # por que un negocio no se publica
    python scripts/gestionar_negocios.py --slug barber-the-peluche

    # borrar un negocio y sus usuarios (pide confirmacion)
    python scripts/gestionar_negocios.py --borrar barber-the-peluche

    # borrar sin preguntar (para automatizar)
    python scripts/gestionar_negocios.py --borrar barber-the-peluche --si

Contra produccion, sin exportar nada a mano:

    DATABASE_MIGRATION_URL="postgresql+asyncpg://..." python scripts/gestionar_negocios.py

El negocio arrastra sus `business_users` por `ON DELETE CASCADE` del FK, pero el
script los cuenta y los informa antes de borrar: si un dia el cascade cambia, el
numero que muestra deja de coincidir y se nota.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

#: Se listan todos los negocios, pero la tabla se corta para no inundar la terminal
#: con cientos de filas. `--todos` levanta el limite.
LIMITE_POR_DEFECTO = 50


def _url_de_la_base() -> str | None:
    """La URL de migraciones, con `DATABASE_URL` como ultimo recurso.

    Prefiere migraciones porque es el rol que puede hacer todo lo que el script
    necesita; con el de la app, borrar no funcionaria y el error seria confuso.

    Cae a `get_settings()` para tomar el `backend/.env`--pydantic-settings lo carga
    solo--y que el script corra sin exportar nada a mano. `os.environ` tiene
    prioridad porque es lo que permite apuntar a produccion en la misma linea:

        DATABASE_MIGRATION_URL="..." python scripts/gestionar_negocios.py
    """
    de_entorno = os.environ.get("DATABASE_MIGRATION_URL")
    if de_entorno:
        return de_entorno

    from app.core.config import get_settings

    ajustes = get_settings()
    url = ajustes.sqlalchemy_url(for_migrations=True)
    if url:
        return url

    return os.environ.get("DATABASE_URL") or ajustes.sqlalchemy_url(for_migrations=False)


async def _listar(conn, *, slug: str | None, todos: bool) -> None:
    """Imprime los negocios y, si se pidio uno, por que se publica o no."""
    filtro = "WHERE b.slug = :slug" if slug else ""
    limite = "" if todos else f"LIMIT {LIMITE_POR_DEFECTO}"

    filas = (
        await conn.execute(
            text(
                f"""
                SELECT
                    b.slug,
                    b.name,
                    b.status,
                    b.plan,
                    b.created_at,
                    b.created_at + INTERVAL '30 days' AS fin_trial,
                    (SELECT count(*) FROM business_users u
                      WHERE u.business_id = b.id) AS usuarios
                FROM businesses b
                {filtro}
                ORDER BY b.created_at DESC
                {limite}
                """
            ),
            {"slug": slug} if slug else {},
        )
    ).all()

    if not filas:
        if slug:
            print(f"  no existe ningun negocio con slug '{slug}'.")
        else:
            print("  no hay negocios en esta base.")
        return

    print(f"  {'slug':<28} {'status':<10} {'plan':<6} {'usuarios':<9} creado")
    print(f"  {'-' * 28} {'-' * 10} {'-' * 6} {'-' * 9} {'-' * 19}")
    for f in filas:
        print(
            f"  {f.slug:<28} {f.status:<10} {f.plan:<6} {f.usuarios:<9} "
            f"{f.created_at:%Y-%m-%d %H:%M}"
        )

    if not todos and not slug:
        print(f"\n  (primeros {LIMITE_POR_DEFECTO}; usar --todos para ver todo)")


async def _ver_slug(conn, slug: str) -> None:
    """Explica si el negocio se publica, que es lo que suele estar en duda.

    Reproduce la regla de `publicacion.es_publicable` en SQL en vez de importarla:
    el script tiene que poder correr contra produccion aunque el codigo local sea
    de otra version, y la regla se lee mejor al lado de la fila que la explica.
    """
    fila = (
        await conn.execute(
            text(
                """
                SELECT b.id, b.slug, b.name, b.status, b.plan, b.created_at,
                       (b.status = 'active') AS es_active,
                       (b.plan = 'pro') AS es_pro,
                       (now() < b.created_at + INTERVAL '30 days') AS dentro_del_mes
                FROM businesses b
                WHERE b.slug = :slug
                """
            ),
            {"slug": slug},
        )
    ).one_or_none()

    if fila is None:
        print(f"\n  '{slug}' no existe en esta base. El link esta mal o es de otra base.")
        print("  Si el panel lo muestra, estas apuntando a una base distinta del panel.")
        return

    publicable = fila.es_active and (fila.es_pro or fila.dentro_del_mes)

    print(f"\n  negocio: {fila.name} ({fila.slug})")
    print(f"    status      : {fila.status}   {'OK' if fila.es_active else 'NO es active'}")
    print(f"    plan        : {fila.plan}   {'(Pro: no vence)' if fila.es_pro else ''}")
    print(f"    creado      : {fila.created_at:%Y-%m-%d %H:%M}")
    print(f"    mes gratis  : {'vigente' if fila.dentro_del_mes else 'VENCIDO'}")
    print(f"\n    pagina publica: {'SE PUBLICA' if publicable else 'NO SE PUBLICA (404)'}")

    if not publicable:
        print("\n    Para publicarlo alcanza con UNA de estas:")
        if not fila.es_active:
            print("      - status = 'active' (el default 'trial' no se publica)")
        if not fila.es_pro and not fila.dentro_del_mes:
            print("      - plan = 'pro', o un mes gratis vigente")
            print("        (si el trial venció: subir el plan, o poner un TRIAL_GRACE_FROM")
            print("         reciente en el backend y redeployar)")


async def _usuarios_de(conn, business_id) -> list:
    """Los miembros del panel del negocio. Se listan antes de borrar para que el
    borrado no sea a ciegas."""
    return (
        await conn.execute(
            text(
                """
                SELECT email, role, status, created_at
                FROM business_users
                WHERE business_id = :bid
                ORDER BY created_at
                """
            ),
            {"bid": business_id},
        )
    ).all()


async def _borrar(conn, slug: str, *, sin_confirmar: bool) -> int:
    """Borra el negocio y devuelve cuantos borro de verdad (0 o 1)."""
    fila = (
        await conn.execute(
            text("SELECT id, name FROM businesses WHERE slug = :slug"),
            {"slug": slug},
        )
    ).one_or_none()

    if fila is None:
        print(f"\n  no existe '{slug}': no hay nada que borrar.")
        return 0

    usuarios = await _usuarios_de(conn, fila.id)
    print(f"\n  a borrar: {fila.name} ({slug})")
    print(f"    id       : {fila.id}")
    print(f"    usuarios : {len(usuarios)}")
    for u in usuarios:
        print(f"      - {u.email}  ({u.role}, {u.status})")

    if not sin_confirmar:
        print("\n  Esto no se puede deshacer.")
        respuesta = input("  Escribi 'borrar' para confirmar: ").strip().lower()
        if respuesta != "borrar":
            print("  cancelado. No se toco nada.")
            return 0

    # Sin politica FOR DELETE sobre `businesses`, con la RLS activa el DELETE afecta
    # cero filas y no falla: diria que borro y no borro. Se apaga dentro de esta
    # transaccion y se vuelve a encender antes del commit, asi el estado de la tabla
    # no queda cambiado si algo sale mal en el medio.
    await conn.execute(text("ALTER TABLE businesses DISABLE ROW LEVEL SECURITY"))
    try:
        resultado = await conn.execute(
            text("DELETE FROM businesses WHERE id = :bid"), {"bid": fila.id}
        )
        # El FK de business_users es ON DELETE CASCADE, pero se borran igual por las
        # dudas: si la migracion cambia el cascade, esto sigue dejando la base limpia
        # en vez de fallar con un FK violation.
        await conn.execute(
            text("DELETE FROM business_users WHERE business_id = :bid"), {"bid": fila.id}
        )
    finally:
        await conn.execute(text("ALTER TABLE businesses ENABLE ROW LEVEL SECURITY"))
        await conn.execute(text("ALTER TABLE businesses FORCE ROW LEVEL SECURITY"))

    borrados = resultado.rowcount or 0
    print(f"\n  borrados: {borrados} negocio(s).")
    if borrados == 0:
        # Si esto pasa, la RLS no se pudo apagar (otro rol, otra version del esquema).
        print("  OJO: se borraron 0 filas. La RLS no se pudo desactivar con este rol.")
        print("  Verificar que la URL sea la de migraciones (DATABASE_MIGRATION_URL).")
    return borrados


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--slug", help="mirar un negocio puntual y explicar si se publica")
    parser.add_argument("--borrar", metavar="SLUG", help="borrar el negocio con ese slug")
    parser.add_argument(
        "--si",
        action="store_true",
        help="no pedir confirmacion al borrar (pensado para automatizar)",
    )
    parser.add_argument("--todos", action="store_true", help="listar sin limite de filas")
    args = parser.parse_args()

    url = _url_de_la_base()
    if not url:
        print("Falta DATABASE_MIGRATION_URL (o DATABASE_URL).")
        print("Se puede pasar en la misma linea, sin exportar:")
        print('  DATABASE_MIGRATION_URL="..." python scripts/gestionar_negocios.py')
        return 2

    motor = create_async_engine(url)
    try:
        async with motor.begin() as conn:
            db = (await conn.execute(text("select current_database()"))).scalar_one()
            rol = (await conn.execute(text("select current_user"))).scalar_one()
            print(f"base: {db}   rol: {rol}\n")

            if args.borrar:
                await _borrar(conn, args.borrar, sin_confirmar=args.si)
                return 0

            await _listar(conn, slug=args.slug, todos=args.todos)
            if args.slug:
                await _ver_slug(conn, args.slug)
    finally:
        await motor.dispose()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
