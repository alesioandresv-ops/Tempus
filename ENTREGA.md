# ENTREGA — Tempus v1.0.0-rc1

Fecha: 2026-10-03. Estado: **release candidate**, no producción.

Este documento es el checklist de lo que está verificado, cómo se verifica, y —con
la misma precisión— lo que **no** está verificado. La segunda parte es la que
importa: un checklist que solo lista lo que funciona no sirve para decidir.

---

## 1. Verificado en esta máquina

| Qué | Resultado | Cómo se comprueba |
|---|---|---|
| Suite completa | **1184 passed, 0 failed, 0 skipped** (766 s) | `cd backend; python -m pytest` |
| Formato | 143 archivos, sin diferencias | `python -m ruff format --check .` |
| Lint | sin errores en todo el repo | `python -m ruff check .` |
| Tipos (crítico) | 0 errores en 11 archivos | `python -m mypy app/core app/modules/auth` |
| Rate limiting §10.5 | 5/5 clases, contra uvicorn real | `python verificar_limites.py` |
| Flujo público E2E | 42 comprobaciones | `python diagnostico_bugs.py` |
| Endurecimiento de producción | 12/12, leyendo `pg_proc` | `python verificar_produccion.py` |
| Migraciones | 1 head, `upgrade head` idempotente ×3 | ver abajo |
| Higiene de archivos | sin BOM, sin mojibake, YAML válido | ver abajo |

### Corrección sobre el número de tests

Antes de esta entrega se informaban **1337 tests**. El número real es **1049**.

La diferencia —288— no era cobertura perdida ni tests rotos: era
`tests/unit/test_fuentes_limpias.py`, que genera **4 comprobaciones de higiene por
cada `.py` del repositorio** (BOM, tabulaciones, caracteres de reemplazo,
final de línea). `backend/scratch/` contenía 72 scripts de depuración descartables, y
sus 288 comprobaciones de higiene contaban como tests.

Borrar `scratch/` eliminó 288 casos de higiene sobre código que nadie iba a leer.
El código real que se Borra no tenía tests: se leían como tests.

> Los 1049 están verificados contra `tempus_test` en PostgreSQL 18. Cero tests
> omitidos: los de RLS, la restricción `EXCLUDE` y los de concurrencia **corrieron**.

**Actualizado 2026-10-06:** las fases 7-10 (onboarding, panel, WhatsApp/avatar y
notificaciones) agregaron tests y hoy la suite es **1184 passed, 0 failed, 0 skipped**
(`python -m pytest`, 766 s), incluidos los 2 tests de concurrencia sobre los cubos de
rate limit de §7.6 y las comprobaciones de higiene sobre los archivos actuales.

---

## 2. Migraciones

- **Head único:** `0016_fase4_whatsapp_avatar`. No hay ramas.
- **Idempotencia verificada:** tres `alembic upgrade head` seguidos → código de
  salida `0 0 0`, versión sin cambios, ninguna migración aplicada.
- `docker-entrypoint.sh` corre `alembic upgrade head` en cada arranque, así que no
  existe el despliegue en el que la app arranca contra un esquema viejo.
- **Sin `DATABASE_MIGRATION_URL` las migraciones se saltan, con dos líneas de aviso
  en el log.** Es deliberado: el proceso web no debe llevar el rol de DDL.

---

## 3. Variables de entorno requeridas

Las cinco sin las cuales la app **no arranca** (validadores en `app/core/config.py`):

```bash
DATABASE_URL=postgresql+asyncpg://tempus_app:<pw>@host:5432/tempus
DATABASE_MIGRATION_URL=postgresql+asyncpg://tempus_owner:<pw>@host:5432/tempus
JWT_SECRET_KEY=$(openssl rand -hex 32)
ENCRYPTION_KEY=$(openssl rand -base64 32)     # 32 bytes exactos; la app valida
SCHEDULER_TICK_SECRET=$(openssl rand -hex 32)
```

Además, para que funcionen las funciones de Meta/WhatsApp (todas opcionales en
`Settings`, vacías ⇒ la función se apaga en vez de fallar):

```bash
META_APP_ID, META_APP_SECRET, META_WEBHOOK_VERIFY_TOKEN
TURNSTILE_SECRET, TURNSTILE_SITE_KEY      # bot de calendario público
FORWARDED_ALLOW_IPS                       # ver §6
```

Catálogo completo comentado: `.env.example` (84 variables). `.env` y `.env.testing`
están en `.gitignore` y **nunca** se comitean.

> Las contraseñas de desarrollo (`tempus_app_dev_pw`, `tempus_owner_dev_pw`) siguen
> apareciendo como **valores por defecto** en `docker-compose.yml` y en
> `.github/workflows/ci.yml`. Son de una base local. El compose las hace
> sobrescribibles con `${POSTGRES_PASSWORD:-...}`, así que un despliegue real nunca
> las usa. Cambiar las de esta máquina está fuera del alcance de esta entrega.

