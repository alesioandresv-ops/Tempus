# Tempus

SaaS multi-tenant de gestión de turnos con recordatorios automáticos por WhatsApp.
Mercado inicial: Latinoamérica, en español.

> **Estado actual: v1.0.0-rc1.** El backend, el frontend y la base están
> verificados contra PostgreSQL real; la suite completa y el checklist de la
> entrega viven en [`ENTREGA.md`](ENTREGA.md#entrega--tempus-v100-rc1).
>
> En curso: la **Fase 0.5 — Meta** (WhatsApp Business Platform). El arranque sin
> chip (Embedded Signup, webhook) está implementado; el registro del número y la
> validación viva dependen del chip. Runbook: [`docs/fase-05-meta.md`](docs/fase-05-meta.md).

## Arrancar

```bash
# 1. Base de datos (PostgreSQL 18, dos roles, dos bases)
docker compose up -d

# 2. Dependencias
cd backend
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"      # Windows
pip install -e ".[dev]"                     # Linux/macOS

# 3. Configuración
cp ../.env.example .env

# 4. Esquema
python -m alembic upgrade head

# 5. API
python -m uvicorn app.main:app --reload
```

La API queda en `http://localhost:8000`: la documentación en `/api/v1/docs` y los
health checks en `/healthz` y `/readyz`, que quedan fuera del prefijo de versión a
propósito porque es donde las plataformas de hosting los buscan.

`.env.example` está cubierto por un test que falla si el código agrega una variable y
el archivo no la documenta, así que la lista de arriba no se puede quedar vieja en
silencio.

### Rate limiting y proxies

Los límites del §10.5 identifican al cliente por `request.client.host`, y **no** leen
`X-Forwarded-For` ni `CF-Connecting-IP` por su cuenta: una cabecera de IP que llega sin
validar quién la mandó se puede mandar distinta en cada pedido y esquivar el límite
entero, que es justo lo que un límite de fuerza bruta no puede permitir.

Atrás de un proxy eso obliga a que el servidor resuelva la IP real, que es lo que hace
uvicorn con `--proxy-headers` --activo por defecto-- combinado con
`--forwarded-allow-ips`:

```bash
# Local: sin nada, `client.host` es el peer real.
python -m uvicorn app.main:app --reload

# Atrás de Cloudflare/Render: declarar qué proxy es de confianza.
python -m uvicorn app.main:app --forwarded-allow-ips='<ip-del-proxy>'
```

Sin `--forwarded-allow-ips` en un despliegue con proxy, `client.host` es la dirección
del proxy: todos los usuarios caen en un mismo cubo y el login de 5/min se vuelve
5/min **para todo el producto junto**. El síntoma es que el login empieza a devolver
429 para cualquiera, sin que nada parezca roto.

## Verificar

```bash
cd backend

# Lint y formato: los dos pasan sin excepciones.
python -m ruff format --check .
python -m ruff check .

# Los tipos, solo en lo critico. `mypy app tests` completo todavia no pasa:
# ver "Deuda conocida", mas abajo.
python -m mypy app/core app/modules/auth

# La suite. Necesita `TEST_DATABASE_URL`: ver mas abajo.
python -m pytest
```

Los tests de integración, tenancy y concurrencia **necesitan PostgreSQL**: RLS y la
restricción `EXCLUDE` no se prueban con un mock. Sin `TEST_DATABASE_URL` en el
entorno, esos tests se saltan con un mensaje que lo dice, y los unitarios corren
igual. Para verlos correr:

```bash
# `preparar_base_test.py` crea `tempus_test` y escribe `backend/.env.testing`, que el
# conftest carga solo. Es la via facil: no hay nada que exportar a mano.
python preparar_base_test.py

# A mano, si preferis no depender del script (puerto **5432**, el de la base de
# desarrollo; el 5433 es el del compose, que publica al host):
TEST_DATABASE_URL=postgresql+asyncpg://tempus_app:tempus_app_dev_pw@127.0.0.1:5432/tempus_test \
DATABASE_MIGRATION_URL=postgresql+asyncpg://tempus_owner:tempus_owner_dev_pw@127.0.0.1:5432/tempus_test \
python -m pytest
```

El CI corre `ruff format --check`, `ruff check` y `pytest`. Además `alembic check`
y un `downgrade base` + `upgrade head` para probar que las migraciones suben y bajan
y que subir es idempotente.

### Deuda conocida

Dos cosas que este `README` dejaba insinuadas como correctas y no lo son:

- **`mypy app tests` no pasa.** Falla con 122 errores. Los dos directorios que
  importan para seguridad --`app/core/` y `app/modules/auth/`-- están **limpios**, y
  el CI los corre como puerta justamente por eso: correr el comando completo dejaba
  el pipeline rojo en cada push, que es peor que la deuda, porque un CI que siempre
  falla deja de leerse. Lo que queda son 32 errores en el resto de `app/` (anotaciones
  faltantes, `Literal`s que el tipo estático no puede estrechar) y 90 en `tests/`. Ninguno ha
  hecho fallar un test, y eso no los vuelve correctos: son errores que el intérprete
  no ve.
- **`diagnostico_bugs.py` no cubre el aislamiento entre negocios por HTTP.** Cubre
  siete casos del flujo público; el octavo --un token del negocio A contra un endpoint
  del negocio B-- quedó sin implementar y la función que lo iba a hacer se borró por
  ser código muerto. La cobertura de esa garantía sí existe, pero abajo: en
  `tests/tenancy/test_rls_isolation.py`, contra la base. Lo que no cubre ninguna de
  las dos capas es el camino completo por HTTP.

El orden de las fases está en
[ARCHITECTURE.md §19](ARCHITECTURE.md#19-roadmap-por-fases). En curso: la
**Fase 0.5 — Meta** (alta de la app, WABA de plataforma, plantillas aprobadas y el
flujo de Embedded Signup). Es una dependencia externa que arranca temprano porque
implica esperas de aprobación de Meta que no dependen de nadie del equipo; el
arranque sin chip ya está entregado (ver [`docs/fase-05-meta.md`](docs/fase-05-meta.md)).

## Verificación final

Los tres comandos que tienen que dar verde antes de cualquier despliegue. Son los
mismos que corre el CI, y el orden importa: el primero (la suite) tarda ~25 minutos
y no toca la base de producción, el segundo es rápido y el tercero necesita un
servidor arriba.

```bash
cd backend

# 1. La suite completa. ~25 minutos, 1249 tests.
python -m pytest

# 2. Los tipos de lo critico: seguridad y auth limpios.
python -m mypy app/core app/modules/auth

# 3. Los cinco limites del §10.5, contra un servidor de verdad.
#    Levanta el servidor en otra terminal y apuntale con TEMPUS_PORT.
python -m uvicorn app.main:app --port 8002
$env:TEMPUS_PORT = '8002'
python verificar_limites.py
```

El tercero **no se puede correr contra la app en memoria** como los tests: mide lo que
pasa con el middleware real y el ciclo de vida real de las conexiones. Y hay una
trampa: su ventana por teléfono es de **una hora**, así que correrlo dos veces seguidas
con el mismo número deja la cubo llena. Por eso genera un teléfono nuevo en cada
corrida.

Para el endurecimiento de producción --`rate_limit_hit` INVOKER, `search_path` sin
`pg_temp`, tokens, enmascarado de secretos-- hay un cuarto script, que lee el
**catálogo** de PostgreSQL y no el código:

```bash
$env:DATABASE_MIGRATION_URL = 'postgresql+asyncpg://tempus_owner:<pw>@localhost:5432/tempus'
python verificar_produccion.py
```

**Resultado 2026-10-09 (v1.0.0-rc1):** ambos scripts pasaron contra el entorno real.
`verificar_limites.py` → 13/13 PASS contra uvicorn real en :8002 (login `[401×5, 429]`,
reservas por IP `[404×10, 429]`, por teléfono `[404×5, 429]`, panel 200, tick 401).
`verificar_produccion.py` → 15/15 PASS, exit 0, sin warnings, contra la base del
compose y la local (ambas en head `0018_whatsapp_reminders`). Los 6 tests de
`test_whatsapp_connect.py` (Fase 0.5) corren dentro de la suite.

Los dos scripts de arriba tardan: cada uno espera a que expiren las ventanas de las
corridas anteriores.

## Documentos

| Documento | Qué es | Estado |
|---|---|---|
| [`PROJECT_MASTER.md`](PROJECT_MASTER.md) | Especificación de producto y requirements. Fuente de verdad del *qué*. | Completo |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Diseño técnico: datos, API, dominio, despliegue, testing, roadmap. Fuente de verdad del *cómo*. | Completo |
| [`docs/adr/`](docs/adr/README.md) | 15 decisiones arquitectónicas con sus alternativas descartadas. | Completo |
| [`.env.example`](.env.example) | Inventario de variables de entorno, agrupadas por dominio. Cubierto por tests. | Completo |
| [`infra/postgres/init/`](infra/postgres/init/01-roles-and-extensions.sql) | Roles, privilegios y extensiones. | Completo |

Cada entregable pedido en `PROJECT_MASTER §51` tiene su sección en
`ARCHITECTURE.md`, con el número de §51 en la primera columna del mapa de la
[sección 0](ARCHITECTURE.md#mapa-de-los-entregables-de-la-51). Los números de sección
de `ARCHITECTURE.md` no coinciden con los de la §51: hay una sección 1 de alcance que
no es un entregable, así que a partir de ahí van corridos en uno. El mapa es el que
resuelve la correspondencia.

## Decisiones en una línea

- **Monolito modular** por dominios, no microservicios. Un solo deploy.
- **Un esquema compartido** para todos los negocios, con `business_id` en 20 de las 27
  tablas, foreign keys compuestas y Row Level Security. Tres capas de aislamiento, la
  última en la base. Las 7 tablas sin RLS están justificadas una por una en
  [§5.8](ARCHITECTURE.md), y hay un test que falla si esa lista se desincroniza.
- **El negocio aporta su propio número de WhatsApp.** Meta no permite un número
  compartido en un contexto multi-tenant, y la reputación del canal no se puede
  compartir. Ver [ADR-0013](docs/adr/0013-whatsapp-per-business-waba.md).
- **La doble reserva se previene en PostgreSQL**, con una restricción `EXCLUDE`, no en
  el código de la aplicación. Ver [ADR-0008](docs/adr/0008-exclusion-constraint-double-booking.md).
- **La disponibilidad es una función pura** de intervalos, sin efectos ni acceso a
  base de datos. Ver [ADR-0006](docs/adr/0006-availability-as-pure-domain.md).
- **El trabajo asíncrono vive en PostgreSQL** con la tabla de jobs y la outbox. Sin
  broker externo. Ver [ADR-0011](docs/adr/0011-postgres-job-queue.md).
- **Despliegue en $0** con Cloudflare, Render Free y Neon Free. La ruta a producción
  pagada está escrita y no requiere cambios de código.
  Ver [ADR-0014](docs/adr/0014-free-tier-deployment-profile.md).

El detalle de cada una, incluidas las alternativas que se descartaron y por qué, está
en el [índice de ADRs](docs/adr/README.md).

## Prerrequisitos para la Fase 1

Ninguno de estos pasos bloquea el código. Bloquean el producto:

1. **Verificar el nombre `tempus`.** Es provisional: hay que comprobar disponibilidad
   de dominio y registro de marca en la clase 42 y la 45 de Argentina (INPI) antes de
   construir el onboarding. Un cambio de nombre después de tener tenants es caro.
2. **Crear la cuenta de Meta Business** y el developer app. No existe. Sin eso no hay
   WABA, ni plantillas aprobadas, ni Cloud API.
3. **Confirmar los precios de WhatsApp vigentes** en la documentación de Meta antes de
   fijar el modelo de precios. Los valores de `.env.example` son de referencia y hay
   que actualizarlos.
4. **Revisar los límites de los free tiers** de Render, Neon y Cloudflare en el momento
   del despliegue, no antes.

## Sobre los datos de clientes

`PROJECT_MASTER` tiene requisitos de privacidad que no son opcionales. Dos en
particular condicionan el diseño y conviene leerlos antes de tocar la base de datos:

- Los datos del cliente final **no** se exportan desde Tempus. El negocio los tiene en
  su propio sistema; Tempus solo envía recordatorios.
- La página pública de un negocio **no** muestra email ni teléfono del cliente. Muestra
  nombre y un código de gestión.

## Licencia

Sin definir. Añadir antes de publicar el repositorio.
