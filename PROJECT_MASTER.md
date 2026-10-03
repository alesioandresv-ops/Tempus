# DESARROLLO DE PLATAFORMA SaaS DE TURNOS

## 1. ROL

Actúa como un equipo senior compuesto por:

* Senior Full Stack Engineer
* Senior Software Architect
* Senior Backend Engineer
* Senior Frontend Engineer
* Senior Database Engineer
* Senior DevOps Engineer
* Senior QA / Test Engineer
* Senior Security Engineer
* Senior UX/UI Engineer
* Product Engineer
* Prompt Engineer

Tu responsabilidad es diseñar y construir una plataforma SaaS profesional, escalable, segura y mantenible.

No quiero código improvisado ni soluciones rápidas que comprometan la arquitectura.

Antes de implementar cualquier funcionalidad importante:

1. Analiza los requisitos.
2. Comprueba cómo encaja con la arquitectura existente.
3. Identifica dependencias.
4. Detecta posibles problemas.
5. Propón la solución técnica.
6. Explica brevemente la decisión.
7. Recién después implementa.

Si existe una solución técnicamente mejor que la que estoy proponiendo, indícala y explica por qué.

No aceptes automáticamente mis decisiones si técnicamente pueden mejorarse.

---

# 2. PRODUCTO

Estamos construyendo una plataforma SaaS de gestión y reserva de turnos para negocios que trabajan con profesionales y servicios.

El sistema debe poder utilizarse inicialmente para:

* Barberías
* Peluquerías
* Estudios de maquillaje
* Estudios de tatuajes
* Masajes
* Estética
* Otros profesionales o negocios basados en citas

IMPORTANTE:

NO diseñes el sistema específicamente para barberías.

La arquitectura debe ser genérica.

Usa conceptos como:

* Business
* Professional
* Service
* Customer
* Booking / Appointment
* Schedule
* Availability
* Block
* Holiday
* Leave
* Notification

y evita conceptos rígidamente ligados a una profesión.

---

# 3. PROPUESTA DE VALOR

La plataforma no debe limitarse a ser una agenda.

Debe automatizar el proceso completo de reserva y gestión de turnos.

El sistema debe:

* Calcular disponibilidad real.
* Permitir elegir un profesional específico.
* Permitir elegir "Cualquier profesional".
* Encontrar automáticamente profesionales disponibles.
* Evitar dobles reservas.
* Confirmar automáticamente los turnos.
* Liberar inmediatamente los horarios cancelados.
* Gestionar horarios de negocio.
* Gestionar vacaciones y ausencias.
* Gestionar bloqueos específicos.
* Enviar notificaciones por WhatsApp.
* Enviar recordatorios automáticos.
* Permitir cancelar y reprogramar sin crear una cuenta.
* Reducir al mínimo la administración manual.

El objetivo es que el negocio tenga que intervenir lo menos posible.

---

# 4. MULTI-TENANCY

La plataforma debe ser multi-tenant.

Cada negocio debe tener aislados:

* Profesionales
* Servicios
* Clientes
* Turnos
* Horarios
* Bloqueos
* Vacaciones
* Configuración
* Notificaciones
* Estadísticas
* Otros datos propios

Nunca permitas que un usuario de un negocio pueda acceder a datos de otro negocio.

Toda consulta relacionada con información privada debe respetar el tenant/business_id correspondiente.

La separación entre tenants debe estar contemplada desde el diseño de la base de datos y la capa de aplicación.

---

# 5. URL PÚBLICA DEL NEGOCIO

Cada negocio tendrá una URL pública propia.

Ejemplos:

/el-peluche

/peluqueria-maria

/sofia-makeup

La URL debe apuntar directamente a la página pública del negocio.

El usuario no debe necesitar pasar primero por la página general de la plataforma.

El sistema puede tener una Home general en la raíz del dominio para presentar la plataforma o eventualmente funcionar como marketplace.

Pero el negocio debe poder compartir directamente su URL.

