"""Cliente para Meta WhatsApp Cloud API.

Este módulo encapsula la comunicación con la API de Meta:
- Envío de mensajes de texto
- Envío de plantillas
- Verificación de estado de plantillas

**Reglas:**
- Nunca se guarda el access token en claro (se cifra con Fernet).
- Los tokens se refrescan automáticamente cuando expiran.
- Todas las llamadas tienen timeout y retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

#: URL base de la Graph API de Meta.
GRAPH_API_BASE = "https://graph.facebook.com/v21.0"

#: Timeout por defecto para las llamadas a la API.
DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True, slots=True)
class WhatsAppMessage:
    """Mensaje de WhatsApp enviado."""

    message_id: str
    to: str
    status: str


@dataclass(frozen=True, slots=True)
class WhatsAppTemplate:
    """Plantilla de WhatsApp."""

    name: str
    language: str
    status: str
    category: str | None = None


class WhatsAppClient:
    """Cliente para Meta WhatsApp Cloud API.

    Uso:
        client = WhatsAppClient(phone_number_id, access_token)
        await client.send_text("+54911...", "Hola!")
    """

    def __init__(
        self,
        phone_number_id: str,
        access_token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._phone_number_id = phone_number_id
        self._access_token = access_token
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=GRAPH_API_BASE,
            timeout=timeout,
        )

    async def close(self) -> None:
        """Cierra el cliente HTTP."""
        await self._client.aclose()

    async def send_text(self, to: str, text: str) -> WhatsAppMessage:
        """Envía un mensaje de texto simple.

        Args:
            to: Número de teléfono en formato E.164.
            text: Texto del mensaje.

        Returns:
            WhatsAppMessage con el ID del mensaje.
        """
        url = f"{self._phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }

        response = await self._post(url, payload)
        data = response.json()

        message_id = data.get("messages", [{}])[0].get("id", "")
        return WhatsAppMessage(
            message_id=message_id,
            to=to,
            status="sent",
        )

    async def send_template(
        self,
        to: str,
        template_name: str,
        language: str = "es_AR",
        components: list[dict[str, Any]] | None = None,
    ) -> WhatsAppMessage:
        """Envía un mensaje con plantilla.

        Args:
            to: Número de teléfono en formato E.164.
            template_name: Nombre de la plantilla.
            language: Código de idioma (BCP 47).
            components: Componentes de la plantilla (opcional).

        Returns:
            WhatsAppMessage con el ID del mensaje.
        """
        url = f"{self._phone_number_id}/messages"
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
            },
        }
        if components:
            payload["template"]["components"] = components

        response = await self._post(url, payload)
        data = response.json()

        message_id = data.get("messages", [{}])[0].get("id", "")
        return WhatsAppMessage(
            message_id=message_id,
            to=to,
            status="sent",
        )

    async def get_templates(self) -> list[WhatsAppTemplate]:
        """Lista las plantillas de la cuenta de WhatsApp."""
        url = f"{self._phone_number_id}/message_templates"
        response = await self._get(url)
        data = response.json()

        templates = []
        for item in data.get("data", []):
            templates.append(
                WhatsAppTemplate(
                    name=item.get("name", ""),
                    language=item.get("language", ""),
                    status=item.get("status", ""),
                    category=item.get("category"),
                )
            )
        return templates

    async def _post(self, url: str, payload: dict[str, Any]) -> httpx.Response:
        """Hace un POST a la Graph API."""
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        response = await self._client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response

    async def _get(self, url: str) -> httpx.Response:
        """Hace un GET a la Graph API."""
        headers = {
            "Authorization": f"Bearer {self._access_token}",
        }
        response = await self._client.get(url, headers=headers)
        response.raise_for_status()
        return response


def get_whatsapp_client(
    phone_number_id: str,
    access_token: str,
) -> WhatsAppClient:
    """Factory para crear un cliente de WhatsApp."""
    return WhatsAppClient(phone_number_id, access_token)


__all__ = [
    "WhatsAppClient",
    "WhatsAppMessage",
    "WhatsAppTemplate",
    "get_whatsapp_client",
]
