"""siembra las palabras reservadas del sistema

Revision ID: 0015_reserved_slugs
Revises: 0014_onboarding_create_business
Create Date: 2026-10-03

`slug_reservations` existia desde `0001`--con su UNIQUE, su modelo y su `reason`-- y
**no tenia ni una fila**. La reserva era, hasta aca, un documento: el unico chequeo
que habia era la lista en Python del modulo de onboarding, que se puede borrar sin
que nada falle y que no aplica a un alta que entre por `psql`.

Esta migracion pone las palabras en la base, que es donde el proyecto decidio que
vivan: `0014` las consulta desde adentro de `tempus_create_business` porque el rol
de la aplicacion no tiene permiso de lectura sobre esa tabla.

## La lista y de donde sale

Las once palabras de la linea de "Slugs reservados" de ARCHITECTURE.md §5, en tres
grupos con tres motivos distintos--y el `reason` lo dice, porque "este slug no se
puede tomar" sin motivo es una regla que el proximo que la lea va a querer eliminar:

- **Infraestructura de la propia aplicacion**: `api`, `r`, `static`, `assets`. Los
  assets de Vite y las rutas del panel se sirven desde el mismo origen que la pagina
  publica, asi que un negocio llamado `assets` se lleva el bundle de los demas.
- **Autenticacion y navegacion**: `login`, `register`, `admin`, `panel`, `app`. El
  nombre de la URL del negocio es la primera mitad de cada URL del panel, asi que
  estas palabras dan colision de rutas.
- **Nombres de dominio que no queremos en manos de un negocio**: `www`, `docs`.

**No se siembran sinónimos en otros idiomas**--`administrador`, `ingresar`,
`acceder`. Son palabras que en espanol valen como nombre de negocio, y reservarlas
seria una decision de producto que todavia nadie tomo. Las once de ARCHITECTURE.md
estan porque cada una choca con algo que existe hoy; una que no choque, todavia no.

## Por que `ON CONFLICT DO NOTHING`

Para que la migracion sea re-ejecutable sobre una base donde un operador ya habia
reservado `app` a mano, que es el caso real si alguien esta probando el alta antes
de que llegue el deploy. Sin eso, un `INSERT` directo revienta la migracion entera
y con ella las que van despues.

El `reason` no se actualiza en el conflicto: la fila que gana es la de quien la
escribio, y describe la misma prohibicion que describe esta.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

#: revision identifiers, used by Alembic.
revision: str = "0015_reserved_slugs"
down_revision: str | None = "0014_onboarding_create_business"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLA = "slug_reservations"

#: Las once palabras de la linea "Slugs reservados" de ARCHITECTURE.md §5, con el
#: motivo de cada una. El orden es el del documento--infraestructura, navegacion,
#: dominios-- y no el alfabetico, porque el orden de lectura es el del criterio.
RESERVADOS: tuple[tuple[str, str], ...] = (
    ("api", "Ruta de la API en el mismo origen que la pagina publica"),
    ("r", "Prefijo de los recursos de la API"),
    ("static", "Directorio de estaticos del frontend"),
    ("assets", "Directorio de assets del frontend"),
    ("login", "Ruta de autenticacion del panel"),
    ("register", "Ruta de alta de negocios"),
    ("admin", "Prefijo del panel administrativo"),
    ("panel", "Prefijo del panel del negocio"),
    ("app", "Prefijo de la aplicacion web"),
    ("www", "Subdominio www"),
    ("docs", "Subdominio de documentacion"),
)


def _literal(valor: str) -> str:
    """Un texto de Python como literal de SQL, con las comillas incluidas.

    Hace falta porque `op.execute` no acepta parametros: escribe la sentencia tal
    cual, y un `:nombre` sin parametro llega a Postgres como texto--error de
    sintaxis-- o, peor, como un placeholder que alguien resuelve a mano despues.

    Y no se puede usar un bind parametro con `op.get_bind().execute` como
    atajo, porque eso **rompe el modo offline**: `alembic upgrade head --sql`
    genera el script a partir de estas migraciones sin conectarse a ninguna base,
    y sin la base no hay quien resuelva los parametros. El script que genera el
    DBA para aplicar a mano--que es como se aplica en produccion-- saldria con
    `$1` sin su valor.

    Escapar la comilla simple duplicandola alcanza porque no hay barras en
    ninguno de los once motivos, y una barra--de aparecer-- haria falta el
    `E'...'`. El `assert` de abajo es la red por si alguien agrega un motivo con
    comilla y no la revisa: en una migracion, una excepcion en el import es
    mucho mejor que un `INSERT` mal formado.
    """
    assert "\\" not in valor, f"motivo con barra invertida, necesita E'...': {valor!r}"
    return "'" + valor.replace("'", "''") + "'"


def _valores() -> str:
    """El `INSERT` completo.

    Se genera desde `RESERVADOS` para que la lista y el SQL no puedan separarse: un
    `VALUES` escrito a mano con once filas al lado de una tupla con once elementos
    divergen igual de facil, y la fila que falta es la que nadie nota--la palabra
    que nadie sabe que esta reservada.
    """
    filas = ",\n                ".join(
        f"({_literal(slug)}, {_literal(motivo)})" for slug, motivo in RESERVADOS
    )
    return f"""
            INSERT INTO {_TABLA} (slug, reason)
            VALUES
                {filas}
            ON CONFLICT (slug) DO NOTHING
            """


def upgrade() -> None:
    op.execute(_valores())


def downgrade() -> None:
    # Borra **solo** las once palabras de esta migracion y no toda la tabla: entre
    # el upgrade y el downgrade un operador puede haber reservado algo mas--por
    # ejemplo el nombre de un socio-- y un `DELETE FROM slug_reservations` sin
    # filtro le devolveria una URL a la que el negocio ya esta enlazado.
    #
    # Los valores tambien se generan: un `IN` con once palabras escritas a mano y
    # once elementos en la tupla divergen igual de facil que el `VALUES`.
    marcas = ", ".join(_literal(slug) for slug, _ in RESERVADOS)
    op.execute(f"DELETE FROM {_TABLA} WHERE slug IN ({marcas})")


__all__ = ["RESERVADOS", "downgrade", "upgrade"]
