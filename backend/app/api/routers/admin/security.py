"""Autenticacion, CSRF y cabeceras del panel administrativo web.

Tres decisiones que no son obvias y que conviene leer antes de tocar el archivo:

1. **La sesion no vive en el cliente entera.** Se firma con `itsdangerous` un
   payload chico (usuario + token CSRF) y se guarda en una cookie HttpOnly. La
   alternativa --el middleware `SessionMiddleware` de Starlette-- tambien firma,
   pero serializa la sesion completa en la cookie; aca no hace falta y el panel
   administra la plataforma entera como para darle al cliente la posibilidad de
   leer el contenido.
2. **La cookie es `Secure` siempre.** Es un panel de plataforma, no una sesion de
   negocio: no hay un caso legitimo de servirlo por HTTP en produccion. En local
   se prueba con un cliente ASGI y una cookie armada a mano (ver
   `tests/test_admin_panel_seguro.py`), no relajando la bandera.
3. **El CSRF del login es double-submit firmado.** Antes de autenticar no hay
   sesion donde guardar el token, asi que el token viaja dos veces: en un campo
   oculto del formulario y en una cookie HttpOnly. Las dos copias se comparan con
   `hmac.compare_digest` y la firma impide que un atacante invente un par
   consistente. Despues del login el token vive dentro de la sesion firmada.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.dependencies import get_settings_dep
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.rate_limit import client_ip, enforce_rate_limit, rate_limit_key

logger = get_logger(__name__)

#: Cookies del panel. El prefijo `tempus_admin_` las separa de la cookie de
#: refresh del producto, que vive con otro path y otra razon.
COOKIE_SESION = "tempus_admin_session"
COOKIE_CSRF = "tempus_admin_csrf"

#: 30 minutos. Es una sesion de operador de plataforma: si se deja abierta toda la
#: tarde en una computadora compartida, el panel queda abierto toda la tarde.
DURACION_SESION = 30 * 60

#: Ruta unica de login. Se usa en los 302 de `require_admin_session`.
RUTA_LOGIN = "/admin/login"

#: Ventana del limite de intentos de login: 15 minutos.
VENTANA_LOGIN = 15 * 60

_SAL_SESION = "tempus-admin-sesion"
_SAL_CSRF = "tempus-admin-csrf"


@dataclass(frozen=True, slots=True)
class SesionAdmin:
    """Lo que viaja dentro de la cookie firmada."""

    user: str
    csrf: str


def panel_configurado(settings: Settings) -> bool:
    """Si estan las piezas que el login necesita.

    Hay dos caminos validos: hash bcrypt (`ADMIN_PASSWORD_HASH`) o password
    plana (`ADMIN_PASSWORD`). El segundo es el bypass para Render Free, que
    descarta variables que contienen el `$` de bcrypt. Con cualquiera de los
    dos + usuario + secreto, el panel esta operativo.
    """
    return bool(
        settings.admin_user
        and (
            settings.admin_password_hash.get_secret_value()
            or settings.admin_password.get_secret_value()
        )
        and settings.secret_key.get_secret_value()
    )


def _serializer(settings: Settings, salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key.get_secret_value(), salt=salt)


# --------------------------------------------------------------------------- #
# Sesion
# --------------------------------------------------------------------------- #


def crear_sesion(settings: Settings, user: str) -> tuple[str, SesionAdmin]:
    """Crea el valor firmado de la cookie y la sesion que representa."""
    sesion = SesionAdmin(user=user, csrf=secrets.token_urlsafe(32))
    valor = _serializer(settings, _SAL_SESION).dumps({"user": sesion.user, "csrf": sesion.csrf})
    return valor, sesion


def leer_sesion(settings: Settings, valor: str | None) -> SesionAdmin | None:
    """Valida la cookie y devuelve la sesion, o `None` si no sirve.

    Un `None` no distingue "no hay cookie" de "la cookie expiro" ni de "el usuario
    configurado cambio": el cliente recibe un 302 al login en los tres casos y no
    se le da un oraculo sobre el motivo. El detalle va al log del servidor.
    """
    if not valor or not settings.admin_user:
        return None
    try:
        datos = _serializer(settings, _SAL_SESION).loads(valor, max_age=DURACION_SESION)
    except BadSignature:
        return None
    if not isinstance(datos, dict):
        return None
    user = datos.get("user")
    csrf = datos.get("csrf")
    if not isinstance(user, str) or not isinstance(csrf, str):
        return None
    # Un cambio de `ADMIN_USER` invalida las sesiones vivas. Sin esto, renombrar
    # la cuenta dejaria cookies viejas con acceso hasta media hora.
    if not hmac.compare_digest(user.encode(), settings.admin_user.encode()):
        return None
    return SesionAdmin(user=user, csrf=csrf)


def require_admin_session(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> SesionAdmin:
    """Dependencia de las rutas del panel: sin sesion valida, 302 al login.

    Es 302 y no 401 a proposito: el panel es HTML navegado por una persona, y un
    401 con JSON en el navegador no ofrece ninguna accion. Lo que cambia es el
    status; el detalle del fallo nunca sale en la respuesta.
    """
    sesion = leer_sesion(settings, request.cookies.get(COOKIE_SESION))
    if sesion is None:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": RUTA_LOGIN},
        )
    return sesion


# --------------------------------------------------------------------------- #
# CSRF
# --------------------------------------------------------------------------- #


def crear_token_csrf(settings: Settings) -> str:
    """Token firmado para el formulario de login (todavia no hay sesion)."""
    return _serializer(settings, _SAL_CSRF).dumps(secrets.token_urlsafe(32))


def token_csrf_firmado_valido(settings: Settings, valor: str | None) -> bool:
    """Verifica que el token del formulario lo emitio este servidor."""
    if not valor:
        return False
    try:
        _serializer(settings, _SAL_CSRF).loads(valor, max_age=DURACION_SESION)
    except BadSignature:
        return False
    return True


def csrf_de_sesion_valido(sesion: SesionAdmin, enviado: str | None) -> bool:
    """Compara el token del formulario con el que viaja en la sesion firmada."""
    if not enviado:
        return False
    return hmac.compare_digest(enviado.encode(), sesion.csrf.encode())


# --------------------------------------------------------------------------- #
# Cookies
# --------------------------------------------------------------------------- #


def poner_cookie_sesion(response: Response, valor: str) -> None:
    response.set_cookie(
        COOKIE_SESION,
        valor,
        max_age=DURACION_SESION,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )


def borrar_cookie_sesion(response: Response) -> None:
    response.delete_cookie(
        COOKIE_SESION,
        path="/",
        httponly=True,
        secure=True,
        samesite="strict",
    )


def poner_cookie_csrf(response: Response, valor: str) -> None:
    response.set_cookie(
        COOKIE_CSRF,
        valor,
        max_age=DURACION_SESION,
        httponly=True,
        secure=True,
        samesite="strict",
        path=RUTA_LOGIN,
    )


def borrar_cookie_csrf(response: Response) -> None:
    response.delete_cookie(
        COOKIE_CSRF,
        path=RUTA_LOGIN,
        httponly=True,
        secure=True,
        samesite="strict",
    )


# --------------------------------------------------------------------------- #
# Password y limite de intentos
# --------------------------------------------------------------------------- #


def credenciales_validas(settings: Settings, user: str, password: str) -> bool:
    """Compara usuario y password sin filtrar por cual de los dos fallo.

    La verificacion cara (bcrypt, o la comparacion) corre **siempre**, aun
    cuando el usuario no coincida, y el resultado se combina al final. Cortar
    antes cuando el usuario esta mal haria que un usuario inexistente respondiera
    en microsegundos y uno valido tardara lo que tarda bcrypt: la diferencia se
    mide y confirma la cuenta.

    Dos caminos de password: si hay `ADMIN_PASSWORD_HASH`, bcrypt (camino
    normal); si solo hay `ADMIN_PASSWORD` (bypass de Render Free), comparacion
    en tiempo constante. Nunca los dos a la vez, y el hash tiene prioridad:
    cuando Render deje pasar el `$`, el camino plano queda sin efecto solo.
    """
    hash_configurado = settings.admin_password_hash.get_secret_value()
    password_plana = settings.admin_password.get_secret_value()
    password_ok = False
    if hash_configurado:
        try:
            password_ok = bcrypt.checkpw(password.encode("utf-8"), hash_configurado.encode())
        except ValueError:
            # Hash con formato invalido: se trata como password incorrecta y se
            # deja rastro, sin devolverle el hash ni el error al cliente.
            logger.warning("admin_password_hash_invalido")
            password_ok = False
    elif password_plana:
        password_ok = hmac.compare_digest(password.encode("utf-8"), password_plana.encode("utf-8"))
    user_ok = hmac.compare_digest(user.encode("utf-8"), settings.admin_user.encode("utf-8"))
    return user_ok and password_ok


async def limitar_login_admin(request: Request, settings: Settings) -> None:
    """5 intentos / 15 min por IP.

    Se cuenta **antes** de verificar credenciales: si se contara solo el fallo, un
    atacante podria probar passwords ilimitadas mientras acierte y el limite no
    protegeria nada. La ventana es larga a proposito--un operador legitimo no
    inicia sesion quince veces en quince minutos-- y el cubo es por IP, no global,
    para que un atacante no pueda dejar afuera al administrador real.
    """
    if not settings.rate_limit_enabled:
        return
    await enforce_rate_limit(
        key=rate_limit_key("admin:login:ip", client_ip(request)),
        limit=settings.rate_limit_admin_login_per_ip,
        window_seconds=VENTANA_LOGIN,
        scope="admin:login:ip",
    )


# --------------------------------------------------------------------------- #
# Cabeceras
# --------------------------------------------------------------------------- #

#: Prefijos donde se aplican las cabeceras. El panel HTML vive en `/admin` y el
#: JSON legacy en `/api/v1/admin`; los dos son la misma superficie.
PREFIJOS_ADMIN = ("/admin", "/api/v1/admin")

CABECERAS_ADMIN = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    # `style-src` admite inline porque las plantillas usan atributos de estilo
    # puntuales; `script-src` NO, y por eso el modal del panel vive en un archivo
    # servido desde el mismo origen (`/static/admin.js`).
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
    ),
    "Referrer-Policy": "no-referrer",
}


class AdminSecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Agrega las cabeceras de seguridad a todo `/admin` y `/api/v1/admin`."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        ruta = request.url.path
        if any(ruta == prefijo or ruta.startswith(prefijo + "/") for prefijo in PREFIJOS_ADMIN):
            for nombre, valor in CABECERAS_ADMIN.items():
                response.headers[nombre] = valor
        return response


__all__ = [
    "CABECERAS_ADMIN",
    "COOKIE_CSRF",
    "COOKIE_SESION",
    "DURACION_SESION",
    "PREFIJOS_ADMIN",
    "RUTA_LOGIN",
    "AdminSecurityHeadersMiddleware",
    "SesionAdmin",
    "borrar_cookie_csrf",
    "borrar_cookie_sesion",
    "crear_sesion",
    "crear_token_csrf",
    "credenciales_validas",
    "csrf_de_sesion_valido",
    "leer_sesion",
    "limitar_login_admin",
    "panel_configurado",
    "poner_cookie_csrf",
    "poner_cookie_sesion",
    "require_admin_session",
    "token_csrf_firmado_valido",
]
