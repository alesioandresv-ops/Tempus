"""Configuracion de WhatsApp por negocio: lectura, guardado y resolucion de plantillas.

El opt-in de Fase D-3 vive en `whatsapp_connections`, una fila por negocio.
"No hay fila" **es** `whatsapp_activo = false`: es el default con el que nace
todo negocio, y activar los recordatorios es crear/actualizar esa fila. No se
agrega una columna a `businesses` porque seria una segunda fuente de verdad
para las mismas credenciales --la tabla de conexion ya las guarda con el token
cifrado con Fernet, que es la decision que el proyecto tomo en `0001`.

**Que significa "activo".** Una conexion sirve si existe, `is_active` es
`true` y `status` es `active`; cualquier otro estado (fila ausente,
desactivada, pendiente de conectar) es "inactivo" para la outbox, que saltea
el envio con un motivo claro en vez de fallar. El admin puede apagar los
recordatorios con `activo=false` **sin borrar** el token: volver a encender es
un click, no volver a pegar credenciales.

**Resolucion de plantilla por tipo de aviso**, en orden de precedencia:

- `reminder_24h`: plantilla del negocio, o `recordatorio_24h`.
- `reminder_2h`: plantilla del negocio, o `recordatorio_2h`.
- `booking_confirmed`: la plantilla de plataforma
  `WHATSAPP_PLATFORM_TEMPLATE_CONFIRMATION`, o nada (la confirmacion no es
  parte de Fase D-3; si la plataforma no la configuro, se saltea con motivo).

Cualquier otro `kind` (cancelacion, reprogramacion, aviso al profesional)
queda fuera del alcance de la fase y se saltea con su motivo. La tabla de
conexion guarda solo los dos nombres de plantilla que la fase configura.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ValidationError
from app.core.config import get_settings
from app.core.encryption import decrypt_string, encrypt_string, mask_secret
from app.core.time import now
from app.models.enums import NotificationKind, WhatsAppConnectionStatus
from app.modules.notifications.models import WhatsAppConnection

#: Nombres de plantilla por defecto (Fase D-3). Meta las aprueba por nombre;
#: estos son los que el negocio registra salvo que elija otros.
TEMPLATE_24H_DEFAULT = "recordatorio_24h"
TEMPLATE_2H_DEFAULT = "recordatorio_2h"


@dataclass(frozen=True, slots=True)
class WhatsappConfig:
    """Lo que el panel ve de la configuracion. Token nunca en claro."""

    activo: bool
    phone_number_id: str | None
    token_ultimos: str | None
    reminder_24h_template: str
    reminder_2h_template: str
    #: Datos publicos de la app de Meta de la plataforma, para que el frontend
    #: pueda abrir el flujo de Conectar WhatsApp (Fase 0.5). Vacios = el flujo
    #: no esta disponible porque la plataforma no configuro la app.
    meta_app_id: str | None = None
    connect_config_id: str | None = None


def plantilla_de(kind: str, conexion: WhatsAppConnection | None) -> str | None:
    """El nombre de plantilla con que enviar un `kind`, o `None` si no hay.

    `None` no es un error de configuracion en si mismo: es "este tipo de aviso
    no tiene plantilla definida todavia", y la outbox lo traduce a un `skipped`
    con el motivo correspondiente.
    """
    if kind == NotificationKind.REMINDER_24H.value:
        if conexion is not None and conexion.reminder_24h_template:
            return conexion.reminder_24h_template
        return TEMPLATE_24H_DEFAULT
    if kind == NotificationKind.REMINDER_2H.value:
        if conexion is not None and conexion.reminder_2h_template:
            return conexion.reminder_2h_template
        return TEMPLATE_2H_DEFAULT
    if kind == NotificationKind.BOOKING_CONFIRMED.value:
        return get_settings().whatsapp_platform_template_confirmation or None
    return None


async def _conexion(session: AsyncSession, business_id: uuid.UUID) -> WhatsAppConnection | None:
    """La conexion del negocio, o `None` si nunca se configuro."""
    resultado = await session.execute(
        select(WhatsAppConnection).where(WhatsAppConnection.business_id == business_id)
    )
    return resultado.scalar_one_or_none()


async def obtener_config(session: AsyncSession, business_id: uuid.UUID) -> WhatsappConfig:
    """El estado actual. Sin fila en la tabla, responde el default inactivo."""
    settings = get_settings()
    conexion = await _conexion(session, business_id)
    if conexion is None:
        return WhatsappConfig(
            activo=False,
            phone_number_id=None,
            token_ultimos=None,
            reminder_24h_template=TEMPLATE_24H_DEFAULT,
            reminder_2h_template=TEMPLATE_2H_DEFAULT,
            meta_app_id=settings.meta_app_id or None,
            connect_config_id=settings.meta_embedded_signup_config_id or None,
        )

    token = (
        decrypt_string(conexion.access_token_encrypted) if conexion.access_token_encrypted else None
    )
    return WhatsappConfig(
        activo=conexion.can_send,
        phone_number_id=conexion.phone_number_id,
        token_ultimos=mask_secret(token) if token else None,
        reminder_24h_template=conexion.reminder_24h_template or TEMPLATE_24H_DEFAULT,
        reminder_2h_template=conexion.reminder_2h_template or TEMPLATE_2H_DEFAULT,
        meta_app_id=settings.meta_app_id or None,
        connect_config_id=settings.meta_embedded_signup_config_id or None,
    )


async def guardar_config(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    activo: bool,
    phone_number_id: str | None,
    access_token: str | None,
    reminder_24h_template: str | None,
    reminder_2h_template: str | None,
) -> WhatsappConfig:
    """Crea o actualiza la conexion del negocio. Idempotente.

    `access_token=None` conserva el token actual (si lo hay); recien entonces
    se puede encender sin volver a pegar el secreto. `phone_number_id=None` y
    las plantillas `None` conservan lo que haya. Desactivar (`activo=false`)
    conserva las credenciales para reactivar con un click.
    """
    conexion = await _conexion(session, business_id)

    if conexion is None:
        if not activo and not phone_number_id and not access_token:
            # Nada que persistir: piden el estado por defecto.
            return WhatsappConfig(
                activo=False,
                phone_number_id=None,
                token_ultimos=None,
                reminder_24h_template=TEMPLATE_24H_DEFAULT,
                reminder_2h_template=TEMPLATE_2H_DEFAULT,
            )
        conexion = WhatsAppConnection(
            business_id=business_id,
            phone_number_id=phone_number_id or "",
            access_token_encrypted=(encrypt_string(access_token) if access_token else ""),
            reminder_24h_template=reminder_24h_template or None,
            reminder_2h_template=reminder_2h_template or None,
            is_active=activo,
            status=WhatsAppConnectionStatus.ACTIVE,
        )
        session.add(conexion)
    else:
        if phone_number_id is not None:
            conexion.phone_number_id = phone_number_id
        if access_token is not None:
            conexion.access_token_encrypted = encrypt_string(access_token)
        conexion.is_active = activo
        conexion.status = WhatsAppConnectionStatus.ACTIVE
        if reminder_24h_template is not None:
            conexion.reminder_24h_template = reminder_24h_template or None
        if reminder_2h_template is not None:
            conexion.reminder_2h_template = reminder_2h_template or None

    # Filtros de carga rapida: encender con credenciales incompletas crearia una
    # conexion "activa" que el worker saltearia siempre, y el panel mostraria
    # "Activo" con recordatorios que nunca salen.
    if activo and not conexion.phone_number_id:
        raise ValidationError(
            "Para activar los recordatorios falta el Phone Number ID de WhatsApp."
        )
    if activo and not conexion.access_token_encrypted:
        raise ValidationError("Para activar los recordatorios falta el token de acceso.")

    await session.flush()
    return await obtener_config(session, business_id)


async def guardar_conexion_pendiente(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    access_token: str,
    expires_in: int | None,
    waba_id: str,
    display_phone: str | None = None,
    phone_number_id: str | None = None,
    quality_rating: str | None = None,
    messaging_limit_tier: str | None = None,
) -> WhatsappConfig:
    """Persiste el resultado del connect de Meta (Fase 0.5) como `pending`.

    El connect trae el WABA y el token, y **todavia no** el numero: registrar el
    telefono exige el chip con el SMS de verificacion de Meta. Se guarda la
    conexion desactivada y `pending` con el token cifrado --la outbox la saltea,
    y el dia que llega el chip el negocio repite el connect (o pega el Phone
    Number ID en el PUT de configuracion y activa el switch) y pasa a `active`.

    Idempotente: repetir el connect sobre el mismo WABA actualiza la fila en
    vez de duplicarla. Si el WABA ya pertenece a **otro** negocio, el
    `UniqueConstraint("waba_id")` falla con `IntegrityError`, que el router
    traduce a un conflicto (409).

    Si la conexion estaba **activa** (re-autenticacion de un negocio con numero
    registrado, por ejemplo porque el token expiro), refrescar el token no la
    apaga: conserva `is_active` y `status`. Si estaba inactiva o pendiente,
    vuelve a `pending` hasta que se active con el switch.
    """
    conexion = await _conexion(session, business_id)
    ahora = now()
    expira = ahora.replace(tzinfo=dt.UTC) + timedelta(seconds=expires_in) if expires_in else None

    if conexion is None:
        conexion = WhatsAppConnection(
            business_id=business_id,
            phone_number_id=phone_number_id or "",
            waba_id=waba_id,
            display_phone=display_phone,
            access_token_encrypted=encrypt_string(access_token),
            quality_rating=quality_rating,
            messaging_limit_tier=messaging_limit_tier,
            is_active=False,
            status=WhatsAppConnectionStatus.PENDING,
            connected_at=ahora,
            token_expires_at=expira,
        )
        session.add(conexion)
    else:
        conexion.waba_id = waba_id
        conexion.access_token_encrypted = encrypt_string(access_token)
        if display_phone is not None:
            conexion.display_phone = display_phone
        if phone_number_id:
            conexion.phone_number_id = phone_number_id
        if quality_rating is not None:
            conexion.quality_rating = quality_rating
        if messaging_limit_tier is not None:
            conexion.messaging_limit_tier = messaging_limit_tier
        if not conexion.can_send:
            conexion.is_active = False
            conexion.status = WhatsAppConnectionStatus.PENDING
        conexion.connected_at = ahora
        conexion.token_expires_at = expira

    await session.flush()
    return await obtener_config(session, business_id)


__all__ = [
    "TEMPLATE_2H_DEFAULT",
    "TEMPLATE_24H_DEFAULT",
    "WhatsappConfig",
    "guardar_conexion_pendiente",
    "guardar_config",
    "obtener_config",
    "plantilla_de",
]
