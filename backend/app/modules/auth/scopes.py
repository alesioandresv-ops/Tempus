"""Scopes: que puede hacer cada rol, en una sola tabla.

El §10.4 define las capacidades en una tabla de texto. Este modulo la vuelve codigo,
y el unico trabajo real es **no perder informacion al traducirla**: una capacidad que
no se puede nombrar no se puede proteger, y una capacidad nombrada pero nunca usada es
una puerta abierta esperando.

Por que scopes y no comparar el rol en cada handler. Un `if user.role == Role.ADMIN`
repetido en cien handlers es cien oportunidades de escribirlo mal, y la que se
escriba mal no va a fallar en los tests: va a *funcionar* y a dejar pasar a un
`staff`. Con un scope, la pregunta es siempre la misma y la respuesta esta en un lugar.

Por que el scope va **dentro** del token y no se recalcula en cada request. Un scope
en el JWT es una decision que queda congelada por 15 minutos, la vida del access token.
Eso es aceptable y es el precio de no consultar la base en cada peticion. Lo que no es
aceptable es que el token sea la *unica* fuente: por eso el guard de refresco vuelve a
recalcular los scopes contra la base y emite un token nuevo. El token es una cache con
caducidad, no una fuente de verdad. Un token de 15 minutos sobrevive a que le bajen el
rol, y eso es la ventana de exposicion que el §10.4 acepta a cambio de no pegarle a la
base en cada peticion.

Relacion con el profesional. `professional` es un rol de `business_user_role` y por
eso tiene su propia fila aca. Sus scopes son todos `*:own`, y ninguna capacidad de
equipo. La distincion entre `own` y `any` no es decorativa: es la que impide que un
login de profesional lea la agenda de otro profesional, y por eso los dos estan en el
nombre del scope y no inferidos del recurso.
"""

from __future__ import annotations

from enum import StrEnum

from app.models.enums import BusinessUserRole, PlatformRole


class Scope(StrEnum):
    """Un permiso, con nombre `recurso:accion[:alcance]`.

    El alcance es `own` o `any` y va en el nombre a proposito. Con el alcance en el
    scope, `require_scopes(Scope.BOOKINGS_READ_ANY)` no admite discusion: o el token lo
    tiene o no. Si el alcance se dedujera del recurso en el handler, cada handler
    tendria que acordarse de filtrar, y la forgets es un `WHERE professional_id` de
    menos.
    """

    # --- Configuracion del negocio ---
    BUSINESS_CONFIG_READ = "business:config:read"
    BUSINESS_CONFIG_WRITE = "business:config:write"

    # --- Equipo: profesionales y servicios ---
    TEAM_READ = "team:read"
    TEAM_WRITE = "team:write"

    # --- Reservas ---
    BOOKINGS_READ_OWN = "bookings:read:own"
    BOOKINGS_WRITE_OWN = "bookings:write:own"
    BOOKINGS_READ_ANY = "bookings:read:any"
    BOOKINGS_WRITE_ANY = "bookings:write:any"
    BOOKINGS_WALKIN = "bookings:walkin"

    # --- Clientes ---
    CLIENTS_READ = "clients:read"
    CLIENTS_WRITE = "clients:write"

    # --- Agenda publica del negocio (consultar sin ser el dueno) ---
    AVAILABILITY_READ = "availability:read"


class PlatformScope(StrEnum):
    """Permisos de operador de plataforma.

    Van en un enum aparte y no mezclados con `Scope` a proposito. Un operador de
    plataforma no administra un negocio: opera **sobre** negocios, y darle scopes de
    negocio lo volveria indistinguible de un `admin` con la diferencia de que ademas
    puede saltar de tenant. Que sea un tipo distinto hace que el error sea visible en
    la firma del handler en vez de aparecer en una comparacion de rol.
    """

    TENANTS_READ = "platform:tenants:read"
    TENANTS_WRITE = "platform:tenants:write"
    SUPPORT_IMPERSONATE_READ = "platform:support:read"


