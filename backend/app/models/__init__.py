"""Punto unico de importacion del ORM.

Los modelos viven con su dominio, en `app/modules/*/models.py`. Este modulo los
reexporta por dos razones concretas:

1. `migrations/env.py` importa **un** modulo. Si la migracion importara cada
   dominio, el orden de importacion decidiria que tablas existen, y una tabla
   faltante en el autogenere se traduce en un `DROP TABLE` propuesto.

2. Los repositorios importan desde `app.models` y no desde el dominio. Asi el
   `rg` de "quien usa Booking" no depende de la ruta del modulo.

Un test (`tests/unit/test_orm_metadata.py`) verifica que este modulo expone
exactamente las mismas tablas que `Base.metadata`, para que la lista no se
desactualice en silencio.

**Por que el reexport es perezoso.** `enums` y `sql_types` son las hojas del
paquete: no importan nada de la app. Pero los modelos de dominio los importan
(`from app.models.enums import BookingStatus`), e importar un submódulo ejecuta
primero el `__init__` del paquete. Si el `__init__` importara los modelos, el
orden seria:

    `app.models.__init__` -> `modules.bookings.models` -> `app.models.enums`
    -> ejecuta `app.models.__init__` otra vez, a medio construir -> los modelos
    de `bookings` todavia no existen -> `ImportError`

Es un ciclo real, y su sintoma es desconcertante: importar `app.models` anda
perfecto, e importar `app.modules.bookings.models` primero falla. Como los
routers importan los dominios, `from app.main import app` fallaba y la app
arrancaba sin una sola ruta de la API registrada, sin error visible.

`__getattr__` resuelve los dos casos a la vez: el paquete no importa nada al
cargarse, y `from app.models import Booking` sigue funcionando, con el import
real ocurriendo en el primer acceso al atributo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - solo para el chequeo de tipos
    # Los nombres siguen estando declarados para el type checker y para el IDE.
    from app.db.base import Base, GlobalBase, TenantBase
    from app.db.system_models import AuditLog, Job, RateLimitBucket
    from app.models.enums import (
        AuditActorType,
        BlockKind,
        BookingEventType,
        BookingSource,
        BookingStatus,
        BusinessStatus,
        BusinessUserRole,
        JobKind,
        JobStatus,
        MediaKind,
        MediaStatus,
        MembershipStatus,
        NotificationChannel,
        NotificationKind,
        NotificationStatus,
        PlatformRole,
        TemplateStatus,
        TimeOffKind,
        TimeOffStatus,
        WebhookProvider,
        WebhookStatus,
        WhatsAppConnectionStatus,
    )
    from app.modules.auth.models import BusinessUser, PlatformUser, RefreshToken
    from app.modules.bookings.models import Booking, BookingEvent, IdempotencyKey
    from app.modules.businesses.models import Business, Media, SlugReservation
    from app.modules.customers.models import Customer
    from app.modules.notifications.models import (
        NotificationRequest,
        WebhookEvent,
        WhatsAppConnection,
        WhatsAppTemplate,
    )
    from app.modules.professionals.models import Professional
    from app.modules.schedules.models import (
        Block,
        BusinessException,
        BusinessExceptionWindow,
        BusinessHour,
        Holiday,
        ProfessionalSchedule,
        TimeOff,
    )
    from app.modules.services.models import ProfessionalService, Service


#: De donde viene cada nombre reexportado. El mapa es lo que hace posible el
#: `__getattr__` de mas abajo: la alternativa -- un `importlib.import_module` con
#: el nombre derivado del atributo -- funciona solo para modulos, y aqui los
#: nombres no coinciden con los de sus modulos (`BookingStatus` vive en
#: `app.models.enums`, `Booking` en `app.modules.bookings.models`).
_EXPORTS: dict[str, str] = {
    "Base": "app.db.base",
    "GlobalBase": "app.db.base",
    "TenantBase": "app.db.base",
    "AuditLog": "app.db.system_models",
    "Job": "app.db.system_models",
    "RateLimitBucket": "app.db.system_models",
    "AuditActorType": "app.models.enums",
    "BlockKind": "app.models.enums",
    "BookingEventType": "app.models.enums",
    "BookingSource": "app.models.enums",
    "BookingStatus": "app.models.enums",
    "BusinessStatus": "app.models.enums",
    "BusinessUserRole": "app.models.enums",
    "JobKind": "app.models.enums",
    "JobStatus": "app.models.enums",
    "MediaKind": "app.models.enums",
    "MediaStatus": "app.models.enums",
    "MembershipStatus": "app.models.enums",
    "NotificationChannel": "app.models.enums",
    "NotificationKind": "app.models.enums",
    "NotificationStatus": "app.models.enums",
    "PlatformRole": "app.models.enums",
    "TemplateStatus": "app.models.enums",
    "TimeOffKind": "app.models.enums",
    "TimeOffStatus": "app.models.enums",
    "WebhookProvider": "app.models.enums",
    "WebhookStatus": "app.models.enums",
    "WhatsAppConnectionStatus": "app.models.enums",
    "BusinessUser": "app.modules.auth.models",
    "PlatformUser": "app.modules.auth.models",
    "RefreshToken": "app.modules.auth.models",
    "Booking": "app.modules.bookings.models",
    "BookingEvent": "app.modules.bookings.models",
    "IdempotencyKey": "app.modules.bookings.models",
    "Business": "app.modules.businesses.models",
    "Media": "app.modules.businesses.models",
    "SlugReservation": "app.modules.businesses.models",
    "Customer": "app.modules.customers.models",
    "NotificationRequest": "app.modules.notifications.models",
    "WebhookEvent": "app.modules.notifications.models",
    "WhatsAppConnection": "app.modules.notifications.models",
    "WhatsAppTemplate": "app.modules.notifications.models",
    "Professional": "app.modules.professionals.models",
    "Block": "app.modules.schedules.models",
    "BusinessException": "app.modules.schedules.models",
    "BusinessExceptionWindow": "app.modules.schedules.models",
    "BusinessHour": "app.modules.schedules.models",
    "Holiday": "app.modules.schedules.models",
    "ProfessionalSchedule": "app.modules.schedules.models",
    "TimeOff": "app.modules.schedules.models",
    "ProfessionalService": "app.modules.services.models",
    "Service": "app.modules.services.models",
}


def __getattr__(name: str) -> Any:
    """Resuelve un nombre reexportado, importando su modulo la primera vez.

    `importlib.import_module` + `getattr` es lo que evita que el paquete se
    construya a medias. Cuando `app.modules.bookings.models` importa
    `app.models.enums`, Python importa `app.models` -- encuentra este
    `__getattr__`, no encuentra `BookingStatus` en el namespace del paquete porque
    aun no se pidio, importa `app.models.enums` y devuelve el enum. Ningun ciclo.
    """
    # Las constantes derivadas de la metadata no son reexportaciones sino calculos,
    # y no se cachean: ver `_constants`.
    if name in _CONSTANT_NAMES:
        return _constants()[name]

    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    import importlib

    value = getattr(importlib.import_module(module_path), name)
    # Este si se cachea: el modulo importado no va a cambiar, y no hay nada que
    # invalidar. El segundo acceso no vuelve a importar.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Lo que aparece en un `dir(app.models)`. Sin esto los nombres lazy no se ven."""
    return sorted(set(globals()) | set(_EXPORTS) | _CONSTANT_NAMES)


