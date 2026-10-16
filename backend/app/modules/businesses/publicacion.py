"""Regla unica de publicacion de un negocio (plan Pro + ventana del mes gratis).

Antes esta decision estaba repartida y ademas desalineada: `Business.is_bookable`
miraba solo `status == 'active'`, el resolver publico repetia el `status ==
'active'` en su propio `SELECT`, y `es_negocio_reservable` delegaba en el modelo.
Cuando la publicabilidad pasa a depender tambien del plan y del tiempo, tener tres
lugares que la deciden es tener tres oportunidades de que digan cosas distintas: el
panel y la pagina publica terminarian habilitando negocios diferentes.

Esta funcion es la unica que sabe la regla. El modelo, el servicio, el router
publico y la guardia del panel (`api.routers.business.gating`) la llaman; ninguno
la reimplementa.

**La regla:**

    publicable = status == ACTIVE  Y  (plan == PRO  O  hoy < fin_del_trial)

`status` es estado administrativo (una plataforma puede suspender un negocio y no se
publica aunque tenga plan Pro). El plan y la fecha son la regla de negocio. Separar
las dos responsabilidades es lo que permite que `status` siga significando lo que
dice y que el corte del mes gratis no dependa de una columna que se quedaria mintiendo.

La guardia del panel no usa `es_publicable` entera sino `mes_gratis_vencido`--la
mitad que no mira `status`--porque a un suspendido con Pro vigente no se le puede
decir "tu mes gratis vencio".

**El piso del trial** es `max(created_at, trial_grace_from)`. Sin ese piso, activar
el corte deja vencidos el primer dia a todos los negocios que ya existian, porque su
`created_at` es viejo. Con `trial_grace_from` en epoch (el default) el piso no hace
nada y el trial corre desde el alta; con la fecha de despliegue, protege a los que
ya estaban.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.core.time import now
from app.models.enums import BusinessPlan, BusinessStatus

if TYPE_CHECKING:  # pragma: no cover - solo para el tipador
    from app.modules.businesses.models import Business


def inicio_del_trial(business: Business) -> dt.datetime:
    """Desde cuando corre el mes gratis: `max(created_at, trial_grace_from)`."""
    return max(business.created_at, get_settings().trial_grace_from)


def fin_del_trial(business: Business) -> dt.datetime:
    """Cuando termina el mes gratis."""
    return inicio_del_trial(business) + dt.timedelta(days=get_settings().trial_dias)


def dias_restantes(business: Business, *, momento: dt.datetime | None = None) -> int:
    """Dias enteros que le quedan al mes gratis. Nunca negativo.

    Se calcula con el mismo `fin_del_trial` que decide `es_publicable`, para que el
    numero que ve el dueno en el panel y la puerta que lo deja pasar no se
    desincronicen.
    """
    restante = fin_del_trial(business) - (momento or now())
    return max(0, restante.days)


def es_publicable(business: Business, *, momento: dt.datetime | None = None) -> bool:
    """Si el negocio acepta reservas online en este instante.

    `momento` existe para los tests: pasan un instante y no dependen del reloj real.
    """
    if business.status != BusinessStatus.ACTIVE:
        return False
    return not mes_gratis_vencido(business, momento=momento)


def mes_gratis_vencido(business: Business, *, momento: dt.datetime | None = None) -> bool:
    """Si el mes gratis ya se agoto, **sin mirar el status administrativo**.

    Es la mitad de `es_publicable` que le importa a la guardia del panel: un
    negocio vencido puede seguir leyendo (y pagando), pero no escribiendo. Se
    separa de `es_publicable` a proposito, porque esa tambien mira `status`: a un
    negocio suspendido con Pro vigente no se le puede decir "tu mes gratis vencio",
    que seria mentira, y a un suspendido no se le abre la escritura por estar
    dentro del mes.

    Un plan Pro nunca esta vencido, aunque el mes original haya pasado.
    """
    if business.plan == BusinessPlan.PRO:
        return False
    return not ((momento or now()) < fin_del_trial(business))


__all__ = [
    "dias_restantes",
    "es_publicable",
    "fin_del_trial",
    "inicio_del_trial",
    "mes_gratis_vencido",
]
