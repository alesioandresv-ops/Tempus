"""Panel administrativo web (`/admin`), server-side con Jinja2.

Es una superficie **de plataforma**: no resuelve tenant, no usa la sesion de
negocio y no comparte cookie con el producto. Toda su logica de negocio (activar
Pro, bloquear, auditar) vive en `app.modules.admin.service`, que es la misma que
usa el endpoint JSON legacy; aca solo se renderiza y se valida la entrada HTTP.

Reglas que sostiene este archivo y no se deben aflojar:

- Ninguna ruta fuera de `GET/POST /admin/login` responde sin sesion. Lo garantiza
  `require_admin_session`, no un `if` repetido en cada handler.
- Todo `POST` valida CSRF contra el token firmado de la sesion.
- Los errores de entrada (UUID invalido, dias fuera de 1..365) son 400 con un
  mensaje generico. El traceback y las variables de entorno nunca se renderizan.
"""

from __future__ import annotations

import hmac
import uuid
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from app.api.dependencies import get_settings_dep
from app.api.errors import RateLimitError
from app.api.routers.admin.security import (
    COOKIE_CSRF,
    COOKIE_SESION,
    RUTA_LOGIN,
    SesionAdmin,
    borrar_cookie_csrf,
    borrar_cookie_sesion,
    crear_sesion,
    crear_token_csrf,
    credenciales_validas,
    csrf_de_sesion_valido,
    leer_sesion,
    limitar_login_admin,
    panel_configurado,
    poner_cookie_csrf,
    poner_cookie_sesion,
    require_admin_session,
    token_csrf_firmado_valido,
)
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.session import get_admin_session
from app.modules.admin import service as admin_service
from app.modules.admin.service import (
    ACCION_ACTIVAR,
    ACCION_BLOQUEAR,
    ACCION_DESBLOQUEAR,
    ACCION_LOGIN,
    registrar_auditoria,
)

logger = get_logger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(prefix="/admin", tags=["Admin UI"], include_in_schema=False)