El negocio tendrá:

* business_id interno
* slug público

El slug NO debe utilizarse como identificador interno.

El business_id debe ser estable aunque el nombre o slug del negocio cambie.

---

# 6. CLIENTES

IMPORTANTE:

El cliente NO debe crear una cuenta.

NO debe necesitar:

* Email
* Contraseña
* Login
* Aplicación móvil

Para reservar debe proporcionar únicamente:

* Nombre
* Apellido
* Número de WhatsApp

El backend debe crear o asociar internamente el Customer correspondiente.

El cliente no debe ver ni administrar una cuenta tradicional.

---

# 7. RESERVA SIN CUENTA

Cada reserva debe tener internamente información equivalente a:

* booking_id
* customer_id
* secure_token

El secure_token debe ser:

* criptográficamente seguro
* impredecible
* no secuencial
* no derivado del booking_id
* apropiado para acciones sin autenticación

El token permitirá acceder a acciones limitadas sobre una reserva.

Por ejemplo:

* Ver reserva
* Cancelar
* Reprogramar

Nunca expongas IDs internos sensibles innecesariamente.

---

# 8. FLUJO DE RESERVA

El flujo principal debe ser:

1. Cliente entra a la URL pública del negocio.
2. Selecciona un servicio.
3. Selecciona un profesional específico O "Cualquier profesional".
4. Selecciona fecha.
5. El sistema calcula disponibilidad real.
6. Se muestran horarios disponibles.
7. El cliente selecciona horario.
8. Introduce:

   * Nombre
   * Apellido
   * WhatsApp
9. El backend vuelve a verificar disponibilidad.
10. El backend crea la reserva dentro de una transacción.
11. La reserva queda automáticamente CONFIRMADA.
12. Se envía confirmación por WhatsApp.
13. Se notifica al profesional correspondiente.
14. Se programan los recordatorios.

NO debe existir un flujo de "esperando confirmación del profesional".

---

# 9. "CUALQUIER PROFESIONAL"

Esta funcionalidad es fundamental.

Ejemplo:

Una barbería tiene:

* Gabriel
* José
* Daniel

El cliente quiere:

Servicio: Corte

Profesional: Cualquier profesional

El sistema debe buscar profesionales que:

1. Pertenecen al negocio.
2. Están activos.
3. Pueden realizar el servicio.
4. Están trabajando en ese horario.
5. No están de vacaciones.
6. No están de licencia.
7. No tienen un bloqueo.
8. No tienen otro turno ocupando el horario.
9. Tienen disponibilidad suficiente para la duración del servicio.

El sistema debe devolver las opciones disponibles.

Si el cliente selecciona "Cualquier profesional", el backend puede asignar automáticamente uno de los profesionales elegibles según una estrategia definida.

La estrategia debe ser configurable posteriormente.

Nunca realices esta asignación solamente desde frontend.

---

# 10. SERVICIOS

Cada negocio podrá crear servicios.

Un servicio debe poder tener al menos:

* Nombre
* Descripción
* Duración
* Precio
* Estado activo/inactivo

Ejemplos:

Corte — 45 min

Corte + barba — 60 min

Coloración — 120 min

Maquillaje — 90 min

Cada profesional debe poder tener una relación con los servicios que puede realizar.

No todos los profesionales necesariamente realizan todos los servicios.

---

# 11. HORARIOS DEL NEGOCIO

NO crear un sistema rígido de "descanso".

Cada negocio debe configurar sus propias ventanas de funcionamiento.

Ejemplo:

Lunes:

08:00–12:00

17:00–21:00

Otro negocio:

08:00–18:00

Otro:

10:00–14:00

16:00–22:00

El sistema debe soportar:

* Negocio abierto/cerrado.
* Una ventana.
* Múltiples ventanas.
* Horarios diferentes por día.
* Cambios excepcionales.
* Feriados.

Los horarios deben ser configurables desde el panel administrativo.

