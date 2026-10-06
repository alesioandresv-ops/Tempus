"""Dependencias FastAPI para sesion, autenticacion y autorizacion.

Centraliza cuatro cosas que de otro modo se repiten en cada router y que tienen
que quedar en un solo lugar para ser correctas:

1. **La sesion con tenant.** Obtener una sesion ya viene con el `SET LOCAL` hecho
   y con el commit garantizado. Ningun router arma su propia sesion, porque un
   router que se arma su propia sesion es un router que puede olvidar una de las
   dos cosas y no fallar.
2. **El principal autenticado.** Un `Principal` con `business_id` que sale del
   claim `tid` y de ningun otro lado.
3. **La verificacion de scopes.** El scope va en el nombre (`read:own` vs
   `read:any`) justamente para que el handler no tenga que decidir el alcance.
4. **CSRF y Origin**, para los endpoints que usan la cookie HttpOnly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.rate_limit import (
    WINDOW_HOUR,
    WINDOW_MINUTE,
    enforce_rate_limit,
    rate_limit_key,
)
from app.core.rate_limit import (
    client_ip as rate_limit_client_ip,
)
from app.db.session import (
    get_sessionmaker,
    session_scope,
    tenant_session,
    tenant_session_readonly,
)
from app.modules.auth.scopes import PlatformScope, Scope
from app.modules.auth.tokens import Principal, TokenError, decode_access_token

# Cabecera que lleva el access token.
AUTH_HEADER = "Authorization"


def get_settings_dep() -> Settings:
    """Dependencia que provee la configuración de la aplicación.

    Va antes de sus usuarios a proposito: `require_principal` la usa como
    dependencia suya, y una funcion definida mas abajo no existe todavia cuando
    Python construye la anotacion de `require_principal`.
    """
    return get_settings()


# --------------------------------------------------------------------------- #
# Sesion
# --------------------------------------------------------------------------- #


async def get_session() -> AsyncIterator[AsyncSession]:
    """Sesion transaccional **sin** tenant, para tablas globales.

    La usan solo los endpoints que resuelven el negocio por `slug`, y solo para
    esa parte: `businesses`, `platform_users` y `slug_reservations` son globales
    y no tienen RLS. En cuanto se conoce el `business_id`, se cambia a
    `get_tenant_session`.
    """
    async with session_scope() as session:
        yield session


async def get_tenant_session(
    principal: Annotated[Principal, Depends(require_principal)],
) -> AsyncIterator[AsyncSession]:
    """Sesion transaccional con el contexto de tenant del principal.

    El tenant sale del `business_id` del principal, que viene del claim `tid`.
    No hay forma de pasar un `business_id` distinto: la dependencia no lo acepta
    como parametro, y esa es toda la razon de que sea una dependencia y no un
    argumento del handler.
    """
    if principal.business_id is None:
        # Un token de plataforma llego a un endpoint de negocio. Es 403 y no 401:
        # esta autenticado, lo que no tiene es contexto de tenant.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El token no pertenece a un negocio",
        )
    async with tenant_session(principal.business_id) as session:
        yield session


async def get_tenant_session_readonly(
    principal: Annotated[Principal, Depends(require_principal)],
) -> AsyncIterator[AsyncSession]:
    """Igual que `get_tenant_session`, con rollback al salir. Para lecturas."""
    if principal.business_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El token no pertenece a un negocio",
        )
    async with tenant_session_readonly(principal.business_id) as session:
        yield session


async def get_session_factory() -> AsyncIterator[AsyncSession]:
    """Sesion suelta, sin transaccion ni tenant. Solo para tests.

    Que quede en el modulo y no en los routers es lo que permite que ningun
    router lo use en produccion.
    """
    async with get_sessionmaker()() as session:
        yield session


# --------------------------------------------------------------------------- #
# Autenticacion
# --------------------------------------------------------------------------- #


def _bearer_token(authorization: str | None) -> str:
    """Extrae el token del header `Authorization: Bearer <token>`.

    Se exige el esquema `Bearer` explicito. Aceptar el token solo, sin prefijo,
    elimina la unica pista de que el valor es un token de portador y hace que un
    header mal formado por un cliente se lea como un token valido.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta el header Authorization",
            headers={"WWW-Authenticate": 'Bearer realm="tempus"'},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Formato de Authorization invalido",
            headers={"WWW-Authenticate": 'Bearer realm="tempus"'},
        )
    return token.strip()


