"""Autenticacion: resolver credenciales, emitir access token y abrir la familia.

Este modulo es el unico que decide si alguien entra. Todo lo demas -- el router, los
guards -- consume lo que sale de aca.

**El orden de las operaciones, y por que es este.** La regla que atraviesa el modulo
es que el camino de un email inexistente y el de una contrasena incorrecta hagan
*exactamente el mismo trabajo criptografico*. Eso obliga a un orden concreto:

1. **Rate limit** (IP y email). Barato, y va primero a proposito: si va ultimo, cada
   intento de fuerza bruta paga un Argon2 completo antes de ser rechazado, y el
   rate limit pasa a ser un detalle economico en vez de una barrera.
2. **Lookup** por email, con las funciones `SECURITY DEFINER` de `0004`. Es la unica
   forma de resolver identidad sin tenant: la RLS de `business_users` devuelve cero
   filas sin el GUC, por construccion.
3. **Verificacion de la contrasena**, siempre al menos una operacion de Argon2. Si
   el lookup no trajo filas, se verifica contra el hash centinela. Aca esta la
   propiedad que sostiene todo lo demas.
4. **Estado de la cuenta**. Desempues, y no antes.
5. **Ambiguedad de tenant**, que se decide solo despues de haber probado la
   contrasena.
6. **Emision**: access token, refresh inicial, cookie.

El paso 4 va despues del 3 por una razon concreta: si el estado se mirara antes, un
`invited` responderia sin verificar la contrasena, y el tiempo de respuesta
distinguiria "existe pero esta invitado" de "existe y la contrasena esta mal" sin
necesidad de adivinar nada. Verificar siempre y decidir el estado despues hace que
el estado de la cuenta sea invisible desde afuera, para bien y para mal: un
`invited` ve exactamente el mismo 401 que un email que no existe.

**Por que el rol de la app no toca `platform_users` ni escribe `password_hash`.** No
es una decision de este modulo: son los permisos de `0002`, que quitan todo privilegio
sobre `platform_users` y restringen el `UPDATE` de `business_users` a
`(email, full_name, last_login_at, invited_at)`. Por eso el lookup de plataforma pasa
por su propia funcion `SECURITY DEFINER`, y por eso aca se usa `verify_password` y no
`verify_and_update_password`: esta ultima devuelve un hash nuevo cuando los
parametros de Argon2 cambiaron, y guardarlo exigiria un `UPDATE` de
`password_hash` que el rol de la app **no tiene**. Usarla y descartar el hash nuevo
seria una operacion Argon2 extra en cada login viejo a cambio de nada.

**Que NO hace este modulo, a proposito.**

- **No escribe `last_login_at`.** Es posible en `business_users` -- el rol puede, la
  columna esta en la lista de escritura -- pero la escritura necesita el contexto de
  tenant, que todavia no existe durante el login, asi que habria que cambiar el GUC
  dentro de la misma transaccion que hizo el lookup. Y en `platform_users` es
  **imposible**: el rol de la app no tiene ningun privilegio sobre la tabla, asi que
  no hay forma de registrar el ultimo login de un operador sin una funcion
  `SECURITY DEFINER` nueva. Hacerlo solo para la mitad deja el dato mas completo en
  un lado y dishonesto en el otro. Queda para la migracion que lo resuelva.
- **No escribe `audit_log` en los fallos.** `audit_log` lleva RLS por `business_id` y
  un fallo de login pre-tenant no sabe de que negocio viene: no hay GUC que poner, y
  poner el de un negocio adivinado seria inventar el dato que se quiere registrar. Los
  fallos van al log de aplicacion, que es donde se consulta mientras se depura. El
  `audit_log` entra en F1.6, donde el tenant ya esta resuelto.
- **No rota refresh tokens.** Emite el primero de la familia. Rotar, detectar reuso y
  revocar es F1.5/F1.6.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any, NoReturn

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AuthenticationError
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.rate_limit import enforce_rate_limit
from app.core.security import generate_token, get_sentinel_hash, hash_token, verify_password
from app.core.time import now
from app.models.enums import BusinessUserRole, MembershipStatus, PlatformRole
from app.modules.auth.models import RefreshToken
from app.modules.auth.scopes import (
    PlatformScope,
    Scope,
    as_strings,
    scopes_for_business_role,
    scopes_for_platform_role,
)
from app.modules.auth.tokens import (
    AccessToken,
    create_access_token,
    create_platform_access_token,
)
from app.modules.businesses.service import slugify

logger = get_logger(__name__)

#: El unico mensaje de fallo de credenciales. **Constante de modulo, no un literal
#: repetido**: la propiedad que se quiere es que las tres rutas -- email inexistente,
#: contrasena incorrecta, cuenta no activa -- produzcan el mismo objeto. Con un
#: literal en cada rama, el dia que alguien escribe "Cuenta desactivada" en una de
#: ellas, el test de uniformidad sigue pasando si compara solo el status.
#:
#: El texto no dice ni que falto ni cual fue el motivo. Un 401 que distingue
#: "usuario no encontrado" de "contrasena incorrecta" es un enumerador de cuentas
#:_regalo_, y uno que dice "Cuenta desactivada" ademas le confirma al atacante que la
#: victima existe, que es el primer paso de un ataque dirigido.
INVALID_CREDENTIALS = "Credenciales invalidas."

#: Ventana del rate limit de login, en segundos. Corresponde al "5/min" y al
#: "10/min" del §10.5.
LOGIN_WINDOW_SECONDS = 60

#: Etiquetas de `scope` para los dos cubos. Distintas para que un operador que
#: este mirando la tabla pueda decir cual de los dos limites se disparo, sin tener
#: que adivinar por el patron del `key`.
SCOPE_EMAIL = "auth:login:email"
SCOPE_IP = "auth:login:ip"

_BUSINESS_LOOKUP = "SELECT * FROM auth_business_user_for_login(:email)"
_PLATFORM_LOOKUP = "SELECT * FROM auth_platform_user_for_login(:email)"


@dataclass(frozen=True, slots=True)
class RefreshGrant:
    """El refresh token recien emitido, y la familia a la que pertenece.

    `token` en claro sale de aca **una sola vez** y no se persiste jamas: a la base va
    `token_hash`. Por eso el dataclass lo lleva y la fila no.

    `row` es la fila ya insertada, con `id` asignado. Va en el dataclass para que el
    llamador pueda encadenar `replaced_by_id` sin tener que volver a buscarla, que
    seria una segunda consulta por algo que la sesion ya tiene en la mano.
    """

    token: str
    family_id: uuid.UUID
    expires_at: dt.datetime
    row: RefreshToken


@dataclass(frozen=True, slots=True)
class RefreshedBusinessIdentity:
    """Identidad de negocio releida al rotar, ya validada.

    `business_id` sale de la fila de `business_users` que devuelve
    `auth_business_user_by_id`, nunca del token ni de un parametro del endpoint. Es
    lo mismo que exige el ADR-0010 para el login, y por el mismo motivo: que el
    `tid` del access token renewal no pueda elegirse.
    """

    user_id: uuid.UUID
    business_id: uuid.UUID
    role: BusinessUserRole
    scopes: frozenset[Scope]


@dataclass(frozen=True, slots=True)
class RefreshedPlatformIdentity:
    """Identidad de plataforma releida al rotar, ya validada."""

    user_id: uuid.UUID
    role: PlatformRole
    scopes: frozenset[PlatformScope]


@dataclass(frozen=True, slots=True)
class BusinessLogin:
    """Login de negocio exitoso.

    `business_id` es el `tid` del token y sale **exclusivamente** de la fila
    autenticada. No hay forma de que el que llama lo elija, y eso es exactamente lo
    que exige el ADR-0010: si el `tid` fuera un parametro del servicio, un endpoint
    con un bug de mass assignment seria un cruce de tenants.
    """

    user_id: uuid.UUID
    business_id: uuid.UUID
    role: BusinessUserRole
    scopes: frozenset[Scope]
    access_token: AccessToken
    refresh: RefreshGrant
    is_platform: bool = False


@dataclass(frozen=True, slots=True)
class PlatformCredentials:
    """Login de plataforma exitoso.

    Simetrico de `BusinessLogin` a proposito, y la simetria es lo que hace segura la
    rotacion. Los dos dataclasses llevan `access_token` y `refresh` para que ningun
    endpoint tenga que emitir tokens por su cuenta: si el login de plataforma
    tambien los emitiera en el router, tarde o temprano alguien volveria a armar el
    refresh ahi y con `user_id=` en vez de `platform_user_id=`, que es el error que
    hace que la rotacion de un operador busque una fila de `business_users`.

    La diferencia con `BusinessLogin` sigue siendo la que exige la §5.1: aca no hay
    `business_id` porque un operador de plataforma no pertenece a ningun negocio, y
    por eso su access token no lleva el claim `tid`. Que los scopes sean
    `PlatformScope` y nunca `Scope` es parte de lo mismo.
    """

    user_id: uuid.UUID
    role: PlatformRole
    scopes: frozenset[PlatformScope]
    access_token: AccessToken
    refresh: RefreshGrant
    is_platform: bool = True


def normalizar_email(email: str) -> str:
    """El email en la forma en la que se busca y en la que se cuenta.

    `strip()` + `casefold()`, y no solo `lower()`: `casefold` es la operacion de
    Unicode que corresponde a una comparacion sin distincion de caso, y sin ella
    `USER@x.com` y `user@x.com` caerian en cubos de rate limit distintos -- o sea
    10/min cada uno y 20/min en total para la misma cuenta.

    Que la base compare con `citext` y el rate limit con `casefold` no es una
    discrepancia: los dos tienen que llevar a *un* cubo, y la unica forma de
    garantizarlo sin depender de como PostgreSQL colaciona es normalizar aca.
    """
    return email.strip().casefold()


def login_rate_limit_keys(*, email: str, ip_address: str) -> tuple[str, str]:
    """Las claves de los dos cubos: `(email, ip)`, hasheadas.

    Nunca en claro, por el §5.7: esta tabla se purga con un job de retencion y nadie
    deberia poder leer de ella una lista de IPs de clientes ni de emails de usuarios.

    El prefijo de dominio va **antes** del hash y no despues. Hash-ear la IP y la
    concatenar con el prefijo daria la misma clave que hashear el prefijo, y dos
    entradas distintas terminarian en el mismo cubo: el rate limit de una superficie
    frenaria la otra.
    """
    return (
        hash_token(f"login:email:{normalizar_email(email)}"),
        hash_token(f"login:ip:{ip_address}"),
    )


def _referencia(email: str) -> str:
    """Como se nombra un email en un log, sin escribir el email.

    El log de autenticacion necesita correlacionar "este usuario vuelve a fallar" y a
    la vez no debe contener PII en un archivo que se rotaciona. Un prefijo del
    SHA-256 cumple las dos cosas: es correlatable y no se puede volver atras.
    """
    return hash_token(f"login:log:{normalizar_email(email)}")[:12]


async def _consultar(
    session: AsyncSession, statement: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Ejecuta una de las funciones de `0004` y trae todas las filas.

    Todas, y no `.one()`. `auth_business_user_for_login` es set-returning y devuelve
    una fila por membresia: la misma persona puede estar en dos negocios con el mismo
    email, porque la unicidad es `(business_id, email)`. `scalar_one()` ahi levanta
    `MultipleResultsFound` -- un 500 que ademas de romper el login es un oraculo, por
    que el mismo email con dos cuentas responde distinto que con una.
    """
    result = await session.execute(text(statement), params)
    return [dict(row) for row in result.mappings().all()]