El sistema NUNCA debe ofrecer un turno fuera de estas ventanas.

---

# 12. DISPONIBILIDAD DEL PROFESIONAL

La disponibilidad real debe considerar como mínimo:

* Horario del negocio.
* Disponibilidad del profesional.
* Servicios que puede realizar.
* Duración del servicio.
* Reservas existentes.
* Bloqueos.
* Vacaciones.
* Licencias/ausencias.
* Feriados.
* Cambios excepcionales.

Ejemplo:

Negocio:

08:00–18:00

Profesional:

09:00–17:00

Servicio:

45 minutos

Reserva existente:

11:00–11:45

El sistema debe calcular correctamente los huecos disponibles.

No utilices simplemente una lista fija de horarios.

La disponibilidad debe ser calculada dinámicamente.

---

# 13. BLOQUEOS

El negocio debe poder bloquear horarios específicos de un profesional.

Ejemplo:

Gabriel:

14:00–15:30

Motivo:

Trámite personal

Ese horario no debe aparecer como disponible.

Los bloqueos deben coexistir correctamente con reservas, horarios, vacaciones y otras reglas.

---

# 14. VACACIONES Y AUSENCIAS

Debe existir soporte para:

* Vacaciones
* Licencias
* Ausencias
* Bloqueos específicos

Las vacaciones/ausencias pertenecen al profesional.

Los feriados generales pertenecen al negocio.

El motor de disponibilidad debe considerar todas estas reglas.

---

# 15. DOBLE RESERVA

La prevención de double booking es CRÍTICA.

NO confiar solamente en:

* React
* JavaScript
* validaciones frontend
* consultas previas simples

Debe existir protección real en backend y base de datos.

La creación de una reserva debe ser transaccional.

Debes considerar:

* concurrencia
* race conditions
* locks
* constraints
* transacciones
* aislamiento apropiado

Dos usuarios intentando reservar simultáneamente el mismo horario NO deben poder obtener dos reservas confirmadas.

---

# 16. CANCELACIÓN

El profesional y el administrador NO deben confirmar ni cancelar reservas.

La reserva se confirma automáticamente al realizarse.

El cliente podrá cancelar mediante:

* WhatsApp
* enlace seguro

Al cancelar:

1. Se valida el secure_token.
2. Se verifica que la reserva pueda cancelarse.
3. Se cambia el estado.
4. Se libera inmediatamente el horario.
5. Se actualiza la agenda.
6. Se notifica al profesional cuando corresponda.

El horario debe volver a estar disponible automáticamente.

---

# 17. REPROGRAMACIÓN

El cliente debe poder reprogramar.

Flujo:

1. Cliente recibe WhatsApp.
2. Presiona "Reprogramar".
3. Se abre una URL segura.
4. El sistema identifica la reserva mediante secure_token.
5. Muestra los nuevos horarios disponibles.
6. Cliente selecciona nuevo horario.
7. Backend vuelve a validar disponibilidad.
8. Se realiza el cambio dentro de una transacción.
9. Se actualiza la reserva.
10. Se envían las notificaciones correspondientes.

No permitir inconsistencias entre la reserva anterior y la nueva.

---

# 18. WHATSAPP

WhatsApp es una pieza CENTRAL del producto.

Utilizar:

META WHATSAPP BUSINESS PLATFORM / CLOUD API

NO utilizar automatizaciones basadas en WhatsApp Web.

NO utilizar scraping.

NO depender de una sesión de WhatsApp Web.

Debe existir una arquitectura preparada para:

* mensajes
* plantillas
* webhooks
* botones/interacciones cuando correspondan
* reintentos
* errores
* logs
* idempotencia

---

# 19. WHATSAPP AL RESERVAR

Al crear una reserva:

### Cliente

Debe recibir confirmación con información como:

* Negocio
* Servicio
* Profesional
* Fecha
* Hora
* Opciones para gestionar la reserva

### Profesional

Debe recibir información como:

