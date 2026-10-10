"""Configuracion de pagos por negocio: credenciales de MP y politica de seña.

Sigue el patron de `app.modules.notifications.config` (Fase D-3): funciones async
que reciben la `AsyncSession` del tenant (ya con el GUC `app.current_business_id`
puesto por el dependency de la request) y el `business_id`. El access token se
guarda cifrado con Fernet; el panel solo ve los ultimos caracteres via
`mask_secret`, nunca el token en claro.

La politica de pago vive en columnas de `businesses` (`plan`, `payment_policy`,
`deposit_mode`, `deposit_value`, `deposit_non_refundable`) y no en una tabla
aparte: es estado del negocio, no una entidad, y el downgrade a `free` es un
UPDATE de esa misma columna.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt_string, encrypt_string, mask_secret
from app.models.enums import DepositMode, PaymentPolicy
from app.modules.businesses.models import Business
from app.modules.payments.models import MpPaymentAccount


def encrypt_access_token(plain: str) -> str:
    """Cifra el access token con Fernet. Devuelve el token ASCII."""
    return encrypt_string(plain)


def decrypt_access_token(token: str) -> str:
    """Descifra un access token guardado."""
    return decrypt_string(token)


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _vista_cuenta(cuenta: MpPaymentAccount) -> dict[str, Any]:
    """Lo que el panel puede ver: nunca el token en claro."""
    token = decrypt_string(cuenta.access_token_encrypted)
    return {
        "activo": cuenta.can_process_payments,
        "business_id": str(cuenta.business_id),
        "sandbox": cuenta.sandbox,
        "token_masked": mask_secret(token) if token else None,
        "has_token": bool(cuenta.access_token_encrypted),
        "created_at": _iso(cuenta.created_at),
        "updated_at": _iso(cuenta.updated_at),
    }


async def _cuenta(session: AsyncSession, business_id: uuid.UUID) -> MpPaymentAccount | None:
    resultado = await session.execute(
        select(MpPaymentAccount).where(MpPaymentAccount.business_id == business_id)
    )
    return resultado.scalar_one_or_none()


async def obtener_cuenta(session: AsyncSession, business_id: uuid.UUID) -> dict[str, Any] | None:
    """Estado (enmascarado) de la cuenta de MP, o `None` si nunca se configuro."""
    cuenta = await _cuenta(session, business_id)
    return _vista_cuenta(cuenta) if cuenta is not None else None


async def guardar_cuenta(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    access_token: str | None,
    public_key: str | None = None,
    sandbox: bool | None = None,
    is_active: bool = True,
) -> dict[str, Any]:
    """Crea o actualiza la cuenta de MP del negocio. Idempotente.

    `access_token=None` conserva el token guardado (se puede apagar y volver a
    encender sin volver a pegar el secreto). Al crear la fila el token es
    obligatorio: una cuenta sin credencial no sirve para nada.
    """
    cuenta = await _cuenta(session, business_id)

    if cuenta is None:
        if not access_token:
            raise ValueError("Falta el access token para conectar Mercado Pago.")
        cuenta = MpPaymentAccount(
            business_id=business_id,
            access_token_encrypted=encrypt_string(access_token),
            public_key=public_key,
            sandbox=True if sandbox is None else sandbox,
            is_active=is_active,
        )
        session.add(cuenta)
    else:
        if access_token is not None:
            cuenta.access_token_encrypted = encrypt_string(access_token)
        if public_key is not None:
            cuenta.public_key = public_key
        if sandbox is not None:
            cuenta.sandbox = sandbox
        cuenta.is_active = is_active

    await session.flush()
    return _vista_cuenta(cuenta)


async def _negocio(session: AsyncSession, business_id: uuid.UUID) -> Business | None:
    # `businesses` no lleva RLS (es la raiz del aislamiento) y el rol de la app
    # tiene SELECT sobre ella: se puede leer con la sesion del tenant.
    resultado = await session.execute(select(Business).where(Business.id == business_id))
    return resultado.scalar_one_or_none()


def _vista_politica(negocio: Business) -> dict[str, Any]:
    return {
        "policy": negocio.payment_policy,
        "deposit_mode": negocio.deposit_mode,
        "deposit_value": (
            float(negocio.deposit_value) if negocio.deposit_value is not None else None
        ),
        "deposit_non_refundable": negocio.deposit_non_refundable,
    }


async def obtener_politica_pago(
    session: AsyncSession, business_id: uuid.UUID
) -> dict[str, Any] | None:
    """Politica de pago configurada, o `None` si el negocio no existe."""
    negocio = await _negocio(session, business_id)
    return _vista_politica(negocio) if negocio is not None else None


async def guardar_politica_pago(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    policy: str,
    deposit_mode: str | None = None,
    deposit_value: float | None = None,
    deposit_non_refundable: bool = False,
) -> dict[str, Any]:
    """Guarda la politica de pago del negocio, validando la coherencia.

    `none` y `full` no llevan `deposit_mode` ni `deposit_value`; `deposit` exige
    ambos y un `percent` no puede pasar de 100. El error es un `ValueError` con
    el motivo, que el endpoint traduce a 422.
    """
    negocio = await _negocio(session, business_id)
    if negocio is None:
        raise ValueError("El negocio no existe.")

    if policy == PaymentPolicy.NONE:
        if deposit_mode is not None or deposit_value is not None:
            raise ValueError("La politica 'none' no admite deposit_mode ni deposit_value.")
        deposit_mode_final: DepositMode | None = None
        deposit_value_final: float | None = None
    elif policy == PaymentPolicy.DEPOSIT:
        if deposit_mode not in (DepositMode.FIXED, DepositMode.PERCENT):
            raise ValueError("La politica 'deposit' exige deposit_mode 'fixed' o 'percent'.")
        if deposit_value is None or deposit_value <= 0:
            raise ValueError("La politica 'deposit' exige un deposit_value mayor a 0.")
        if deposit_mode == DepositMode.PERCENT and deposit_value > 100:
            raise ValueError("Un deposito porcentual no puede superar el 100%.")
        deposit_mode_final = DepositMode(deposit_mode)
        deposit_value_final = deposit_value
    elif policy == PaymentPolicy.FULL:
        if deposit_mode is not None or deposit_value is not None:
            raise ValueError(
                "La politica 'full' no admite deposit_mode ni deposit_value: se cobra el total."
            )
        deposit_mode_final = None
        deposit_value_final = None
    else:
        raise ValueError(f"Politica de pago invalida: {policy!r}.")

    negocio.payment_policy = PaymentPolicy(policy)
    negocio.deposit_mode = deposit_mode_final
    negocio.deposit_value = (
        Decimal(str(deposit_value_final)) if deposit_value_final is not None else None
    )
    negocio.deposit_non_refundable = deposit_non_refundable

    await session.flush()
    return _vista_politica(negocio)


def calcular_monto_servicio(
    *,
    base_amount: float,
    policy: PaymentPolicy,
    deposit_mode: DepositMode | None,
    deposit_value: float | None,
    deposit_non_refundable: bool,
) -> dict[str, Any]:
    """Monto a cobrar al reservar segun la politica del negocio.

    Devuelve `total`, `deposit_amount` (lo que se cobra ahora), `remaining` (lo
    que queda por cobrar en el local) y `non_refundable_deposit` (la parte que no
    se devuelve si el negocio marco la seña como no reembolsable).
    """
    total = round(base_amount, 2)

    if policy == PaymentPolicy.NONE:
        deposit_amount = 0.0
    elif policy == PaymentPolicy.FULL:
        deposit_amount = total
    else:
        # El unico valor restante del enum es DEPOSIT.
        if deposit_mode == DepositMode.PERCENT:
            deposit_amount = round(total * (deposit_value or 0.0) / 100.0, 2)
        else:
            deposit_amount = round(deposit_value or 0.0, 2)

    deposit_amount = min(deposit_amount, total)
    return {
        "total": total,
        "deposit_amount": deposit_amount,
        "remaining": round(total - deposit_amount, 2),
        "non_refundable_deposit": deposit_amount if deposit_non_refundable else 0.0,
    }


__all__ = [
    "calcular_monto_servicio",
    "decrypt_access_token",
    "encrypt_access_token",
    "guardar_cuenta",
    "guardar_politica_pago",
    "obtener_cuenta",
    "obtener_politica_pago",
]
