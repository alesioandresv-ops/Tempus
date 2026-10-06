"""Alta self-service de un negocio, de punta a punta contra Postgres real.

Verifica lo que el modulo `onboarding` promete: que el negocio, su dueno, su
profesional y sus horarios se crean **juntos o no se crean**, que el dueno puede
entrar despues con las credenciales que eligio, y que el nombre de URL se rechaza
con un mensaje que dice que hacer.

## Por que estos tests son de integracion y no de unidad

El alta toca cuatro tablas, una funcion `SECURITY DEFINER` y tres politicas de RLS.
Un test de unidad con sesion falsa probaria que el codigo llama a `session.add` en
el orden que el autor quiso, y nada mas: no probaria que la funcion existe, que
`tempus_app` tiene permiso de ejecutarla, que el `INSERT` de `business_hours` pasa
la politica con el GUC puesto, ni que el `UNIQUE` de `businesses.slug` se traduce
en 409. Todo eso--lo unico que puede salir mal de verdad-- es lo que queda afuera de
un mock.

## Lo que estos tests NO cubren, y por que

**Dos altas simultaneas del mismo slug.** Seria el test mas importante--es la
carrera que el `UNIQUE` existe para arbitrar-- pero no se puede escribir con estos
fixtures: `http_client` y `session` comparten **una sola conexion** en una sola
transaccion, y dos `asyncio.gather` sobre ella se ejecutan en serie. El test pasaria
dando la impresion de concurrencia sin probar ninguna.

Lo que lo cubre es el indice unico, que es garantia de PostgreSQL y no codigo
nuestro, y `test_bookings_integration.py` ya ejercita concurrencia real--con
conexiones separadas-- para el caso equivalente de las reservas. Agregar aqui una
version secuencial con otro nombre solo enganaria al que lea el reporte.
"""

from __future__ import annotations

import datetime as dt
import uuid

import jwt as pyjwt
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

#: Credenciales que el dueo "eligio" en el formulario. El dominio es real a
#: proposito: `EmailStr` rechaza `.test`/`.example` salvo que se le pase
#: `test_environment=True`, y un 422 de validacion taparia el 201 que se quiere
#: comprobar. Es el mismo problema que documenta `test_auth_router.py`.
EMAIL_DUENO = "Duenio.Peluche@Peluqueria.com"
PASSWORD_DUENO = "una-contrasena-larga-de-prueba"

URL_ALTA = "/api/v1/auth/register-business"
URL_SLUG = "/api/v1/public/slug-disponible"
URL_LOGIN = "/api/v1/auth/login"


def _cuerpo(slug: str = "peluqueria-el-peluche", **cambios: object) -> dict[str, object]:
    """El cuerpo de un alta valida. `**cambios` pisa campos para los casos raros."""
    cuerpo: dict[str, object] = {
        "business_name": "Peluqueria El Peluche",
        "slug": slug,
        "owner_name": "Ana Perez",
        "email": EMAIL_DUENO,
        "password": PASSWORD_DUENO,
        "phone": "+54 9 11 2345-6789",
        "timezone": "America/Argentina/Buenos_Aires",
    }
    cuerpo.update(cambios)
    return cuerpo


async def _contar(sesion: AsyncSession, sql: str, **params: object) -> int:
    valor = await sesion.execute(text(sql), params)
    return int(valor.scalar_one())


async def _alta(http_client: AsyncClient, **cambios: object) -> dict[str, object]:
    """Hace un alta que se espera exitosa y devuelve el cuerpo de la respuesta.

    Concentra el `assert 201` para que los tests que van a verificar el contenido
    no repitan el codigo de estado en cada linea. Si el alta falla, el `assert`
    trae el body entero--que es donde esta la causa-- en vez de un
    `KeyError: 'business_id'`.
    """
    respuesta = await http_client.post(URL_ALTA, json=_cuerpo(**cambios))
    assert respuesta.status_code == 201, respuesta.text
    cuerpo: dict[str, object] = respuesta.json()
    return cuerpo


