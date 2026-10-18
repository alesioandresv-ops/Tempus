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
- **Preparación BD de tests**: ejecuta `cd backend && python scripts/preparar_base_test.py` para crear `tempus_test` y escribir `backend/.env.testing`. `conftest` carga `.env.testing` pero **nunca sobreescribe** variables ya definidas (gana lo que esté en el shell). Ver comentarios en `conftest` sobre esto.
- **Guardia crítica**: la sesión de tests fuerza `DATABASE_URL = TEST_DATABASE_URL` (si está definido) para evitar que tests de integración escriban en la BD de desarrollo. Los tests de integración/tenancy/concurrency requieren Postgres y se **saltan** (no fallan) si `TEST_DATABASE_URL` no está definido.

## Comandos exactos (no obvios)
- **Instalar (dev)**: `cd backend && python -m venv .venv && .venv\Scripts\pip install -e ".[dev]"` (Windows). Linux/macOS según README.
- **Configurar**: `cp ../.env.example .env` en `backend/` (`.env.example` está protegido por un test).
- **Migrar**: `cd backend && python -m alembic upgrade head`.
- **Ejecutar API**: `cd backend && python -m uvicorn app.main:app --reload` (local). Detrás de proxy usar `--forwarded-allow-ips='<ip-del-proxy>'` (el rate limiting usa `request.client.host`, no lee cabeceras de IP confiables por defecto).
- **Lint/formato**: `cd backend && python -m ruff format --check . && python -m ruff check .` (CI los ejecuta).
- **Typecheck (gate crítico)**: `cd backend && python -m mypy app/core app/modules/auth` (el chequeo completo de `app tests` falla actualmente; CI solo revisa paths críticos).
- **Tests**: `cd backend && python -m pytest` (1944 al momento; crece por fase). Necesita Postgres para integration/tenancy/concurrency; usa `scripts/preparar_base_test.py` o define `TEST_DATABASE_URL` y `DATABASE_MIGRATION_URL`. Marcadores: `unit`, `integration`, `concurrency`, `tenancy`, `slow`. Excluir slow con `-m 'not slow'`.
- **Frontend**: `cd frontend && npm install && npm run dev` (Vite) / `npm run build` (CI lo ejecuta en `deploy-check`). Ver sección de quirks más abajo.

