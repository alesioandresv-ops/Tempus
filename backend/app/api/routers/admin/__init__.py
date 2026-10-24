"""Router administrativo de la plataforma: cobro manual del plan Pro.

Protegido por `ADMIN_SECRET` (query `?key=`), no por JWT de usuario: es la
consola del operador de Tempus, no un panel de un negocio. Con el secreto vacio
el router entero responde 401 -- un despliegue sin la variable no expone nada.

**Aqui se usan sesiones globales, sin GUC de tenant.** Las filas que se leen y
escriben son de todos los negocios (`pro_payments` es de tenant pero el rol que
ejecuta el router tiene la ventana abierta por `session_scope`, que setea el GUC
de plataforma). Si un dia el admin maneja mas que pagos, ese dia se revisa este
parrafo.
"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_settings_dep
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.time import now
from app.db.session import admin_session
from app.models.enums import BusinessPlan, BusinessStatus
from app.modules.businesses.models import Business
from app.modules.businesses.publicacion import dias_restantes, fin_del_trial
from app.modules.payments import pro_pagos
from app.modules.payments.pro_models import ProPayment, ProPaymentStatus

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])

Sesion = Annotated[AsyncSession, Depends(admin_session)]


def _verificar_secreto(
    key: Annotated[str, Query(alias="key")],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> None:
    """Guardia del router entero: el secreto manda.

    Con `ADMIN_SECRET` vacio el panel esta desactivado por completo: no es
    "cualquier valor entra", es "nadie entra". `secrets.compare_digest` evita el
    oraculo de tiempo en la comparacion.
    """
    import secrets

    esperado = settings.admin_secret.get_secret_value()
    if not esperado or not secrets.compare_digest(key, esperado):
        raise HTTPException(status_code=401, detail="Clave de administrador invalida")


router.dependencies.append(Depends(_verificar_secreto))


# ---------------------------------------------------------------------- schemas
class AdminBusinessOut(BaseModel):
    """Fila de negocio tal como la ve el admin."""

    model_config = ConfigDict(extra="forbid")

    id: str
    business_name: str
    email: str
    slug: str
    estado: str  # trial | activo | pro_vencido | pendiente | bloqueado
    plan: str
    trial_dias_restantes: int
    trial_ends_at: str | None
    pro_until: str | None
    ultimo_pago_status: str | None
    ultimo_pago_at: str | None


class AdminStatsOut(BaseModel):
    """Contadores de la cabecera del panel."""

    model_config = ConfigDict(extra="forbid")

    en_prueba: int
    pendientes: int
    pro_activos: int
    vencidos: int


class AdminActionOut(BaseModel):
    """Salida generica de las acciones de activar/bloquear."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    mensaje: str
    pro_until: str | None = None


def _estado_de(negocio: Business) -> str:
    """Estado consolidado para el badge del admin.

    Deriva de los datos y no de una columna: el `status` administrativo y el plan
    cuentan historias distintas y el admin necesita ver la combinacion.
    """
    if negocio.plan == BusinessPlan.PRO:
        if negocio.pro_until is not None and negocio.pro_until <= now():
            return "pro_vencido"
        return "activo"
    if negocio.status == BusinessStatus.SUSPENDED:
        return "bloqueado"
    if now() >= fin_del_trial(negocio):
        return "vencido"
    return "trial"


def _proyeccion(negocio: Business, pago: ProPayment | None) -> AdminBusinessOut:
    fin = fin_del_trial(negocio)
    return AdminBusinessOut(
        id=str(negocio.id),
        business_name=negocio.name,
        # El email del dueno vive en `users`, no en `businesses`; el admin lo
        # cruza con los pagos, que traen el email declarado.
        email=pago.email if pago else "",
        slug=negocio.slug,
        estado=_estado_de(negocio),
        plan=str(negocio.plan),
        trial_dias_restantes=dias_restantes(negocio),
        trial_ends_at=fin.isoformat(),
        pro_until=negocio.pro_until.isoformat() if negocio.pro_until else None,
        ultimo_pago_status=str(pago.status) if pago else None,
        ultimo_pago_at=pago.created_at.isoformat() if pago else None,
    )


@router.get("/businesses", response_model=list[AdminBusinessOut], summary="Listar negocios con su estado de cobro")
async def listar_negocios(
    sesion: Sesion,
    filtro: str = Query(default="todos", pattern="^(todos|trial|pending|activos|vencidos)$"),
) -> list[AdminBusinessOut]:
    """Todos los negocios con el estado consolidado de cobro.

    El filtro es de presentacion y se aplica en memoria: la tabla de negocios es
    chica (miles, no millones) y una sola query la traiga entera. Optimizar aca
    seria optimizar lo que no hace falta.
    """
    result = await sesion.execute(select(Business).order_by(Business.created_at.desc()))
    negocios = list(result.scalars().all())

    # Ultimo pago de cada negocio, en una sola query para no hacer N+1.
    pagos: dict[uuid.UUID, ProPayment] = {}
    result_pagos = await sesion.execute(
        select(ProPayment).order_by(ProPayment.created_at.desc())
    )
    for pago in result_pagos.scalars():
        pagos.setdefault(pago.business_id, pago)

    filas = [_proyeccion(n, pagos.get(n.id)) for n in negocios]
    if filtro == "trial":
        return [f for f in filas if f.estado == "trial"]
    if filtro == "pending":
        return [f for f in filas if f.ultimo_pago_status == ProPaymentStatus.PENDING.value]
    if filtro == "activos":
        return [f for f in filas if f.estado == "activo"]
    if filtro == "vencidos":
        return [f for f in filas if f.estado in ("vencido", "pro_vencido", "bloqueado")]
    return filas


