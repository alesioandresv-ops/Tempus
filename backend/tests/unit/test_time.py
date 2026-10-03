"""La hora local del negocio es la que decide el dia del turno.

Dos instantes con el mismo instante UTC pueden estar en dias distintos segun donde
este el negocio. En Buenos Aires, un turno de las 23:30 del lunes es del **martes**
en UTC, y el martes no le corresponde el horario del lunes. Por eso existe este
modulo y por eso `bookings.local_date` se calcula aca y no en el codigo de
disponibilidad.

Estos tests fijan el contrato: `now()` es UTC, `to_local` no muta el instante, y
`local_date` cruza el dia exactamente cuando tiene que cruzar.

El reloj es inyectable a proposito. Un test de timezone que depende de
`datetime.now()` es un test que falla una vez al anio, en el dia del cambio de
horario, y que nadie sabe reproducir.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from app.core import time as tiempo

BUENOS_AIRES = ZoneInfo("America/Argentina/Buenos_Aires")
#: Zona sin desfase y sin horario de verano, para las aserciones que necesitan una
#: referencia estable. Argentina si tiene DST, y esa complication se prueba aparte.
#: (Lima es UTC-5, no UTC+0: sirve para probar el otro sentido, no este.)
UTC_FIJA = ZoneInfo("UTC")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reloj_limpio() -> object:
    """Devuelve el reloj real al final del test, pase lo que pase.

    Sin este autouse, un test que congela el reloj y falla antes del restore deja
    el reloj congelado para todos los que corren despues. El sintoma es un fallo
    en un test que no toca el reloj, tres archivos mas abajo, y la causa real esta a
    la vista de nadie.
    """
    tiempo.reset_clock()
    yield
    tiempo.reset_clock()


class TestNow:
    def test_devuelve_un_instante_con_timezone(self) -> None:
        """Un datetime naive no sabe que hora es, y no se puede ordenar bien."""
        momento = tiempo.now()
        assert momento.tzinfo is not None
        assert momento.utcoffset() is not None

    def test_devuelve_utc(self) -> None:
        """`now()` es UTC siempre; la conversion a local es explicita.

        Si `now()` devolviera hora local, cada `datetime.now()` olvidado en el
        codigo seria un bug de timezone, y ninguno de esos falla en desarrollo
        porque el servidor esta en la zona del negocio.
        """
        assert tiempo.now().tzinfo is dt.UTC

    def test_el_reloj_inyectado_se_usa(self) -> None:
        fijo = dt.datetime(2026, 3, 14, 15, 9, 26, tzinfo=dt.UTC)
        tiempo.set_clock(lambda: fijo)
        assert tiempo.now() == fijo


class TestReset:
    def test_reset_devuelve_el_reloj_real(self) -> None:
        """Sin esto, un test contaminaria a todos los que lo siguen."""
        fijo = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)
        tiempo.set_clock(lambda: fijo)
        assert tiempo.now() == fijo
        tiempo.reset_clock()
        assert tiempo.now().year >= 2026


class TestFreeze:
    def test_congela_en_utc(self) -> None:
        momento = dt.datetime(2026, 6, 15, 12, 0, tzinfo=dt.UTC)
        tiempo.freeze_clock(momento)
        assert tiempo.now() == momento

    def test_normaliza_a_utc_un_instante_de_otra_zona(self) -> None:
        """Congelar con hora local tiene que guardarse como UTC.

        Si se guardara tal cual, `now().tzinfo` dejaria de ser UTC y habria dos
        representaciones del mismo instante circulando por el codigo.
        """
        momento = dt.datetime(2026, 6, 15, 9, 0, tzinfo=BUENOS_AIRES)
        tiempo.freeze_clock(momento)
        assert tiempo.now().tzinfo is dt.UTC
        assert tiempo.now().hour == 12

    def test_rechaza_un_datetime_naive(self) -> None:
        """Congelar la hora de una pared no dice nada: no tiene zona.

        El `noqa` de DTZ001 es el punto del test: la llamada sin `tzinfo` es
        deliberada, y es justo lo que `freeze_clock` tiene que rechazar.
        """
        with pytest.raises(ValueError, match="timezone"):
            tiempo.freeze_clock(dt.datetime(2026, 6, 15, 12, 0))  # noqa: DTZ001

    def test_devuelve_el_restaurador_y_no_el_reloj_anterior(self) -> None:
        """El contrato de `freeze_clock`: devuelve algo para deshacer el cambio.

        Antes devolvia el reloj previo. La diferencia no es academica: con el
        contrato viejo, el que congela tiene que guardar el valor a mano, y basta
        con que uno lo olvide para que el reloj congelado se filtre al test
        siguiente y falle con un datetime de 2026.
        """
        original = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
        tiempo.set_clock(lambda: original)

        restaurar = tiempo.freeze_clock(dt.datetime(2026, 6, 15, tzinfo=dt.UTC))
        assert tiempo.now().year == 2026

        restaurar()
        assert tiempo.now() == original

    def test_el_restaurador_es_idempotente(self) -> None:
        """Llamarlo dos veces no debe romper nada.

        Un `finally` con doble restore es un error facil de cometer y facil de
        escribir. Que sea idempotente lo hace inofensivo.
        """
        original = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
        tiempo.set_clock(lambda: original)
        restaurar = tiempo.freeze_clock(dt.datetime(2026, 6, 15, tzinfo=dt.UTC))
        restaurar()
        restaurar()
        assert tiempo.now() == original

    def test_relleno_anidado_se_restaura_en_orden(self) -> None:
        """Dos congelaciones encadenadas vuelven al punto de partida."""
        uno = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        dos = dt.datetime(2026, 2, 2, tzinfo=dt.UTC)

        restaurar_uno = tiempo.freeze_clock(uno)
        assert tiempo.now() == uno
        restaurar_dos = tiempo.freeze_clock(dos)
        assert tiempo.now() == dos
        restaurar_dos()
        assert tiempo.now() == uno
        restaurar_uno()
        assert tiempo.now().year >= 2026


class TestToLocal:
    def test_convierte_el_instante_sin_moverlo(self) -> None:
        """23:30 UTC en Buenos Aires son las 20:30 del mismo dia.

        Conversion no es traslacion: el instante es el mismo antes y despues.
        """
        momento = dt.datetime(2026, 6, 15, 23, 30, tzinfo=dt.UTC)
        local = tiempo.to_local(momento, BUENOS_AIRES)
        assert local.hour == 20
        assert local.minute == 30
        assert local == momento  # el instante es el mismo, solo cambia la vista

    def test_acepta_el_nombre_de_la_zona(self) -> None:
        momento = dt.datetime(2026, 6, 15, 23, 30, tzinfo=dt.UTC)
        assert tiempo.to_local(momento, "America/Argentina/Buenos_Aires") == tiempo.to_local(
            momento, BUENOS_AIRES
        )

    def test_una_zona_inexistente_falla_con_mensaje_util(self) -> None:
        """El error tiene que decir cual fue el valor, no solo "timezone invalido"."""
        momento = dt.datetime(2026, 6, 15, tzinfo=dt.UTC)
        with pytest.raises(Exception) as exc:
            tiempo.to_local(momento, "America/Santa_Fe")
        assert "Santa_Fe" in str(exc.value)


class TestLocalDate:
    def test_no_cruza_el_dia_dentro_del_rango_normal(self) -> None:
        momento = dt.datetime(2026, 6, 15, 12, 0, tzinfo=dt.UTC)
        assert tiempo.local_date(momento, BUENOS_AIRES) == dt.date(2026, 6, 15)

    def test_cruza_el_dia_hacia_atras_este_oeste(self) -> None:
        """El caso R-03: 23:30 UTC en Buenos Aires ya es el dia siguiente.

        En UTC son las 23:30 del lunes; en Buenos Aires (UTC-3) son las 20:30 del
        lunes tambien. Para que el bug aparezca tiene que ser al otro lado: las
        02:00 UTC del martes son las 23:00 del lunes en Buenos Aires.
        """
        momento = dt.datetime(2026, 6, 16, 2, 0, tzinfo=dt.UTC)
        assert momento.date() == dt.date(2026, 6, 16)
        assert tiempo.local_date(momento, BUENOS_AIRES) == dt.date(2026, 6, 15)

    def test_una_reserva_de_la_noche_es_del_dia_local(self) -> None:
        """23:30 hora de Buenos Aires es del dia siguiente en UTC, no al reves.

        Es el caso que mas caro sale cuando se resuelve al reves: un turno del
        lunes a las 23:30 guardado como UTC del martes, y el martes no le
        corresponde el horario del lunes.
        """
        momento = dt.datetime(2026, 6, 15, 2, 30, tzinfo=dt.UTC)  # 23:30 del 14 en BA
        assert tiempo.local_date(momento, BUENOS_AIRES) == dt.date(2026, 6, 14)

    def test_una_zona_sin_desfase_no_mueve_la_fecha(self) -> None:
        momento = dt.datetime(2026, 6, 15, 2, 0, tzinfo=dt.UTC)
        assert tiempo.local_date(momento, UTC_FIJA) == dt.date(2026, 6, 15)

    def test_el_mismo_instante_da_fechas_distintas_por_zona(self) -> None:
        """La razon de existir de `local_date`: el instante no alcanza.

        Dos negocios con la misma reserva en el mismo instante la guardan en dias
        distintos, y por eso la columna no puede derivarse del `created_at`.
        """
        momento = dt.datetime(2026, 6, 16, 2, 0, tzinfo=dt.UTC)
        assert tiempo.local_date(momento, BUENOS_AIRES) == dt.date(2026, 6, 15)
        assert tiempo.local_date(momento, UTC_FIJA) == dt.date(2026, 6, 16)


class TestNowLocal:
    def test_usa_el_reloj_inyectado(self) -> None:
        """`now_local` tiene que pasar por `now()`, no por `datetime.now()`.

        Si leyeran el reloj del sistema por su cuenta, congelar el reloj no serviria
        para probar la logica de negocio por zonas horarias, que es justamente para
        lo que existe.
        """
        momento = dt.datetime(2026, 6, 16, 2, 0, tzinfo=dt.UTC)
        tiempo.freeze_clock(momento)
        assert tiempo.now_local(BUENOS_AIRES).hour == 23
        assert tiempo.local_today(BUENOS_AIRES) == dt.date(2026, 6, 15)
