"""Verifica el flujo publico de reservas de punta a punta contra el server.

Cada caso usa **un dia habil distinto**. No es por prolijidad: antes de que la
reserva revalidara contra el calendario, cada corrida dejaba turnos ocupados y el
caso siguiente se comia un 409 legitimo que no era el bug que buscaba. Ahora que
la revalidacion funciona, dos casos que comparten fecha se pisan entre si. Un dia
por caso es lo que hace que el script sea repetible.

Salida: `PASS`/`FAIL` por caso y codigo de salida distinto de cero si algo falla,
para poder correrlo en un pipeline sin tener que leer la salida.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid

import requests

BASE = f"http://127.0.0.1:{os.environ.get('TEMPUS_PORT', '8005')}/api/v1"
SLUG = "pelu-demo"
TIMEOUT = 30

_resultados: list[tuple[str, bool, str]] = []


# ---------------------------------------------------------------- utilidades


def pedir(metodo: str, path: str, **kw) -> requests.Response:
    """Una peticion con reintentos.

    El server de desarrollo con `--reload` a veces deja colgada la primera
    conexion. Dos reintentos y a seguir: lo que se busca medir aca es logica de
    negocio, no la salud del socket.

    **Ademas espera los 429 del rate limit** (§10.5) y reintenta cuando se va la
    ventana. Sin esto, este script se viola a si mismo: hace muchas reservas seguidas
    y el endpoint tiene un techo de 10/min por IP, asi que a partir de la decima toda
    reserva daba 429 y las comprobaciones de doble reserva y de reprogramacion
    fallaban con el codigo equivocado--un bug de negocio reportado como falso
    negativo. Es el mismo motivo por el que un cliente real tiene que mirar
    `Retry-After`: el 429 no es un error de la peticion, es "espera".
    """
    ultimo: Exception | None = None
    espera_429 = 0
    for intento in range(3):
        try:
            r = requests.request(metodo, f"{BASE}{path}", timeout=TIMEOUT, **kw)
        except requests.RequestException as exc:
            ultimo = exc
            print(f"   [reintento {intento + 1}] {type(exc).__name__} en {path}")
            continue
        if r.status_code == 429 and espera_429 < 6:
            espera_429 += 1
            # +1 s de margen: `Retry-After` es un piso, no una promesa exacta, y si se
            # vuelve a pedir en el segundo exacto del limite el 429 se repite igual.
            segundos = int(r.headers.get("Retry-After", "5")) + 1
            print(f"   [rate limit] {r.status_code} en {path}; espero {segundos}s y reintento")
            time.sleep(segundos)
            continue
        return r
    raise AssertionError(f"{metodo} {path} fallo tres veces: {ultimo}")


def get(path: str, **kw) -> requests.Response:
    return pedir("GET", path, **kw)


def post(path: str, **kw) -> requests.Response:
    return pedir("POST", path, **kw)


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> bool:
    _resultados.append((nombre, condicion, detalle))
    marca = "PASS" if condicion else "FAIL"
    print(f"  [{marca}] {nombre}" + (f" -- {detalle}" if detalle else ""))
    return condicion


def habilitado_desde(offset_dias: int) -> dt.date:
    """Primer dia habil (L-V) a `offset_dias`calendar de hoy.

    Empieza por manana: los horarios de hoy ya pasaron para casi cualquier hora de
    la tarde, y `min_lead_minutes` los filtra. Reservar sobre un dia que ya
    termino daria un 409 correcto que el script leeria como bug.
    """
    # `noqa: DTZ011`--`DTZ011` apunta a `datetime.now()`, donde la zona implicita
    # convierte "el instante de ahora" en algo que depende de donde corra el script.
    # Aca lo que se quiere es precisamente la fecha **local** del negocio: manana en
    # el calendario del cliente, no manana en UTC. Un `date` no tiene hora que
    # normalizar, asi que la regla no aplica.
    d = dt.date.today() + dt.timedelta(days=1)  # noqa: DTZ011
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    for _ in range(offset_dias):
        d += dt.timedelta(days=1)
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
    return d


def slots_de(fecha: dt.date, servicio_id: str, profesional: str | None = None) -> list[dict]:
    r = post(
        f"/public/businesses/{SLUG}/availability",
        json={
            "service_id": servicio_id,
            "professional_id": profesional,
            "date": fecha.isoformat(),
        },
    )
    assert r.status_code == 200, f"disponibilidad dio {r.status_code}: {r.text}"
    return r.json()["slots"]


def reservar(
    *,
    servicio_id: str,
    profesional: str | None,
    slot: dict,
    fecha: dt.date,
    apellido: str,
    telefono: str | None = None,
    headers: dict | None = None,
    **extra,
) -> requests.Response:
    cuerpo = {
        "slug": SLUG,
        "service_id": servicio_id,
        "professional_id": profesional,
        "customer_first_name": "Ana",
        "customer_last_name": apellido,
        "customer_phone_e164": telefono or f"+54911{uuid.uuid4().int % 10**8:08d}",
        "starts_at": slot["starts_at"],
        "ends_at": slot["ends_at"],
        "local_date": fecha.isoformat(),
        **extra,
    }
    return post("/public/bookings", json=cuerpo, headers=headers or {})


def cancelar(token: str) -> None:
    post(f"/public/bookings/{token}/cancel", json={})


# --------------------------------------------------------------------- casos


def caso_disponibilidad_dice_quien(servicio_id: str, profesionales: list[dict]) -> None:
    """ "Cualquier profesional" tiene que decir a quien le toca cada horario."""
    print("\n1. La disponibilidad identifica al profesional del horario")
    fecha = habilitado_desde(0)
    slots = slots_de(fecha, servicio_id)

    comprobar("hay horarios para probar", bool(slots), f"{len(slots)} slots el {fecha}")
    if not slots:
        return

    sin_nombre = [s for s in slots if not s.get("professional_id")]
    comprobar(
        "ningun slot viene sin profesional",
        not sin_nombre,
        f"{len(sin_nombre)} de {len(slots)} slots con professional_id null",
    )

    validos = {p["id"] for p in profesionales}
    conocidos = [s for s in slots if s.get("professional_id") in validos]
    comprobar(
        "el profesional asignado es uno real del negocio",
        len(conocidos) == len(slots),
        f"{len(conocidos)}/{len(slots)} slots con un profesional del negocio",
    )

    # El mismo horario no puede aparecer repetido una vez por profesional: el
    # cliente quiere una lista de horas, no una lista de personas.
    inicios = [s["starts_at"] for s in slots]
    comprobar(
        "no hay horarios duplicados",
        len(inicios) == len(set(inicios)),
        f"{len(inicios) - len(set(inicios))} duplicados",
    )


def caso_rechaza_fuera_de_agenda(servicio_id: str) -> None:
    """Un domingo, con el negocio cerrado, tiene que ser 409."""
    print("\n2. No se reserva con el negocio cerrado")

    domingo = habilitado_desde(0)
    while domingo.weekday() != 6:
        domingo += dt.timedelta(days=1)

    vacio = slots_de(domingo, servicio_id)
    comprobar("el domingo no ofrece horarios", not vacio, f"{len(vacio)} slots")

    # Se construye a mano un turno de las 12:00 del domingo. Es exactamente lo
    # que hacia el cliente: el endpoint dice que no hay nada, y el POST dice que
    # si. Sin esto, la reserva del domingo tiene que fallar.
    inicio = dt.datetime.combine(
        domingo, dt.time(12, 0), tzinfo=dt.timezone(dt.timedelta(hours=-3))
    )
    r = reservar(
        servicio_id=servicio_id,
        profesional=None,
        slot={
            "starts_at": inicio.astimezone(dt.UTC).isoformat(),
            "ends_at": (inicio + dt.timedelta(minutes=30)).astimezone(dt.UTC).isoformat(),
        },
        fecha=domingo,
        apellido="Domingo",
    )
    comprobar(
        "el POST de un domingo con el negocio cerrado da 409",
        r.status_code == 409,
        f"HTTP {r.status_code}",
    )

    # Y lo mismo con la hora: las 03:00 del primer dia habil.
    fecha = habilitado_desde(0)
    madrugada = dt.datetime.combine(
        fecha, dt.time(3, 0), tzinfo=dt.timezone(dt.timedelta(hours=-3))
    )
    r = reservar(
        servicio_id=servicio_id,
        profesional=None,
        slot={
            "starts_at": madrugada.astimezone(dt.UTC).isoformat(),
            "ends_at": (madrugada + dt.timedelta(minutes=30)).astimezone(dt.UTC).isoformat(),
        },
        fecha=fecha,
        apellido="Madrugada",
    )
    comprobar(
        "un turno a las 03:00 da 409",
        r.status_code == 409,
        f"HTTP {r.status_code}",
    )


def caso_cualquier_profesional(servicio_id: str, profesionales: list[dict]) -> None:
    """ "Cualquier profesional" tiene que asignar a quien puede, no al primero."""
    print("\n3. 'Cualquier profesional' elige a quien puede tomar el horario")
    fecha = habilitado_desde(1)
    # **Los slots se piden de Ana, no del grupo.** Un horario del grupo puede ser
    # de Caro y no de Ana: cada profesional tiene su propia agenda. Reservarle a
    # Ana un horario que el grupo ofrecio--pero que ella no puede atender--da un
    # 409 correcto que este caso leeria como un bug. La prueba tiene que partir
    # de un horario que Ana realmente puede tomar.
    propios = slots_de(fecha, servicio_id, profesionales[0]["id"])
    comprobar("Ana tiene horarios propios", bool(propios), f"{len(propios)} slots el {fecha}")
    if not propios:
        return

    ana = profesionales[0]
    objetivo = propios[len(propios) // 2]

    r = reservar(
        servicio_id=servicio_id,
        profesional=ana["id"],
        slot=objetivo,
        fecha=fecha,
        apellido="AnaOcupada",
    )
    comprobar("Ana reserva ese horario", r.status_code == 201, f"HTTP {r.status_code}")
    if r.status_code != 201:
        print(f"     {r.text[:200]}")
        return

    # El horario tiene que seguir saliendo: Beto y Caro estan libres. Y tiene que
    # seguir saliendo **con otro profesional en el campo**, que es justo el punto.
    despues = [s for s in slots_de(fecha, servicio_id) if s["starts_at"] == objetivo["starts_at"]]
    comprobar("el horario sigue disponible", bool(despues))
    if not despues:
        return
    comprobar(
        "el horario se ofrece con un profesional que lo puede atender",
        despues[0]["professional_id"] != ana["id"],
        f"ofrece {despues[0]['professional_id'][:8]}, Ana {ana['id'][:8]}",
    )

    r = reservar(
        servicio_id=servicio_id,
        profesional=None,
        slot=objetivo,
        fecha=fecha,
        apellido="CualquierPro",
    )
    ok = r.status_code == 201
    comprobar(
        "la reserva del mismo horario da 201 en vez de 409",
        ok,
        f"HTTP {r.status_code}",
    )
    if not ok:
        print(f"     {r.text[:200]}")
        return

    asignado = r.json()["professional_id"]
    comprobar(
        "el profesional asignado no es el que ya estaba ocupado",
        asignado != ana["id"],
        f"asignado {asignado[:8]}, Ana {ana['id'][:8]}",
    )
    cancelar(r.json()["secure_token"])


def caso_respuesta_completa(servicio_id: str, profesionales: list[dict]) -> None:
    """El POST tiene que devolver la misma respuesta que el GET por token."""
    print("\n4. El POST devuelve la reserva completa, no una recortada")
    fecha = habilitado_desde(2)
    beto = profesionales[1]
    # De la agenda de Beto, no de la del grupo: un horario del grupo puede no
    # ser suyo. Ver el comentario del caso 3.
    slots = slots_de(fecha, servicio_id, beto["id"])
    comprobar("Beto tiene horarios propios", bool(slots), f"{len(slots)} slots el {fecha}")
    if not slots:
        return
    r = reservar(
        servicio_id=servicio_id,
        profesional=beto["id"],
        slot=slots[0],
        fecha=fecha,
        apellido="Respuesta",
    )
    comprobar("la reserva se crea", r.status_code == 201, f"HTTP {r.status_code}")
    if r.status_code != 201:
        print(f"     {r.text[:200]}")
        return

    cuerpo = r.json()
    token = cuerpo["secure_token"]
    detalle = get(f"/public/bookings/{token}").json()

    print("     POST:")
    print("     " + json.dumps(cuerpo, indent=2, ensure_ascii=False).replace("\n", "\n     "))

    faltantes = [
        campo
        for campo in (
            "secure_token",
            "service_name",
            "professional_name",
            "customer_first_name",
            "business_name",
            "business_phone",
            "business_slug",
            "local_date",
            "price",
            "currency",
        )
        if cuerpo.get(campo) in (None, "")
    ]
    comprobar("el POST no deja campos en null", not faltantes, f"faltan: {faltantes}")

    distintos = [
        campo
        for campo in ("service_name", "professional_name", "business_name", "local_date")
        if cuerpo.get(campo) != detalle.get(campo)
    ]
    comprobar(
        "el POST y el GET coinciden",
        not distintos,
        f"difieren: {distintos}",
    )
    cancelar(token)


def caso_idempotencia(servicio_id: str, profesionales: list[dict]) -> None:
    """El reintento devuelve el mismo token; la key reusada con otro cuerpo, 409."""
    print("\n5. La idempotencia devuelve el mismo turno y el mismo token")
    fecha = habilitado_desde(3)
    caro = profesionales[2]
    slots = slots_de(fecha, servicio_id, caro["id"])
    comprobar("Caro tiene horarios propios", bool(slots), f"{len(slots)} slots el {fecha}")
    if not slots:
        return

    slot = slots[min(1, len(slots) - 1)]
    key = uuid.uuid4().hex
    cabeceras = {"Idempotency-Key": key}

    r1 = reservar(
        servicio_id=servicio_id,
        profesional=caro["id"],
        slot=slot,
        fecha=fecha,
        apellido="Idempotente",
        headers=cabeceras,
    )
    comprobar("el primer POST crea la reserva", r1.status_code == 201, f"HTTP {r1.status_code}")
    if r1.status_code != 201:
        print(f"     {r1.text[:200]}")
        return

    r2 = reservar(
        servicio_id=servicio_id,
        profesional=caro["id"],
        slot=slot,
        fecha=fecha,
        apellido="Idempotente",
        headers=cabeceras,
    )
    comprobar("el reintento da 201", r2.status_code == 201, f"HTTP {r2.status_code}")

    if r2.status_code == 201:
        token1, token2 = r1.json()["secure_token"], r2.json()["secure_token"]
        comprobar(
            "el reintento devuelve el mismo secure_token",
            bool(token1) and token1 == token2,
            f"'{token1[:16]}' vs '{token2[:16]}'",
        )
        comprobar(
            "el reintento devuelve el mismo booking_id",
            r1.json()["booking_id"] == r2.json()["booking_id"],
        )

        # Y el enlace tiene que abrir de verdad: es lo que el cliente guarda.
        abierto = get(f"/public/bookings/{token2}")
        comprobar(
            "el token del reintento abre la reserva",
            abierto.status_code == 200,
            f"HTTP {abierto.status_code}",
        )

    # Misma clave, cuerpo distinto: no es un reintento, es otra reserva.
    r3 = reservar(
        servicio_id=servicio_id,
        profesional=caro["id"],
        slot=slots[min(2, len(slots) - 1)],
        fecha=fecha,
        apellido="OtroCuerpo",
        headers=cabeceras,
    )
    comprobar(
        "la misma clave con otro cuerpo da 409",
        r3.status_code == 409,
        f"HTTP {r3.status_code}",
    )

    cancelar(r1.json()["secure_token"])


def caso_reprogramar(servicio_id: str, profesionales: list[dict]) -> None:
    """Reprogramar mueve el turno y libera el horario anterior."""
    print("\n6. Reprogramar mueve el turno y libera el horario viejo")
    fecha = habilitado_desde(4)
    ana = profesionales[0]
    # De la agenda de Ana: el turno se reprograma dentro de los horarios que ella
    # puede atender, no dentro de los que el grupo puede atender.
    slots = slots_de(fecha, servicio_id, ana["id"])
    comprobar("Ana tiene horarios propios", bool(slots), f"{len(slots)} slots el {fecha}")
    if len(slots) < 4:
        comprobar("al menos 4 horarios para poder mover el turno", False, f"hay {len(slots)}")
        return

    viejo, nuevo = slots[3], slots[-1]

    r = reservar(
        servicio_id=servicio_id,
        profesional=ana["id"],
        slot=viejo,
        fecha=fecha,
        apellido="Reprogramable",
    )
    comprobar("la reserva original se crea", r.status_code == 201, f"HTTP {r.status_code}")
    if r.status_code != 201:
        print(f"     {r.text[:200]}")
        return
    token = r.json()["secure_token"]

    r2 = post(
        f"/public/bookings/{token}/reschedule",
        json={
            "new_starts_at": nuevo["starts_at"],
            "new_ends_at": nuevo["ends_at"],
            "new_duration_minutes": 30,
        },
    )
    comprobar("la reprogramacion da 200", r2.status_code == 200, f"HTTP {r2.status_code}")
    if r2.status_code != 200:
        print(f"     {r2.text[:200]}")
        return

    cuerpo = r2.json()
    comprobar(
        "el turno se movio al horario nuevo",
        cuerpo["starts_at"].startswith(nuevo["starts_at"][:16]),
        f"{cuerpo['starts_at']} vs {nuevo['starts_at']}",
    )
    comprobar(
        "el turno ya no esta en el horario viejo",
        not cuerpo["starts_at"].startswith(viejo["starts_at"][:16]),
    )

    # El horario viejo tiene que estar libre de nuevo.
    libres = slots_de(fecha, servicio_id, ana["id"])
    comprobar(
        "el horario viejo vuelve a estar disponible",
        any(s["starts_at"].startswith(viejo["starts_at"][:16]) for s in libres),
    )

    # Y el nuevo, tomado.
    libres_nuevo = slots_de(fecha, servicio_id, ana["id"])
    comprobar(
        "el horario nuevo ya no esta disponible",
        not any(s["starts_at"].startswith(nuevo["starts_at"][:16]) for s in libres_nuevo),
    )

    # Y el token sigue sirviendo para gestionar el turno movido.
    detalle = get(f"/public/bookings/{token}")
    comprobar("el enlace sigue funcionando", detalle.status_code == 200)
    cancelar(token)


def caso_doble_reserva(servicio_id: str, profesionales: list[dict]) -> None:
    """Dos reservas del mismo horario y profesional: la segunda es 409."""
    print("\n7. El mismo horario no se puede vender dos veces")
    fecha = habilitado_desde(5)
    ana = profesionales[0]
    slots = slots_de(fecha, servicio_id, ana["id"])
    comprobar("Ana tiene horarios propios", bool(slots), f"{len(slots)} slots el {fecha}")
    if not slots:
        return

    # Un horario que Ana, Beto y Caro puedan atender. Si el horario fuera solo de
    # Ana, "desaparece cuando ya no puede atenderlo nadie" seria trivial: basta con
    # ocupar a Ana. Hace falta uno compartido para que la comprobacion signifique
    # algo.
    slot = next(
        (
            s
            for s in slots
            if all(
                any(t["starts_at"] == s["starts_at"] for t in slots_de(fecha, servicio_id, p["id"]))
                for p in profesionales
            )
        ),
        None,
    )
    comprobar(
        "hay un horario que los tres pueden atender",
        slot is not None,
        "ninguno compartido por los tres" if slot is None else f"{slot['starts_at']}",
    )
    if slot is None:
        slot = slots[0]

    tokens: list[str] = []
    r1 = reservar(
        servicio_id=servicio_id, profesional=ana["id"], slot=slot, fecha=fecha, apellido="Primero"
    )
    comprobar("la primera reserva entra", r1.status_code == 201, f"HTTP {r1.status_code}")
    if r1.status_code == 201:
        tokens.append(r1.json()["secure_token"])

    r2 = reservar(
        servicio_id=servicio_id, profesional=ana["id"], slot=slot, fecha=fecha, apellido="Segundo"
    )
    comprobar("la segunda da 409", r2.status_code == 409, f"HTTP {r2.status_code}")

    # Con "cualquier profesional": el horario tiene que seguir saliendo mientras
    # quede alguien que lo pueda atender, y desaparecer recien cuando no quede
    # nadie. **Ninguna reserva se cancela hasta el final**: cancelar libera el
    # horario, asi que comprobar antes de cancelar seria medir lo contrario de lo
    # que dice el caso.
    libres = slots_de(fecha, servicio_id)
    sigue = [s for s in libres if s["starts_at"] == slot["starts_at"]]
    comprobar(
        "el horario sigue mientras otro profesional pueda atenderlo",
        bool(sigue),
        f"{len(sigue)} slots con el horario tomado por Ana",
    )
    comprobar(
        "y se ofrece con un profesional distinto al que ya lo tiene",
        bool(sigue) and sigue[0]["professional_id"] != ana["id"],
        f"ofrece {sigue[0]['professional_id'][:8] if sigue else '-'}",
    )

    for otro in profesionales[1:]:
        r3 = reservar(
            servicio_id=servicio_id,
            profesional=otro["id"],
            slot=slot,
            fecha=fecha,
            apellido=f"Ocupado{otro['display_name']}",
        )
        comprobar(
            f"{otro['display_name']} puede tomar el horario",
            r3.status_code == 201,
            f"HTTP {r3.status_code}",
        )
        if r3.status_code == 201:
            tokens.append(r3.json()["secure_token"])

    ultimos = [s for s in slots_de(fecha, servicio_id) if s["starts_at"] == slot["starts_at"]]
    comprobar(
        "el horario desaparece cuando ya no puede atenderlo nadie",
        not ultimos,
        f"quedan {len(ultimos)} slots en ese horario",
    )

    # Y ahora si: al cancelar, el horario tiene que volver.
    for t in tokens:
        cancelar(t)

    vuelve = [s for s in slots_de(fecha, servicio_id) if s["starts_at"] == slot["starts_at"]]
    comprobar(
        "al cancelar, el horario vuelve a la lista",
        bool(vuelve),
        f"{len(vuelve)} slots",
    )


# --------------------------------------------------------------------------- #
# Lo que este script NO cubre
# --------------------------------------------------------------------------- #
#
# **Aislamiento entre negocios por HTTP.** Habia una `caso_aislamiento()` que se
# limitaba a imprimir "se omite: requiere sembrar un segundo negocio" y nunca fue
# llamada desde `main`, asi que no aportaba ni una comprobacion--solo hacia creer que
# el caso estaba contemplado. Se borro en vez de dejarla.
#
# La cobertura que si existe es de la **capa de base**, en
# `tests/tenancy/test_rls_isolation.py`: no leer, insertar, actualizar ni borrar filas
# de otro tenant. Lo que no cubre ninguna de las dos es el camino completo--un token
# del negocio A contra un endpoint del negocio B pasando por HTTP, autenticacion y
# RLS--. Esta anotado como hueco conocido en `ENTREGA.md`.


# ------------------------------------------------------------------- corrida


def main() -> int:
    print(f"Servidor: {BASE}")
    print(f"Negocio:  {SLUG}")

    servicios = get(f"/public/businesses/{SLUG}/services").json()
    profesionales = get(f"/public/businesses/{SLUG}/professionals").json()
    corte = servicios[0]

    print(f"\nServicio:    {corte['name']} ({corte['duration_minutes']} min)")
    print(f"Profesionales: {', '.join(p['display_name'] for p in profesionales)}")

    caso_disponibilidad_dice_quien(corte["id"], profesionales)
    caso_rechaza_fuera_de_agenda(corte["id"])
    caso_cualquier_profesional(corte["id"], profesionales)
    caso_respuesta_completa(corte["id"], profesionales)
    caso_idempotencia(corte["id"], profesionales)
    caso_reprogramar(corte["id"], profesionales)
    caso_doble_reserva(corte["id"], profesionales)

    # ---------------------------------------------------------------- resumen
    print("\n" + "=" * 78)
    fallos = [n for n, ok, _ in _resultados if not ok]
    print(f"{len(_resultados) - len(fallos)}/{len(_resultados)} comprobaciones OK")
    if fallos:
        print("\nFallan:")
        for n in fallos:
            detalle = next(d for name, _, d in _resultados if name == n)
            print(f"  - {n}" + (f" ({detalle})" if detalle else ""))
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
