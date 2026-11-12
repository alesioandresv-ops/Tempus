"""Rate limiting del §10.5, sobre la funcion `rate_limit_hit` de Postgres.

Una sola primitiva, `enforce_rate_limit`, y cinco clases de endpoint que la usan con
distintos parametros:

| Endpoint | Limite | Clave |
|---|---|---|
| `/auth/login` | 30/min por IP, 20/min por email | IP y email |
| `POST /public/bookings` | 10/min por IP, 5/hora por telefono | IP y telefono |
| `/internal/scheduler/tick` | 2/min por IP | IP |
| Resto publico | 120/min por IP | IP |
| Panel autenticado | 600/min por usuario | `user_id` del token |

**Por que la transaccion es propia y no la del request.** Es la parte que mas caro
salio aprender. `rate_limit_hit` escribe en la tabla, y si comparte transaccion con
el request, todo intento que termine en error se lleva el hit con el `rollback`: el
limitador terminaba contando solo los **exitos**, que es exactamente lo contrario de
lo que debe hacer. Con `session_scope` propio el commit pasa al salir del `with`, y el
`RateLimitError` se levanta despues de ese commit--asi el intento cuenta tanto si
pasa, si falla y si es denegado. Esta comprobacion vive en
`tests/integration/test_auth_router.py::test_el_limite_sobrevive_al_rollback_de_la_peticion`.

**De donde sale la IP.** Hay dos capas, y las dos existen por un motivo:

1. **`client_ip` lee `CF-Connecting-IP`, pero solo si el peer inmediato es de
   Cloudflare.** Cloudflare sobrescribe (no agrega) `CF-Connecting-IP` en cada request
   que pasa por el edge: un cliente no puede traer esa cabecera de su casa y hacerla
   llegar hasta nuestra app, porque Cloudflare la reemplaza por la IP real del
   cliente. Es la cabecera mas dificil de falsificar del stack. La validacion del
   peer contra los rangos publicos de Cloudflare (`_redes_cloudflare`) es lo que
   impide que un atacante directo a Render manda la cabecera inventada.

   **Historia:** originalmente este modulo no leia ninguna cabecera de IP y dejaba
   el trabajo a `--forwarded-allow-ips` de uvicorn. En produccion esa variable
   quedo sin setear en el dashboard y todos los usuarios compartian un cubo: el
   login daba 429 y la pagina publica no cargaba, **desde cualquier IP de origen**
   (el cubo lo llenaba el trafico agregado de la plataforma, no el del visitante).
   Por eso ahora la app resuelve la IP sola: no depende de una variable que alguien
   tiene que acordarse de poner en el dashboard.

2. **`request.client.host` como respaldo.** Si el peer no es Cloudflare (local,
   tests, o un despliegue sin CF), la IP del peer es la real y no hay nada que
   resolver. Comportamiento identico al de antes para esos entornos.

Los tests pueden forzar la cabecera; en local el peer es `127.0.0.1`, estable, y los
cubos se pueden vaciar: las pruebas no dependen de la maquina.

**Los mensajes son genericos a proposito.** Un 429 no dice si se disparo el cubo de la
IP o el del email: distinguirlos le diria al atacante si una casilla existe.

**Los mensajes son genericos a proposito.** Un 429 no dice si se disparo el cubo de la
IP o el del email: distinguirlos le diria al atacante si una casilla existe.
"""

from __future__ import annotations

import functools
import ipaddress

from fastapi import Request
from sqlalchemy import text

from app.api.errors import RateLimitError
from app.core.security import hash_token
from app.db.session import session_scope


