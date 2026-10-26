"""API JSON del panel administrativo (`/api/v1/admin`).

Esta superficie es la que ya consumia el panel viejo y sigue existiendo por
compatibilidad. **No** es la via principal: el panel web vive en `/admin`
(`api.routers.admin.ui`) y comparte con este router la logica de escritura, que
esta en `modules.admin.service`.

Autenticacion, en este orden:

1. Cookie de sesion del panel (`SECRET_KEY` + `itsdangerous`). Es el camino
   normal.
2. `?key=ADMIN_SECRET`, **solo** si `ALLOW_LEGACY_ADMIN_KEY=true`. La clave en la
   query termina en logs, historial y proxies; por eso esta apagada por defecto y
   debe quedar apagada en produccion.

Sin ninguna de las dos, la respuesta es un 302 al login. Con una clave invalida,
401. Los tests viejos siguen pudiendo overridear `_verificar_secreto`, que es la
dependencia de router y el unico punto de autenticacion de esta superficie.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_settings_dep
from app.api.routers.admin.security import COOKIE_SESION, RUTA_LOGIN, leer_sesion
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.time import now
from app.db.session import get_admin_engine, get_admin_session
from app.models.enums import BusinessPlan, BusinessStatus
from app.modules.admin import service as admin_service
from app.modules.admin.service import (
    ACCION_ACTIVAR,
    ACCION_APROBAR_PAGO,
    ACCION_BLOQUEAR,
    ACCION_DESBLOQUEAR,
    ACCION_RECHAZAR_PAGO,
    registrar_auditoria,
)
from app.modules.businesses.models import Business
from app.modules.businesses.publicacion import dias_restantes, fin_del_trial
from app.modules.payments import pro_pagos
from app.modules.payments.pro_models import ProPayment, ProPaymentStatus

logger = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])
Sesion = Annotated[AsyncSession, Depends(get_admin_session)]


def _verificar_secreto(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    key: Annotated[str | None, Query(alias="key")] = None,
) -> None:
    """Autentica por cookie de sesion o, si esta habilitado, por clave legacy."""
    sesion = leer_sesion(settings, request.cookies.get(COOKIE_SESION))
    if sesion is not None:
        return
    if key:
        # `getattr` con default False y no acceso directo: el doble de `Settings`
        # que usan los tests viejos no declara el campo, y la respuesta correcta
        # ante un contrato incompleto es cerrar, no abrir.
        if not getattr(settings, "allow_legacy_admin_key", False):
            raise HTTPException(status_code=401, detail="Clave de administrador invalida")
        esperado = settings.admin_secret.get_secret_value()
        if esperado and secrets.compare_digest(key, esperado):
            return
        raise HTTPException(status_code=401, detail="Clave de administrador invalida")
    raise HTTPException(
        status_code=302,
        headers={"Location": RUTA_LOGIN},
    )


router.dependencies.append(Depends(_verificar_secreto))


class AdminBusinessOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    business_name: str
    email: str
    slug: str
    estado: str
    plan: str
    trial_dias_restantes: int
    trial_ends_at: str | None
    pro_until: str | None
    ultimo_pago_status: str | None
    ultimo_pago_at: str | None


class AdminStatsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    en_prueba: int
    pendientes: int
    pro_activos: int
    vencidos: int


class AdminActionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    mensaje: str
    pro_until: str | None = None


def _estado_de(n: Business) -> str:
    if n.plan == BusinessPlan.PRO:
        if n.pro_until is not None and n.pro_until <= now():
            return "pro_vencido"
        return "activo"
    if n.status == BusinessStatus.SUSPENDED:
        return "bloqueado"
    if now() >= fin_del_trial(n):
        return "vencido"
    return "trial"


def _proyeccion(negocio: Business, pago: ProPayment | None) -> AdminBusinessOut:
    fin = fin_del_trial(negocio)
    return AdminBusinessOut(
        id=str(negocio.id),
        business_name=negocio.name,
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


async def _set_tenant_for_write(sesion: Sesion, business_id: uuid.UUID) -> None:
    # Esta es la clave: la policy businesses_write exige app.current_business_id = id
    await sesion.execute(
        text("SELECT set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)}
    )


def _uuid_o_400(valor: str) -> uuid.UUID:
    """Un id con formato invalido es un 400, no un 500 con traceback."""
    try:
        return uuid.UUID(valor)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="Identificador invalido") from None


def _quien(request: Request) -> str:
    """Nombre del operador para la fila de auditoria.

    No se inyecta como dependencia para no cambiar la firma que los tests viejos
    overridean: se resuelve del mismo material que autentico la peticion (cookie
    firmada o clave legacy).
    """
    sesion = leer_sesion(get_settings(), request.cookies.get(COOKIE_SESION))
    if sesion is not None:
        return sesion.user
    return "legacy-key" if request.query_params.get("key") else "desconocido"


@router.get("/businesses", response_model=list[AdminBusinessOut])
async def listar_negocios(
    sesion: Sesion,
    filtro: str = Query(
        default="todos", pattern="^(todos|trial|pending|activos|pro|bloqueados|vencidos)$"
    ),
    q: str = Query(default="", max_length=100),
):
    result = await sesion.execute(select(Business).order_by(Business.created_at.desc()))
    negocios = list(result.scalars().all())
    pagos: dict[uuid.UUID, ProPayment] = {}
    result_pagos = await sesion.execute(select(ProPayment).order_by(ProPayment.created_at.desc()))
    for pago in result_pagos.scalars():
        pagos.setdefault(pago.business_id, pago)
    filas = [_proyeccion(n, pagos.get(n.id)) for n in negocios]
    if q.strip():
        busqueda = q.strip().casefold()
        filas = [
            f
            for f in filas
            if busqueda in f.slug.casefold() or busqueda in f.business_name.casefold()
        ]
    if filtro == "trial":
        return [f for f in filas if f.estado == "trial"]
    if filtro == "pending":
        return [f for f in filas if f.ultimo_pago_status == ProPaymentStatus.PENDING.value]
    if filtro == "activos":
        return [f for f in filas if f.estado == "activo"]
    if filtro == "pro":
        return [f for f in filas if f.estado == "activo"]
    if filtro == "bloqueados":
        return [f for f in filas if f.estado == "bloqueado"]
    if filtro == "vencidos":
        return [f for f in filas if f.estado in ("vencido", "pro_vencido", "bloqueado")]
    return filas


@router.get("/stats", response_model=AdminStatsOut)
async def stats(sesion: Sesion):
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
        vencidos=sum(
            1 for n in negocios if _estado_de(n) in ("vencido", "pro_vencido", "bloqueado")
        ),
    )


@router.put("/businesses/{business_id}/activate", response_model=AdminActionOut)
async def activar(
    business_id: str, request: Request, sesion: Sesion, dias: int = Query(default=30, ge=1, le=365)
):
    bid = _uuid_o_400(business_id)
    try:
        _negocio, nuevo = await admin_service.activar_pro(sesion, bid, dias=dias)
    except LookupError:
        raise HTTPException(404, "Negocio no encontrado") from None
    except RuntimeError:
        logger.exception("admin_pro_activar_fallo", business_id=business_id)
        raise HTTPException(500, "No se pudo activar el negocio") from None
    await registrar_auditoria(
        sesion, admin_user=_quien(request), action=ACCION_ACTIVAR, business_id=bid, request=request
    )
    await sesion.commit()
    logger.info("admin_pro_activado", business_id=business_id, dias=dias)
    return AdminActionOut(
        ok=True, mensaje=f"Pro activado por {dias} dias", pro_until=nuevo.isoformat()
    )


@router.put("/businesses/{business_id}/block", response_model=AdminActionOut)
async def bloquear(business_id: str, request: Request, sesion: Sesion):
    bid = _uuid_o_400(business_id)
    try:
        await admin_service.bloquear(sesion, bid)
    except LookupError:
        raise HTTPException(404, "Negocio no encontrado") from None
    except RuntimeError:
        logger.exception("admin_bloquear_fallo", business_id=business_id)
        raise HTTPException(500, "No se pudo bloquear el negocio") from None
    await registrar_auditoria(
        sesion, admin_user=_quien(request), action=ACCION_BLOQUEAR, business_id=bid, request=request
    )
    await sesion.commit()
    return AdminActionOut(ok=True, mensaje="Negocio bloqueado")


@router.put("/businesses/{business_id}/unblock", response_model=AdminActionOut)
async def desbloquear(business_id: str, request: Request, sesion: Sesion):
    bid = _uuid_o_400(business_id)
    try:
        await admin_service.desbloquear(sesion, bid)
    except LookupError:
        raise HTTPException(404, "Negocio no encontrado") from None
    except RuntimeError:
        logger.exception("admin_desbloquear_fallo", business_id=business_id)
        raise HTTPException(500, "No se pudo desbloquear el negocio") from None
    await registrar_auditoria(
        sesion,
        admin_user=_quien(request),
        action=ACCION_DESBLOQUEAR,
        business_id=bid,
        request=request,
    )
    await sesion.commit()
    return AdminActionOut(ok=True, mensaje="Negocio desbloqueado")


@router.put("/payments/{payment_id}/approve", response_model=AdminActionOut)
async def aprobar_pago(
    payment_id: str, request: Request, sesion: Sesion, dias: int = Query(default=30, ge=1, le=365)
):
    # pro_pagos.aprobar internamente hace UPDATE en businesses, necesita también el GUC
    # por eso lo seteamos dentro leyendo el payment primero
    pid = _uuid_o_400(payment_id)
    pago = await sesion.get(ProPayment, pid)
    if pago is None:
        raise HTTPException(404, "Pago no encontrado")
    await _set_tenant_for_write(sesion, pago.business_id)
    negocio = await pro_pagos.aprobar(sesion, pid, dias=dias)
    await registrar_auditoria(
        sesion,
        admin_user=_quien(request),
        action=ACCION_APROBAR_PAGO,
        business_id=pago.business_id,
        request=request,
    )
    await sesion.commit()
    return AdminActionOut(
        ok=True,
        mensaje=f"Pro activado para {negocio.name}",
        pro_until=negocio.pro_until.isoformat() if negocio.pro_until else None,
    )


@router.put("/payments/{payment_id}/reject", response_model=AdminActionOut)
async def rechazar_pago(
    payment_id: str, request: Request, sesion: Sesion, motivo: str = Query(default="")
):
    pid = _uuid_o_400(payment_id)
    pago = await sesion.get(ProPayment, pid)
    if pago is None:
        raise HTTPException(404, "Pago no encontrado")
    await pro_pagos.rechazar(sesion, pid, motivo=motivo or None)
    await registrar_auditoria(
        sesion,
        admin_user=_quien(request),
        action=ACCION_RECHAZAR_PAGO,
        business_id=pago.business_id,
        request=request,
    )
    await sesion.commit()
    return AdminActionOut(ok=True, mensaje=f"Comprobante rechazado ({pago.status.value})")


@router.post("/cron/vencer")
async def cron_vencer(sesion: Sesion):
    ahora = now()
    negocios = (await sesion.execute(select(Business))).scalars().all()
    vencidos = 0
    for negocio in negocios:
        if negocio.plan == BusinessPlan.PRO:
            continue
        if negocio.status == BusinessStatus.ACTIVE and ahora >= fin_del_trial(negocio):
            await _set_tenant_for_write(sesion, negocio.id)
            await sesion.execute(
                update(Business)
                .where(Business.id == negocio.id)
                .values(status=BusinessStatus.SUSPENDED)
            )
            vencidos += 1
    await sesion.commit()
    return {"suspendidos": vencidos}


async def _eliminar_negocio_cascade(sesion: AsyncSession, business_id: uuid.UUID) -> int:
    """Borra el negocio y todo lo que depende de el. Devuelve filas eliminadas.

    La operacion destructiva compartida por el endpoint API (DELETE) y el panel
    HTML (POST /eliminar): un solo lugar define el orden de FKs y el manejo de
    RLS, y no dos copias que divergen. **No valida precondiciones** (slug
    reservado, ultimo negocio): eso lo hace el llamador antes de invocarla.

    Las FKs son RESTRICT, asi que el orden importa: reservas (por customer y
    por negocio), customers, y despues el resto de las tablas hijas. Cada
    DELETE absorbe su propio fallo para que una tabla sin filas no aborte todo.

    RLS: las tablas tienen FORCE, que filtra tambien al owner. Se apagan al
    empezar y se restauran al final en una transaccion propia; si algo falla a
    mitad, la transaccion muere y los ALTERs (DDL transaccional) se revierten
    solos.
    """
    await sesion.execute(text("SET LOCAL row_security = off"))
    tablas = ["bookings", "customers", "professional_services", "professionals",
              "services", "business_hours", "businesses"]
    for tabla in tablas:
        try:
            await sesion.execute(text(f"ALTER TABLE {tabla} NO FORCE ROW LEVEL SECURITY"))
        except Exception:
            await sesion.rollback()

    orden = [
        "DELETE FROM bookings WHERE customer_id IN (SELECT id FROM customers WHERE business_id = :id)",
        "DELETE FROM bookings WHERE business_id = :id",
        "DELETE FROM customers WHERE business_id = :id",
        "DELETE FROM professional_services WHERE business_id = :id",
        "DELETE FROM professionals WHERE business_id = :id",
        "DELETE FROM services WHERE business_id = :id",
        "DELETE FROM business_hours WHERE business_id = :id",
    ]
    borradas = 0
    for sql in orden:
        try:
            r = await sesion.execute(text(sql), {"id": business_id})
            borradas += r.rowcount or 0
        except Exception:
            await sesion.rollback()

    await sesion.execute(text("DELETE FROM businesses WHERE id = :id"), {"id": business_id})
    await sesion.commit()

    # Restaurar FORCE: la transaccion principal ya commitio los NO FORCE, asi
    # que hay que devolverlos en una transaccion aparte.
    restaurador = async_sessionmaker(
        get_admin_engine(), class_=AsyncSession, expire_on_commit=False
    )()
    try:
        async with restaurador.begin():
            for tabla in tablas:
                try:
                    await restaurador.execute(text(f"ALTER TABLE {tabla} FORCE ROW LEVEL SECURITY"))
                except Exception:
                    pass
    finally:
        await restaurador.close()
    return borradas


@router.delete("/businesses/{business_id}", summary="Eliminar un negocio y todos sus datos")
async def eliminar_negocio(business_id: str, sesion: Sesion) -> AdminActionOut:
    """Borra un negocio y todo lo que depende de el, con cascade manual.

    **No se puede borrar el ultimo negocio** ni el negocio principal
    (`the-peluche`, slug reservado): una BD sin negocios no tiene camino de
    vuelta desde el panel.
    """
    negocio = await sesion.get(Business, uuid.UUID(business_id))
    if negocio is None:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    if negocio.slug == "the-peluche":
        raise HTTPException(status_code=403, detail="El negocio principal no se puede eliminar")
    total = (await sesion.execute(select(func.count()).select_from(Business))).scalar_one()
    if total <= 1:
        raise HTTPException(status_code=403, detail="No se puede eliminar el ultimo negocio")

    filas = await _eliminar_negocio_cascade(sesion, negocio.id)
    logger.info("admin_negocio_eliminado", business_id=business_id, filas=filas)
    return AdminActionOut(ok=True, mensaje=f"Negocio eliminado ({filas} filas borradas)")


def _whatsapp_de(negocio: Business, _pago: ProPayment | None) -> str:
    from app.core.config import get_settings

    s = get_settings()
    from urllib.parse import quote

    texto = f"Hola! Te escribimos de Tempus por la activacion PRO de {negocio.name} 🚀"
    return f"https://wa.me/{s.pro_support_whatsapp}?text={quote(texto)}"


__all__ = ["_whatsapp_de", "router"]