* Nuevo turno
* Cliente
* Servicio
* Fecha
* Hora

---

# 20. RECORDATORIOS

Cada reserva debe generar automáticamente dos recordatorios:

* 2 horas antes
* 1 hora antes

El mensaje debe contener:

* Negocio
* Servicio
* Profesional
* Fecha/hora
* Botón/opción Cancelar
* Botón/opción Reprogramar

Los recordatorios deben ser enviados mediante un sistema de tareas/background jobs.

NO bloquear una request HTTP esperando el momento del recordatorio.

---

# 21. AGENDA DEL PROFESIONAL

El profesional debe tener un panel.

Debe poder consultar:

* Turnos de hoy
* Próximos turnos
* Historial
* Clientes
* Servicios que realiza
* Horarios
* Bloqueos
* Vacaciones/ausencias según permisos

La agenda debe actualizarse automáticamente cuando:

* se reserva
* se cancela
* se reprograma

---

# 22. AGENDA POR WHATSAPP

Posteriormente se puede ofrecer un resumen diario.

Ejemplo:

"Agenda de hoy — Gabriel"

09:00 — Juan Pérez — Corte

10:00 — Pedro Gómez — Corte + barba

11:30 — Disponible

12:15 — María López — Corte

Debe ser claro y compacto.

---

# 23. WALK-IN

El sistema debe contemplar eventualmente clientes que llegan físicamente sin reserva.

Desde el panel profesional/admin se podrá registrar una reserva manual.

Ejemplo:

"Agregar cliente sin reserva"

Esto permite que la agenda represente la realidad.

Esta funcionalidad puede implementarse después del flujo principal de reservas online.

---

# 24. PANEL ADMINISTRATIVO

El administrador del negocio debe poder gestionar:

### Negocio

* Nombre
* Descripción
* Logo
* Imagen
* Dirección
* WhatsApp
* Configuración
* Slug público

### Profesionales

* Crear
* Editar
* Activar/desactivar
* Servicios
* Horarios
* Vacaciones
* Licencias
* Bloqueos

### Servicios

* Crear
* Editar
* Precio
* Duración
* Activar/desactivar

### Horarios

* Horarios por día
* Múltiples ventanas
* Excepciones

### Feriados

* Crear
* Editar
* Eliminar

### Reservas

* Consultar
* Filtrar
* Historial

IMPORTANTE:

El administrador NO debe confirmar manualmente reservas online.

---

# 25. SEGURIDAD

La seguridad debe diseñarse desde el principio.

Implementar:

* JWT
* Access tokens
* Refresh token rotation
* Argon2
* Validación de permisos
* RBAC
* Rate limiting
* Validación de entrada
* Protección contra IDOR
* Protección contra mass assignment
* CORS correctamente configurado
* CSRF cuando corresponda
* Sanitización
* Logs de seguridad
* Gestión segura de secretos
* Variables de entorno
* Secure cookies cuando correspondan
* HTTPS en producción

Nunca guardar contraseñas en texto plano.

Nunca incluir secretos en el repositorio.

Nunca confiar en datos provenientes del frontend.

---

# 26. STACK TECNOLÓGICO

## Frontend

React

TypeScript

Vite

Tailwind CSS

shadcn/ui

TanStack Query

React Hook Form

Zod

Vitest

Playwright

---

## Backend

Python

FastAPI

Pydantic

SQLAlchemy 2.x

Alembic

Pytest

---

## Base de datos

PostgreSQL

La base de datos debe diseñarse pensando en:

* integridad
* índices
* relaciones
* concurrencia
* escalabilidad
* multi-tenancy

---

## Infraestructura

Docker

Docker Compose

Redis

Sistema de background jobs

---

## Archivos

Utilizar:

Cloudflare R2

o almacenamiento S3-compatible.

NO guardar imágenes grandes directamente dentro de PostgreSQL.

---

# 27. AUTENTICACIÓN

Los usuarios internos sí tendrán autenticación.

Por ejemplo:

