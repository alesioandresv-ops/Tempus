"""El codigo fuente no puede tener caracteres fuera del latin-1.

Este test existe por una razon concreta y repetida: durante la edicion se colaron
varias veces fragmentos de sintaxis de llamadas a herramientas y un par de
palabras en otros idiomas dentro de docstrings en español. **Ninguno de los dos
casos lo detecta el interprete.** Un docstring acepta cualquier caracter, y un
`<invoke name=` dentro de un comentario es, para Python, un comentario.

El sintoma que los delata es siempre el mismo y siempre tarde: la documentacion
sale con basura, o el archivo tiene codigo que no compila y se descubre cuando
falta una hora para entregar. Un test que corre en un segundo es mas barato que
esa hora.

Que se chequea:

1. **CJK, hangul, cirilico y fullwidth.** Nunca son legitimos: el proyecto esta
   escrito en español y no tiene soporte multiidioma, asi que su presencia
   significa un error de edicion, no una decision.
2. **Marcadores de tool-call**: los de apertura y cierre de una llamada de edicion
   de codigo. Un `.py` no deberia contenerlos nunca.
3. **Caracteres de ancho cero y BOM.** Inocuos para Python, rompen editores,
   diffs y cualquier chequeo que lea el archivo crudo. En un repo sin
   `.gitattributes` que los controle, vuelven a entrar.
4. **Tabuladores en la indentacion.** Python los acepta y los muestra como
   espacio; dos personas con distinta configuracion de editor fight por el mismo
   archivo.

Lo que **no** se chequea: acentos, enye y demas latin-1. El proyecto esta escrito
en español y `Con nº1` es codigo valido.

**Este archivo se chequea a si mismo, y no se exceptua.** La primera version
armaba los marcadores como literales y el test se marcaba a si mismo, que es la
forma de que un linter termine siendo el unico archivo exento. La salida es
ensamblar los marcadores de partes: el archivo no contiene la cadena prohibida,
pero el test sigue buscando exactamente la misma.
"""

from __future__ import annotations

import pathlib
import re
import unicodedata

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]

EXCLUIR = {".venv", "venv", "node_modules", "__pycache__", ".git", ".pytest_cache"}

#: (inicio, fin, nombre) de los rangos que solo aparecen por un error de edicion.
RANGOS_SOSPECHOSOS = [
    (0x2E80, 0x9FFF, "CJK"),
    (0xAC00, 0xD7AF, "hangul"),
    (0x0400, 0x04FF, "cirilico"),
    (0x3000, 0x303F, "cjk-puntuacion"),
    (0xFF00, 0xFFEF, "fullwidth"),
]

#: Los marcadores de una llamada de edicion de codigo, armados de partes.
#:
#: La concatenacion es lo que permite que este archivo pase su propio test sin
#: meterse en la lista de exentos. Excluirlo seria mas simple y peor: dejaria al
#: unico archivo donde las reglas estan escritas como el unico donde no se
#: comprueban, y un archivo que se autolimpia a si mismo no puede usarse para
#: argumentar que el resto esta limpio.
_CIERRE = chr(62)
_ANGULO = chr(60) + _CIERRE
MARCADORES_TOOL_CALL = (
    _ANGULO + "invoke",
    _ANGULO + "old" + "String" + _CIERRE,
    _ANGULO + "new" + "String" + _CIERRE,
    _ANGULO + "/" + "old" + "String" + _CIERRE,
    _ANGULO + "tool" + "_call",
)

#: Ancho cero, zero width joiner y BOM.
CARGAS = chr(0x200B) + chr(0x200C) + chr(0x200D) + chr(0xFEFF)


def _archivos_python() -> list[pathlib.Path]:
    return [ruta for ruta in sorted(RAIZ.rglob("*.py")) if not (set(ruta.parts) & EXCLUIR)]


