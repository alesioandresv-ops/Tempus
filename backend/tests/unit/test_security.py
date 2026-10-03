"""Primitivas de criptografia: Argon2id, tokens opacos y hash de token.

Los tests de Argon2 bajan el coste a proposito. Con los parametros de produccion
(64 MB y 3 iteraciones) cada `hash()` tarda del orden de 100 ms, y una suite con
docenas de Verificacion seria de minutos. El coste real se prueba aparte, en un test
que corre una vez con los parametros de verdad.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator

import pytest
from app.core.config import get_settings
from app.core.security import (
    generate_token,
    get_password_hash,
    hash_password,
    hash_token,
    reset_password_hash_cache,
    settings_snapshot,
    verify_and_update_password,
    verify_password,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def argon2_rapido() -> Iterator[None]:
    """Argon2 con coste minimo, y lo restaura al final.

    Va como `usefixtures` y **no** como autouse a proposito. Con autouse, el unico
    test que mide el coste real de produccion queda contaminado por el mismo atajo que
    el resto del archivo, y pasa a medir 0,2 ms y a afirmar que Argon2 es gratis. Ese
    fallo es silencioso y es exactamente el que este test existe para evitar: un
    `autouse` que abarata la suite no puede convivir con un test que verifica que la
    primitiva es cara.
    """
    settings = get_settings()
    original = (
        settings.argon2_time_cost,
        settings.argon2_memory_cost,
        settings.argon2_parallelism,
    )
    settings.argon2_time_cost = 1
    settings.argon2_memory_cost = 8
    settings.argon2_parallelism = 1
    reset_password_hash_cache()
    try:
        yield
    finally:
        (
            settings.argon2_time_cost,
            settings.argon2_memory_cost,
            settings.argon2_parallelism,
        ) = original
        reset_password_hash_cache()


# Por nombre y no por objeto: pytest no acepta el objeto fixture en
# `usefixtures`, y ademas asi el alias se ve en el `--fixtures` del reporte.
rapido = pytest.mark.usefixtures("argon2_rapido")


@rapido
class TestHashPassword:
    def test_no_devuelve_la_password(self) -> None:
        hashed = hash_password("hunter2")
        assert "hunter2" not in hashed
        assert hashed.startswith("$argon2id$")

    def test_es_argon2id_y_no_argon2i(self) -> None:
        """Argon2id, no `i` ni `d`.

        `i` resiste GPU a costa de memoria, `d` resiste ataques laterales a costa de
        tiempo. Argon2id da las dos. Un `v=19` con tipo `i` seria una eleccion
        distinta de la del §10.1 y tiene que notarse en el hash.
        """
        assert hash_password("hunter2").startswith("$argon2id$v=19$")

    def test_la_misma_password_da_hashes_distintos(self) -> None:
        """Dos hashes de la misma password no son iguales: la sal es aleatoria."""
        assert hash_password("hunter2") != hash_password("hunter2")

    def test_el_hash_cambia_con_los_parametros(self) -> None:
        """Los parametros viajan en el hash, no en un config aparte.

        Si no fueran parte del string, un hash creado con el coste viejo seguiria
        validandose con el coste nuevo y `verify_and_update` no tendria nada que
        actualizar.
        """
        assert "m=8" in hash_password("hunter2")
        settings = get_settings()
        settings.argon2_memory_cost = 16
        reset_password_hash_cache()
        assert "m=16" in hash_password("hunter2")

    def test_una_password_vacia_se_hashea(self) -> None:
        """Una password vacia es un hash valido, no un caso especial.

        Que el login la rechace es responsabilidad del endpoint, que ademas tiene que
        distinguir "vacia" de "no mandada". Hashear la vacia es correcto: es una
        entrada mas.
        """
        assert verify_password("", hash_password("")) is True


@rapido
class TestVerifyPassword:
    def test_coincide(self) -> None:
        assert verify_password("hunter2", hash_password("hunter2")) is True

    def test_no_coincide(self) -> None:
        assert verify_password("hunter3", hash_password("hunter2")) is False

    def test_un_hash_corrupto_da_false_y_no_excepcion(self) -> None:
        """Un hash ilegible no puede ser un 500.

        Un 500 le confirma al atacante que el usuario existe y que su hash esta
        corrupto. `False` lo mezcla con "password incorrecta", que es la unica
        respuesta que no filtra.
        """
        assert verify_password("hunter2", "no-es-un-hash") is False
        assert verify_password("hunter2", "") is False
        assert verify_password("hunter2", "$argon2id$v=19$m=8,t=1,p=1$c2FsdA$basura") is False

    def test_no_distingue_hash_roto_de_password_incorrecta(self) -> None:
        """El resultado no depende de *por que* fallo.

        Un hash corrupto y una password incorrecta tienen que dar el mismo `bool`, y
        no una excepcion en un caso y `False` en el otro. Si difieren, el endpoint que
        los use esta a una distincion de distancia: uno produce un `False` y el otro
        un 500, y esa diferencia es la que permite enumerar que emails existen.
        """
        hash_roto = verify_password("hunter2", "no-es-un-hash")
        password_mala = verify_password("otra-cosa", hash_password("hunter2"))
        assert hash_roto is False
        assert hash_roto == password_mala


@rapido
class TestVerifyAndUpdate:
    def test_no_hay_hash_nuevo_cuando_todo_coincide(self) -> None:
        valid, nuevo = verify_and_update_password("hunter2", hash_password("hunter2"))
        assert valid is True
        assert nuevo is None

    def test_rehashea_cuando_suben_los_parametros(self) -> None:
        """Subir el coste de Argon2 tiene que alcanzar a las passwords viejas.

        Sin esto, el parametro nuevo solo aplica a las passwords nuevas y las viejas
        quedan con el coste viejo indefinidamente.
        """
        viejo = hash_password("hunter2")
        assert "m=8" in viejo
        settings = get_settings()
        settings.argon2_memory_cost = 64
        reset_password_hash_cache()
        valid, nuevo = verify_and_update_password("hunter2", viejo)
        assert valid is True
        assert nuevo is not None
        assert "m=64" in nuevo
        assert verify_password("hunter2", nuevo) is True

    def test_no_rehashea_una_password_incorrecta(self) -> None:
        """Reescribir el hash de una password que no se sabe no sirve de nada."""
        valid, nuevo = verify_and_update_password("no-es-la-mia", hash_password("hunter2"))
        assert valid is False
        assert nuevo is None

    def test_hash_corrupto_da_false(self) -> None:
        assert verify_and_update_password("hunter2", "basura") == (False, None)


class TestGenerateToken:
    def test_largo_correcto(self) -> None:
        """32 bytes en base64url dan 43 caracteres.

        43 es la longitud canonica de 256 bits en base64url sin padding. Un token
        mas corto no seria el nivel de entropia que fija ADR-0009, y uno mas largo
        seria mas de lo que hace falta.
        """
        assert len(generate_token()) == 43

    def test_es_urlsafe_sin_padding_ni_iguales(self) -> None:
        token = generate_token()
        assert re.fullmatch(r"[A-Za-z0-9_-]+", token)
        assert not token.endswith("=")
        assert generate_token() != generate_token()

    def test_no_usa_random_sin_sembrar(self) -> None:
        """El token no puede depender de una fuente predecible.

        `random` esta sembrado con el reloj del sistema: dos procesos arrancados en el
        mismo segundo generan la misma secuencia. `secrets` lee del CSPRNG del
        sistema operativo. Este test no puede probarlo estadisticamente, asi que
        comprueba lo que si es observable: la entropia por byte.
        """
        tokens = [generate_token() for _ in range(200)]
        bytes_totales = set("".join(tokens))
        # 200 tokens de 43 chars = 8600 caracteres sobre una fuente sesgada. Si el
        # alfabeto se reduce drasticamente, la fuente no es un CSPRNG.
        assert len(bytes_totales) >= 60


class TestHashToken:
    def test_es_sha256_hexadecimal(self) -> None:
        """El hash es SHA-256 en hexadecimal, para un indice unico de 64 chars."""
        esperado = hashlib.sha256(b"token-de-prueba", usedforsecurity=False).hexdigest()
        assert hash_token("token-de-prueba") == esperado
        assert len(hash_token("x")) == 64
        assert re.fullmatch(r"[0-9a-f]{64}", hash_token("x"))

    def test_es_estable_y_sin_sal(self) -> None:
        """A diferencia de la password, el hash del token es deterministico.

        Es lo que permite buscar por el indice unico en vez de recorrer la tabla, y lo
        que hace que dos Rotaciones de la misma familia se puedan relacionar. Sin sal
        es correcto: la entrada tiene 2^256 de entropia, no hay diccionario.
        """
        assert hash_token("abc") == hash_token("abc")

    def test_diferente_token_da_diferente_hash(self) -> None:
        assert hash_token("a") != hash_token("b")

    def test_no_pierde_el_unicode(self) -> None:
        """Un token no canonico produce un hash, no una excepcion de encoding.

        Los tokens se generan con `token_urlsafe`, asi que siempre son ASCII. Pero el
        hash tambien se usa para materializar `secure_token` de reservas, y una
        excepcion de encoding ahi seria un 500 en la creacion de una reserva.
        """
        assert (
            hash_token("tokén-café-🔑")
            == hashlib.sha256("tokén-café-🔑".encode(), usedforsecurity=False).hexdigest()
        )

    def test_el_hash_no_es_reversible_a_un_token_valido(self) -> None:
        """El hash no es a su vez un token: no sirve para autenticarse.

        Verificacion estructural: un atacante que lee la base tiene el hash, y si
        `hash_token(hash)` devolviera algo que `generate_token` podria producir, la
        tabla seria un almacen de tokens validos y no de hashes.
        """
        h = hash_token(generate_token())
        assert not re.fullmatch(r"[A-Za-z0-9_-]{43}", h)


@rapido
class TestCacheDelHasher:
    """El `PasswordHash` se memoriza, y por eso necesita invalidarse solo.

    En produccion los parametros no cambian dentro del proceso, asi que el cache es
    inocuo. El problema aparece en los tests: un test que baja el coste sin invalidar
    el cache deja hasher barato el resto de la suite, y el test que verifica que
    Argon2 es caro pasa a medir 0,2 ms. Que se invalide por parametro hace que ese
    error sea imposible de cometer por olvido.
    """

    def test_cambiar_los_parametros_invalida_el_cache_sin_reset(self) -> None:
        settings = get_settings()
        antes = hash_password("hunter2")
        assert "m=8" in antes

        # Sin llamar a `reset_password_hash_cache()` a proposito.
        settings.argon2_memory_cost = 32

        assert "m=32" in hash_password("hunter2")

    def test_el_mismo_parametro_reusa_el_cache(self) -> None:
        """Sin cambios de configuracion, el hasher es el mismo objeto."""
        primero = get_password_hash()
        assert get_password_hash() is primero

    def test_volver_al_original_reinicia_el_cache(self) -> None:
        settings = get_settings()
        hash_password("hunter2")
        settings.argon2_memory_cost = 32
        hash_password("hunter2")
        settings.argon2_memory_cost = 8
        assert "m=8" in hash_password("hunter2")


class TestSettingsSnapshot:
    def test_no_incluye_claves_ni_hashes(self) -> None:
        """El snapshot es para diagnostico y va a logs.

        Si apareciera la clave de firma o la de cifrado, este helper seria la forma
        mas corta de filtrarlas a un log o a un endpoint de health.
        """
        snapshot = settings_snapshot()
        assert "jwt_secret_key" not in snapshot
        assert "encryption_key" not in snapshot
        assert snapshot["jwt_algorithm"] == "HS256"
        assert all(isinstance(v, (int, str)) for v in snapshot.values())


class TestCosteReal:
    """El coste de produccion, una vez. Los tests de arriba lo falsean a proposito."""

    @pytest.mark.slow
    def test_argon2_con_parametros_de_produccion_tarda_lo_suficiente(self) -> None:
        """Con los parametros reales, hashear tiene que ser caro de verdad.

        El proposito de Argon2 es gastar tiempo y memoria. Si un cambio de
        configuracion lo volviera instantaneo, la proteccion contra fuerza bruta
        desaparece y ningun test funcional lo notaria. Solo se nota midiendo.

        El limite superior es generoso a proposito: en una VM compartida y en CI puede
        tardar bastante mas que en un escritorio. Lo que se verifica es que no sea
        instantaneo, que es la condicion que importa.
        """
        import time

        reset_password_hash_cache()
        inicio = time.perf_counter()
        hash_password("hunter2")
        elapsed = time.perf_counter() - inicio
        reset_password_hash_cache()

        assert elapsed >= 0.02, f"Argon2 tardo {elapsed * 1000:.1f} ms con los parametros reales"
        assert elapsed < 5.0, f"Argon2 tardo {elapsed:.2f} s: hay que revisar los parametros"
