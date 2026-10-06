"""Access token: emision, validacion y, sobre todo, que se rechaza.

El foco de este archivo son los rechazos. Un token que se emite bien es codigo de cinco
lineas que ya exercise el login; lo que hay que demostrar es que **todo lo demas** no
pasa, porque cada rechazo cierra una forma de que un atacante fabrique o reutilice una
identidad.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from app.core.config import get_settings
from app.core.security import reset_password_hash_cache
from app.models.enums import BusinessUserRole
from app.modules.auth.scopes import Scope, as_strings, scopes_for_business_role
from app.modules.auth.tokens import (
    CLAIM_TYPE,
    TYPE_ACCESS,
    AccessToken,
    Principal,
    TokenError,
    create_access_token,
    decode_access_token,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _argon2_rapido() -> Iterator[None]:
    """`create_access_token` no hashea, pero el import de `tokens` lo carga."""
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


@pytest.fixture
def usuario() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def negocio() -> uuid.UUID:
    return uuid.uuid4()


def _emitir(usuario: uuid.UUID, negocio: uuid.UUID, **kwargs: Any) -> AccessToken:
    return create_access_token(
        user_id=usuario,
        business_id=negocio,
        role=BusinessUserRole.ADMIN,
        scopes=as_strings(scopes_for_business_role(BusinessUserRole.ADMIN)),
        **kwargs,
    )


def _sin_verificar(token: str | AccessToken) -> dict[str, object]:
    """El payload sin validar la firma. Para armar tokens rotos a proposito.

    Acepta el dataclass o el string. El dataclass es lo que devuelve `create_access_token`
    y es lo que se tiene a mano al escribir el test, asi que no hace falta acordarse de
    desempaquetar `.token` en cada llamada.
    """
    crudo = token.token if isinstance(token, AccessToken) else token
    _, payload, _ = crudo.split(".")
    relleno = payload + "=" * (-len(payload) % 4)
    return dict(json.loads(base64.urlsafe_b64decode(relleno)))


def _reemitir(token: str | AccessToken, cambios: dict[str, object]) -> str:
    """Vuelve a firmar un payload modificado con la clave real de la app."""
    payload = _sin_verificar(token)
    payload.update(cambios)
    settings = get_settings()
    return jwt.encode(payload, settings.jwt_secret_key.get_secret_value(), algorithm="HS256")


class TestClaims:
    def test_los_claims_del_10_2_estan_todos(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        payload = _sin_verificar(_emitir(usuario, negocio))
        assert payload["sub"] == str(usuario)
        assert payload["tid"] == str(negocio)
        assert payload["role"] == "admin"
        scopes = payload["scopes"]
        assert isinstance(scopes, list)
        assert scopes == sorted(scopes), "as_strings ordena, y el token tiene que ser estable"
        assert uuid.UUID(str(payload["jti"]))
        assert isinstance(payload["iat"], int)
        exp = payload["exp"]
        assert isinstance(exp, int)
        assert payload[CLAIM_TYPE] == TYPE_ACCESS

    def test_expira_en_15_minutos_por_defecto(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        momento = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)
        emitido = _emitir(usuario, negocio, issued_at=momento)
        assert emitido.expires_at == momento + dt.timedelta(minutes=15)
        payload = _sin_verificar(emitido)
        iat, exp = payload["iat"], payload["exp"]
        assert isinstance(iat, int) and isinstance(exp, int)
        assert exp - iat == 15 * 60

    def test_cada_token_tiene_un_jti_distinto(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        """`jti` unico por token: es lo que permite revocar uno concreto.

        Sin `jti` distinto, dos tokens del mismo usuario en el mismo segundo serian
        indistinguibles y una revocacion por `jti` podria levarse por delante el token
        equivocado.
        """
        jtis = {_emitir(usuario, negocio).jti for _ in range(50)}
        assert len(jtis) == 50

    def test_es_hs256(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        header = _emitir(usuario, negocio).token.split(".")[0]
        relleno = header + "=" * (-len(header) % 4)
        assert json.loads(base64.urlsafe_b64decode(relleno))["alg"] == "HS256"


class TestIdaYVuelta:
    def test_el_principal_sale_del_token(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        emitido = _emitir(usuario, negocio)
        principal = decode_access_token(emitido.token)
        assert principal.user_id == usuario
        assert principal.business_id == negocio
        assert principal.role == "admin"
        assert principal.jti == emitido.jti
        assert Scope.BUSINESS_CONFIG_WRITE in principal.scopes
        assert principal.is_platform is False

    def test_el_business_id_solo_esta_en_el_token(self, usuario: uuid.UUID) -> None:
        """El tenant no se acepta de ningun otro lado (ADR-0010).

        `Principal` tiene cinco campos y el `business_id` sale de `tid`. No hay
        constructor alternativo ni parametro que lo sobreescriba: la regla del ADR-0010
        es cumplible porque no hay otro camino para cumplirla mal.
        """
        campos = set(Principal.__dataclass_fields__)
        assert campos == {"user_id", "business_id", "role", "scopes", "jti", "is_platform"}

    def test_acepta_un_token_emitido_hace_14_minutos(
        self, usuario: uuid.UUID, negocio: uuid.UUID
    ) -> None:
        momento = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=14)
        assert decode_access_token(_emitir(usuario, negocio, issued_at=momento).token)

    def test_rechaza_un_token_emitido_hace_16_minutos(
        self, usuario: uuid.UUID, negocio: uuid.UUID
    ) -> None:
        momento = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=16)
        with pytest.raises(TokenError):
            decode_access_token(_emitir(usuario, negocio, issued_at=momento).token)


class TestRechazosDeFirma:
    def test_otra_clave(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        otro = jwt.encode(
            {
                "sub": str(usuario),
                "tid": str(negocio),
                "exp": int(dt.datetime.now(dt.UTC).timestamp()) + 900,
            },
            "otra-clave-que-no-es-la-nuestra-pero-si-de-32",
            algorithm="HS256",
        )
        with pytest.raises(TokenError):
            decode_access_token(otro)

    def test_firma_alterada(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        token = _emitir(usuario, negocio).token
        head, payload, firma = token.split(".")
        firma_alterada = ("A" if firma[0] != "A" else "B") + firma[1:]
        with pytest.raises(TokenError):
            decode_access_token(f"{head}.{payload}.{firma_alterada}")

    def test_alg_none(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        """`alg: none` con la firma vacia.

        Es el ataque clasico de JWT: si el servidor acepta el token sin verificar la
        firma, cualquiera puede escribir su propio `sub` y `tid`. PyJWT >= 2 lo
        rechaza, y el test existe para que un downgrade de version no lo reintroduzca.
        """
        payload = _sin_verificar(_emitir(usuario, negocio))
        sin_firma = jwt.encode(payload, key="", algorithm=None)
        with pytest.raises(TokenError):
            decode_access_token(sin_firma)

    def test_alg_none_escrito_a_mano(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        """`alg: none` armado a mano, sin pasar por la libreria.

        `jwt.encode(..., algorithm=None)` podria negarse a generarlo. Un atacante no
        usa la libreria, asi que el header se escribe a mano y hay que probar el caso
        de verdad.
        """
        payload = _sin_verificar(_emitir(usuario, negocio))
        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
            .decode()
            .rstrip("=")
        )
        cuerpo = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        with pytest.raises(TokenError):
            decode_access_token(f"{header}.{cuerpo}.")

    def test_confusion_de_algoritmo_con_clave_publica(
        self, usuario: uuid.UUID, negocio: uuid.UUID
    ) -> None:
        """Firmar con una clave publica RSA como si fuera un secreto HMAC.

        El ataque: si la app acepta mas de un algoritmo, el atacante elige `HS256` y
        firma el token usando la clave publica RSA **como secreto HMAC**. La app
        verifica con la misma clave publica y la firma cuadra, sin que el atacante
        sepa ningun secreto.

        Hay dos barreras y este test mide donde esta cada una:

        1. `algorithms=[...]` en el decode. Sin ella, PyJWT usaria el `alg` del header,
           que lo elige quien emite el token.
        2. `prepare_key` de HMAC rechaza una clave con forma de PEM. Sin esto, la
           barrera 1 es la unica, y la barrera 1 depende de que el algoritmo sea
           siempre el mismo.

        Lo que se comprueba aca es que la barrera 2 existe y que esta donde tiene que
        estar: **antes** de que exista el token. El intento de firmarlo falla con
        `InvalidKeyError`, y no hay token forjado que este codigo pueda llegar a
        verificar. Por eso `pyproject.toml` declara `pyjwt[crypto]` y no `pyjwt`: sin
        el extra no hay deteccion de PEM y la segunda barrera no existe.
        """
        pem = (
            "-----BEGIN PUBLIC KEY-----\n"
            "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtleast32bytes\n"
            "-----END PUBLIC KEY-----\n"
        )
        payload = _sin_verificar(_emitir(usuario, negocio))

        # La barrera 2: el intento de firma con una clave publica como secreto.
        with pytest.raises(jwt.exceptions.InvalidKeyError, match="HMAC secret"):
            jwt.encode(payload, key=pem, algorithm="HS256")

    def test_la_clave_publica_tampoco_pasa_el_decode(
        self, usuario: uuid.UUID, negocio: uuid.UUID
    ) -> None:
        """La segunda barrera tambien esta del lado del que verifica.

        El test anterior comprueba que no se pueda *firmar*. Este comprueba que
        tampoco se acepte un token ya existente cuya firma se haya hecho por otra via.
        Es la direccion que importa en un despliegue real: el atacante no usa nuestra
        libreria.
        """
        payload = _sin_verificar(_emitir(usuario, negocio))
        # Firma a mano, sin la validacion de PyJWT: es lo que haria un atacante.
        head = (
            base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
            .decode()
            .rstrip("=")
        )
        cuerpo = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        firma = hmac.new(
            b"-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkq\n-----END PUBLIC KEY-----\n",
            f"{head}.{cuerpo}".encode(),
            hashlib.sha256,
        ).digest()
        firma_b64 = base64.urlsafe_b64encode(firma).decode().rstrip("=")

        with pytest.raises(TokenError):
            decode_access_token(f"{head}.{cuerpo}.{firma_b64}")

    def test_otro_algoritmo_firmado_con_la_clave_real(
        self, usuario: uuid.UUID, negocio: uuid.UUID
    ) -> None:
        """Aunque la firma sea valida, `HS512` no es lo que esta app emite."""
        payload = _sin_verificar(_emitir(usuario, negocio))
        otro = jwt.encode(
            payload, get_settings().jwt_secret_key.get_secret_value(), algorithm="HS512"
        )
        with pytest.raises(TokenError):
            decode_access_token(otro)


class TestRechazosDeClaims:
    @pytest.mark.parametrize(
        "claim", ["sub", "tid", "role", "scopes", "jti", "iat", "exp", CLAIM_TYPE, "iss", "aud"]
    )
    def test_falta_un_claim_obligatorio(
        self, claim: str, usuario: uuid.UUID, negocio: uuid.UUID
    ) -> None:
        """Sin `exp` el token no expira nunca. Sin `sub` no tiene titular.

        Cada claim de `REQUIRED_CLAIMS` tiene que ver: se prueba uno por uno porque un
        `require` mal escrito es facil de no notar â€” PyJWT no avisa de lo que no le
        pidio.
        """
        payload = _sin_verificar(_emitir(usuario, negocio))
        del payload[claim]
        settings = get_settings()
        forjado = jwt.encode(payload, settings.jwt_secret_key.get_secret_value(), algorithm="HS256")
        with pytest.raises(TokenError):
            decode_access_token(forjado)

    def test_typ_distinto(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        """`typ` tiene que ser `access`.

        Hoy el refresh es opaco y no hay colision. El claim existe para que el proximo
        tipo de token no tenga que adivinarse.
        """
        otro = _reemitir(_emitir(usuario, negocio).token, {CLAIM_TYPE: "refresh"})
        with pytest.raises(TokenError):
            decode_access_token(otro)

    def test_issuer_distinto(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        """Una firma valida no dice que el token sea nuestro.

        Sin `iss`, un token firmado con la misma clave por otra aplicacion del mismo
        despliegue valida aca. Con dos servicios en un repo, esa es una forma de
        confused deputy bastante real.
        """
        otro = _reemitir(_emitir(usuario, negocio).token, {"iss": "otro-servicio"})
        with pytest.raises(TokenError):
            decode_access_token(otro)

    def test_audience_distinto(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        otro = _reemitir(_emitir(usuario, negocio).token, {"aud": "otra-api"})
        with pytest.raises(TokenError):
            decode_access_token(otro)

    @pytest.mark.parametrize(
        "malo", ["no-es-uuid", "", "123", "00000000-0000-0000-0000-00000000000"]
    )
    def test_un_uuid_malo_se_rechaza(
        self, malo: str, usuario: uuid.UUID, negocio: uuid.UUID
    ) -> None:
        """Un `tid` invalido no puede llegar a la politica de RLS.

        Un `business_id` con formato raro que llega a `set_config('
        app.current_business_id', ...)` deja la politica comparando contra una cadena
        que nunca va a coincidir: RLS activa y sin efecto, que es peor que RLS apagada
        porque aparenta funcionar. Se rechaza en el borde.
        """
        forjado = _reemitir(_emitir(usuario, negocio).token, {"tid": malo})
        with pytest.raises(TokenError):
            decode_access_token(forjado)

    def test_un_sub_malo_se_rechaza(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        forjado = _reemitir(_emitir(usuario, negocio).token, {"sub": "no-soy-yo"})
        with pytest.raises(TokenError):
            decode_access_token(forjado)

    @pytest.mark.parametrize("malo", ["no-es-lista", "un-string", 42, {"a": 1}])
    def test_scopes_con_forma_equivocada_se_rechazan(
        self, malo: object, usuario: uuid.UUID, negocio: uuid.UUID
    ) -> None:
        """`scopes` tiene que ser una lista de strings.

        Si fuera un string suelto, `frozenset("bookings:read")` seria un conjunto de
        caracteres y `require_scopes` compararia contra letras sueltas: denegaria todo,
        que es el fallo seguro. Si fuera un dict, `set()` daria las claves. El chequeo
        explicito evita depender de que un error de forma produzca el resultado
        correcto.
        """
        forjado = _reemitir(_emitir(usuario, negocio).token, {"scopes": malo})
        with pytest.raises(TokenError):
            decode_access_token(forjado)

    def test_un_role_vacio_se_rechaza(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        forjado = _reemitir(_emitir(usuario, negocio).token, {"role": ""})
        with pytest.raises(TokenError):
            decode_access_token(forjado)


class TestElErrorNoFiltrar:
    def test_el_mensaje_no_dice_por_que_fallo(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        """Todos los rechazos dicen lo mismo.

        Distinguir "expirado" de "firma invalida" de "le falta `sub`" le da a un
        atacante un oraculo para distinguir tokens validos de invalidos. El detalle va
        al log, que es donde sirve; la respuesta al cliente es una sola.
        """
        valido = _emitir(usuario, negocio).token
        expirado = _emitir(
            usuario, negocio, issued_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
        ).token
        sin_sub = _reemitir(valido, {"sub": "x"})
        messages = set()
        for token in (valido.replace("a", "b", 1), expirado, sin_sub):
            try:
                decode_access_token(token)
            except TokenError as exc:
                messages.add(str(exc))
        assert messages == {"access token invalido"}, messages

    def test_el_error_tiene_causa_para_el_log(self, usuario: uuid.UUID, negocio: uuid.UUID) -> None:
        """La causa se encadena, no se borra.

        `raise ... from exc` deja la excepcion de PyJWT en `__cause__`, que es lo que
        el log va a mostrar. El mensaje para el cliente sigue siendo opaco, pero el
        diagnostico no se pierde.
        """
        otro = jwt.encode({"sub": "x"}, "otra-clave-de-prueba-de-32-bytes-larga", algorithm="HS256")
        with pytest.raises(TokenError) as excinfo:
            decode_access_token(otro)
        assert excinfo.value.__cause__ is not None
        assert isinstance(excinfo.value.__cause__, jwt.PyJWTError)