## Trampas de tests y configuración (a preservar)
- **No SQLite en tests** (impuesto por config/arquitectura). Siempre Postgres para aislamiento transversal.
- **Límite de RLS**: crear un tenant (`businesses`) es onboarding con privilegios DDL; en tests `tempus_app` no puede hacer INSERT en `businesses` — los tests siembran el negocio vía rol de migraciones (ver comentarios de `BUSINESS_*` en `conftest`). No eludas RLS para simplificar tests.
- **Migración irreversible**: `0003_auth_roles` es un piso duro (no se puede hacer downgrade por debajo sin romper). CI comprueba explícitamente que el downgrade de 0003 a 0002 falla y que se recupera.
- **Orden de teardown en CI**: `docker compose down -v` se ejecuta al final de cada job (no antes de setup/uso). Este orden está protegido por `backend/tests/unit/test_ci_workflow.py`; no reordenes el teardown antes de migraciones/tests.
- **Validación estricta de config**: `JWT_SECRET_KEY` >= 32 bytes (UTF-8); `ENCRYPTION_KEY` debe decodificarse con base64url a exactamente 32 bytes (Fernet). Placeholders en `PLACEHOLDER_SECRETS` se rechazan fuera de `local` (y en entornos tipo prod). `default_timezone` debe ser IANA válido (falla rápido). Listas aceptan CSV/JSON vía validador personalizado (no asumir solo decode JSON de pydantic-settings).
- **tzdata requerido en Windows** (dependencia explícita) — su ausencia rompe la validación de timezone al arrancar.
- **pgcrypto puede no existir** (AppLocker de la facu bloquea `pgcrypto.dll`): la migración `0000` tolera su ausencia (DO block con EXCEPTION, RAISE NOTICE). Nada en runtime ni en migraciones posteriores usa `gen_random_bytes` (en PG18 `gen_random_uuid()` es core), así que la ausencia es inofensiva. No reintroducir dependencias de pgcrypto en migraciones nuevas sin revisar esta trampa.
- **Rate limiting**: usa `request.client.host`; detrás de proxies definir `FORWARDED_ALLOW_IPS` (o `--forwarded-allow-ips` de uvicorn). Mal configurado colapsa todos los usuarios en un mismo bucket.
- **IDs fijos en datos de test**: UUID estilo v7 fijos (p.ej. `BUSINESS_A` empieza 111…-7111…) para reproducibilidad; preservar semántica al tocar fixtures.
- **Seed de negocios para rutas públicas**: las rutas públicas (`/public/bookings`, `/public/businesses/{slug}/availability`) resuelven el negocio solo si `publicacion.es_publicable(business)` (status `active` **y** plan Pro **o** dentro del mes gratis; el default `trial` de la columna no se publica). Los seeds de integración deben sembrar `status='active'`, no el default. Y un upsert con `ON CONFLICT DO UPDATE` sobre `businesses` cae en la política `businesses_write` (exige GUC = id del negocio): fijar `app.current_business_id` **antes** de la sentencia, en el mismo `begin()`.
- **Limpieza de sobras committed por la ruta pública**: `/public/bookings` commitea con sesión propia (no la del test), así los turnos creados persisten entre corridas y chocan con la EXCLUDE en la siguiente. Limpiar `bookings`/`customers` del negocio en el **setup** del fixture (en teardown aún hay locks del test sin deshacer y el `DELETE` espera hasta el timeout).
- **`pytest-flask` global**: si está instalado en el intérprete (no es dependencia del proyecto) inyecta un fixture `autouse` que hace `app.response_class` y mata decenas de tests en el setup con `AttributeError`; neutralizado con `-p no:flask` en `addopts` (ver comentario en `pyproject.toml`).

## Plan, publicabilidad y gating (a preservar)
- **Una sola fuente de publicabilidad**: `backend/app/modules/businesses/publicacion.py` (`es_publicable`, `mes_gratis_vencido`, `fin_del_trial`, `dias_restantes`). `businesses.status` es **estado administrativo**; que un negocio acepte reservas depende además del plan y del tiempo. El modelo (`Business.is_bookable`), el servicio (`es_negocio_reservable`) y el resolver público la llaman: **no la reimplementes**.
- **Alta activa**: `tempus_create_business` crea el negocio en `status='active'` desde la migración `0024_activar_al_alta` (antes nacía en el default `trial` y no se publicaba). Roadmap: Fase 9.2. Decisión y alternativas descartadas: `docs/adr/0017-publicabilidad-y-plan.md`.
- **Gate 402 del panel**: `backend/app/api/routers/business/gating.py::exigir_plan_vigente` cuelga del `router` de `business` y corta **escrituras** (por **método HTTP**: `POST`/`PUT`/`PATCH`/`DELETE`) con 402 y `code="plan_vencido"` cuando `publicacion.mes_gratis_vencido`. Las lecturas (`GET`/`HEAD`/`OPTIONS`) pasan siempre. `/business/suscripcion*` vive en un **`billing_router`** aparte (mismo prefijo, sin gate) para que un negocio vencido pueda pagar. Un endpoint de escritura nuevo en `router` queda gateado solo; los de facturación van a `billing_router`.
- **Config del trial**: `TRIAL_DIAS` (30) y `TRIAL_GRACE_FROM` (default epoch = contar desde `created_at`; en `.env.example` va la fecha de despliegue para que los negocios que ya existían no venzan el día 1). El corte es `max(created_at, TRIAL_GRACE_FROM) + TRIAL_DIAS`.

