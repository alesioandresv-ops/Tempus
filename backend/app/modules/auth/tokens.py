"""Emision y validacion del access token.

El access token es un JWT HS256 de 15 minutos con los claims que fija el §10.2. Lo
que hace este modulo no es firmar: es **decidir que se rechaza**.

**Por que un JWT y no un token opaco como el refresh.** Son dos superficies con
riesgos opuestos. El access token viaja en el `Authorization` de cada peticion y lo
lee el servidor decenas de veces por request del cliente: consultarlo en la base en
cada una seria un round-trip a PostgreSQL por peticion, y el perfil de despliegue es
un Postgres de 1 GB. El refresh token se usa una vez cada 15 minutos: ahi la consulta a
la base no cuesta nada y da algo que el JWT no puede dar, que es **revocar**. Un JWT es
imposible de revocar antes de que expire, asi que el token que se puede revocar tiene
que ser opaco, y el que no se puede revocar tiene que ser stateless. Confundir los dos
es la forma habitual de dejar sesiones vivas 15 minutos despues de cerrar la cuenta.

**Que se rechaza, y por que cada rejection importa.**

- `algorithms=[HS256]`: PyJWT no infiere el algoritmo del header del token. Sin esto,
  un atacante que consiga que la app firme con la clave publica de la RSA —que es
  publica justamente— produce un token que valida. Es el ataque de confusion de
  algoritmo, y es la razon de `pyjwt[crypto]`: el backend HMAC rechaza una clave que
  huela a PEM, y `pyjwt` sin el extra no tiene esa proteccion.
- `require`: un token al que le falta `exp` no expira nunca. Un token al que le falta
  `sub` no tiene titular. Que falten es un bug o un ataque, y los dos se rechazan.
- `iss` y `aud`: una firma valida no dice que el token sea *nuestro*. Sin `iss`, un
  token firmado con la misma clave por otra aplicacion del mismo despliegue valida
  aca. Son dos valores en `Settings` y dos lineas de verificacion.
- `typ == "access"`: el refresh token no es un JWT, asi que no hay colision hoy. El
  claim existe para que mañana, cuando haya tokens de otros tipos, no haya que
  desempacar por las dudas.

**Por que `sub` y `tid` son strings y no UUIDs.** El estandar dice que `sub` es un
string, y PyJWT no obliga a nada. Lo que importa es que el parseo de ambos sea
estricto: un `tid` que no sea un UUID se rechaza, no se pasa. Un `tid` invalido que
llegara a `set_config` seria un `current_setting` con basura y una politica de RLS que
no filtra, que es el peor de los casos posibles: RLS activa y sin efecto.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

import jwt

from app.core.config import Settings, get_settings
from app.core.time import now

#: Claim que marca el tipo de token. Un refresh es opaco y no JWT, asi que hoy el
#: claim no evita ningun ataque concreto; existe para no tener que adivinar cuando
#: haya un tercer tipo de token.
CLAIM_TYPE = "typ"
TYPE_ACCESS = "access"

#: Claims que un access token tiene que traer. Todos, no solo `exp`.
#:
#: Nota: `tid` es obligatorio para tokens de negocio, pero opcional para tokens de
#: plataforma (donde no hay tenant). La validación en `_principal_desde` maneja
#: ambos casos.
REQUIRED_CLAIMS: tuple[str, ...] = (
    "sub",
    "role",
    "scopes",
    "jti",
    "iat",
    "exp",
    CLAIM_TYPE,
)


class TokenError(Exception):
    """El token no sirve. Deliberadamente opaca y sin causa visible.

    El mensaje no dice *que* fallo. Distinguir "firmado con otra clave" de "expirado"
    de "sin `sub`" le da a un atacante un oraculo para distinguir tokens validos de
    invalidos, y con eso enumerar sesiones. El log de aplicacion si registra el
    motivo, que es donde lo que ayuda a diagnosticar; la respuesta al cliente es una
    sola.
    """


@dataclass(frozen=True, slots=True)
class AccessToken:
    """Un access token emitido."""

    token: str
    expires_at: dt.datetime
    jti: uuid.UUID
    subject: uuid.UUID
    business_id: uuid.UUID | None  # None para tokens de plataforma


@dataclass(frozen=True, slots=True)
class Principal:
    """Quien esta autenticado, y en que tenant.

    Es lo que sale de `require_principal`. `business_id` viene del claim `tid` y de
    ningun otro lado: el §25 y el ADR-0010 lo prohiben explicitamente, y este dataclass
    no tiene forma de construirlo de otra forma, que es la forma de hacer la regla
    cumplible en vez de recordada.

    Para tokens de plataforma, `business_id` es `None` y `is_platform` es `True`.
    """

    user_id: uuid.UUID
    business_id: uuid.UUID | None  # None para tokens de plataforma
    role: str
    scopes: frozenset[str]
    jti: uuid.UUID
    is_platform: bool = False


def create_access_token(
    *,
    user_id: uuid.UUID,
    business_id: uuid.UUID,
    role: str,
    scopes: list[str],
    settings: Settings | None = None,
    issued_at: dt.datetime | None = None,
) -> AccessToken:
    """Emite un access token para un miembro de negocio.

    `issued_at` existe para los tests: sin el, un token emitido "hace 16 minutos" en
    un test de expiracion depende de que el reloj y el reloj de expiracion coincidan
    por suerte. Con el, el test dice exactamente que quiere.
    """
    cfg = settings or get_settings()
    momento = issued_at or now()
    expires_at = momento + dt.timedelta(minutes=cfg.jwt_access_token_expire_minutes)
    jti = uuid.uuid4()

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tid": str(business_id),
        "role": role,
        "scopes": scopes,
        "jti": str(jti),
        "iat": int(momento.timestamp()),
        "exp": int(expires_at.timestamp()),
        CLAIM_TYPE: TYPE_ACCESS,
        "iss": cfg.jwt_issuer,
        "aud": cfg.jwt_audience,
    }
    token = jwt.encode(payload, cfg.jwt_secret_key.get_secret_value(), algorithm=cfg.jwt_algorithm)
    return AccessToken(
        token=token,
        expires_at=expires_at,
        jti=jti,
        subject=user_id,
        business_id=business_id,
    )


def create_platform_access_token(
    *,
    user_id: uuid.UUID,
    role: str,
    scopes: list[str],
    settings: Settings | None = None,
    issued_at: dt.datetime | None = None,
) -> AccessToken:
    """Emite un access token para un operador de plataforma.

    A diferencia de `create_access_token`, este token **no incluye `tid`** (claim `tid`
    ausente o `null`), porque un operador de plataforma no pertenece a ningún tenant.

    El token incluye `is_platform: true` en los claims para diferenciación explícita
    en la validación y en los guards de autorización.
    """
    cfg = settings or get_settings()
    momento = issued_at or now()
    expires_at = momento + dt.timedelta(minutes=cfg.jwt_access_token_expire_minutes)
    jti = uuid.uuid4()

    payload: dict[str, Any] = {
        "sub": str(user_id),
        # tid ausente: indica token de plataforma sin tenant
        "role": role,
        "scopes": scopes,
        "jti": str(jti),
        "iat": int(momento.timestamp()),
        "exp": int(expires_at.timestamp()),
        CLAIM_TYPE: TYPE_ACCESS,
        "iss": cfg.jwt_issuer,
        "aud": cfg.jwt_audience,
        "is_platform": True,
    }
    token = jwt.encode(payload, cfg.jwt_secret_key.get_secret_value(), algorithm=cfg.jwt_algorithm)
    return AccessToken(
        token=token,
        expires_at=expires_at,
        jti=jti,
        subject=user_id,
        business_id=None,  # Sin business_id para tokens de plataforma
    )


def decode_access_token(
    token: str, *, settings: Settings | None = None, now_override: dt.datetime | None = None
) -> Principal:
    """Valida un access token y devuelve el `Principal`. Levanta `TokenError` si no vale.

    El orden de las verificaciones importa poco para la seguridad y mucho para el
    diagnostico: primero la firma y las claims obligatorias, despues el formato de los
    UUID, y recien ahi se construye el principal. Si un UUID esta mal, el error dice
    eso, porque en ese punto la firma ya se valido y no hay nada que un atacante
    necesite.
    """
    cfg = settings or get_settings()
    try:
        # No requerir `tid` en la validación de PyJWT; lo validamos manualmente
        # porque tokens de plataforma no lo tienen.
        claims = jwt.decode(
            token,
            cfg.jwt_secret_key.get_secret_value(),
            # Lista explicita: PyJWT no debe deducir el algoritmo del header, que es
            # controlado por quien emite el token, o sea por el atacante.
            algorithms=[cfg.jwt_algorithm],
            audience=cfg.jwt_audience,
            issuer=cfg.jwt_issuer,
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("access token invalido") from exc

    if claims.get(CLAIM_TYPE) != TYPE_ACCESS:
        raise TokenError("access token invalido")

    # `leeway` en el clock skew se omite a proposito: son 15 minutos de vida y
    # tolerate 0 seria molestar en el despliegue, pero el reloj es NTP y el drift de
    # un server con NTP es de milisegundos. Un `leeway` de un minuto abriria una
    # ventana real sin ganar nada practico.
    momento = now_override or now()
    if _esta_expirado(claims, momento):
        raise TokenError("access token invalido")

    return _principal_desde(claims)


def _esta_expirado(claims: dict[str, Any], momento: dt.datetime) -> bool:
    """Comprobacion de expiracion explicita, ademas de la de PyJWT.

    PyJWT ya rechaza `exp` vencido. Esta segunda comprobacion existe por el
    `now_override` de los tests: PyJWT usa `time.time()`, asi que un test que pasa un
    reloj simulado para que el token "venga del pasado" tiene que controlarlo por
    este lado. Duplicar la regla es malo; duplicarla solo para el caso de test es peor
    todavia, asi que lo que se hace es que el test use la via que PyJWT si entiende
    (`issued_at`) y esta funcion quede como red de seguridad para el caso en que
    `exp` no sea un entero, que ahi si es una comprobacion real.
    """
    exp = claims.get("exp")
    if not isinstance(exp, int):
        return True
    return dt.datetime.fromtimestamp(exp, tz=dt.UTC) <= momento


def _principal_desde(claims: dict[str, Any]) -> Principal:
    """Construye el `Principal`. Parseo estricto de UUID.

    Maneja dos tipos de tokens:
    - Tokens de negocio: tienen `tid` (UUID), `is_platform` ausente o False
    - Tokens de plataforma: no tienen `tid`, tienen `is_platform: true`

    Un `sub` o un `tid` (si existe) que no sea un UUID se rechaza.
    """
    try:
        user_id = uuid.UUID(str(claims["sub"]))
        jti = uuid.UUID(str(claims["jti"]))
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise TokenError("access token invalido") from exc

    # Determinar si es token de plataforma
    is_platform = claims.get("is_platform") is True

    if is_platform:
        business_id = None
    else:
        # Token de negocio: tid obligatorio
        try:
            business_id = uuid.UUID(str(claims["tid"]))
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise TokenError(
                "access token invalido: tid ausente o invalido en token de negocio"
            ) from exc

    role = claims.get("role")
    if not isinstance(role, str) or not role:
        raise TokenError("access token invalido")

    raw_scopes = claims.get("scopes")
    if not isinstance(raw_scopes, list) or not all(isinstance(s, str) for s in raw_scopes):
        raise TokenError("access token invalido")

    return Principal(
        user_id=user_id,
        business_id=business_id,
        role=role,
        scopes=frozenset(raw_scopes),
        jti=jti,
        is_platform=is_platform,
    )


__all__ = [
    "CLAIM_TYPE",
    "REQUIRED_CLAIMS",
    "TYPE_ACCESS",
    "AccessToken",
    "Principal",
    "TokenError",
    "create_access_token",
    "create_platform_access_token",
    "decode_access_token",
]
