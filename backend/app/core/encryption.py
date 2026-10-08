"""Secretos en reposo: cifrado Fernet para tokens de terceros.

`whatsapp_connections` guarda el access token de Meta cifrado, no en claro: un
dump de la tabla no debe dar acceso a una cuenta de WhatsApp que puede
facturar. La clave vive en `ENCRYPTION_KEY` (settings, fuera de la base) y se
valida al arrancar como clave Fernet de 32 bytes (ver `config.py`).

Un solo modulo para cifrar, descifrar y enmascarar: el enmascarado usa la
misma convencion en el endpoint y en los logs, y la alternativa -- reescribir
`Fernet(...)` en cada uso -- es la forma clasica de que un dia alguien guarde
el token en claro "porque era un helper".
"""

from __future__ import annotations

from cryptography.fernet import Fernet

from app.core.config import get_settings


def _cipher() -> Fernet:
    return Fernet(get_settings().encryption_key.get_secret_value().encode("ascii"))


def encrypt_string(plain: str) -> str:
    """Cifra un secreto. Devuelve el token Fernet, que es texto ASCII."""
    return _cipher().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_string(token: str) -> str:
    """Descifra un token Fernet guardado por `encrypt_string`."""
    return _cipher().decrypt(token.encode("ascii")).decode("utf-8")


def mask_secret(plain: str) -> str:
    """Deja ver solo los ultimos 4 caracteres de un secreto.

    El panel muestra el estado ("hay token configurado") sin exponerlo. Cuatro
    caracteres son los que se muestran en el dashboard de Meta al identificar
    la cuenta, asi que a un admin le alcanzan para confirmar que es el token
    que cree que es.
    """
    if not plain:
        return ""
    if len(plain) <= 4:
        return "••••"
    return "••••" + plain[-4:]


__all__ = ["decrypt_string", "encrypt_string", "mask_secret"]