@router.get("/stats", response_model=AdminStatsOut, summary="Contadores del panel")
async def stats(sesion: Sesion) -> AdminStatsOut:
    result = await sesion.execute(select(Business))
    negocios = list(result.scalars().all())
    pendientes = (
        await sesion.execute(
            select(func.count())
            .select_from(ProPayment)
            .where(ProPayment.status == ProPaymentStatus.PENDING)
        )
    ).scalar_one()
    return AdminStatsOut(
        en_prueba=sum(1 for n in negocios if _estado_de(n) == "trial"),
        pendientes=pendientes,
        pro_activos=sum(1 for n in negocios if _estado_de(n) == "activo"),
        vencidos=sum(1 for n in negocios if _estado_de(n) in ("vencido", "pro_vencido", "bloqueado")),
    )


@router.put("/businesses/{business_id}/activate", response_model=AdminActionOut, summary="Activar Pro manualmente")
async def activar(
    business_id: str,
    sesion: Sesion,
    dias: int = Query(default=pro_pagos.DIAS_DE_PRO, ge=1, le=365),
) -> AdminActionOut:
    """Activa Pro sin pago previo (regalo, compensacion o correccion manual)."""
    negocio = await sesion.get(Business, uuid.UUID(business_id))
    if negocio is None:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    base = (
        negocio.pro_until
        if negocio.plan == BusinessPlan.PRO and negocio.pro_until and negocio.pro_until > now()
        else now()
    )
    negocio.plan = BusinessPlan.PRO
    negocio.pro_until = base + dt.timedelta(days=dias)
    if negocio.status != BusinessStatus.ACTIVE:
        negocio.status = BusinessStatus.ACTIVE
    await sesion.commit()
    logger.info("admin_pro_activado", business_id=business_id, dias=dias)
    return AdminActionOut(
        ok=True,
        mensaje=f"Pro activado por {dias} dias",
        pro_until=negocio.pro_until.isoformat(),
    )


@router.put("/businesses/{business_id}/block", response_model=AdminActionOut, summary="Bloquear un negocio")
async def bloquear(business_id: str, sesion: Sesion) -> AdminActionOut:
    """Suspende el negocio: deja de publicarse y de aceptar reservas."""
    negocio = await sesion.get(Business, uuid.UUID(business_id))
    if negocio is None:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    negocio.status = BusinessStatus.SUSPENDED
    await sesion.commit()
    logger.info("admin_negocio_bloqueado", business_id=business_id)
    return AdminActionOut(ok=True, mensaje="Negocio bloqueado")


@router.put("/payments/{payment_id}/approve", response_model=AdminActionOut, summary="Aprobar un comprobante")
async def aprobar_pago(payment_id: str, sesion: Sesion, dias: int = Query(default=pro_pagos.DIAS_DE_PRO, ge=1, le=365)) -> AdminActionOut:
    negocio = await pro_pagos.aprobar(sesion, uuid.UUID(payment_id), dias=dias)
    await sesion.commit()
    return AdminActionOut(
        ok=True,
        mensaje=f"Pro activado para {negocio.name}",
        pro_until=negocio.pro_until.isoformat() if negocio.pro_until else None,
    )


@router.put("/payments/{payment_id}/reject", response_model=AdminActionOut, summary="Rechazar un comprobante")
async def rechazar_pago(payment_id: str, sesion: Sesion, motivo: str = Query(default="")) -> AdminActionOut:
    pago = await pro_pagos.rechazar(sesion, uuid.UUID(payment_id), motivo=motivo or None)
    await sesion.commit()
    return AdminActionOut(ok=True, mensaje=f"Comprobante rechazado ({pago.status.value})")


@router.post("/cron/vencer", summary="Cerrar trials y Pros vencidos (cron diario)")
async def cron_vencer(sesion: Sesion) -> dict[str, int]:
    """Ejecuta el corte diario: trials vencidos y Pro sin renovar.

    Lo llama el Worker con el cron diario (o un job externo). Idempotente:
    correrlo dos veces no cambia nada la segunda.
    """
    ahora = now()
    # El corte del Pro es en el momento, no hace falta fecha: un Pro cuyo
    # `pro_until` paso vuelve a la regla del trial via `publicacion`.
    negocios = (await sesion.execute(select(Business))).scalars().all()
    vencidos = 0
    for negocio in negocios:
        if negocio.plan == BusinessPlan.PRO:
            continue
        if negocio.status == BusinessStatus.ACTIVE and ahora >= fin_del_trial(negocio):
            negocio.status = BusinessStatus.SUSPENDED
            vencidos += 1
    await sesion.commit()
    return {"suspendidos": vencidos}


def _whatsapp_de(negocio: Business, pago: ProPayment | None) -> str:
    """Enlace de WhatsApp al dueno con el nombre del negocio pre-armado."""
    s = get_settings()
    from urllib.parse import quote

    texto = f"Hola! Te escribimos de Tempus por la activacion PRO de {negocio.name} 🚀"
    return f"https://wa.me/{s.pro_support_whatsapp}?text={quote(texto)}"


__all__ = ["_whatsapp_de", "router"]
