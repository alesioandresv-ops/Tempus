"""Primitivas de criptografia de la aplicacion.

Tres cosas distintas viven aca, y conviene no mezclarlas:

1. **Passwords.** Argon2id con parametros que salen de `Settings`. Es la unica
   operacion cara y la unica cuyo hash se guarda con sal implicita.
2. **Tokens opacos.** 256 bits del CSPRNG del sistema, como dictamina ADR-0009.
3. **Hashes de token.** SHA-256 en hexadecimal, que es lo unico que se persiste.

La distincion entre 2 y 3 es la que sostiene la decision del ADR: en la base hay un
hash, no un token. Quien lea `refresh_tokens` o `bookings.secure_token_hash` no
suplanta a nadie, y un incidente de lectura de la base no escala a un robo de
sesiones.

**Por que SHA-256 para los tokens y Argon2 para las passwords.** Argon2 esta disenado
para ser lento y caro a proposito, que es lo que sirve contra un atacante que prueba
muchas contrasenas. Un token de 256 bits no se puede atacar por fuerza bruta: el
espacio de busqueda es de 2^256, asi que un atacante que tenga el hash no tiene una
peor alternativa que adivinar el token. Aplicarle Argon2 ahi solo haria que cada
refresh de la app tarde 100 ms por lo mismo, y el ataque que Argon2 previene no es el
que existe contra un token aleatorio. SHA-256 es lo que corresponde a una entrada de
alta entropia, y la busqueda es una consulta exacta sobre un indice unico, asi que no
hay nada que comparar en tiempo constante.
"""

from __future__ import annotations

import hashlib
import secrets

from argon2.low_level import Type
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import Settings, get_settings

# 32 bytes = 256 bits. Es el numero que fija ADR-0009, y el que hace que un token
# robado no sea atacable por fuerza bruta: el costo de probarlo es el mismo que el de
# cualquier otro.
TOKEN_BYTES = 32

#: Pre-imagen del hash centinela.
#:
#: No es un secreto y no tiene que serlo. El hash centinela existe para **gastar el
#: mismo tiempo de CPU** que un hash real, asi que lo unico que importa es que nadie
#: pueda evitar pasar por Argon2. Que la pre-imagen sea una constante conocida en el
#: codigo no la vuelve adivinable en el sentido que importaria: no hay nada que
#: adivinar. El atacante tiene que ejecutar la operacion cara igual.
#:
#: Eligiendo un string con el nombre del dominio, un grep por `hash_password` en el
#: codigo no lo confunde con una credencial real ni con un valor de `.env`.
SENTINEL_PREIMAGE = "tempus:login:usuario-inexistente"

# El hasher se construye una vez y se reusa. `Argon2Hasher` es stateless, asi que
# compartirlo es seguro y evita reconstruir la configuracion en cada login.
_password_hash: PasswordHash | None = None
_password_hash_params: tuple[int, int, int] | None = None

# El centinela se cachea aparte del hasher y con la misma invalidacion por
# parametros. El motivo no es performance: es una **invariante**. Argon2 lleva sus
# parametros dentro del hash, asi que un centinela generado con el coste viejo
# seguiria verificando bien contra un hasher nuevo -- pero `verify_and_update` si
# devolveria un hash nuevo para el centinela, y el codigo de login tendria que
# empezar a distinguir "hay que rehashear" de "esto es un centinela". Regenerandolo
# junto con el hasher, la pre-imagen siempre coincide con los parametros vigentes y
# `verify_and_update` nunca tiene nada que actualizar.
_sentinel_hash: str | None = None
_sentinel_params: tuple[int, int, int] | None = None


def _argon2_params() -> tuple[int, int, int]:
    settings = get_settings()
    return (
        settings.argon2_time_cost,
        settings.argon2_memory_cost,
        settings.argon2_parallelism,
    )


