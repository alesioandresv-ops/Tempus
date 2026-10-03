"""Servicio de negocios: lectura y actualizacion de la configuracion del tenant.

Vive en el servicio y no en el router por una razon que ya se pago una vez en este
proyecto: el handler no debe ser el que decide que campos se pueden cambiar. Un
`PATCH` que armase su propio `UPDATE` seria un `PATCH` al que se le puede anadir un
campo por descuido y saltarse las validaciones.

**`businesses` no tiene RLS.** Es la tabla global que identifica al tenant, asi que
para leerla no hace falta GUC. Pero `tempus_app` tiene **solo `SELECT`** sobre ella
(`has_table_privilege('tempus_app','businesses','UPDATE')` es `false`), asi que
tampoco se puede escribir con la sesion de la app. De ahi la division real:

- leer configuracion y resolver el tenant: sesion normal;
- **crear** un negocio: sesion de migraciones (ver `onboarding.py` del router).

La creacion de tenants es la unica operacion del producto que necesita mas
privilegios que operar dentro de un tenant, y por eso vive aislada en su propio
modulo en vez de repartirse en el CRUD de servicios.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import NotFoundError, ValidationError
from app.models.enums import BusinessStatus
from app.modules.businesses.models import Business

#: Campos que un administrador puede cambiar sobre su negocio. Es una lista
#: blanca explicita y no "todos los que no sean id/created_at": con una lista negra,
#: agregar una columna al modelo la vuelve editable de inmediato sin que nadie lo
#: decida.
EDITABLE_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "timezone",
        "locale",
        "currency",
        "phone_e164",
        "address_text",
        "brand_color",
        "slot_interval_minutes",
        "min_lead_minutes",
        "max_advance_days",
        "cancellation_window_minutes",
    }
)

#: Longitudes de un slug. El minimo importa mas de lo que parece: los enlaces
#: publicos son `/b/{slug}` y van en pantallas de celular, en mails y en QR codes.
SLUG_MIN_LENGTH = 3
SLUG_MAX_LENGTH = 48

#: Un slug es `[a-z0-9-]`. Sin acentos ni mayusculas, porque el mismo negocio se
#: escribe de varias maneras y `/b/pelu-rio` y `/b/PeluRío` tienen que ser el mismo
#: lugar. Las reglas de normalizacion estan en `slugify`.
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Formatos de color aceptados. Tres porque los tres aparecen en la practica: el
#: picker del panel devuelve `#RRGGBB`, algunos temas pasan `rgb(...)` y las
#: plantillas de marca traen el nombre. Un color mal escrito se ve en la pagina
#: publica de cada cliente, asi que se valida al guardar y no al pintar.
COLOR_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
COLOR_RGB = re.compile(r"^rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\)$")
COLOR_NAMES = frozenset(
    {
        "black",
        "white",
        "red",
        "blue",
        "green",
        "yellow",
        "orange",
        "purple",
        "pink",
        "gray",
        "grey",
        "brown",
        "cyan",
        "magenta",
        "teal",
        "navy",
    }
)

LOCALE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2,3})?$")

MONEDA_PATTERN = re.compile(r"^[A-Z]{3}$")


def slugify(texto: str) -> str:
    """Convierte un nombre de negocio en un slug utilizable como URL.

    El pipeline es: NFD para separar los acentos, se descarta lo que no es
    combinante, se baja a minusculas, y los separadores se vuelven guion. Sin el
    paso de NFD el resultado seria `peluqueria-caf` a partir de "Peluquería Café", y
    con el slug como clave unica del negocio eso son dos negocios distintos para el
    mismo nombre.

    Un texto que se reduce a nada (por ejemplo "!!!") devuelve cadena vacia, y el
    llamador decide que hacer. Devolver un slug inventado seria peor: el usuario
    veria una URL que no eligio y que despues no podria cambiar sin romper enlaces.
    """
    sin_acentos = unicodedata.normalize("NFD", texto)
    solo_letras = "".join(c for c in sin_acentos if not unicodedata.combining(c))
    minusculas = solo_letras.lower()
    reemplazos = re.sub(r"[^a-z0-9]+", "-", minusculas)
    return reemplazos.strip("-")


def validar_slug(slug: str) -> str:
    """Valida un slug ya normalizado. Devuelve el slug o levanta `ValidationError`."""
    if not SLUG_PATTERN.match(slug):
        raise ValidationError(
            "El slug solo puede tener letras minusculas, numeros y guiones, "
            "y no puede empezar ni terminar con guion."
        )
    if not SLUG_MIN_LENGTH <= len(slug) <= SLUG_MAX_LENGTH:
        raise ValidationError(
            f"El slug debe tener entre {SLUG_MIN_LENGTH} y {SLUG_MAX_LENGTH} caracteres."
        )
    return slug


async def get_business(session: AsyncSession, business_id: uuid.UUID) -> Business:
    """Negocio por id, o `NotFoundError`.

    `businesses` es global y sin RLS, asi que esta lectura funciona con cualquier
    sesion. El id normalmente sale del claim `tid` del token, no de la peticion: si
    el handler lo tomara de la URL, un token del tenant A podria leer y escribir el
    tenant B siempre que los endpoints aceptaran el id.
    """
    result = await session.execute(select(Business).where(Business.id == business_id))
    business = result.scalar_one_or_none()
    if business is None:
        raise NotFoundError("Negocio no encontrado.")
    return business


async def get_business_by_slug(session: AsyncSession, slug: str) -> Business | None:
    """Negocio por slug, o `None`. Sin RLS, asi que no necesita GUC de tenant."""
    result = await session.execute(select(Business).where(Business.slug == slug))
    return result.scalar_one_or_none()


async def slug_disponible(
    session: AsyncSession, slug: str, *, excluir: uuid.UUID | None = None
) -> bool:
    """Si el slug esta libre.

    `exclude_business_id` importa en la edicion: sin el, el negocio que esta por
    cambiar su propio slug siempre veria el suyo como ocupado y no podria guardarlo.
    """
    validar_slug(slug)
    query = select(Business.id).where(Business.slug == slug)
    if excluir is not None:
        query = query.where(Business.id != excluir)
    result = await session.execute(query.limit(1))
    return result.first() is None


def _validar_color(valor: str) -> str:
    """Acepta `#rgb`, `#rrggbb`, `rgb(...)` o nombre de color. Devuelve el original.

    No se normaliza a un unico formato a proposito: el panel y las plantillas de
    marca mandan formatos distintos, y convertirlos obligaria al que llama a
    conocer la conversion. Lo que no se acepta es un color que el navegador no
    pueda pintar, que es el error que de verdad duele -- sale en la pagina publica
    de cada cliente y no en el panel del administrador.
    """
    candidato = valor.strip()
    if COLOR_HEX.match(candidato) or COLOR_RGB.match(candidato):
        return candidato
    if candidato.lower() in COLOR_NAMES:
        return candidato.lower()
    raise ValidationError(
        "El color no es valido. Usa #rgb, #rrggbb, rgb(r,g,b) o un nombre de color."
    )


def _validar_timezone(valor: str) -> str:
    """Verifica que el timezone exista en la base IANA.

    Un timezone inventado no falla al guardarse: falla cuando la disponibilidad
    calcula `local_date` y explota con `ZoneInfoNotFoundError` en el medio de una
    reserva. Un cambio de configuracion tiene que rechazar un valor que va a romper
    el producto, no solo anotarlo.
    """
    try:
        ZoneInfo(valor)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ValidationError(f"Timezone desconocido: {valor}.") from None
    return valor


def _validar_locale(valor: str) -> str:
    """Un locale tipo `es-AR`. Se valida la forma, no la existencia.

    El idioma de los mensajes se elige por este valor y hay fallback a `es-AR`, asi
    que un locale raro no rompe nada: se valida la forma para no guardar typos como
    `esAR`, que es indistinguible del correcto en el selector del panel.
    """
    if not LOCALE_PATTERN.match(valor):
        raise ValidationError("El locale debe tener forma de idioma y pais, por ejemplo es-AR.")
    return valor


def _validar_moneda(valor: str) -> str:
    candidato = valor.strip().upper()
    if not MONEDA_PATTERN.match(candidato):
        raise ValidationError("La moneda debe ser un codigo ISO 4217 de 3 letras, por ejemplo ARS.")
    return candidato


def _validar_cambios(cambios: dict[str, Any]) -> dict[str, Any]:
    """Valida los valores antes de escribir. Devuelve los valores ya saneados."""
    validados = dict(cambios)
    if "brand_color" in validados and validados["brand_color"] is not None:
        validados["brand_color"] = _validar_color(str(validados["brand_color"]))
    if "timezone" in validados:
        validados["timezone"] = _validar_timezone(str(validados["timezone"]))
    if "locale" in validados:
        validados["locale"] = _validar_locale(str(validados["locale"]))
    if "currency" in validados:
        validados["currency"] = _validar_moneda(str(validados["currency"]))
    return validados


async def actualizar_configuracion(
    session: AsyncSession,
    business_id: uuid.UUID,
    cambios: dict[str, Any],
) -> Business:
    """Aplica `cambios` sobre la configuracion del negocio.

    `session.flush()`, nunca `commit()`: el commit es de la dependencia que abrio la
    sesion. Un `commit()` aca parte la transaccion en dos, y si el handler falla
    despues el cambio ya quedo aplicado a medias.

    Los campos ausentes no se tocan. Es lo que hace que `PATCH` sea `PATCH` y no un
    `PUT`: el panel manda solo los campos que el usuario toco, y enviar `null` para
    los demas significaria borrar la direccion o el telefono cada vez que se
    cambia el nombre.
    """
    if not cambios:
        return await get_business(session, business_id)

    desconocidos = sorted(set(cambios) - EDITABLE_FIELDS)
    if desconocidos:
        # Se levanta antes de tocar nada. Si se ignoraran en silencio, un typo en
        # el nombre del campo seria un 200 con el valor viejo, que es el peor
        # resultado posible para quien esta configurando su negocio.
        raise ValidationError(
            f"Campos no editables: {', '.join(desconocidos)}.",
            extra={"campos_no_editables": desconocidos},
        )

    business = await get_business(session, business_id)
    for campo, valor in _validar_cambios(cambios).items():
        setattr(business, campo, valor)

    await session.flush()
    return business


def cambiar_slug(session_business: Business, nuevo_slug: str) -> str:
    """Fija el slug del negocio, ya validado y ya comprobado como libre.

    El chequeo de disponibilidad lo hace el router **antes** de llamar. Aqui solo
    queda la asignacion, porque un `IntegrityError` de la unica global seria una
    carrera: dos negocios pidiendo el mismo slug a la vez pasan los dos el
    `SELECT` y despues uno recibe un 500.
    """
    session_business.slug = validar_slug(nuevo_slug)
    return session_business.slug


def es_negocio_reservable(business: Business) -> bool:
    """Si el negocio acepta reservas publicas.

    Delegado a la propiedad del modelo en vez de repetir el `status == 'active'`
    aca: si el modelo cambia la regla, este modulo dejaria de estar de acuerdo sin
    que ninguna prueba lo note.
    """
    return business.status == BusinessStatus.ACTIVE


__all__ = [
    "COLOR_HEX",
    "COLOR_NAMES",
    "COLOR_RGB",
    "EDITABLE_FIELDS",
    "SLUG_MAX_LENGTH",
    "SLUG_MIN_LENGTH",
    "SLUG_PATTERN",
    "actualizar_configuracion",
    "cambiar_slug",
    "es_negocio_reservable",
    "get_business",
    "get_business_by_slug",
    "slug_disponible",
    "slugify",
    "validar_slug",
]