#: Tablas que llevan `business_id` y por lo tanto RLS. Se deriva de los modelos,
#: no de una lista escrita a mano: una lista manual se desactualiza en silencio.
#:
#: La excepcion es `jobs`. La regla "tiene business_id => es tabla de tenant" no
#: sirve para la cola: el dispatcher saca trabajos de muchas cuentas a la vez y no
#: puede poner un unico `SET LOCAL` antes de cada lectura. El aislamiento de `jobs`
#: no lo da la RLS, lo da el handler, que abre su propia transaccion con el tenant
#: del job y **falla ruidosamente** si el job no lo tiene. Declarar la excepcion de
#: forma explicita es lo que evita que la proxima tabla con `business_id` y sin RLS
#: pase desapercibida.
NO_RLS_TABLES: frozenset[str] = frozenset({"jobs"})


#: Tablas **globales**--sin columna `business_id`-- que aun asi llevan RLS.
#:
#: Que una tabla sea global dice *como se identifica una fila*, no *si necesita
#: aislamiento*. Confundir las dos cosas es lo que hacia fallar
#: `test_cada_tabla_tenant_tiene_exactamente_una_politica`: el test asumia que toda
#: tabla global era una tabla sin RLS, y `businesses` dejo de cumplirlo cuando
#: `0011_businesses_rls_write` le agrego politicas--a proposito.
#:
#: `businesses` es global--se identifica por su propio `id`, no por `business_id`-- y
#: sin embargo necesita RLS por una razon puntual: el flujo publico tiene que
#: **resolver** el negocio por `slug` antes de saber cual es el tenant, y eso exige un
#: `SELECT` sin GUC. El resto de sus operaciones--editar la configuracion-- si van
#: atadas al GUC, y el alta--que es la operacion de onboarding-- se reserva al rol de
#: DDL. Tres politicas distintas para tres operaciones distintas, y ninguna es "esta
#: tabla no necesita aislamiento".
#:
#: Se declara aca y no en el test para que la excepcion quede a la vista de quien lea
#: el modelo, y no como una lista magicallya dentro de una asercion.
GLOBAL_TABLES_WITH_RLS: frozenset[str] = frozenset({"businesses"})


