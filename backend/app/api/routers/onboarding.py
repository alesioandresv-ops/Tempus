"""Router de onboarding: el alta self-service de un negocio.

Es **pre-tenant**: no hay `business_id` todavia, asi que no hay GUC que poner y la
sesion que usa es la global, la misma que usa el login. Por eso vive en su propio
router y no dentro de `business`: mezclarlo con el panel autenticado haria creer que
es una ruta de negocio, y no lo es.

**El router no lleva `prefix`.** Sus dos endpoints cuelgan de prefijos distintos--el
alta es de autenticacion y el chequeo de slug es de la superficie publica--y las
dos opciones de colgarlo tendrian un costo: meter el chequeo en el modulo `public`
lo haria agir como si fuera suyo, y meter el alta en `public` la expondría en el
lugar donde vive la superficie que **no** necesita cuenta. Con los paths
completos y el `prefix` en el `include` de `main.py`, cada ruta queda donde cae
por su naturaleza.

Los dos endpoints viven juntos porque son las dos mitades del mismo formulario: sin
el chequeo en vivo, el alta no se puede escribir sin ver el 409.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_session,
    limit_public_per_ip,
    limit_register_business_per_ip,
)
from app.api.routers.business.schemas import SlugDisponibleOut
from app.api.routers.schemas import BusinessRegisterRequest, NegocioRegistradoOut
from app.core.logging import get_logger
from app.modules.businesses import service as negocios
from app.modules.onboarding.service import registrar_negocio

logger = get_logger(__name__)

#: Sin `prefix` a proposito; ver el docstring del modulo. El `prefix` real lo agrega
#: el `include_router` de `main.py`.
router = APIRouter(tags=["Onboarding"])

#: Sesion global, sin tenant. Es la correcta aca: `businesses` es la unica tabla
#: global que se escribe, y las otras tres del alta son del tenant que todavia no
#: existe--al que se le pone el GUC recien de escrito el negocio.
Sesion = Annotated[AsyncSession, Depends(get_session)]

#: Chequeo de slug en vivo, por la funcion privilegiada de `0014`.
#:
#: **No se usa `negocios.slug_disponible`**, que solo mira `businesses`. La lista de
#: palabras reservadas--`admin`, `panel`, `api`--vive en `slug_reservations`, y el
#: rol de la app no tiene ni permiso de lectura sobre esa tabla (`FORBIDDEN_FOR_APP_ROLE`).
#: Con el chequeo del panel el formulario responderia "disponible" para `admin` y el
#: 409 apareceria recien al enviar, que es justo lo que el chequeo en vivo evita.
_SLUG_DISPONIBLE = text("SELECT tempus_slug_disponible(CAST(:slug AS citext)) AS disponible")


@router.post(
    "/auth/register-business",
    status_code=status.HTTP_201_CREATED,
    response_model=NegocioRegistradoOut,
    summary="Alta self-service de negocio",
    responses={
        201: {"description": "Negocio creado"},
        409: {"description": "El nombre de URL ya esta en uso o esta reservado"},
        # 422 y no 400: es lo que produce `ValidationError` para la zona horaria y
        # lo que produce el esquema de pydantic para el resto de los campos. Un
        # 400 aca seria un estado que este producto no emite nunca.
        422: {"description": "Datos invalidos"},
        429: {"description": "Rate limit excedido"},
    },
    dependencies=[Depends(limit_register_business_per_ip)],
)
async def register_business(
    payload: BusinessRegisterRequest,
    sesion: Sesion,
) -> NegocioRegistradoOut:
    """Crea negocio + dueno + profesional + horarios, atomico, y responde 201.

    **No devuelve tokens.** El alta no abre sesion: el dueno entra por `/login` con
    las credenciales que acaba de elegir. La razon de no emitirlo aca es que el
    limite de este endpoint es de 3 por hora y por IP--un token de paso-- y porque
    emitirlo obliga a resolver el tenant recien creado en la misma peticion, que es
    el trabajo que `authenticate_business_user` ya sabe hacer con una fila que existe.

    El limite va **antes** de tocar la base de negocio, por la misma razon que en el
    login: cada intento cuesta un Argon2 completo, asi que si fuera lo ultimo seria
    un detalle economico y no una barrera.
    """
    resultado = await registrar_negocio(
        sesion,
        business_name=payload.business_name,
        slug=payload.slug,
        owner_name=payload.owner_name,
        email=payload.email,
        password=payload.password,
        phone=payload.phone,
        timezone=payload.timezone,
    )
    logger.info(
        "negocio_registrado",
        business_ref=str(resultado.business_id)[:8],
        slug=resultado.slug,
    )
    return NegocioRegistradoOut(
        business_id=resultado.business_id,
        slug=resultado.slug,
        mensaje="Negocio registrado. Inicia sesion para entrar al panel.",
    )


@router.get(
    "/public/slug-disponible",
    response_model=SlugDisponibleOut,
    summary="Comprobar si un nombre de URL esta libre",
    dependencies=[Depends(limit_public_per_ip)],
)
async def comprobar_slug(
    sesion: Sesion,
    slug: Annotated[
        str,
        Query(min_length=1, max_length=120, description="Texto a normalizar como slug"),
    ],
) -> SlugDisponibleOut:
    """Para el selector de URL mientras se escribe.

    **Responde 200 con `disponible: false` y no 409**, igual que el chequeo
    equivalente del panel: el frontend lo consulta en cada tecla y un error por cada
    slug tomado seria ruido de consola, no informacion. El 409 queda para el envio
    del alta, que es donde el conflicto importa.

    El texto entra crudo y se normaliza con la misma `slugify` que el alta, porque
    quien escribe "Barbería El Peluche" tiene que ver la URL que va a quedar, no un
    rechazo por mayusculas.
    """
    candidato = negocios.slugify(slug)
    if not candidato:
        return SlugDisponibleOut(slug="", disponible=False)

    resultado = await sesion.execute(_SLUG_DISPONIBLE, {"slug": candidato})
    return SlugDisponibleOut(slug=candidato, disponible=bool(resultado.scalar_one()))


__all__ = ["router"]
