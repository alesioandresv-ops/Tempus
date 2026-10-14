"""Primitivas de rol para las migraciones que usan el definer BYPASSRLS.
Version single-role safe para Render
"""

from __future__ import annotations
import os
from alembic import op
import sqlalchemy as sa

OWNER_ROLE = os.getenv("DB_OWNER_ROLE", "tempus_owner")
DEFINER_ROLE = os.getenv("DB_LOGIN_DEFINER_ROLE", "tempus_login_definer")
MIGRATOR_ROLE = os.getenv("DB_MIGRATOR_ROLE", "tempus_migrator")

def _role_exists(r):
    try:
        b = op.get_bind()
        return b.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": r}).first() is not None
    except:
        return False

def _safe_exec(s):
    try:
        op.execute(s)
    except Exception as e:
        m = str(e).lower()
        if "does not exist" in m or "not a member" in m:
            print(f"[roles_definer SKIP] {s[:80]}")
        else:
            raise

def como_definer():
    if not _role_exists(MIGRATOR_ROLE) or not _role_exists(DEFINER_ROLE):
        return
    _safe_exec(f'SET ROLE "{MIGRATOR_ROLE}"')
    _safe_exec(f'SET ROLE "{DEFINER_ROLE}"')

def reset_role():
    try: op.execute("RESET ROLE")
    except: pass
    try: op.execute("RESET ROLE")
    except: pass

def definer_con_create():
    if _role_exists(DEFINER_ROLE):
        _safe_exec(f'GRANT CREATE ON SCHEMA public TO "{DEFINER_ROLE}"')

def definer_sin_create():
    if _role_exists(DEFINER_ROLE):
        _safe_exec(f'REVOKE CREATE ON SCHEMA public FROM "{DEFINER_ROLE}"')

def membresia_temporal_al_definer():
    if not (_role_exists(MIGRATOR_ROLE) and _role_exists(DEFINER_ROLE) and _role_exists(OWNER_ROLE)):
        return
    _safe_exec(f'SET ROLE "{MIGRATOR_ROLE}"')
    _safe_exec(f'GRANT "{DEFINER_ROLE}" TO "{OWNER_ROLE}"')
    reset_role()

def revoca_membresia_al_definer():
    if not (_role_exists(MIGRATOR_ROLE) and _role_exists(DEFINER_ROLE)):
        return
    _safe_exec(f'SET ROLE "{MIGRATOR_ROLE}"')
    if _role_exists(OWNER_ROLE):
        _safe_exec(f'REVOKE "{DEFINER_ROLE}" FROM "{OWNER_ROLE}"')
    reset_role()
