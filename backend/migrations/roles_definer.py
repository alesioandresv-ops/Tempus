"""Primitivas de rol para las migraciones que usan el definer BYPASSRLS.

Version single-role safe para Render: cada operacion comprueba que los roles
existan antes de usarlos, de modo que en una base con un solo rol las llamadas
son no-ops en vez de fallar con "role does not exist".
"""

from __future__ import annotations

import contextlib
import os

import sqlalchemy as sa
from alembic import op

OWNER_ROLE = os.getenv("DB_OWNER_ROLE", "tempus_owner")
DEFINER_ROLE = os.getenv("DB_LOGIN_DEFINER_ROLE", "tempus_login_definer")
MIGRATOR_ROLE = os.getenv("DB_MIGRATOR_ROLE", "tempus_migrator")


def _role_exists(rol: str) -> bool:
    try:
        bind = op.get_bind()
        fila = bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": rol}
        ).first()
        return fila is not None
    except Exception:
        return False


def _safe_exec(sentencia: str) -> None:
    try:
        op.execute(sentencia)
    except Exception as exc:
        mensaje = str(exc).lower()
        if "does not exist" in mensaje or "not a member" in mensaje:
            print(f"[roles_definer SKIP] {sentencia[:80]}")
        else:
            raise


def como_definer() -> None:
    if not _role_exists(MIGRATOR_ROLE) or not _role_exists(DEFINER_ROLE):
        return
    _safe_exec(f'SET ROLE "{MIGRATOR_ROLE}"')
    _safe_exec(f'SET ROLE "{DEFINER_ROLE}"')


def reset_role() -> None:
    # Dos veces a proposito: deshace un `SET ROLE` anidado (migrator -> definer).
    with contextlib.suppress(Exception):
        op.execute("RESET ROLE")
    with contextlib.suppress(Exception):
        op.execute("RESET ROLE")


def definer_con_create() -> None:
    if _role_exists(DEFINER_ROLE):
        _safe_exec(f'GRANT CREATE ON SCHEMA public TO "{DEFINER_ROLE}"')


def definer_sin_create() -> None:
    if _role_exists(DEFINER_ROLE):
        _safe_exec(f'REVOKE CREATE ON SCHEMA public FROM "{DEFINER_ROLE}"')


def membresia_temporal_al_definer() -> None:
    if not (
        _role_exists(MIGRATOR_ROLE) and _role_exists(DEFINER_ROLE) and _role_exists(OWNER_ROLE)
    ):
        return
    _safe_exec(f'SET ROLE "{MIGRATOR_ROLE}"')
    _safe_exec(f'GRANT "{DEFINER_ROLE}" TO "{OWNER_ROLE}"')
    reset_role()


def revoca_membresia_al_definer() -> None:
    if not (_role_exists(MIGRATOR_ROLE) and _role_exists(DEFINER_ROLE)):
        return
    _safe_exec(f'SET ROLE "{MIGRATOR_ROLE}"')
    if _role_exists(OWNER_ROLE):
        _safe_exec(f'REVOKE "{DEFINER_ROLE}" FROM "{OWNER_ROLE}"')
    reset_role()