Sesion = Annotated[AsyncSession, Depends(get_admin_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
SesionAdminDep = Annotated[SesionAdmin, Depends(require_admin_session)]

FILTROS = ("todos", "trial", "pro", "bloqueados", "vencidos")

#: Etiquetas humanas del estado derivado. El estado se calcula en el service; aca
#: solo se traduce para mostrarlo.
ETIQUETAS_ESTADO = {
    "trial": "Mes gratis",
    "activo": "Pro activo",
    "vencido": "Trial vencido",
    "pro_vencido": "Pro vencido",
    "bloqueado": "Bloqueado",
}

AVISOS = {
    "pro": "Plan Pro activado.",
    "bloqueado": "Negocio bloqueado.",
    "desbloqueado": "Negocio desbloqueado.",
}


def _render_login(
    request: Request,
    settings: Settings,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    """Renderiza el login y renueva el token CSRF de double-submit."""
    configurado = panel_configurado(settings)
    token = crear_token_csrf(settings) if configurado else ""
    response = templates.TemplateResponse(
        request=request,
        name="admin/login.html",
        context={
            "csrf_token": token,
            "error": error,
            "configurado": configurado,
        },
        status_code=status_code,
    )
    if token:
        poner_cookie_csrf(response, token)
    return response


def _form_str(form: FormData, campo: str) -> str:
    valor = form.get(campo)
    return valor if isinstance(valor, str) else ""


def _destino_dashboard(filtro: str, q: str, aviso: str | None = None) -> str:
    parametros: dict[str, str] = {}
    if filtro in FILTROS and filtro != "todos":
        parametros["filtro"] = filtro
    if q:
        parametros["q"] = q[:100]
    if aviso:
        parametros["aviso"] = aviso
    return "/admin" + (f"?{urlencode(parametros)}" if parametros else "")


def _uuid_o_400(valor: str) -> uuid.UUID:
    try:
        return uuid.UUID(valor)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="Identificador invalido") from None


def _dias_o_400(valor: str) -> int:
    try:
        dias = int(valor)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Los dias deben ser un numero") from None
    if not 1 <= dias <= 365:
        raise HTTPException(status_code=400, detail="Los dias deben estar entre 1 y 365")
    return dias


async def _validar_csrf(request: Request, principal: SesionAdmin) -> FormData:
    form = await request.form()
    if not csrf_de_sesion_valido(principal, _form_str(form, "csrf_token")):
        logger.warning("admin_csrf_invalido", path=str(request.url.path))
        raise HTTPException(status_code=403, detail="Token CSRF invalido")
    return form


# --------------------------------------------------------------------------- #
# Login / logout
# --------------------------------------------------------------------------- #


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, settings: SettingsDep) -> Response:
    """Formulario de login. Si ya hay sesion, no pide credenciales de nuevo."""
    if leer_sesion(settings, request.cookies.get(COOKIE_SESION)) is not None:
        return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    return _render_login(request, settings)


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    sesion: Sesion,
    settings: SettingsDep,
) -> Response:
    """Verifica credenciales y emite la cookie de sesion firmada."""
    if not panel_configurado(settings):
        # No se evalua el formulario: sin configuracion no hay contra que comparar.
        return _render_login(
            request,
            settings,
            error="El panel no esta configurado en este entorno.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    form = await request.form()
    enviado = _form_str(form, "csrf_token")
    cookie_csrf = request.cookies.get(COOKIE_CSRF)
    csrf_ok = (
        token_csrf_firmado_valido(settings, enviado)
        and isinstance(cookie_csrf, str)
        and hmac.compare_digest(enviado.encode(), cookie_csrf.encode())
    )
    if not csrf_ok:
        logger.warning("admin_login_csrf_invalido", ip=admin_service.ip_de(request))
        return _render_login(
            request,
            settings,
            error="El formulario expiro. Volve a intentar.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        await limitar_login_admin(request, settings)
    except RateLimitError:
        logger.warning("admin_login_rate_limit", ip=admin_service.ip_de(request))
        return _render_login(
            request,
            settings,
            error="Demasiados intentos. Espera 15 minutos y volve a intentar.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    usuario = _form_str(form, "user")
    password = _form_str(form, "password")
    if not credenciales_validas(settings, usuario, password):
        logger.warning("admin_login_fallido", ip=admin_service.ip_de(request))
        return _render_login(
            request,
            settings,
            error="Usuario o contrasena incorrectos.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    valor, principal = crear_sesion(settings, settings.admin_user)
    # La auditoria del login se commitea antes de entregar la cookie: si la base
    # no acepta la fila, la sesion no se emite. Un acceso sin rastro es peor que
    # un login que falla.
    await registrar_auditoria(
        sesion,
        admin_user=principal.user,
        action=ACCION_LOGIN,
        request=request,
    )
    await sesion.commit()

    response = RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    poner_cookie_sesion(response, valor)
    borrar_cookie_csrf(response)
    logger.info("admin_login_ok", admin_user=principal.user, ip=admin_service.ip_de(request))
    return response


@router.post("/logout")
async def logout(
    request: Request,
    principal: SesionAdminDep,
) -> Response:
    """Cierra la sesion. Limpia las dos cookies del panel."""
    await _validar_csrf(request, principal)
    response = RedirectResponse(RUTA_LOGIN, status_code=status.HTTP_303_SEE_OTHER)
    borrar_cookie_sesion(response)
    borrar_cookie_csrf(response)
    logger.info("admin_logout", admin_user=principal.user)
    return response


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    principal: SesionAdminDep,
    sesion: Sesion,
    filtro: str = Query(default="todos"),
    q: str = Query(default="", max_length=100),
    aviso: str = Query(default=""),
) -> Response:
    """Lista de negocios con estadisticas, busqueda y filtros."""
    filtro_normalizado = filtro if filtro in FILTROS else "todos"
    filas = await admin_service.listar_negocios(sesion)
    visibles = admin_service.filtrar_filas(filas, filtro=filtro_normalizado, q=q)
    return templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context={
            "csrf_token": principal.csrf,
            "admin_user": principal.user,
            "filas": visibles,
            "total_filas": len(filas),
            "stats": admin_service.estadisticas(filas),
            "filtro": filtro_normalizado,
            "filtros": FILTROS,
            "q": q,
            "etiquetas_estado": ETIQUETAS_ESTADO,
            "aviso": AVISOS.get(aviso),
        },
    )


# --------------------------------------------------------------------------- #
# Acciones
# --------------------------------------------------------------------------- #


@router.post("/businesses/{business_id}/activar")
async def activar(
    business_id: str,
    request: Request,
    principal: SesionAdminDep,
    sesion: Sesion,
) -> Response:
    """Activa o renueva Pro por la cantidad de dias que pide el modal."""
    bid = _uuid_o_400(business_id)
    form = await _validar_csrf(request, principal)
    dias = _dias_o_400(_form_str(form, "dias"))
    try:
        _negocio, vencimiento = await admin_service.activar_pro(sesion, bid, dias=dias)
    except LookupError:
        raise HTTPException(status_code=404, detail="Negocio no encontrado") from None
    except RuntimeError:
        # El detalle (incluido el id) ya lo loguea el service; al cliente no se le
        # devuelve un mensaje que hable de RLS ni de la tabla.
        logger.exception("admin_activar_fallo", business_id=str(bid))
        raise HTTPException(status_code=500, detail="No se pudo activar el negocio") from None
    await registrar_auditoria(
        sesion,
        admin_user=principal.user,
        action=ACCION_ACTIVAR,
        business_id=bid,
        request=request,
    )
    await sesion.commit()
    logger.info(
        "admin_pro_activado",
        business_id=str(bid),
        dias=dias,
        pro_until=vencimiento.isoformat(),
        admin_user=principal.user,
    )
    return RedirectResponse(
        _destino_dashboard(_form_str(form, "filtro"), _form_str(form, "q"), "pro"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/businesses/{business_id}/bloquear")
async def bloquear(
    business_id: str,
    request: Request,
    principal: SesionAdminDep,
    sesion: Sesion,
) -> Response:
    bid = _uuid_o_400(business_id)
    form = await _validar_csrf(request, principal)
    try:
        await admin_service.bloquear(sesion, bid)
    except LookupError:
        raise HTTPException(status_code=404, detail="Negocio no encontrado") from None
    except RuntimeError:
        logger.exception("admin_bloquear_fallo", business_id=str(bid))
        raise HTTPException(status_code=500, detail="No se pudo bloquear el negocio") from None
    await registrar_auditoria(
        sesion,
        admin_user=principal.user,
        action=ACCION_BLOQUEAR,
        business_id=bid,
        request=request,
    )
    await sesion.commit()
    logger.info("admin_bloqueado", business_id=str(bid), admin_user=principal.user)
    return RedirectResponse(
        _destino_dashboard(_form_str(form, "filtro"), _form_str(form, "q"), "bloqueado"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/businesses/{business_id}/desbloquear")
async def desbloquear(
    business_id: str,
    request: Request,
    principal: SesionAdminDep,
    sesion: Sesion,
) -> Response:
    bid = _uuid_o_400(business_id)
    form = await _validar_csrf(request, principal)
    try:
        await admin_service.desbloquear(sesion, bid)
    except LookupError:
        raise HTTPException(status_code=404, detail="Negocio no encontrado") from None
    except RuntimeError:
        logger.exception("admin_desbloquear_fallo", business_id=str(bid))
        raise HTTPException(status_code=500, detail="No se pudo desbloquear el negocio") from None
    await registrar_auditoria(
        sesion,
        admin_user=principal.user,
        action=ACCION_DESBLOQUEAR,
        business_id=bid,
        request=request,
    )
    await sesion.commit()
    logger.info("admin_desbloqueado", business_id=str(bid), admin_user=principal.user)
    return RedirectResponse(
        _destino_dashboard(_form_str(form, "filtro"), _form_str(form, "q"), "desbloqueado"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


__all__ = ["router", "templates"]
