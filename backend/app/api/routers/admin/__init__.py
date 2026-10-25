from __future__ import annotations
import datetime as dt
import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, update, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.dependencies import get_settings_dep
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.time import now
from app.db.session import get_admin_session
from app.models.enums import BusinessPlan, BusinessStatus
from app.modules.businesses.models import Business
from app.modules.businesses.publicacion import dias_restantes, fin_del_trial
from app.modules.payments import pro_pagos
from app.modules.payments.pro_models import ProPayment, ProPaymentStatus

logger = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])
Sesion = Annotated[AsyncSession, Depends(get_admin_session)]

def _verificar_secreto(key: Annotated[str, Query(alias="key")], settings: Annotated[Settings, Depends(get_settings_dep)]) -> None:
    import secrets
    esperado = settings.admin_secret.get_secret_value()
    if not esperado or not secrets.compare_digest(key, esperado):
        raise HTTPException(status_code=401, detail="Clave de administrador invalida")
router.dependencies.append(Depends(_verificar_secreto))

class AdminBusinessOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str; business_name: str; email: str; slug: str; estado: str; plan: str
    trial_dias_restantes: int; trial_ends_at: str | None; pro_until: str | None
    ultimo_pago_status: str | None; ultimo_pago_at: str | None
class AdminStatsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    en_prueba: int; pendientes: int; pro_activos: int; vencidos: int
class AdminActionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool; mensaje: str; pro_until: str | None = None

def _estado_de(n: Business) -> str:
    if n.plan == BusinessPlan.PRO:
        if n.pro_until is not None and n.pro_until <= now(): return "pro_vencido"
        return "activo"
    if n.status == BusinessStatus.SUSPENDED: return "bloqueado"
    if now() >= fin_del_trial(n): return "vencido"
    return "trial"

def _proyeccion(negocio: Business, pago: ProPayment | None) -> AdminBusinessOut:
    fin = fin_del_trial(negocio)
    return AdminBusinessOut(id=str(negocio.id), business_name=negocio.name, email=pago.email if pago else "", slug=negocio.slug, estado=_estado_de(negocio), plan=str(negocio.plan), trial_dias_restantes=dias_restantes(negocio), trial_ends_at=fin.isoformat(), pro_until=negocio.pro_until.isoformat() if negocio.pro_until else None, ultimo_pago_status=str(pago.status) if pago else None, ultimo_pago_at=pago.created_at.isoformat() if pago else None)