class TestAltaExitosa:
    async def test_devuelve_201_con_el_slug_normalizado(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """El alta responde 201 y devuelve el slug canonico, no el que se escribio."""
        cuerpo = await _alta(http_client)

        assert cuerpo["slug"] == "peluqueria-el-peluche"
        assert cuerpo["mensaje"]
        # Sin tokens: el dueno entra por `/login`. Ver la razon en el docstring del
        # handler; aca se verifica que no aparecio ninguno de los dos.
        assert "access_token" not in cuerpo
        assert "refresh_token" not in cuerpo

        negocio = (
            await session.execute(
                text(
                    "SELECT name, slug, phone_e164, timezone, status FROM businesses WHERE id = :id"
                ),
                {"id": uuid.UUID(str(cuerpo["business_id"]))},
            )
        ).one()
        assert negocio.name == "Peluqueria El Peluche"
        # El telefono se guarda en E.164 **sin** el `+`: es lo que espera la API de
        # Meta y lo que hace comparables dos registros del mismo numero.
        assert negocio.phone_e164 == "5491123456789"
        assert negocio.slug == "peluqueria-el-peluche"
        assert negocio.timezone == "America/Argentina/Buenos_Aires"

    async def test_crea_dueno_profesional_y_horarios(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Un negocio, un admin y doce ventanas. Ni una fila de mas."""
        cuerpo = await _alta(http_client)
        business_id = uuid.UUID(str(cuerpo["business_id"]))

        assert (
            await _contar(session, "SELECT count(*) FROM businesses WHERE id = :id", id=business_id)
            == 1
        )
        assert (
            await _contar(
                session,
                "SELECT count(*) FROM business_users WHERE business_id = :id",
                id=business_id,
            )
            == 1
        )
        # Doce filas y no seis: el modelo no tiene descanso dentro de una ventana
        # (§11), asi que la jornada partida de 09-12 / 16-20 son dos filas por dia.
        assert (
            await _contar(
                session,
                "SELECT count(*) FROM business_hours WHERE business_id = :id",
                id=business_id,
            )
            == 12
        )

        dueno = (
            await session.execute(
                text(
                    "SELECT role, status, email, full_name"
                    " FROM business_users WHERE business_id = :id"
                ),
                {"id": business_id},
            )
        ).one()
        assert dueno.role == "admin"
        # `active` y no `invited`: el dueno eligio su propia contrasena en este
        # formulario, asi que no hay nada que confirmar.
        assert dueno.status == "active"
        assert dueno.full_name == "Ana Perez"
        # `casefold`, no `lower`: el correo se guardo en minusculas aunque el
        # formulario lo mando con mayusculas y punto. `citext` compararia igual, pero
        # guardar el texto tal cual haria que el mismo miembro apareciera escrito de
        # dos formas en el panel.
        assert dueno.email == EMAIL_DUENO.casefold()

    async def test_el_profesional_apunta_al_dueno_y_al_mismo_negocio(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """El profesional del alta es el dueno, y la FK compuesta ata los dos tenants.

        Esta es la asercion que mas vale: si `professionals.user_id` quedara sin su
        `business_id` en la FK, un bug que propague mal el id dejaria al profesional
        del negocio A vinculado al login del negocio B, y **ningun** otro test de
        este archivo lo veria.
        """
        cuerpo = await _alta(http_client)
        business_id = uuid.UUID(str(cuerpo["business_id"]))

        profesional = (
            await session.execute(
                text(
                    "SELECT user_id, business_id, display_name, is_active"
                    " FROM professionals WHERE business_id = :id"
                ),
                {"id": business_id},
            )
        ).one()
        dueno_id = await session.scalar(
            text("SELECT id FROM business_users WHERE business_id = :id"), {"id": business_id}
        )

        assert profesional.user_id == dueno_id
        assert profesional.business_id == business_id
        assert profesional.display_name == "Ana Perez"
        assert profesional.is_active is True

    async def test_los_horarios_van_de_lunes_a_sabado(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Lunes a sabado con dos ventanas; el domingo no tiene filas.

        El domingo **no** se siembra como `is_open = false`: la ausencia de filas ya
        significa cerrado (§11), y la fila cerrada seria una fila mas que el admin
        tiene que editar el dia que quiera abrir.
        """
        cuerpo = await _alta(http_client)
        business_id = uuid.UUID(str(cuerpo["business_id"]))

        filas = (
            await session.execute(
                text(
                    "SELECT weekday, window_index, start_time, end_time, is_open"
                    " FROM business_hours WHERE business_id = :id"
                    " ORDER BY weekday, window_index"
                ),
                {"id": business_id},
            )
        ).all()

        assert {f.weekday for f in filas} == {0, 1, 2, 3, 4, 5}
        assert 6 not in {f.weekday for f in filas}
        assert all(f.is_open for f in filas)
        # Dos ventanas por dia y no una: el indice de ventana las distingue.
        assert len({f.window_index for f in filas}) == 2

        ventanas = {(f.start_time, f.end_time) for f in filas}
        # `datetime.time`--no `timestamp`: si el modelo guardara `09:00:00` la
        # comparacion con `time(9, 0)` seria False y esta asercion lo detectaria.
        assert ventanas == {
            (dt.time(9, 0), dt.time(12, 0)),
            (dt.time(16, 0), dt.time(20, 0)),
        }
        for fila in filas:
            assert fila.start_time < fila.end_time

    async def test_el_dueno_puede_entrar_con_su_contrasena(self, http_client: AsyncClient) -> None:
        """El cierre del flujo: el alta deja una credencial que `/login` acepta.

        Sin esto, el alta podria crear un dueno con la contrasena hasheada de otra
        forma, con el rol equivocado, o inactivo, y todos los tests anteriores
        seguirian en verde--porque ninguno mira la autenticacion.
        """
        cuerpo = await _alta(http_client)
        business_id = uuid.UUID(str(cuerpo["business_id"]))

        login = await http_client.post(
            URL_LOGIN, json={"email": EMAIL_DUENO, "password": PASSWORD_DUENO}
        )

        assert login.status_code == 200, login.text
        claims = pyjwt.decode(
            login.json()["access_token"],
            options={"verify_signature": False, "verify_exp": False},
        )
        # El `tid` del claim es lo que hace que el panel entre al tenant correcto
        # sin pedir nada mas (ADR-0010). Si el alta creara el dueno en otro negocio,
        # el login daria 200 igual y el panel abriria en vacio: por eso se compara
        # el claim contra el id que devolvio el alta.
        assert claims["tid"] == str(business_id)

    async def test_el_email_se_normaliza_para_el_login(self, http_client: AsyncClient) -> None:
        """El mismo correo en otra capitalizacion es el mismo miembro.

        Sin `casefold`, `Duenio@x.com` y `duenio@x.com` serian el mismo login para
        `citext` pero dos filas con dos contrasenas, y el dueno que escribio con
        mayusculas no podria volver a entrar.
        """
        await _alta(http_client)

        login = await http_client.post(
            URL_LOGIN, json={"email": EMAIL_DUENO.lower(), "password": PASSWORD_DUENO}
        )

        assert login.status_code == 200, login.text


class TestSlug:
    async def test_normaliza_mayusculas_y_acentos(self, http_client: AsyncClient) -> None:
        """Escribir el slug en crudo no es un error: se canonicaliza con `slugify`.

        El usuario escribe el nombre del negocio, no una URL. Que "Barbería El
        Peluche" se convierta solo--y que la respuesta diga cual quedo-- evita el
        ciclo de leer el 422, corregir a mano y volver a enviar.
        """
        respuesta = await http_client.post(URL_ALTA, json=_cuerpo(slug="Barbería El Peluche"))

        assert respuesta.status_code == 201, respuesta.text
        assert respuesta.json()["slug"] == "barberia-el-peluche"

    async def test_slug_duplicado_responde_409(self, http_client: AsyncClient) -> None:
        """El `UNIQUE` de `businesses.slug` traducido a 409, y no a 500.

        El mensaje tiene que decir "ya esta en uso" y no repetir el error de
        Postgres: es lo que se muestra al lado del campo del formulario.
        """
        await _alta(http_client)

        segunda = await http_client.post(
            URL_ALTA, json=_cuerpo(email="otro@peluqueria.com", owner_name="Bruno Diaz")
        )

        assert segunda.status_code == 409, segunda.text
        assert "en uso" in segunda.json()["detail"]

    async def test_slug_reservado_responde_409_con_otro_mensaje(
        self, http_client: AsyncClient
    ) -> None:
        """Una palabra de `slug_reservations` se rechaza con su propio mensaje.

        **Este test solo puede existir porque el chequeo vive en la base.** La
        palabra reservada sale de la funcion `SECURITY DEFINER` de `0014`, a la que
        llega el `23514`; `tempus_app` no puede ni leer `slug_reservations`, asi que
        una lista en Python--la que habia antes--seria un chequeo que ademas de
        borrarse en silencio no cubre un alta hecha por `psql`.
        """
        respuesta = await http_client.post(URL_ALTA, json=_cuerpo(slug="admin"))

        assert respuesta.status_code == 409, respuesta.text
        detalle = respuesta.json()["detail"]
        assert "reservad" in detalle
        # Distinto del mensaje de duplicado, porque "ya esta en uso" invita a esperar
        # y "esta reservado" a elegir otra palabra.
        assert "en uso" not in detalle

    async def test_el_slug_vacio_despues_de_normalizar_responde_422(
        self, http_client: AsyncClient
    ) -> None:
        """Texto que no deja slug--puntuacion suelta--se rechaza antes de la base.

        Aceptarlo seria peor que una URL sin nombre: el negocio quedaria con
        `slug = ''` y su pagina publica no tendria donde exist.
        """
        respuesta = await http_client.post(URL_ALTA, json=_cuerpo(slug="---"))

        assert respuesta.status_code == 422, respuesta.text


class TestChequeoDeSlugEnVivo:
    """`GET /public/slug-disponible`, que el formulario consulta mientras se escribe."""

    async def test_responde_disponible_para_un_slug_libre(self, http_client: AsyncClient) -> None:
        respuesta = await http_client.get(URL_SLUG, params={"slug": "un-sitio-nuevo"})

        assert respuesta.status_code == 200
        # Devuelve el slug ya normalizado: el formulario muestra la URL que va a
        # quedar, no un eco de lo que se escribio.
        assert respuesta.json() == {"slug": "un-sitio-nuevo", "disponible": True}

    async def test_normaliza_lo_que_llega_crudo(self, http_client: AsyncClient) -> None:
        respuesta = await http_client.get(URL_SLUG, params={"slug": "Barbería Nueva"})

        assert respuesta.status_code == 200
        assert respuesta.json() == {"slug": "barberia-nueva", "disponible": True}

    async def test_responde_no_disponible_para_un_slug_ocupado(
        self, http_client: AsyncClient
    ) -> None:
        await _alta(http_client)

        respuesta = await http_client.get(URL_SLUG, params={"slug": "peluqueria-el-peluche"})

        assert respuesta.status_code == 200
        assert respuesta.json()["disponible"] is False

    async def test_responde_no_disponible_para_un_slug_reservado(
        self, http_client: AsyncClient
    ) -> None:
        """La palabra reservada se ve antes de enviar, no con el 409.

        Con el chequeo del panel--que solo mira `businesses`-- el formulario
        responderia "disponible" para `admin` y el 409 apareceria recien al enviar:
        justo el caso que un chequeo en vivo existe para evitar.
        """
        respuesta = await http_client.get(URL_SLUG, params={"slug": "admin"})

        assert respuesta.status_code == 200
        assert respuesta.json() == {"slug": "admin", "disponible": False}

    async def test_texto_sin_slug_responde_no_disponible(self, http_client: AsyncClient) -> None:
        """Puntuacion suelta no es un error 422: es "aun no hay nada que reservar"."""
        respuesta = await http_client.get(URL_SLUG, params={"slug": "!!!"})

        assert respuesta.status_code == 200
        assert respuesta.json() == {"slug": "", "disponible": False}


class TestValidacionesDelAlta:
    async def test_zona_horaria_inexistente_responde_422(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Una zona invalida se rechaza en el alta y no meses despues.

        El sintoma de dejarlo pasar no es un error: es un negocio nuevo que no
        muestra disponibilidad, sin mensaje y sin log.
        """
        respuesta = await http_client.post(URL_ALTA, json=_cuerpo(timezone="America/No_Existe"))

        assert respuesta.status_code == 422, respuesta.text
        assert "zona horaria" in respuesta.json()["detail"].lower()
        # Y no dejo negocio a medias: la transaccion se deshizo entera.
        assert (
            await _contar(
                session,
                "SELECT count(*) FROM businesses WHERE slug = :slug",
                slug="peluqueria-el-peluche",
            )
            == 0
        )

    async def test_contrasena_corta_responde_422(self, http_client: AsyncClient) -> None:
        """El piso de 12 caracteres--el unico punto donde la API acepta que alguien
        elija una contrasena-- lo responde el esquema, antes de hashear nada.

        Que lo rechace el esquema y no el servicio importa por costo: hashear once
        caracteres para descubrir despues que no servian seria trabajo de Argon2
        regalado a cualquiera que haga POST.
        """
        respuesta = await http_client.post(URL_ALTA, json=_cuerpo(password="corta12345"))

        assert respuesta.status_code == 422, respuesta.text

    async def test_nombre_muy_corto_responde_422(self, http_client: AsyncClient) -> None:
        respuesta = await http_client.post(URL_ALTA, json=_cuerpo(owner_name="A"))

        assert respuesta.status_code == 422, respuesta.text

    async def test_telefono_no_internacional_responde_422(self, http_client: AsyncClient) -> None:
        """Un numero local no sirve para WhatsApp: Meta solo acepta E.164.

        El campo es opcional--se puede dar de alta sin telefono-- pero si viene tiene
        que servir para lo que Tempus hace con el: enviar mensajes.
        """
        respuesta = await http_client.post(URL_ALTA, json=_cuerpo(phone="011 2345-6789"))

        assert respuesta.status_code == 422, respuesta.text

    async def test_telefono_ausente_no_es_error(self, http_client: AsyncClient) -> None:
        """El telefono es opcional, y el alta funciona sin el."""
        respuesta = await http_client.post(URL_ALTA, json=_cuerpo(phone=None))

        assert respuesta.status_code == 201, respuesta.text


class TestRateLimit:
    async def test_el_limite_por_ip_corta_en_el_cuarto_intento(
        self, http_client: AsyncClient
    ) -> None:
        """Tres altas por hora y por IP; la cuarta es 429.

        El limite existe por el costo, no por la maldad: cada intento paga un Argon2
        completo. Sin el, un bucle de requests desde una sola IP gasta CPU del
        servidor a voluntad de cualquiera.

        **Se prueba con cuatro intentos consecutivos y no con concurrencia**: el
        cubo se cuenta con `rate_limit_hit`, que commitea en su propia transaccion
        (§ `0013`), asi que lo que importa es el orden y no la simultaneidad.
        """
        respuestas = []
        for indice in range(4):
            respuestas.append(
                await http_client.post(
                    URL_ALTA,
                    json=_cuerpo(slug=f"negocio-{indice}", email=f"duenio{indice}@peluqueria.com"),
                )
            )

        assert [r.status_code for r in respuestas] == [201, 201, 201, 429]
        # El 429 dice cuando volver a intentar--sin eso el frontend no puede
        # explicarle al usuario por que no lo deja seguir.
        assert respuestas[3].json()["detail"]
        assert respuestas[3].headers.get("Retry-After")

    async def test_el_chequeo_de_slug_no_cuenta_contra_el_limite_del_alta(
        self, http_client: AsyncClient
    ) -> None:
        """Consultar disponibilidad muchas veces no agota el cubo del alta.

        El formulario consulta en cada tecla. Si las dos compartieran cubo--o si el
        chequeo usara el limite de 3/hora-- escribir el nombre del negocio entero
        seria imposible.
        """
        for indice in range(5):
            respuesta = await http_client.get(URL_SLUG, params={"slug": f"prueba-{indice}"})
            assert respuesta.status_code == 200

        assert (await http_client.post(URL_ALTA, json=_cuerpo())).status_code == 201


class TestAtomicidad:
    async def test_un_fallo_tardio_no_deja_el_negocio_sin_dueno(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Si algo falla despues de crear el negocio, no queda un negocio huerfano.

        Este es el estado peor posible--y el que justifica que el modulo no commitee
        adentro--: el negocio existe, el dueno ya se leyo el mensaje de exito, y no
        hay nadie que pueda entrar a administrarlo.

        **Este test no usa `http_client` a proposito.** El fixture pisa `get_session`
        por un generador que hace `yield` y nada mas: no commitea ni revierte. La
        atomicidad real la da `session_scope`, que es la dependencia de produccion y
        la que revierte ante excepcion. Con el override del fixture, un fallo
        tenderia las filas en la transaccion del test--que se deshace al final-- y el
        `assert` de que no quedo nada estaria probando el fixture, no el producto.

        Por eso se abre `session_scope` a mano, se inyecta el fallo adentro y se
        comprueba con una consulta nueva--que es la unica forma de ver si quedo algo
        commiteado de verdad.
        """
        from app.db.session import session_scope
        from app.modules.onboarding import service as onboarding

        flush_original = AsyncSession.flush
        llamadas = {"n": 0}

        async def flush_con_fallo(self: AsyncSession, *args: object, **kwargs: object) -> None:
            """Deja pasar el primer `flush`--el del dueno-- y rompe el segundo."""
            await flush_original(self, *args, **kwargs)
            llamadas["n"] += 1
            if llamadas["n"] >= 2:
                raise RuntimeError("fallo simulado despues de crear el negocio")

        monkeypatch.setattr(onboarding.AsyncSession, "flush", flush_con_fallo)

        with pytest.raises(RuntimeError, match="fallo simulado"):
            async with session_scope() as sesion:
                await onboarding.registrar_negocio(
                    sesion,
                    business_name="Peluqueria El Peluche",
                    slug="peluqueria-el-peluche",
                    owner_name="Ana Perez",
                    email=EMAIL_DUENO,
                    password=PASSWORD_DUENO,
                    phone=None,
                    timezone="America/Argentina/Buenos_Aires",
                )

        monkeypatch.undo()

        # La consulta va por `session`, que abre sus propias statements sobre otra
        # conexion: si el rollback de `session_scope` no hubiera ocurrido, las filas
        # commiteadas se verian aqui.
        assert (
            await _contar(
                session,
                "SELECT count(*) FROM businesses WHERE slug = :slug",
                slug="peluqueria-el-peluche",
            )
            == 0
        )
        assert (
            await _contar(
                session,
                "SELECT count(*) FROM business_users WHERE email = :email",
                email=EMAIL_DUENO.casefold(),
            )
            == 0
        )
