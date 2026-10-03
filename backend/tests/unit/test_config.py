"""La configuracion no puede arrancar en un estado que va a fallar en produccion.

La mayoria de estos tests parecen obvios: un `Field(default=...)` esta bien o esta
mal, un validador acepta o rechaza. La razon de que sean tests y no una lectura
cuidadosa es la §48: un default mal puesto no falla en el despliegue, falla mas
tarde y en otro lugar. `DEFAULT_TIMEZONE=America/Santa_Fe` no rompe el arranque;
rompe el dia que un negocio nuevo no muestra disponibilidad, y para entonces
nadie recuerda el deploy.

Por eso los casos que mas pesan son los negativos: que la app **no** arranque con
`DEBUG=true` en produccion, con un secreto de ejemplo, o con una clave Fernet que
no descodifica a 32 bytes.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any, ClassVar

import pytest
from app.core.config import PLACEHOLDER_SECRETS, Settings
from pydantic import ValidationError

#: Fernet real de 32 bytes. Se genera una vez y queda fijo: si el test generara una
#: clave por corrida, fallaria por rotura de base, no por lo que verifica.
CLAVE_FERNET = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()

#: Lo minimo para que `Settings()` no falle por campos obligatorios. Los tests que
#: verifican una regla concreta arrancan de aca y solo se desvian en lo que proban.
BASE: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://tempus_app:pw@localhost:5432/tempus",
    "database_migration_url": "postgresql+asyncpg://tempus_owner:pw@localhost:5432/tempus",
    "jwt_secret_key": "x" * 40,
    "encryption_key": CLAVE_FERNET,
    "scheduler_tick_secret": "tick-" + "y" * 40,
}


def _leer_env(texto: str) -> dict[str, str]:
    """Variables de un `.env`, sin depender de python-dotenv.

    Solo hace falta lo que el archivo tiene: `CLAVE=valor`, `#` de comentario y
    lineas en blanco. Se parsea aca en vez de usar `dotenv` porque este test
    tambien tiene que servir si el paquete no esta instalado, y porque una lectura
    casera puede(bytes()) fallar de forma obvia, que es justo lo que un test de
    documentacion necesita.
    """
    variables: dict[str, str] = {}
    for linea in texto.splitlines():
        limpia = linea.strip()
        if not limpia or limpia.startswith("#") or "=" not in limpia:
            continue
        clave, _, valor = limpia.partition("=")
        variables[clave.strip()] = valor.strip()
    return variables


def _settings(**overrides: Any) -> Settings:
    """Construye `Settings` sin leer el `.env` del repositorio.

    `_env_file=None` es lo que hace estos tests deterministas: sin eso, el `.env`
    de la maquina pisa los defaults y el test pasa o falla segun quien lo corra.
    """
    datos = {**BASE, **overrides}
    return Settings(_env_file=None, **datos)


def _produccion_valida(**overrides: Any) -> dict[str, Any]:
    """Overrides minimos para que la app se este por `environment=production`.

    Se separa en su propia funcion porque casi todos los tests de produccion
    repiten la misma forma de "endurecer todo y cambiar una sola cosa". La version
    buena de este patron es: si el endurecimiento crece, se agrega en un lugar, no
    en veinte tests.
    """
    return {
        "environment": "production",
        "debug": False,
        "cookie_secure": True,
        "log_client_data": False,
        "log_format": "json",
        **overrides,
    }


class TestListasPorComa:
    """Los cuatro campos de lista aceptan CSV y JSON.

    El CSV es lo que se escribe a mano en un `.env`; el JSON es lo que produce
    `docker compose`. Admitir solo uno de los dos obliga a documentar una excepcion
    por entorno, y la excepcion termina olvidada.
    """

    @pytest.mark.parametrize(
        "campo",
        ["cors_origins", "job_queues", "media_allowed_content_types", "log_redact_keys"],
    )
    def test_csv_separte_por_comas(self, campo: str) -> None:
        config = _settings(**{campo: "a, b ,c"})
        assert getattr(config, campo) == ["a", "b", "c"]

    @pytest.mark.parametrize(
        "campo",
        ["cors_origins", "job_queues", "media_allowed_content_types", "log_redact_keys"],
    )
    def test_json_tambien_se_acepta(self, campo: str) -> None:
        config = _settings(**{campo: '["a", "b"]'})
        assert getattr(config, campo) == ["a", "b"]

    def test_csv_vacio_es_lista_vacia_y_no_error(self) -> None:
        """Un `CORS_ORIGINS=` en blanco no puede romper el arranque.

        Es el caso de un `.env` copiado y editado a medias, y es comun porque el
        valor por defecto sensato de una lista vacia es "no restricting".
        """
        assert _settings(cors_origins="").cors_origins == []
        assert _settings(cors_origins="   ").cors_origins == []

    def test_una_lista_ya_estructurada_no_se_toca(self) -> None:
        """Si el valor ya es una lista de Python, no se reinterpretan las comas.

        Pisar un valor ya parseado con el CSV rompe los tests que pasan listas por
        parametro, y el sintoma es un `"['a,b']"` que nadie sabe de donde salio.
        """
        assert _settings(cors_origins=["https://a", "https://b"]).cors_origins == [
            "https://a",
            "https://b",
        ]

    def test_una_url_con_coma_dentro_separte(self) -> None:
        """Documenta el limite conocido del formato CSV, para que no sorprenda.

        Una query string con comas (`?ids=1,2`) se parte en dos entradas. Es el
        precio de admitir CSV, y no importa para CORS ni para colas: no hay ninguna
        de estas cuatro listas donde una coma sea un caracter legitimo. Si alguna
        vez lo hay, este test es el que avisa.
        """
        config = _settings(cors_origins="https://a.test/?ids=1,2")
        assert config.cors_origins == ["https://a.test/?ids=1", "2"]


class TestNormalizaciones:
    def test_log_level_se_normaliza_a_mayusculas(self) -> None:
        assert _settings(log_level="debug").log_level == "DEBUG"

    def test_samesite_se_normaliza_a_minusculas(self) -> None:
        assert _settings(cookie_samesite="STRICT").cookie_samesite == "strict"

    @pytest.mark.parametrize("valor", ["none", "None", "NONE"])
    def test_samesite_none_es_valido(self, valor: str) -> None:
        """`None` es necesario para el cross-site de las cookies de refresh."""
        assert _settings(cookie_samesite=valor).cookie_samesite == "none"

    @pytest.mark.parametrize("valor", ["sin_samesite", "", "lax2"])
    def test_samesite_invalido_se_rechaza(self, valor: str) -> None:
        with pytest.raises(ValidationError, match="COOKIE_SAMESITE"):
            _settings(cookie_samesite=valor)


class TestTimezone:
    def test_una_zona_iana_valida_pasa(self) -> None:
        assert _settings(default_timezone="America/Argentina/Buenos_Aires")

    @pytest.mark.parametrize(
        "valor",
        ["America/Santa_Fe", "Argentina/Buenos_Aires", "UTC+3", "gmt-5", "Europe/Nowhere"],
    )
    def test_una_zona_inexistente_falla_al_arrancar(self, valor: str) -> None:
        """El motivo de que este test exista.

        Con `America/Santa_Fe` la app levanta sin error y falla al mostrar
        disponibilidad de un negocio nuevo. Detenerse en el arranque es lo unico
        que convierte un error de deploy en un error visible en el deploy.
        """
        with pytest.raises(ValidationError, match="timezone IANA inexistente"):
            _settings(default_timezone=valor)


class TestClaveFernet:
    def test_una_clave_de_32_bytes_pasa(self) -> None:
        assert _settings(encryption_key=CLAVE_FERNET)

    def test_una_clave_del_tamano_equivocado_falla(self) -> None:
        corta = base64.urlsafe_b64encode(b"corto").decode()
        with pytest.raises(ValidationError, match="debe decodificar a 32 bytes"):
            _settings(encryption_key=corta)

    def test_una_clave_que_no_es_base64_falla(self) -> None:
        with pytest.raises(ValidationError, match="no es base64 url-safe"):
            _settings(encryption_key="no soy base64!!")

    def test_una_clave_vacia_se_permite_en_local(self) -> None:
        """Vacia significa "Meta todavia no configurado", no "clave rota".

        La plataforma arranca sin credenciales de Meta y las carga cuando el negocio
        las conecta. Reclamar una clave al arrancar haria imposible correr el
        proyecto sin una cuenta de Meta.
        """
        assert _settings(encryption_key="").encryption_key.get_secret_value() == ""


class TestReglasDeProduccion:
    """Las reglas que solo se pueden comprobar con todos los campos cargados."""

    def test_en_local_no_hace_falta_endurecer_nada(self) -> None:
        """El caso base: desarrollo tolera `debug`, cookies sin `secure` y console."""
        config = _settings(environment="local", debug=True, cookie_secure=False)
        assert config.debug is True

    def test_debug_prohibido_en_produccion(self) -> None:
        with pytest.raises(ValidationError, match="DEBUG no puede ser true"):
            _settings(environment="production", debug=True, cookie_secure=True)

    def test_cookies_inseguras_prohibidas_en_produccion(self) -> None:
        with pytest.raises(ValidationError, match="COOKIE_SECURE"):
            _settings(environment="production", debug=False, cookie_secure=False)

    def test_log_de_clientes_prohibido_en_produccion(self) -> None:
        """`log_client_data` mete datos de clientes en los logs.

        Con el phone y el nombre de un cliente, un dump de logs es un dump de datos
        personales. Que sea opt-in fuera de produccion y no negociable adentro.
        """
        with pytest.raises(ValidationError, match="LOG_CLIENT_DATA"):
            _settings(**_produccion_valida(log_client_data=True))

    def test_logs_console_prohibidos_en_produccion(self) -> None:
        """Un log de texto no estructurado no se puede consultar ni redactar bien."""
        with pytest.raises(ValidationError, match="LOG_FORMAT"):
            _settings(**_produccion_valida(log_format="console"))

    @pytest.mark.parametrize("campo", ["jwt_secret_key", "scheduler_tick_secret"])
    def test_un_secreto_de_ejemplo_no_alcanza_para_produccion(self, campo: str) -> None:
        """El valor de `.env.example` copiado a produccion es el fallo clasico.

        Falla con un mensaje que dice como arreglarlo, y no con un 401 en el primer
        request autenticado tres dias despues del deploy.
        """
        valor = "cambiame_en_produccion_openssl_rand_hex_32"
        assert valor in PLACEHOLDER_SECRETS, "el placeholder del test debe existir en la app"
        with pytest.raises(ValidationError, match="valor de ejemplo"):
            _settings(**_produccion_valida(**{campo: valor}))

    def test_el_placeholder_de_enryption_key_lo_atrapa_el_validador_de_fernet(
        self,
    ) -> None:
        """Para `ENCRYPTION_KEY` hay una barrera anterior, mas fuerte.

        El placeholder `cambiame_openssl_rand_base64_32` no es base64 valido, asi
        que el `field_validator` de Fernet lo rechaza **antes** de que corra el
        `model_validator` que busca placeholders. El error que ve el operador es
        "debe decodificar a 32 bytes", no "cambiame esto", y es mejor: dice el
        problema real y no que el valor se parece a un ejemplo.

        Este test existe para dejar escrito que el orden es ese. Si alguien saca el
        validador de Fernet esperando que el chequeo de placeholders cubra el hueco,
        este test le avisa que quedaria sin barrera.
        """
        with pytest.raises(ValidationError, match=r"base64|32 bytes"):
            _settings(
                **_produccion_valida(
                    encryption_key="cambiame_openssl_rand_base64_32",
                )
            )

    def test_secreto_corto_rechazado_en_produccion(self) -> None:
        """Una clave de firma corta se rechaza **siempre**, no solo en produccion.

        El chequeo vivia antes en el `model_validator` de produccion, y por eso una
        clave de 16 bytes pasaba en desarrollo y rompia en produccion. Ahora es un
        `field_validator`, como el de Fernet, y el error dice cuantos bytes tiene y
        como generar una.
        """
        with pytest.raises(ValidationError, match="necesita al menos 32"):
            _settings(**_produccion_valida(jwt_secret_key="corto"))

    def test_secreto_corto_tambien_se_rechaza_en_desarrollo(self) -> None:
        """El mismo rechazo fuera de produccion.

        No es rigidez: una clave de 16 bytes firma tokens que se pueden falsificar con
        mas voluntad que la necesaria, y descubrirlo en el `log` de produccion, a las
        3am, es la forma mas cara de enterarse.
        """
        with pytest.raises(ValidationError, match="necesita al menos 32"):
            _settings(jwt_secret_key="corto")

    def test_la_longitud_se_mide_en_bytes_no_en_caracteres(self) -> None:
        """ "32 caracteres" y "32 bytes" no son lo mismo.

        Una clave de 32 caracteres con tildes o emojis mide mas de 32 bytes, y al reves
        tambien. HS256 usa la longitud en bytes, asi que un `len()` sobre el string
        acepta claves que la libreria despues considera debiles y de las que PyJWT
        avisa con `InsecureKeyLengthWarning`.
        """
        con_tildes = "ñ" * 16  # 16 caracteres, 32 bytes en UTF-8
        assert len(con_tildes) < 32
        _settings(jwt_secret_key=con_tildes)  # 32 bytes: valido
        with pytest.raises(ValidationError, match="necesita al menos 32"):
            _settings(jwt_secret_key="ñ" * 10)  # 10 caracteres, 20 bytes: no alcanza

    def test_tick_secret_no_puede_ser_el_jwt_secret(self) -> None:
        """El endpoint de tick es interno pero expuesto por red.

        Que reutilice la clave de los tokens es el atajo que hace que un endpoint
        de scheduling quede autenticado con el mismo secreto que todos los
        usuarios. Son dos dominios de confianza distintos.
        """
        compartido = "z" * 40
        with pytest.raises(ValidationError, match="no puede ser igual a JWT_SECRET_KEY"):
            _settings(
                **_produccion_valida(jwt_secret_key=compartido, scheduler_tick_secret=compartido)
            )

    def test_produccion_valida_pasa_con_todo_endurecido(self) -> None:
        """El caso positivo: produccion bien configurada arranca.

        Sin este test, un validador nuevo demasiado estricto se descubre en el
        despliegue en vez de en la suite.
        """
        config = _settings(**_produccion_valida())
        assert config.environment.is_production


class TestSecretosNoSeImprimen:
    def test_un_secreto_aparece_como_mascara_en_el_repr(self) -> None:
        """`SecretStr` existe para esto: que un traceback no vuelque la clave.

        El test falla si alguien cambia el tipo de `jwt_secret_key` a `str`, que es
        un cambio de una linea y una fuga de credenciales en el primer log de error.
        """
        texto = repr(_settings())
        assert "x" * 40 not in texto, "el secret de JWT aparece en claro en el repr"
        assert "**********" in texto or "********" in texto

    def test_el_valor_real_sigue_disponible_por_atexto(self) -> None:
        config = _settings()
        assert config.jwt_secret_key.get_secret_value() == "x" * 40


class TestRangos:
    """Los limites que PostgreSQL y la arquitectura imponen, validados al inicio."""

    @pytest.mark.parametrize("valor", [0, -1])
    def test_db_pool_size_no_puede_ser_cero(self, valor: int) -> None:
        with pytest.raises(ValidationError):
            _settings(db_pool_size=valor)

    def test_intervalo_de_slot_dentro_de_los_limites(self) -> None:
        """5 a 120 minutos.

        Menos de 5 produce una grilla ilegible; mas de 120 produce un horario con
        huecos que ninguna agenda real usa.
        """
        assert _settings(default_slot_interval_minutes=15)
        with pytest.raises(ValidationError):
            _settings(default_slot_interval_minutes=2)
        with pytest.raises(ValidationError):
            _settings(default_slot_interval_minutes=500)

    def test_moneda_de_tres_letras(self) -> None:
        assert _settings(default_currency="ARS").default_currency == "ARS"
        with pytest.raises(ValidationError):
            _settings(default_currency="ARS$")

    def test_tamanio_de_subida_con_piso(self) -> None:
        """El piso de 1 KB evita un limite tan bajo que rompa un video corto."""
        assert _settings(media_max_upload_bytes=5_242_880)
        with pytest.raises(ValidationError):
            _settings(media_max_upload_bytes=10)


class TestEnvExample:
    """`.env.example` no puede quedarse viejo en silencio.

    Un archivo de ejemplo desalineado del codigo es la forma mas comun de romper un
    despliegue: todo funciona en local porque el `.env` local esta bien, y el que
    arma un entorno nuevo copia un archivo que no menciona la variable nueva. El
    síntoma aparece en el despliegue, y como el default suele existir, aparece
    *funcionando mal* en vez de aparecer como error.
    """

    #: Variables que existen en `.env.example` sin ser campos de `Settings`: las
    #: lee el harness de tests y ninguna otra parte.
    SOLO_HARNESS: ClassVar[frozenset[str]] = frozenset({"TEST_DATABASE_URL"})

    def _env_example(self) -> dict[str, str]:
        ruta = Path(__file__).resolve().parents[3] / ".env.example"
        assert ruta.is_file(), f"no existe {ruta}"
        return _leer_env(ruta.read_text(encoding="utf-8"))

    def test_cubre_todas_las_variables_del_env_example(self) -> None:
        """Cada campo de `Settings` tiene su variable en `.env.example`.

        El nombre se deriva del campo, no de una lista escrita a mano: asi el test
        sigue siendo valido si alguien renombra un campo, que es justo cuando hace
        falta avisar.
        """
        declarados = set(self._env_example())
        esperados = {campo.upper() for campo in Settings.model_fields} | self.SOLO_HARNESS
        faltan = esperados - declarados
        assert not faltan, (
            f"campos de Settings sin documentar en .env.example: {sorted(faltan)}. "
            "Un despliegue nuevo arranca con el default y falla mas tarde."
        )

    def test_no_documenta_variables_que_no_existen(self) -> None:
        """Y al reves: sin variables fantasma.

        Una variable en `.env.example` que ya no existe en el codigo hace creer que
        se configura algo que no se configura. Es la direccion del error que mas
        confia.
        """
        declarados = set(self._env_example())
        reales = {campo.upper() for campo in Settings.model_fields} | self.SOLO_HARNESS
        sobran = declarados - reales
        assert not sobran, f"variables en .env.example que no existen en Settings: {sorted(sobran)}"

    def test_los_secretos_de_ejemplo_son_los_que_rechaza_produccion(self) -> None:
        """Los placeholders del archivo son los que la validación conoce.

        Si `.env.example` inventa un placeholder distinto del que
        `PLACEHOLDER_SECRETS` contiene, la protección de producción no dispara: el
        despliegue arranca con un secreto de ejemplo creyendo que es real.
        """
        ejemplo = self._env_example()
        for campo in ("jwt_secret_key", "encryption_key", "scheduler_tick_secret"):
            valor = ejemplo[campo.upper()]
            assert valor in PLACEHOLDER_SECRETS, (
                f"{campo.upper()} en .env.example es {valor!r}, que no esta en "
                "PLACEHOLDER_SECRETS. La validación de producción no lo va a "
                "rechazar."
            )

    def test_el_archivo_es_utf8_sin_mojibake(self) -> None:
        """Sin doble codificación.

        Un archivo que se guardó como latin-1 y se leyó como utf-8 queda lleno de
        `Ã¡` y `Â§`. No rompe el arranque porque pydantic no lee comentarios, asi
        que es del tipo de cosa que nadie reporta y que vuelve imposible leer el
        archivo.
        """
        texto = (Path(__file__).resolve().parents[3] / ".env.example").read_text(encoding="utf-8")
        mojibake = {"Ã¡": "á", "Ã©": "é", "Ã­": "í", "Ã³": "ó", "Ãº": "ú", "Ã±": "ñ", "Â§": "§"}
        for basura, correcta in mojibake.items():
            assert basura not in texto, f".env.example tiene '{basura}' donde va '{correcta}'"

    def test_las_credenciales_de_ejemplo_usan_los_roles_reales(self) -> None:
        """Las dos URLs usan los dos roles, y son distintos.

        `DATABASE_URL` con el rol de DDL es el error que anula la separacion de
        permisos de ARCHITECTURE.md §7: la app pasa a ser duena de las tablas, y
        `FORCE ROW LEVEL SECURITY` deja de protegerla. La cookie del rol equivocado
        no lo detecta nadie hasta que se audita.
        """
        ejemplo = self._env_example()
        app_url = re.sub(r"//[^@]+@", "//", ejemplo["DATABASE_URL"])
        migracion_url = re.sub(r"//[^@]+@", "//", ejemplo["DATABASE_MIGRATION_URL"])
        rol_app = re.search(r"//(?P<rol>[^:]+):", ejemplo["DATABASE_URL"])
        rol_ddl = re.search(r"//(?P<rol>[^:]+):", ejemplo["DATABASE_MIGRATION_URL"])
        assert rol_app and rol_ddl
        assert rol_app["rol"] != rol_ddl["rol"], (
            "DATABASE_URL y DATABASE_MIGRATION_URL usan el mismo rol: la app "
            "correria con permisos de DDL."
        )
        assert rol_app["rol"] == "tempus_app", (
            f"el rol de la app deberia ser tempus_app y es {rol_app['rol']}"
        )
        assert rol_ddl["rol"] == "tempus_owner", (
            f"el rol de migraciones deberia ser tempus_owner y es {rol_ddl['rol']}"
        )
        assert app_url.endswith("/tempus")
        assert migracion_url.endswith("/tempus")
        assert "asyncpg" in ejemplo["DATABASE_URL"]

    def test_cookie_path_y_api_base_usan_el_prefijo_real(self) -> None:
        """`COOKIE_PATH` tiene que ser una ruta que exista.

        La app sirve la API bajo `/api/v1`. Si esta variable queda en otra cosa, la
        cookie de refresh se envia en todas las peticiones y no solo en las de
        autenticación, que es justo lo que el campo dice evitar. Y no falla nada:
        la cookie se sigue enviando.
        """
        from app.main import create_app

        ejemplo = self._env_example()
        app = create_app(_settings(**BASE))
        assert app.docs_url is not None
        prefijo = app.docs_url.removesuffix("/docs")
        assert ejemplo["COOKIE_PATH"].startswith(prefijo), (
            f"COOKIE_PATH={ejemplo['COOKIE_PATH']} esta fuera del prefijo real {prefijo}"
        )
        assert ejemplo["VITE_API_BASE_URL"].endswith(prefijo), (
            f"VITE_API_BASE_URL={ejemplo['VITE_API_BASE_URL']} no termina en {prefijo}"
        )
