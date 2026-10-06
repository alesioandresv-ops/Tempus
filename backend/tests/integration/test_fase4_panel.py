"""CRUD del panel de la Fase 4: servicios, profesionales y horarios.

Contra Postgres real y con la RLS puesta, por las mismas razones que
`test_onboarding.py`: lo que puede salir mal en estos endpoints no es
que el codigo llame a `session.add` en el orden correcto--eso lo ve
cualquier test con mocks-- sino que la politica de seguridad deje pasar
un INSERT sin el GUC, que el `UNIQUE` de `professional_services` se
traduzca en el error correcto, y que un borrado logico deje de verse
en la reserva online sin desaparecer de la historia.

## El GUC en los tests

`get_tenant_session` de produccion pone `app.current_business_id`
antes de entregar la sesion. El override de `http_client` entrega la
sesion del test **sin** eso, y por eso cada test llama a
`_poner_tenant`: sin el GUC, la RLS devuelve cero filas y el panel
parece vacio aunque el alta haya ido bien. `set_config(..., true)`
es `SET LOCAL`: vale por la transaccion que comparten el test y el
cliente, y muere con el rollback final.

## Lo que no se prueba aca

La disponibilidad (Fase 5) y las reservas (Fase 6) no existen todavia:
un horario valido aca solo garantiza que se guarda bien, no que se
pueda reservar en el.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from app.api.errors import ValidationError
from app.db.session import TENANT_GUC
from app.modules.schedules.service import (
    HorarioDia,
    Ventana,
    reemplazar_horarios,
)
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

#: Credenciales que el dueno "eligio" en el formulario. El dominio es
#: real a proposito: `EmailStr` rechaza `.test`/`.example` salvo que se
#: le pase `test_environment=True`, y un 422 de validacion taparia el
#: 201 que se quiere comprobar. Es el mismo problema que documenta
#: `test_auth_router.py`.
EMAIL_DUENO = "Duenio.Fase4@Peluqueria.com"
EMAIL_DUENO_B = "Duenio.B.Fase4@Peluqueria.com"
PASSWORD_DUENO = "una-contrasena-larga-de-prueba"

URL_ALTA = "/api/v1/auth/register-business"
URL_LOGIN = "/api/v1/auth/login"
URL_SERVICIOS = "/api/v1/business/servicios"
URL_PROFESIONALES = "/api/v1/business/profesionales"
URL_HORARIOS = "/api/v1/business/horarios"


def _cuerpo_alta(slug: str = "peluqueria-fase4", **cambios: object) -> dict[str, object]:
    """El cuerpo de un alta valida. `**cambios` pisas campos para los casos raros."""
    cuerpo: dict[str, object] = {
        "business_name": "Peluqueria Fase 4",
        "slug": slug,
        "owner_name": "Ana Perez",
        "email": EMAIL_DUENO,
        "password": PASSWORD_DUENO,
        "phone": "+54 9 11 2345-6789",
        "timezone": "America/Argentina/Buenos_Aires",
    }
    cuerpo.update(cambios)
    return cuerpo


async def _alta(http_client: AsyncClient, **cambios: object) -> dict[str, object]:
    """Hace un alta que se espera exitosa y devuelve el cuerpo de la respuesta."""
    respuesta = await http_client.post(URL_ALTA, json=_cuerpo_alta(**cambios))
    assert respuesta.status_code == 201, respuesta.text
    cuerpo: dict[str, object] = respuesta.json()
    return cuerpo


async def _login(http_client: AsyncClient, email: str = EMAIL_DUENO) -> str:
    """Devuelve el `access_token` del dueno, para el header de los endpoints del panel."""
    respuesta = await http_client.post(URL_LOGIN, json={"email": email, "password": PASSWORD_DUENO})
    assert respuesta.status_code == 200, respuesta.text
    token: str = respuesta.json()["access_token"]
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _mensajes_de_campo(respuesta: object) -> str:
    """Los mensajes de un 422 de esquema, en un solo texto.

    El `detail` de un 422 de esquema es generico a proposito
    ("El pedido no cumple el esquema."); lo util esta en `errors`,
    con `field` y `message` por campo (RFC 9457). Juntar los
    mensajes en un texto deja la asercion simple sin perder la
    busqueda del dato que importa.
    """
    cuerpo: dict[str, object] = respuesta.json()  # type: ignore[attr-defined]
    errores: list[dict[str, str]] = cuerpo["errors"]  # type: ignore[typeddict-item]
    return " ".join(e["message"] for e in errores)


async def _poner_tenant(session: AsyncSession, business_id: uuid.UUID) -> None:
    """Pone el GUC del tenant en la transaccion compartida (ver docstring)."""
    await session.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(business_id)},
    )


def _cuerpo_servicio(**cambios: object) -> dict[str, object]:
    cuerpo: dict[str, object] = {
        "name": "Corte",
        "duration_minutes": 30,
        "price": "1000.00",
        "currency": "ARS",
    }
    cuerpo.update(cambios)
    return cuerpo


class TestServicios:
    async def test_las_duraciones_permitidas_crean_servicio(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Las seis del set del catalogo entran, y vuelven escritas."""
        cuerpo = await _alta(http_client)
        await _poner_tenant(session, uuid.UUID(str(cuerpo["business_id"])))
        token = await _login(http_client)

        for duracion in (15, 30, 45, 60, 90, 120):
            respuesta = await http_client.post(
                URL_SERVICIOS,
                json=_cuerpo_servicio(name=f"Corte {duracion}", duration_minutes=duracion),
                headers=_auth(token),
            )
            assert respuesta.status_code == 201, respuesta.text
            assert respuesta.json()["duration_minutes"] == duracion

    async def test_una_duracion_fuera_del_set_responde_422(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Cuatro minutos de margen no alcanzan: el set es cerrado a proposito.

        El detalle nombra las permitidas, que es lo unico que le sirve al
        administrador para corregir el formulario sin adivinar.
        """
        cuerpo = await _alta(http_client)
        await _poner_tenant(session, uuid.UUID(str(cuerpo["business_id"])))
        token = await _login(http_client)

        respuesta = await http_client.post(
            URL_SERVICIOS,
            json=_cuerpo_servicio(duration_minutes=40),
            headers=_auth(token),
        )

        assert respuesta.status_code == 422, respuesta.text
        mensajes = _mensajes_de_campo(respuesta)
        for duracion in (15, 30, 45, 60, 90, 120):
            assert str(duracion) in mensajes

    async def test_crud_completo_y_baja_logica(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Crear, leer, editar y archivar: el servicio desaparece del listado
        activo pero sigue ahi con su historia.

        El DELETE es archivado y no fisico por la FK de `bookings`
        (ver el docstring del servicio); que el listado activo quede
        vacio y el archivado siga visible con `incluir_archivados` es
        lo que el panel muestra.
        """
        cuerpo = await _alta(http_client)
        await _poner_tenant(session, uuid.UUID(str(cuerpo["business_id"])))
        token = await _login(http_client)

        creado = (
            await http_client.post(
                URL_SERVICIOS,
                json=_cuerpo_servicio(),
                headers=_auth(token),
            )
        ).json()
        servicio_id = creado["id"]

        # Listado: aparece, activo y sin `archived_at`.
        listado = (await http_client.get(URL_SERVICIOS, headers=_auth(token))).json()
        assert [s["id"] for s in listado] == [servicio_id]
        assert listado[0]["is_active"] is True
        assert listado[0]["archived_at"] is None

        # Lectura por id: la misma fila.
        uno = (await http_client.get(f"{URL_SERVICIOS}/{servicio_id}", headers=_auth(token))).json()
        assert uno["name"] == "Corte"

        # Edicion parcial: solo lo que se manda cambia.
        editado = (
            await http_client.patch(
                f"{URL_SERVICIOS}/{servicio_id}",
                json={"name": "Corte premium", "price": "1500.50"},
                headers=_auth(token),
            )
        ).json()
        assert editado["name"] == "Corte premium"
        assert str(editado["price"]) == "1500.50"
        assert editado["duration_minutes"] == 30

        # Baja: archiva y desactiva, no borra.
        archivado = (
            await http_client.delete(f"{URL_SERVICIOS}/{servicio_id}", headers=_auth(token))
        ).json()
        assert archivado["is_active"] is False
        assert archivado["archived_at"] is not None

        # Ya no esta en el listado activo...
        listado = (await http_client.get(URL_SERVICIOS, headers=_auth(token))).json()
        assert listado == []
        # ...pero sigue existiendo para quien pregunte por los archivados.
        # `incluir_inactivos` tambien: archivar desactiva, y son dos
        # filtros distintos (ver el docstring del servicio).
        con_archivados = (
            await http_client.get(
                URL_SERVICIOS,
                params={"incluir_archivados": True, "incluir_inactivos": True},
                headers=_auth(token),
            )
        ).json()
        assert [s["id"] for s in con_archivados] == [servicio_id]

    async def test_el_servicio_de_otro_negocio_es_404(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Un token de otro negocio no ve el servicio: 404, no 403.

        No confirmar que existe es la decision de diseño: un 403 le
        diria a quien pregunta que el servicio existe en otro negocio.
        """
        primero = await _alta(http_client)
        await _poner_tenant(session, uuid.UUID(str(primero["business_id"])))
        token_a = await _login(http_client)
        creado = (
            await http_client.post(
                URL_SERVICIOS,
                json=_cuerpo_servicio(),
                headers=_auth(token_a),
            )
        ).json()

        # Limpia el tenant antes del segundo alta: no es algo que este
        # test deba asumir del modulo de onboarding.
        await session.execute(text(f"SELECT set_config('{TENANT_GUC}', '', true)"))
        segundo = await _alta(http_client, slug="peluqueria-fase4-b", email=EMAIL_DUENO_B)
        await _poner_tenant(session, uuid.UUID(str(segundo["business_id"])))
        token_b = await _login(http_client, email=EMAIL_DUENO_B)

        respuesta = await http_client.get(f"{URL_SERVICIOS}/{creado['id']}", headers=_auth(token_b))
        assert respuesta.status_code == 404, respuesta.text


class TestProfesionales:
    async def test_crear_profesional_con_whatsapp_y_avatar(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """El WhatsApp viaja sin `+`, como el telefono del cliente."""
        cuerpo = await _alta(http_client)
        await _poner_tenant(session, uuid.UUID(str(cuerpo["business_id"])))
        token = await _login(http_client)

        respuesta = await http_client.post(
            URL_PROFESIONALES,
            json={
                "display_name": "Ana Perez",
                "whatsapp": "5491123456789",
                "avatar_url": "https://ejemplo.com/ana.png",
            },
            headers=_auth(token),
        )

        assert respuesta.status_code == 201, respuesta.text
        creado = respuesta.json()
        assert creado["whatsapp"] == "5491123456789"
        assert creado["avatar_url"] == "https://ejemplo.com/ana.png"

    async def test_whatsapp_invalido_responde_422(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """El `+`, las letras y los numeros cortos son 422 antes de la base.

        El CHECK de `0016` es la garantia de ultimo recurso; el esquema
        da el error util. Un WhatsApp mal guardado es un recordatorio
        que nunca llega, y de eso se trata el campo. El patron acepta
        de 7 a 15 digitos (E.164): seis no alcanzan.
        """
        cuerpo = await _alta(http_client)
        await _poner_tenant(session, uuid.UUID(str(cuerpo["business_id"])))
        token = await _login(http_client)

        for whatsapp in ("+5491123456789", "549abc", "123456"):
            respuesta = await http_client.post(
                URL_PROFESIONALES,
                json={"display_name": "Ana Perez", "whatsapp": whatsapp},
                headers=_auth(token),
            )
            assert respuesta.status_code == 422, (whatsapp, respuesta.text)
            assert "E.164" in _mensajes_de_campo(respuesta)

    async def test_patch_cambia_y_borra_el_whatsapp(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Vacio es la forma de borrar el campo; `None` (no mandarlo) no lo toca."""
        cuerpo = await _alta(http_client)
        await _poner_tenant(session, uuid.UUID(str(cuerpo["business_id"])))
        token = await _login(http_client)

        creado = (
            await http_client.post(
                URL_PROFESIONALES,
                json={"display_name": "Ana Perez"},
                headers=_auth(token),
            )
        ).json()
        profesional_id = creado["id"]
        assert creado["whatsapp"] is None

        editado = (
            await http_client.patch(
                f"{URL_PROFESIONALES}/{profesional_id}",
                json={"whatsapp": "5491123456789", "avatar_url": "https://ejemplo.com/a.png"},
                headers=_auth(token),
            )
        ).json()
        assert editado["whatsapp"] == "5491123456789"

        # Vacio borra; la base no aceptaria "" y el servicio lo convierte en NULL.
        borrado = (
            await http_client.patch(
                f"{URL_PROFESIONALES}/{profesional_id}",
                json={"whatsapp": ""},
                headers=_auth(token),
            )
        ).json()
        assert borrado["whatsapp"] is None
        assert borrado["avatar_url"] == "https://ejemplo.com/a.png"

    async def test_avatar_que_no_es_http_responde_422(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Un `javascript:` o un `ftp://` no son fuente de foto para el panel."""
        cuerpo = await _alta(http_client)
        await _poner_tenant(session, uuid.UUID(str(cuerpo["business_id"])))
        token = await _login(http_client)

        respuesta = await http_client.post(
            URL_PROFESIONALES,
            json={"display_name": "Ana Perez", "avatar_url": "javascript:alert(1)"},
            headers=_auth(token),
        )
        assert respuesta.status_code == 422, respuesta.text


class TestAsignacionDeServicios:
    """La tabla intermedia: que profesional hace que servicio."""

    async def test_reemplaza_el_conjunto_completo(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Mandar la lista entera reemplaza, no suma.

        Un delta (agregar/quitar) obliga al frontend a conocer el estado
        previo para calcular el diff, y en un formulario con guardar, un
        fallo de red deja servicios cambiados a medias.
        """
        cuerpo = await _alta(http_client)
        negocio = uuid.UUID(str(cuerpo["business_id"]))
        await _poner_tenant(session, negocio)
        token = await _login(http_client)

        servicios = []
        for nombre in ("Corte", "Barba"):
            creado = (
                await http_client.post(
                    URL_SERVICIOS,
                    json=_cuerpo_servicio(name=nombre),
                    headers=_auth(token),
                )
            ).json()
            servicios.append(creado["id"])

        profesional = (
            await http_client.post(
                URL_PROFESIONALES,
                json={"display_name": "Ana Perez"},
                headers=_auth(token),
            )
        ).json()
        profesional_id = profesional["id"]

        url = f"{URL_PROFESIONALES}/{profesional_id}/servicios"

        # Los dos servicios, asignados.
        primera = (
            await http_client.put(
                url,
                json={"servicios": [{"service_id": s} for s in servicios]},
                headers=_auth(token),
            )
        ).json()
        assert sorted(a["service_id"] for a in primera) == sorted(servicios)
        assert all(a["is_active"] for a in primera)

        # Solo uno: la lista anterior desaparecio, no se sumo.
        segunda = (
            await http_client.put(
                url,
                json={"servicios": [{"service_id": servicios[0]}]},
                headers=_auth(token),
            )
        ).json()
        assert [a["service_id"] for a in segunda] == [servicios[0]]

        # Y se lee lo mismo por GET.
        listado = (await http_client.get(url, headers=_auth(token))).json()
        assert [a["service_id"] for a in listado] == [servicios[0]]

    async def test_un_servicio_que_no_es_del_negocio_responde_422(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """La FK compuesta lo garantiza en la base; el servicio lo
        traduce a 422 con el id en el mensaje antes de llegar a ella."""
        cuerpo = await _alta(http_client)
        await _poner_tenant(session, uuid.UUID(str(cuerpo["business_id"])))
        token = await _login(http_client)

        profesional = (
            await http_client.post(
                URL_PROFESIONALES,
                json={"display_name": "Ana Perez"},
                headers=_auth(token),
            )
        ).json()

        respuesta = await http_client.put(
            f"{URL_PROFESIONALES}/{profesional['id']}/servicios",
            json={"servicios": [{"service_id": str(uuid.uuid4())}]},
            headers=_auth(token),
        )

        assert respuesta.status_code == 422, respuesta.text
        assert "no son del negocio" in respuesta.json()["detail"]


class TestHorarios:
    async def test_ventanas_solapadas_responden_422(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Dos ventanas del mismo dia que se pisan son 422, y no se guarda nada.

        La validacion va antes del borrado: un error a mitad del reemplazo
        no puede dejar al negocio sin horarios.
        """
        cuerpo = await _alta(http_client)
        await _poner_tenant(session, uuid.UUID(str(cuerpo["business_id"])))
        token = await _login(http_client)

        respuesta = await http_client.put(
            URL_HORARIOS,
            json={
                "dias": [
                    {
                        "weekday": 0,
                        "windows": [
                            {"start": "09:00", "end": "13:00"},
                            {"start": "11:00", "end": "15:00"},
                        ],
                    }
                ]
            },
            headers=_auth(token),
        )

        assert respuesta.status_code == 422, respuesta.text
        assert "se pisan" in _mensajes_de_campo(respuesta)

        # Nada se escribio: el lunes sigue con el horario que siembra
        # el alta (09-12 y 16-20, lunes a sabado), intacto. El
        # reemplazo valida **antes** de borrar: un error a mitad
        # del pedido no puede dejar al negocio sin horarios.
        lectura = (await http_client.get(URL_HORARIOS, headers=_auth(token))).json()
        lunes = next(d for d in lectura["dias"] if d["weekday"] == 0)
        assert lunes["windows"] == [
            {"start": "09:00:00", "end": "12:00:00"},
            {"start": "16:00:00", "end": "20:00:00"},
        ]

    async def test_reemplaza_la_semana_completa(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """La jornada partida son dos filas en el mismo dia, no una con un hueco.

        No existe el concepto de descanso dentro de una ventana (§11):
        09-12 y 16-20 son dos ventanas, y el orden lo define el
        backend, no el orden en que llegaron.
        """
        cuerpo = await _alta(http_client)
        await _poner_tenant(session, uuid.UUID(str(cuerpo["business_id"])))
        token = await _login(http_client)

        respuesta = await http_client.put(
            URL_HORARIOS,
            json={
                "dias": [
                    {
                        "weekday": 0,
                        "windows": [
                            {"start": "16:00", "end": "20:00"},
                            {"start": "09:00", "end": "12:00"},
                        ],
                    },
                    {"weekday": 1, "windows": [{"start": "09:00", "end": "13:00"}]},
                ]
            },
            headers=_auth(token),
        )

        assert respuesta.status_code == 200, respuesta.text
        dias = {d["weekday"]: d["windows"] for d in respuesta.json()["dias"]}
        # Ordenadas por inicio, no por orden de llegada.
        assert dias[0] == [
            {"start": "09:00:00", "end": "12:00:00"},
            {"start": "16:00:00", "end": "20:00:00"},
        ]
        assert dias[1] == [{"start": "09:00:00", "end": "13:00:00"}]
        # Los dias sin ventanas son dias cerrados: no hay fila "cerrado".
        assert dias[2] == []

    async def test_ventana_que_termina_antes_de_empezar_responde_422(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        cuerpo = await _alta(http_client)
        await _poner_tenant(session, uuid.UUID(str(cuerpo["business_id"])))
        token = await _login(http_client)

        respuesta = await http_client.put(
            URL_HORARIOS,
            json={
                "dias": [
                    {
                        "weekday": 0,
                        "windows": [{"start": "13:00", "end": "09:00"}],
                    }
                ]
            },
            headers=_auth(token),
        )
        assert respuesta.status_code == 422, respuesta.text


class TestHorariosDeProfesional:
    async def test_hereda_el_negocio_hasta_tener_horario_propio(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Sin filas propias, `hereda: true`: el profesional sigue el
        horario del negocio sin que nadie tenga que duplicarlo (ADR-0005).

        Cero ventanas propias no significa "no atiendo": significa
        "no tengo horario propio", y el campo explicito es lo que
        permite al panel distinguirlo.
        """
        cuerpo = await _alta(http_client)
        await _poner_tenant(session, uuid.UUID(str(cuerpo["business_id"])))
        token = await _login(http_client)

        profesional = (
            await http_client.post(
                URL_PROFESIONALES,
                json={"display_name": "Ana Perez"},
                headers=_auth(token),
            )
        ).json()
        url = f"{URL_PROFESIONALES}/{profesional['id']}/horarios"

        hereda = (await http_client.get(url, headers=_auth(token))).json()
        assert hereda["hereda"] is True

        # Horario propio: deja de heredar.
        propio = (
            await http_client.put(
                url,
                json={"dias": [{"weekday": 2, "windows": [{"start": "10:00", "end": "14:00"}]}]},
                headers=_auth(token),
            )
        ).json()
        assert propio["hereda"] is False
        assert propio["dias"][2]["windows"] == [{"start": "10:00:00", "end": "14:00:00"}]

        # Y se lee de vuelta lo mismo.
        lectura = (await http_client.get(url, headers=_auth(token))).json()
        assert lectura["hereda"] is False

    async def test_ventanas_solapadas_en_el_horario_propio_responden_422(
        self, http_client: AsyncClient, session: AsyncSession
    ) -> None:
        """La misma regla del negocio aplica al horario propio."""
        cuerpo = await _alta(http_client)
        await _poner_tenant(session, uuid.UUID(str(cuerpo["business_id"])))
        token = await _login(http_client)

        profesional = (
            await http_client.post(
                URL_PROFESIONALES,
                json={"display_name": "Ana Perez"},
                headers=_auth(token),
            )
        ).json()

        respuesta = await http_client.put(
            f"{URL_PROFESIONALES}/{profesional['id']}/horarios",
            json={
                "dias": [
                    {
                        "weekday": 0,
                        "windows": [
                            {"start": "09:00", "end": "13:00"},
                            {"start": "12:00", "end": "15:00"},
                        ],
                    }
                ]
            },
            headers=_auth(token),
        )
        assert respuesta.status_code == 422, respuesta.text
        assert "se pisan" in _mensajes_de_campo(respuesta)


class TestSolapamientoEnElServicio:
    async def test_el_servicio_rechaza_solapadas_sin_esquema_de_por_medio(
        self, session: AsyncSession, business_a: uuid.UUID
    ) -> None:
        """La garantia vive en el servicio, no solo en el esquema del router.

        El esquema ya rechaza las ventanas que se pisan; esto prueba que
        llamar al servicio directamente--un worker, un import de datos,
        otro router--recibe la misma respuesta. Si la validacion fuera
        solo del esquema, este test no podria existir y nadie lo notaria
        hasta que una llamada no-CRUD escribiera un horario imposible.
        """
        await _poner_tenant(session, business_a)
        semana = [
            HorarioDia(
                weekday=0,
                windows=[
                    Ventana(start=dt.time(9, 0), end=dt.time(13, 0)),
                    Ventana(start=dt.time(11, 0), end=dt.time(15, 0)),
                ],
            )
        ]

        with pytest.raises(ValidationError, match="se pisan"):
            await reemplazar_horarios(session, business_a, semana)
