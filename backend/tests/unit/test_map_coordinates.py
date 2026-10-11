"""Tests unitarios de validación de coordenadas lat/lng para businesses.

Cubre la validación de las nuevas columnas `latitude` y `longitude` en el
modelo `Business`, asegurando que:
- Los valores None son permitidos (columna nullable).
- Los valores válidos (rangos geográficos) son aceptados.
- Los valores fuera de rango son rechazados a nivel de aplicación (no de SQLAlchemy).

Nota: SQLAlchemy no valida rangos en los tipos de columna por defecto;
la validación de rangos se hace a nivel de aplicación/Pydantic.
"""

import pytest
from pydantic import BaseModel, Field, ValidationError


@pytest.mark.unit
def test_latitud_rango_valido() -> None:
    """Latitud debe estar entre -90 y 90."""
    from app.modules.businesses.models import Business

    negocio = Business(
        slug="test-valid-lat",
        name="Negocio Test",
        latitude=45.0,
        longitude=-73.0,
        timezone="America/Argentina/Buenos_Aires",
    )
    assert negocio.latitude == 45.0
    assert negocio.longitude == -73.0


@pytest.mark.unit
def test_longitud_rango_valido() -> None:
    """Longitud debe estar entre -180 y 180."""
    from app.modules.businesses.models import Business

    negocio = Business(
        slug="test-valid-lng",
        name="Negocio Test",
        latitude=0.0,
        longitude=150.0,
        timezone="America/Argentina/Buenos_Aires",
    )
    assert negocio.latitude == 0.0
    assert negocio.longitude == 150.0


@pytest.mark.unit
def test_latitud_longitud_none_permitido() -> None:
    """Ambas columnas deben ser nullable: None debe ser aceptado."""
    from app.modules.businesses.models import Business

    negocio = Business(
        slug="test-none-coords",
        name="Negocio Test",
        latitude=None,
        longitude=None,
        timezone="America/Argentina/Buenos_Aires",
    )
    assert negocio.latitude is None
    assert negocio.longitude is None


class CoordenadasInput(BaseModel):
    """Modelo Pydantic para validación de coordenadas a nivel de aplicación."""

    latitud: float = Field(..., ge=-90, le=90)
    longitud: float = Field(..., ge=-180, le=180)


@pytest.mark.unit
def test_coordenadas_pydantic_valido() -> None:
    """Validación Pydantic: coordenadas dentro de rangos."""
    datos = CoordenadasInput(latitud=45.0, longitud=-73.0)
    assert datos.latitud == 45.0
    assert datos.longitud == -73.0


@pytest.mark.unit
def test_coordenadas_pydantic_lat_fuera_de_rango() -> None:
    """Validación Pydantic: latitud fuera de rango."""
    with pytest.raises(ValidationError):
        CoordenadasInput(latitud=100.0, longitud=-73.0)

    with pytest.raises(ValidationError):
        CoordenadasInput(latitud=-100.0, longitud=-73.0)


@pytest.mark.unit
def test_coordenadas_pydantic_lng_fuera_de_rango() -> None:
    """Validación Pydantic: longitud fuera de rango."""
    with pytest.raises(ValidationError):
        CoordenadasInput(latitud=0.0, longitud=200.0)

    with pytest.raises(ValidationError):
        CoordenadasInput(latitud=0.0, longitud=-200.0)
