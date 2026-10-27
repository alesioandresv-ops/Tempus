"""Router de autenticación: login de negocio, login de plataforma, refresh y logout.

Este modulo es el unico que decide si alguien entra. Todo lo demas -- el router, los
guards -- consume lo que sale de auth/service.py.

**El orden de las operaciones, y por que es este.** La regla que atraviesa el modulo
es que el camino de un email inexistente y el de una contrasena incorrecta hagan
*exactamente el mismo trabajo criptografico*. Eso obliga a un orden concreto:

1. **Rate limit** (IP y email). Barato, y va primero a proposito: si va ultimo, cada
   intento de fuerza bruta paga un Argon2 completo antes de ser rechazado, y el
   rate limit pasa a ser un detalle economico en vez de una barrera.
2. **Lookup** por email, con la funcion SECURITY DEFINER que saltea RLS pre-tenant.
   Es la unica forma de resolver identidad sin tenant: la RLS de business_users devuelve
   cero filas sin el GUC, por construccion.
3. **Verificacion de la contrasena**, siempre al menos una operacion de Argon2. Si
   el lookup no trajo filas, se verifica contra el hash centinela. Aca esta la
   propiedad que sostiene todo lo demas.
4. **Estado de la cuenta**. Despues, y no antes.
5. **Ambiguedad de tenant**, que se decide solo despues de haber probado la
   contrasena.
6. **Emision**: access token, refresh inicial, cookie.

El paso 4 va despues del 3 por una razon concreta: si el estado se mirara antes, un
`invited` responderia sin verificar la contrasena, y el tiempo de respuesta
distinguiria "existe pero esta invitado" de "existe y la contrasena esta mal" sin
necesidad de adivinar nada. Verificar siempre y decidir el estado despues hace que
el estado de la cuenta sea invisible desde afuera, para bien y para mal: un
`invited` ve exactamente el mismo 401 que un email que no existe.

**Por que el rol de la app no toca platform_users ni escribe password_hash.** No
es una decision de este modulo: son los privilegios de ADR-0002, que quitan todo
privilegio sobre platform_users y restringen el UPDATE de business_users a
(email, full_name, last_login_at, invited_at). Por eso el lookup de plataforma pasa
por su propia funcion SECURITY DEFINER, y por aqui se usa verify_password y no
verify_and_update_password: esta ultima devuelve un hash nuevo cuando los
parametros de Argon2 cambiaron, y guardarlo exigiria un UPDATE de password_hash
que el rol de la app no tiene. Usarla y descartar el hash nuevo seria una operacion
Argon2 extra en cada login viejo a cambio de nada.

**Que NO hace este modulo, a proposito.**

- No escribe last_login_at. Es posible en business_users -- el rol puede, la
  columna esta en la lista de escritura -- pero la escritura necesita el contexto
  de tenant, que todavia no existe durante el login, asi que habria que cambiar el
  GUC dentro de la misma transaccion que hizo el lookup. Y en platform_users es
  imposible: el rol de la app no tiene ningun privilegio sobre la tabla, asi que
  no hay forma de registrar el ultimo login de un operador sin una funcion
  SECURITY DEFINER nueva. Hacerlo solo para la mitad deja el dato mas completo en
  un lado y dishonesto en el otro. Queda para la migracion que lo resuelva.
- No escribe audit_log en los fallos. audit_log lleva RLS por business_id y un
  fallo de login pre-tenant no sabe de que negocio viene: no hay GUC que poner, y
  poner el de un negocio adivinado seria inventar el dato que se quiere registrar.
  Los fallos van al log de aplicacion, que es donde se consulta mientras se depura.
  El audit_log entra en F1.6, donde el tenant ya esta resuelto.
- No rota refresh tokens. Emite el primero de la familia. Rotar, detectar reuso y
  revocar es F1.5/F1.6.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cookies import clear_refresh_cookie, set_refresh_cookie
from app.api.dependencies import (
    get_refresh_token,
    get_request_ip,
    get_session,
    get_settings_dep,
    verify_csrf_and_origin,
)
from app.api.errors import ValidationError
from app.api.routers.schemas import (
    BusinessLoginRequest,
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
)
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.rate_limit import WINDOW_HOUR, enforce_rate_limit
from app.core.security import hash_token
from app.core.time import now
from app.modules.auth import mailer, reset_service
from app.modules.auth.service import (
    RefreshedPlatformIdentity,
    authenticate_business_user,
    authenticate_platform_user,
    revoke_session,
    rotate_refresh_token,
)
from app.modules.auth.tokens import create_access_token, create_platform_access_token

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    status_code=status.HTTP_200_OK,
    summary="Login de miembro de negocio",
    responses={
        200: {"description": "Login exitoso"},
        401: {"description": "Credenciales inválidas"},
        429: {"description": "Rate limit excedido"},
    },
)
async def login_business(
    request: Request,
    response: Response,
    credentials: BusinessLoginRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, str | int]:
    """Autentica a un miembro de negocio y emite access token + refresh token cookie.

    - Valida credenciales contra `business_users` usando la funcion `SECURITY DEFINER`
      que saltea RLS pre-tenant.
    - Emite access token JWT (15 min) en el cuerpo JSON.
    - Emite refresh token opaco en cookie HttpOnly (Secure en prod, SameSite=Lax).
    - No devuelve el refresh token en el JSON.

    `business_slug` solo se usa cuando el mismo email es miembro de varios negocios.
    Con una sola membresia se ignora, y cuando el slug no corresponde la respuesta es
    el mismo 401 que una contrasena incorrecta: no hay 404 para un slug inexistente,
    porque ese 404 confirmaria que el negocio existe.
    """
    client_ip = get_request_ip(request)

    login_result = await authenticate_business_user(
        session,
        email=credentials.email,
        password=credentials.password,
        ip_address=client_ip,
        user_agent=request.headers.get("User-Agent"),
        business_slug=credentials.business_slug,
    )

    # Establecer cookie de refresh token
    set_refresh_cookie(
        response,
        token=login_result.refresh.token,
        expires_at=login_result.refresh.expires_at,
        settings=settings,
    )

    return {
        "access_token": login_result.access_token.token,
        "token_type": "bearer",
        "expires_in": int((login_result.access_token.expires_at - now()).total_seconds()),
    }


@router.post(
    "/platform/login",
    status_code=status.HTTP_200_OK,
    summary="Login de operador de plataforma",
    responses={
        200: {"description": "Login exitoso"},
        401: {"description": "Credenciales inválidas"},
        429: {"description": "Rate limit excedido"},
    },
)
async def login_platform(
    request: Request,
    response: Response,
    credentials: LoginRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, str | int]:
    """Autentica a un operador de plataforma y emite access token + refresh token cookie.

    - Valida credenciales contra `platform_users` (sin RLS, tabla global).
    - Emite access token JWT (15 min) con claims de plataforma:
      * `sub`: platform_user_id
      * `role`: platform_role
      * `scopes`: platform_scopes
      * `is_platform: true`
      * **sin `tid`** (claim ausente, indica ausencia de tenant)
    - Emite refresh token opaco en cookie HttpOnly (Secure en prod, SameSite=Lax).
    - No devuelve el refresh token en el JSON.
    """
    client_ip = get_request_ip(request)

    platform_creds = await authenticate_platform_user(
        session,
        email=credentials.email,
        password=credentials.password,
        ip_address=client_ip,
        user_agent=request.headers.get("User-Agent"),
    )

    set_refresh_cookie(
        response,
        token=platform_creds.refresh.token,
        expires_at=platform_creds.refresh.expires_at,
        settings=settings,
    )

    return {
        "access_token": platform_creds.access_token.token,
        "token_type": "bearer",
        "expires_in": int((platform_creds.access_token.expires_at - now()).total_seconds()),
    }


@router.post(
    "/refresh",
    status_code=status.HTTP_200_OK,
    summary="Rotación de refresh token",
    responses={
        200: {"description": "Refresh exitoso, nuevo access + nuevo refresh"},
        401: {"description": "Refresh token inválido, expirado o reusado"},
        403: {"description": "CSRF/Origin inválido"},
    },
    dependencies=[Depends(verify_csrf_and_origin)],
)
async def refresh_token(
    request: Request,
    response: Response,
    refresh_token: str = Depends(get_refresh_token),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, str | int]:
    """Rota el refresh token: revoca el anterior, emite nuevo access + nuevo refresh.

    Requiere:
    - Cookie `refresh_token` presente (HttpOnly)
    - Header `X-CSRF-Token` válido
    - Header `Origin` presente y en lista permitida (`settings.cors_origins`)

    El token anterior queda revocado y el nuevo pertenece a la **misma familia**, que
    es el hilo que ata la cadena de rotaciones. Si un token ya rotado vuelve a
    aparecer se detecta el reuso y se revoca la familia entera: ver
    `rotate_refresh_token` para por qué esa es la unica salida cuando no se puede
    saber cual de los dos que lo presentan es el atacante.

    El access token se vuelve a emitir desde la fila releida del usuario, no desde
    los claims del token viejo. Es lo que hace que desactivar a un miembro corte su
    acceso en la proxima renovacion y no solo cuando vence el access que ya tiene.
    """
    client_ip = get_request_ip(request)
    user_agent = request.headers.get("User-Agent")

    rotacion = await rotate_refresh_token(
        session,
        presented_token=refresh_token,
        ip_address=client_ip,
        user_agent=user_agent,
    )
    identidad = rotacion.identity

    if isinstance(identidad, RefreshedPlatformIdentity):
        access = create_platform_access_token(
            user_id=identidad.user_id,
            role=identidad.role.value,
            scopes=[str(s) for s in identidad.scopes],
        )
    else:
        access = create_access_token(
            user_id=identidad.user_id,
            business_id=identidad.business_id,
            role=identidad.role.value,
            scopes=[str(s) for s in identidad.scopes],
            settings=settings,
        )

    # El token nuevo va en la cookie, nunca en el JSON: es lo que permite que la
    # sesion sobreviva a un F5 sin guardar nada en `localStorage`, donde un XSS lo
    # leeria. Y es **el** que emitio la rotacion -- no uno nuevo: si se emitiera
    # otro, la cookie llevaria un token distinto del que quedo encadenado con
    # `replaced_by_id`, y el proximo refresh seria detectado como reuso.
    set_refresh_cookie(
        response,
        token=rotacion.grant.token,
        expires_at=rotacion.grant.expires_at,
        settings=settings,
    )

    return {
        "access_token": access.token,
        "token_type": "bearer",
        "expires_in": int((access.expires_at - now()).total_seconds()),
    }


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cierre de sesión",
    responses={
        204: {"description": "Sesión cerrada (siempre 204, haya cookie o no)"},
        403: {"description": "CSRF/Origin inválido"},
    },
    dependencies=[Depends(verify_csrf_and_origin)],
)
async def logout(
    response: Response,
    refresh_token: str | None = Depends(get_refresh_token),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """Revoca la familia de refresh tokens y borra la cookie.

    **Idempotente a propósito**: devuelve 204 aunque no haya cookie, aunque el token
    no exista y aunque ya este revocado. Un logout que fallara con 401 cuando la
    cookie ya vencio obligaría al frontend a distinguir "no hay sesion" de "el
    servidor no me dejo cerrar la sesion", que es una distincion que el usuario no
    puede actuar y que un atacante podría usar como oracul0.

    Revoca la familia es lo que hace que el corte sea real: un logout que solo
    revocara un único token dejaría vivos los sucesores de esa familia, y como cada
    refresh genera el siguiente, el usuario que hizo logout tendería a tener un
    token valido en el navegador (el que acababa de rotar) y podría renovar
    indefinidamente.

    Requiere CSRF y Origin igual que el refresh, porque es una operacion que cambia
    el estado de la sesion y sin esos controles un sitio de terceros podría cerrar
    la sesion de un usuario con un `<form>` cross-site.
    """
    if refresh_token:
        await revoke_session(session, refresh_token)

    clear_refresh_cookie(response, settings=settings)
    # 204: sin cuerpo. `response` ya tiene los headers; se cortan los que la
    # calculadora de cookies pone y se devuelven tal cual.
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# --------------------------------------------------------------------------- #
# Recuperación de contraseña (flujo "olvidé mi contraseña")
# --------------------------------------------------------------------------- #


@router.post(
    "/forgot-password",
    status_code=status.HTTP_200_OK,
    summary="Pedir el enlace de recuperación de contraseña",
    responses={
        200: {"description": "Siempre 200, exista o no la cuenta (anti-enumeración)"},
        429: {"description": "Demasiados pedidos (3/h por email, 10/h por IP)"},
    },
)
async def forgot_password(
    datos: ForgotPasswordRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    ip_address: str = Depends(get_request_ip),
) -> dict[str, str]:
    """Registra el pedido de reset y manda el email con el enlace.

    **La respuesta es siempre la misma** exista o no la cuenta: es lo que hace
    que este endpoint no sea un oráculo de enumeración de emails. El rate
    limit (3/h por email, 10/h por IP) se aplica dentro del servicio, antes de
    mirar la base, para que el contador mismo tampoco sea un oráculo.

    Si el SMTP no está configurado (local, o un deploy sin correo), el link se
    loguea en el servidor para poder probar el flujo completo sin casilla.
    """

    async def _enviar(para: str, asunto: str, cuerpo: str) -> None:
        if not mailer.smtp_configurado(settings):
            # Modo local/sin SMTP: el link queda en el log del servidor.
            logger.warning(
                "reset_email_no_enviado_smtp_sin_configurar",
                destino=para[:3] + "***",
                link=cuerpo.splitlines()[4] if len(cuerpo.splitlines()) > 4 else "",
            )
            return
        await mailer.enviar_email_seguro(settings, para=para, asunto=asunto, cuerpo=cuerpo)

    await reset_service.solicitar_reset(
        session,
        email=datos.email,
        ip_address=ip_address,
        enviar_email=_enviar,
    )
    # Sin commit manual: `get_session` es `session_scope` y commitea al salir.
    # Un commit explicito aca romperia el contrato de los tests (la sesion del
    # test no puede commitear una transaccion anidada).
    return {
        "mensaje": (
            "Si el correo tiene cuenta, te enviamos un enlace para restablecer "
            "la contraseña. Revisá también la carpeta de spam."
        )
    }


@router.post(
    "/reset-password",
    status_code=status.HTTP_200_OK,
    summary="Restablecer la contraseña con el token del email",
    responses={
        200: {"description": "Contraseña cambiada y sesiones revocadas"},
        400: {"description": "Token inválido, vencido o ya usado (un solo mensaje)"},
        429: {"description": "Demasiados intentos"},
    },
)
async def reset_password(
    datos: ResetPasswordRequest,
    session: AsyncSession = Depends(get_session),
    ip_address: str = Depends(get_request_ip),
) -> dict[str, str]:
    """Consume el token y cambia la contraseña en todas las membresías del email.

    Un 400 no distingue "no existe" de "vencido" de "ya usado": distinguirlos
    le diría a quien robó un link si todavía le sirve.

    Al cambiar la contraseña se revocan los refresh tokens de la persona en
    todos sus negocios: la sesión robada muere con el reset.
    """
    await enforce_rate_limit(
        key=hash_token(f"reset-consumo:ip:{ip_address}"),
        limit=10,
        window_seconds=WINDOW_HOUR,
        scope="reset-consumo:ip",
        detail="Demasiados intentos. Reintentá en un rato.",
    )
    try:
        await reset_service.consumir_reset(
            session,
            token=datos.token,
            nueva_password=datos.password,
            ip_address=ip_address,
        )
    except ValueError as error:
        raise ValidationError(str(error)) from error
    return {"mensaje": "Contraseña actualizada. Ya podés entrar con la nueva."}