#: Scopes de cada rol de negocio. El unico lugar donde un rol se convierte en poder.
#:
#: `admin` tiene todo lo de `staff` mas la configuracion, que es exactamente la
#: distincion que describe el §10.4: delegar sin dar el control de la configuracion.
BUSINESS_ROLE_SCOPES: dict[BusinessUserRole, frozenset[Scope]] = {
    BusinessUserRole.ADMIN: frozenset(
        {
            Scope.BUSINESS_CONFIG_READ,
            Scope.BUSINESS_CONFIG_WRITE,
            Scope.TEAM_READ,
            Scope.TEAM_WRITE,
            Scope.BOOKINGS_READ_OWN,
            Scope.BOOKINGS_WRITE_OWN,
            Scope.BOOKINGS_READ_ANY,
            Scope.BOOKINGS_WRITE_ANY,
            Scope.BOOKINGS_WALKIN,
            Scope.CLIENTS_READ,
            Scope.CLIENTS_WRITE,
            Scope.AVAILABILITY_READ,
        }
    ),
    BusinessUserRole.STAFF: frozenset(
        {
            Scope.TEAM_READ,
            Scope.TEAM_WRITE,
            Scope.BOOKINGS_READ_OWN,
            Scope.BOOKINGS_WRITE_OWN,
            Scope.BOOKINGS_READ_ANY,
            Scope.BOOKINGS_WRITE_ANY,
            Scope.BOOKINGS_WALKIN,
            Scope.CLIENTS_READ,
            Scope.CLIENTS_WRITE,
            Scope.AVAILABILITY_READ,
        }
    ),
    BusinessUserRole.PROFESSIONAL: frozenset(
        {
            Scope.BOOKINGS_READ_OWN,
            Scope.BOOKINGS_WRITE_OWN,
            Scope.BOOKINGS_WALKIN,
            Scope.CLIENTS_READ,
            Scope.AVAILABILITY_READ,
        }
    ),
}

#: Scopes de cada rol de plataforma.
PLATFORM_ROLE_SCOPES: dict[PlatformRole, frozenset[PlatformScope]] = {
    PlatformRole.OWNER: frozenset(
        {
            PlatformScope.TENANTS_READ,
            PlatformScope.TENANTS_WRITE,
            PlatformScope.SUPPORT_IMPERSONATE_READ,
        }
    ),
    PlatformRole.ADMIN: frozenset(
        {
            PlatformScope.TENANTS_READ,
            PlatformScope.TENANTS_WRITE,
            PlatformScope.SUPPORT_IMPERSONATE_READ,
        }
    ),
    # `support` lee para responder tickets y no escribe tenants. Es el rol que
    # existe para poder(let) ayudar sin poder romper.
    PlatformRole.SUPPORT: frozenset(
        {
            PlatformScope.TENANTS_READ,
            PlatformScope.SUPPORT_IMPERSONATE_READ,
        }
    ),
}


def scopes_for_business_role(role: BusinessUserRole) -> frozenset[Scope]:
    """Los scopes de un rol de negocio.

    Levanta `KeyError` para un rol que no este en el mapa, a proposito: un rol nuevo
    sin scopes no es un rol sin permisos, es un rol que nadie penso. Fallar al
    arrancar es mejor que emitir tokens sin permisos y descubrirlo en produccion.
    """
    return BUSINESS_ROLE_SCOPES[role]


def scopes_for_platform_role(role: PlatformRole) -> frozenset[PlatformScope]:
    """Los scopes de un rol de plataforma. Mismo criterio que el de negocio."""
    return PLATFORM_ROLE_SCOPES[role]


def as_strings(scopes: frozenset[Scope] | frozenset[PlatformScope]) -> list[str]:
    """Los scopes como lista de strings, que es lo que va al claim `scopes`.

    Se ordena para que el token sea deterministico: dos llamadas con los mismos
    scopes producen el mismo string, lo que hace comparables los tokens en los tests y
    evita que un cambio de orden en el `set` se vea como un token distinto.
    """
    return sorted(str(scope) for scope in scopes)


__all__ = [
    "BUSINESS_ROLE_SCOPES",
    "PLATFORM_ROLE_SCOPES",
    "PlatformScope",
    "Scope",
    "as_strings",
    "scopes_for_business_role",
    "scopes_for_platform_role",
]
