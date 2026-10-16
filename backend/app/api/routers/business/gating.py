"""Guardia de plan del panel: corta las **escrituras** cuando el mes gratis vencio.

Vive aparte del router por dos razones:

1. Es una dependencia **transversal**: aplica a todas las rutas de escritura del
   panel (negocio, servicios, horarios, reservas, bloqueos...) y no a una en
   particular, asi que no tiene sentido repetirla handler por handler.
2. El router necesita **omitirla en un subconjunto**: los endpoints de
   suscripcion. Un negocio vencido tiene que poder pagar, y eso empieza por poder
   llamar a `/business/suscripcion/activar`, que tambien es una escritura.

El corte es por **metodo HTTP** y no por una lista de endpoints: `POST`/`PUT`/
`PATCH`/`DELETE` son escrituras y se bloquean; `GET`/`HEAD`/`OPTIONS` no. Mantener
una lista de rutas permitidas seria una lista que se desactualiza con cada
endpoint nuevo; el metodo no se olvida.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_tenant_session, require_principal
from app.api.errors import PaymentRequiredError
from app.core.config import get_settings
from app.modules.auth.tokens import Principal
from app.modules.businesses import publicacion
from app.modules.businesses.models import Business

#: Metodos que no cambian nada. La guardia los deja pasar: un negocio vencido tiene
#: que poder leer su agenda y, sobre todo, entrar a la pantalla de pago.
_METODOS_DE_LECTURA = frozenset({"GET", "HEAD", "OPTIONS"})


def url_de_pago() -> str:
    """A donde mandar al dueno a pagar: la pestana de Pagos del panel.

    Sale de `public_site_base_url`--la misma base que los `back_urls` del checkout
    de MP--y no de una URL escrita a mano, para que el link funcione igual en
    local, en staging y en produccion sin tocar codigo.
    """
    base = get_settings().public_site_base_url.rstrip("/")
    return f"{base}/admin?tab=pagos"


async def exigir_plan_vigente(
    request: Request,
    sesion: Annotated[AsyncSession, Depends(get_tenant_session)],
    principal: Annotated[Principal, Depends(require_principal)],
) -> None:
    """Levanta 402 en una escritura si el mes gratis del negocio ya vencio.

    **Solo escrituras.** Bloquear la lectura dejaria al dueno sin poder ver que
    pasa ni llegar a pagar: un mes vencido no es una sesion invalida, es un negocio
    que tiene que pasar a Pro.

    El `402`--y no `403`--es lo que el frontend distingue para abrir la pantalla de
    pago en vez de "no tenes permiso". El `code="plan_vencido"` viaja en el cuerpo
    porque el status por si solo no dice **cual** pago falta, y el `checkout_url`
    para que un cliente que no es el navegador sepa a donde ir.
    """
    if request.method in _METODOS_DE_LECTURA:
        return
    if principal.business_id is None:
        # `get_tenant_session` ya rechazo los tokens de plataforma con un 403; este
        # `return` es para el tipador, no una rama de runtime.
        return

    negocio = await sesion.get(Business, principal.business_id)
    if negocio is None or not publicacion.mes_gratis_vencido(negocio):
        return

    raise PaymentRequiredError(
        "El mes gratis terminó. Activá el plan Pro para seguir cargando datos.",
        extra={
            "code": "plan_vencido",
            "trial_ends_at": publicacion.fin_del_trial(negocio).isoformat(),
            "dias_restantes": publicacion.dias_restantes(negocio),
            "checkout_url": url_de_pago(),
        },
    )


__all__ = ["exigir_plan_vigente", "url_de_pago"]
