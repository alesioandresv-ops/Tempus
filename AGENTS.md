# AGENTS.md

Instrucciones compactas para trabajar en este repositorio. Solo información específica del repositorio que probablemente se pasaría por alto sin ayuda.

## Estructura del proyecto (alta señal)
- **Monolito modular** por dominios: `backend/app/modules/{auth,businesses,bookings,customers,professionals,services,schedules,availability,notifications}`, `core/`, `api/`, `db/`, `workers/`, `integrations/`.
- **Puntos de entrada**: API en `backend/app/main.py` (`app.main:app`). El entorno de Alembic en `backend/migrations/env.py` inyecta `DATABASE_MIGRATION_URL`.
- **Migraciones numeradas manualmente** (`0000_…`, `0001_…`, …). No se usan nombres con timestamp (el orden debe coincidir). El hook post-escritura de Alembic formatea las nuevas migraciones con `ruff` como módulo (`ruff_format.type = module`) en `backend/alembic.ini` — esto es una particularidad de Windows/PATH; no cambies el hook a `console_scripts`.
- **Monorepo**: `backend/` (FastAPI, Python 3.13), `frontend/` (Vite + React + TS), `infra/postgres/init/` (bootstrap de roles/DDL), `docs/adr/` son fuente de verdad para las decisiones.

## Configuración requerida y roles de BD
- **BD de desarrollo**: `docker compose up -d` (Postgres 18; el compose expone el puerto host `${POSTGRES_PORT:-5433}`). Dos roles/bases se crean con `infra/postgres/init/*.sql`.
- **Roles app vs migraciones**: la app usa `DATABASE_URL` (rol `tempus_app`, con RLS aplicado). Migraciones/siembra con DDL usan `DATABASE_MIGRATION_URL` (rol `tempus_owner`, también bajo RLS en tests pero con permisos DDL). Nunca uses el mismo rol para servir requests y para cambios de esquema.
- **Preparación BD de tests**: ejecuta `cd backend && python preparar_base_test.py` para crear `tempus_test` y escribir `backend/.env.testing`. `conftest` carga `.env.testing` pero **nunca sobreescribe** variables ya definidas (gana lo que esté en el shell). Ver comentarios en `conftest` sobre esto.
- **Guardia crítica**: la sesión de tests fuerza `DATABASE_URL = TEST_DATABASE_URL` (si está definido) para evitar que tests de integración escriban en la BD de desarrollo. Los tests de integración/tenancy/concurrency requieren Postgres y se **saltan** (no fallan) si `TEST_DATABASE_URL` no está definido.

## Comandos exactos (no obvios)
- **Instalar (dev)**: `cd backend && python -m venv .venv && .venv\Scripts\pip install -e ".[dev]"` (Windows). Linux/macOS según README.
- **Configurar**: `cp ../.env.example .env` en `backend/` (`.env.example` está protegido por un test).
- **Migrar**: `cd backend && python -m alembic upgrade head`.
- **Ejecutar API**: `cd backend && python -m uvicorn app.main:app --reload` (local). Detrás de proxy usar `--forwarded-allow-ips='<ip-del-proxy>'` (el rate limiting usa `request.client.host`, no lee cabeceras de IP confiables por defecto).
- **Lint/formato**: `cd backend && python -m ruff format --check . && python -m ruff check .` (CI los ejecuta).
- **Typecheck (gate crítico)**: `cd backend && python -m mypy app/core app/modules/auth` (el chequeo completo de `app tests` falla actualmente; CI solo revisa paths críticos).
- **Tests**: `cd backend && python -m pytest` (1184). Necesita Postgres para integration/tenancy/concurrency; usa `preparar_base_test.py` o define `TEST_DATABASE_URL` y `DATABASE_MIGRATION_URL`. Marcadores: `unit`, `integration`, `concurrency`, `tenancy`, `slow`. Excluir slow con `-m 'not slow'`.
- **Frontend**: `cd frontend && npm install && npm run dev` (Vite) / `npm run build` (CI lo ejecuta en `deploy-check`).