async def require_principal(
    authorization: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings_dep),
) -> Principal:
    """Exige un access token valido y devuelve el `Principal`.

    El error de `decode_access_token` se traduce a un 401 generico, sin decir si
    fallo la firma, la expiracion o el `sub`: distinguirlo le daria a un atacante
    un oraculo para enumerar sesiones. El motivo real va al log.
    """
    token = _bearer_token(authorization)
    try:
        return decode_access_token(token, settings=settings)
    except TokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalido",
            headers={"WWW-Authenticate": 'Bearer realm="tempus"'},
        ) from None


async def require_platform_principal(
    principal: Annotated[Principal, Depends(require_principal)],
) -> Principal:
    """Exige que el token sea de plataforma.

    Un token de negocio es 403 y no 401: el usuario esta autenticado, lo que no
    tiene es el rol. Confundir los dos dos casos lleva a un panel de plataforma
    a pedir login de nuevo a alguien que si tiene sesion.
    """
    if not principal.is_platform or principal.business_id is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Se requiere un token de plataforma",
        )
    return principal


# --------------------------------------------------------------------------- #
# Autorizacion
# --------------------------------------------------------------------------- #


def require_scopes(*required: Scope | PlatformScope):
    """Exige que el token tenga **todos** los scopes indicados.

    "Todos" y no "alguno": un endpoint que necesita leer y escribir no debe
    poder abrirse solo con uno de los dos. Y el `*_OWN` de un profesional no
    abre un `*_ANY`: estan separados en el nombre para que un login de
    profesional no lea la agenda de otro.
    """
    required_set = frozenset(str(s) for s in required)

    async def _dependency(
        principal: Annotated[Principal, Depends(require_principal)],
    ) -> Principal:
        if not required_set.issubset(principal.scopes):
            faltantes = sorted(required_set - principal.scopes)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Faltan permisos: {', '.join(faltantes)}",
            )
        return principal

    return _dependency


# --------------------------------------------------------------------------- #
# Cookies, CSRF y red
# --------------------------------------------------------------------------- #


def get_refresh_token(refresh_token: Annotated[str | None, Cookie()] = None) -> str:
    """Extrae el refresh token de la cookie HttpOnly.

    Lanza HTTPException 401 si la cookie no está presente o está vacía.
    """
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token ausente",
        )
    return refresh_token


def get_csrf_token(x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None) -> str:
    """Extrae el token CSRF del header X-CSRF-Token.

    Lanza HTTPException 403 si el header no está presente o está vacío.
    """
    if not x_csrf_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token ausente",
        )
    return x_csrf_token


async def verify_csrf_and_origin(
    csrf_token: Annotated[str, Depends(get_csrf_token)],
    origin: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings_dep),
) -> None:
    """Valida CSRF token y Origin para endpoints que usan cookies HttpOnly.

    Esta dependencia debe usarse en endpoints que leen cookies HttpOnly
    (como /auth/refresh) para prevenir CSRF.

    Validaciones:
    1. Origin header presente y en lista permitida (settings.cors_origins)
    2. X-CSRF-Token presente y válido (validación básica de formato)
    3. Origin header no wildcard

    Lanza HTTPException 403 si alguna validación falla.
    """
    # 1. Validar Origin header
    if not origin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin header ausente",
        )

    if origin not in settings.cors_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Origin no permitido: {origin}",
        )

    # 3. Validar formato básico del CSRF token
    # Debe ser un string no vacío, alfanumérico con guiones/puntos permitidos
    if not csrf_token or not csrf_token.strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token inválido",
        )

    # Validación básica de formato (alfanumérico + guiones + puntos)
    # Los tokens CSRF generados por el frontend deben seguir este formato
    if not all(c.isalnum() or c in "-_" for c in csrf_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token con formato inválido",
        )

    # En una implementación completa con tokens CSRF del lado servidor,
    # aquí se verificaría que el token coincida con el almacenado en la sesión/BD.
    # Para esta implementación, validamos formato + Origin como defensa en profundidad.


