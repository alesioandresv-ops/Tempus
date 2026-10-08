"""Dreno de la outbox de notificaciones.

`notification_requests` es la fuente de verdad de "que hay que mandarle a quien y
cuando". Este modulo la lee y la manda. No encola `jobs`: la tabla ya tiene
`status`, `attempts`, `max_attempts`, `scheduled_for` y `error`, que es
exactamente el estado de reintento que una fila de `jobs` aportaria. Meter las dos
seria tener dos fuentes de verdad para la misma pregunta -- "esta notificacion ya
salio?" -- y la que se equivoca es la que nadie mira.

**Como se resuelve la falta de un estado `sending`.** `NotificationStatus` tiene
`pending`, `sent`, `delivered`, `failed`, `skipped` y `cancelled`, y ninguno significa
"alguien lo esta mandando ahora". Se podria agregar, pero no hace falta: el lease se
codifica empujando `scheduled_for` al futuro.

El claim es

    UPDATE ... SET attempts = attempts + 1,
                    scheduled_for = now() + :lease
    WHERE status = 'pending' AND scheduled_for <= now()

y `scheduled_for > now()` **es** el estado de ocupado. Un segundo worker que llega
mientras tanto no cumple el filtro y no toca la fila; si el primero se muere, el
lease vence solo y la fila vuelve a ser elegible, con el `attempts` ya incrementado
para que el intento perdido cuente.

Esto evita la migracion y el estado nuevo, pero no es gratis: `scheduled_for` deja
de significar "cuando hay que mandarlo" y pasa a significar "cuando hay que mandarlo,
o cuando se libera el intento en curso". Se documenta aca porque un lector futuro
tiene que saber que la columna cumple dos papeles.

**Por que el claim es un `UPDATE` condicional y no un `SELECT` seguido de `UPDATE`.**
Un `SELECT` no bloquea, asi que dos workers que corren a la vez -- el tick del
proceso y el cron externo, que es justo el despliegue que el proyecto contempla --
leen las mismas filas y mandan el mismo mensaje dos veces. En WhatsApp el duplicado
es la razon principal por la que un cliente bloquea a un negocio. Con el `UPDATE`
condicional, el segundo no actualiza nada y sigue de largo.

**Lo que este modulo NO garantiza.** Si el worker muere *despues* de que Meta
acepto el mensaje y *antes* de comitear `status='sent'`, el reintento manda un
duplicado. No hay forma de evitarlo sin idempotencia del lado de Meta, que la API
no ofrece para envios de mensajes. Se elige reintentar antes que perder el
recordatorio: un cliente que no recibe aviso de su turno se queja; un duplicado se
olvida. Es una decision de producto y por eso queda escrita.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt_string
from app.core.logging import get_logger
from app.core.time import now
from app.db.session import get_engine, tenant_session
from app.integrations.whatsapp.client import WhatsAppClient, get_whatsapp_client
from app.models.enums import NotificationStatus
from app.modules.notifications.config import plantilla_de
from app.modules.notifications.models import WhatsAppConnection

logger = get_logger(__name__)

#: Maximo de notificaciones tomadas por tenant y tick.
#:
#: Limite de trabajo, no de correctitud: lo que no entra se toma en el tick
#: siguiente. Sin tope, un dia que se acumulen notificaciones vencidas podria abrir
#: miles de transacciones y agotar el pool, que es el mismo que atiende el HTTP.
BATCH_LIMIT = 50

#: Cuanto queda ocupada una notificacion mientras se manda.
#:
#: Un envio a Meta tarda segundos. Treinta minutos es holgado a proposito: el coste
#: de esperar de mas es un recordatorio tarde, y el de reintentar sobre un envio que
#: si completo es un mensaje duplicado. Ante la duda, esperar.
LEASE = dt.timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class DrainResult:
    """Que hizo un tick. Suma de todos los tenants."""

    businesses: int = 0
    claimed: int = 0
    sent: int = 0
    failed: int = 0
    requeued: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class Notification:
    """Una notificacion tomada. Datos planos, no un ORM: la sesion ya se cerro."""

    id: uuid.UUID
    business_id: uuid.UUID
    kind: str
    attempts: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class Mensaje:
    """Un mensaje de plantilla listo para transmitir a Meta.

    Lleva la configuracion del negocio (numero y token) adentro a proposito:
    `_resolver` la lee con el GUC de tenant puesto y `_transmitir` la consume sin
    sesion, que es la unica forma de no atar una transaccion a la llamada de red.
    """

    destino: str
    template_name: str
    components: list[dict[str, Any]]
    phone_number_id: str
    access_token: str


@dataclass(frozen=True, slots=True)
class Resolucion:
    """Que sale de resolver una notificacion: el mensaje o el motivo de saltarla."""

    mensaje: Mensaje | None = None
    skip_motivo: str | None = None


@dataclass(frozen=True, slots=True)
class TransmitResult:
    """Desenlace de un intento de transmision."""

    enviado: bool = False
    message_id: str | None = None
    error: str | None = None


#: Fabrica del cliente de Meta. Atributo de modulo a proposito: los tests la
#: reemplazan por un cliente fake que registra las llamadas, sin tocar la red.
whatsapp_client_factory: Callable[[str, str], WhatsAppClient] = get_whatsapp_client


async def businesses_with_work(limit: int = BATCH_LIMIT) -> list[uuid.UUID]:
    """Negocios con notificaciones vencidas. Solo ids.

    La funcion es `SECURITY DEFINER` porque `notification_requests` tiene RLS y el
    worker es cross-tenant. Ver `0009_outbox_tenant_scan`: devuelve unicamente ids y
    no filas, asi que el unico priviliegio que agrega es *saber a quien visitar*.
    """
    engine = get_engine()
    conn = await engine.connect()
    try:
        result = await conn.execute(
            text("SELECT business_id FROM businesses_with_pending_notifications(:limit)"),
            {"limit": limit},
        )
        rows = result.fetchall()
        return [row[0] for row in rows]
    finally:
        await conn.close()


async def drain_once(limit: int = BATCH_LIMIT) -> DrainResult:
    """Un tick completo: visita los negocios con trabajo y manda lo vencido."""
    businesses = await businesses_with_work(limit)
    total = DrainResult(businesses=len(businesses))

    for business_id in businesses:
        # Un negocio con datos corruptos -- un telefono raro, un payload que no
        # parsea -- no puede impedir que los demas reciban sus recordatorios. Por eso
        # la excepcion se loguea y se sigue con el siguiente, en vez de subir y
        # abortar el tick entero.
        try:
            parcial = await _drain_business(business_id, limit)
        except Exception as exc:
            logger.error(
                "outbox_tenant_fallo",
                business_id=str(business_id),
                error=str(exc),
            )
            continue

        total = DrainResult(
            businesses=total.businesses,
            claimed=total.claimed + parcial.claimed,
            sent=total.sent + parcial.sent,
            failed=total.failed + parcial.failed,
            requeued=total.requeued + parcial.requeued,
            skipped=total.skipped + parcial.skipped,
        )

    logger.info(
        "outbox_tick",
        negocios=total.businesses,
        tomadas=total.claimed,
        enviadas=total.sent,
        fallidas=total.failed,
        reencoladas=total.requeued,
        omitidas=total.skipped,
    )
    return total


async def _drain_business(business_id: uuid.UUID, limit: int) -> DrainResult:
    """Toma y procesa las notificaciones vencidas de un negocio.

    **Cada fase va en su propia `tenant_session`.** No es estilo, es lo unico que
    funciona: `notification_requests` y `customers` tienen RLS con `FORCE`, asi que
    una sesion sin GUC de tenant no ve **ninguna** fila de las dos. Con una sola
    sesion mal elegida el fallo no es una excepcion, es peor -- es silencioso:

    - el `SELECT` del destinatario devuelve cero filas, el comparador queda en
      `NULL` y la notificacion se marca `skipped` como si el cliente se hubiera dado
      de baja, cuando en realidad nunca se lo pudo leer;
    - el `UPDATE` del estado final afecta cero filas, asi que la base nunca registra
      el desenlace, y el proximo tick vuelve a ver la misma fila pendiente.

    El log de esa corrida decia `estado=skipped` para todo, y la base seguia con las
    filas intactas. Un log que afirma un estado que la base no registro es peor que
    no tener log.

    Ademas el envio a Meta va **fuera** de toda transaccion. Una transaccion abierta
    mientras se espera a un HTTP externo retiene un lock del pool durante los
    timeouts de la llamada, y el pool es el mismo que atiende el trafico del cliente.
    """
    # La barredora va **antes** del claim, y va primero por una razon concreta: una
    # fila que agoto sus intentos queda en `pending`, y el filtro del claim la excluye
    # (`attempts < max_attempts`). Sin esta barredora, esa fila no es reclamable, no
    # es terminal, y no hay nadie que la mueva: se queda `pending` para siempre. El
    # sintoma es una cola que enuncia recordatorios perdidos sin dar ningun error.
    #
    # No se resuelve metiendo la fila en el claim (quitarle el filtro de intentos):
    # eso haria que se intentara **enviar** un mensaje que ya se rindio, y un envio
    # de mas es peor que una fila sin mandar. Primero se la declara `failed`, y ahi
    # si queda fuera de todo.
    await _marcar_agotadas(business_id)

    async with tenant_session(business_id) as session:
        tomadas = await _reclamar(session, business_id, limit)

    if not tomadas:
        return DrainResult()

    enviadas = fallidas = reencoladas = omitidas = 0
    for notificacion in tomadas:
        # Aislar cada notificacion es lo que evita que **una sola fila mala vacie el
        # lote entero**. Sin este `try`, un error en la quinta notificacion salia
        # disparado hacia arriba, el resto del lote nunca se procesaba, y el resumen
        # del tick reportaba cero tomadas aunque las cinco ya estaban reclamadas --
        # un log que no coincide con lo que paso, que es como uno empieza a desconfiar
        # de los logs y termina sin mirar ninguno.
        #
        # La fila que falla queda con `attempts` incrementado y el lease puesto, asi
        # que se reintenta sola cuando el lease vence. No hace falta deshacer nada.
        try:
            # 1. Resolver el mensaje: destinatario, variables de la plantilla y
            #    configuracion de WhatsApp del negocio. Transaccion corta, cerrada
            #    antes de ir a la red.
            async with tenant_session(business_id) as session:
                resolucion = await _resolver(session, notificacion)

            if resolucion.mensaje is None:
                # Sin destinatario elegible, WhatsApp no activo o tipo sin plantilla:
                # un salto terminal y no un error, porque ningun reintento lo arregla.
                # El motivo queda en el `error` de la fila, que es donde el panel lo
                # muestra como diagnostico.
                async with tenant_session(business_id) as session:
                    await _settle(session, notificacion, skip_motivo=resolucion.skip_motivo)
                omitidas += 1
                continue

            # 2. Transmitir. Sin sesion: no se puede atar una transaccion a una
            #    llamada de red que puede tardar 30 segundos.
            resultado = await _transmitir(resolucion.mensaje)

            # 3. Asentar el desenlace. Otra transaccion corta y propia.
            async with tenant_session(business_id) as session:
                await _settle(session, notificacion, resultado=resultado)
        except Exception as exc:
            logger.error(
                "outbox_notificacion_fallo",
                notificacion_id=str(notificacion.id),
                kind=notificacion.kind,
                error=str(exc),
                detalle="queda con lease; se reintentara sola cuando venza",
            )
            fallidas += 1
            continue

        if resultado.enviado:
            enviadas += 1
        elif notificacion.attempts >= notificacion.max_attempts:
            # Agotados los intentos no quedan mas: el desenlace es terminal. Sin
            # destinatario o configuracion ya se fue por el camino `skipped` de
            # arriba; el camino de aca es el del envio que intento y fallo.
            fallidas += 1
        else:
            reencoladas += 1

    return DrainResult(
        businesses=1,
        claimed=len(tomadas),
        sent=enviadas,
        failed=fallidas,
        requeued=reencoladas,
        skipped=omitidas,
    )


async def _marcar_agotadas(business_id: uuid.UUID) -> int:
    """Pasa a `failed` las que agotaron sus intentos. Devuelve cuantas.

    Es la unica transicion del sistema que no necesita mandar nada: el recordatorio
    ya se rindio antes y de lo que se trata es dejar de fingir que sigue en la cola.

    `COALESCE(error, ...)`: la barredora **no pisa** el motivo del ultimo intento.
    "faltan las credenciales de Meta" dice por que fallo; el mensaje generico de la
    barredora solo dice que se rindio, que ya se sabia. Sobrescribir el error real
    con uno que no aporta nada seria tirar el unico dato util para diagnosticar.
    """
    async with tenant_session(business_id) as session:
        resultado = await session.execute(
            text(
                """
                UPDATE notification_requests
                SET status = CAST(:agotada AS notification_status),
                    error = COALESCE(error, :error)
                WHERE business_id = :business_id
                  AND status = 'pending'
                  AND attempts >= max_attempts
                """
            ),
            {
                "agotada": NotificationStatus.FAILED.value,
                "error": "Se agotaron los intentos de envio. Rastro del ultimo intento en los logs.",
                "business_id": str(business_id),
            },
        )
        return int(resultado.rowcount or 0)


async def _reclamar(
    session: AsyncSession, business_id: uuid.UUID, limit: int
) -> list[Notification]:
    """Toma hasta `limit` notificaciones vencidas. El `UPDATE` hace la exclusion."""
    result = await session.execute(
        text(
            """
            UPDATE notification_requests
            SET attempts = attempts + 1,
                scheduled_for = now() + make_interval(secs => :lease)
            WHERE business_id = :business_id
              AND status = 'pending'
              AND scheduled_for <= now()
              AND attempts < max_attempts
            RETURNING id, business_id, kind, attempts, max_attempts
            """
        ),
        {"business_id": str(business_id), "lease": LEASE.total_seconds()},
    )
    filas = result.fetchall()
    # El `LIMIT` va aca y no en el SQL a proposito: agregar `LIMIT` a un `UPDATE ...
    # RETURNING` es valido pero impede el `LIMIT` interno del plan, y lo que aca
    # importa es acotar cuanto trabajo carga el tick, no la cantidad de filas que el
    # planner decide bloquear.
    return [
        Notification(
            id=row[0],
            business_id=row[1],
            kind=str(row[2]),
            attempts=row[3],
            max_attempts=row[4],
        )
        for row in filas[:limit]
    ]


async def _transmitir(mensaje: Mensaje) -> TransmitResult:
    """Manda la plantilla a Meta. Sin sesion y sin transaccion a proposito: se
    invoca desde `_drain_business` entre dos `tenant_session` cortas, justamente
    para no tener un lock abierto mientras se espera a la red.

    Solo un `2xx` de la Graph API cuenta como enviado: en Meta el `2xx` es la
    aceptacion del mensaje para la cola de entrega, y el `message_id` que
    devuelve queda en `provider_message_id` de la fila como rastro. Un error
    transitorio (Meta caido, 4xx/5xx) devuelve `enviado=False` con el motivo, y
    el `_settle` lo deja en `pending` para reintentar con backoff.

    El token viaja por parametro y no se registra: `_transmitir` no loguea nada
    que pueda contenerlo.
    """
    cliente = whatsapp_client_factory(mensaje.phone_number_id, mensaje.access_token)
    try:
        enviado = await cliente.send_template(
            mensaje.destino,
            mensaje.template_name,
            language="es_AR",
            components=mensaje.components,
        )
        return TransmitResult(enviado=True, message_id=enviado.message_id)
    except httpx.HTTPError as exc:
        return TransmitResult(enviado=False, error=f"Meta rechazo el envio: {str(exc)[:200]}")
    finally:
        cerrar = getattr(cliente, "close", None)
        if cerrar is not None:
            await cerrar()


async def _resolver(session: AsyncSession, notificacion: Notification) -> Resolucion:
    """Arma el mensaje de plantilla a partir de la notificacion.

    Trae en una sola transaccion corta todo lo que el envio necesita y que solo
    se puede leer con el GUC de tenant puesto: el telefono del cliente (con el
    opt-out respetado), los nombres para las variables de la plantilla, la zona
    horaria del negocio y la configuracion de WhatsApp con su token descifrado.

    Tres motivos de salto terminal, cada uno con el mensaje que el panel va a
    mostrar en la fila:

    - sin destinatario elegible (opt-out o cliente inexistente);
    - WhatsApp no activo para el negocio (sin fila, desactivado o pendiente);
    - el tipo de aviso no tiene plantilla configurada.

    Cualquier otro resultado devuelve el `Mensaje` listo para la red.
    """
    fila = (
        await session.execute(
            text(
                """
                SELECT c.phone_e164,
                       c.first_name,
                       bz.name,
                       bz.timezone,
                       b.starts_at,
                       COALESCE(s.name, ''),
                       COALESCE(p.display_name, '')
                FROM notification_requests n
                JOIN bookings b ON b.id = n.booking_id AND b.business_id = n.business_id
                JOIN customers c ON c.id = n.customer_id AND c.business_id = n.business_id
                LEFT JOIN services s ON s.id = b.service_id AND s.business_id = b.business_id
                LEFT JOIN professionals p
                    ON p.id = b.professional_id AND p.business_id = b.business_id
                JOIN businesses bz ON bz.id = n.business_id
                WHERE n.id = :id
                  AND n.business_id = :business_id
                  AND c.is_opted_out IS NOT TRUE
                """
            ),
            {"id": str(notificacion.id), "business_id": str(notificacion.business_id)},
        )
    ).first()
    if fila is None:
        return Resolucion(skip_motivo="Sin destinatario elegible (opt-out o cliente inexistente).")

    conexion = (
        await session.execute(
            select(WhatsAppConnection).where(
                WhatsAppConnection.business_id == notificacion.business_id
            )
        )
    ).scalar_one_or_none()
    if conexion is None or not conexion.can_send:
        return Resolucion(
            skip_motivo=(
                "WhatsApp no activo para este negocio: configurar el Phone Number ID, "
                "el token y activar los recordatorios."
            )
        )

    plantilla = plantilla_de(notificacion.kind, conexion)
    if plantilla is None:
        return Resolucion(
            skip_motivo=f"Tipo de notificacion '{notificacion.kind}' sin plantilla configurada."
        )

    token = (
        decrypt_string(conexion.access_token_encrypted) if conexion.access_token_encrypted else ""
    )
    if not conexion.phone_number_id or not token:
        return Resolucion(
            skip_motivo="Faltan el Phone Number ID o el token de la conexion de WhatsApp."
        )

    (
        telefono,
        cliente_nombre,
        negocio_nombre,
        tz_name,
        starts_at,
        servicio_nombre,
        profesional_nombre,
    ) = fila
    zona = ZoneInfo(tz_name)
    local = starts_at.astimezone(zona)
    #: Orden de las variables del cuerpo de la plantilla (Fase D-3): nombre del
    #: cliente, servicio, fecha, hora, profesional y negocio.
    variables = [
        cliente_nombre,
        servicio_nombre,
        local.date().isoformat(),
        local.strftime("%H:%M"),
        profesional_nombre,
        negocio_nombre,
    ]
    components = [{"type": "body", "parameters": [{"type": "text", "text": v} for v in variables]}]
    return Resolucion(
        mensaje=Mensaje(
            # E.164 sin el `+`, como lo exige el campo `to` de la API de plantillas.
            destino=telefono.lstrip("+"),
            template_name=plantilla,
            components=components,
            phone_number_id=conexion.phone_number_id,
            access_token=token,
        )
    )


async def _settle(
    session: AsyncSession,
    notificacion: Notification,
    *,
    resultado: TransmitResult | None = None,
    skip_motivo: str | None = None,
) -> None:
    """Deja la notificacion en su estado final y programa el proximo intento.

    Tres desenlaces, y los tres son terminales o reintentables segun corresponda:

    - `skip_motivo` (sin destinatario, WhatsApp no activo, tipo sin plantilla):
      `skipped`, terminal. Ningun reintento lo arregla, y dejarlo para otro
      intento solo gasta presupuesto de reintentos y tapa el problema real.
    - `resultado.enviado` (Meta acepto): `sent`, terminal, con el `message_id`
      de Meta como rastro en `provider_message_id`.
    - `resultado` con error (Meta caido, 4xx/5xx): `pending` con backoff; si se
      agotaron los intentos, `failed`.

    **Comprueba el `rowcount` y el log solo se escribe si escribio.** El motivo es
    concreto: con RLS, un `UPDATE` sobre la tabla equivocada --o sin GUC de
    tenant-- no da error, actualiza cero filas y devuelve exito. Un log que dice
    "quedo en sent" cuando no se actualizo nada convierte un bug de aislamiento
    en un bug invisible, que es la peor forma de fallo que tiene este sistema.
    """
    agotados = notificacion.attempts >= notificacion.max_attempts

    if skip_motivo is not None:
        estado_final = "skipped"
        error = skip_motivo
    elif resultado is not None and resultado.enviado:
        estado_final = "sent"
        error = ""
    else:
        # Habia destinatario y configuracion, pero el envio no salio. `pending`
        # se reintenta con backoff; `failed` si se acabaron los intentos.
        estado_final = "pending" if not agotados else "failed"
        # `resultado.error` puede ser `None` en teoria; el texto de
        # aca es el que sale si pasa, para que `error` sea siempre texto.
        error = (
            resultado.error if resultado is not None else None
        ) or "Error de envio sin detalle."

    scheduled_for = (
        now() + dt.timedelta(minutes=2**notificacion.attempts)
        if estado_final == "pending"
        else now()
    )

    resultado_db = await session.execute(
        text(
            """
            UPDATE notification_requests
            SET status = CAST(:estado AS notification_status),
                error = :error,
                scheduled_for = :scheduled_for,
                sent_at = CASE WHEN :exitoso THEN now() ELSE sent_at END,
                provider_message_id = CASE WHEN :exitoso THEN :message_id
                                          ELSE provider_message_id END
            WHERE id = :id
              AND business_id = :business_id
            """
        ),
        {
            "estado": estado_final,
            "error": error[:500] or None,
            "scheduled_for": scheduled_for,
            # Un parametro booleano aparte, y no reusar `:estado` en el `CASE`.
            # Reusarlo obliga a PostgreSQL a deducir el tipo de `$1` como enum en la
            # asignacion y como texto en la comparacion, y de ahi sale
            # "se dedujeron tipos de dato inconsistentes" para un parametro. El
            # `CAST` explicito del `status` mas este flag separado lo evitan.
            "exitoso": estado_final == "sent",
            "message_id": (
                resultado.message_id if resultado is not None and resultado.enviado else None
            ),
            "id": str(notificacion.id),
            "business_id": str(notificacion.business_id),
        },
    )
    if resultado_db.rowcount != 1:
        # Se loguea como error y no como info porque significa que la invariante
        # "la fila que reclama el worker es la fila que actualiza" se rompio.
        logger.error(
            "outbox_settle_no_escribio",
            notificacion_id=str(notificacion.id),
            business_id=str(notificacion.business_id),
            filas_afectadas=resultado_db.rowcount,
        )
        return

    logger.info(
        "outbox_notificacion",
        notificacion_id=str(notificacion.id),
        kind=notificacion.kind,
        estado=estado_final,
        intento=notificacion.attempts,
    )


__all__ = [
    "BATCH_LIMIT",
    "LEASE",
    "DrainResult",
    "Mensaje",
    "Notification",
    "Resolucion",
    "TransmitResult",
    "businesses_with_work",
    "drain_once",
    "whatsapp_client_factory",
]
