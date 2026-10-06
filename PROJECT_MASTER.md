# DESARROLLO DE PLATAFORMA SAAS DE TURNOS - TEMPUS

> Nombre comercial: Tempus (ex Turnify). Caso base: Barbería El Peluche con Gabriel, Daniel, José.

## 1. ROL
Actúa como equipo senior: Full Stack, Architect, Backend, Frontend, Database, DevOps, QA, Security, UX/UI, Product, Prompt Engineer.
Responsabilidad: plataforma SaaS profesional, escalable, segura, mantenible. Calidad > Velocidad.

Antes de implementar: 1. Analiza requisitos 2. Encaje con arquitectura 3. Dependencias 4. Problemas 5. Solución técnica 6. Explicación decisión 7. Implementa. Si hay solución mejor, proponela.

## 2. PRODUCTO
Plataforma SaaS de gestión y reserva de turnos para negocios con profesionales y servicios. Inicial: barberías, peluquerías, maquillaje, tatuajes, masajes, estética. Luego genérico.

NO diseñar para barberías. Usar vocabulario genérico: Business, Professional, Service, Customer, Booking, Schedule, Availability, Block, Holiday, Leave, Notification.

MEJORA APLICADA: Modelo self-service. El negocio se crea solo vía /register. No necesita Platform Admin. Flujo onboarding: User(owner) + Business + Professional(owner) en transacción atómica.

## 3. PROPUESTA DE VALOR
No solo agenda. Automatizar todo. Debe:
- Calcular disponibilidad real (motor único backend)
- Permitir profesional específico O "Cualquier profesional" (diferencial El Peluche)
- Mostrar "Gabriel no disponible, Daniel y José sí a las 14:00" y primera disponibilidad global
- Evitar dobles reservas con garantía DB
- Confirmar automáticamente, sin que barbero tenga que confirmar
- Liberar inmediatamente cancelados
- Horarios 100% configurables por negocio (no regla fija)
- Vacaciones, bloqueos, feriados
- WhatsApp oficial Cloud API como núcleo, no complemento
- Cancelar/reprogramar sin cuenta vía secure_token
- Mínima administración manual

Objetivo: negocio interviene lo menos posible. Agenda = realidad.

## 4. MULTI-TENANCY
Esquema compartido + business_id + RLS FORCE. 3 capas:
Capa 1: FK compuestas UNIQUE(id,business_id) + FK (professional_id,business_id) REFERENCES professionals(id,business_id) = imposible cross-tenant.
Capa 2: RLS FORCE + SET LOCAL app.current_business_id por transacción (nunca SET sesión por PgBouncer).
Capa 3: App repo exige TenantContext desde JWT tid.

Todo dato privado filtra por business_id. Test parametrizado por tabla.

## 5. URL PÚBLICA DEL NEGOCIO - MEJORA CLAVE
Cada negocio URL propia:
tempus.com/el-peluche
tempus.com/peluqueria-maria
tempus.com/sofia-makeup

Entra a /el-peluche y ve DIRECTO a El Peluche, no a home general. Home general existe en / (landing SaaS con marketplace futuro).

Separación:
business_id interno UUIDv7 estable = identidad real
slug público citext UNIQUE = dirección pública mutable

Si cambia nombre, business_id no cambia. Links de Instagram no se rompen. Slugs reservados en tabla slug_reservations: api,admin,panel,r,assets,login,register,www,app,docs. Reserva atómica con SELECT FOR UPDATE.

Futuro: dominio personalizado peluqueriamaria.com -> apunta a su página dentro de Tempus.

Seguridad: /{slug} solo datos públicos. Datos internos solo panel auth. Gestión reservas vía token, no booking_id.

## 6. CLIENTES - SIN CUENTA
Cliente NO crea cuenta. No email, no password, no login, no app.
Solo: Nombre, Apellido, WhatsApp.
Backend crea/asocia Customer interno por phone_e164 UNIQUE(business_id,phone). Historial interno sin cuenta visible.

## 7. RESERVA SIN CUENTA
Internamente: booking_id, customer_id, secure_token
secure_token: secrets.token_urlsafe(32) 256bits CSPRNG, impredecible, no secuencial, no derivado booking_id. Almacén: sha256 hash UNIQUE, token claro nunca en DB. Búsqueda por hash.
Permite: ver, cancelar, reprogramar su propia reserva. No agenda completa.
URL: /r/{token}

