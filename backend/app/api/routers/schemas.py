"""Schemas de entrada del router de autenticacion y del alta self-service.

Viven en `routers/schemas.py` y no junto al router que los usa porque los consumen
dos: el login los usa `routers/auth.py` y el alta los usa `routers/onboarding.py`.
Duplicarlos seria dos lugares que se desactualizan en silencio cuando un campo
cambia de nombre.
"""

from __future__ import annotations

import re
import uuid

from pydantic import BaseModel, EmailStr, Field, field_validator

#: Telefono en formato E.164 sin el `+`: `5491123456789`.
#:
#: **Se valida el formato y no la existencia.** Un SMS de verificacion de numero
#: real seria la unica forma de probar que el numero existe, y no hay numero de
#: prueba ni costo gratis para eso. Lo que se garantiza es que el formato sirve
#: para WhatsApp, que es lo que Tempus necesita: `secrets` y cuentas que no son
#: E.164 no se pueden entregar a Meta.
PHONE_E164_RE = re.compile(r"[1-9]\d{6,14}")


class LoginRequest(BaseModel):
    """Credenciales de login: email + password."""

    email: EmailStr
    password: str


class BusinessLoginRequest(LoginRequest):
    """Credenciales de login de negocio, con el slug como desambiguador opcional.

    El mismo email puede ser miembro de varios negocios--una persona que trabaja para
    dos,-- y sin slug el login no puede saber a cual entrar. La opcion por defecto es
    **fallar** con el mismo 401 de siempre en vez de adivinar, porque entrar "a la
    primera" es entrar a un tenant arbitrario.

    El slug solo desempata: no agrega tenants alcanzables y no se valida como 404. Un
    slug que no corresponde y uno que no existen dan el mismo 401 que una contrasena
    mala, precisamente para que la respuesta no diga si un negocio existe. Por eso es
    opcional y no obligatorio--con una sola membresia se ignora--, y por eso no lleva
    `min_length`: una cadena vacia se canoniza a nada y se trata como "no vino", que
    con una sola membresia es un 200 y con varias es el mismo 401 de siempre. Validarla
    aca solo agregaria un 422--un codigo que el cliente final no puede distinguir de los
    de credenciales-- sin cambiar a quien se le puede entrar.
    """

    business_slug: str | None = Field(default=None, max_length=50)


class BusinessRegisterRequest(BaseModel):
    """Alta self-service de un negocio con su owner.

    Los limites de longitud no son decorativos: los replica el modelo, y un 422 de
    Pydantic es un mensaje que el formulario puede mostrar tal cual, mientras que
    un `ValueError` de PostgreSQL en la misma columna es un 500 con un detalle que
    el cliente final nunca deberia ver.

    `slug` acepta mayusculas y espacios para que el formulario pueda autocompletar
    desde el nombre del negocio; la normalizacion a la forma canonica la hace el
    servicio, que es el unico lugar donde vive esa decision.
    """

    business_name: str = Field(min_length=2, max_length=200)
    slug: str = Field(min_length=3, max_length=50)
    owner_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    # El tope del **input**, no el de la columna. Lo que se guarda--E.164 sin `+`-- no
    # pasa de 15 digitos y la columna es `Text`, pero lo que llega tiene espacios,
    # guiones y un `+`: `+54 9 11 2345-6789` son dieciocho caracteres. Con un tope de
    # 16 el numero que escribe una persona--el unico formato que se copia de un
    # celular--era un 422, y el mensaje que llegaba era el de "string too long", que no
    # le dice a nadie que hay que sacar los espacios. 32 es holgado para cualquier
    # numero real y deja margen a los parentesis y los espacios que la gente pega.
    phone: str | None = Field(default=None, max_length=32)
    timezone: str = Field(default="America/Argentina/Buenos_Aires", max_length=64)

    @field_validator("phone")
    @classmethod
    def _normaliza_telefono(cls, value: str | None) -> str | None:
        """Deja el telefono en E.164 sin `+`, o `None` si no viene.

        Se acepta lo que una persona pega de verdad --`+54 9 11 2345-6789`-- porque
        exigirle que escriba el E.164 a mano es una barrera en el onboarding sin
        ningun beneficio. Todo lo que no sea un digito se descarta, y lo que queda
        se valida: un numero que no es E.164 no sirve para WhatsApp, y es mejor
        rechazarlo en el formulario que en el envio de la primera notificacion.
        """
        if value is None:
            return None
        digitos = "".join(ch for ch in value if ch.isdigit())
        if not digitos:
            raise ValueError("El telefono no tiene digitos")
        if PHONE_E164_RE.fullmatch(digitos) is None:
            raise ValueError("El telefono no tiene un formato internacional valido")
        return digitos


class NegocioRegistradoOut(BaseModel):
    """Lo que el alta devuelve.

    **No incluye tokens.** El alta deja al dueno en `/login` en vez de devolverle
    una sesion, y la respuesta dice eso en `mensaje` para que el frontend no tenga
    que hardcodear el texto.

    `business_id` y `slug` van los dos porque son identidades distintas: el id es la
    identidad interna y estable--no cambia si el negocio renombra su URL-- y el slug
    es la publica y mutable.
    """

    business_id: uuid.UUID
    slug: str
    mensaje: str


__all__ = [
    "PHONE_E164_RE",
    "BusinessLoginRequest",
    "BusinessRegisterRequest",
    "LoginRequest",
    "NegocioRegistradoOut",
]
