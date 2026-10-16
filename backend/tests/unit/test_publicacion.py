"""La regla de publicacion de un negocio, en un solo lugar.

Estos tests son unitarios a proposito: no tocan Postgres. La regla es aritmetica de
fechas y plan, y probarla contra la base solo agrega ruido (seed, RLS, limpieza) a
algo que es una funcion pura. La parte de "el endpoint publico responde 404 cuando
no es publicable" si es de integracion y vive en `test_availability_public.py`.

El caso que mas pesa es el borde del mes gratis: dos dias de diferencia deciden si
un negocio sigue tomando reservas o deja de existir para sus clientes, y un error de
un dia ahi no lo nota ninguna prueba de camino feliz.
"""

from __future__ import annotations

import datetime as dt
import types as ts

import pytest
from app.models.enums import BusinessPlan, BusinessStatus
from app.modules.businesses import publicacion
from app.modules.businesses.models import Business

EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)


def _negocio(
    *,
    status: BusinessStatus = BusinessStatus.ACTIVE,
    plan: BusinessPlan = BusinessPlan.FREE,
    created_at: dt.datetime | None = None,
) -> Business:
    """Un `Business` sin base de datos: solo los campos que lee la regla."""
    return Business(
        slug="negocio-de-prueba",
        name="Negocio de prueba",
        status=status,
        plan=plan,
        created_at=created_at or dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )


@pytest.fixture
def ajustes(monkeypatch: pytest.MonkeyPatch):
    """Inyecta `trial_dias` y `trial_grace_from` sin depender del entorno."""

    def _con(*, trial_dias: int = 30, trial_grace_from: dt.datetime = EPOCH) -> None:
        fake = ts.SimpleNamespace(trial_dias=trial_dias, trial_grace_from=trial_grace_from)
        monkeypatch.setattr(publicacion, "get_settings", lambda: fake)

    return _con


def test_un_plan_pro_es_publicable_siempre(ajustes) -> None:
    """El plan Pro manda sobre la antiguedad: un cliente que paga no vence."""
    ajustes(trial_dias=30)
    viejo = _negocio(plan=BusinessPlan.PRO, created_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC))
    assert publicacion.es_publicable(viejo) is True


def test_un_free_dentro_del_mes_es_publicable(ajustes) -> None:
    ajustes(trial_dias=30)
    negocio = _negocio(created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    assert (
        publicacion.es_publicable(negocio, momento=dt.datetime(2026, 1, 20, tzinfo=dt.UTC)) is True
    )


def test_un_free_vencido_no_es_publicable(ajustes) -> None:
    ajustes(trial_dias=30)
    negocio = _negocio(created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    assert (
        publicacion.es_publicable(negocio, momento=dt.datetime(2026, 2, 1, tzinfo=dt.UTC)) is False
    )


def test_el_borde_es_estricto(ajustes) -> None:
    """Justo en el limite ya no es publicable: `<` y no `<=`, sin ambiguedad de 1 dia."""
    ajustes(trial_dias=30)
    negocio = _negocio(created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    limite = dt.datetime(2026, 1, 31, tzinfo=dt.UTC)
    assert publicacion.es_publicable(negocio, momento=limite) is False
    assert publicacion.es_publicable(negocio, momento=limite - dt.timedelta(seconds=1)) is True


def test_un_negocio_suspendido_no_es_publicable_aunque_sea_pro(ajustes) -> None:
    """`status` es estado administrativo: suspender gana sobre el plan Pro."""
    ajustes()
    negocio = _negocio(status=BusinessStatus.SUSPENDED, plan=BusinessPlan.PRO)
    assert publicacion.es_publicable(negocio) is False


def test_un_negocio_trial_no_es_publicable(ajustes) -> None:
    """Un `status` distinto de `active` no publica, sin importar la antiguedad."""
    ajustes()
    negocio = _negocio(status=BusinessStatus.TRIAL)
    assert publicacion.es_publicable(negocio) is False


def test_la_gracia_es_un_piso_y_no_un_techo(ajustes) -> None:
    """Un negocio anterior al piso arranca su mes en el piso, no en su `created_at`."""
    piso = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
    ajustes(trial_dias=30, trial_grace_from=piso)
    viejo = _negocio(created_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC))
    assert publicacion.inicio_del_trial(viejo) == piso
    assert publicacion.es_publicable(viejo, momento=dt.datetime(2026, 3, 20, tzinfo=dt.UTC)) is True
    assert (
        publicacion.es_publicable(viejo, momento=dt.datetime(2026, 4, 15, tzinfo=dt.UTC)) is False
    )


def test_un_negocio_posterior_al_piso_cuenta_desde_su_alta(ajustes) -> None:
    """Si nacio despues del piso, `created_at` manda: el `max` no es caprichoso."""
    piso = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
    ajustes(trial_dias=30, trial_grace_from=piso)
    nuevo = _negocio(created_at=dt.datetime(2026, 5, 1, tzinfo=dt.UTC))
    assert publicacion.inicio_del_trial(nuevo) == nuevo.created_at


def test_dias_restantes_cuenta_y_no_es_negativo(ajustes) -> None:
    ajustes(trial_dias=30)
    negocio = _negocio(created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    assert (
        publicacion.dias_restantes(negocio, momento=dt.datetime(2026, 1, 21, tzinfo=dt.UTC)) == 10
    )
    assert publicacion.dias_restantes(negocio, momento=dt.datetime(2026, 6, 1, tzinfo=dt.UTC)) == 0


def test_mes_gratis_vencido_para_pro_siempre_falso(ajustes) -> None:
    ajustes(trial_dias=30)
    viejo = _negocio(plan=BusinessPlan.PRO, created_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC))
    assert (
        publicacion.mes_gratis_vencido(viejo, momento=dt.datetime(2030, 1, 1, tzinfo=dt.UTC))
        is False
    )


def test_mes_gratis_vencido_free_dentro_de_mes_falso(ajustes) -> None:
    ajustes(trial_dias=30)
    negocio = _negocio(created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    assert (
        publicacion.mes_gratis_vencido(negocio, momento=dt.datetime(2026, 1, 20, tzinfo=dt.UTC))
        is False
    )


def test_mes_gratis_vencido_free_vencido_verdadero(ajustes) -> None:
    ajustes(trial_dias=30)
    negocio = _negocio(created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    assert (
        publicacion.mes_gratis_vencido(negocio, momento=dt.datetime(2026, 2, 1, tzinfo=dt.UTC))
        is True
    )


def test_mes_gratis_vencido_borde_estricto(ajustes) -> None:
    ajustes(trial_dias=30)
    negocio = _negocio(created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    limite = dt.datetime(2026, 1, 31, tzinfo=dt.UTC)
    assert publicacion.mes_gratis_vencido(negocio, momento=limite) is True
    assert (
        publicacion.mes_gratis_vencido(negocio, momento=limite - dt.timedelta(seconds=1)) is False
    )