## 8. FLUJO DE RESERVA
1. Cliente entra a /el-peluche
2. Selecciona servicio (Corte 45min)
3. Selecciona profesional Gabriel/José/Daniel o Cualquier profesional
4. Selecciona fecha
5. Sistema calcula disponibilidad real (motor único)
6. Muestra horarios: 14:00 Gabriel ocupado, 14:00 Daniel disponible, 14:00 José disponible + Primera disponibilidad
7. Selecciona horario
8. Introduce Nombre, Apellido, WhatsApp
9. Backend re-verifica disponibilidad en transacción
10. Crea reserva transaccional + idempotency_key
11. Confirmada automática
12. WhatsApp cliente: confirmación + botones [Cancelar][Reprogramar] con link /r/{token}
13. WhatsApp profesional: Nuevo turno
14. Programa recordatorios 2h y 1h

NO flujo "esperando confirmación".

## 9. CUALQUIER PROFESIONAL - CORE
Barbería: Gabriel, José, Daniel. Cliente quiere Corte, Cualquier profesional.
Sistema busca profesionales que: pertenecen negocio, activos, pueden hacer servicio, trabajan ese horario, no vacaciones/licencia/bloqueo, no turno solapado, ventana suficiente duración.
Devuelve opciones: 18:00 Daniel, 18:00 José, o 18:30 Gabriel primera disponibilidad.
Si selecciona Any, backend asigna automáticamente según estrategia configurable (round-robin, menos ocupado, primera disponibilidad). Asignación solo backend.

## 10. SERVICIOS
Por negocio: Nombre, Descripción, Duración, Precio numeric(12,2) no float, activo/inactivo. Relación Professional N--N Service. No todos hacen todo.

## 11. HORARIOS DEL NEGOCIO - 100% CONFIGURABLES - MEJORA
No sistema rígido descanso. Ventanas de funcionamiento configurables por negocio desde panel.
Ejemplo:
Barbería A Lunes 08:00-12:00 y 17:00-21:00 = 2 filas
Peluquería B Lunes 08:00-18:00 = 1 fila
Estudio C Lunes 09:00-13:00 y 15:00-20:00
Domingo 0 filas = cerrado
Soporta: abierto/cerrado, 1 o N ventanas, diferente por día, cambios excepcionales, feriados. Nunca ofrecer fuera ventanas. Modelo: business_hours(business_id, weekday, open_time, close_time).

## 12. DISPONIBILIDAD PROFESIONAL
Considera: horario negocio + horario profesional (professional_schedules) + servicios que puede + duración + reservas + bloqueos + vacaciones + licencias + feriados + excepciones. Cálculo dinámico huecos, no lista fija. Única fuente de verdad backend.

## 13. BLOQUEOS
Bloquear horario específico profesional: Gabriel 14:00-15:30 trámite. No aparece disponible. Coexiste con reservas/horarios/vacaciones.

## 14. VACACIONES Y AUSENCIAS
Vacaciones/licencias pertenecen a profesional (time_off). Feriados a negocio (holidays). No bloquear negocio completo porque un profesional ausente. Motor considera todo.

## 15. DOBLE RESERVA - CRÍTICA
No confiar frontend. Protección backend + DB transaccional:
BEGIN; SET LOCAL business_id; SELECT FOR UPDATE bookings WHERE professional_id; verificar disponibilidad motor; INSERT con EXCLUDE USING gist (professional_id WITH =, tstzrange WITH &&) WHERE status=confirmed; INSERT idempotency_keys ON CONFLICT DO NOTHING; COMMIT;
Dos simultáneos mismo horario = solo uno 201, otro 409. Tests concurrencia 50 threads.

## 16. CANCELACIÓN
Profesional/admin NO confirman ni cancelan. Reserva nace confirmada. Cliente cancela vía WhatsApp botón -> URL segura /r/{token} -> confirmar cancelación.
Validar token hash, verificar puede cancelarse (no cancelada/completada/no_show, no dentro cancellation_window, no en curso), cambiar estado, liberar horario inmediato, actualizar agenda, notificar profesional. Horario vuelve disponible automático para walk-in.

