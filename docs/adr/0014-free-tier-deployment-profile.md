# ADR-0014: Perfil de despliegue gratuito y heartbeat de Cloudflare

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §45, §50
- **Contexto en:** ARCHITECTURE.md §18

## Contexto

El proyecto no tiene presupuesto para plataformas de hosting de pago. Eso es un
requisito, no una preferencia, y la §45 pone una condición dura sobre cualquier
infraestructura gratuita: los recordatorios, notificaciones y mensajes programados
"deben ejecutarse mediante workers/background jobs" y no se pueden mantener requests
HTTP abiertas.

La investigación de los planes gratuitos dio un resultado incómodo y
específico, verificado en la documentación de cada proveedor:

- **Render Free**: los *background workers* **no existen** en el plan gratuito (arrancan
  en Starter, $7/mes). El web service gratuito se suspende tras 15 minutos sin tráfico
  entrante y tarda ~1 minuto en despertar.
- **Supabase Free**: el proyecto **se pausa tras una semana de baja actividad**, con
  500 MB de base y sin backups automáticos.
- **Neon Free**: no pausa el proyecto. Suspende el compute, la base siempre está, e
  incluye PITR de 6 horas. Límite de 0.5 GB y 5 GB/mes de egress.
- **Cloudflare**: DNS, proxy, WAF, Turnstile (ilimitado), R2 (10 GB), Pages y Workers
  con cron trigger, todo en free tier.
- **Vercel Hobby**: los términos son para uso personal y no comercial. Con clientes que
  pagan, no es una opción.

La conclusión es que **el perfil gratuito no es "todo en free tier"**: es una
combinación donde la pieza crítica (el worker) tiene que resolverse con ingenuity.

## Decisión

Perfil FREE, en $0:

| Componente | Servicio | Rol |
|---|---|---|
| Frontend | Cloudflare Pages | SPA estática con rewrite de rutas |
| API | Render Free web service | FastAPI |
| Scheduler | Cloudflare Worker con cron | Heartbeat + disparo del drenaje de jobs |
| DB | Neon Free | PostgreSQL |
| Media y backups | Cloudflare R2 | Logos, fotos, exportes |
| Edge | Cloudflare | DNS, TLS, WAF, Turnstile |

### El heartbeat: la pieza que hace viable el perfil

Un Cloudflare Worker con cron trigger golpea `POST /api/v1/internal/scheduler/tick`.
No es un adorno de monitoreo; resuelve dos problemas a la vez:

1. **Ese request entrante es tráfico**, así que Render **no se suspende**. El cold
   start de ~1 minuto desaparece para el usuario real. Sin el heartbeat, el primer
   visitante de la mañana espera un minuto; con él, espera lo que tarda la consulta.
2. **Dispara el drenaje de jobs vencidos**, de modo que los recordatorios no dependen de
   que alguien esté navegando la plataforma.

Dentro del proceso, un `asyncio` task hace tick cada 30 segundos como red de seguridad, por si
el cron falla.

El Worker vive en el free tier: 100.000 requests/día, 5 cron triggers por cuenta, y 10
ms de CPU por invocación, que es de sobra para un `fetch`.

### Seguridad del endpoint interno

- **Bearer token** secreto, comparado con `compare_digest` (tiempo constante).
- Refuerzo de diseño: el tick **solo puede reclamar jobs ya vencidos**. No puede
  encolar trabajo arbitrario ni elegir a quién se le manda un mensaje. El radio de daño
  de una fuga del secreto es "drenar lo que debía drenarse", no "enviar mensajes a
  cualquier número".
- Rate limit de 2/min por IP además del token.

### Degradaciones aceptadas de forma consciente

- **Puntualidad de los recordatorios** limitada al intervalo del cron. Con
  granularidad de 1–5 minutos, un recordatorio de "2 horas antes" puede llegar hasta 5
  minutos tarde: invisible para un cliente. Con un negocio de atención, sería
  inaceptable, y por eso PROD es un cambio de perfil, no una optimización.
- **Sin backups automáticos programados** en Neon Free. Hay 1 snapshot manual y PITR de
  6 horas. Mitigación: snapshot manual antes de cada migración y export periódico a R2.
  Registrado como riesgo R-06.
- 0.5 GB de base: suficiente para el orden de millones de reservas.
- **5 GB/mes de egress.** El API devuelve JSON chico, así que el consumo real es bajo;
  R2 no cuenta como egress de la base.
- El backend duerme si el cron falla, y con él los recordatorios. Se mitiga con una
  alerta sobre la salud del tick.

### Migración a PROD