def get_password_hash() -> PasswordHash:
    """El `PasswordHash` de Argon2id, con los parametros de `Settings`.

    Se memoriza por parametros, no solo por instancia: si un test baja el coste de
    Argon2 para no esperar 100 ms por password, y no lo restaura, el hasher caro se
    queda cacheado y el test siguiente mide el rendimiento real sin quererlo. Guardar
    la tupla de parametros hace que un cambio de configuracion invalide el cache solo.
    """
    global _password_hash, _password_hash_params

    settings = get_settings()
    params = _argon2_params()
    if _password_hash is None or _password_hash_params != params:
        # Una **secuencia** de hashers, no uno solo: el primero es el que se usa para
        # hashear y el resto se aceptan al verificar, que es como se migra de un
        # algoritmo a otro sin invalidar las passwords existentes.
        #
        # El §10.1 dice `PasswordHash.recommended()`, que es exactamente esta
        # configuracion con los defaults de la libreria. Se construye explicito
        # porque `Settings` expone `argon2_*` y unas config que no llegan a ningun
        # sitio son una promesa vacia. Los valores por defecto coinciden con los de
        # `recommended()`, asi que el comportamiento es el mismo salvo que se toquen.
        #
        # `type=Type.ID` tambien va explicito aunque coincida con el default. Argon2
        # tiene tres variantes con permisos de memoria y de tiempo distintos, y la
        # eleccion entre `i`, `d` e `id` es una decision de seguridad, no un detalle
        # de la libreria. Dejarla implicita seria confiar en que un default de
        # terceros no cambia y en que nadie lo lee como una decision tomada.
        _password_hash = PasswordHash(
            (
                Argon2Hasher(
                    time_cost=settings.argon2_time_cost,
                    memory_cost=settings.argon2_memory_cost,
                    parallelism=settings.argon2_parallelism,
                    type=Type.ID,
                ),
            )
        )
        _password_hash_params = params
    return _password_hash


def reset_password_hash_cache() -> None:
    """Limpia el cache del hasher y el del centinela. Para tests.

    Los dos se limpian juntos a proposito: dejarlos desincronizados abriria la
    ventana en la que el centinela se genero con parametros que el hasher ya no
    tiene.
    """
    global _password_hash, _password_hash_params, _sentinel_hash, _sentinel_params
    _password_hash = None
    _password_hash_params = None
    _sentinel_hash = None
    _sentinel_params = None


def get_sentinel_hash() -> str:
    """Un hash Argon2id valido, constante, para cuando el usuario no existe.

    Es lo que hace que "no existe" y "existe pero la contrasena esta mal" cuesten lo
    mismo. La forma canonica de esa propiedad no es un `if user is None: return`: es
    que **no haya rama rapida**. Con el `if`, el login de un email inexistente evita
    por completo la unica operacion cara del sistema, y esa diferencia -- 100 ms
    contra 1 ms, por request, medible desde el otro lado sin ningun secreto -- es un
    enumerador de cuentas gratuito.

    Por que se **genera** en vez de hardcodear el string. Un hash pegado en el
    codigo tiene parametros de Argon2 congelados: si el dia de mañana se suben
    `time_cost` o `memory_cost`, el centinela sigue costingando con los valores
    viejos mientras los hashes reales usan los nuevos, y el tiempo de un login
    inexistente empieza a no parecerse al de uno real justo cuando se lo sube a
    proposito. Generandolo con el `PasswordHash` vigente, el coste del camino
    inexistente sube con el resto.

    Y por que se cachea: `hash()` aplica una sal aleatoria, asi que generarlo por
    request seria una operacion Argon2 **mas** por login, y el login inexistente --
    el que mas nos importa medir -- seria el mas lento de todos. Se genera una vez
    por configuracion de parametros y se reutiliza.
    """
    global _sentinel_hash, _sentinel_params

    params = _argon2_params()
    if _sentinel_hash is None or _sentinel_params != params:
        _sentinel_hash = get_password_hash().hash(SENTINEL_PREIMAGE)
        _sentinel_params = params
    return _sentinel_hash