def _tablas() -> frozenset[str]:
    """Los nombres de tabla, forzando el registro de los modelos.

    Se llama desde `_constants()` y no al import del modulo, por el mismo motivo
    que el `__getattr__`: leer `Base.metadata` exige que todos los modelos esten
    importados, y eso aqui es trabajo diferido.
    """
    base = __getattr__("Base")
    return frozenset(base.metadata.tables)


#: Modulos ya importados por `_importar_todos`. Es lo que evita que un acceso a
#: `TABLE_COUNT` reimporte los 27 modelos en cada llamada.
_importados: set[str] = set()


def _importar_todos() -> None:
    """Importa todos los modulos con modelos, una sola vez.

    Los modelos registran su tabla al importarse. Con el reexport perezoso, leer
    `Base.metadata` no garantiza que se hayan importado todos, y sin esto
    `TABLE_COUNT` seria 0 para quien consultara el paquete antes que cualquier
    dominio.
    """
    import importlib

    for module_path in dict.fromkeys(_EXPORTS.values()):
        if module_path in _importados:
            continue
        _importados.add(module_path)
        importlib.import_module(module_path)


def registrar_modelos() -> None:
    """Importa todos los modelos. Idempotente.

    Existe para el unico consumidor que **no** puede permitirse el reexport
    perezoso: `migrations/env.py`. Ahi `import app.models` no registra ni una tabla,
    porque el paquete no importa nada al construirse--por el ciclo documentado
    arriba-- y `target_metadata` queda vacio.

    Con la metadata vacia, `alembic check` y `--autogenerate` proponen **borrar las
    27 tablas**: el diff es exactamente "remove_table" para cada una. El sintoma es
    especialmente traicionero porque el grafo de migraciones esta impecable y
    `alembic heads` coincide; lo unico que cambia es el modelo, y este modulo--que
    "reexporta los modelos"-- es el que lo deja sin registrar.

    Por eso es una funcion publica y no una constante mas: `env.py` tiene que
    llamarla de forma explicita, y que quede escrito en el import deja claro que
    el registro es una precondicion del autogenerate y no una consecuencia de
    importar el paquete.
    """
    _importar_todos()