#: Rangos publicos de entrada de Cloudflare (https://www.cloudflare.com/ips-v4/).
#: Solo un peer cuya IP cae en alguno de estos es el edge real, y solo a el se le
#: cree la cabecera `CF-Connecting-IP`. Se cargan una vez: la lista cambia con una
#: frecuencia de anos.
@functools.lru_cache(maxsize=1)
def _redes_cloudflare() -> tuple[ipaddress.IPv4Network, ...]:
    return tuple(
        ipaddress.ip_network(cidr)
        for cidr in (
            "173.245.48.0/20",
            "103.21.244.0/22",
            "103.22.200.0/22",
            "103.31.4.0/22",
            "141.101.64.0/18",
            "108.162.192.0/18",
            "190.93.240.0/20",
            "188.114.96.0/20",
            "197.234.240.0/22",
            "198.41.128.0/17",
            "162.158.0.0/15",
            "104.16.0.0/13",
            "104.24.0.0/14",
            "172.64.0.0/13",
            "131.0.72.0/22",
        )
    )


def _peer_es_cloudflare(peer: str | None) -> bool:
    """¿El peer inmediato es un edge de Cloudflare?

    Una IP invalida (o `None`, que pasa en tests sin conexion) es "no": sin peer
    verificable no se confia en ninguna cabecera.
    """
    if not peer:
        return False
    try:
        direccion = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(direccion in red for red in _redes_cloudflare())


#: `rate_limit_hit` devuelve `(allowed, hits, retry_after)` y ya sabe aplicar la
#: ventana deslizante y podar; aca no hay nada de ventana que decidir.
_HIT = "SELECT allowed, hits, retry_after FROM rate_limit_hit(:key, :limit, :window, :scope)"

WINDOW_MINUTE = 60
WINDOW_HOUR = 60 * 60

#: Unico mensaje para las cinco clases. Ver la nota del docstring.
DETALLE = "Demasiadas peticiones. Reintentá en un rato."


def rate_limit_key(scope: str, *parts: str) -> str:
    """Clave de cubo, hasheada.

    **El dominio va antes del hash y no despues.** Hash-ear cada parte y concatenar
    los digitos daria la misma clave que hashear el dominio entero, con lo que dos
    superficies distintas terminarian caendo en el mismo cubo: el limite de una frenaria
    a la otra. Es el mismo criterio que usa `login_rate_limit_keys`.

    Nunca en claro: la tabla se purga con un job de retencion y no debe poder leerse
    para obtener IPs de clientes ni telefonos de clientes, que es informacion que ni
    de los clientes del propio negocio.
    """
    return hash_token(f"{scope}:" + ":".join(parts))


def client_ip(request: Request) -> str:
    """IP del cliente real, con `CF-Connecting-IP` si el peer es Cloudflare.

    Detras de Cloudflare el peer es el edge y la cabecera trae la IP del visitante;
    sin Cloudflare (local, tests) el peer ya es el cliente y la cabecera ni se mira.
    Ver la nota del docstring del modulo para el por que de cada capa.
    """
    cliente = request.client
    peer = cliente.host if cliente is not None else None
    if _peer_es_cloudflare(peer):
        cf_ip = request.headers.get("CF-Connecting-IP", "").strip()
        # Una cabecera vacia o malformada cae al peer: peor desenlace posible es
        # un cubo compartido de edge, no un cubo falsificado por el atacante.
        try:
            ipaddress.ip_address(cf_ip)
        except ValueError:
            return peer or "desconocida"
        return cf_ip
    return peer or "desconocida"


async def enforce_rate_limit(
    *,
    key: str,
    limit: int,
    window_seconds: int,
    scope: str,
    detail: str = DETALLE,
) -> None:
    """Cuenta un intento en el cubo `key` y levanta `RateLimitError` si no se permitio.

    Sin `try/except`: si la base no contesta, el error sube y la capa HTTP lo traduce.
    Convertirlo en "permitido" seria fail-open en la unica barrera que hay antes de
    Argon2 y antes de la reserva.
    """
    async with session_scope() as cubo:
        allowed, _hits, retry_after = (
            await cubo.execute(
                text(_HIT),
                {
                    "key": key,
                    "limit": limit,
                    "window": window_seconds,
                    "scope": scope,
                },
            )
        ).one()

    if not allowed:
        raise RateLimitError(detail, extra={"retry_after": int(retry_after)})


__all__ = [
    "DETALLE",
    "WINDOW_HOUR",
    "WINDOW_MINUTE",
    "client_ip",
    "enforce_rate_limit",
    "rate_limit_key",
]