def get_origin_header(origin: Annotated[str | None, Header()] = None) -> str:
    """Extrae el header Origin, lanzando 403 si está ausente."""
    if not origin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin header ausente",
        )
    return origin


def get_request_ip(request: Request) -> str:
    """IP del cliente, tal como la resuelve el servidor.

    **No lee `X-Forwarded-For`.** Antes lo hacia, y era un agujero: cualquier
    cliente podia mandar esa cabecera con un valor distinto en cada pedido y caer en
    un cubo distinto del rate limit. El limite por IP--5/min en el login-- se
    esquivaba entero con una cabecera, que es exactamente lo que un limitador de
    fuerza bruta no puede permitir.

    Quitar la cabecera tampoco era una opcion por si sola: atras de Cloudflare y
    Render, `request.client.host` es la direccion del proxy, y los cinco intentos por
    minuto pasaban a ser **cinco por minuto para todo el producto junto**. Un logout
    del ultimo usuario de la plataforma trababa el login de los demas. El sintoma
    habria sido "la pagina anda lento" sin relacion con nada que se hubiera tocado.

    La resolucion correcta no la hace esta funcion: la hace uvicorn, que con
    `--proxy-headers`--activo por defecto-- reescribe `request.client.host` desde
    `X-Forwarded-For` **solo si el pariente inmediato esta en
    `--forwarded-allow-ips`**. Ahi la IP que llega es la del cliente de verdad y
    nadie puede falsearla sin estar en esa lista de confianza.

    **Requisito de despliegue.** Para correr atras de Cloudflare hay que pasar
    `--forwarded-allow-ips` con la IP del proxy. Sin eso, todos los usuarios sharean
    un cubo--que es el modo de falla de arriba-- y el sintoma es que el login empieza
    a devolver 429 para todo el mundo.
    """
    return rate_limit_client_ip(request)


# --------------------------------------------------------------------------- #
# Rate limiting (§10.5)
# --------------------------------------------------------------------------- #
#
# Son dependencias y no un middleware por dos razones concretas, no por estetica:
#
# 1. Una dependencia puede pedir cosas que un middleware no ve sin trabajo extra--el
#    `Principal` para el limite por usuario, el cuerpo parseado para el limite por
#    telefono-- y FastAPI las cachea por request, asi que `require_principal` no se
#    descifra dos veces.
# 2. El middleware corre para **todo**, `/health` incluido, y ahi no hay a quien
#    proteger: una sonda de uptime quemando un cubo es una sonda que puede hacer caer
#    el sitio. Las dependencias se cuelgan de routers concretos, asi que cada clase
#    queda declarada donde se aplica y es visible leyendo el router.
#
# La primitiva y el porque de la transaccion propia estan en `app/core/rate_limit.py`.
#
# El limite de `POST /public/bookings` **tampoco esta aca**, pero por el motivo
# contrario al que se pensaria: no es un ciclo de imports sino el contrato HTTP. Como
# dependencia tendria que declarar `reserva: BookingCreateRequest`, y el handler
# declara lo mismo; con dos parametros de cuerpo FastAPI deja de tratar el cuerpo como
# el modelo entero y espera un objeto con un campo por parametro, asi que el endpoint
# responde 422 a cualquier cuerpo de reserva bien formado. Se llama desde el handler,
# que ya tiene el modelo validado. Lo mismo con `limitar_tick`, que va despues de
# verificar el token y no antes.