def _constants() -> dict[str, Any]:
    """Las constantes derivadas de la metadata. Se calculan en cada acceso.

    **No se cachean, a proposito.** Un cache necesaria invalidacion, y cualquier
    invalidacion depende de acertar el momento exacto en que cambia la metadata.
    Acertarlo significa enganchar un evento de SQLAlchemy, y esa clase de bug es
    la peor posible: el mismo codigo devuelve numeros distintos segun que modulo
    se importo primero, y el fallo aparece en un test que compara `TABLE_COUNT`
    contra la documentacion sin senalar que la causa es el orden de importacion.

    Recorrer 27 tablas y mirar sus columnas cuesta microsegundos, y a estos
    niveles se lee una vez por proceso. El tradeoff es asimetrico y obvio: no
    cachear cuesta nanosegundos por arranque, cachear mal cuesta un bug que
    aparece sin relacion con su causa.
    """
    # Los modelos tienen que estar importados antes de mirarlos: cada uno registra
    # su tabla al ser importado, y `Base.metadata` arranca vacio. Como el
    # reexport es perezoso, "estar importados" ya no es una consecuencia de
    # importar este paquete, asi que hay que dispararlo explicitamente.
    _importar_todos()

    base = __getattr__("Base")
    tables = base.metadata.tables
    tenant = frozenset(
        name
        for name, table in tables.items()
        if "business_id" in table.c and name not in NO_RLS_TABLES
    )
    return {
        "TENANT_TABLES": tenant,
        "GLOBAL_TABLES": frozenset(
            name
            for name, table in tables.items()
            if "business_id" not in table.c or name in NO_RLS_TABLES
        ),
        "GLOBAL_TABLES_WITH_RLS": GLOBAL_TABLES_WITH_RLS,
        "ALL_TABLES": tuple(sorted(tables)),
        # La primera version de `ARCHITECTURE.md` decia 26. El numero real es 27: la
        # enumeracion de la seccion se saltaba `webhook_events`. Se expone como
        # constante para que `tests/unit/test_orm_metadata.py` compare la metadata
        # real contra el numero de la documentacion, y el desync falle en un test.
        "TABLE_COUNT": len(tables),
        "FORBIDDEN_FOR_APP_ROLE": frozenset({"platform_users", "businesses", "slug_reservations"}),
    }


_CONSTANT_NAMES = frozenset(
    {
        "TENANT_TABLES",
        "GLOBAL_TABLES",
        "GLOBAL_TABLES_WITH_RLS",
        "ALL_TABLES",
        "TABLE_COUNT",
        "FORBIDDEN_FOR_APP_ROLE",
    }
)
__all__ = [
    "ALL_TABLES",
    "FORBIDDEN_FOR_APP_ROLE",
    "GLOBAL_TABLES",
    "GLOBAL_TABLES_WITH_RLS",
    "NO_RLS_TABLES",
    "TABLE_COUNT",
    "TENANT_TABLES",
    "AuditActorType",
    "AuditLog",
    "Base",
    "Block",
    "BlockKind",
    "Booking",
    "BookingEvent",
    "BookingEventType",
    "BookingSource",
    "BookingStatus",
    "Business",
    "BusinessException",
    "BusinessExceptionWindow",
    "BusinessHour",
    "BusinessStatus",
    "BusinessUser",
    "BusinessUserRole",
    "Customer",
    "GlobalBase",
    "Holiday",
    "IdempotencyKey",
    "Job",
    "JobKind",
    "JobStatus",
    "Media",
    "MediaKind",
    "MediaStatus",
    "MembershipStatus",
    "NotificationChannel",
    "NotificationKind",
    "NotificationRequest",
    "NotificationStatus",
    "PlatformRole",
    "PlatformUser",
    "Professional",
    "ProfessionalSchedule",
    "ProfessionalService",
    "RateLimitBucket",
    "RefreshToken",
    "Service",
    "SlugReservation",
    "TemplateStatus",
    "TenantBase",
    "TimeOff",
    "TimeOffKind",
    "TimeOffStatus",
    "WebhookEvent",
    "WebhookProvider",
    "WebhookStatus",
    "WhatsAppConnection",
    "WhatsAppConnectionStatus",
    "WhatsAppTemplate",
    "registrar_modelos",
]