---

## 4. Puertos

| Servicio | Puerto | Nota |
|---|---|---|
| API (uvicorn, sesión de desarrollo) | **8002** | el que está levantando el usuario |
| Frontend (Vite) | **5173** | |
| API dentro de compose | 8000 | `BACKEND_PORT` lo overridea |
| PostgreSQL (compose, publicado al host) | 5433 | `POSTGRES_PORT` lo overridea |
| PostgreSQL (instalación local) | 5432 | la que usa `backend/.env` |

---

## 5. Cómo correr los tests

```bash
cd backend
python preparar_base_test.py       # crea tempus_test y escribe .env.testing
python -m pytest
```

`preparar_base_test.py` es la vía facil: el `conftest.py` carga `backend/.env.testing`
solo. A mano, exportá `TEST_DATABASE_URL` y `DATABASE_MIGRATION_URL` apuntando a
`tempus_test`.

**No corras dos pytest a la vez.** Las fixtures de scope sesión comparten el esquema
`tempus_test`; dos procesos simultáneos se pisan y producen fallos que no existen.

---

## 6. Rate limiting y proxies — lo que hay que configurar en el despliegue

Los cinco límites de §10.5 son dependencias de FastAPI, no middleware, para que
puedan pedir un `Principal` y para que `/health` no los atraviese.

| Límite | Valor |
|---|---|
| Login por IP | 5 / min |
| Login por email | 10 / min |
| Reserva pública por IP | 10 / min |
| Reserva pública por teléfono | 5 / hora |
| Router público | 120 / min |
| Router del panel | 600 / min por usuario |
| Tick del scheduler | 2 / min |

**Detrás de un proxy es obligatorio declarar cuál se confía:**

```bash
FORWARDED_ALLOW_IPS=<IP o CIDR del proxy>
```

Sin eso, `client.host` es la dirección del proxy, **todos los usuarios caen en un
mismo cubo**, y el login de 5/min se vuelve 5/min para el producto entero. El síntoma
es que el login empieza a devolver 429 para cualquiera sin que nada parezca roto.

La app **no** confía en `X-Forwarded-For` por su cuenta. Antes lo hacía, y era
falsificable con una cabecera. Resolver el proxy es trabajo de uvicorn
(`--proxy-headers` / `--forwarded-allow-ips`), que es quien sabe qué hay en el medio.

---

## 7. Lo que NO está verificado

Esto es lo que hay que resolver antes de llamar a esto producción.

### 7.1 El `Dockerfile` y el `docker-entrypoint.sh` — build verificado, `up` pendiente

**Actualizado 2026-10-06:** `docker compose build` ya pasó en esta máquina con Docker
(imágenes `tempus-backend` y `tempus-frontend` construidas). Lo que cambió en esta
entrega:

- `pip install .` en vez de `.[dev]` — pytest, mypy y ruff no van a producción.
- usuario `appuser` (uid 10001) en vez de root.
- `HEALTHCHECK` contra `/healthz`.
- `chmod +x` del entrypoint: Git en Windows no guarda el bit de ejecución y sin esto
  el contenedor muere con "permission denied".
- el entrypoint corre las migraciones y pasa `--forwarded-allow-ips` solo si el
  despliegue declara un proxy de confianza.

**Pendiente:** `docker compose up` (levantar el stack completo) no se probó; no es el
gate del checklist §10, pero conviene correrlo antes de publicar.

### 7.2 Deuda de tipos: 122 errores de mypy

`mypy app tests` **no pasa**. El desglose:

| Dónde | Errores | Estado |
|---|---|---|
| `app/core/` | 0 | limpio |
| `app/modules/auth/` | 0 | limpio |
| resto de `app/` | 32 | anotaciones faltantes, `Literal`s que el estático no estrecha |
| `tests/` | 90 | anotaciones en fixtures |

El CI quedó acotado a `mypy app/core app/modules/auth`. **Esto fue una decisión, no un
olvido:** con el comando completo el pipeline estaba rojo en todos los pushes, y un CI
que siempre falla deja de leerse. Ampliar el alcance es un PR aparte.

Durante esta entrega se eliminaron 6 errores falsos de `app/modules/availability/service.py`:
`occupied`/`blocked` se reenlazaban en dos ramas y mypy no reiniciaba el estrechamiento,
así que denunciaba como "asignación por índice sobre una tupla" dos líneas que en
runtime son un `dict`. Se renombraron a `occupied_per_pro`/`blocked_per_pro`.

### 7.3 Rama y CI — resuelto

**Actualizado 2026-10-06:** la rama de trabajo es `master`, que coincide con
`origin/HEAD` y con los triggers de `.github/workflows/ci.yml` (`branches: [master, main]`,
push y pull_request). El primer push a `master` dispara el workflow.

