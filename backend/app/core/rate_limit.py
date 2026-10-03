"""Rate limiting del §10.5, sobre la funcion `rate_limit_hit` de Postgres.

Una sola primitiva, `enforce_rate_limit`, y cinco clases de endpoint que la usan con
distintos parametros:

| Endpoint | Limite | Clave |
|---|---|---|
| `/auth/login` | 5/min por IP, 10/min por email | IP y email |
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

**De donde sale la IP: `request.client.host`, y nada mas.** No se leen
`X-Forwarded-For` ni `CF-Connecting-IP` a mano, y esa es una decision:

- Leer una cabecera de IP sin validar quien la manda es abrir la puerta a que un
  cliente se mande un `X-Forwarded-For` inventado en cada peticion y esquive el
  limite entero. Un limitador que se puede esquivar con una cabecera no limita.
- La otra alternativa--usar solo `request.client.host` sin pensar-- se rompe igual al
  reves: atras de Cloudflare y Render esa direccion es la del proxy, todos los
  usuarios caerian en **un mismo cubo** y las 120/min del resto publico se gastarian
  entre todos. El sitio se caeria solo, y pareceria un bug de la app.

La salida correcta es la que ya resuelve uvicorn: `--proxy-headers`, que reescribe
`request.client.host` desde `X-Forwarded-For` **solo si el pariente inmediato esta en
`--forwarded-allow-ips`**. Con ahi el proxy real, la IP que llega es la del cliente y
nadie puede falsearla sin estar en esa lista. Esta modulo no necesita saber nada de eso,
y por eso no tiene un ajuste propio que alguien pueda activar sin querer.

En tests y en local la IP es `127.0.0.1`, que es estable: los cubos se pueden vaciar y
las pruebas no dependen de la maquina.

**Los mensajes son genericos a proposito.** Un 429 no dice si se disparo el cubo de la
IP o el del email: distinguirlos le diria al atacante si una casilla existe.
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy import text

from app.api.errors import RateLimitError
from app.core.security import hash_token
from app.db.session import session_scope

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
    """IP del cliente segun el servidor. Ver la nota del docstring del modulo."""
    cliente = request.client
    return cliente.host if cliente is not None else "desconocida"


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
