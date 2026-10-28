"""Envío de email saliente para la recuperación de contraseña.

**Por qué una API HTTP y no SMTP.** Render bloquea los puertos SMTP
salientes (25, 465, 587) por política anti-spam: `smtp.gmail.com:587` no es
alcanzable desde un servicio de Render y falla con `[Errno 101] Network is
unreachable`. La API de Resend viaja por HTTPS (443), que sí está abierto,
así que es el camino de producción. El SMTP queda como fallback para local
(donde sí se puede usar el Gmail de soporte con contraseña de aplicación).

**Prioridad de envío.** `RESEND_API_KEY` definida → API de Resend. Si no,
`SMTP_USER` + `SMTP_PASSWORD` → SMTP. Si no hay ninguna, el endpoint de reset
responde 200 y loguea el link en el servidor (modo prueba).

**Fallo silencioso para el usuario, ruidoso en el log.** El envío ocurre
después de que la fila del token ya se commiteó; si el envío falla, el pedido
queda registrado y el usuario tiene su respuesta — lo que falla es la entrega,
y queda en el log con el `email_ref` sin PII. Reintentar dentro del request
alargaría la respuesta después de que el usuario ya se fue a mirar la casilla.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Endpoint de la API de Resend. HTTP puro, sin SDK: una sola llamada, sin
#: dependencia nueva (httpx ya es dependencia del proyecto).
_RESEND_URL = "https://api.resend.com/emails"

#: Endpoint de la API v3 de Brevo (ex-Sendinblue). Mismo criterio: HTTPS,
#: sin SDK.
_BREVO_URL = "https://api.brevo.com/v3/smtp/email"

#: Timeout del envío: un email de reset no justifica retener el request más
#: de esto. Resend contesta en cientos de ms normalmente.
_TIMEOUT = httpx.Timeout(30.0)


def canal_disponible(settings: Settings) -> str | None:
    """Qué canal de envío hay configurado: 'brevo', 'resend', 'smtp' o None."""
    if settings.brevo_api_key.get_secret_value():
        return "brevo"
    if settings.resend_api_key.get_secret_value():
        return "resend"
    if settings.smtp_user and settings.smtp_password.get_secret_value():
        return "smtp"
    return None


def smtp_configurado(settings: Settings) -> bool:
    """Compat: ¿hay SMTP? (el router lo usa para decidir si loguea el link)."""
    return canal_disponible(settings) is not None


def _remitente(settings: Settings) -> str:
    return settings.smtp_from or settings.smtp_user


async def _enviar_brevo(
    settings: Settings,
    *,
    para: str,
    asunto: str,
    cuerpo: str,
) -> None:
    """Envía por la API v3 de Brevo (HTTPS 443, alcanzable desde Render).

    El remitente tiene que estar verificado en "Senders & IPs" del panel de
    Brevo: sin dominio propio basta confirmar el codigo que Brevo manda a la
    casilla remitente.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cliente:
        respuesta = await cliente.post(
            _BREVO_URL,
            headers={
                "api-key": settings.brevo_api_key.get_secret_value(),
                "Content-Type": "application/json",
                "accept": "application/json",
            },
            json={
                "sender": {
                    "email": settings.brevo_from_email or _remitente(settings),
                    "name": settings.brevo_from_name,
                },
                "to": [{"email": para}],
                "subject": asunto,
                "textContent": cuerpo,
            },
        )
    if respuesta.status_code >= 400:
        # El cuerpo del error de Brevo va al log: dice si el remitente no esta
        # verificado (401/400 con message puntual) o la key es invalida.
        logger.error(
            "brevo_rechazo",
            status=respuesta.status_code,
            cuerpo=respuesta.text[:300],
        )
        respuesta.raise_for_status()


async def _enviar_resend(
    settings: Settings,
    *,
    para: str,
    asunto: str,
    cuerpo: str,
) -> None:
    """Envía por la API de Resend (HTTPS 443, alcanzable desde Render)."""
    remitente = settings.resend_from or _remitente(settings)
    mensaje = {"from": remitente, "to": [para], "subject": asunto, "text": cuerpo}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cliente:
        respuesta = await cliente.post(
            _RESEND_URL,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            json=mensaje,
        )
    if respuesta.status_code >= 400:
        # El cuerpo del error de Resend va al log: dice si el dominio no está
        # verificado (403) o la key es invalida (401), que son las dos fallas
        # de configuracion posibles.
        logger.error(
            "resend_rechazo",
            status=respuesta.status_code,
            cuerpo=respuesta.text[:300],
        )
        respuesta.raise_for_status()


def _enviar_smtp_sync(settings: Settings, mensaje: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_password.get_secret_value())
        smtp.send_message(mensaje)


async def _enviar_smtp(
    settings: Settings,
    *,
    para: str,
    asunto: str,
    cuerpo: str,
) -> None:
    """Envía por SMTP (solo alcanza en local; Render bloquea 587 saliente)."""
    mensaje = EmailMessage()
    mensaje["From"] = _remitente(settings)
    mensaje["To"] = para
    mensaje["Subject"] = asunto
    mensaje.set_content(cuerpo)
    await asyncio.to_thread(_enviar_smtp_sync, settings, mensaje)


async def enviar_email(
    settings: Settings,
    *,
    para: str,
    asunto: str,
    cuerpo: str,
) -> None:
    """Envía un email de texto plano por el canal disponible.

    Levanta excepción si el envío falla: quien llama decide qué hacer
    (el router de reset lo loguea y no lo propaga).
    """
    canal = canal_disponible(settings)
    if canal == "brevo":
        await _enviar_brevo(settings, para=para, asunto=asunto, cuerpo=cuerpo)
    elif canal == "resend":
        await _enviar_resend(settings, para=para, asunto=asunto, cuerpo=cuerpo)
    elif canal == "smtp":
        await _enviar_smtp(settings, para=para, asunto=asunto, cuerpo=cuerpo)
    else:
        raise RuntimeError("Sin canal de email configurado (ni RESEND_API_KEY ni SMTP)")
    logger.info("email_enviado", para_ref=para[:3] + "***", asunto=asunto, canal=canal)


async def enviar_email_seguro(
    settings: Settings,
    *,
    para: str,
    asunto: str,
    cuerpo: str,
) -> None:
    """Como `enviar_email`, pero traga la excepción dejándola en el log.

    Es el adaptador que el router pasa a `solicitar_reset`: el fallo de
    entrega no puede cambiar la respuesta del endpoint.
    """
    try:
        await enviar_email(settings, para=para, asunto=asunto, cuerpo=cuerpo)
    except Exception:
        # Excepción amplia a propósito: cubre httpx.HTTPError, OSError (la
        # "[Errno 101] Network is unreachable" de Render contra SMTP) y
        # smtplib.SMTPException. Registrar y seguir es la política acordada;
        # distinguir tipos no cambia la acción.
        logger.exception("email_fallo", destino=para[:3] + "***")


__all__ = ["canal_disponible", "enviar_email", "enviar_email_seguro", "smtp_configurado"]