async def _set_tenant_for_write(sesion: Sesion, business_id: uuid.UUID):
    # Esta es la clave: la policy businesses_write exige app.current_business_id = id
    await sesion.execute(text("SELECT set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)})

@router.get("/businesses", response_model=list[AdminBusinessOut])
async def listar_negocios(sesion: Sesion, filtro: str = Query(default="todos", pattern="^(todos|trial|pending|activos|vencidos)$")):
    result = await sesion.execute(select(Business).order_by(Business.created_at.desc()))
    negocios = list(result.scalars().all())
    pagos: dict[uuid.UUID, ProPayment] = {}
    result_pagos = await sesion.execute(select(ProPayment).order_by(ProPayment.created_at.desc()))
    for pago in result_pagos.scalars(): pagos.setdefault(pago.business_id, pago)
    filas = [_proyeccion(n, pagos.get(n.id)) for n in negocios]
    if filtro == "trial": return [f for f in filas if f.estado == "trial"]
    if filtro == "pending": return [f for f in filas if f.ultimo_pago_status == ProPaymentStatus.PENDING.value]
    if filtro == "activos": return [f for f in filas if f.estado == "activo"]
    if filtro == "vencidos": return [f for f in filas if f.estado in ("vencido", "pro_vencido", "bloqueado")]
    return filas

@router.get("/stats", response_model=AdminStatsOut)
async def stats(sesion: Sesion):
    result = await sesion.execute(select(Business))
    negocios = list(result.scalars().all())
    pendientes = (await sesion.execute(select(func.count()).select_from(ProPayment).where(ProPayment.status == ProPaymentStatus.PENDING))).scalar_one()
    return AdminStatsOut(en_prueba=sum(1 for n in negocios if _estado_de(n)=="trial"), pendientes=pendientes, pro_activos=sum(1 for n in negocios if _estado_de(n)=="activo"), vencidos=sum(1 for n in negocios if _estado_de(n) in ("vencido","pro_vencido","bloqueado")))

@router.put("/businesses/{business_id}/activate", response_model=AdminActionOut)
async def activar(business_id: str, sesion: Sesion, dias: int = Query(default=30, ge=1, le=365)):
    bid = uuid.UUID(business_id)
    negocio = await sesion.get(Business, bid)
    if not negocio: raise HTTPException(404, "Negocio no encontrado")
    base = negocio.pro_until if negocio.plan==BusinessPlan.PRO and negocio.pro_until and negocio.pro_until > now() else now()
    nuevo = base + dt.timedelta(days=dias)
    await _set_tenant_for_write(sesion, bid)
    result = await sesion.execute(update(Business).where(Business.id==bid).values(plan=BusinessPlan.PRO, pro_until=nuevo, status=BusinessStatus.ACTIVE))
    if result.rowcount==0: raise HTTPException(500, f"RLS sigue bloqueando UPDATE id={bid} rowcount=0")
    await sesion.commit()
    logger.info("admin_pro_activado", business_id=business_id, dias=dias)
    return AdminActionOut(ok=True, mensaje=f"Pro activado por {dias} dias", pro_until=nuevo.isoformat())

@router.put("/businesses/{business_id}/block", response_model=AdminActionOut)
async def bloquear(business_id: str, sesion: Sesion):
    bid = uuid.UUID(business_id)
    negocio = await sesion.get(Business, bid)
    if not negocio: raise HTTPException(404, "Negocio no encontrado")
    await _set_tenant_for_write(sesion, bid)
    result = await sesion.execute(update(Business).where(Business.id==bid).values(status=BusinessStatus.SUSPENDED))
    if result.rowcount==0: raise HTTPException(500, f"RLS bloqueando block id={bid}")
    await sesion.commit()
    return AdminActionOut(ok=True, mensaje="Negocio bloqueado")

@router.put("/payments/{payment_id}/approve", response_model=AdminActionOut)
async def aprobar_pago(payment_id: str, sesion: Sesion, dias: int = Query(default=30, ge=1, le=365)):
    # pro_pagos.aprobar internamente hace UPDATE en businesses, necesita también el GUC
    # por eso lo seteamos dentro leyendo el payment primero
    pago = await sesion.get(ProPayment, uuid.UUID(payment_id))
    if pago: await _set_tenant_for_write(sesion, pago.business_id)
    negocio = await pro_pagos.aprobar(sesion, uuid.UUID(payment_id), dias=dias)
    await sesion.commit()
    return AdminActionOut(ok=True, mensaje=f"Pro activado para {negocio.name}", pro_until=negocio.pro_until.isoformat() if negocio.pro_until else None)

@router.put("/payments/{payment_id}/reject", response_model=AdminActionOut)
async def rechazar_pago(payment_id: str, sesion: Sesion, motivo: str = Query(default="")):
    pago = await pro_pagos.rechazar(sesion, uuid.UUID(payment_id), motivo=motivo or None)
    await sesion.commit()
    return AdminActionOut(ok=True, mensaje=f"Comprobante rechazado ({pago.status.value})")

@router.post("/cron/vencer")
async def cron_vencer(sesion: Sesion):
    ahora = now()
    negocios = (await sesion.execute(select(Business))).scalars().all()
    vencidos = 0
    for negocio in negocios:
        if negocio.plan == BusinessPlan.PRO: continue
        if negocio.status == BusinessStatus.ACTIVE and ahora >= fin_del_trial(negocio):
            await _set_tenant_for_write(sesion, negocio.id)
            await sesion.execute(update(Business).where(Business.id==negocio.id).values(status=BusinessStatus.SUSPENDED))
            vencidos+=1
    await sesion.commit()
    return {"suspendidos": vencidos}

def _whatsapp_de(negocio: Business, pago: ProPayment | None) -> str:
    from app.core.config import get_settings
    s = get_settings()
    from urllib.parse import quote
    texto = f"Hola! Te escribimos de Tempus por la activacion PRO de {negocio.name} 🚀"
    return f"https://wa.me/{s.pro_support_whatsapp}?text={quote(texto)}"
__all__ = ["_whatsapp_de", "router"]