El perfil PROD (Hetzner CX22, ~€3.79/mes) corre API + worker + Postgres + Caddy en un
solo servicio, y **es más barato que el Starter de Render** ($7/mes). Oracle Cloud
Always Free es la alternativa de $0 con capacidad siempre encendida.

La migración **no requiere cambios de código**: se levanta el mismo contenedor, se
apunta el dominio y se cambia la URL de la base. Lo único que se desactiva es el Worker
de cron, porque el servicio ya no se duerme.

Se documenta explícitamente para que la decisión de infraestructura de hoy no ate el
proyecto a un perfil gratuito.

## Alternativas consideradas

### Todo en free tier con GitHub Actions como scheduler — descartada

Un workflow programado de GitHub Actions cada 5 minutos podría correr el drenaje del
worker. Se descartó por dos razones duras:

1. **Requiere repo público.** En repositorio privado, los minutos gratuitos de Actions
   (2000/mes) no alcanzan para 288 ejecuciones mensuales de 5 minutos. Con repo
   público, se expone el código del producto a cualquier persona, y eso es una decisión
   de negocio, no de infraestructura.
2. **Deja el problema del cold start sin resolver.** El workflow Keeps Render despierto
   solo si corre cada 10 minutos, lo que duplica el gasto de minutos y deja huecos.

El Cloudflare Worker resuelve ambas cosas con una pieza que ya está pagada, que es
siempre activa y que además sirve para otras automatizaciones después.

### Supabase Free para la base — descartada

El proyecto se pausa a la semana de baja actividad. Una agenda de barbería con pocos
turnos diarios entra exactamente en ese umbral: la base desaparecería de un día para
otro, con un email de aviso una semana antes. Para una herramienta donde el cliente
paga por un servicio diario, una base que se va sola no es un riesgo aceptable, y el
free tier además no tiene backups automáticos.

Neon Free no pausa y trae PITR. Es estrictamente mejor para este caso.

### Vercel para el frontend — descartada por términos de uso

El plan Hobby es para uso personal y no comercial. Este proyecto tendrá negocios que
pagan. Cloudflare Pages no tiene esa restricción y además comparte cuenta, DNS, WAF y
R2 con el resto de la capa de borde.

### Un VPS propio barato desde el día uno — descartada por ahora

Hetzner CX22 a ~€3.79/mes es, en realidad, más barato que el Starter de Render y
resolvería el problema del worker sin ingenierías. Se descarta solo por el requisito
de $0, no por sus servicios. Queda documentado como el camino de migración.

### Servidor propio con Cloudflare Tunnel — descartada

Costo cero absoluto, pero la agenda pública dependería de la conexión de casa. El
producto es un SaaS para negocios: la disponibilidad no puede depender de que un
router de casa esté encendido. Descartada por lo que cuesta cuando falla.

## Consecuencias

**Positivas**

- $0 de hosting. La única factura variable es la de Meta por mensajes, que se controla
  y se rastrea.
- Todo el plano de ataque del booking público (WAF, rate limiting, CAPTCHA, TLS) en el
  free tier de Cloudflare.
- La base no se pausa por inactividad: la disponibilidad del producto no depende de
  que haya tráfico.
- El mismo código funciona en DEV (Docker Compose), FREE y PROD. El destino es
  configuración.
- La ruta de migración a PROD está escrita, probada conceptualmente y no requiere tocar
  el código de aplicación.

**Negativas / costos aceptados**

- **Una dependencia de free tier cuyo comportamiento no controlamos.** Si Render cambia
  su política, o Neon sube límites, el perfil se rompe. Riesgo R-07. Mitigación: la
  arquitectura no depende de estos límites, solo del perfil FREE.
- Los recordatorios tienen ±5 minutos de margen. Invisible para un cliente normal,
  inaceptable para un negocio de atención. Documentado.
- Sin backups automáticos programados. Es el riesgo residual más serio de este perfil
  (R-06) y la razón principal para migrar a PROD en cuanto haya un solo cliente
  pagando.
- El Worker de cron es un punto único de fallo silencioso. Si se rompe, los recordatorios
  se detienen sin error visible. Mitigación: alerta sobre la salud del tick y el tick
  interno de 30 s como red de seguridad.
- Latencia extra: la comunicación Cloudflare → Render → Neon suma un salto. Se mitiga
  eligiendo regiones alineadas.
- Cold start en el despliegue inicial de cada deploy, que afecta el primer request
  posterior al reinicio.

**Revisar si**

- Hay un primer cliente pagando: el argumento para migrar a PROD es inmediato, porque
  los backups automáticos dejan de ser opcionales.
- Los recordatorios pasan a tener valor contractual (un negocio que cobra por no
  llegar tarde).
- Los límites de Render o Neon cambian, o el volumen crece hasta hacerlos insuficientes.
