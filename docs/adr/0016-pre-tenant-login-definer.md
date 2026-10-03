# ADR-0016: Rol dedicado con BYPASSRLS para login pre-tenant

- **Estado:** Aceptada
- **Fecha:** 2026-09-29
- **Referencia:** PROJECT_MASTER §10, §25
- **Contexto en:** ARCHITECTURE.md §10.6
- **Migración:** `0005_login_definer_role`

## Contexto

La migración `0004_login_functions` introdujo dos funciones `SECURITY DEFINER` para
permitir el login pre-tenant:

- `auth_business_user_for_login(p_email citext)` — busca en `business_users`
- `auth_platform_user_for_login(p_email citext)` — busca en `platform_users`

La premisa documentada en `0004` era:

> "Una función `SECURITY DEFINER` corre como owner, saltea la RLS de esa tabla."

Sin embargo, la migración `0002_rls_triggers_grants` aplica `FORCE ROW LEVEL SECURITY`
a las 20 tablas de tenant, incluyendo `business_users`. `FORCE ROW LEVEL SECURITY`
hace que **el owner de la tabla también quede sujeto a la política**. Como la
función de `0004` era owner `tempus_owner` (que es también owner de la tabla),
la función seguía sujeta a la RLS y **no devolvía filas sin GUC**.

Verificado empíricamente:

```
dueno de la funcion = dueno de la tabla = tempus_owner
relforcerowsecurity = true
funcion sin GUC  -> 0 filas
funcion con GUC  -> 1 fila
```

## Decisión

Crear un rol dedicado `tempus_login_definer` con `BYPASSRLS`, `NOLOGIN`,
`NOINHERIT`, dueño exclusivo de `auth_business_user_for_login`, y con
`SELECT` solo en las 6 columnas que la función lee.

```sql
CREATE ROLE tempus_login_definer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS;
GRANT SELECT (id, business_id, password_hash, role, status, full_name)
  ON business_users TO tempus_login_definer;
GRANT tempus_login_definer TO tempus_owner WITH ADMIN OPTION;
ALTER FUNCTION auth_business_user_for_login(citext) OWNER TO tempus_login_definer;
```

`auth_platform_user_for_login` **no cambia**: `platform_users` no tiene RLS,
su función sigue siendo owner `tempus_owner`.

## Consecuencias

### Positivas

- El login de negocio funciona sin GUC pre-tenant.
- El bypass está acotado: un solo rol, una sola función, 6 columnas, sin escritura.
- `tempus_app` y `tempus_owner` **no** tienen `BYPASSRLS`.
- `PUBLIC` no puede ejecutar la función.
- El downgrade de la migración es posible (gracias a `WITH ADMIN OPTION`).

### Negativas / costos aceptados

- Un rol con `BYPASSRLS` existe en el cluster. Aunque acotado a 6 columnas de una
  tabla y sin login, es una superficie de riesgo que no existía antes.
- La migración `0005` no es reversible sin superusuario (el downgrade requiere
  cambiar el owner de la función, lo que solo puede hacer el owner actual o un
  superuser). El test de piso de reversibilidad usa superuser para el downgrade.
- La premisa de `0004` era incorrecta; se corrige en ARCHITECTURE.md §10.6 y este ADR.

## Alternativas consideradas

### A) Quitar FORCE RLS de `business_users`
Descarte: debilita una decisión arquitectónica deliberada de `0002`. El aislamiento
dejaría de depender solo de políticas y pasaría a depender de configuración de roles.

### B) Índice pre-tenant sin credenciales (`login_identities`)
Tabla separada `email -> (business_id, user_id)` sin RLS, sin hashes. El servicio
resuelve el tenant, pone el GUC y luego lee `business_users` por RLS normal.
Es la forma arquitectónicamente más honesta, pero añade tabla, triggers de sincronismo,
y expone la lista de membresías de un email en zona pre-tenant. Más complejo y
no justificado para el MVP.

### C) `BYPASSRLS` para `tempus_owner`
Descarte: da bypass a todo el esquema al rol de migraciones. Viola el principio de
mínimo privilegio y anula el propósito de `FORCE RLS`.

## Verificación

- Tests de catálogo (`tests/tenancy/test_login_definer.py`): 14 aserciones sobre
  rol, privilegios, ownership, ACLs.
- Test funcional (`tests/integration/test_auth_service.py::TestLoginPreTenantConDefiner`):
  login real sin GUC previo, verifica `tid` en JWT y aislamiento posterior.
- Suite completa: 465 tests pasan, 93% cobertura, ruff/mypy/alembic limpios.

## Revisar si

- Un requisito regulatorio exige aislamiento físico por tenant (entonces opción B).
- El número de tablas con bypass crece (revisar principio de mínimo privilegio).