* Platform Admin
* Business Admin
* Professional

El cliente público NO tendrá login.

Utilizar:

JWT access token

*

Refresh token rotation

Las contraseñas deben almacenarse mediante:

Argon2

Implementar RBAC correctamente.

---

# 28. ARQUITECTURA

Utilizar:

MODULAR MONOLITH

NO microservicios inicialmente.

La aplicación debe estar modularizada para que posteriormente pueda evolucionar.

Módulos principales:

* auth
* businesses
* professionals
* services
* schedules
* availability
* bookings
* customers
* notifications
* payments
* reports

---

# 29. ESTRUCTURA PROPUESTA

```text
project/
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── features/
│   │   ├── hooks/
│   │   ├── services/
│   │   ├── types/
│   │   └── lib/
│   │
│   └── package.json
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── repositories/
│   │   └── workers/
│   │
│   ├── migrations/
│   └── pyproject.toml
│
├── tests/
│
├── docker-compose.yml
├── .env.example
├── README.md
└── ARCHITECTURE.md
```

Puedes modificar esta estructura si existe una razón técnica clara.

---

# 30. API

La API debe utilizar:

REST

OpenAPI

Versionado:

/api/v1/

Ejemplos:

GET /api/v1/businesses

GET /api/v1/businesses/{id}

GET /api/v1/businesses/{id}/services

GET /api/v1/businesses/{id}/professionals

GET /api/v1/availability

POST /api/v1/bookings

GET /api/v1/bookings

PATCH /api/v1/bookings/{id}

DELETE /api/v1/bookings/{id}

Los endpoints definitivos deben diseñarse según los casos de uso reales.

No crear endpoints innecesarios.

---

# 31. FRONTEND

La interfaz debe ser:

* moderna
* profesional
* responsive
* rápida
* accesible
* clara
* mobile-first donde tenga sentido

Utilizar componentes reutilizables.

Evitar:

* componentes gigantes
* lógica duplicada
* estados globales innecesarios
* estilos repetidos
* llamadas API dispersas
* lógica de negocio dentro de componentes visuales

Separar correctamente:

UI

estado

API

lógica de negocio

tipos

validación

---

# 32. MOTOR DE DISPONIBILIDAD

Este será uno de los componentes más importantes del sistema.

Diseñarlo como una pieza de dominio independiente.

Debe poder recibir:

* business
* service
* professional opcional
* fecha
* duración

y considerar:

* horarios del negocio
* horarios profesionales
* reservas
* bloqueos
* vacaciones
* ausencias
* feriados
* excepciones

Debe devolver únicamente slots realmente reservables.

Debe existir una única fuente de verdad.

NO duplicar la lógica de disponibilidad entre frontend y backend.

El frontend puede previsualizar.

El backend siempre debe validar nuevamente.

---

# 33. DINERO

Los precios deben manejarse de manera segura.

NO utilizar floats para dinero.

Utilizar:

Decimal

o representación equivalente apropiada.

Preparar la arquitectura para incorporar Mercado Pago posteriormente.

NO implementar pagos antes de que el núcleo de reservas esté correctamente terminado.

---

# 34. MAPAS

Los mapas no son necesarios para el MVP inicial.

Cuando se incorporen:

preferir:

MapLibre

OpenStreetMap

Evitar dependencia innecesaria de proveedores propietarios.

---

# 35. EMAIL

El email no es obligatorio para el cliente.

Podrá incorporarse posteriormente para:

* administración
* recuperación de contraseña
* notificaciones internas
* comunicaciones empresariales

WhatsApp tiene prioridad para el cliente.

---

# 36. TESTING

Todo componente crítico debe tener tests.

Backend:

Pytest

Frontend:

Vitest

E2E:

Playwright

Especial atención a:

* disponibilidad
* reservas
* concurrencia
* cancelaciones
* reprogramaciones
* "cualquier profesional"
* horarios
* vacaciones
* bloqueos
* feriados
* multi-tenancy
* permisos
* tokens seguros

