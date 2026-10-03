"""Comprueba que los tests de `test_security.py` fallan cuando el codigo se rompe.

Un test que pasa siempre no es un test. Este archivo no prueba el comportamiento: le
toca la vuelta al codigo, rompe cada garantia de `app/core/security.py` de a una vez,
y exige que algún test lo note. Si una mutacion sobrevive, el test que supuestamente
la cubria no la cubria, y el problema se descubre ahora y no en produccion.

La tecnica es mutacion de codigo real: se edita el archivo, se corre la suite
apuntada, se restaura. Es mas lenta que leer los tests y mucho mas dificil de
engañar, porque no depende de que el test este bien escrito sino de que el codigo este
bien.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SEGURIDAD = Path(__file__).resolve().parents[2] / "app" / "core" / "security.py"
OBJETIVO = "tests/unit/test_security.py"


# Cada mutacion es (nombre, texto_original, texto_roto). El texto original tiene que
# existir: si alguien reescribe esa linea, la mutacion deja de aplicar y el test
# falla con un mensaje claro en vez de pasar en silencio.
MUTACIONES: list[tuple[str, str, str]] = [
    (
        "argon2id -> argon2i",
        "                    type=Type.ID,",
        "                    type=Type.I,",
    ),
    (
        "el cache se invalida por parametro -> nunca",
        "    if _password_hash is None or _password_hash_params != params:",
        "    if _password_hash is None:",
    ),
    (
        # El objetivo real es `hash_token_bytes`, no `hash_token`: `hash_token`
        # solo delega (`return hash_token_bytes(token).hex()`), asi que romperlo
        # a el noeria romper el hash sino la representacion. Y `.digest()`, no
        # `.hexdigest()`: esta es la funcion que alimenta `BYTEA(32)`, y una
        # mutacion que devuelve hexei la haria fallar por el tamano--que es el
        # fallo real que la columna de 32 bytes evita.
        #
        # Estas dos mutaciones apuntaban a la linea anterior a que `hash_token` y
        # `hash_token_bytes` se separaran, y el guard de "el texto original tiene
        # que existir" las detecto en vez de dejarlas pasar en silencio.
        "SHA-256 -> MD5 del token",
        'return hashlib.sha256(token.encode("utf-8"), usedforsecurity=False).digest()',
        'return hashlib.md5(token.encode("utf-8")).digest()',
    ),
    (
        "token de 32 bytes -> 8 bytes",
        "TOKEN_BYTES = 32",
        "TOKEN_BYTES = 8",
    ),
    (
        "secrets -> random",
        "    return secrets.token_urlsafe(TOKEN_BYTES)",
        "    import random\n\n    return random.token_urlsafe(TOKEN_BYTES)",
    ),
    (
        "verify se traga la excepcion",
        "    try:\n        return get_password_hash().verify(password, stored_hash)\n    except Exception:\n        return False",
        "    return get_password_hash().verify(password, stored_hash)",
    ),
    (
        "verify_and_update nunca rehashea",
        "        return get_password_hash().verify_and_update(password, stored_hash)",
        "        ok = get_password_hash().verify(password, stored_hash)\n        return ok, None",
    ),
    (
        "el hash del token lleva sal aleatoria",
        'return hashlib.sha256(token.encode("utf-8"), usedforsecurity=False).digest()',
        "    import os\n\n    return hashlib.sha256(os.urandom(8) + token.encode()).digest()",
    ),
    (
        # Truncar el hexadecimal no rompe nada visible: el token sigue siendo un
        # string, la base lo acepta y el login sigue funcionando. Solo se rompe
        # la seguridad, y de forma silenciosa--que es la clase de fallo que este
        # archivo existe para encontrar. Sin esta mutacion, nadie mira.
        "el hash del token se trunca a 32 caracteres",
        "    return hash_token_bytes(token).hex()",
        "    return hash_token_bytes(token).hex()[:32]",
    ),
    (
        "el snapshot filtra la clave de firma",
        '    return {\n        "argon2_time_cost": settings.argon2_time_cost,',
        '    return {\n        "jwt_secret_key": settings.jwt_secret_key.get_secret_value(),\n        "argon2_time_cost": settings.argon2_time_cost,',
    ),
]


@pytest.fixture
def fuente_original() -> Iterator[str]:
    """El contenido bueno del archivo, y garantiza que se restaure."""
    texto = SEGURIDAD.read_text(encoding="utf-8")
    try:
        yield texto
    finally:
        SEGURIDAD.write_text(texto, encoding="utf-8")


def _correr_pytest() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-x",
            "--no-cov",
            "-p",
            "no:cacheprovider",
            OBJETIVO,
        ],
        capture_output=True,
        text=True,
        cwd=SEGURIDAD.parents[2],
    )


@pytest.mark.parametrize(("nombre", "original", "roto"), MUTACIONES, ids=[m[0] for m in MUTACIONES])
def test_la_mutacion_la_alguna_prueba(
    nombre: str, original: str, roto: str, fuente_original: str
) -> None:
    assert original in fuente_original, (
        f"la mutacion '{nombre}' ya no aplica: el codigo cambio de forma. "
        "O se actualiza la mutacion o el codigo, lo que corresponda."
    )
    SEGURIDAD.write_text(fuente_original.replace(original, roto, 1), encoding="utf-8")
    resultado = _correr_pytest()
    assert resultado.returncode != 0, (
        f"la mutacion '{nombre}' sobrevivio: ningun test de {OBJETIVO} la nota. "
        "Ese comportamiento no esta cubierto."
    )


def test_el_archivo_de_test_pasa_sin_mutar(fuente_original: str) -> None:
    """Control: el archivo pasa limpio. Sin esto, 'todo falla' pasa los tests.

    Sin este contrapeso, el criterio de esta archivo es 'la suite falla', y una
    mutacion mal aplicada que rompa todo el modulo daria verde. Este test exige el
    estado contrario: sin tocar nada, pasa.
    """
    resultado = _correr_pytest()
    assert resultado.returncode == 0, f"{OBJETIVO} falla sin mutar: {resultado.stdout[-2000:]}"
