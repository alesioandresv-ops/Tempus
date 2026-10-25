"""Logica compartida del panel administrativo.

La usan el panel HTML (`/admin`) y el endpoint JSON legacy (`/api/v1/admin`). Tener
una sola implementacion de "activar Pro" y "bloquear" evita que las dos superficies
digan cosas distintas sobre el mismo negocio, que es exactamente el tipo de drift
que ya costo caro en la regla de publicabilidad.

La regla de RLS no se toca: antes de cada `UPDATE` sobre `businesses` se fija
`app.current_business_id` con `set_config(..., true)`. El filtro lo sigue haciendo
la politica `businesses_write`, no el `WHERE` de Python.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import uuid
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.time import now
from app.models.enums import BusinessPlan, BusinessStatus
from app.modules.admin.models import AdminAuditLog
from app.modules.businesses.models import Business
from app.modules.businesses.publicacion import dias_restantes, fin_del_trial
from app.modules.payments.pro_models import ProPayment, ProPaymentStatus

logger = get_logger(__name__)

#: Acciones que quedan en `admin_audit_log`. Es una lista cerrada para que la
#: columna `action` no se vuelva un texto libre que despues nadie puede agrupar.
ACCION_ACTIVAR = "activar_pro"
ACCION_BLOQUEAR = "bloquear"
ACCION_DESBLOQUEAR = "desbloquear"
ACCION_APROBAR_PAGO = "aprobar_pago"
ACCION_RECHAZAR_PAGO = "rechazar_pago"
ACCION_LOGIN = "login"


@dataclass(frozen=True, slots=True)
class FilaNegocio:
    """Proyeccion de un negocio para la tabla del panel."""

    id: uuid.UUID
    business_name: str
    slug: str
    email: str
    plan: str
    estado: str
    pro_until: dt.datetime | None
    trial_ends_at: dt.datetime
    trial_dias_restantes: int
    ultimo_pago_status: str | None

    @property
    def activo(self) -> bool:
        return self.estado == "activo"

    @property
    def bloqueado(self) -> bool:
        return self.estado == "bloqueado"


def estado_de(negocio: Business) -> str:
    """Estado que muestra el panel.

    No es una columna: se deriva de `plan`, `pro_until`, `status` y la ventana del
    mes gratis. La fuente del corte es `publicacion.fin_del_trial`; no se
    reimplementa la regla.
    """
    if negocio.plan is BusinessPlan.PRO:
        if negocio.pro_until is not None and negocio.pro_until <= now():
            return "pro_vencido"
        return "activo"
    if negocio.status is BusinessStatus.SUSPENDED:
        return "bloqueado"
    if now() >= fin_del_trial(negocio):
        return "vencido"
    return "trial"


def proyectar(negocio: Business, pago: ProPayment | None = None) -> FilaNegocio:
    """Convierte un `Business` en la fila que renderiza el panel."""
    fin = fin_del_trial(negocio)
    return FilaNegocio(
        id=negocio.id,
        business_name=negocio.name,
        slug=negocio.slug,
        email=pago.email if pago is not None else "",
        plan=str(negocio.plan),
        estado=estado_de(negocio),
        pro_until=negocio.pro_until,
        trial_ends_at=fin,
        trial_dias_restantes=dias_restantes(negocio),
        ultimo_pago_status=str(pago.status) if pago is not None else None,
    )


async def listar_negocios(sesion: AsyncSession) -> list[FilaNegocio]:
    """Todos los negocios con el email del ultimo comprobante, si lo hay.

    Se traen todos de una vez a proposito: el panel es de plataforma, no de un
    tenant, y el volumen de negocios no justifica paginar antes de que exista la
    necesidad. La busqueda y los filtros se aplican en memoria para no duplicar la
    regla de estado en SQL.
    """
    negocios = list(
        (await sesion.execute(select(Business).order_by(Business.created_at.desc())))
        .scalars()
        .all()
    )
    pagos: dict[uuid.UUID, ProPayment] = {}
    for pago in (
        (await sesion.execute(select(ProPayment).order_by(ProPayment.created_at.desc())))
        .scalars()
        .all()
    ):
        pagos.setdefault(pago.business_id, pago)
    return [proyectar(negocio, pagos.get(negocio.id)) for negocio in negocios]


def _normalizar_busqueda(q: str) -> str:
    return q.strip().casefold()[:100]


def filtrar_filas(filas: list[FilaNegocio], *, filtro: str, q: str) -> list[FilaNegocio]:
    """Aplica el filtro de estado y la busqueda por slug o nombre."""
    visibles = filas
    if filtro == "trial":
        visibles = [fila for fila in visibles if fila.estado == "trial"]
    elif filtro == "pro":
        visibles = [fila for fila in visibles if fila.estado == "activo"]
    elif filtro == "bloqueados":
        visibles = [fila for fila in visibles if fila.estado == "bloqueado"]
    elif filtro == "vencidos":
        visibles = [fila for fila in visibles if fila.estado in ("vencido", "pro_vencido")]

    busqueda = _normalizar_busqueda(q)
    if busqueda:
        visibles = [
            fila
            for fila in visibles
            if busqueda in fila.slug.casefold() or busqueda in fila.business_name.casefold()
        ]
    return visibles


def estadisticas(filas: list[FilaNegocio]) -> dict[str, int]:
    """Contadores de la cabecera del panel."""
    return {
        "total": len(filas),
        "trial": sum(1 for fila in filas if fila.estado == "trial"),
        "pro_activos": sum(1 for fila in filas if fila.estado == "activo"),
        "bloqueados": sum(1 for fila in filas if fila.estado == "bloqueado"),
        "vencidos": sum(1 for fila in filas if fila.estado in ("vencido", "pro_vencido")),
        "pendientes_pago": sum(
            1 for fila in filas if fila.ultimo_pago_status == ProPaymentStatus.PENDING.value
        ),
    }


async def set_tenant_para_escritura(sesion: AsyncSession, business_id: uuid.UUID) -> None:
    """Fija el GUC que exige la politica `businesses_write`.

    `set_config(..., true)` es local a la transaccion. Es el unico SQL textual del
    panel: el valor no se interpola en la sentencia, viaja como parametro, y la
    alternativa (un `SET` de sesion) dejaria el tenant filtrado a la conexion
    siguiente del pool.
    """
    await sesion.execute(
        text("SELECT set_config('app.current_business_id', :bid, true)"),
        {"bid": str(business_id)},
    )


async def _negocio_para_escribir(sesion: AsyncSession, business_id: uuid.UUID) -> Business:
    negocio = await sesion.get(Business, business_id)
    if negocio is None:
        raise LookupError("negocio inexistente")
    await set_tenant_para_escritura(sesion, business_id)
    return negocio


async def activar_pro(
    sesion: AsyncSession, business_id: uuid.UUID, *, dias: int
) -> tuple[Business, dt.datetime]:
    """Activa/renueva Pro por `dias`, sumando sobre la vigencia si sigue activa."""
    if not 1 <= dias <= 365:
        raise ValueError("dias fuera de rango")
    negocio = await _negocio_para_escribir(sesion, business_id)
    base = (
        negocio.pro_until
        if negocio.plan is BusinessPlan.PRO
        and negocio.pro_until is not None
        and negocio.pro_until > now()
        else now()
    )
    nuevo_vencimiento = base + dt.timedelta(days=dias)
    resultado = await sesion.execute(
        update(Business)
        .where(Business.id == business_id)
        .values(
            plan=BusinessPlan.PRO,
            pro_until=nuevo_vencimiento,
            status=BusinessStatus.ACTIVE,
        )
    )
    if resultado.rowcount == 0:
        # Si la RLS bloquea, el UPDATE afecta cero filas y no lanza. Reportarlo
        # como error es lo que evita que el panel diga "listo" sin haber escrito.
        raise RuntimeError(f"RLS bloqueo el UPDATE de businesses id={business_id}")
    negocio.plan = BusinessPlan.PRO
    negocio.pro_until = nuevo_vencimiento
    negocio.status = BusinessStatus.ACTIVE
    return negocio, nuevo_vencimiento


async def bloquear(sesion: AsyncSession, business_id: uuid.UUID) -> Business:
    negocio = await _negocio_para_escribir(sesion, business_id)
    resultado = await sesion.execute(
        update(Business).where(Business.id == business_id).values(status=BusinessStatus.SUSPENDED)
    )
    if resultado.rowcount == 0:
        raise RuntimeError(f"RLS bloqueo el UPDATE de businesses id={business_id}")
    negocio.status = BusinessStatus.SUSPENDED
    return negocio


async def desbloquear(sesion: AsyncSession, business_id: uuid.UUID) -> Business:
    negocio = await _negocio_para_escribir(sesion, business_id)
    resultado = await sesion.execute(
        update(Business).where(Business.id == business_id).values(status=BusinessStatus.ACTIVE)
    )
    if resultado.rowcount == 0:
        raise RuntimeError(f"RLS bloqueo el UPDATE de businesses id={business_id}")
    negocio.status = BusinessStatus.ACTIVE
    return negocio


def ip_de(request: Request) -> str | None:
    """IP del cliente en forma valida para una columna `INET`.

    Se lee `request.client.host` y no `X-Forwarded-For`, por la misma razon que el
    rate limiting: una cabecera que el cliente controla no es una fuente de IP.
    """
    cliente = request.client
    if cliente is None or not cliente.host:
        return None
    try:
        return str(ipaddress.ip_address(cliente.host))
    except ValueError:
        return None


async def registrar_auditoria(
    sesion: AsyncSession,
    *,
    admin_user: str,
    action: str,
    business_id: uuid.UUID | None = None,
    request: Request | None = None,
) -> None:
    """Agrega la fila de auditoria. El commit lo decide el llamador."""
    sesion.add(
        AdminAuditLog(
            admin_user=admin_user[:200],
            action=action,
            business_id=business_id,
            ip=ip_de(request) if request is not None else None,
        )
    )


async def buscar_por_slug_o_nombre(
    sesion: AsyncSession, *, q: str, filtro: str
) -> list[FilaNegocio]:
    """Variante ORM para el endpoint JSON legacy.

    El panel HTML filtra en memoria porque ya trae todas las filas para las
    tarjetas de estadisticas. El endpoint JSON puede recibir un `q` y no necesita
    el universo entero, asi que la busqueda la hace Postgres con `ILIKE`.
    """
    patron = f"%{q.strip()[:100]}%"
    consulta = select(Business).order_by(Business.created_at.desc())
    if q.strip():
        consulta = consulta.where(or_(Business.slug.ilike(patron), Business.name.ilike(patron)))
    negocios = list((await sesion.execute(consulta)).scalars().all())
    pagos: dict[uuid.UUID, ProPayment] = {}
    for pago in (
        (await sesion.execute(select(ProPayment).order_by(ProPayment.created_at.desc())))
        .scalars()
        .all()
    ):
        pagos.setdefault(pago.business_id, pago)
    filas = [proyectar(negocio, pagos.get(negocio.id)) for negocio in negocios]
    return filtrar_filas(filas, filtro=filtro, q="")


__all__ = [
    "ACCION_ACTIVAR",
    "ACCION_APROBAR_PAGO",
    "ACCION_BLOQUEAR",
    "ACCION_DESBLOQUEAR",
    "ACCION_LOGIN",
    "ACCION_RECHAZAR_PAGO",
    "FilaNegocio",
    "activar_pro",
    "bloquear",
    "buscar_por_slug_o_nombre",
    "desbloquear",
    "estadisticas",
    "estado_de",
    "filtrar_filas",
    "ip_de",
    "listar_negocios",
    "proyectar",
    "registrar_auditoria",
    "set_tenant_para_escritura",
]
