"""Verifica `rate_limit_hit` contra una base real, sin pasar por el servidor.

Por que existe: la migracion `0013` cambio la funcion, y la version que quedo
instalada en la base de desarrollo estaba a medio camino--tenia el cubo alineado
pero el `coalesce` que arreglaba un bug latente de `0004` todavia no. Un test de
pytest que corre contra `tempus_test` no dice nada sobre `tempus`, que es la base
que usa el servidor de verdad. Esto si.

Los tres comportamientos que se comprueban:

1. **Acumula.** Seis llamadas con limite 5 tienen que dar `allowed` cinco veces y
   `False` la sexta. Antes de `0013` el cubo se reiniciaba cada segundo, asi que
   seis llamadas seguidas nunca llegaban al limite: `rate_limit_login_per_ip = 5`
   era en la practica "5 por segundo", que no limita nada.

2. **`retry_after` no es un numero de conveniencia.** Tiene que decir el tiempo
   que falta para que se libere el intento mas viejo, no un valor inventado.

3. **No se rompe cuando el cubo se vacia.** Este es el bug que se manifesto en el
   servidor: `0004` guardaba el `hits` vacio como `'{}'::jsonb`, y en JSON `{}`
   es un objeto, no un array, asi que `jsonb_array_length` revienta con
   "no se puede obtener el largo de array de un no-array" y la transaccion queda
   abortada. Con ventana de 1 segundo y una espera se llega a ese estado; con
   ventana de 60 no se llega nunca, que es exactamente por lo que estaba latente.

Usa claves propias con prefijo `verificacion_rl_` y las borra al final, para no
mezclarse con las celdas reales.
"""

import asyncio
import os
import sys
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

PREFIJO = "verificacion_rl_"
fallos: list[str] = []


def comprobar(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {nombre}{f' -- {detalle}' if detalle else ''}")
    if not ok:
        fallos.append(nombre)


async def main() -> int:
    url = os.environ.get("DATABASE_MIGRATION_URL") or os.environ.get("DATABASE_URL", "")
    if not url:
        print("Falta DATABASE_MIGRATION_URL / DATABASE_URL")
        return 2
    motor = create_async_engine(url, poolclass=None)
    try:
        async with motor.connect() as conn:
            base = await conn.execute(text("SELECT current_database()"))
            version = await conn.execute(text("SELECT version_num FROM alembic_version"))
            print(f"Base: {base.scalar_one()}   migracion: {version.scalar_one()}\n")

        # --- 1. Acumula de verdad ------------------------------------------
        print("1. Seis llamadas con limite 5: la sexta tiene que negarse")
        clave = f"{PREFIJO}{uuid.uuid4().hex[:12]}"
        vistos = []
        for i in range(6):
            # `motor.begin()`, no `motor.connect()`: en SQLAlchemy 2.0 la conexion
            # suelta hace rollback de lo que se hizo en ella, asi que cada intento
            # se borraba solo y la ventana nunca llegaba a llenarse. Con el patron
            # de "commit as you go" hay que commitear explicitamente.
            async with motor.begin() as conn:
                fila = (
                    await conn.execute(
                        text(
                            "SELECT allowed, hits, retry_after FROM rate_limit_hit("
                            ":clave, 5, 60, 'verificacion')"
                        ),
                        {"clave": clave},
                    )
                ).one()
            vistos.append((bool(fila[0]), fila[1], fila[2]))
            print(f"  llamada {i + 1}: allowed={fila[0]} hits={fila[1]} retry_after={fila[2]}")

        comprobar("las primeras 5 pasan", all(v[0] for v in vistos[:5]), f"vistos={vistos}")
        comprobar("la sexta se niega", vistos[5][0] is False, f"vistos={vistos}")
        comprobar(
            "hits crece monotonamente",
            [v[1] for v in vistos] == [1, 2, 3, 4, 5, 6],
            f"hits={[v[1] for v in vistos]}",
        )

        # Un cubo solo para toda la ventana: si el alineado fallara, habria varios.
        # Ojo: la funcion guarda `p_key` tal cual--el hasheo lo hace Python antes de
        # llamar-- asi que aqui se busca la clave en crudo, no su digest.
        async with motor.connect() as conn:
            cubos = (
                await conn.execute(
                    text("SELECT count(*) FROM rate_limit_buckets WHERE key = :c"),
                    {"c": clave},
                )
            ).scalar_one()
        comprobar("la ventana de 60 s vive en un solo cubo", cubos == 1, f"cubos={cubos}")

        # --- 2. retry_after -----------------------------------------------
        print("\n2. retry_after cuenta el tiempo que falta, no algo inventado")
        comprobar(
            "la sexta dice que hay que esperar lo que queda de la ventana",
            vistos[5][2] == 60,
            f"retry_after={vistos[5][2]}",
        )

        # --- 3. El camino que reventaba ------------------------------------
        print("\n3. El cubo que se vacia del todo no debe reventar")
        clave_corta = f"{PREFIJO}corta{uuid.uuid4().hex[:8]}"
        async with motor.begin() as conn:
            await conn.execute(
                text("SELECT allowed FROM rate_limit_hit(:c, 1, 1, 'verificacion')"),
                {"c": clave_corta},
            )
        print("  esperando 2,2 s para que el unico hit salga de la ventana...")
        await asyncio.sleep(2.2)
        try:
            async with motor.begin() as conn:
                fila = (
                    await conn.execute(
                        text(
                            "SELECT allowed, hits, retry_after "
                            "FROM rate_limit_hit(:c, 1, 1, 'verificacion')"
                        ),
                        {"c": clave_corta},
                    )
                ).one()
            comprobar(
                "la llamada despues de vaciarse no da error",
                True,
                f"allowed={fila[0]} hits={fila[1]}",
            )
            comprobar(
                "y vuelve a permitir, porque la ventana ya corrio",
                bool(fila[0]) is True,
                f"allowed={fila[0]}",
            )
            comprobar("hits arranca de nuevo en 1", fila[1] == 1, f"hits={fila[1]}")
        except Exception as exc:
            comprobar(
                "la llamada despues de vaciarse no da error",
                False,
                f"{type(exc).__name__}: {str(exc).splitlines()[0]}",
            )

        # Y el cubo viejo no debe quedar dando vueltas.
        async with motor.connect() as conn:
            sobras = (
                await conn.execute(
                    text("SELECT count(*) FROM rate_limit_buckets WHERE key = :c"),
                    {"c": clave_corta},
                )
            ).scalar_one()
        comprobar(
            "el cubo viejo se borro cuando ya no le quedaba ningun hit",
            sobras <= 1,
            f"cubos={sobras}",
        )

        # --- limpieza -------------------------------------------------------
        async with motor.begin() as conn:
            await conn.execute(
                text("DELETE FROM rate_limit_buckets WHERE key LIKE 'verificacion\\_rl\\_%'")
            )
    finally:
        await motor.dispose()

    print("\n" + "=" * 70)
    if fallos:
        print(f"{len(fallos)} FALLARON:")
        for f in fallos:
            print(f"  - {f}")
        return 1
    print("La ventana deslizante se comporta como debe en esta base.")
    return 0


sys.exit(asyncio.run(main()))
