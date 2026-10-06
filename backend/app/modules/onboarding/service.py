"""Alta self-service de un negocio: el unico camino que crea tenants.

**Por que este modulo existe y no es una funcion del router.** El alta es la unica
operacion del producto que necesita mas privilegios que operar *dentro* de un
tenant: `businesses` tiene `FORCE ROW LEVEL SECURITY`, su politica de `INSERT` es
`TO tempus_owner` (`0012`) y `tempus_app` no tiene permiso de tabla sobre ella. La
sesion que usa el resto de la API no puede escribir ahi, y una sesion de tenant
tampoco: todavia no hay tenant al que ponerle el GUC.

La puerta la abre `tempus_create_business`, una funcion `SECURITY DEFINER` de la
migracion `0014` que inserta en `businesses` y devuelve el id. Todo lo demas --el
usuario, el profesional y los horarios-- si lo puede escribir la sesion normal,
porque esas tablas son del tenant que ya existe y el GUC se pone en cuanto se tiene
el id. Por eso el modulo toca la base en dos pasos y no en uno.

**El orden de las operaciones es la garantia de atomicidad.** Todo va en una sola
transaccion--la de la dependencia `get_session`, que commitea al salir-- y en este
orden:

1. Se crea el negocio (via la funcion). Devuelve el `business_id`.
2. Se pone el GUC de tenant con ese id.
3. Se escriben `business_users`, `professionals` y `business_hours`.

El paso 2 va entre el 1 y el 3 porque esas tres tablas tienen RLS: sin el GUC el
`INSERT` falla por violacion de politica, que es el sintoma correcto. Ponerlo antes
no serviria de nada, porque el id todavia no existe.

**Lo que este modulo NO hace, a proposito:**

- **No reserva el slug con una tabla intermedia.** `slug_reservations` guarda las
  palabras que hay que quitar *antes* de que exista ningun negocio, y su chequeo va
  dentro de la funcion de `0014`--que es la unica que puede leerla, porque
  `tempus_app` no tiene permiso ni de lectura sobre esa tabla. La unicidad entre
  negocios la arbitra el indice `UNIQUE` de `businesses.slug`, que es la
  autoridad: dos altas del mismo slug a la vez, una gana y la otra recibe el error
  de constraint. Una tabla de reservas intermediaria agregaria una segunda fuente
  de verdad que puede desincronizarse del indice sin que nada falle.
- **No manda ningun email.** No hay proveedor de email en el stack, y el acceso al
  panel es el efecto del alta.
- **No crea servicios ni clientes de ejemplo.** El negocio arranca vacio: un
  servicio de ejemplo que el dueno no borro aparece en su pagina publica y es un
  turno reservado que nadie puede tomar.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ConflictError, ValidationError
from app.core.security import hash_password
from app.core.time import now
from app.db.session import TENANT_GUC
from app.models.enums import BusinessUserRole, MembershipStatus
from app.modules.auth.models import BusinessUser
from app.modules.businesses import service as negocios
from app.modules.professionals.models import Professional
from app.modules.schedules.models import BusinessHour

#: `weekday` de lunes a sabado con la convencion del modelo: 0 = lunes (ADR-0005).
DEFAULT_WEEKDAYS: tuple[int, ...] = (0, 1, 2, 3, 4, 5)

#: Ventanas por defecto, en hora local del negocio. Dos por dia y no una: el modelo
#: no tiene descanso dentro de una ventana (§11), asi que una jornada partida son
#: dos filas.
DEFAULT_VENTANAS: tuple[tuple[dt.time, dt.time], ...] = (
    (dt.time(9, 0), dt.time(12, 0)),
    (dt.time(16, 0), dt.time(20, 0)),
)

#: SQLSTATE que levanta `tempus_create_business` cuando el slug esta en
#: `slug_reservations`. Distinto del `23505` del indice UNIQUE, y esa distincion es
#: lo que permite decir "ya esta en uso" o "esta reservado" en vez de un 409
#: generico que no dice que hacer.
SQLSTATE_SLUG_RESERVADO = "23514"
SQLSTATE_UNIQUE = "23505"

#: Llamada a la funcion privilegiada. Va en un `text()` y no en una expresion de
#: SQLAlchemy a proposito: es DDL de la migracion, no parte del modelo, asi que no
#: tiene por que aparecer en el `MetaData` ni cambiar si le agregan un parametro.
_INSERTAR_NEGOCIO = text(
    "SELECT tempus_create_business("
    "  CAST(:slug AS citext), :nombre, :zona, :telefono"
    ") AS business_id"
)


@dataclass(frozen=True, slots=True)
class NegocioRegistrado:
    """Lo que produjo el alta. Los ids van porque el router los responde."""

    business_id: uuid.UUID
    slug: str
    user_id: uuid.UUID
    professional_id: uuid.UUID


def _sqlstate(exc: DBAPIError) -> str | None:
    """El SQLSTATE de un error de driver, o `None` si no se puede leer.

    Vive aislado en una funcion porque el camino para llegar al codigo--`sqlstate`
    en asyncpg, `diag.sqlstate` en el driver sync-- varia, y porque un test la
    puede ejercitar con una excepcion armada sin levantar Postgres. El
    `getattr` en cadena escrito en el `except` seria el tipo de cosa que se rompe
    en silencio cuando el driver cambia.
    """
    original = exc.orig
    codigo = getattr(original, "sqlstate", None)
    if codigo is not None:
        return str(codigo)
    diag = getattr(original, "diag", None)
    valor = getattr(diag, "sqlstate", None)
    return None if valor is None else str(valor)


def _validar_zona_horaria(valor: str) -> str:
    """Verifica que la zona exista antes de escribirla.

    Una zona invalida no falla en el alta: falla meses despues, el dia que el
    negocio nuevo no muestra disponibilidad. Es el mismo error que la validacion de
    `Settings` evita para el proceso, y aca hace falta para el tenant: cada negocio
    tiene la suya y se escribe desde una peticion publica.
    """
    if not valor.strip():
        raise ValidationError("La zona horaria es obligatoria.")
    try:
        ZoneInfo(valor)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ValidationError(f"Zona horaria desconocida: {valor}.") from exc
    return valor.strip()


def _horarios_por_defecto(business_id: uuid.UUID) -> list[BusinessHour]:
    """Las filas de `business_hours` del alta: lunes a sabado, dos ventanas.

    **Domingo sin filas, a proposito.** El modelo tiene `is_open = false` para que
    el admin vea el domingo en la grilla como una decision tomada, pero el registro
    por defecto no lo siembra: la ausencia de filas ya significa "cerrado", y la
    fila cerrada seria una fila mas que el admin tiene que editar cuando quiera
    abrir.
    """
    filas: list[BusinessHour] = []
    for weekday in DEFAULT_WEEKDAYS:
        for indice, (inicio, fin) in enumerate(DEFAULT_VENTANAS):
            filas.append(
                BusinessHour(
                    business_id=business_id,
                    weekday=weekday,
                    window_index=indice,
                    start_time=inicio,
                    end_time=fin,
                    is_open=True,
                )
            )
    return filas


async def _crear_negocio(
    session: AsyncSession,
    *,
    slug: str,
    nombre: str,
    zona: str,
    telefono: str | None,
) -> uuid.UUID:
    """Inserta el negocio por la funcion privilegiada y devuelve su id.

    **El error de la base se traduce y no se propaga.** Los unicos conflictos reales
    aca son el indice UNIQUE de `businesses.slug`--dos altas simultaneas del mismo
    slug-- y el `23514` que la funcion levanta para una palabra reservada. Crudo,
    ninguno de los dos le dice nada a quien esta creando su negocio; y un 500 en un
    alta self-service se lee como "Tempus esta caido".
    """
    try:
        resultado = await session.execute(
            _INSERTAR_NEGOCIO,
            {"slug": slug, "nombre": nombre, "zona": zona, "telefono": telefono},
        )
    except DBAPIError as exc:
        codigo = _sqlstate(exc)
        if codigo == SQLSTATE_SLUG_RESERVADO:
            raise ConflictError(f"El nombre '{slug}' esta reservado. Proba con otro.") from exc
        if codigo == SQLSTATE_UNIQUE:
            raise ConflictError(f"El nombre '{slug}' ya esta en uso.") from exc
        raise

    business_id: uuid.UUID = resultado.scalar_one()
    return business_id


async def registrar_negocio(
    session: AsyncSession,
    *,
    business_name: str,
    slug: str,
    owner_name: str,
    email: str,
    password: str,
    phone: str | None = None,
    timezone: str,
) -> NegocioRegistrado:
    """Crea el negocio con su dueno, su profesional y sus horarios. Atomico.

    **La transaccion es la del llamador.** Esta funcion hace `flush`, nunca
    `commit`: commitear adentro partiria el alta en dos, y un fallo del handler
    dejaria el negocio creado sin dueno--que es el peor estado posible, porque el
    dueno ya se leyo el mensaje de exito.
    """
    nombre = business_name.strip()
    if len(nombre) < 2:
        raise ValidationError("El nombre del negocio es demasiado corto.")
    duenio = owner_name.strip()
    if len(duenio) < 2:
        raise ValidationError("El nombre del propietario es demasiado corto.")

    # El slug se canonicaliza con la misma `slugify` que usa el selector del panel.
    # No se reimplementa el patron aca: dos reglas distintas harian que `/register`
    # y el panel negaran nombres diferentes para el mismo texto, y el primero en
    # responder se queda con el slug.
    slug_canonico = negocios.slugify(slug)
    if not slug_canonico:
        raise ValidationError("El nombre de URL no puede quedar vacio.")
    negocios.validar_slug(slug_canonico)

    zona = _validar_zona_horaria(timezone)

    business_id = await _crear_negocio(
        session,
        slug=slug_canonico,
        nombre=nombre,
        zona=zona,
        telefono=phone.strip() if phone else None,
    )

    await session.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(business_id)},
    )

    owner = BusinessUser(
        business_id=business_id,
        # `casefold` y no `lower`, igual que en el login: `Duenio@x.com` y
        # `duenio@x.com` tienen que ser el mismo miembro y no dos.
        email=email.strip().casefold(),
        password_hash=hash_password(password),
        full_name=duenio,
        role=BusinessUserRole.ADMIN,
        # `ACTIVE` y no `invited`: el dueno eligio su propia contrasena en este
        # formulario, asi que no hay nada que confirmar. Un `invited` al que se le
        # acaba de poner la contrasena seria un estado que el login rechaza y que
        # el dueno no entenderia.
        status=MembershipStatus.ACTIVE,
        invited_at=now(),
    )
    session.add(owner)
    await session.flush()

    profesional = Professional(
        business_id=business_id,
        # La FK es compuesta `(user_id, business_id)`. Sin el `business_id` el id
        # del dueno cruza tenants: un bug que propague mal el id haria que el
        # profesional del negocio A quedara vinculado al login del negocio B.
        user_id=owner.id,
        display_name=duenio,
        is_active=True,
    )
    session.add(profesional)

    for fila in _horarios_por_defecto(business_id):
        session.add(fila)

    await session.flush()

    return NegocioRegistrado(
        business_id=business_id,
        slug=slug_canonico,
        user_id=owner.id,
        professional_id=profesional.id,
    )


__all__ = [
    "DEFAULT_VENTANAS",
    "DEFAULT_WEEKDAYS",
    "SQLSTATE_SLUG_RESERVADO",
    "SQLSTATE_UNIQUE",
    "NegocioRegistrado",
    "registrar_negocio",
]
