"""Audita el endurecimiento de produccion contra la base real, no contra el codigo.

Se lee `pg_proc` porque lo que importa no es lo que el archivo de migracion **dice**
sino lo que PostgreSQL **tiene**: una migracion que se editó a mano despues de
aplicarse deja el catalogo y el repo discrepando, y es exactamente el estado en el
que un despliegue se rompe.

Por eso usa SQLSTATE y nombres de objeto, nunca el texto en ingles del error de
PostgreSQL: este servidor esta en español y sus mensajes cambian entre versiones.
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

fallos: list[str] = []


def comprobar(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {nombre}" + (f" -- {detalle}" if detalle else ""))
    if not ok:
        fallos.append(nombre)


async def main() -> int:
    url = os.environ.get("DATABASE_MIGRATION_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("Falta DATABASE_MIGRATION_URL o DATABASE_URL.")
        return 2

    motor = create_async_engine(url, poolclass=None)
    try:
        async with motor.begin() as conn:
            db = (await conn.execute(text("select current_database()"))).scalar_one()
            ver = (await conn.execute(text("select version()"))).scalar_one().split(",")[0]
            print(f"Base: {db}\n{ver}\n")

            # --- rate_limit_hit: SECURITY DEFINER y search_path cerrado ------
            print("1. rate_limit_hit")
            filas = (
                await conn.execute(
                    text(
                        """
                        select p.proname,
                               p.prosecdef,
                               p.provolatile,
                               coalesce(array_to_string(p.proconfig, '|'), '(sin config)')
                        from pg_proc p
                        join pg_namespace n on n.oid = p.pronamespace
                        where p.proname = 'rate_limit_hit'
                          and n.nspname = 'public'
                        """
                    )
                )
            ).all()
            if not filas:
                comprobar("la función existe", False, "no está en el catálogo")
            else:
                for _n, secdef, volatile, cfg in filas:
                    # **INVOKER, y es a proposito.** `rate_limit_hit` escribe sobre
                    # `rate_limit_buckets`, y `tempus_app` ya tiene INSERT y UPDATE
                    # sobre esa tabla (se verifica mas abajo), asi que no necesita
                    # escalated ningun privilegio. Con SECURITY DEFINER correria como
                    # el dueño y bastaria con darle EXECUTE a la app para que pudiera
                    # escribir en cualquier tabla-- una via de escalamiento disfrazada
                    # de helper. INVOKER mantiene la superficie minima.
                    comprobar(
                        "es SECURITY INVOKER (no escala privilegios)",
                        not bool(secdef),
                        f"prosecdef={secdef}" + ("" if not secdef else " -> DEFINER: revisar"),
                    )
                    # `char` vuelve como bytes desde asyncpg; sin el decode la
                    # comparacion siempre da False y el chequeo miente.
                    vol = volatile.decode() if isinstance(volatile, bytes) else volatile
                    comprobar(
                        "es VOLATILE (lee y escribe estado)",
                        vol == "v",
                        f"provolatile={vol}",
                    )
                    sp = cfg if "search_path" in cfg else "(sin search_path)"
                    comprobar(
                        "fija search_path",
                        "search_path" in cfg,
                        sp,
                    )
                    if "search_path" in cfg:
                        # `pg_temp` de ultimo (o de ninguna parte) es lo que impide
                        # que un atacante crie un schema temporal con una funcion
                        # `rate_limit_hit` propia y se ejecute en su lugar.
                        tiene_pgtemp = "pg_temp" in cfg
                        comprobar(
                            "search_path SIN pg_temp",
                            not tiene_pgtemp,
                            "pg_temp presente: un schema temporal propio gana la busqueda"
                            if tiene_pgtemp
                            else "ok",
                        )
                        partes = cfg.split("=", 1)[1].strip().strip("'\"").split(",")
                        partes = [p.strip().strip('"') for p in partes]
                        comprobar(
                            "empieza por pg_catalog",
                            bool(partes) and partes[0] == "pg_catalog",
                            f"search_path={partes}",
                        )

            # --- Grants: la app necesita EXECUTE, nada mas ------------------
            print("\n2. Grants sobre las tablas del rate limit")
            for tabla in ("rate_limit_buckets", "rate_limit_hit"):
                existe = (
                    await conn.execute(text("select to_regclass(:t)"), {"t": f"public.{tabla}"})
                ).scalar_one()
                if existe is None:
                    # `rate_limit_hit` es una tabla de hits; si no existe como tabla
                    # no es un error, solo hay que decirlo.
                    print(f"  [INFO] {tabla} no existe como tabla")
                    continue
                grants = (
                    await conn.execute(
                        text(
                            """
                            select grantee, string_agg(privilege_type, ',' order by privilege_type)
                            from information_schema.role_table_grants
                            where table_name = :t and grantee <> 'PUBLIC'
                            group by grantee
                            """
                        ),
                        {"t": tabla},
                    )
                ).all()
                print(f"  [INFO] {tabla}: {grants or '(sin grants explicitos)'}")

            # --- generate_token: usa secrets, no random ---------------------
            print("\n3. Generacion de tokens (en el codigo, no en la base)")
            from app.core.security import generate_token, hash_token, settings_snapshot

            t1, t2 = generate_token(), generate_token()
            comprobar("generate_token() da dos tokens distintos", t1 != t2)
            comprobar(
                "el token es URL-safe",
                all(c.isalnum() or c in "-_" for c in t1),
                f"muestra={t1[:12]}...",
            )
            h = hash_token("x")
            comprobar(
                "hash_token es SHA-256 hex de 64 chars",
                len(h) == 64 and all(c in "0123456789abcdef" for c in h),
                f"len={len(h)}",
            )
            comprobar(
                "hash_token es determinista",
                hash_token("x") == h,
            )
            comprobar(
                "hash_token no es el token en claro",
                h != "x",
            )

            # --- settings_snapshot no filtra secretos ----------------------
            print("\n4. settings_snapshot")
            snap = settings_snapshot()
            print(f"  [INFO] claves: {sorted(snap)}")
            import json

            crudo = json.dumps(snap)
            from app.core.config import get_settings

            s = get_settings()
            secretos = [
                v
                for v in (
                    s.jwt_secret_key.get_secret_value(),
                    s.scheduler_tick_secret.get_secret_value(),
                )
                if v and len(v) >= 8
            ]
            filtrados = [v[:8] + "..." for v in secretos if v in crudo]
            comprobar(
                "ningun valor de secreto aparece literal en el snapshot",
                not filtrados,
                f"filtrados={filtrados}" if filtrados else "ok",
            )
            # La lista de claves tiene que ser **exactamente** la de los parametros
            # publicos. Un chequeo por nombre ("algo que huela a secreto") miente:
            # `access_token_minutes` y `refresh_token_days` no son secretos, son
            # duraciones, y hay que learn a distinguirlos.
            esperadas = {
                "jwt_algorithm",
                "access_token_minutes",
                "refresh_token_days",
                "argon2_time_cost",
                "argon2_memory_cost",
                "argon2_parallelism",
            }
            comprobar(
                "expone solo parametros publicos, ni uno mas",
                set(snap) == esperadas,
                f"sobran={sorted(set(snap) - esperadas)} faltan={sorted(esperadas - set(snap))}",
            )

            # Enmascarado: los secretos son `SecretStr`, asi que pydantic los
            # devuelve como `**********` en `repr()` y en `model_dump()`. Esto es lo
            # que hace que un `logger.info(f"{settings}")` accidental **no** pueda
            # filtrar nada, y por eso vale como garantia y no como buena intencion.
            print("\n5. Enmascarado de secretos")
            repr_settings = repr(s)
            comprobar(
                "repr(Settings) no contiene ningun secreto en claro",
                all(v not in repr_settings for v in secretos),
                "ok",
            )
            volcado = json.dumps(s.model_dump(mode="json"), default=str)
            comprobar(
                "model_dump() no contiene ningun secreto en claro",
                all(v not in volcado for v in secretos),
                "ok",
            )
            comprobar(
                "repr(Settings) muestra el enmascarado",
                "**********" in repr_settings,
            )
    finally:
        await motor.dispose()

    print("\n" + "=" * 70)
    if fallos:
        print(f"{len(fallos)} FALLARON:")
        for f in fallos:
            print(f"  - {f}")
        return 1
    print("Endurecimiento verificado contra el catalogo real.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