@pytest.mark.parametrize("ruta", _archivos_python(), ids=lambda p: str(p.relative_to(RAIZ)))
def test_archivo_sin_caracteres_sospechosos(ruta: pathlib.Path) -> None:
    """Ningun rango fuera del latin-1, ni marcadores, ni ancho cero."""
    problemas: list[str] = []
    # `newline=None` normaliza CRLF y LF, asi que un archivo escrito en Windows no
    # falla por el `\r`.
    lineas = ruta.read_text(encoding="utf-8").splitlines()

    for numero, linea in enumerate(lineas, 1):
        for ch in linea:
            cp = ord(ch)
            for inicio, fin, nombre in RANGOS_SOSPECHOSOS:
                if inicio <= cp <= fin:
                    problemas.append(
                        f"linea {numero}: {nombre} U+{cp:04X} ({unicodedata.name(ch, '?')})"
                    )
                    break

        for marcador in MARCADORES_TOOL_CALL:
            if marcador in linea:
                problemas.append(f"linea {numero}: marcador de tool-call {marcador!r}")

        for ch in CARGAS:
            if ch in linea:
                problemas.append(f"linea {numero}: caracter invisible U+{ord(ch):04X}")

    assert not problemas, (
        f"{ruta.relative_to(RAIZ)} tiene caracteres que no pueden estar en el "
        f"proyecto:\n  " + "\n  ".join(problemas)
    )


@pytest.mark.parametrize("ruta", _archivos_python(), ids=lambda p: str(p.relative_to(RAIZ)))
def test_archivo_sin_bom(ruta: pathlib.Path) -> None:
    """Ningun `.py` arranca con BOM."""
    datos = ruta.read_bytes()
    assert not datos.startswith(b"\xef\xbb\xbf"), (
        f"{ruta.relative_to(RAIZ)} arranca con BOM. Python lo tolera al importar, "
        f"pero rompe editores, diffs y cualquier chequeo que lo lea crudo."
    )


@pytest.mark.parametrize("ruta", _archivos_python(), ids=lambda p: str(p.relative_to(RAIZ)))
def test_archivo_sin_tabuladores(ruta: pathlib.Path) -> None:
    """La indentacion es con espacios, no con tabuladores."""
    con_tabs = [
        numero
        for numero, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1)
        if "\t" in linea
    ]
    assert not con_tabs, (
        f"{ruta.relative_to(RAIZ)} usa tabuladores en las lineas {con_tabs}. "
        f"Python los acepta y los muestra como espacios: dos personas con distinta "
        f"configuracion de editor pelean por el mismo archivo sin que ninguna see "
        f"el conflicto."
    )


#: Escape de cadena al final de una linea de comentario.
#:
#: Lo que busca es una comilla de apertura perdida. En una cadena de varias lineas
#: como esta--
#:
#:     contenido = (
#:         "# primera\\n"
#:         "# segunda\\n"          <-- aca faltaba la comilla de apertura
#:         "# tercera\\n"
#:     )
#:
#: --la segunda linea empieza con `#`, asi que para Python es un comentario entero:
#: la comilla que abria un literal queda tragada y el interprete no dice nada, porque
#: un comentario puede contener cualquier caracter. El archivo es valido, los tests
#: pasan, y lo que falta es un fragmento de texto--que es exactamente el sintoma
#: dificil, porque aparece en la salida y no en ninguna excepcion.
#:
#: `\n"` al final de un comentario es la firma casi infalible. Un comentario de
#: verdad puede hablar de `\n`, pero no suele terminar en el.
#:
#: **Las barras del ejemplo van duplicadas a proposito.** Esta es la unica
#: tolerancia del patron--`\\n` no se marca-- y es la que evita que el archivo se
#: marque a si mismo por mostrar el defecto que busca. Si se escribieran simples,
#: el propio test fallaria contra su propia documentacion.
ESCAPE_AL_FINAL_DE_COMENTARIO = re.compile(r'(?<!\\)\\[nrt]"?\s*$')


@pytest.mark.parametrize("ruta", _archivos_python(), ids=lambda p: str(p.relative_to(RAIZ)))
def test_archivo_sin_comentarios_que_tragan_texto(ruta: pathlib.Path) -> None:
    """Ningun comentario termina en un escape de cadena.

    Si el comentario es legitimo, el autor quiere escribir una barra y no un salto.
    Si no lo es, se le cayo la comilla de apertura de una cadena multilinea y hay
    texto que desaparecio del archivo sin que nadie lo note.
    """
    sospechosos = [
        (numero, linea)
        for numero, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1)
        if linea.lstrip().startswith("#") and ESCAPE_AL_FINAL_DE_COMENTARIO.search(linea)
    ]
    assert not sospechosos, (
        f"{ruta.relative_to(RAIZ)} tiene comentarios que parecen texto tragado:\n"
        + "\n".join(f"  linea {n}: {linea.strip()}" for n, linea in sospechosos)
        + "\n\nSi es un comentario de verdad, no deberia terminar en un escape de cadena.\n"
        "Si es texto que se perdio, le falta la comilla de apertura: la linea tendria "
        'que empezar con `"# ..."` y no con `# "..."`.'
    )
