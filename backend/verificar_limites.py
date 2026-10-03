"""Verifica en vivo los cinco limites del §10.5 contra un servidor de verdad.

Los tests de `tests/integration/test_rate_limits.py` ya cubren las cuatro clases que
faltaban, pero corren contra la app montada en memoria, con las dependencias de
FastAPI resueltas en proceso. Esta script las prueba por HTTP contra un uvicorn de
verdad, que es donde aparecen los problemas que un test no ve: el middleware real, la
pila de ASGI real y un ciclo de vida de conexion real.

Ademas comprueba lo que un test con `dependency_overrides` no puede: que el limite
cuente cuando la peticion **falla**. Todos los casos de booking mandan un cuerpo
valido con un `service_id` inexistente, asi que el handler responde 404. Si el
limitador contara solo los exitos, estos casos darian todos 404 y ninguno 429, que es
justo el bug que se corrigio.

    $env:TEMPUS_PORT = '8002'
    python verificar_limites.py
"""

import json
import os
import time
import urllib.error
import urllib.request
import uuid

PUERTO = os.environ.get("TEMPUS_PORT", "8002")
BASE = f"http://127.0.0.1:{PUERTO}/api/v1"
NEGOCIO = os.environ.get("TEMPUS_SLUG", "pelu-demo")
ADMIN = ("admin@pelu-demo.com.ar", "Demo1234!")

fallos: list[str] = []
informacion: list[str] = []