## 17. REPROGRAMACIÓN
WhatsApp [Reprogramar] -> URL segura token -> muestra nuevos horarios disponibles -> cliente elige -> backend revalida disponibilidad -> transacción libera horario anterior y ocupa nuevo -> notificaciones. Sin inconsistencias.

## 18. WHATSAPP - CENTRAL - OFICIAL - MEJORA
WhatsApp es CENTRAL, no complemento. Usar META WHATSAPP BUSINESS PLATFORM / CLOUD API oficial. NO WhatsApp Web scraping, NO Baileys, NO sesión web.
Arquitectura preparada para: mensajes, plantillas aprobadas, webhooks firmados, botones/interacciones Quick Reply, reintentos exponenciales, logs sin PII, idempotencia por event_id, outbox pattern.
Docs actuales Meta exponen Cloud API, webhooks, recursos integración externa - usar eso.

## 19. WHATSAPP AL RESERVAR
Cliente: ✅ Turno reservado El Peluche 📅 Lunes 28 🕐 18:30 ✂️ Corte 👤 Gabriel + [Cancelar][Reprogramar] + [Ver reserva]
Profesional: 🔔 Nuevo turno Cliente: Juan Pérez Servicio: Corte Fecha: lunes 28 Hora: 18:30 WhatsApp: +54... + agenda auto.

## 20. RECORDATORIOS
2 recordatorios automáticos: 2 horas antes y 1 hora antes. Contenido: negocio, servicio, profesional, fecha/hora, botones Cancelar/Reprogramar. Vía background jobs (jobs tabla + drain in-process + tick Cloudflare Worker cron). No bloquear request HTTP. Ejemplo: ⏰ Recordatorio hoy 18:00 en El Peluche con Gabriel [Cancelar][Reprogramar]. Útil para liberar si no va y tomar walk-in.

## 21. AGENDA PROFESIONAL
Panel: turnos hoy, próximos, historial, clientes, servicios, horarios, bloqueos, vacaciones según permisos. Actualiza auto reserva/cancel/reprograma. No confirma manualmente.

## 22. AGENDA POR WHATSAPP
Resumen diario mañana: Agenda de hoy — Gabriel 09:00 Juan Pérez Corte 10:00 Pedro Gómez Corte+barba 11:30 Disponible 12:15 María López Corte - claro y compacto, no bombardeo.

## 23. WALK-IN
Clientes sin reserva físicos. Panel "Agregar cliente sin reserva" registra atención para que agenda = realidad, permite stats ocupación real. Después de MVP reservas online.

## 24. PANEL ADMINISTRATIVO
Negocio: nombre, descripción, logo/cover R2, dirección, WhatsApp, slug, timezone, slot_interval, min_lead, max_advance, cancellation_window
Profesionales: crear, editar, activar/desactivar, servicios, horarios, vacaciones, licencias, bloqueos
Servicios: crear, editar, precio numeric, duración, activar/desactivar
Horarios: por día múltiples ventanas, excepciones
Feriados: CRUD
Reservas: consultar, filtrar, historial
IMPORTANTE: admin NO confirma manualmente reservas online.

## 25. SEGURIDAD
JWT access 15min + refresh rotation + reuse detection familia + bloqueo user, Argon2id, RBAC admin/staff/professional/platform_admin separado, rate limiting PG (login 5/min IP, register-business 3/h IP, public bookings 10/min IP + 5/h teléfono), validación Pydantic/Zod, IDOR protegido por RLS + token hash, mass assignment bloqueado por schemas, CORS strict, Turnstile en /register y /public/bookings, sanitización, logs seguridad sin secretos,.env.example sin secretos, secure cookies HttpOnly Secure SameSite=Strict Path=/api/v1/auth, HTTPS prod.

## 26. STACK
Frontend: React, TypeScript, Vite, Tailwind CSS, shadcn/ui, TanStack Query, React Hook Form, Zod, Vitest, Playwright
Backend: Python 3.13+, FastAPI, Pydantic, SQLAlchemy 2.x, Alembic, Pytest
DB: PostgreSQL con btree_gist, índices, transacciones, constraints
Infra: Docker, Docker Compose, Redis opcional (PG como cola en MVP), background jobs
Archivos: Cloudflare R2 S3-compatible, no imágenes en PG

