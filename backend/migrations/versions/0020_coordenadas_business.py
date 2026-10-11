"""Fase 9.2 — Columnas de coordenadas en businesses.

Suma dos columnas float nullable: `latitude` y `longitude`.
Sirven para el mapa arrastrable con MapLibre GL JS en el panel.

Migración 0020, numerada manualmente tras 0019.
"""

from __future__ import annotations

from collections.abc import Sequence


revision: str = "0020_coordenadas_business_v2"
down_revision: str | None = "0019_mercado_pago"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
