import os
from alembic import op
import sqlalchemy as sa

APP_ROLE = os.getenv("DB_APP_ROLE", "tempus_app")
MIGRATOR_ROLE = os.getenv("DB_MIGRATOR_ROLE", "tempus_migrator")
LOGIN_DEFINER = os.getenv("DB_LOGIN_DEFINER_ROLE", "tempus_login_definer")
BOOKING_DEFINER = os.getenv("DB_BOOKING_DEFINER_ROLE", "tempus_booking_definer")

def _role_exists(role_name: str) -> bool:
    try:
        res = op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role_name})
        return res.first() is not None
    except Exception:
        return False

def _safe_execute(sql: str, skip_if_missing_role: str = None):
    if skip_if_missing_role and not _role_exists(skip_if_missing_role):
        print(f"[roles_definer.py] Skipping: role {skip_if_missing_role} not exists -> {sql[:80]}")
        return
    try:
        op.execute(sql)
    except Exception as e:
        if "does not exist" in str(e) and "role" in str(e).lower():
            print(f"[roles_definer.py] Skipped (role missing): {sql[:120]} -> {e}")
        else:
            raise

def membresia_temporal_al_definer():
    # En single-role env (Render) no necesitamos SET ROLE, ya somos owner
    # Solo intentar si el rol existe
    if _role_exists(MIGRATOR_ROLE):
        try:
            op.execute(f'SET ROLE "{MIGRATOR_ROLE}"')
        except Exception as e:
            print(f"[roles_definer.py] Skip SET ROLE {MIGRATOR_ROLE}: {e}")
    else:
        print(f"[roles_definer.py] Skip SET ROLE {MIGRATOR_ROLE} - not in single-role DB")

def reset_role():
    try:
        op.execute("RESET ROLE")
    except Exception:
        pass

def grant_to_app(sql_template: str):
    # Template espera {APP_ROLE}
    if _role_exists(APP_ROLE):
        _safe_execute(sql_template.format(APP_ROLE=APP_ROLE), APP_ROLE)
    else:
        # En single-role, grant a CURRENT_USER
        sql = sql_template.format(APP_ROLE="CURRENT_USER").replace(f'"{APP_ROLE}"', 'CURRENT_USER').replace(f"'{APP_ROLE}'", 'CURRENT_USER')
        # si aún tiene placeholder de rol inexistente, grant a current_user
        if "tempus_app" in sql.lower():
            print(f"[roles_definer.py] Converting grant to CURRENT_USER: {sql[:100]}")
            # intentar grant a current_user si es posible, sino skip
            try:
                op.execute(sql)
            except Exception as e:
                print(f"[roles_definer.py] Skip grant: {e}")
        else:
            _safe_execute(sql)

def grant_to_definer(role_name: str, sql_grant: str):
    if _role_exists(role_name):
        _safe_execute(sql_grant, role_name)
    else:
        print(f"[roles_definer.py] Skipping GRANT to {role_name} - role does not exist (single-role env)")