def comprobar(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {nombre}{f' -- {detalle}' if detalle else ''}")
    if not ok:
        fallos.append(nombre)


def pedir(
    metodo: str, ruta: str, cuerpo: dict | None = None, cabeceras: dict | None = None
) -> tuple[int, dict, dict]:
    """Devuelve `(codigo, cuerpo_json, cabeceras)`. Un 429 no es excepcion."""
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(f"{BASE}{ruta}", data=datos, method=metodo)
    req.add_header("Content-Type", "application/json")
    for clave, valor in (cabeceras or {}).items():
        req.add_header(clave, valor)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            crudo = r.read().decode()
            return r.status, (json.loads(crudo) if crudo else {}), dict(r.headers)
    except urllib.error.HTTPError as e:
        crudo = e.read().decode()
        try:
            return e.code, json.loads(crudo), dict(e.headers)
        except json.JSONDecodeError:
            return e.code, {"crudo": crudo}, dict(e.headers)


def _cabecera(cabeceras: dict, nombre: str) -> str | None:
    """Busca una cabecera sin distinguir mayusculas.

    `urllib` normaliza los nombres pero no garantiza que lleguen como los mandamos, y
    `dict.get("Retry-After")` sobre un `{"retry-after": ...}` devuelve `None`. Un
    chequeo que da `None` por una diferencia de mayusculas hace fallar la verificacion
    de algo que funciona, que es peor que no verificarla.
    """
    for clave, valor in cabeceras.items():
        if clave.lower() == nombre.lower():
            return valor
    return None


def codigos_hasta_429(n: int, metodo: str, ruta: str, **kwargs) -> list[int]:
    """Los primeros `n` codigos, cortando en el primer 429."""
    salida = []
    for _ in range(n):
        codigo, _, _ = pedir(metodo, ruta, **kwargs)
        salida.append(codigo)
        if codigo == 429:
            break
    return salida


def cuerpo_reserva(telefono: str, clave: str) -> dict:
    """Reserva valida con un `service_id` que no existe: el handler dara 404.

    El 404 es lo que permite probar el limite sin depender del motor de
    disponibilidad: lo que se quiere medir es que el intento **se cuenta**, y el
    intento fallido es justamente el que un atacante hace.
    """
    return {
        "slug": NEGOCIO,
        "service_id": str(uuid.UUID("99999999-9999-7999-8999-999999999999")),
        "customer_first_name": "Ana",
        "customer_last_name": "Verificacion",
        "customer_phone_e164": telefono,
        "starts_at": "2099-01-01T15:00:00Z",
        "ends_at": "2099-01-01T15:30:00Z",
        "local_date": "2099-01-01",
        "idempotency_key": clave,
    }


def esperar_bloques_vacios() -> None:
    """Espera a que los cubos se vacien de intentos anteriores.

    La ventana de login y la publica son de 60 s y los cubos son compartidos por IP, asi
    que una corrida anterior dejaria hits que correria a los tests de hoy. Con
    `RATE_LIMIT_ENABLED=false` en el servidor esto seria inocuo, pero entonces no
    estariamos probando nada.
    """
    print("Esperando 62 s a que expiren los cubos de corridas anteriores...")
    time.sleep(62)


def _claims_de(token: str) -> dict:
    """Claims de un JWT, decodificado a mano.

    No se verifica la firma--esto no es un test de seguridad, es una mirada a lo que
    el servidor puso-- y por eso no se importa el modulo de tokens: este script corre
    contra el servidor, no contra el codigo.
    """
    try:
        import base64

        carga = token.split(".")[1]
        carga += "=" * (-len(carga) % 4)
        return json.loads(base64.urlsafe_b64decode(carga))
    except Exception:
        return {}


def main() -> int:
    print(f"Servidor: {BASE}\n")

    # --- reachable --------------------------------------------------------
    codigo, _, _ = pedir("GET", f"/public/businesses/{NEGOCIO}")
    comprobar("el servidor responde", codigo == 200, f"HTTP {codigo}")
    if codigo != 200:
        return 2

    esperar_bloques_vacios()

    # --- login: 5/min por IP (ya implementado antes, se revalida) --------
    print("\n1. Login: 5/min por IP")
    codigos = []
    for _ in range(7):
        codigo, _, cabeceras = pedir(
            "POST",
            "/auth/login",
            {"email": "nadie-aqui@ejemplo.com.ar", "password": "incorrecta"},
        )
        codigos.append(codigo)
        if codigo == 429:
            retry = _cabecera(cabeceras, "Retry-After")
            comprobar("el 429 trae Retry-After", bool(retry), f"Retry-After={retry}")
            break
    comprobar(
        "los intentos fallidos cuentan y el sexto rebota",
        codigos[:5] == [401] * 5 and codigos[5:6] == [429],
        f"codigos={codigos}",
    )

    esperar_bloques_vacios()

    # --- login de negocio: sigue funcionando ------------------------------
    print("\n2. Login de negocio: 200 y con su tenant")
    codigo, cuerpo, cabeceras = pedir(
        "POST", "/auth/login", {"email": ADMIN[0], "password": ADMIN[1]}
    )
    comprobar("el login de negocio da 200", codigo == 200, f"HTTP {codigo}")
    token = ""
    if codigo == 200:
        token = cuerpo.get("access_token", "")
        claims = _claims_de(token)
        comprobar("el token trae su tenant", bool(claims.get("tid")), f"claims={list(claims)}")
    cookie = _cabecera(cabeceras, "set-cookie") or ""
    comprobar(
        "viene cookie de refresh HttpOnly",
        "refresh_token" in cookie.lower() and "httponly" in cookie.lower(),
        f"set-cookie={cookie[:70] or '(vacia)'}",
    )

    esperar_bloques_vacios()

    # --- reserva publica: 10/min por IP ------------------------------------
    # Numeros distintos en cada corrida: estos tambien caen en el cubo por telefono,
    # que es horario, y tres corridas con los mismos 10 lo dejarian lleno.
    prefijo = int(time.time()) % 100000000
    print("\n3. POST /public/bookings: 10/min por IP")
    codigos = [
        pedir(
            "POST",
            "/public/bookings",
            cuerpo_reserva(f"+5492{prefijo * 100 + i:09d}"[-15:], f"ip{i}"),
        )[0]
        for i in range(12)
    ]
    comprobar(
        "el onceavo intento desde la misma IP rebota",
        429 in codigos and codigos.index(429) == 10,
        f"codigos={codigos}",
    )
    comprobar(
        "ninguno de los diez primeros fue 429",
        429 not in codigos[:10],
        f"codigos={codigos[:10]}",
    )
    print(f"     (los handlers respondieron {list(codigos[:10])}; lo que importa es el 429)")

    esperar_bloques_vacios()

    # --- reserva publica: 5/hora por telefono ------------------------------
    # Numero nuevo en cada corrida, a proposito: la ventana por telefono es de **una
    # hora**, asi que un numero fijo hace que la segunda corrida arranque con los 5
    # hits de la primera y falle entera. Ya paso: daba 429 desde el primer intento,
    # que es el limite funcionando, no roto--pero hace la verificacion inutilizable.
    # La espera de `esperar_bloques_vacios` no ayuda: 62 s vacian la ventana de un
    # minuto, no la horaria.
    telefono_de_hoy = f"+5491{int(time.time()) % 100000000:08d}"
    print(f"\n4. POST /public/bookings: 5/hora por telefono ({telefono_de_hoy})")
    codigos = [
        pedir(
            "POST",
            "/public/bookings",
            cuerpo_reserva(telefono_de_hoy, f"tel{i}"),
        )[0]
        for i in range(7)
    ]
    comprobar(
        "el sexto intento con el mismo telefono rebota",
        429 in codigos and codigos.index(429) == 5,
        f"codigos={codigos}",
    )

    # Y el cubo es del telefono, no de la IP.
    otro = pedir(
        "POST",
        "/public/bookings",
        cuerpo_reserva(f"+5491{(int(time.time()) + 7) % 100000000:08d}", "otro"),
    )[0]
    comprobar("un telefono distinto no rebota", otro != 429, f"HTTP {otro}")

    esperar_bloques_vacios()

    # --- el techo publico no frena al panel -------------------------------
    print("\n5. El limite publico no alcanza al panel")
    pagina, _, _ = pedir("GET", f"/public/businesses/{NEGOCIO}")
    comprobar("la pagina del negocio sigue sirviendo", pagina == 200, f"HTTP {pagina}")
    if token:
        panel, _, _ = pedir("GET", "/business/me", cabeceras={"Authorization": f"Bearer {token}"})
        comprobar(
            "el panel sigue respondiendo con el mismo token",
            panel == 200,
            f"HTTP {panel}",
        )

    # --- el token interno va antes que el limite --------------------------
    print("\n6. El tick del scheduler: 401 antes que 429")
    codigos = [
        pedir(
            "POST",
            "/internal/scheduler/tick",
            {},
            {"X-Internal-Token": "token-incorrecto-a-proposito"},
        )[0]
        for _ in range(4)
    ]
    comprobar(
        "sin el secreto siempre es 401, nunca 429",
        set(codigos) == {401},
        f"codigos={codigos}",
    )

    print("\n" + "=" * 70)
    if fallos:
        print(f"{len(fallos)} FALLARON:")
        for f in fallos:
            print(f"  - {f}")
        return 1
    for i in informacion:
        print(f"  {i}")
    print("Los cinco limites del §10.5 se comportan como dice la tabla.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
