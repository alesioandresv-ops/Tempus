# Arquitectura — Tempus

> Estado: **fase de arquitectura aprobada. Sin código de producción.**
> Fuente de verdad. SaaS self-service multi-tenant de reservas.
> Todo negocio se crea solo. El cliente no crea cuenta.

## Mapa de los entregables de la §51
| §51 | Tema | Sección |
|-----|------|---------|
| 1 | Nombre técnico | 2 |
| 2 | Arquitectura general | 3 |
| 3 | Diagrama de componentes | 4 |
| 4 | Modelo de datos | 5 |
| 5 | Relaciones | 6 |
| 6 | Multi-tenant | 7 |
| 7 | Disponibilidad | 8 |
| 8 | Anti-double-booking | 9 |
| 9 | Autenticación | 10 |
| 10 | Secure tokens | 11 |
| 11 | WhatsApp | 12 |
| 12 | Background jobs | 13 |
| 13 | Estructura | 14 |
| 14 | API | 15 |
| 15 | Testing | 16 |
| 16 | Seguridad | 17 |
| 17 | Despliegue | 18 |
| 18 | Roadmap | 19 |
| 19 | Riesgos | 20 |
| 20 | Decisiones | 21 |

## 1. Alcance y principios
Plataforma SaaS genérica: Business, Professional, Service, Customer, Booking. No barbería hardcoded.
1. La DB es la autoridad.
2. Una sola fuente de verdad para disponibilidad.
3. Aislamiento tenant 3 capas: FK compuestas + RLS FORCE + app.
4. WhatsApp oficial obligatorio. Meta Cloud API con templates, botones, webhooks. Prohibido WhatsApp Web.
5. Horarios 100% configurables por negocio. El software se adapta al negocio.
6. URL propia por negocio: tempus.com/{slug} lleva directo al negocio. business_id estable, slug público mutable.

## 2. Nombre y convenciones
Nombre técnico: tempus. Dominio objetivo tempus.com (ex Turnify). IDs UUIDv7. Timezone UTC timestamptz + date local. Dinero numeric(12,2). Slugs citext UNIQUE.

