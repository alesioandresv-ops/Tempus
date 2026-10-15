"""Verificacion server-side de Cloudflare Turnstile.

Turnstile es la barrera anti-bot delante de los endpoints publicos --crear una
reserva y dar de alta un negocio--, que son los dos que no pasan por autenticacion
y los dos que cuestan plata si los dispara un script (Argon2 en el alta, un
mensaje de WhatsApp en la reserva).

El widget corre en el navegador y devuelve un **token de un solo uso**; el frontend
lo manda en el cuerpo de la peticion y el backend lo verifica contra la API de
Cloudflare. El token no se guarda: caduca en minutos y lo unico que importa es la
respuesta booleana del momento.

**Va detras de `TURNSTILE_ENABLED`.** Apagado --el default, lo que corre en tests y
en local-- la funcion es un no-op: exigir un token sin widget en la pagina romperia
todos los clientes que hoy reservan. Encendido, una peticion sin token valido no
pasa. La bandera la pone el operador cuando el sitekey ya esta montado en el
frontend, no antes.
"""

from __future__ import annotations

import httpx
from fastapi import HTTPException, status

from app.core.config import Settings

#: Timeout de la verificacion. Cloudflare contesta en decenas de milisegundos; los
#: 5 s son el techo de paciencia de una persona mirando un formulario, no una
#: estimacion de la latencia real.
_TIMEOUT_S = 5.0

_DETALLE_FALTA = "Falta la verificacion anti-bot."
_DETALLE_RECHAZADO = "La verificacion anti-bot no paso. Recarga la pagina e intentalo de nuevo."
_DETALLE_INDISPONIBLE = (
    "No se pudo comprobar la proteccion anti-bot. Intentalo de nuevo en un momento."
)


async def verificar_turnstile(
    token: str | None,
    *,
    settings: Settings,
    remote_ip: str | None,
) -> None:
    """Levanta `HTTPException` si Turnstile esta activo y el token no vale.

    Silencio total con la bandera apagada. Con la bandera encendida:

    - Sin token: **403**. El frontend no mando el widget.
    - Cloudflare rechaza el token: **403**.
    - No se pudo hablar con Cloudflare: **503**, no 403. No es que la persona sea un
      bot, es que no se pudo comprobar; un 403 la mandaria a reintentar de una forma
      que no arregla nada y ademas mentiria sobre el motivo.
    """
    if not settings.turnstile_enabled:
        return

    if not token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_DETALLE_FALTA)

    datos = {
        "secret": settings.turnstile_secret.get_secret_value(),
        "response": token,
    }
    if remote_ip:
        # `remoteip` es opcional para Cloudflare pero mejora la deteccion: si la IP
        # del token no coincide con la de la peticion, lo marca como sospechoso.
        datos["remoteip"] = remote_ip

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as cliente:
            respuesta = await cliente.post(settings.turnstile_verify_url, data=datos)
        cuerpo = respuesta.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_DETALLE_INDISPONIBLE,
        ) from exc
    except ValueError as exc:
        # Respuesta con cuerpo que no es JSON: el contrato roto es el de Cloudflare,
        # no el del cliente.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_DETALLE_INDISPONIBLE,
        ) from exc

    if not isinstance(cuerpo, dict) or not cuerpo.get("success"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_DETALLE_RECHAZADO)


__all__ = ["verificar_turnstile"]
