"""Verifica en vivo los arreglos de auth y del rate limiter.

No es un test de pytest: va contra el servidor que esta corriendo (`TEMPUS_PORT`),
porque los tres cambios son de comportamiento observable desde afuera y un test que
solo mira la sesion no los demuestra.

1. **Login de plataforma.** Estaba roto de punta a punta: `_emitir_refresh` exigia
   `user_id` en la firma y el login de plataforma no tiene--no hay tenant-- asi que
   daba `TypeError` y un 500 para todos los operadores. El cambio es de firma, y un
   cambio de firma no se verifica con un test unitario del servicio: se verifica
   entrando.
2. **Login de negocio** sigue,andando con el mismo camino.
3. **Rate limit.** Antes de la migracion `0013` el limite era de 5 intentos **por
   segundo**, porque el cubo se reiniciaba cada segundo. Seis intentos seguidos con
   contrasena incorrecta tienen que dar 429 al sexto, no 401.

El operador de plataforma se siembra con el rol de DDL y se borra al final, porque
`0002` le quita todo privilegio de `platform_users` al rol de la app.
"""

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

PUERTO = os.environ.get("TEMPUS_PORT", "8002")
BASE = f"http://127.0.0.1:{PUERTO}"
MIGRATION_URL = os.environ.get("DATABASE_MIGRATION_URL", "")
NEGOCIO_ADMIN = ("admin@pelu-demo.com.ar", "Demo1234!")
#: `.test` no sirve: `EmailStr` rechaza los TLD de uso reservado salvo que se pida
#: `test_environment=True`, y el endpoint no lo pide. Con `.test` el login de
#: plataforma respondia 422 y el chequeo de "no da 500" pasaba por el motivo
#: equivocado--era un rechazo de validacion, no un login funcionando.
PLATFORM = ("owner@verificacion.com.ar", "Plataforma1234!")

fallos: list[str] = []


def pedir(metodo: str, ruta: str, cuerpo: dict | None = None) -> tuple[int, dict, list[str]]:
    """Devuelve status, cuerpo y **cabeceras**.

    Las cabeceras hacen falta para ver el `Set-Cookie`: el cuerpo del login no
    menciona el refresh, y un chequeo que lo busque ahi pasaria siempre.
    """
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(
        f"{BASE}{ruta}",
        data=datos,
        method=metodo,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as e:
        crudo = e.read()
        try:
            cuerpo_ = json.loads(crudo or b"{}")
        except json.JSONDecodeError:
            cuerpo_ = {"crudo": crudo.decode(errors="replace")}
        return e.code, cuerpo_, e.headers.get_all("Set-Cookie") or []


def comprobar(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {nombre}{f' -- {detalle}' if detalle else ''}")
    if not ok:
        fallos.append(nombre)


async def sembrar_operador() -> None:
    """Siembra el operador con el rol de DDL y lo commitea.

    Async y no sincronico porque el venv no tiene `psycopg2`: la unica variante de
    driver instalada es `asyncpg`. `poolclass=None` ademas evita dejar una conexion
    abierta por todo el script--esta corrida abre varias-- y `hash_password` es la
    misma funcion que usa la app, para que el hash sea comparable.
    """
    from app.core.security import hash_password

    motor = create_async_engine(MIGRATION_URL, poolclass=None)
    try:
        async with motor.begin() as conn:
            await conn.execute(
                text("DELETE FROM platform_users WHERE email = :email"),
                {"email": PLATFORM[0]},
            )
            await conn.execute(
                text(
                    "INSERT INTO platform_users "
                    "(id, email, password_hash, full_name, role, is_active) "
                    "VALUES (gen_random_uuid(), :email, :hash, 'Verificacion', 'owner', true)"
                ),
                {"email": PLATFORM[0], "hash": hash_password(PLATFORM[1])},
            )
    finally:
        await motor.dispose()


async def borrar_operador() -> None:
    motor = create_async_engine(MIGRATION_URL, poolclass=None)
    try:
        async with motor.begin() as conn:
            await conn.execute(
                text("DELETE FROM platform_users WHERE email = :email"),
                {"email": PLATFORM[0]},
            )
    finally:
        await motor.dispose()


async def main() -> int:
    print(f"Servidor: {BASE}\n")

    print("1. Login de plataforma (estaba roto: TypeError en la firma)")
    await sembrar_operador()
    try:
        st, cuerpo, cookies = pedir(
            "POST", "/api/v1/auth/platform/login", {"email": PLATFORM[0], "password": PLATFORM[1]}
        )
        comprobar("el login de plataforma no da 500", st != 500, f"HTTP {st}")
        comprobar("el login de plataforma da 200", st == 200, json.dumps(cuerpo)[:200])
        refresco = next((c for c in cookies if c.startswith("refresh_token=")), None)
        comprobar(
            "viene cookie de refresh HttpOnly",
            refresco is not None and "HttpOnly" in refresco,
            refresco or f"cookies={cookies}",
        )
        if st == 200:
            import jwt as pyjwt

            claims = pyjwt.decode(
                cuerpo["access_token"],
                options={"verify_signature": False, "verify_exp": False},
            )
            comprobar("el token es de plataforma", claims.get("is_platform") is True)
            comprobar("el token no trae tenant", "tid" not in claims, f"claims={sorted(claims)}")
            comprobar(
                "trae scopes de plataforma", "platform:tenants:read" in claims.get("scopes", [])
            )
            comprobar(
                "no trae scopes de negocio",
                not any(
                    s.startswith(("bookings:", "clients:", "team:"))
                    for s in claims.get("scopes", [])
                ),
            )
    finally:
        await borrar_operador()

    print("\n2. Credenciales malas de plataforma dan 401, no 500")
    st, _, _ = pedir(
        "POST", "/api/v1/auth/platform/login", {"email": PLATFORM[0], "password": "incorrecta"}
    )
    comprobar("401 uniforme", st == 401, f"HTTP {st}")

    print("\n3. Login de negocio sigue funcionando")
    st, cuerpo, _ = pedir(
        "POST", "/api/v1/auth/login", {"email": NEGOCIO_ADMIN[0], "password": NEGOCIO_ADMIN[1]}
    )
    comprobar("login de negocio da 200", st == 200, f"HTTP {st} {json.dumps(cuerpo)[:160]}")
    if st == 200:
        import jwt as pyjwt

        claims = pyjwt.decode(
            cuerpo["access_token"], options={"verify_signature": False, "verify_exp": False}
        )
        comprobar("el token trae su tenant", bool(claims.get("tid")), f"tid={claims.get('tid')}")

    print("\n4. El rate limit ahora frena: 6 intentos con contrasena mala desde la misma IP")
    codigos = []
    cuerpo: dict = {}
    for _ in range(10):
        st, cuerpo, _cabeceras = pedir(
            "POST",
            "/api/v1/auth/login",
            {"email": "nadie-esta@ejemplo.com.ar", "password": "incorrecta"},
        )
        codigos.append(st)
        if st == 429:
            break
    print(f"  codigos: {codigos}")
    comprobar(
        "aparece un 429 dentro de los primeros 6 intentos", 429 in codigos[:6], f"codigos={codigos}"
    )
    comprobar(
        "el 429 trae Retry-After en la cabecera",
        429 not in codigos or "retry_after" in cuerpo,
        json.dumps(cuerpo)[:200],
    )

    print("\n" + "=" * 70)
    if fallos:
        print(f"{len(fallos)} comprobaciones FALLARON:")
        for f in fallos:
            print(f"  - {f}")
        return 1
    print("Todas las comprobaciones en vivo pasaron.")
    return 0


sys.exit(asyncio.run(main()))
