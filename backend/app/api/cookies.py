"""Utilidades centralizadas para manejo de cookies de autenticación.

Centraliza la creación y eliminación de la cookie de refresh token para evitar
duplicación de atributos y garantizar configuración consistente en todos los
endpoints de autenticación.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Response

from app.core.config import Settings


def set_refresh_cookie(
    response: Response,
    token: str,
    expires_at: datetime,
    settings: Settings,
) -> None:
    """Establece la cookie de refresh token con atributos de seguridad.

    Args:
        response: Respuesta FastAPI donde se adjuntará la cookie.
        token: Token de refresh en claro (solo sale aquí una vez).
        expires_at: Momento de expiración del token (UTC).
        settings: Configuración de la aplicación para atributos de la cookie.
    """
    # Calcular max-age en segundos desde ahora hasta expires_at
    # expires_at ya es UTC aware
    now = datetime.now(UTC)
    max_age = int((expires_at - now).total_seconds())
    if max_age < 0:
        max_age = 0

    # Validación de configuración de seguridad
    if settings.environment.is_production:
        if not settings.cookie_secure:
            raise ValueError("COOKIE_SECURE debe ser true en producción")
        if settings.cookie_samesite.lower() == "none" and not settings.cookie_secure:
            raise ValueError("COOKIE_SAMESITE=none requiere COOKIE_SECURE=true")

    response.set_cookie(
        key="refresh_token",
        value=token,
        max_age=max_age,
        path=settings.cookie_path,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite.lower(),
        # domain se omite intencionalmente: cookie host-only
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    """Elimina la cookie de refresh token.

    Establece max_age=0 y una fecha en el pasado para forzar su eliminación
    inmediata en el navegador.
    """
    response.set_cookie(
        key="refresh_token",
        value="",
        max_age=0,
        path=settings.cookie_path,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite.lower(),
        expires=0,
        # domain se omite intencionalmente
    )