## 27. AUTENTICACIÓN
Usuarios internos: Platform Admin (platform_users tabla separada), Business Admin, Staff, Professional. Cliente público NO login.
JWT access + refresh rotation + Argon2 + RBAC. Password hash nunca sale API ni log.

## 28. ARQUITECTURA
MODULAR MONOLITH, NO microservicios inicial. Módulos: auth, onboarding, businesses, professionals, services, schedules, availability, bookings, customers, notifications, payments, reports. Cada módulo autonomous, no importa repos de otro. Dependencia: api->services->repositories->db, core.

## 29. ESTRUCTURA
project/
 frontend/src/{components,pages/{LandingPage,RegisterPage,LoginPage,BusinessPage,BookingSuccessPage,ManageBookingPage,PanelPage,AdminPage},features,hooks,services,types,lib}
 backend/app/{api/v1/{auth,onboarding,public,businesses},core/{config,security},models, schemas, services/{availability_engine,booking_service,onboarding_service}, repositories, workers}
 migrations/ tests/ docker-compose.yml.env.example README.md ARCHITECTURE.md PROJECT_MASTER.md docs/adr/

## 30. API
REST OpenAPI versionado /api/v1/
POST /api/v1/auth/register-business (público, self-service, atómico, rate limit) - NUEVO
POST /api/v1/auth/login
POST /api/v1/auth/refresh
GET /api/v1/public/businesses/{slug}
GET /api/v1/public/businesses/{slug}/services
GET /api/v1/public/businesses/{slug}/professionals
GET /api/v1/availability?business_id&service_id&date&professional_id
POST /api/v1/public/bookings (idempotency-key requerido)
GET /api/v1/public/bookings/{token}
POST /api/v1/public/bookings/{token}/cancel
POST /api/v1/public/bookings/{token}/reschedule
No endpoints innecesarios.

## 31. FRONTEND
Moderna, profesional, responsive, rápida, accesible, clara, mobile-first. Componentes reutilizables. Evitar componentes gigantes, lógica duplicada, estados globales innecesarios, estilos repetidos, llamadas API dispersas, lógica negocio en componentes visuales. Separar UI, estado, API, lógica negocio, tipos, validación.

## 32. MOTOR DISPONIBILIDAD
Pieza más importante. Diseño dominio independiente. Recibe business, service, professional opcional, fecha, duración. Considera horarios negocio, horarios profesional, servicios, reservas, bloqueos, vacaciones, feriados, excepciones. Devuelve solo slots realmente reservables. Única fuente de verdad. Frontend previsualiza, backend revalida.

## 33. DINERO
No floats. numeric(12,2) + ISO 4217. Preparar Mercado Pago después núcleo reservas.

## 34. MAPAS
No MVP. Luego MapLibre + OpenStreetMap, evitar propietario.

## 35. EMAIL
No obligatorio cliente. Luego admin, recupero password, notificaciones internas. WhatsApp prioridad cliente.

## 36. TESTING
Backend Pytest, Frontend Vitest, E2E Playwright. Atención a disponibilidad, reservas, concurrencia, cancelaciones, reprogramaciones, any professional, horarios, vacaciones, bloqueos, feriados, multi-tenancy, permisos, tokens seguros.

## 37. CASOS CRÍTICOS
1. Dos reservan mismo horario -> solo 1 confirmada
2. Cancel -> horario disponible
3. Reprograma -> libera anterior ocupa nuevo
4. Any professional -> solo elegibles disponibles
5. Vacaciones -> no horarios
6. Bloqueo -> no disponibilidad bloqueo
7. Negocio cerrado -> no turnos
8. Servicio 90min -> no slot si no ventana 90min continua
9. Manipular booking_id -> no acceso (no existe endpoint público por id)
10. Token inválido -> 403

