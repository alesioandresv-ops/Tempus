"""Cliente para almacenamiento S3-compatible (Cloudflare R2).

Este módulo encapsula la subida y descarga de archivos:
- Imágenes de negocio (logo, cover)
- Imágenes de profesionales

**Reglas:**
- Nunca se guardan bytes en PostgreSQL (solo la clave del objeto).
- Las URLs firmadas expiran (no son permanentes).
- Los tokens de acceso nunca se guardan en claro.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: URL firmada expira en 1 hora por defecto.
DEFAULT_URL_EXPIRY_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class UploadResult:
    """Resultado de una subida."""

    object_key: str
    url: str
    size_bytes: int
    content_type: str


@dataclass(frozen=True, slots=True)
class PresignedUrl:
    """URL firmada para upload o descarga."""

    url: str
    expires_at: dt.datetime
    fields: dict[str, str] | None = None  # Para POST policies


class StorageClient:
    """Cliente para S3-compatible storage (R2, MinIO, etc.).

    Uso:
        client = StorageClient(endpoint, bucket, access_key, secret_key)
        result = await client.upload("logo.png", b"...", "image/png")
    """

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        *,
        region: str = "auto",
        timeout: float = 30.0,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=self._endpoint,
            timeout=timeout,
        )

    async def close(self) -> None:
        """Cierra el cliente HTTP."""
        await self._client.aclose()

    async def upload(
        self,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> UploadResult:
        """Sube un objeto al bucket.

        Args:
            object_key: Clave del objeto (ej: "businesses/123/logo.png").
            data: Bytes del archivo.
            content_type: MIME type.

        Returns:
            UploadResult con la clave y URL del objeto.
        """
        # TODO: Implementar subida real con firma AWS SigV4
        # Por ahora, retornamos un mock
        logger.info(
            "storage_upload",
            object_key=object_key,
            size_bytes=len(data),
            content_type=content_type,
        )

        url = f"{self._endpoint}/{self._bucket}/{object_key}"
        return UploadResult(
            object_key=object_key,
            url=url,
            size_bytes=len(data),
            content_type=content_type,
        )

    async def download(self, object_key: str) -> bytes:
        """Descarga un objeto del bucket.

        Args:
            object_key: Clave del objeto.

        Returns:
            Bytes del archivo.
        """
        # TODO: Implementar descarga real
        logger.info("storage_download", object_key=object_key)
        return b""

    async def delete(self, object_key: str) -> None:
        """Elimina un objeto del bucket.

        Args:
            object_key: Clave del objeto.
        """
        # TODO: Implementar eliminación real
        logger.info("storage_delete", object_key=object_key)

    async def generate_presigned_url(
        self,
        object_key: str,
        *,
        expires_in: int = DEFAULT_URL_EXPIRY_SECONDS,
        method: str = "get",
    ) -> PresignedUrl:
        """Genera una URL firmada para acceso temporal.

        Args:
            object_key: Clave del objeto.
            expires_in: Duración en segundos.
            method: Método HTTP ("get" o "put").

        Returns:
            PresignedUrl con la URL y expiración.
        """
        # TODO: Implementar firma AWS SigV4 real
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_in)
        url = f"{self._endpoint}/{self._bucket}/{object_key}"

        logger.info(
            "storage_presigned_url",
            object_key=object_key,
            method=method,
            expires_at=expires_at.isoformat(),
        )

        return PresignedUrl(
            url=url,
            expires_at=expires_at,
        )


def get_storage_client() -> StorageClient:
    """Factory para crear un cliente de storage desde la config."""
    settings = get_settings()
    return StorageClient(
        endpoint=settings.r2_endpoint,
        bucket=settings.r2_bucket,
        access_key=settings.r2_access_key_id,
        secret_key=settings.r2_secret_access_key.get_secret_value(),
    )


__all__ = [
    "PresignedUrl",
    "StorageClient",
    "UploadResult",
    "get_storage_client",
]
