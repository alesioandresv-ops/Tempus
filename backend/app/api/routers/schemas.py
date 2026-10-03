"""Schemas Pydantic para el router de autenticación."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    """Credenciales de login: email + password."""

    email: EmailStr
    password: str


__all__ = ["LoginRequest"]