def hash_password(password: str) -> str:
    """Hashea una password con Argon2id. El resultado es lo que va a la base.

    La password en claro no se guarda, no se loguea y no sale de este scope.
    """
    return get_password_hash().hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Comprueba una password contra su hash.

    `False` si no coincide, y tambien si el hash almacenado esta corrupto: un valor
    roto en la base no debe convertirse en un 500 que le confirma al atacante que toco
    algo interesante.
    """
    if not stored_hash:
        return False
    try:
        return get_password_hash().verify(password, stored_hash)
    except Exception:
        return False


def verify_and_update_password(password: str, stored_hash: str) -> tuple[bool, str | None]:
    """Verifica y ademas devuelve un hash nuevo si el almacenado quedo viejo.

    Devuelve `(valido, hash_nuevo)`. El hash nuevo aparece cuando los parametros de
    Argon2 cambiaron o cuando el hash usaba otro algoritmo, y `None` cuando el
    almacenado ya esta al dia.

    Es la unica forma de que subir el coste de Argon2 un dia sirva de algo: sin esto,
    el coste nuevo solo aplica a las passwords nuevas y las viejas se quedan con el
    coste viejo para siempre. El que tiene que guardar el hash devuelto es el
    llamador, en la misma transaccion que la autenticacion.
    """
    if not stored_hash:
        return False, None
    try:
        return get_password_hash().verify_and_update(password, stored_hash)
    except Exception:
        return False, None


def generate_token() -> str:
    """Un token opaco de 256 bits, en base64url.

    Del CSPRNG del sistema operativo (`secrets`), no de `random`: `random` esta
    sembrado con el reloj y es predecible, y un token predecible es un token que no
    es un token.
    """
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 en hexadecimal. Es lo unico que se persiste de un token.

    Se pasa `usedforsecurity=False` porque la FIPS lo exige cuando el hash no protege
    una password. Se marca de forma explicita que aca no hay una proteccion de
    password que saltarse: asi una politica de FIPS en modo estricto no rompe el
    arranque, y queda escrito que la decision es consciente y no un olvido. El
    resultado es identico en cualquier caso.
    """
    return hash_token_bytes(token).hex()


def hash_token_bytes(token: str) -> bytes:
    """SHA-256 en bruto: los 32 bytes que van a una columna `BYTEA(32)`.

    Existe separada de `hash_token` porque las dos consumers no quieren lo mismo.
    `refresh_tokens.token_hash` es `TEXT` y por eso recibe el hexadecimal, pero
    `bookings.secure_token_hash` es `LargeBinary(32)`: persistirle un `str` de 64
    caracteres en una columna de 32 bytes es un error de tipos que solo aparece en
    runtime, cuando la reserva se inserta. El digest crudo evita la conversion y
    ademas ocupa la mitad.
    """
    return hashlib.sha256(token.encode("utf-8"), usedforsecurity=False).digest()


def settings_snapshot() -> dict[str, int | str]:
    """Los parametros de cifrado efectivos. Para logs de arranque y diagnostico.

    Jamas incluir el hash ni la clave: solo los numeros publicos de la configuracion.
    """
    settings: Settings = get_settings()
    return {
        "argon2_time_cost": settings.argon2_time_cost,
        "argon2_memory_cost": settings.argon2_memory_cost,
        "argon2_parallelism": settings.argon2_parallelism,
        "jwt_algorithm": settings.jwt_algorithm,
        "access_token_minutes": settings.jwt_access_token_expire_minutes,
        "refresh_token_days": settings.refresh_token_expire_days,
    }


__all__ = [
    "SENTINEL_PREIMAGE",
    "TOKEN_BYTES",
    "generate_token",
    "get_password_hash",
    "get_sentinel_hash",
    "hash_password",
    "hash_token",
    "reset_password_hash_cache",
    "settings_snapshot",
    "verify_and_update_password",
    "verify_password",
]
