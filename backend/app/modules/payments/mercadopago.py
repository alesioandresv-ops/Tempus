"""Cliente de la API de Mercado Pago.

Patron: un `httpx.AsyncClient` inyectable en cada funcion (`client=None` crea uno
con la base URL de `Settings.mercadopago_api_base_url`), errores tipados con
`MercadoPagoError` y respuestas como dataclasses simples. Los tests inyectan un
client de fake a traves de `client=` y, cuando lo necesitan, reemplazan la
funcion completa con un monkeypatch.

El sandbox vs produccion lo decide el flag `sandbox` de la cuenta del negocio:
MP devuelve `init_point` y `sandbox_init_point` en las respuestas, y el servicio
elige segun el flag. No hay una URL distinta de la API por entorno.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings

MERCADOPAGO_DEFAULT_BASE_URL = "https://api.mercadopago.com"
_TIMEOUT_SECONDS = 30


class MercadoPagoError(RuntimeError):
    """Error de la API de Mercado Pago (no es un 500 interno)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class PreapprovalInfo:
    """Respuesta de create_preapproval / get_preapproval de MP."""

    preapproval_id: str
    init_point: str
    status: str
    next_billing_at: str | None = None


@dataclass
class PreferenceInfo:
    """Respuesta de create_preference de MP."""

    preference_id: str
    init_point: str
    sandbox_init_point: str | None = None


@dataclass
class PaymentInfo:
    """Respuesta de get_payment de MP."""

    payment_id: str
    status: str
    transaction_amount: float
    currency: str
    date_approved: str | None = None
    external_reference: str | None = None


def _base_url() -> str:
    settings = get_settings()
    return settings.mercadopago_api_base_url or MERCADOPAGO_DEFAULT_BASE_URL


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }


@asynccontextmanager
async def _cliente(client: httpx.AsyncClient | None) -> AsyncIterator[httpx.AsyncClient]:
    """Usa el client inyectado o crea uno desechable para la llamada."""
    if client is not None:
        yield client
        return
    async with httpx.AsyncClient(base_url=_base_url(), timeout=_TIMEOUT_SECONDS) as nuevo:
        yield nuevo


async def _send(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    access_token: str,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ejecuta una peticion a MP y levanta `MercadoPagoError` en error."""
    resp = await client.request(method, url, json=json, headers=_headers(access_token))
    if resp.status_code >= 400:
        try:
            body = resp.json()
            code = body.get("code", "")
            message = body.get("message", resp.text)
        except ValueError:
            code = ""
            message = resp.text
        detalle = f"{message} (code={code})" if code else message
        raise MercadoPagoError(detalle, status=resp.status_code)
    data: dict[str, Any] = resp.json()
    return data


async def create_preapproval(
    access_token: str,
    *,
    client: httpx.AsyncClient | None = None,
    reason: str = "Suscripcion de plan Pro",
    back_url: str = "",
    frequency: int = 1,
    frequency_type: str = "months",
    transaction_amount: float = 0.0,
    currency_id: str = "ARS",
) -> PreapprovalInfo:
    """Crea un preapproval (suscripcion recurrente) en Mercado Pago."""
    body = {
        "reason": reason,
        "back_url": back_url,
        "auto_recurring": {
            "frequency": frequency,
            "frequency_type": frequency_type,
            "transaction_amount": transaction_amount,
            "currency_id": currency_id,
        },
    }
    async with _cliente(client) as c:
        data = await _send(c, "POST", "/v1/preapproval", json=body, access_token=access_token)
    return PreapprovalInfo(
        preapproval_id=str(data["id"]),
        init_point=data.get("init_point", ""),
        status=data.get("status", "pending"),
        next_billing_at=data.get("next_payment_date"),
    )


async def get_preapproval(
    access_token: str,
    preapproval_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> PreapprovalInfo:
    """Estado actual de un preapproval."""
    async with _cliente(client) as c:
        data = await _send(
            c,
            "GET",
            f"/v1/preapproval/{preapproval_id}",
            access_token=access_token,
        )
    return PreapprovalInfo(
        preapproval_id=str(data["id"]),
        init_point=data.get("init_point", ""),
        status=data.get("status", "pending"),
        next_billing_at=data.get("next_payment_date"),
    )


async def cancel_preapproval(
    access_token: str,
    preapproval_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Cancela un preapproval (da de baja la suscripcion en MP)."""
    body = {"status": "cancelled"}
    async with _cliente(client) as c:
        await _send(
            c,
            "PUT",
            f"/v1/preapproval/{preapproval_id}",
            json=body,
            access_token=access_token,
        )


async def create_preference(
    access_token: str,
    *,
    client: httpx.AsyncClient | None = None,
    items: list[dict[str, Any]],
    external_reference: str,
    back_urls: dict[str, str],
    description: str | None = None,
) -> PreferenceInfo:
    """Crea una preference de checkout para el pago de una reserva."""
    body: dict[str, Any] = {
        "items": items,
        "external_reference": external_reference,
        "back_urls": back_urls,
        "auto_return": "all",
    }
    if description is not None:
        body["description"] = description
    async with _cliente(client) as c:
        data = await _send(
            c, "POST", "/v1/checkout/preferences", json=body, access_token=access_token
        )
    return PreferenceInfo(
        preference_id=str(data["id"]),
        init_point=data.get("init_point", ""),
        sandbox_init_point=data.get("sandbox_init_point"),
    )


async def get_payment(
    access_token: str,
    payment_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> PaymentInfo:
    """Detalle de un pago (usado por el webhook para confirmar el estado)."""
    async with _cliente(client) as c:
        data = await _send(c, "GET", f"/v1/payments/{payment_id}", access_token=access_token)
    return PaymentInfo(
        payment_id=str(data["id"]),
        status=data.get("status", "pending"),
        transaction_amount=float(data.get("transaction_amount", 0)),
        currency=data.get("currency", "ARS"),
        date_approved=data.get("date_approved"),
        external_reference=data.get("external_reference"),
    )


async def refund_payment(
    access_token: str,
    payment_id: str,
    *,
    amount: float | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Reembolsa un pago (total, o parcial si viene `amount`)."""
    body: dict[str, Any] = {}
    if amount is not None:
        body["amount"] = amount
    async with _cliente(client) as c:
        return await _send(
            c,
            "POST",
            f"/v1/payments/{payment_id}/refunds",
            json=body or None,
            access_token=access_token,
        )


__all__ = [
    "MERCADOPAGO_DEFAULT_BASE_URL",
    "MercadoPagoError",
    "PaymentInfo",
    "PreapprovalInfo",
    "PreferenceInfo",
    "cancel_preapproval",
    "create_preapproval",
    "create_preference",
    "get_payment",
    "get_preapproval",
    "refund_payment",
]