async def limit_public_per_ip(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
) -> None:
    """120/min por IP para la superficie publica. La ultima fila de la tabla del §10.5.

    Aplica a todo el router publico. `POST /public/bookings` encima tiene su propio
    limite, mas ajustado, y los dos cuentan: el de esta dependencia es el techo general
    y el del endpoint el que protege el costo de Meta.
    """
    if not settings.rate_limit_enabled:
        return
    await enforce_rate_limit(
        key=rate_limit_key("public:ip", rate_limit_client_ip(request)),
        limit=settings.rate_limit_general_per_ip,
        window_seconds=WINDOW_MINUTE,
        scope="public:ip",
    )


async def limitar_tick(*, request: Request, settings: Settings) -> None:
    """2/min por IP en el tick del scheduler, ademas del token interno.

    El token ya hace el trabajo pesado--es un secreto compartido--, pero el limite
    esta porque el Worker lo llama cada 30 segundos: si el tick devuelve 500 por un
    motivo del motor y el Worker reintenta en bucle, sin limite eso es una cadena
    infinita de trabajo contra la base. Con 2/min, el reintento se corta solo.

    **Se llama desde el handler y no es dependencia de ruta, a proposito.** Tiene que
    ir despues de verificar el token: al reves, cualquiera sin el secreto gastaba el
    cubo del Worker y el Worker legitimo--misma IP-- se comia un 429. Ver el comentario
    en `routers.internal.scheduler_tick`, que es donde se decide el orden.
    """
    if not settings.rate_limit_enabled:
        return
    await enforce_rate_limit(
        key=rate_limit_key("internal:ip", rate_limit_client_ip(request)),
        limit=settings.rate_limit_scheduler_tick,
        window_seconds=WINDOW_MINUTE,
        scope="internal:ip",
    )


async def limit_register_business_per_ip(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
) -> None:
    """3/h por IP para registro self-service."""
    if not settings.rate_limit_enabled:
        return
    await enforce_rate_limit(
        key=rate_limit_key("register:ip", rate_limit_client_ip(request)),
        limit=settings.rate_limit_register_business_per_ip_hourly,
        window_seconds=WINDOW_HOUR,
        scope="register:ip",
    )


async def limit_panel_por_usuario(
    principal: Annotated[Principal, Depends(require_principal)],
    settings: Settings = Depends(get_settings_dep),
) -> None:
    """600/min por usuario en el panel autenticado.

    Va por `user_id` y no por IP a proposito: el panel es donde trabaja una persona de
    carne y hueso, y el limite tiene que ser lo bastante alto para que no la toque
    (600/min son 10 por segundo) y lo bastante alto tambien para que un
    comportamiento automatizado dentro de una sesion valida se note. Por IP seria
    inútil: en una oficina todos salen por la misma puerta, y un abusive colgado de
    una sesion robada comparte cubo con todos los demas.
    """
    if not settings.rate_limit_enabled:
        return
    await enforce_rate_limit(
        key=rate_limit_key("panel:usuario", str(principal.user_id)),
        limit=settings.rate_limit_admin_per_user,
        window_seconds=WINDOW_MINUTE,
        scope="panel:usuario",
    )


__all__ = [
    "AUTH_HEADER",
    "get_csrf_token",
    "get_origin_header",
    "get_refresh_token",
    "get_request_ip",
    "get_session",
    "get_session_factory",
    "get_settings_dep",
    "get_tenant_session",
    "get_tenant_session_readonly",
    "limit_panel_por_usuario",
    "limit_public_per_ip",
    "limit_register_business_per_ip",
    "limitar_tick",
    "require_platform_principal",
    "require_principal",
    "require_scopes",
    "verify_csrf_and_origin",
]
