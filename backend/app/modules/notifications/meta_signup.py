"""Embedded Signup de WhatsApp: intercambio de `code` y lectura de WABAs (Fase 0.5).

Meta no deja que un negocio pegue un token cualquiera y el flujo de "Conectar
WhatsApp" del panel usa el Embedded Signup de Meta: el admin autoriza con su
cuenta de Facebook, la plataforma recibe un `code` de un solo uso, lo cambia por
un token de acceso y lee el WABA del negocio para guardar la conexion.

Este modulo contiene **solo** las llamadas a la Graph API. La persistencia de la
conexion (cifrada, estado `pending`) la hace `config.guardar_conexion_pendiente`,
y el router orquesta las dos cosas. Separarlos es lo que permite testear el
intercambio sin una cuenta de Meta: las funciones aceptan un `client` inyectable
y el router las llama por nombre, que es el patron que ya usa la outbox con
`whatsapp_client_factory`.

**Que pasa con el telefono.** El registro del numero requiere un chip con el SMS
de verificacion de Meta (no esta disponible en esta etapa). El connect guarda la
conexion como `pending` con el `waba_id` y el token, sin numero: cuando el chip
llega, el negocio repite el connect (o pega el Phone Number ID a mano en el PUT
de configuracion) y la conexion pasa a `active` con `is_active=true`.

Formato de datos de Meta (Graph API v21):
- Intercambio de code: GET `/{version}/oauth/access_token` con `client_id`,
  `client_secret`, `code` y `redirect_uri`.
- WABAs del usuario: GET `/me/whatsapp_business_accounts` con el token de acceso.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Tiempo de espera para las llamadas a Meta.
DEFAULT_TIMEOUT = 30.0

#: Campos que se piden de cada WABA. Pedir menos no conviene (hay que volver a
#: la API para completar), y pedir todo tampoco (el `phone_numbers` es lo unico
#: variable y vale la pena traerlo de una).
WABA_FIELDS = (
    "id,name,display_phone_number,quality_rating,messaging_limit_tier,"
    "phone_numbers{id,display_phone,verified_name,quality_rating}"
)


class MetaSignupError(Exception):
    """Meta rechazo el pedido: code invalido, token vencido, falta de permisos.

    Es un error de dominio: la capa HTTP decide si es un 400 (datos del cliente)
    o un 502 (Meta caido). El `detail` que llega del cliente nunca viaja a un
    usuario final tal cual; el router arma un mensaje propio.
    """


@dataclass(frozen=True, slots=True)
class WabaInfo:
    """Un WABA del negocio, con su primer numero si ya tiene uno.

    `phone_number_id` es el id del primer numero del WABA, o `None` si el WABA
    todavia no tiene numeros --el caso de esta etapa, bloqueado por el chip.
    """

    waba_id: str
    name: str | None = None
    display_phone: str | None = None
    quality_rating: str | None = None
    messaging_limit_tier: str | None = None
    phone_number_id: str | None = None


@dataclass(frozen=True, slots=True)
class TokenInfo:
    """El token de acceso de usuario y cuanto vive."""

    access_token: str
    expires_in: int | None = None
    token_type: str | None = None


def _cliente(settings: Settings) -> httpx.AsyncClient:
    """Cliente HTTP apuntando a la base de la Graph API, sin version en el path.

    Las URLs de este modulo llevan la version como prefijo (`v21.0/...`) porque
    `meta_api_base_url` es `https://graph.facebook.com` y el path completo se
    arma aca; meter la version en el `base_url` obligaria a cada llamada a
    recordar si la URL ya la trae.
    """
    base = settings.meta_api_base_url.rstrip("/")
    return httpx.AsyncClient(
        base_url=f"{base}/{settings.meta_graph_api_version}", timeout=DEFAULT_TIMEOUT
    )


async def intercambiar_code(
    code: str,
    redirect_uri: str | None = None,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> TokenInfo:
    """Cambia el `code` de un solo uso por un token de acceso de usuario.

    Es el intercambio OAuth clasico de Facebook, del lado del servidor: el
    `client_secret` viaja aca, nunca al navegador. `redirect_uri` tiene que
    coincidir con el que se configuro en la app de Meta para el login que
    emitio el code; si el flujo del SDK no lo declara, se puede omitir.
    """
    settings = settings or get_settings()
    if not settings.meta_app_id or not settings.meta_app_secret.get_secret_value():
        raise MetaSignupError("La plataforma no tiene configurados META_APP_ID y META_APP_SECRET.")

    params: dict[str, str] = {
        "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret.get_secret_value(),
        "code": code,
    }
    if redirect_uri:
        params["redirect_uri"] = redirect_uri

    cierre = client is None
    http = client or _cliente(settings)
    try:
        response = await http.get("oauth/access_token", params=params)
    except httpx.HTTPError as exc:
        logger.warning("meta_signup_red_no_disponible", error=str(exc))
        raise MetaSignupError("Meta no respondio al intercambio del code.") from exc
    finally:
        if cierre:
            await http.aclose()

    if response.status_code != 200:
        logger.warning(
            "meta_signup_intercambio_fallido",
            status=response.status_code,
            error=response.text[:512],
        )
        raise MetaSignupError(
            "Meta rechazo el code. Volve a intentar el flujo de Conectar WhatsApp."
        )

    datos = response.json()
    token = datos.get("access_token")
    if not token:
        raise MetaSignupError("Meta respondio sin access_token al intercambio del code.")
    expires_in = datos.get("expires_in")
    return TokenInfo(
        access_token=str(token),
        expires_in=int(expires_in) if expires_in is not None else None,
        token_type=datos.get("token_type"),
    )


async def listar_wabas(
    access_token: str,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[WabaInfo]:
    """Los WABAs que el usuario puede administrar con el token.

    Sin WABAs es un caso valido (el negocio nunca configuro WhatsApp en Meta):
    la lista vuelve vacia y el router devuelve un "todavia no hay WABA" con la
    guia de crearlo desde la consola de Meta.
    """
    settings = settings or get_settings()

    cierre = client is None
    http = client or _cliente(settings)
    try:
        response = await http.get(
            "me/whatsapp_business_accounts",
            params={
                "fields": WABA_FIELDS,
                "access_token": access_token,
            },
        )
    except httpx.HTTPError as exc:
        logger.warning("meta_signup_red_no_disponible", error=str(exc))
        raise MetaSignupError("Meta no respondio al pedido de WABAs.") from exc
    finally:
        if cierre:
            await http.aclose()

    if response.status_code != 200:
        logger.warning(
            "meta_signup_wabas_fallido",
            status=response.status_code,
            error=response.text[:512],
        )
        raise MetaSignupError("Meta rechazo el token al pedir los WABAs del negocio.")

    wabas: list[WabaInfo] = []
    for item in response.json().get("data", []):
        numero = (item.get("phone_numbers") or [{}])[0]
        wabas.append(
            WabaInfo(
                waba_id=str(item.get("id") or ""),
                name=item.get("name"),
                display_phone=item.get("display_phone_number") or numero.get("display_phone"),
                quality_rating=numero.get("quality_rating") or item.get("quality_rating"),
                messaging_limit_tier=item.get("messaging_limit_tier"),
                phone_number_id=numero.get("id"),
            )
        )
    return wabas


__all__ = [
    "MetaSignupError",
    "TokenInfo",
    "WabaInfo",
    "intercambiar_code",
    "listar_wabas",
]