async def _enforce_rate_limit(*, key: str, limit: int, scope: str) -> None:
    """Un cubo de `rate_limit_hit`, con la ventana de login.

    Delega en `core.rate_limit`, que es donde vive la primitiva y donde esta escrito
    **por que la transaccion es propia y no la del request**. Ese detalle no se
    repite aca a proposito: duplicado en dos lugares, el dia que se cambie uno queda
    el otro contando otra cosa y ninguno de los dos esta mal para sus ojos.

    `LOGIN_WINDOW_SECONDS` es lo unico especifico del login y por eso queda aqui; las
    otras superficies pasan su propia ventana.
    """
    await enforce_rate_limit(
        key=key,
        limit=limit,
        window_seconds=LOGIN_WINDOW_SECONDS,
        scope=scope,
        detail="Demasiados intentos. Reintentá en un rato.",
    )


async def _enforce_login_rate_limits(*, email: str, ip_address: str) -> None:
    """Los dos limites del §10.5 para `/auth/login`: 5/min por IP, 10/min por email.

    La IP va primero porque es el limite mas ajustado. Ninguno de los dos revela si
    el usuario existe -- por eso el detalle del 429 es generico y no dice cual se
    disparo mas veces: un atacante que distinguiera "tu email esta limitado" de "tu IP
    esta limitada" sabria que el email existe, y la respuesta a las dos es identica.

    **Siempre IP antes que email.** Cada cubo es una transaccion propia, asi que si
    dos peticiones tuvieran que tomar los dos cubos en orden contrario se trabarian
    en un interbloqueo esperandose la fila una de la otra. Con un orden fijo no hay
    forma de construirlo.
    """
    settings = get_settings()
    email_key, ip_key = login_rate_limit_keys(email=email, ip_address=ip_address)
    await _enforce_rate_limit(key=ip_key, limit=settings.rate_limit_login_per_ip, scope=SCOPE_IP)
    await _enforce_rate_limit(
        key=email_key,
        limit=settings.rate_limit_login_per_email,
        scope=SCOPE_EMAIL,
    )


