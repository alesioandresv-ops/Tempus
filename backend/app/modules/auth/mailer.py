"""Envío de email saliente: SMTP con el Gmail de soporte.

**Por qué `smtplib` de la stdlib en un hilo, y no `aiosmtplib`.** El flujo de
recuperación manda un email por pedido y el límite de pedidos es 3/h por
cuenta: una dependencia nueva para tres emails por hora no se justifica.
`asyncio.to_thread` libera el event loop y el error de red se ve igual que
cualquier otro fallo del envío.

**Gmail SMTP con Contraseña de Aplicación.** La contraseña va en
`SMTP_PASSWORD` (contraseña de aplicación de 16 letras que genera Google, no
la de la cuenta). Gmail exige STARTTLS en el puerto 587, que es lo que hace
`starttls()`.

**Fallo silencioso para el usuario, ruidoso para el log.** El envío ocurre
después de commitear la fila del token (el router commitea antes de llamar);
si el SMTP falla, el usuario ya tiene su respuesta 200 y el pedido queda
registrado — lo que falla es solo la entrega, y se registra con el
`email_ref` sin PII. Reintentar dentro del request alargaría la respuesta
antes de decirle al usuario "mirá tu casilla", que ya es una mentira.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def smtp_configurado(settings: Settings) -> bool:
    """¿Hay credenciales de SMTP? Si no, el router loguea el link en el server."""
    return bool(settings.smtp_user) and bool(settings.smtp_password.get_secret_value())


def _remitente(settings: Settings) -> str:
    return settings.smtp_from or settings.smtp_user


async def enviar_email(
    settings: Settings,
    *,
    para: str,
    asunto: str,
    cuerpo: str,
) -> None:
    """Envía un email de texto plano. Levanta excepción si el SMTP falla.

    Quien llama decide qué hacer con el fallo: el router de reset lo loguea y
    no lo propaga (la respuesta al usuario ya no depende de esto).
    """
    mensaje = EmailMessage()
    mensaje["From"] = _remitente(settings)
    mensaje["To"] = para
    mensaje["Subject"] = asunto
    mensaje.set_content(cuerpo)

    def _enviar_sync() -> None:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password.get_secret_value())
            smtp.send_message(mensaje)

    await asyncio.to_thread(_enviar_sync)
    logger.info("email_enviado", para_ref=para[:3] + "***", asunto=asunto)


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
        # Excepción amplia a propósito: smtplib lanza clases propias (SMTPException,
        # SMTPAuthenticationError) y OSError cubre timeouts y DNS. Registrar y
        # seguir es la política acordada; distinguir tipos no cambia la acción.
        logger.exception("email_fallo", destino=para[:3] + "***")


__all__ = ["enviar_email", "enviar_email_seguro", "smtp_configurado"]