### 7.4 `diagnostico_bugs.py` no cubre el aislamiento entre negocios por HTTP

Cubre siete casos del flujo público. El octavo —un token del negocio A contra un
endpoint del negocio B— quedó sin implementar. La función que lo iba a hacer
(`caso_aislamiento`) solo imprimía *"se omite"* y nunca fue llamada desde `main`; se
borró en vez de dejarla dando la impresión de cobertura.

La garantía **sí** está cubierta, pero abajo: `tests/tenancy/test_rls_isolation.py`
prueba que no se lee, inserta, actualiza ni borra filas de otro tenant contra la base
real. Lo que ninguna de las dos capas cubre es el camino completo por HTTP.

### 7.5 El CI apunta los tests a `tempus_test` — resuelto

**Actualizado 2026-10-06:** en `.github/workflows/ci.yml` los jobs de test declaran
`TEST_DATABASE_URL` terminando en `/tempus_test`, con las migraciones contra
`DATABASE_MIGRATION_URL` en la misma base. Verificado por lectura del workflow.

### 7.6 Concurrencia sobre los cubos de rate limit — resuelto

**Actualizado 2026-10-06:** `tests/concurrency/test_rate_limit_concurrency.py` golpea
el cubo de `POST /public/bookings` con `asyncio.gather` de N requests paralelos
(12 contra límite 5, y 5 contra límite 1) y exige que pasen exactamente L con el resto
en 429. Los dos tests corren verdes de forma repetida (3/3 corridas), sobre la base
real con la sesión propia de `enforce_rate_limit`.

---

## 8. Lo que sí quedó verificado contra el catálogo de PostgreSQL

`verificar_produccion.py` lee `pg_proc`, no el código. Esto importa: una migración
editada a mano después de aplicada deja el repo y la base discrepando, y es
justamente el estado en el que un despliegue se rompe.

| Comprobación | Resultado |
|---|---|
| `rate_limit_hit` es SECURITY **INVOKER** | `prosecdef=false` |
| `rate_limit_hit` es VOLATILE | `provolatile=v` |
| `search_path` fijado | `pg_catalog, public` |
| `search_path` **sin** `pg_temp` | confirmado |
| `tempus_app` tiene INSERT/UPDATE sobre `rate_limit_buckets` | confirmado |
| `generate_token()` → `secrets.token_urlsafe`, dos tokens distintos | confirmado |
| `hash_token` → SHA-256 hex de 64 chars, determinista | confirmado |
| `settings_snapshot()` expone 6 parámetros, ninguno secreto | confirmado |
| `repr(Settings)` y `model_dump()` enmascaran los secretos | confirmado |

INVOKER es lo correcto a propósito: la app ya tiene INSERT/UPDATE sobre la tabla, así
que la función no necesita escalar privilegios. Con SECURITY DEFINER bastaría darle
EXECUTE para poder escribir en cualquier tabla.

`verificar_produccion.py` necesita `DATABASE_MIGRATION_URL` apuntando a la base real.

---

## 9. Instalación desde cero

```bash
git clone <repo> && cd Tempus

# 1. PostgreSQL 18 con dos roles y dos bases: tempus_app (la app) y
#    tempus_owner (DDL). Script de bootstrap: infra/postgres/init/

# 2. Dependencias
cd backend && python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
cd ../frontend && npm install

# 3. Configuración
cp ../.env.example ../backend/.env     # y completar las 5 obligatorias

# 4. Esquema
cd backend && python -m alembic upgrade head

# 5. Datos de ejemplo (opcional)
python seed_demo.py

# 6. API y front
python -m uvicorn app.main:app --port 8002 --reload
cd ../frontend && npm run dev
```

---

## 10. Checklist de publicación

- [x] `docker compose build` funciona (§7.1) — hecho (2026-10-06)
- [x] `python -m pytest` → 1184 passed (§1) — hecho
- [ ] `python -m mypy app/core app/modules/auth` → 0 errores — hecho
- [ ] `python verificar_limites.py` → 5/5 — hecho
- [ ] `python verificar_produccion.py` → 12/12 — hecho
- [ ] `alembic upgrade head` idempotente (§2) — hecho
- [x] Rama coherente con los triggers del CI (§7.3) — hecho (rama `master`, triggers `[master, main]`)
- [x] `TEST_DATABASE_URL` del CI apunta a `tempus_test` (§7.5) — hecho (el workflow declara `/tempus_test`)
- [x] `FORWARDED_ALLOW_IPS` documentado para el despliegue (§6) — el valor real se declara en infra, junto a las 5 variables
- [x] Prueba de concurrencia sobre los cubos de rate limit (§7.6) — hecho (`tests/concurrency/test_rate_limit_concurrency.py`, 3/3 corridas verdes)
- [ ] Las 5 variables obligatorias con valores reales de producción — **pendiente**