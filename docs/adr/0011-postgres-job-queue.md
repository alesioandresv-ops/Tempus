# ADR-0011: Cola de jobs en PostgreSQL

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §20, §44, §45
- **Contexto en:** ARCHITECTURE.md §13

## Contexto

La §45 exige que recordatorios, notificaciones, mensajes programados y procesamiento de
webhooks se ejecuten en background workers, y prohíbe mantener requests HTTP abiertas
esperando esas tareas. La §44 exige idempotencia: un mismo evento recibido dos veces no
debe generar dos efectos.

La §20 agrega un requisito que parece de negocio y es de infraestructura: los
recordatorios son a las **2 horas antes** y a la **1 hora antes**. Eso significa que
alguien tiene que despertarse exactamente a las 15:00 para mandar el mensaje de las
15:00, aunque en ese momento no haya tráfico en la plataforma.

En el perfil de despliegue elegido (ADR-0014) esto deja de ser un detalle: el backend
se duerme si no recibe tráfico, y **Render no ofrece background workers en su plan
gratuio**. Así que la infraestructura de jobs no es una decisión aislada: condiciona
qué plataformas son viables.

## Decisión

**La cola es la tabla `jobs` en PostgreSQL.** Sin broker externo.

```sql
WITH claimed AS (
  SELECT id FROM jobs
   WHERE status = 'pending' AND run_at <= now()
   ORDER BY run_at
   FOR UPDATE SKIP LOCKED
   LIMIT :batch
)
UPDATE jobs
   SET status = 'running', locked_at = now(), locked_by = :worker_id,
       attempts = attempts + 1
  FROM claimed
 WHERE jobs.id = claimed.id
RETURNING jobs.*;
```

Decisiones que importan:

- **`SKIP LOCKED`** permite N workers concurrentes sin bloquearse: cada uno toma las
  filas libres y deja las tomadas. Es lo que hace posible el paralelismo sin un
  coordinador.
- **`now()` es de la base**, nunca `datetime.utcnow()` del proceso. Un worker con reloj
  desfasado no debe ejecutar recordatorios fuera de tiempo ni retenerlos. La diferencia
  entre las dos opciones es de minutos, y en recordatorios es exactamente lo que el
  cliente nota.
- **Leasing con `locked_at` / `locked_by`**: un job en `running` cuyo lock tiene más
  tiempo que el timeout vuelve a `pending`. Sin esto, un worker que muere a mitad de un
  envío deja el mensaje sin despachar para siempre, y nadie se entera.
- **Índice parcial** `ON jobs (run_at) WHERE status = 'pending'`: el índice cubre
  exactamente la consulta del worker.

Colas separadas por dominio (`reminders`, `whatsapp_outbound`, `webhooks`, `media`,
`reports`) con concurrencia y backoff configurables por cola. Los reportes se pueden
calcular de noche; los recordatorios no pueden esperar. Una cola compartida con la
misma concurrencia obligaría a elegir entre las dos cosas.

### Por qué no Redis

Porque el requisito es que el outbox de WhatsApp se escriba **dentro de la transacción
que crea la reserva** (ADR-0012). Con Celery o Dramatiq sobre Redis, para cumplir eso
hay que escribir de todas formas una tabla de outbox en PostgreSQL y además correr un
relay que lee esa tabla y publica en Redis. Es decir: la base de datos sigue siendo
obligatoria, y encima se agrega el broker, el relay, su código y su operación.

La cola en PostgreSQL da la misma garantía de transactionalidad con un solo
componente, y deja el historial de jobs consultable con SQL: "este cliente recibió el
recordatorio a las 15:02" es un `SELECT`, no una búsqueda en una interfaz de
administración de Redis.

## Alternativas consideradas

### Celery + Redis — descartada

La opción por defecto en el ecosistema Python, y probablemente la que usaría cualquier
equipo sin pensar. Es battle-tested, tiene dashboard, reintentos, planificación y
muchos más workers.

Se descarta por el argumento del relay: para que la creación de la reserva y el
encolado del mensaje sean atómicos, Celery exige un outbox en la base más un proceso
que lo drena. Es exactamente el mismo trabajo que hace la tabla `jobs`, con dos
infraestructuras adicionales y un modo de falla adicional (el relay muere y la base se
acumula).

Sus ventajas reales —throughput, ecosistema, herramientas de monitoreo— no son el
cuello de botella aquí. El volumen de jobs es el de las reservas de negocios pequeños:
decenas por hora, no millones.

### Dramatiq + Redis — descartada

Más liviana y con mejor experiencia que Celery, con el mismo problema estructural: no
ofrece atomicidad entre la base y el broker sin un outbox.

### `apscheduler` / cron del sistema — descartada

Nadie puede garantizar la entrega de un job individual, ni reintentos por job, ni
visibilidad de un job fallido, ni dead-letter. Un recordatorio fallido se pierde en
silencio, que es la peor falla posible en una función que el cliente nunca ve
funcionar.

### Cola en una tabla con polling desde la propia API — aceptada como parte de la
decisión

Es lo que se hace, y tiene una ventaja que no había considerado: el drain corre en el
proceso de la API, así que en desarrollo no hay un segundo proceso que levantar. En
producción, el trigger externo (Cloudflare Worker con cron) es lo que garantiza que el
proceso esté vivo. Es la pieza que hace viable el plan gratuito.

### Amazon SQS / Google Pub-Sub — descartadas

Buenas colas, pero agregan un proveedor más, tienen costo por operación, y traen el
mismo problema de atomicidad que Redis: outbox obligatorio.

## Consecuencias

**Positivas**

- Una sola infraestructura. Menos piezas que pueden fallar y menos que operar.
- El outbox y los jobs viven en la misma transacción y la misma base: si la reserva se
  creó, el recordatorio existe.
- Historial consultable con SQL, que es lo que se necesita para responder "¿se envió?".
- `SKIP LOCKED` da paralelismo real sin bloqueos entre workers.
- Sin costo adicional: no hay un servicio que cobrar.

**Negativas / costos aceptados**

- Throughput menor que un broker dedicado. Irrelevante a este volumen, pero es la razón
  por la que esta decisión se revisaría.
- Un job muy largo puede mantener locks y entorpecer el drenaje. Mitigación: lease
  curto con renovación, y ningún handler debe hacer trabajo largo.
- `UPDATE ... RETURNING` con `SKIP LOCKED` es un poco menos familiar que
  `SELECT FOR UPDATE` + `UPDATE`. Hay un test de concurrencia que valida el
  comportamiento real.
- La tabla `jobs` crece y hay que limpiarla. Un job de mantenimiento la purga
  periódicamente, con retención distinta según el estado.
- Una consulta mal escrita sobre `jobs` sin el índice puede degradar el worker. El
  índice parcial y las rutas de código las controlan.

**Revisar si**

- El volumen de jobs supera lo que PostgreSQL maneja cómodamente. Señal concreta: el
  tiempo de drenaje de una cola con carga real supera el intervalo entre ticks.
- Aparece un requisito de jobs programados muy lejano en el tiempo (meses), donde
  consultar `jobs` para el siguiente job vencidos se vuelva ineficiente.
- Se necesita un broker con entrega garantizada entre servicios, si algún día la
  arquitectura deja de ser un monolito.
