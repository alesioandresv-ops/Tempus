"""El pipeline tiene que hacer las cosas en el orden en que se las pide.

Este archivo existe por un bug concreto que estuvo en `.github/workflows/ci.yml`: el
paso `docker compose down -v` estaba ubicado **despues** de levantar PostgreSQL y
**antes** de migrar y de correr los tests. El pipeline era valido contra el schema de
GitHub Actions -- y por eso el workflow entero pasaba la validacion --, pero en cada
run tumbaba la base antes de usarla y los tests fallaban con un error de conexion que
no senalaba el problema real.

Un error de orden en un YAML no lo agarra el linter, ni `actionlint`, ni
`check-jsonschema`. Solo lo agarra un test que mire la lista de pasos en orden, que es
lo que hace este.

**Por que un test y no un comentario.** El comentario ya estaba, y no sirvio: el paso
tenia su propia explicacion de por que era correcto. Los comentarios se desactualizan
cuando el archivo se edita; este test falla en el momento en que alguien agrega un
paso despues del teardown.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

TEARDOWN = "docker compose down -v"
SETUP = "docker compose up -d --wait"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    """El workflow parseado.

    Si el YAML no parsea, el test falla con el error de `yaml` en vez de con un
    `AttributeError` de `None`, que no dice nada.
    """
    datos: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return datos


def _pasos(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [paso for paso in job.get("steps", []) if isinstance(paso, dict)]


def _es_teardown(paso: dict[str, Any]) -> bool:
    return TEARDOWN in str(paso.get("run", ""))


def _es_setup(paso: dict[str, Any]) -> bool:
    return SETUP in str(paso.get("run", ""))


class TestElWorkflowSeParsea:
    def test_existe_y_es_yaml_valido(self, workflow: dict[str, Any]) -> None:
        assert "jobs" in workflow, "el workflow no declara jobs"
        assert workflow["jobs"], "el workflow no tiene jobs"

    def test_los_jobs_esperados_estan(self, workflow: dict[str, Any]) -> None:
        assert set(workflow["jobs"]) == {"calidad", "test", "migraciones", "deploy-check"}


class TestOrdenDeLosPasos:
    """La parte que estaba rota."""

    @pytest.mark.parametrize("nombre", ["test", "migraciones", "deploy-check"])
    def test_levantar_postgres_ocurre_una_vez(self, workflow: dict[str, Any], nombre: str) -> None:
        pasos = _pasos(workflow["jobs"][nombre])
        setups = [i for i, paso in enumerate(pasos) if _es_setup(paso)]
        assert len(setups) == 1, (
            f"el job {nombre} levanta PostgreSQL {len(setups)} veces; se esperaba 1"
        )

    @pytest.mark.parametrize("nombre", ["test", "migraciones", "deploy-check"])
    def test_el_teardown_es_el_ultimo_paso(self, workflow: dict[str, Any], nombre: str) -> None:
        """El teardown va **al final**. Este es el test que habria atrapado el bug.

        `if: always()` hace que el paso corra aunque algo anterior falle, y por eso
        tiene que ser el ultimo: si va antes, no limpia nada y encima se lleva por
        delante la base que el resto del job necesita.
        """
        pasos = _pasos(workflow["jobs"][nombre])
        indices = [i for i, paso in enumerate(pasos) if _es_teardown(paso)]
        assert indices, f"el job {nombre} nunca baja el contenedor"
        assert indices == [len(pasos) - 1], (
            f"el job {nombre} baja el contenedor en el paso {indices[0]} de "
            f"{len(pasos) - 1} (0-based). Tiene que ser el ultimo: bajarlo antes deja "
            "sin base a los pasos que vienen despues."
        )

    def test_el_teardown_corre_siempre(self, workflow: dict[str, Any]) -> None:
        """`if: always()`: el paso tiene que correr tambien cuando un test falla.

        Es justo cuando mas importa no dejar un contenedor colgado. Y `if: always()`
        tiene que ir **antes** de que el paso tenga cuerpo, porque despues del teardown
        no queda nada que limpiar.
        """
        for nombre, job in workflow["jobs"].items():
            for paso in _pasos(job):
                if _es_teardown(paso):
                    assert paso.get("if") == "always()", (
                        f"el teardown de {nombre} no tiene `if: always()`, asi que no "
                        "corre cuando el job falla y el contenedor queda colgado"
                    )

    def test_el_job_de_tests_migra_antes_de_correr_pytest(self, workflow: dict[str, Any]) -> None:
        """El orden de `test`: levantar -> verificar -> migrar -> tests -> teardown.

        Sin migrar, la base esta vacia y casi todo el suite falla con errores de
        tabla inexistente, que es un mensaje que no dice "falta `alembic upgrade`".
        """
        pasos = _pasos(workflow["jobs"]["test"])
        posiciones = {
            "setup": next((i for i, p in enumerate(pasos) if _es_setup(p)), -1),
            "migrar": next(
                (i for i, p in enumerate(pasos) if "alembic upgrade head" in str(p.get("run", ""))),
                -1,
            ),
            "pytest": next(
                (i for i, p in enumerate(pasos) if "python -m pytest" in str(p.get("run", ""))), -1
            ),
            "teardown": next((i for i, p in enumerate(pasos) if _es_teardown(p)), -1),
        }
        assert -1 not in posiciones.values(), f"falta un paso en el job test: {posiciones}"
        orden = [
            posiciones["setup"],
            posiciones["migrar"],
            posiciones["pytest"],
            posiciones["teardown"],
        ]
        assert orden == sorted(orden), (
            f"el job test hace las cosas en el orden equivocado: {posiciones}"
        )


class TestElJobDeMigraciones:
    def test_no_usa_downgrade_base(self, workflow: dict[str, Any]) -> None:
        """Baja hasta el piso, no hasta `base`.

        `0003` es un piso de reversibilidad: agregar valores de enum no tiene vuelta
        con asyncpg. Un `downgrade base` en el CI falla siempre, y un job que falla
        siempre entrena a mirar el job en rojo sin leerlo.
        """
        pasos = _pasos(workflow["jobs"]["migraciones"])
        lowers = [
            linea
            for paso in pasos
            for linea in str(paso.get("run", "")).splitlines()
            if "alembic downgrade" in linea and not linea.strip().startswith("#")
        ]
        assert lowers, "el job de migraciones no prueba ningun downgrade"
        bajas = [linea for linea in lowers if "downgrade base" in linea]
        # Se permite mencionarlo solo si es para comprobar que **falla**.
        for linea in bajas:
            paso = next(p for p in pasos if linea in str(p.get("run", "")))
            contexto = str(paso.get("run", ""))
            assert "deberia ser un piso" in contexto, (
                f"`{linea.strip()}` aparece sin comprobar que falla:\n{contexto}"
            )

    def test_verifica_que_el_piso_falla_sin_mover_la_base(self, workflow: dict[str, Any]) -> None:
        """El piso se prueba, no se asume.

        Sin este paso, `0003` podria dejar de ser un piso -- osea, pasar a deshacerse
        sin avisar -- y nadie se enteraria. Lo que se afirma es que sigue en la
        revision en la que estaba, porque un `CommandError` que se levanta a medias
        deja un enum al que ya se le saco un valor.
        """
        pasos = _pasos(workflow["jobs"]["migraciones"])
        codigos = "\n".join(str(p.get("run", "")) for p in pasos)
        assert "codigo" in codigos, "el job no captura el codigo de salida del downgrade"
        assert "0003_auth_roles" in codigos, "el job no comprueba que la base quedo en 0003"
        assert "0002_rls_triggers_grants" in codigos, "el job no menciona el piso"


class TestLosComentariosNoMienten:
    def test_no_se_afirma_que_volumes_no_existe(self, workflow: dict[str, Any]) -> None:
        """El comentario sobre `services` y `volumes` decia algo falso.

        Decia que los contenedores de servicio de GitHub no aceptan `volumes` y que
        el workflow entero fallaba en la validacion. Es falso: `volumes` esta en
        `definitions/jobContainer` del schema real, y el workflow original -- que lo
        usaba -- pasaba la validacion.

        Un comentario que afirma algo falso y verificado como falso es peor que sin
        comentario: el proximo que lo lea justifica una decision por una razon
        inventada, y no tiene forma de notar que el razonamiento esta roto.
        """
        texto = WORKFLOW.read_text(encoding="utf-8")
        afirmaciones_falsas = [
            "no aceptan `volumes`",
            "la clave no existe en su esquema",
            "falla en la validacion",
        ]
        presentes = [a for a in afirmaciones_falsas if a in texto]
        assert not presentes, (
            f"el workflow sigue afirmando {presentes}, que es falso: `volumes` si es "
            "una clave valida de `jobContainer`"
        )