def _verificar(password: str, candidatos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Verifica la contrasena contra todos los candidatos. Sin cortocircuito.

    Devuelve la lista de los que verificaron, que puede estar vacia.

    **Por que no se corta en el primero.** Con cortocircuito, un email que pertenece
    a dos negocios cuesta un Argon2 si la contrasena es correcta (corta en el
    primero) y dos si es incorrecta. Eso convierte el tiempo de respuesta en un
    verificador de contrasenas *dentro* del caso multi-negocio: el atacante que ya
    sabe que el email tiene dos membresias aprende cual de las dos contrasenas es,
    probando una sola. Verificando siempre todas, el costo es la cantidad de
    membresias del email, que es el mismo se acierte o no.

    **El canal residual que queda, y es real.** El tiempo revela *cuantas*
    membresias tiene un email. Para cero y para uno, el costo es identico (el
    centinela cubre el cero), asi que un email existente con una sola membresia es
    indistinguible de uno inexistente. A partir de dos, la duracion los delata. Es un
    canal angosto -- hace falta conocer el email y tener cuentas en varios negocios
    para notarlo -- y cerrar del todo exigiria o un `LIMIT` en la funcion de `0004` o
    una tabla de identidad global, ninguna de las cuales existe. Se deja documentado
    en vez de resuelto con un `sleep`, que solo moveria el problema.
    """
    if not candidatos:
        # Sin filas: se ejecuta Argon2 igual, contra el centinela. Esta es la linea
        # que hace que el login de un email inexistente no sea mas corto.
        get_sentinel_hash()
        verify_password(password, get_sentinel_hash())
        return []
    return [fila for fila in candidatos if verify_password(password, str(fila["password_hash"]))]


async def _desambiguar_por_slug(
    session: AsyncSession,
    verificados: list[dict[str, Any]],
    business_slug: str | None,
) -> dict[str, Any] | None:
    """Elige cual de las N membresias con el mismo email es la que se autentica.

    Devuelve la fila elegida, o `None` si el slug no resuelve a ninguna de las
    membresias ya verificadas --o si no vino slug.

    **Va despues de verificar la contrasena, y por eso no es un enumerador.** Para
    llegar aca el atacante ya tiene que haber probado la credencial correcta de las N
    cuentas; lo unico que queda por decidir es *cual* de los N negocios quiere, y eso
    lo decide el slug. Ademas, un slug que no corresponde y un slug que no existe
    devuelven los dos `None`, asi que tampoco distinguen "existe pero no es mio" de "no
    existe": la respuesta es el mismo 401 de siempre en los dos casos.

    El slug se canoniza con el mismo `slugify` que usa el alta, para que lo que el
    navegador pone en la barra de direcciones y lo que el backend tiene guardado
    comparen igual.

    **No se resuelve por `business_id`.** Aceptar un id de tenant desde el cliente
    seria exactamente el `business_id` que la firma de `authenticate_business_user`
    rechaza, y ademas dejaria al atacante recorrer tenants probando UUID.
    """
    if business_slug is None:
        return None

    slug = slugify(business_slug)
    if not slug:
        # Un slug que se canoniza a cadena vacia ("!!!") no puede ser el de un
        # negocio: `businesses.slug` no admite vacio. Se trata como "no vino", que es
        # el mismo 401 de siempre.
        return None

    business_id = await session.scalar(
        text("SELECT id FROM businesses WHERE slug = CAST(:slug AS citext)"),
        {"slug": slug},
    )
    if business_id is None:
        return None

    for fila in verificados:
        if fila["business_id"] == business_id:
            return fila
    return None


def _rechazar(email: str, motivo: str, **extra: Any) -> NoReturn:
    """Registra el motivo real en el log interno y levanta el 401 uniforme.

    El motivo va al log y no a la respuesta, y esa asimetria es deliberada: el log es
    donde se pregunta por que un usuario legitimo no entra, y la respuesta es donde no
    se le dice a un atacante por que la suya no entra. Un `reason` con enumeracion
    corta de valores solo se puede revisar mirando los logs.

    `email` nunca se escribe: va su hash truncado.
    """
    logger.warning(
        "login_rechazado",
        motivo=motivo,
        email_ref=_referencia(email),
        **extra,
    )
    raise AuthenticationError(INVALID_CREDENTIALS)


async def _emitir_refresh(
    session: AsyncSession,
    *,
    ip_address: str,
    user_agent: str | None,
    user_id: uuid.UUID | None = None,
    family_id: uuid.UUID | None = None,
    platform_user_id: uuid.UUID | None = None,
) -> RefreshGrant:
    """Emite un refresh token y lo escribe.

    `family_id=None` crea una familia nueva, que es el caso del login. Pasarlo
    continua la familia, que es el caso de la rotacion: la familia es el hilo que
    ata toda la cadena de rotaciones de una sesion.

    **Ni `user_id` ni `platform_user_id` son obligatorios, y los dos a la vez esta
    prohibido.** Son las dos superficies de identidad y la columna tiene un CHECK
    `exactly_one_principal`. Exigir `user_id` en la firma obligaba al login de
    plataforma --que por definicion no tiene-- a pasar `user_id=None` con un
    `# type: ignore`, y la mitad de las llamadas lo pasaban y la otra mitad no. El
    sintoma era `TypeError` al iniciar sesion como operador: **el login de
    plataforma estaba roto de punta a punta**, con un error de firma y no de
    negocio. La regla se valida aca y no por tipos para que un cuarto camino --una
    rotacion de un token con las dos columnas vacias-- falle con un mensaje que
    diga cual de las dos se rompio.

    El `flush` no es cosmetico. Sin el, la fila no tiene `id` --lo genera la base-- y
    la rotacion no podria setear `replaced_by_id` en el token que reemplaza, que es
    justamente el dato que despues permite detectar el reuso. Ademas el `flush`
    hace efectivo el indice unico de `token_hash`: dos rotaciones simultaneas del
    mismo token compiten por el y una gana, en vez de las dos devolver 200.

    El `token_hash` es lo unico que va a la base. Si alguien lee `refresh_tokens` no
    puede suplantar a nadie, y un incidente de lectura de la base no escala a un robo
    de sesiones.
    """
    if (user_id is None) == (platform_user_id is None):
        raise ValueError(
            "un refresh token es de exactamente una superficie: "
            f"user_id={user_id!r}, platform_user_id={platform_user_id!r}"
        )

    settings: Settings = get_settings()
    token = generate_token()
    familia = family_id if family_id is not None else uuid.uuid4()
    expires_at = now() + dt.timedelta(days=settings.refresh_token_expire_days)
    fila = RefreshToken(
        user_id=user_id,
        platform_user_id=platform_user_id,
        family_id=familia,
        token_hash=hash_token(token),
        expires_at=expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session.add(fila)
    await session.flush()
    return RefreshGrant(token=token, family_id=familia, expires_at=expires_at, row=fila)


async def authenticate_business_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    ip_address: str,
    user_agent: str | None = None,
    business_slug: str | None = None,
) -> BusinessLogin:
    """Autentica a un miembro de negocio. Levanta `AuthenticationError` si no entra.

    La firma es la garantia de aislamiento: recibe `email`, `password`, `ip_address` y
    `user_agent`, y **nada mas**. No hay un `business_id` que el que llama pueda pasar,
    ni un `tid`, ni un `role`. El tenant sale de la fila que el propio lookup
    devolvio, o sea del registro que la contrasena acabo de autenticar. Un endpoint
    con un bug de mass assignment no puede cruzar tenants con esta firma, porque no
    tiene de donde sacar un tenant ajeno.

    `business_slug` es la unica excepcion, y no por que abra la puerta: elige entre
    filas que la contrasena **ya** autentico. No agrega un tenant que antes no se
    pudiera alcanzar; solo desempata cuando el mismo email es miembro de varios
    negocios, algo que sin slug es un 401 por `membresia_ambigua`.

    `session` debe ser una sesion **sin** contexto de tenant: todavia no hay tenant,
    que es justo lo que se esta resolviendo. El GUC se pone despues, en el router,
    con `tenant_session(business_id)` sobre el `business_id` que sale de aca.
    """
    await _enforce_login_rate_limits(email=email, ip_address=ip_address)

    candidatos = await _consultar(session, _BUSINESS_LOOKUP, {"email": normalizar_email(email)})
    verificados = _verificar(password, candidatos)

    if not verificados:
        # Tres causas distintas -- no existia, la contrasena no es, o todas las
        # cuentas estan bloqueadas -- y una sola respuesta. El log las separa.
        _rechazar(
            email,
            "sin_coincidencia",
            candidatos=len(candidatos),
        )

    if len(verificados) > 1:
        elegido = await _desambiguar_por_slug(session, verificados, business_slug)

        if elegido is None:
            # Politica acordada: la ambiguedad es un fallo, no una eleccion, salvo que
            # el cliente diga cual de los N es. Se evalua **despues** de verificar la
            # contrasena, asi que no es enumeracion: para llegar aca hay que haber
            # probado la credencial correcta.
            #
            # Enterar a "la primera" seria entrar a un tenant arbitrario, y el dano es
            # exactamente el que el ADR-0010 quiere cerrar. Un slug equivocado y uno
            # inexistente se registran con motivos distintos y responden igual, porque
            # la respuesta unica es justamente lo que los hace indistinguibles.
            _rechazar(
                email,
                "slug_no_corresponde" if business_slug else "membresia_ambigua",
                membresias=len(verificados),
                negocio=str(verificados[0]["business_id"]),
            )

        verificados = [elegido]
    # Con una sola membresia el slug se ignora a proposito. Aceptarlo seria validar un
    # dato que no puede cambiar el resultado, y exigir que coincida daria un 401 a
    # quien tiene las credenciales correctas solo por escribir mal la URL.

    fila = verificados[0]
    estado = MembershipStatus(str(fila["status"]))

    # El estado se mira despues de la contrasena, nunca antes: un `invited` que
    # respondiera sin verificar seria mas rapido que uno con contrasena incorrecta, y
    # esa diferencia ya es un oraculo sin necesidad de ningun otro.
    if estado is not MembershipStatus.ACTIVE:
        _rechazar(email, "estado_no_activo", estado=str(estado))

    user_id = fila["id"]
    business_id = fila["business_id"]
    if not isinstance(user_id, uuid.UUID) or not isinstance(business_id, uuid.UUID):
        # La funcion declara `uuid`; si volviera otra cosa, el `str()` que haria
        # falta para el token seria un valor de otra persona.
        _rechazar(email, "fila_malformada")

    rol = BusinessUserRole(str(fila["role"]))
    scopes = scopes_for_business_role(rol)

    access = create_access_token(
        user_id=user_id,
        business_id=business_id,
        role=str(rol),
        scopes=as_strings(scopes),
    )
    refresh = await _emitir_refresh(
        session, user_id=user_id, ip_address=ip_address, user_agent=user_agent
    )

    logger.info("login_ok", email_ref=_referencia(email), rol=str(rol), is_platform=False)
    return BusinessLogin(
        user_id=user_id,
        business_id=business_id,
        role=rol,
        scopes=scopes,
        access_token=access,
        refresh=refresh,
    )


async def authenticate_platform_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    ip_address: str,
    user_agent: str | None = None,
) -> PlatformCredentials:
    """Autentica a un operador de plataforma. Levanta `AuthenticationError` si no entra.

    Superficie separada a proposito, y no un `is_platform` en la misma funcion: con
    una sola, un `type` equivocado en el codigo buscaria en la tabla equivocada y el
    error seria "no existe" en vez de "estas buscando en el sitio equivocado", que es
    la clase de bug que en un login no se distingue de un ataque.

    Emite el access token aca y no en el router, por la misma razon que
    `authenticate_business_user` lo hace: el endpoint no tiene por que saber que
    existen dos tipos de titular, y si lo supiera, la proxima emision manual la
    escribiria en el lugar equivocado.
    """
    await _enforce_login_rate_limits(email=email, ip_address=ip_address)

    candidatos = await _consultar(session, _PLATFORM_LOOKUP, {"email": normalizar_email(email)})
    verificados = _verificar(password, candidatos)

    if not verificados:
        _rechazar(email, "sin_coincidencia", plataforma=True, candidatos=len(candidatos))
    if len(verificados) > 1:
        # No deberia pasar: `platform_users.email` es unico. Si aparece, es una
        # inconsistencia de datos, y con el indice unico no puede ser una carrera.
        _rechazar(email, "membresia_ambigua", plataforma=True)

    fila = verificados[0]
    if not bool(fila["is_active"]):
        _rechazar(email, "estado_no_activo", plataforma=True)

    user_id = fila["id"]
    if not isinstance(user_id, uuid.UUID):
        _rechazar(email, "fila_malformada", plataforma=True)

    rol = PlatformRole(str(fila["role"]))
    scopes = scopes_for_platform_role(rol)

    access = create_platform_access_token(
        user_id=user_id,
        role=rol.value,
        scopes=as_strings(scopes),
    )
    # `platform_user_id` y no `user_id`: el CHECK `exactly_one_principal` exige
    # exactamente uno, asi que emitir con `user_id` no lo viola -- pasa. Lo que rompe
    # es la rotacion, que decide que superficie es mirando cual de los dos esta
    # lleno, y con `user_id` lleno resolveria `auth_business_user_by_id` con el id de
    # un operador de plataforma. No existiria fila, y el primer refresh de un
    # operador fallaria con 401 siempre.
    refresh = await _emitir_refresh(
        session,
        platform_user_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    logger.info("login_ok", email_ref=_referencia(email), rol=str(rol), is_platform=True)
    return PlatformCredentials(
        user_id=user_id,
        role=rol,
        scopes=scopes,
        access_token=access,
        refresh=refresh,
    )


#: Motivo de auditoria cuando un refresh token ya rotado reaparece.
REFRESH_REUSE = "refresh_reuse"

_BUSINESS_BY_ID = "SELECT * FROM auth_business_user_by_id(:id)"
_PLATFORM_BY_ID = "SELECT * FROM auth_platform_user_by_id(:id)"


async def _revocar_familia(session: AsyncSession, family_id: uuid.UUID) -> int:
    """Revoca todos los tokens vivos de una familia, en su propia transaccion.

    Devuelve cuantos revocó.

    **Por qué commitea adentro y no confia en la del request.** Esta escritura tiene
    que sobrevivir al fallo de la peticion que la dispara, y la peticion va a fallar:
    en la deteccion de reuso se llama y despues se levanta `AuthenticationError`, y
    `session_scope` -- que hace `rollback` ante cualquier excepcion -- deshacia
    todo. El resultado era que la deteccion de reuso **no revocaba nada**: el
    atacante se llevaba un 401, la victima seguia con su sesion viva, y la proxima
    renovacion del atacante volvia a ser aceptada. El unico sintoma era que el token
    del atacante tambien fallaba, que es indistinguible de un problema de red.

    O sea: la proteccion existed, se ejecutaba, y no dejaba rastro. Un control de
    seguridad que se deshace solo es peor que uno que no esta, porque el que lee el
    codigo y el que lee los logs concluyen que el riesgo esta cubierto.

    Por eso el `commit` es explicito aqui. El `rollback` que despues hace
    `session_scope` ya no tiene nada que deshacer y sale como no-op, y el `with` del
    llamador sigue cerrando la sesion normalmente.

    La idea del ADR-0010 es que cuando un token ya rotado vuelve a aparecer no se
    puede saber cual de los dos que lo presentan es el atacante y cual es la victima
    -- el token es publico para quien se lo copio--, asi que la unica salida que no
    deja sesion abierta es cortar las dos.
    """
    resultado = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now())
    )
    await session.commit()
    return int(getattr(resultado, "rowcount", 0) or 0)


@dataclass(frozen=True, slots=True)
class RefreshRotation:
    """El resultado completo de una rotacion.

    `grant` va adentro y no se devuelve aparte para que no haya forma de que el
    llamador **no** lo use. El token en claro sale una sola vez, acá: si el router
    emitiera su propio token ademas del que ya genero `_emitir_refresh` para encadenar
    `replaced_by_id`, quedarian dos filas y la cookie llevaria una que no es la que
    quedo encadenada -- el proximo refresh seria reuso y mataria la familia.
    """

    identity: RefreshedBusinessIdentity | RefreshedPlatformIdentity
    grant: RefreshGrant


async def rotate_refresh_token(
    session: AsyncSession,
    *,
    presented_token: str,
    ip_address: str,
    user_agent: str | None,
) -> RefreshRotation:
    """Rota un refresh token. Levanta `AuthenticationError` si no corresponde.

    El token presentado se busca **por hash**: el plano no se persiste y por lo tanto
    no hay nada que comparar contra el, solo su SHA-256.

    **La revocacion del token viejo es condicional y eso es lo que hace que la
    deteccion de reuso sea correcta bajo concurrencia.** Es un
    `UPDATE ... WHERE id = :id AND revoked_at IS NULL`: si dos peticiones llegan con
    el mismo token --un doble clic, o el atacante y la victima al mismo tiempo--,
    PostgreSQL serializa los dos UPDATE sobre la misma fila y **solo uno** ve
    `rowcount == 1`. El otro ve 0, y para el segundo significa exactamente lo que
    significa en el caso no concurrente: ese token ya se uso, hay reuso, y se
    revoca la familia.

    Sin el `AND revoked_at IS NULL` el paso seria un `SELECT` seguido de un `UPDATE`,
    y las dos peticiones verian `revoked_at IS NULL` antes de que ninguna escriba.
    Las dos devolverian 200 con dos access tokens validos derivados del mismo
    refresh, que es precisamente lo que la rotacion existe para impedir.

    El orden es: revocar primero, recien despues resolver la identidad y emitir. Si
    la identidad no se puede validar --miembro desactivado, operador dado de baja--,
    el token viejo ya quedo revocado igual, que es lo correcto: el intento fue
    invalido de todos modos y no debe dejar una sesion viva.
    """
    token_hash = hash_token(presented_token)

    fila = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()

    if fila is None:
        raise AuthenticationError("Refresh token invalido.")

    if fila.expires_at <= now():
        raise AuthenticationError("Refresh token expirado.")

    # Revocacion condicional: el que gane el rowcount es el unico legitimo.
    revocacion = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.id == fila.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now(), last_used_at=now())
    )

    if getattr(revocacion, "rowcount", 0) == 0:
        # El token ya estaba revocado cuando llegamos: alguien lo uso antes.
        affected = await _revocar_familia(session, fila.family_id)
        logger.warning(
            "refresh_reuso_detectado",
            family_ref=str(fila.family_id)[:8],
            tokens_revocados=affected,
            ip_address=ip_address,
        )
        raise AuthenticationError(
            "Refresh token invalido. Todas las sesiones de esta familia fueron "
            "cerradas por seguridad; volve a iniciar sesion."
        )

    grant = await _emitir_refresh(
        session,
        user_id=fila.user_id,
        platform_user_id=fila.platform_user_id,
        family_id=fila.family_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    fila.replaced_by_id = grant.row.id

    # Releer la identidad **siempre**, nunca confiar en la del login.
    if fila.platform_user_id is not None:
        identity: (
            RefreshedBusinessIdentity | RefreshedPlatformIdentity
        ) = await _identidad_plataforma(session, fila.platform_user_id)
    else:
        identity = await _identidad_negocio(session, fila.user_id)

    return RefreshRotation(identity=identity, grant=grant)


async def revoke_session(session: AsyncSession, presented_token: str) -> int:
    """Cierra la sesion del token presentado. Devuelve cuantos tokens revocados.

    **Revoca la familia entera, no solo el token.** Un logout que revocara un unico
    token dejaria vivos los sucesores de esa familia, y como cada refresh genera el
    siguiente, el usuario que hizo logout tendria un token valido en el navegador
    (el que acababa de rotar) y podria renovar otra vez indefinidamente: logout no
    cortaria nada. Revocar la familia es lo que hace que el corte sea real.

    No levanta `AuthenticationError` si el token no existe o ya esta revocado, y a
    proposito: el logout tiene que ser idempotente y no puede filtrar informacion
    sobre que tokens existen. Que la UI borre la cookie y listo; que el token haya
    sido valido o no es indistinto desde afuera.

    El `0` que devuelve el caso normal (cookie expirada del navegador) no es un error.
    """
    fila = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(presented_token))
        )
    ).scalar_one_or_none()

    if fila is None:
        return 0
    return await _revocar_familia(session, fila.family_id)


async def _identidad_negocio(
    session: AsyncSession, user_id: uuid.UUID | None
) -> RefreshedBusinessIdentity:
    """Relee y valida un miembro de negocio. `AuthenticationError` si ya no sirve.

    El `status` se comprueba **aca y no en el login** a proposito. Una sesion abierta
    sobrevive a que alguien suspenda al miembro, y si la rotacion no volviera a
    mirar la fila, ese access renewed cada 15 minutos para siempre. Releer el status
    en cada renovacion hace que desactivar a alguien tenga efecto en la proxima, sin
    tener que buscar y revocar sesiones una por una.
    """
    if user_id is None:
        # `exactly_one_principal` lo hace imposible; si se llegara, la fila esta
        # corrupta y no hay titular al que devolverle nada.
        raise AuthenticationError("Refresh token invalido.")

    filas = await _consultar(session, _BUSINESS_BY_ID, {"id": user_id})
    if len(filas) != 1:
        raise AuthenticationError("Refresh token invalido.")

    fila = filas[0]
    if str(fila["status"]) != MembershipStatus.ACTIVE:
        logger.warning(
            "refresh_rechazado_estado",
            user_ref=str(user_id)[:8],
            estado=str(fila["status"]),
        )
        raise AuthenticationError("Refresh token invalido.")

    rol = BusinessUserRole(str(fila["role"]))
    return RefreshedBusinessIdentity(
        user_id=user_id,
        business_id=fila["business_id"],
        role=rol,
        scopes=scopes_for_business_role(rol),
    )


async def _identidad_plataforma(
    session: AsyncSession, user_id: uuid.UUID
) -> RefreshedPlatformIdentity:
    """Relee y valida un operador de plataforma."""
    filas = await _consultar(session, _PLATFORM_BY_ID, {"id": user_id})
    if len(filas) != 1 or not bool(filas[0]["is_active"]):
        raise AuthenticationError("Refresh token invalido.")

    rol = PlatformRole(str(filas[0]["role"]))
    return RefreshedPlatformIdentity(
        user_id=user_id, role=rol, scopes=scopes_for_platform_role(rol)
    )


__all__ = [
    "INVALID_CREDENTIALS",
    "LOGIN_WINDOW_SECONDS",
    "REFRESH_REUSE",
    "SCOPE_EMAIL",
    "SCOPE_IP",
    "BusinessLogin",
    "PlatformCredentials",
    "RefreshGrant",
    "RefreshedBusinessIdentity",
    "RefreshedPlatformIdentity",
    "authenticate_business_user",
    "authenticate_platform_user",
    "login_rate_limit_keys",
    "normalizar_email",
    "revoke_session",
    "rotate_refresh_token",
]