## 3. Arquitectura general
Monolito modular. Módulos: auth · onboarding · businesses · professionals · services · schedules · availability · bookings · customers · notifications · reports
onboarding es pre-tenant: crea Business + BusinessUser(admin) + Professional(owner) + BusinessHours default en transacción atómica.
Frontend SPA: /{slug} público, /register onboarding, /login, /panel/*, /admin/*, /r/{token}

## 4. Diagrama de componentes
Cliente (sin cuenta) -> Cloudflare -> Pages SPA -> FastAPI -> Postgres
Meta WhatsApp Cloud API <-> Webhooks firmados -> FastAPI -> Outbox -> Job drain
Cloudflare Worker cron -> GET /internal/scheduler/tick -> FastAPI
FastAPI -> R2 media presigned

## 5. Modelo de datos (27 tablas)
businesses(id, slug UNIQUE, name, timezone, locale, currency, slot_interval 15, min_lead 60, max_advance 60, cancellation_window 120, status[trial,active,suspended], phone_e164, address, brand_color)
business_users(id, business_id, email UNIQUE(business_id,email), password_hash Argon2id, full_name, role[admin,staff])
platform_users SEPARADA.
professionals(id, business_id, user_id NULL, display_name, is_active)
services(id, business_id, name, duration_minutes, price numeric, is_active)
professional_services(professional_id, business_id, service_id, business_id) FK compuesta.
customers(id, business_id, first_name, last_name, phone_e164 UNIQUE(business_id,phone))
business_hours(id, business_id, weekday 0-6, open_time, close_time) múltiples filas por día. Ej Lunes 08:00-12:00 y 17:00-21:00 = 2 filas. Domingo 0 filas = cerrado.
business_exceptions + business_exception_windows, holidays, professional_schedules, time_off[vacation,leave], blocks
bookings(id, business_id, professional_id, service_id, customer_id, start_ts, end_ts, date_local, status[confirmed,cancelled,completed,no_show], secure_token_hash UNIQUE, price_at_booking, idempotency_key) + EXCLUDE btree_gist professional_id + tstzrange
booking_events, idempotency_keys
whatsapp_connections(business_id UNIQUE, phone_number_id, token_encrypted)
whatsapp_templates, notification_requests[booking_confirmed,professional_new,reminder_2h,reminder_1h], webhook_events
jobs(business_id NULL), refresh_tokens(user_id NULL, platform_user_id NULL, token_hash, family_id), audit_log, media, slug_reservations(slug UNIQUE), rate_limit_buckets

RLS: 20 con RLS FORCE (bookings, business_hours, etc) y 7 sin RLS: businesses, platform_users, slug_reservations, rate_limit_buckets, webhook_events, refresh_tokens, jobs.

### 5.8 Qué tablas llevan RLS y cuáles no

De las 27 tablas, **20 llevan `ROW LEVEL SECURITY` y `FORCE ROW LEVEL SECURITY`**, y
7 no llevan ninguna de las dos. La lista no es una convención: está escrita en
`app/models/__init__.py` como `GLOBAL_TABLES`, la aplica la migración `0002`, y
`tests/unit/test_orm_metadata.py` falla si el modelo y la base se separan.

**Las 20 con RLS**, todas de tenant:
`audit_log`, `blocks`, `booking_events`, `bookings`, `business_exception_windows`,
`business_exceptions`, `business_hours`, `business_users`, `customers`, `holidays`,
`idempotency_keys`, `media`, `notification_requests`, `professional_schedules`,
`professional_services`, `professionals`, `services`, `time_off`,
`whatsapp_connections`, `whatsapp_templates`.

**Las 7 sin RLS**, y por qué:

| tabla | razón |
|---|---|
| `businesses` | Es la fila de la que cuelga el resto, y no tiene columna `business_id`: una política que comparara `business_id` contra sí misma no filtraría nada. Es global por modelo, pero **sí tiene RLS FORCE** desde `0011`, con tres políticas: `businesses_read` (`SELECT ... USING (true)`, para poder resolver el negocio por `slug` antes de conocer el tenant), `businesses_write` (`UPDATE` atada al GUC) y `businesses_insert`, que es `TO tempus_owner` y por lo tanto inaccesible para el rol de la app. El rol de la app tiene `SELECT` a nivel de tabla, `UPDATE` solo sobre una lista blanca de 14 columnas, y nada de `INSERT` ni `DELETE`: el alta entra por la función `SECURITY DEFINER` `tempus_create_business` (`0014`). |
| `platform_users` | Personal de la plataforma, que por definición no pertenece a un negocio (§5.1). Sin permiso de escritura para el rol de la app. |
| `slug_reservations` | Reserva global de un slug. No es legible por el rol de la app (`FORBIDDEN_FOR_APP_ROLE`), y por eso el chequeo de "esta palabra está reservada" vive dentro de `tempus_create_business` (`0014`) y no en Python. |
| `rate_limit_buckets` | Anterior al tenant: se mide por IP y por email (§5.7). |
| `webhook_events` | Bandeja de entrada de Meta. Es de la plataforma, no de un negocio. |
| `refresh_tokens` | No tiene `business_id`: cuelga de `user_id` o `platform_user_id`. Ver la nota de abajo. |
| `jobs` | La cola es del sistema. El dispatcher saca trabajos de varias cuentas a la vez a propósito; filtrarla por tenant rompería el drenado. `business_id` sigue nullable y presente en la fila para poder depurar y para que un handler de negocio verifique que no le llego algo global. |

**La nota sobre `refresh_tokens`.** Es la única de las siete cuyo aislamiento no
depende de una decisión de permisos sino de que la aplicación filtre bien. No hay
`business_id` porque el token pertenece a una *persona*, que puede ser de un negocio
o de la plataforma, y la tabla tiene un CHECK que exige exactamente uno de los dos
(`ck_refresh_tokens_exactly_one_principal`). El aislamiento real es: solo se
consultan tokens del principal autenticado.

Es una dependencia del código, no de la base, y conviene decirlo explícito en vez de
dejarlo implícito:

- Una consulta a `refresh_tokens` sin filtro de `user_id` devuelve metadatos de otros
  tenants (`user_agent`, `ip_address`, fechas).
- El impacto está acotado por diseño: se guarda `token_hash` (SHA-256), nunca el
  token. Robar una fila no permite autenticarse, porque el hash no es el secreto
  que se presenta.
- Si alguna vez esto deja de ser aceptable, el camino es agregar `business_id` a la
  tabla y ponerla en RLS. No lo tiene ahora porque exigiría una migración de datos
  para proteger metadatos, y el costo no está justificado mientras el hash impida
  el uso del token. **Es una decisión, no un olvido**, y por eso está escrita acá.

**Total: de las 27 tablas, 20 con RLS y 7 sin ella.**

## 6. Relaciones
Business 1--N Professional, Service, Customer, Booking
Professional N--N Service via professional_services FK compuesta
Booking N-1 Professional, Service, Customer

## 7. Multi-tenant
Capa 1: FK compuestas UNIQUE(id,business_id). Capa 2: RLS FORCE POLICY USING business_id = current_setting. SET LOCAL por transacción. Capa 3: TenantContext desde JWT tid.

## 8. Motor de disponibilidad
Única fuente. Input: business, service, professional_id|any, date_local. Output: slots reales.
1. business_hours weekday + exceptions/holidays -> ventanas base
2. Intersectar con professional_schedules
3. Restar time_off, blocks, bookings confirmed
4. Filtrar duración + slot_interval + min_lead + max_advance
5. Si any: buscar profesionales elegibles. Cache 30s.

## 9. Anti-doble-reserva
BEGIN; SET LOCAL; SELECT FOR UPDATE; verificar disponibilidad; INSERT con EXCLUDE; INSERT idempotency_keys ON CONFLICT DO NOTHING; COMMIT; 409 si colisión.

## 10. Autenticación
Solo internos. Cliente sin cuenta. Argon2id. JWT access 15min {sub, tid, role, jti}. Refresh opaque 256bits SHA256 cookie HttpOnly Secure SameSite=Strict Path=/api/v1/auth rotation family_id reuse detection revoca familia.
Login pre-tenant: función SECURITY DEFINER tempus_login_definer BYPASSRLS.
Onboarding: POST /auth/register-business público, atómico. Status trial. El INSERT en `businesses` va por una función SECURITY DEFINER propiedad de `tempus_owner` (migración 0014), NO por un rol BYPASSRLS y NO con `DATABASE_MIGRATION_URL` en runtime: `businesses_insert` es `TO tempus_owner`, así que la función satisface la política sin bypassear nada. La función también consulta `slug_reservations`, que `tempus_app` no puede leer.

## 11. Secure tokens
secrets.token_urlsafe(32) 256bits CSPRNG. Almacén sha256 hash UNIQUE. Token nunca en claro. Ruta /r/{token} y /public/bookings/{token}, /{token}/cancel, /{token}/reschedule. No endpoint por booking_id.

## 12. WhatsApp - Plataforma oficial
Meta Cloud API únicamente. Graph API POST /{phone_number_id}/messages con templates + botones Quick Reply [Ver reserva] [Cancelar] [Reprogramar] -> https://tempus.com/r/{token}
Webhooks firmados -> webhook_events idempotente. Outbox notification_requests INSERT misma transacción booking, worker drain reintentos. Recordatorios jobs run_at = start_ts -2h/-1h.

## 13. Background jobs
Tabla jobs + drain in-process + tick externo Worker cron cada 1min GET /internal/scheduler/tick Bearer.

## 14. Estructura
frontend/src/pages/{LandingPage,RegisterPage,LoginPage,BusinessPage,BookingSuccessPage,ManageBookingPage,PanelPage,AdminPage}
backend/app/{api/v1/{auth,onboarding,public}, core, models, schemas, services/{availability_engine,booking_service,onboarding_service}, repositories, workers}
migrations/ docker-compose.yml.env.example ARCHITECTURE.md PROJECT_MASTER.md

## 15. API
POST /api/v1/auth/register-business {business_name, slug, owner_name, email, password, phone, timezone} -> 201
POST /api/v1/auth/login, POST /api/v1/auth/refresh
GET /api/v1/public/businesses/{slug}
GET /api/v1/public/businesses/{slug}/availability?service_id&date&professional_id=any|uuid
POST /api/v1/public/bookings {business_slug, service_id, professional_id|any, start_ts, customer_first, last, phone, idempotency_key} -> {secure_token}
GET /api/v1/public/bookings/{token}
POST /api/v1/public/bookings/{token}/cancel
POST /api/v1/public/bookings/{token}/reschedule
Slugs reservados: api, admin, panel, r, assets, static, login, register, www, app, docs

## 16. Testing
Pytest, Vitest, Playwright. Casos: doble reserva concurrente, cancel libera, reschedule, any profesional, vacaciones, bloqueo, cerrado, duración 90min, IDOR, token inválido.

## 17. Seguridad
JWT, refresh rotation, Argon2, RBAC, rate limit PG, Zod/Pydantic, RLS + token hash, CORS strict, Turnstile.

## 18. Despliegue
Docker Compose dev. Prod Free: Render Free + Neon Free + Cloudflare Free + R2 Free + Worker cron.

## 19. Roadmap
0.1 Spec DONE, 0.2 Onboarding /register, 1 Auth + RLS, 2 Negocios/profesionales/servicios/horarios configurables, 3 Motor disponibilidad, 4 Reservas sin cuenta + any professional, 5 Frontend /{slug}, 6 WhatsApp, 7 Panel, 8 Tests, 9 Pagos/mapas/dominios

## 20. Riesgos
Double-booking: EXCLUDE + FOR UPDATE. RLS bypass login: rol dedicado. Slug hijacking: SELECT FOR UPDATE. Pooler fuga: SET LOCAL.

## 21. Decisiones
ADR-0001 Monolito modular, ADR-0002 RLS+FK compuesta, ADR-0017 WhatsApp Cloud API oficial, ADR-0018 Onboarding pre-tenant, ADR-0019 Horarios ventanas múltiples, ADR-0020 Slug vs business_id

## 22. Decisiones aplazadas
Mercado Pago, MapLibre, dominios personalizados, marketplace, email.