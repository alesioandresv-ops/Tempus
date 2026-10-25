"""Administracion de plataforma.

El panel web (`/admin`) y el endpoint legacy (`/api/v1/admin`) comparten este
modulo. No es un dominio de negocio: no tiene `business_id` obligatorio ni RLS.
La auditoria vive aca porque es informacion de la plataforma, no de un tenant.
"""

from __future__ import annotations

__all__: list[str] = []
