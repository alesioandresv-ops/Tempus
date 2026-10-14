import pathlib, re

def patch_owner_definer(fp: pathlib.Path):
    t = fp.read_text(encoding="utf-8")
    orig = t
    # 0005_login_definer_role.py
    t = t.replace(
        '    op.execute(f\'ALTER FUNCTION {BUSINESS_LOGIN}(citext) OWNER TO "{DEFINER_ROLE}"\')',
        "    op.execute(f'''\n        DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{DEFINER_ROLE}') THEN EXECUTE 'ALTER FUNCTION {BUSINESS_LOGIN}(citext) OWNER TO \"{DEFINER_ROLE}\"'; END IF; END $do$;\n    ''')"
    )
    # 0017_booking_token_definer.py
    t = t.replace(
        '    op.execute(f\'ALTER FUNCTION {BOOKING_TENANT}(bytea) OWNER TO "{DEFINER_ROLE}"\')',
        "    op.execute(f'''\n        DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{DEFINER_ROLE}') THEN EXECUTE 'ALTER FUNCTION {BOOKING_TENANT}(bytea) OWNER TO \"{DEFINER_ROLE}\"'; END IF; END $do$;\n    ''')"
    )
    # 0019_mercado_pago.py
    t = t.replace(
        '    op.execute(f\'ALTER FUNCTION {RESOLVE_SIGNATURE} OWNER TO "{DEFINER_ROLE}"\')',
        "    op.execute(f'''\n        DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{DEFINER_ROLE}') THEN EXECUTE 'ALTER FUNCTION {RESOLVE_SIGNATURE} OWNER TO \"{DEFINER_ROLE}\"'; END IF; END $do$;\n    ''')"
    )
    # GRANT a definer tambien puede fallar - envolver en DO
    # 0009 ya parcheado, pero su GRANT SELECT TO definer:
    t = re.sub(
        r'op\.execute\(\s*f"GRANT SELECT \(business_id, status, scheduled_for, attempts, max_attempts\) "\s*f\'ON notification_requests TO "\{DEFINER_ROLE\}"\'\s*\)',
        'op.execute(f""" DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = \'{DEFINER_ROLE}\') THEN EXECUTE \'GRANT SELECT (business_id, status, scheduled_for, attempts, max_attempts) ON notification_requests TO "{DEFINER_ROLE}"\'; END IF; END $do$; """)',
        t, flags=re.DOTALL
    )
    if t != orig:
        fp.write_text(t, encoding="utf-8", newline="\n")
        print(f"patched {fp.name}")
        return True
    return False

for fp in pathlib.Path("backend/migrations/versions").glob("*.py"):
    patch_owner_definer(fp)

# 0014_onboarding_create_business.py y 0002 usan MIGRATION_ROLE / _MIGRATION_ROLE - esos tambien son single-role unsafe
for fp in [pathlib.Path("backend/migrations/versions/0014_onboarding_create_business.py"), pathlib.Path("backend/migrations/versions/0002_rls_triggers_grants.py")]:
    if fp.exists():
        txt = fp.read_text(encoding="utf-8")
        # Envolvemos esos OWNER TO tambien en check de existencia
        new = txt
        # 0014: ALTER FUNCTION {_FIRMA} OWNER TO {_MIGRATION_ROLE}
        if "_MIGRATION_ROLE" in txt:
            # no parcheamos si es variable local, solo si rol no existe en Render - hacemos DO con esa variable
            new = re.sub(
                r'op\.execute\(f"ALTER FUNCTION \{_FIRMA\} OWNER TO \{_MIGRATION_ROLE\}"\)',
                'op.execute(f""" DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = \\"{_MIGRATION_ROLE}\\") THEN EXECUTE \\"ALTER FUNCTION {_FIRMA} OWNER TO {_MIGRATION_ROLE}\\"; END IF; END $do$; """)',
                new
            )
            new = re.sub(
                r'op\.execute\(f"ALTER FUNCTION \{_FIRMA_DISPONIBLE\} OWNER TO \{_MIGRATION_ROLE\}"\)',
                'op.execute(f""" DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = \\"{_MIGRATION_ROLE}\\") THEN EXECUTE \\"ALTER FUNCTION {_FIRMA_DISPONIBLE} OWNER TO {_MIGRATION_ROLE}\\"; END IF; END $do$; """)',
                new
            )
        if "MIGRATION_ROLE" in txt and fp.name == "0002_rls_triggers_grants.py":
            new = re.sub(
                r'op\.execute\(f\'ALTER FUNCTION set_updated_at\(\) OWNER TO "\{MIGRATION_ROLE\}"\'\)',
                'op.execute(f""" DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = \\"{MIGRATION_ROLE}\\") THEN EXECUTE \\"ALTER FUNCTION set_updated_at() OWNER TO \'{MIGRATION_ROLE}\\'\\"; END IF; END $do$; """)',
                new
            )
        if new != txt:
            fp.write_text(new, encoding="utf-8", newline="\n")
            print(f"patched extra {fp.name}")
