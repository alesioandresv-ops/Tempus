"""Las listas congeladas de la migracion 0002 no pueden desincronizarse del modelo.

La migracion `0002_rls_triggers_grants` copia a mano las tablas de tenant, las de
timestamps y las prohibidas al rol de la app, en vez de importarlas de
`app.models`. La copia es lo correcto: una migracion tiene que seguir aplicando
dentro de dos anos, cuando el modelo haya cambiado.

El precio de esa decision es que las copias pueden quedar viejas. Ese drift es
invisible para los otros suites: `test_rls_isolation` consulta el catalogo y
compara contra `app.models`, asi que detecta que *la base* no tiene la politica,
pero no detecta que la migracion que deberia crearla ya no la crea. El sintoma es
un `alembic upgrade` que termina sin error y deja una tabla de tenant sin RLS.

Estos tests cierran ese hueco: comparan las listas de la migracion contra el modelo
sin que la migracion dependa del codigo, que es la misma disciplina del otro lado.

Se importa la migracion con `importlib` y no con un import normal porque vive fuera
del paquete `app` y no es un modulo importable por nombre.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "migrations" / "versions" / "0002_rls_triggers_grants.py"

pytestmark = pytest.mark.tenancy


def _load_migration() -> ModuleType:
    """Carga 0002 como modulo sin pisar el registro de `sys.modules`.

    Alembic importa el archivo con su propio loader de migraciones, asi que un
    import normal puede devolver la copia cacheada o chocar con el modulo que creo
    Alembic. El `importlib` explicito con nombre propio evita las dos cosas.
    """
    if MIGRATION.name in sys.modules:
        return sys.modules[MIGRATION.name]

    spec = importlib.util.spec_from_file_location(MIGRATION.stem, MIGRATION)
    assert spec is not None and spec.loader is not None, f"no se pudo cargar {MIGRATION}"
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[MIGRATION.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def migracion() -> ModuleType:
    return _load_migration()


def _tablas_del_modelo() -> set[str]:
    """Nombres de tabla registrados, sin la de Alembic.

    Importar `app.models` es lo que puebla la `metadata`; sin ese import la
    metadata viene vacia y todos los tests de este archivo compararian contra
    nada. Se importa adentro de la funcion y no a nivel de modulo para que el
    orden de coleccion de pytest no decida cuando se ejecuta.
    """
    import app.models  # noqa: F401  registers every table in Base.metadata
    from app.db.base import Base

    return set(Base.metadata.tables) - {"alembic_version"}


class TestTablasDeTenant:
    def test_la_lista_congelada_iguala_las_tablas_del_modelo(self, migracion: ModuleType) -> None:
        """Cada tabla con `business_id` en el modelo tiene su politica en 0002.

        La comparacion va en los dos sentidos a proposito. Solo "modelo ->
        migracion" detectaria una tabla nueva sin politica, que es el agujero de
        seguridad. La direccion inversa detecta una tabla que la migracion todavia
        maneja y el modelo ya no declara, que en un `downgrade` intentaria borrar
        una politica sobre una tabla inexistente.
        """
        from app.models import TENANT_TABLES

        congeladas = set(migracion.TENANT_TABLES)
        modelo = set(TENANT_TABLES)

        assert congeladas - modelo == set(), (
            f"0002 maneja tablas que el modelo ya no declara: {sorted(congeladas - modelo)}"
        )
        assert modelo - congeladas == set(), (
            f"tablas de tenant del modelo sin politica en 0002: {sorted(modelo - congeladas)}"
        )

    def test_jobs_no_esta_en_las_tablas_de_tenant(self, migracion: ModuleType) -> None:
        """`jobs` es global con `business_id` nullable, y por eso no lleva RLS.

        Es la excepcion que hace que el invariante "toda tabla con `business_id`
        tiene RLS" sea falso, asi que necesita un test propio: si alguien la
        agrega a la lista por uniformidad, el dispatcher de la plataforma deja de
        poder leer sus propios trabajos y el sintoma es un job que nunca se ejecuta.
        """
        from app.models import GLOBAL_TABLES

        assert "jobs" not in migracion.TENANT_TABLES
        assert "jobs" in set(GLOBAL_TABLES)

    def test_whatsapp_templates_si_lleva_rls_pese_a_su_business_id_nullable(
        self, migracion: ModuleType
    ) -> None:
        """La plantilla de plataforma es invisible, pero la tabla sigue filtrada.

        `whatsapp_templates.business_id` es nullable para la plantilla de
        plataforma. Alguien podria concluir que la tabla no necesita RLS porque
        "no es de un solo tenant". No: las filas con `business_id = NULL` no las ve
        nadie y las que lo tienen las ve solo su negocio, que es exactamente lo que
        hace la politica con `USING`.
        """
        from app.db.base import Base

        assert "whatsapp_templates" in migracion.TENANT_TABLES
        assert Base.metadata.tables["whatsapp_templates"].c["business_id"].nullable is True

    def test_las_tablas_de_tenant_existen_todas_en_la_metadata(self, migracion: ModuleType) -> None:
        conocidas = _tablas_del_modelo()
        for tabla in migracion.TENANT_TABLES:
            assert tabla in conocidas, f"{tabla} no existe en la metadata"


class TestTimestamps:
    def test_todas_las_tablas_del_modelo_estan_en_la_lista(self, migracion: ModuleType) -> None:
        """El trigger de `updated_at` alcanza cada tabla que declara el mixin.

        Verificado de los dos lados. Si falta una tabla, nadie actualiza su
        `updated_at` y los incidentes se investigan con una fecha vieja. Si sobra,
        el `CREATE TRIGGER` falla y la migracion entera no aplica.
        """
        import app.models  # noqa: F401
        from app.db.base import Base

        modelo = {
            nombre
            for nombre, tabla in Base.metadata.tables.items()
            if {"created_at", "updated_at"} <= set(tabla.c.keys())
        }
        congeladas = set(migracion._TABLES_WITH_TIMESTAMPS)

        assert congeladas - modelo == set(), (
            f"0002 pone trigger en tablas sin timestamps: {sorted(congeladas - modelo)}"
        )
        assert modelo - congeladas == set(), (
            f"tablas con timestamps sin trigger en 0002: {sorted(modelo - congeladas)}"
        )

    def test_la_lista_no_contiene_alembic_version(self, migracion: ModuleType) -> None:
        """Alembic gestiona su propia tabla y no debe llevar trigger de negocio."""
        assert "alembic_version" not in migracion._TABLES_WITH_TIMESTAMPS

    def test_todas_las_tablas_de_tenant_tienen_trigger(self, migracion: ModuleType) -> None:
        """Ninguna tabla filtrada se queda sin `updated_at`.

        El log de auditoria y la agenda del profesional se investigan por fecha de
        modificacion, asi que este cruce entre las dos listas no es decorativo.
        """
        sin_trigger = set(migracion.TENANT_TABLES) - set(migracion._TABLES_WITH_TIMESTAMPS)
        assert sin_trigger == set(), f"tablas de tenant sin trigger: {sorted(sin_trigger)}"


class TestTablasProhibidas:
    def test_las_tres_tablas_de_administracion_sin_acceso(self, migracion: ModuleType) -> None:
        """`platform_users`, `businesses` y `slug_reservations` quedan fuera del DML.

        `slug_reservations` existe para que dos negocios no pelen por el mismo slug,
        y `platform_users` es la tabla de credenciales internas: que la app pueda
        tocarlas las vuelve inútiles como frontera.
        """
        from app.models import GLOBAL_TABLES

        congeladas = set(migracion.FORBIDDEN_FOR_APP_ROLE)
        esperados = {"platform_users", "businesses", "slug_reservations"}

        assert congeladas == esperados
        assert congeladas <= set(GLOBAL_TABLES)

    def test_businesses_queda_solo_legible(self, migracion: ModuleType) -> None:
        """`businesses` esta prohibida y de ahi se le reabre solo el SELECT.

        La app necesita nombre y zona horaria del negocio en casi cada request, pero
        no puede alterar `slug` (es la direccion publica) ni `status` (decide si el
        negocio existe). Por eso la lista la declara inaccesible y el `upgrade` le
        concede SELECT despues: prohibido y legible a la vez, que es el estado
        correcto y el unico que el primer caso podria hacer parecer contradictorio.
        """
        assert "businesses" in migracion.FORBIDDEN_FOR_APP_ROLE
        # La re-apertura vive en el cuerpo de la migracion, no en una constante.
        # Se verifica que el SQL este, para que quitarlo sea un cambio visible.
        fuente = MIGRATION.read_text(encoding="utf-8")
        assert 'GRANT SELECT ON TABLE "businesses"' in fuente
        assert 'REVOKE ALL ON TABLE "{table}" FROM "{APP_ROLE}"' in fuente


class TestCoherenciaInterna:
    def test_las_colecciones_no_se_solapan(self, migracion: ModuleType) -> None:
        """Tenant y prohibidas no pueden compartir tablas.

        El solapamiento no rompe la migracion, porque el revoke corre despues del
        grant, pero vuelve ilegible la politica de permisos: nadie podria responder
        si una tabla prohibida tiene o no UPDATE.
        """
        tenant = set(migracion.TENANT_TABLES)
        prohibidas = set(migracion.FORBIDDEN_FOR_APP_ROLE)

        assert tenant & prohibidas == set(), f"tenant y prohibida: {tenant & prohibidas}"

    def test_todas_las_tablas_de_la_migracion_existen_en_la_metadata(
        self, migracion: ModuleType
    ) -> None:
        """Ninguna lista nombra una tabla que no exista.

        Este es el test que mas veces atrapa un error real: una tabla renombrada
        deja el nombre viejo en la migracion, el `ALTER TABLE` falla en el
        despliegue y no en la maquina de quien la escribio.
        """
        conocidas = _tablas_del_modelo()
        for nombre in (
            migracion.TENANT_TABLES,
            migracion._TABLES_WITH_TIMESTAMPS,
            migracion.FORBIDDEN_FOR_APP_ROLE,
        ):
            assert set(nombre) <= conocidas, f"tablas inexistentes: {set(nombre) - conocidas}"


class TestLaDocumentacionNoSeQuedaVieja:
    """`ARCHITECTURE.md` §5.8 dice qué tablas llevan RLS y cuáles no.

    Es documentación, y la documentación no falla: si se agrega una tabla y la
    sección no se actualiza, nadie se entera hasta que alguien la lee para decidir si
    esa tabla necesita permisos. Este test la ata al modelo, que es la única fuente
    que no se puede editar sin que algo falle.
    """

    ARCHIVO = BACKEND.parent / "ARCHITECTURE.md"
    ENCABEZADO = "### 5.8"

    def _seccion(self) -> str:
        assert self.ARCHIVO.is_file(), f"no existe {self.ARCHIVO}"
        lineas = self.ARCHIVO.read_text(encoding="utf-8").splitlines()
        inicio = next(
            (i for i, linea in enumerate(lineas) if linea.startswith(self.ENCABEZADO)),
            None,
        )
        assert inicio is not None, (
            f"{self.ARCHIVO.name} no tiene la sección {self.ENCABEZADO}, que es donde "
            "se documenta qué tablas llevan RLS"
        )
        fin = next(
            (
                i
                for i in range(inicio + 1, len(lineas))
                if lineas[i].startswith("## ") and not lineas[i].startswith("### ")
            ),
            len(lineas),
        )
        return "\n".join(lineas[inicio:fin])

    def test_las_todas_de_rls_estan_nombradas(self) -> None:
        import app.models

        seccion = self._seccion()
        con_rls = _tablas_del_modelo() - app.models.GLOBAL_TABLES
        faltantes = {tabla for tabla in con_rls if f"`{tabla}`" not in seccion}
        assert not faltantes, (
            f"tablas con RLS que §5.8 no nombra: {sorted(faltantes)}. Agregarlas a la "
            "sección o sacales RLS, según corresponda."
        )

    def test_las_globales_estan_nombradas(self) -> None:
        import app.models

        seccion = self._seccion()
        faltantes = {tabla for tabla in app.models.GLOBAL_TABLES if f"`{tabla}`" not in seccion}
        assert not faltantes, (
            f"tablas globales que §5.8 no nombra: {sorted(faltantes)}. Son las que no "
            "tienen RLS, y son exactamente las que hay que justificar."
        )

    def test_el_conteo_de_tablas_no_esta_vejo(self) -> None:
        """El conteo de §5.8 tiene que ser el real.

        El número aparece en varios lugares del documento y ninguno de ellos falla si
        queda viejo. Acá se chequea contra la metadata, que sí.

        La comparación es insensible a mayúsculas porque el número va en medio de una
        frase y si no, el test falla por la mayúscula de "De" y hay que ir a mirar por
        qué. Un test que se rompe por eso deja de leerse.
        """
        import app.models  # noqa: F401

        total = len(_tablas_del_modelo())
        seccion = self._seccion().lower()
        assert f"de las {total} tablas" in seccion, (
            f"§5.8 dice de cuántas tablas habla y ya no son {total}. Buscar el conteo "
            "viejo en el documento: es el que engaña al que decide permisos."
        )
        # Y el detalle de la tabla de la sección, que es donde se lee la lista.
        fila = next(
            (linea for linea in self._seccion().splitlines() if linea.startswith("| `businesses`")),
            None,
        )
        assert fila is not None, (
            "§5.8 perdió la tabla que explica por qué `businesses` no lleva RLS"
        )
