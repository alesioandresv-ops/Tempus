"""Recorre el flujo de reserva igual que lo hace el navegador, a traves del proxy de Vite.

No es una prueba de la API: es una prueba de que **la pagina** puede completar el
camino. Va por `http://localhost:5173/api/...`, o sea que cruza el proxy de Vite,
que es el mismo salto que hace el `fetch` del navegador.

Los pasos son los de `BusinessPage.tsx`: cargar el negocio, elegir servicio,
elegir profesional, pedir disponibilidad para una fecha y enviar el turno.
"""

import datetime as dt
import json
import sys
import urllib.error
import urllib.request
import uuid
from zoneinfo import ZoneInfo

BASE = "http://localhost:5173/api/v1"
SLUG = "pelu-demo"
ZONA = ZoneInfo("America/Argentina/Buenos_Aires")

#: Unica por corrida. Con una clave fija el script no era repetible: la segunda
#: corrida tomaba el slot siguiente porque el primero ya estaba reservado, y la
#: misma clave con otro cuerpo devolvia 409--que es lo correcto del lado del
#: servidor, pero hacia fallar la verificacion sin decir por que.
CLAVE = f"verif-navegador-{uuid.uuid4().hex[:12]}"


def pedir(metodo: str, ruta: str, cuerpo: dict | None = None) -> tuple[int, dict]:
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(
        f"{BASE}{ruta}",
        data=datos,
        method=metodo,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        crudo = e.read()
        try:
            return e.code, json.loads(crudo or b"{}")
        except json.JSONDecodeError:
            return e.code, {"crudo": crudo.decode(errors="replace")}


def paso(nombre: str) -> None:
    print(f"\n=== {nombre} ===")


def main() -> int:
    fallos: list[str] = []

    paso("1. El negocio carga por slug")
    st, negocio = pedir("GET", f"/public/businesses/{SLUG}")
    print(f"status={st} nombre={negocio.get('name')}")
    if st != 200:
        return 1

    paso("2. Hay servicios y profesionales para elegir")
    st, servicios = pedir("GET", f"/public/businesses/{SLUG}/services")
    print(f"status={st} servicios={[s['name'] for s in servicios]}")
    if st != 200 or not servicios:
        fallos.append("no hay servicios")
    servicio = next((s for s in servicios if s["duration_minutes"] <= 60), servicios[0])
    print(f"elegido: {servicio['name']} ({servicio['duration_minutes']} min)")

    st, profesionales = pedir(
        "GET", f"/public/businesses/{SLUG}/professionals?service_id={servicio['id']}"
    )
    print(f"status={st} profesionales={[p['display_name'] for p in profesionales]}")
    if st != 200 or not profesionales:
        fallos.append("no hay profesionales para el servicio")
        return 1
    profesional = profesionales[0]

    paso("3. Hay disponibilidad (el corazon de la pagina)")
    # Hoy y los siguientes dias, hasta el primero con huecos.
    elegido = None
    for offset in range(0, 14):
        dia = (dt.datetime.now(ZONA) + dt.timedelta(days=offset)).date()
        st, disp = pedir(
            "POST",
            f"/public/businesses/{SLUG}/availability",
            {
                "service_id": servicio["id"],
                "professional_id": profesional["id"],
                "date": dia.isoformat(),
            },
        )
        slots = disp.get("slots", [])
        if st == 200 and slots:
            elegido = (dia, slots[0])
            print(f"dia con huecos: {dia.isoformat()} -> {len(slots)} slots")
            break
        print(f"  {dia.isoformat()}: {len(slots)} slots (sigue)")
    if elegido is None:
        fallos.append("ningun dia con disponibilidad en 14 dias")
        return 1

    dia, slot = elegido
    print(f"slot elegido: {json.dumps(slot)}")

    paso("4. Se reserva el turno")
    st, turno = pedir(
        "POST",
        "/public/bookings",
        {
            # Los nombres de estos campos no son los que se me ocurren: son los de
            # `BookingCreateRequest`. Los copio del schema, no de la documentacion,
            # porque el 422 los devuelve uno por uno y asi no queda duda de cual es
            # el contrato real.
            "slug": SLUG,
            "service_id": servicio["id"],
            "professional_id": profesional["id"],
            "customer_first_name": "Prueba",
            "customer_last_name": "Navegador",
            "customer_phone_e164": "+5491112345678",
            "starts_at": slot["starts_at"],
            "ends_at": slot["ends_at"],
            "local_date": dia.isoformat(),
            "idempotency_key": CLAVE,
        },
    )
    print(f"status={st}")
    print(json.dumps(turno, indent=2, ensure_ascii=False)[:1200])
    if st not in (200, 201):
        fallos.append(f"la reserva no se creo (status={st})")
        return 1

    paso("5. La idempotencia: la misma clave devuelve el mismo turno")
    st, repetido = pedir(
        "POST",
        "/public/bookings",
        {
            "slug": SLUG,
            "service_id": servicio["id"],
            "professional_id": profesional["id"],
            "customer_first_name": "Prueba",
            "customer_last_name": "Navegador",
            "customer_phone_e164": "+5491112345678",
            "starts_at": slot["starts_at"],
            "ends_at": slot["ends_at"],
            "local_date": dia.isoformat(),
            "idempotency_key": CLAVE,
        },
    )
    mismo = (
        st == 201
        and repetido.get("booking_id") == turno.get("booking_id")
        and repetido.get("secure_token") == turno.get("secure_token")
    )
    print(f"status={st} mismo turno={mismo}")
    if not mismo:
        fallos.append(f"la idempotencia no devolvio el mismo turno (status={st})")

    paso("6. El turno se puede consultar con su token")
    token = turno.get("secure_token")
    if not token:
        fallos.append("la respuesta no trae token de gestion")
    else:
        st, consulta = pedir("GET", f"/public/bookings/{token}")
        print(
            f"status={st} estado={consulta.get('status')} "
            f"inicio={consulta.get('starts_at')} "
            f"servicio={consulta.get('service_name')} "
            f"profesional={consulta.get('professional_name')}"
        )
        if st != 200:
            fallos.append("el turno no se pudo consultar por token")

    paso("7. El turno se cancela y el slot vuelve a estar disponible")
    if token:
        st, cancelado = pedir("POST", f"/public/bookings/{token}/cancel")
        print(f"cancelar status={st} estado={cancelado.get('status')}")
        if st != 200:
            fallos.append(f"no se pudo cancelar el turno (status={st})")
        else:
            st, disp = pedir(
                "POST",
                f"/public/businesses/{SLUG}/availability",
                {
                    "service_id": servicio["id"],
                    "professional_id": profesional["id"],
                    "date": dia.isoformat(),
                },
            )
            libres = [s for s in disp.get("slots", []) if s["starts_at"] == slot["starts_at"]]
            print(f"el slot vuelve a aparecer en la disponibilidad: {bool(libres)}")
            if not libres:
                fallos.append("tras cancelar, el slot no volvio a la disponibilidad")

    paso("RESULTADO")
    if fallos:
        for f in fallos:
            print(f"FALLA: {f}")
        return 1
    print("OK: el flujo completo de la pagina funciona a traves del proxy")
    return 0


sys.exit(main())