---

# 37. CASOS CRÍTICOS QUE DEBES PROBAR

### Caso 1

Dos clientes intentan reservar simultáneamente el mismo horario.

Resultado esperado:

Solo una reserva confirmada.

---

### Caso 2

Cliente cancela una reserva.

Resultado:

El horario vuelve a estar disponible.

---

### Caso 3

Cliente reprograma.

Resultado:

La reserva anterior deja de ocupar el horario anterior y la nueva ocupa el nuevo horario.

---

### Caso 4

Cliente selecciona "Cualquier profesional".

Resultado:

Solo aparecen profesionales que realmente pueden realizar el servicio y están disponibles.

---

### Caso 5

Profesional está de vacaciones.

Resultado:

No aparecen sus horarios.

---

### Caso 6

Existe un bloqueo.

Resultado:

No aparece disponibilidad durante el bloqueo.

---

### Caso 7

Negocio cerrado.

Resultado:

No aparecen turnos.

---

### Caso 8

Servicio dura 90 minutos.

Resultado:

No ofrecer un slot si no existe una ventana continua de 90 minutos.

---

### Caso 9

Cliente intenta manipular booking_id.

Resultado:

No debe poder acceder a reservas ajenas.

---

### Caso 10

Cliente utiliza un token inválido.

Resultado:

Acceso rechazado.

---

# 38. MVP

El MVP debe priorizar:

1. Arquitectura
2. Base de datos
3. Autenticación interna
4. Negocios
5. Profesionales
6. Servicios
7. Horarios
8. Disponibilidad
9. Reservas
10. "Cualquier profesional"
11. Cancelación
12. Reprogramación
13. Panel profesional
14. Panel administrador
15. URL pública del negocio
16. WhatsApp
17. Recordatorios
18. Tests
19. Seguridad
20. Docker

Después:

* estadísticas avanzadas
* Mercado Pago
* mapas
* email avanzado
* marketplace
* dominios personalizados
* funciones avanzadas de WhatsApp
* funcionalidades SaaS avanzadas

---

# 39. REGLAS DE DESARROLLO

NO inventes APIs.

NO inventes respuestas del backend.

NO agregues dependencias sin justificar.

NO cambies el stack sin explicarlo.

NO implementes microservicios.

NO dupliques lógica de negocio.

NO pongas lógica crítica solamente en frontend.

NO uses mocks como sustituto de funcionalidades reales salvo que explícitamente te lo solicite.

NO ocultes errores.

NO ignores errores de compilación.

NO marques una funcionalidad como terminada si no fue probada.

NO afirmes que algo funciona si no fue ejecutado/verificado.

---

# 40. MANEJO DE CAMBIOS

Antes de modificar una parte importante:

1. Analiza el código existente.
2. Identifica dependencias.
3. Explica qué vas a modificar.
4. Implementa el cambio.
5. Ejecuta los tests relevantes.
6. Verifica errores.
7. Actualiza documentación.

No sobrescribas funcionalidades existentes sin analizar su impacto.

---

# 41. DOCUMENTACIÓN

Mantener actualizados:

README.md

ARCHITECTURE.md

.env.example

Documentación de API cuando corresponda.

La documentación debe reflejar el estado REAL del proyecto.

No documentes funcionalidades inexistentes.

---

# 42. VARIABLES DE ENTORNO

Nunca hardcodear:

* passwords
* JWT secrets
* API keys
* WhatsApp tokens
* database credentials
* Mercado Pago credentials
* storage credentials

Crear:

.env.example

con variables necesarias y valores de ejemplo seguros.

---

# 43. LOGS Y OBSERVABILIDAD

Preparar el sistema para registrar:

* errores
* reservas
* cancelaciones
* reprogramaciones
* notificaciones
* webhooks
* eventos importantes
* errores de integración

Los logs NO deben exponer:

* contraseñas
* tokens
* datos sensibles innecesarios
* credenciales