## 38. MVP
Priorizar: 1 Arquitectura 2 DB 3 Auth interna + onboarding self-service 4 Negocios 5 Profesionales 6 Servicios 7 Horarios configurables 8 Disponibilidad 9 Reservas 10 Any professional 11 Cancelación 12 Reprogramación 13 Panel profesional 14 Panel admin 15 URL pública /{slug} 16 WhatsApp Cloud API 17 Recordatorios 18 Tests 19 Seguridad 20 Docker
Después: estadísticas avanzadas, Mercado Pago, mapas, email avanzado, marketplace, dominios personalizados, WhatsApp avanzado, SaaS avanzado.

## 39. REGLAS DESARROLLO
NO inventes APIs, NO inventes respuestas backend, NO agregues dependencias sin justificar, NO cambies stack sin explicar, NO microservicios, NO dupliques lógica negocio, NO lógica crítica solo frontend, NO mocks sustituto salvo explícito, NO ocultes errores, NO ignores errores compilación, NO marques terminada sin probar, NO afirmes funciona sin ejecutar.

## 40. MANEJO CAMBIOS
Analiza código existente, identifica dependencias, explica qué modificar, implementa, ejecuta tests relevantes, verifica errores, actualiza docs. No sobrescribir sin analizar impacto.

## 41. DOCUMENTACIÓN
README.md, ARCHITECTURE.md, PROJECT_MASTER.md,.env.example, docs/adr, API docs. Reflejar estado REAL, no funcionalidades inexistentes.

## 42. VARIABLES ENTORNO
Nunca hardcodear passwords, JWT secrets, API keys, WhatsApp tokens, DB creds, Mercado Pago creds, storage creds..env.example con ejemplos seguros.

## 43. LOGS Y OBSERVABILIDAD
Registrar errores, reservas, cancelaciones, reprogramaciones, notificaciones, webhooks, eventos importantes, errores integración. Logs NO exponer passwords, tokens, datos sensibles, creds.

## 44. IDEMPOTENCIA
Operaciones sensibles idempotentes: creación reservas (idempotency_key), webhooks (event_id), envío notificaciones (provider_message_id), cancelaciones, reprogramaciones. Mismo evento 2 veces NO 2 efectos.

## 45. WHATSAPP Y JOBS
Tareas recordatorios, notificaciones, mensajes programados, webhooks vía workers/background jobs. NO requests HTTP abiertas esperando. Redis puede usarse pero PG como cola suficiente MVP. Outbox pattern.

## 46. EXPERIENCIA CLIENTE
Pocos pasos móvil: Servicio -> Profesional -> Fecha -> Hora -> Datos (nombre apellido WhatsApp) -> Confirmación. Extremadamente sencilla, sin fricción, sin cuenta.

## 47. EXPERIENCIA NEGOCIO
Admin configura sin conocimientos técnicos: agenda, disponibilidad, profesionales, servicios, horarios, reservas, clientes. Entiende fácil.

## 48. PRINCIPIO FUNDAMENTAL
Plataforma debe representar REALIDAD. Si profesional ocupado NO mostrar disponibilidad. Si vacaciones NO mostrar. Si negocio cerrado NO mostrar. Si cancela LIBERAR horario. Si dos intentan simultáneo SOLO UNO. Si servicio 90min NO hueco 60min. Si profesional no realiza servicio NO asignarlo. Si cambia horarios disponibilidad refleja.

## 49. FORMA TRABAJAR
Senior Tech Lead. Analiza, explica breve solución, implementa, prueba, indica qué se modificó, archivos modificados, tests ejecutados, resultado tests, problemas, pendiente. No código sin contexto.

## 50. REGLA ORO
CALIDAD > VELOCIDAD, CORRECCIÓN > CANTIDAD, SEGURIDAD > COMODIDAD, ARQUITECTURA LIMPIA > PARCHE RÁPIDO, DATOS REALES > MOCKS, TESTS REALES > "PARECE FUNCIONAR"

## 51. PRIMERA TAREA - MEJORA
No programar inmediato. Fase arquitectura ya aprobada con self-service.
Entregables §51 ya en ARCHITECTURE.md con mejoras: onboarding transaccional, horarios ventanas múltiples configurables por negocio, WhatsApp Cloud API oficial con botones/webhooks/outbox, slug vs business_id con reserva atómica, URL propia /{slug} directo sin pasar por home.
Una vez aprobada, implementación desde cero por fases sin saltarse pasos, empezando por Fase 0.2 Register.