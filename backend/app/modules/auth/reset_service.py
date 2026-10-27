"""Flujo "olvidé mi contraseña": pedir el reset y consumirlo.

**El diseño se ata al endpoint, no al email.** La promesa de seguridad del
endpoint es "la respuesta es idéntica exista o no la cuenta": sin esa promesa
el flujo es un oráculo de enumeración de emails. La respuesta del endpoint es
siempre 200 con el mismo cuerpo, y el email sale con el mismo asunto y remitente
exista o no la cuenta (decisión consciente: silencio total como respuesta; el
costo es que un usuario que se equivoca de letra no se entera, y el beneficio es
que nadie puede usar este flujo para sondear qué correos tienen cuenta).

**Un pedido, todos los negocios.** La misma persona puede ser miembro de varios
negocios con el mismo email (la unicidad es `(business_id, email)`). El link
cubre a la **persona**: al consumir el token se rehashea la contraseña en cada
membresía con ese email y se revocan sus refresh tokens en todas ellas. Un
reset por-negocio dejaría a alguien con la clave nueva en un negocio y la vieja
en otro, que es el estado imposible de explicar por teléfono.

**Token opaco de 256 bits, hash SHA-256 en la base.** Igual que los refresh
tokens (ADR-0010): quien lea `password_reset_tokens` no puede resetear a nadie.

**Último-token-gana.** Un pedido nuevo marca los pendientes anteriores del
mismo email como usados: si alguien robó un link viejo, el nuevo pedido lo
mata antes de que pueda usarlo.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.rate_limit import WINDOW_HOUR, enforce_rate_limit
from app.core.security import generate_token, hash_password, hash_token
from app.core.time import now
from app.models.enums import MembershipStatus
from app.modules.auth.models import PasswordResetToken

logger = get_logger(__name__)

#: Ventana de validez del token. 1 hora: es el tiempo que tarda una persona en
#: ver el email y el estándar del sector; más largo solo agranda la ventana de
#: un link robado de la casilla.
TOKEN_TTL = dt.timedelta(hours=1)

#: Pedidos de reset: 3 por hora por email. Un bombardeo de casilla puede pedir
#: 60/h; con 3 el daño es acotado y quien se equivoca dos veces no se queda
#: afuera (el límite se aplica **antes** de saber si la cuenta existe, para no
#: dar un oráculo por ahí).
LIMITE_POR_EMAIL = 3

#: Por IP: 10 por hora. Cubre una oficina con una sola IP saliente y varios
#: usuarios que pierden su clave el mismo día.
LIMITE_POR_IP = 10

#: El email con el link. Lo construye el servicio y lo envía el adaptador que
#: el router le inyecta (SMTP real en producción, capturado en tests).
_ASUNTO = "Recuperá tu contraseña de Tempus"


def _normalizar(email: str) -> str:
    """El email en la forma canónica. Mismo criterio que el login."""
    return email.strip().casefold()


def _clave_email(email: str) -> str:
    """Clave del cubo de rate limit por email, hasheada (sin PII en la tabla)."""
    return hash_token(f"reset:email:{_normalizar(email)}")


def _clave_ip(ip: str) -> str:
    return hash_token(f"reset:ip:{ip}")


def _referencia(email: str) -> str:
    """Cómo se nombra un email en el log, sin escribir el email."""
    return hash_token(f"reset:log:{_normalizar(email)}")[:12]


def _cuerpo_email(nombre: str, enlace: str, minutos: int) -> str:
    """El cuerpo del email, en texto plano.

    A propósito sin HTML: un email de texto no se filtra por spam, no pide
    imágenes remotas y se lee en cualquier cliente. El enlace es el único
    dato accionable.
    """
    return (
        f"Hola {nombre}:\n\n"
        f"Recibimos un pedido para restablecer la contraseña de tu cuenta de "
        f"Tempus. Si fuiste vos, entrá a este enlace (vence en {minutos} minutos):\n\n"
        f"{enlace}\n\n"
        f"Si no fuiste vos, ignorá este mensaje: tu contraseña sigue igual y este "
        f"enlace queda inservible.\n\n"
        f"— Tempus"
    )


async def solicitar_reset(
    session: AsyncSession,
    *,
    email: str,
    ip_address: str,
    enviar_email: Callable[[str, str, str], Awaitable[None]],
) -> bool:
    """Registra el pedido de reset y delega el envío del email.

    Devuelve `True` si se creó un token y `False` si el email no pertenece a
    ninguna membresía activa. El **endpoint** convierte eso en la respuesta
    única 200 — acá solo interesa para el log y para los tests.

    `enviar_email(email, asunto, cuerpo)` lo inyecta el router: producción pasa
    el adaptador SMTP, los tests pasan uno que captura.
    """
    email_norm = _normalizar(email)

    # Los límites se aplican antes de mirar la base: si solo se contaran los
    # pedidos de cuentas existentes, el contador mismo sería el oráculo.
    await enforce_rate_limit(
        key=_clave_email(email_norm),
        limit=LIMITE_POR_EMAIL,
        window_seconds=WINDOW_HOUR,
        scope="reset:email",
        detail="Demasiados pedidos de recuperación. Reintentá en un rato.",
    )
    await enforce_rate_limit(
        key=_clave_ip(ip_address),
        limit=LIMITE_POR_IP,
        window_seconds=WINDOW_HOUR,
        scope="reset:ip",
        detail="Demasiados pedidos de recuperación. Reintentá en un rato.",
    )

    # Lookup con la funcion SECURITY DEFINER de `0004`: sin ella la RLS de
    # `business_users` devolveria cero filas, porque este endpoint corre sin
    # contexto de tenant (el tenant es justo lo que se esta resolviendo).
    filas = (
        (
            await session.execute(
                text(
                    "SELECT id, business_id, password_hash, role, status, full_name "
                    "FROM auth_business_user_for_login(:email)"
                ),
                {"email": email_norm},
            )
        )
        .mappings()
        .all()
    )
    activas = [f for f in filas if f["status"] == MembershipStatus.ACTIVE.value]
    if not activas:
        # El log es el mismo que el del caso positivo salvo el motivo: sin PII,
        # sin la cuenta de pedidos, sin nada que un atacante pueda leer.
        logger.info("reset_pedido", email_ref=_referencia(email_norm), ip=ip_address, cuenta=False)
        return False

    momento = now()

    # Último-token-gana: un pedido nuevo mata los pendientes anteriores del email.
    await session.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.email == email_norm)
        .where(PasswordResetToken.used_at.is_(None))
        .values(used_at=momento)
    )

    token = generate_token()
    session.add(
        PasswordResetToken(
            user_id=activas[0]["id"],
            email=email_norm,
            token_hash=hash_token(token),
            expires_at=momento + TOKEN_TTL,
            requested_at=momento,
            ip_address=ip_address,
        )
    )
    await session.flush()

    enlace = f"{get_settings().public_site_base_url.rstrip('/')}/reset-password?token={token}"
    cuerpo = _cuerpo_email(activas[0]["full_name"], enlace, int(TOKEN_TTL.total_seconds() // 60))

    await enviar_email(email_norm, _ASUNTO, cuerpo)
    logger.info("reset_pedido", email_ref=_referencia(email_norm), ip=ip_address, cuenta=True)
    return True


async def consumir_reset(
    session: AsyncSession,
    *,
    token: str,
    nueva_password: str,
    ip_address: str,
) -> int:
    """Consume el token y rehashea la contrasena en **todas** las membresias.

    Devuelve cuantas membresias se actualizaron. Levanta `ValueError` si el token no
    existe, está vencido o ya se usó — el endpoint traduce eso a un 400 con un
    mensaje que no distingue entre las tres causas.
    """
    momento = now()
    fila = (
        await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(token))
        )
    ).scalar_one_or_none()
    if fila is None or not fila.is_usable(momento):
        logger.info("reset_token_invalido", ip=ip_address)
        raise ValueError("El enlace no es válido o ya fue utilizado.")

    # Marcar el token y cualquier otro pendiente del mismo email como usados.
    await session.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.email == fila.email)
        .where(PasswordResetToken.used_at.is_(None))
        .values(used_at=momento)
    )

    # Cambio de contrasena + revocacion de sesiones via la funcion SECURITY
    # DEFINER de `0030`: la RLS de `business_users` no filtra por tenant aqui
    # (el reset cubre a la persona en todos sus negocios) y el rol de la app no
    # puede escribir `password_hash` por columna (0002). Una sola sentencia hace
    # las dos cosas, atomicas con el resto de la transaccion.
    afectadas = int(
        (
            await session.execute(
                text("SELECT auth_reset_password_for_email(:email, :hash)"),
                {"email": fila.email, "hash": hash_password(nueva_password)},
            )
        ).scalar_one()
    )

    # La sesión robada muere con el reset (lo hace la funcion); si no habia
    # membresias, el token no debio existir: es un estado inconsistente.
    if afectadas == 0:
        logger.warning("reset_sin_membresias", email_ref=_referencia(fila.email), ip=ip_address)

    logger.info(
        "reset_consumido",
        email_ref=_referencia(fila.email),
        ip=ip_address,
        membresias=afectadas,
    )
    return afectadas


__all__ = [
    "LIMITE_POR_EMAIL",
    "LIMITE_POR_IP",
    "TOKEN_TTL",
    "consumir_reset",
    "solicitar_reset",
]