## Frontend (Vite + React + TS)
- **Gates locales**: `cd frontend && npx tsc --noEmit` (debe dar 0) y `npm run build` (= `tsc -b && vite build`; el `tsc -b` ya es typecheck). `npm run lint` corre eslint. CI solo ejecuta `npm install && npm run build` en el job `deploy-check`; **no hay** job de lint ni de tests de frontend.
- **Variables `VITE_*` (build-time)**: las del frontend se documentan en `frontend/.env.example` y se espejan en el root `.env.example` como campos de `Settings` (el test de `test_config.py` exige paridad exacta: cada campo de `Settings` ↔ una clave del `.env.example`, sin sobrantes). **`VITE_API_URL` es el origen del backend *sin* `/api/v1`**: el prefijo lo agrega el código (`frontend/src/services/api.ts`); si la variable ya lo trae, el frontend pega a `/api/v1/api/v1` (hay validador en `config.py` y una aserción en el test). `VITE_ROOT_DOMAIN` es un dominio (ej. `tempus.app`), no una URL. No existe `VITE_API_BASE_URL` (era un campo muerto que se eliminó).
- **e2e sin cablear**: `frontend/tests/e2e/booking.spec.ts` es Playwright, pero falta `@playwright/test` en `package.json` y no hay `playwright.config` (ni script `test`). No corre en CI; no asumas que `npx playwright test` funciona sin instalarlo.
- **MapLibre (`maplibre-gl` v6)**: no hay default export → `import { Map, Marker } from 'maplibre-gl'` + `import 'maplibre-gl/dist/maplibre-gl.css'`. `MapaComponente.tsx` usa un **style raster inline de OSM**; no uses `demotiles.maplibre.org` (bloqueado en la red de la escuela → mapa en blanco). El contenedor necesita altura explícita y hay que llamar `map.resize()` (bug de pestaña oculta). El dep-optimizer de Vite 5 no emite el worker de maplibre: solo rompería estilos vectoriales/geojson/terrain, no el raster usado.
- **`npm install` puede colgarse** (red de la facultad; pasó con `maplibre-gl` y `lucide-react`). Si un dep queda sin `.d.ts` —visto en `lucide-react`: su `typings` apunta a `dist/lucide-react.d.ts` ausente— `tsc` falla con `TS7016`; reinstalá desde una red estable o bajá el `.d.ts` de unpkg a `node_modules/<pkg>/`.

## Scripts de verificación (notas operativas)
- `backend/verificar_limites.py`: prueba los 5 límites (§10.5) contra un servidor real en ejecución (no en memoria). Ventana por teléfono 1 hora — ejecutarlo dos veces con el mismo número puede llenar bucket (genera un teléfono nuevo por corrida). Definir `TEMPUS_PORT` si no es puerto por defecto.
- `backend/verificar_produccion.py`: lee catálogo de PostgreSQL (no código) para verificar endurecimiento de producción (`rate_limit_hit` INVOKER, `search_path` sin `pg_temp`, tokens, enmascarado de secretos). Usa `DATABASE_MIGRATION_URL`.
- `backend/scripts/preparar_base_test.py`: escribe `backend/.env.testing` usado por conftest para habilitar suite completa localmente. Vive en `scripts/` pero escribe en `backend/` (el conftest lee de ahí); no moverlo sin ajustar `parents[1]`.

## Qué cambiar / qué no cambiar
- **Preferir `edit` sobre reescritura ciega** si cambia `AGENTS.md`. Preservar orientación verificada, borrar fluff/obsoleto, reconciliar con código actual.
- **Confiar en fuentes ejecutables** (pyproject.toml, conftest.py, alembic.ini, .github/workflows/ci.yml, docker-compose.yml, config.py) sobre prosa si hay conflicto.
- **Agregar solo contenido específico del repo y alta señal**: comandos/atajos que es probable adivinar mal, arquitectura/convenios no obvios diferentes a defaults, quirks de entorno o gotchas operativos. No consejos genéricos, tutoriales, convenciones obvias ni afirmaciones especulativas.
- **Referencias relevantes**: README §Verificación/CI, ARCHITECTURE.md (mapa a entregables §51), `docs/adr/`, `.env.example` (cubierto por test).