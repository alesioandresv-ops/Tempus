# ADR-0020: Panel administrativo de plataforma con sesión firmada

- **Estado:** Aceptada
- **Fecha:** 2026-10-25
- **Referencia:** ARCHITECTURE.md §5.8 (RLS), §22 (decisiones aplazadas)
- **Migración:** `0029_admin_panel_seguro`

## Contexto

El panel administrativo existía como una API JSON en `/api/v1/admin?key=ADMIN_SECRET`.
La clave viajaba en la query string, así que terminaba en logs de acceso, historial del
navegador y proxies; además no había logout, ni vencimiento, ni rastro de quién activó
un Pro o bloqueó un negocio. El panel tampoco tenía interfaz: el operador dependía de
clientes HTTP para tareas de soporte cotidianas.

La tentación era reutilizar la sesión de negocio (`/auth/login`). No sirve: esa sesión
está atada a un tenant y el panel administra la plataforma entera. Convertir a un
miembro de negocio en operador de plataforma por tener un flag es exactamente el
escalamiento que el §25 pide cerrar.

## Decisión

**Superficie separada, server-side.** `GET/POST /admin/login` renderiza Jinja2 y
`/admin` es el tablero. No comparte cookie, prefijo de API ni sesión con el producto.
El panel JSON legacy sigue existiendo por compatibilidad, pero solo se autentica con
la cookie o con `?key=` cuando `ALLOW_LEGACY_ADMIN_KEY=true` (default `false`).

**Una sola cuenta de operador.** `ADMIN_USER` + `ADMIN_PASSWORD_HASH` (bcrypt) +
`SECRET_KEY` (HMAC de `itsdangerous`). No hay tabla de operadores todavía: el panel
administra la plataforma, no un tenant, y un solo operador no justifica el modelo de
roles. Cuando haya más de uno se reemplaza por una tabla, no por una lista en una
variable.

**Sesión firmada, no JWT.** `itsdangerous` firma un payload chico (`user` + token
CSRF) en una cookie `HttpOnly`, `Secure`, `SameSite=Strict`, `max-age=1800`. La sesión
no viaja legible al cliente y no hay refresh token que robar. El CSRF de las acciones
vive dentro de esa cookie firmada; el del login usa double-submit firmado, porque
todavía no hay sesión donde guardarlo. Todo `POST` lo valida.

**El login se limita antes de verificar.** 5 intentos por IP cada 15 minutos, contados
antes de comparar credenciales. Contar solo los fallos permitiría probar passwords
ilimitadas mientras se acierta. La IP sale de `request.client.host`, no de una cabecera
que el cliente controle.

**La auditoría es de plataforma.** `admin_audit_log(admin_user, action, business_id
NULL, ip, created_at)` es global y no lleva RLS: `business_id` es el **objetivo** de la
acción, no el dueño de la fila, y un login no tiene negocio. La escribe únicamente el
rol de migraciones; `tempus_app` no tiene grants. No tiene FK a `businesses` para que
borrar un negocio no borre la historia de quién lo bloqueó.

**Lectura cruzada acotada.** El panel necesita el email del último comprobante y los
pagos pendientes, y `pro_payments` tiene `FORCE RLS` con un GUC que el engine
administrativo no fija. La migración `0029` agrega **una política SELECT solo para
`tempus_owner`** en `pro_payments`. Las políticas de `tempus_app` no se tocan: un
request de negocio sigue viendo únicamente su tenant.

**Las escrituras tienen una sola implementación.**
`app.modules.admin.service` concentra activar/bloquear/desbloquear y la auditoría; el
panel HTML y la API legacy la llaman. El `UPDATE` sobre `businesses` mantiene el
`set_config('app.current_business_id', ...)` que exige `businesses_write`; la RLS no se
relaja.

## Alternativas consideradas

- **Mantener `?key=` y solo agregar una página.** Descartada: la clave sigue en logs e
  historial y no hay forma de cerrar sesión ni de saber quién hizo cada acción.
- **SPA + JWT en el panel.** Descartada: agrega superficie (CORS, almacenamiento del
  token, refresh) para una pantalla que se sirve mejor desde el mismo proceso. La
  cookie `HttpOnly` no necesita JavaScript para existir.
- **Reutilizar la cookie de refresh del producto.** Descartada: mezcla la identidad de
  negocio con la de plataforma y hace que un bug de scopes sea un bug de plataforma.
- **`BYPASSRLS` para el rol administrativo.** Descartada: le daría lectura de todas las
  tablas de tenant para siempre. La política dirigida a `tempus_owner` sobre una tabla
  puntual es más chica y auditable.
- **Guardar la auditoría en una tabla de tenant.** Descartada: los logins no tienen
  tenant y la política por `business_id` impediría registrarlos.
- **Basic Auth.** Descartada: no tiene logout real, ni CSRF, ni límite por IP, y el
  navegador reenvía las credenciales en cada request.

## Consecuencias

- El panel exige HTTPS en producción: la cookie es `Secure` siempre. En local se prueba
  con un cliente ASGI sobre `https://test`, no relajando la bandera.
- La sesión vence a los 30 minutos; el operador vuelve a entrar. Es deliberado para una
  computadora compartida.
- Cambiar `ADMIN_USER` invalida las sesiones vivas; cambiar la password exige regenerar
  el hash y reiniciar el proceso (no hay UI de rotación todavía).
- El rol `tempus_owner` puede leer `pro_payments` de todos los negocios. Es el mismo rol
  que ya ejecuta las migraciones y aplica los `UPDATE` administrativos; el permiso no se
  extiende a `tempus_app`.
- `ALLOW_LEGACY_ADMIN_KEY` queda como puente de migración y debe borrarse cuando ningún
  cliente use `/api/v1/admin?key=`.