## Trampas de tests y configuración (a preservar)
- **No SQLite en tests** (impuesto por config/arquitectura). Siempre Postgres para aislamiento transversal.
- **Límite de RLS**: crear un tenant (`businesses`) es onboarding con privilegios DDL; en tests `tempus_app` no puede hacer INSERT en `businesses` — los tests siembran el negocio vía rol de migraciones (ver comentarios de `BUSINESS_*` en `conftest`). No eludas RLS para simplificar tests.
- **Migración irreversible**: `0003_auth_roles` es un piso duro (no se puede hacer downgrade por debajo sin romper). CI comprueba explícitamente que el downgrade de 0003 a 0002 falla y que se recupera.
- **Orden de teardown en CI**: `docker compose down -v` se ejecuta al final de cada job (no antes de setup/uso). Este orden está protegido por `backend/tests/unit/test_ci_workflow.py`; no reordenes el teardown antes de migraciones/tests.
- **Validación estricta de config**: `JWT_SECRET_KEY` >= 32 bytes (UTF-8); `ENCRYPTION_KEY` debe decodificarse con base64url a exactamente 32 bytes (Fernet). Placeholders en `PLACEHOLDER_SECRETS` se rechazan fuera de `local` (y en entornos tipo prod). `default_timezone` debe ser IANA válido (falla rápido). Listas aceptan CSV/JSON vía validador personalizado (no asumir solo decode JSON de pydantic-settings).
- **tzdata requerido en Windows** (dependencia explícita) — su ausencia rompe la validación de timezone al arrancar.
- **Rate limiting**: usa `request.client.host`; detrás de proxies definir `FORWARDED_ALLOW_IPS` (o `--forwarded-allow-ips` de uvicorn). Mal configurado colapsa todos los usuarios en un mismo bucket.
- **IDs fijos en datos de test**: UUID estilo v7 fijos (p.ej. `BUSINESS_A` empieza 111…-7111…) para reproducibilidad; preservar semántica al tocar fixtures.
- **Seed de negocios para rutas públicas**: las rutas públicas (`/public/bookings`, `/public/businesses/{slug}/availability`) resuelven el negocio solo si `businesses.status = 'active'` (default de la columna: `trial`, que no se publica). Los seeds de integración deben sembrar `status='active'`, no el default. Y un upsert con `ON CONFLICT DO UPDATE` sobre `businesses` cae en la política `businesses_write` (exige GUC = id del negocio): fijar `app.current_business_id` **antes** de la sentencia, en el mismo `begin()`.
- **Limpieza de sobras committed por la ruta pública**: `/public/bookings` commitea con sesión propia (no la del test), así los turnos creados persisten entre corridas y chocan con la EXCLUDE en la siguiente. Limpiar `bookings`/`customers` del negocio en el **setup** del fixture (en teardown aún hay locks del test sin deshacer y el `DELETE` espera hasta el timeout).
- **`pytest-flask` global**: si está instalado en el intérprete (no es dependencia del proyecto) inyecta un fixture `autouse` que hace `app.response_class` y mata decenas de tests en el setup con `AttributeError`; neutralizado con `-p no:flask` en `addopts` (ver comentario en `pyproject.toml`).

## Scripts de verificación (notas operativas)
- `backend/verificar_limites.py`: prueba los 5 límites (§10.5) contra un servidor real en ejecución (no en memoria). Ventana por teléfono 1 hora — ejecutarlo dos veces con el mismo número puede llenar bucket (genera un teléfono nuevo por corrida). Definir `TEMPUS_PORT` si no es puerto por defecto.
- `backend/verificar_produccion.py`: lee catálogo de PostgreSQL (no código) para verificar endurecimiento de producción (`rate_limit_hit` INVOKER, `search_path` sin `pg_temp`, tokens, enmascarado de secretos). Usa `DATABASE_MIGRATION_URL`.
- `backend/preparar_base_test.py`: escribe `backend/.env.testing` usado por conftest para habilitar suite completa localmente.

## Qué cambiar / qué no cambiar
- **Preferir `edit` sobre reescritura ciega** si cambia `AGENTS.md`. Preservar orientación verificada, borrar fluff/obsoleto, reconciliar con código actual.
- **Confiar en fuentes ejecutables** (pyproject.toml, conftest.py, alembic.ini, .github/workflows/ci.yml, docker-compose.yml, config.py) sobre prosa si hay conflicto.
- **Agregar solo contenido específico del repo y alta señal**: comandos/atajos que es probable adivinar mal, arquitectura/convenios no obvios diferentes a defaults, quirks de entorno o gotchas operativos. No consejos genéricos, tutoriales, convenciones obvias ni afirmaciones especulativas.
- **Referencias relevantes**: README §Verificación/CI, ARCHITECTURE.md (mapa a entregables §51), `docs/adr/`, `.env.example` (cubierto por test).