---

# 44. IDEMPOTENCIA

Las operaciones sensibles deben contemplar idempotencia.

Especialmente:

* creación de reservas
* webhooks
* envío de notificaciones
* cancelaciones
* reprogramaciones

Un mismo evento recibido dos veces NO debe generar dos efectos.

---

# 45. WHATSAPP Y JOBS

Las tareas como:

* recordatorios
* notificaciones
* mensajes programados
* procesamiento de webhooks

deben ejecutarse mediante workers/background jobs.

NO mantener requests HTTP abiertas esperando tareas.

Redis puede utilizarse como infraestructura para jobs y cache.

---

# 46. EXPERIENCIA DEL CLIENTE

El cliente debe poder reservar en pocos pasos.

Idealmente:

Servicio

→ Profesional

→ Fecha

→ Hora

→ Datos

→ Confirmación

La experiencia debe ser extremadamente sencilla desde móvil.

No pedir información innecesaria.

---

# 47. EXPERIENCIA DEL NEGOCIO

El administrador debe poder configurar su negocio sin conocimientos técnicos.

Debe poder entender fácilmente:

* agenda
* disponibilidad
* profesionales
* servicios
* horarios
* reservas
* clientes

---

# 48. PRINCIPIO FUNDAMENTAL

La plataforma debe representar la REALIDAD.

Si un profesional está ocupado:

NO mostrar disponibilidad.

Si está de vacaciones:

NO mostrar disponibilidad.

Si el negocio está cerrado:

NO mostrar disponibilidad.

Si un cliente cancela:

LIBERAR el horario.

Si dos clientes intentan reservar simultáneamente:

SOLO UNO debe conseguirlo.

Si un servicio dura 90 minutos:

NO ofrecer un hueco de 60 minutos.

Si un profesional no realiza el servicio:

NO asignarlo.

Si un negocio cambia sus horarios:

La disponibilidad debe reflejarlo.

---

# 49. FORMA DE TRABAJAR CONMIGO

Quiero que trabajes conmigo como un Senior Tech Lead.

Cuando te pida implementar algo:

### Primero

Analiza.

### Después

Explica brevemente la solución.

### Luego

Implementa.

### Después

Prueba.

### Finalmente

Indica:

* Qué se modificó.
* Qué archivos se modificaron.
* Qué tests se ejecutaron.
* Resultado de los tests.
* Problemas encontrados.
* Qué queda pendiente.

No me entregues simplemente código sin contexto.

---

# 50. REGLA DE ORO

CALIDAD > VELOCIDAD

CORRECCIÓN > CANTIDAD DE FUNCIONALIDADES

SEGURIDAD > COMODIDAD

ARQUITECTURA LIMPIA > PARCHE RÁPIDO

DATOS REALES > MOCKS

TESTS REALES > "PARECE FUNCIONAR"

---

# 51. PRIMERA TAREA

NO empieces programando inmediatamente.

Primero realiza una fase de arquitectura.

Quiero que me entregues:

1. Nombre técnico recomendado para el proyecto.
2. Arquitectura general.
3. Diagrama conceptual de componentes.
4. Modelo de datos completo.
5. Relaciones entre entidades.
6. Estrategia multi-tenant.
7. Estrategia de disponibilidad.
8. Estrategia anti-double-booking.
9. Estrategia de autenticación.
10. Estrategia de secure tokens.
11. Arquitectura de WhatsApp.
12. Arquitectura de background jobs.
13. Estructura definitiva del proyecto.
14. Diseño de API.
15. Estrategia de testing.
16. Estrategia de seguridad.
17. Estrategia de despliegue.
18. Roadmap por fases.
19. Riesgos técnicos.
20. Decisiones arquitectónicas y justificación.

NO escribas código de producción todavía.

Primero quiero validar la arquitectura.

Una vez aprobada la arquitectura, comenzaremos la implementación desde cero, siguiendo las fases y sin saltarnos pasos.
