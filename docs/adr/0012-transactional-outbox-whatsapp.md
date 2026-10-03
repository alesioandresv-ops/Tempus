# ADR-0012: Outbox transaccional y plantillas obligatorias en WhatsApp

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §18, §19, §20, §44
- **Contexto en:** ARCHITECTURE.md §12

## Contexto

La §20 exige que cada reserva genere automáticamente dos recordatorios, a las 2 horas
antes y a la 1 hora antes, con botones de cancelar y reprogramar. La §19 exige
confirmación inmediata al reservar, para el cliente y para el profesional.

Hay dos restricciones que la §20 y la §18 no mencionan y que definen la arquitectura:

**La primera: la ventana de 24 horas de Meta.** Fuera de la ventana de servicio al
cliente —que se abre cuando el cliente escribe y dura 24 horas— la Cloud API **solo
acepta plantillas aprobadas**. Un recordatorio de "2 horas antes" cae sistemáticamente
fuera de esa ventana: quien reserva un viernes para el lunes y recibe un recordatorio el
lunes a las 15:00 no ha interactuado con el número en dos días. La línea de atención no
está abierta.

La consecuencia es directa: **los recordatorios tienen que usar plantillas**. No es una
optimización ni una alternativa de implementación; es la única forma en que la
funcionalidad del §20 puede existir.

**La segunda: la atomicidad.** La reserva y sus tres mensajes (confirmación al cliente,
aviso al profesional, dos recordatorios) tienen que ser consistentes. Un doble
escritura —una en la base, otra en un broker— puede dejar mensajes de reservas que no
existen, o reservas sin sus recordatorios. Y "sin sus recordatorios" es un fallo
silencioso: el cliente no reserva, no recibe nada, y nadie se entera hasta que no llega.

## Decisión

### 1. Las notificaciones se escriben en la misma transacción que la reserva

```sql
BEGIN
  SET LOCAL app.current_business_id = :tenant

  INSERT INTO bookings (...) ...                    -- la reserva
  INSERT INTO notification_requests (...) ...       -- kind = booking_confirmed
  INSERT INTO notification_requests (...) ...       -- kind = reminder_2h
  INSERT INTO notification_requests (...) ...       -- kind = reminder_1h
  INSERT INTO jobs (...) ...                        -- "enviar ahora"
COMMIT
```

Si la transacción falla, no hay reserva **ni** mensajes huérfanos. Si el envío falla
después, la reserva sigue existiendo y el mensaje se reintenta.

Por eso el outbox vive en la base y no en el broker (ADR-0011): el doble escritura
entre base y broker no es reversible, y la reserva **no se puede perder**. Un mensaje
se puede reenviar; una reserva, no.

### 2. Los recordatorios usan plantillas, siempre

`notification_requests.template_id` no es opcional. La selección de plantilla ocurre en
el momento de renderizar, no en el de encolar, para que cambiar el texto de una plantilla
no obligue a reencolar nada.

Consecuencias aceptadas:

- Las plantillas requieren **aprobación previa de Meta**, con lead time de horas o días
  y posibilidad de rechazo. Por eso existe la fase 0.5 del roadmap, que arranca en
  paralelo con la fase 0 y no cuando llega la fase 7.
- Cada idioma necesita su plantilla aprobada. El MVP es `es-AR`; si el `locale` del
  cliente no tiene plantilla, se usa la del negocio y se registra el fallback en el log,
  en vez de no enviar nada.
- Los botones de cancelar y reprogramar requieren que la plantilla se apruebe **como
  interactiva**. Los payloads llevan el `secure_token`, de modo que la respuesta
  funcione sin cuenta.

### 3. Idempotencia por unicidad, no por memoria del worker

`UNIQUE (booking_id, kind)`. Un recordatorio de 2 horas existe **una vez por reserva,
para siempre**. La garantía no depende de que el worker recuerde qué mandó.

Al cancelar o reprogramar, en la **misma transacción** los `notification_requests`
pendientes de esa reserva pasan a `cancelled`. Así un recordatorio de 2 horas no puede
salir después de una cancelación, aunque ya estuviera en la cola de envío.

### 4. Envío con clasificación de errores

Backoff exponencial con jitter, tope de intentos, y dead-letter. Los errores de Meta se
separan en reintentables (5xx, rate limit, timeout) y no reintentables (número inválido,
plantilla rechazada, opt-out). Los no reintentables no se reintentan, porque queman
cuota sin ninguna posibilidad de éxito.

Se registra el costo estimado por mensaje. La factura de Meta es el gasto variable más
grande del producto después del hosting, y no puede ser una sorpresa (riesgo R-05).

## Alternativas consideradas

### Enviar el mensaje dentro de la transacción que crea la reserva — descartada

Viola la §45 de forma directa: mantiene la request HTTP abierta mientras espera a Meta, y
si la llamada a Meta tarda 5 segundos, el cliente espera 5 segundos. Además, un fallo de
Meta abortaría la reserva, cuando el mensaje es accesorio y la reserva no.

### Publicar en el broker dentro de la transacción — descartada

No es atómico con la base. Si el commit falla después del publish, hay un mensaje de una
reserva que no existe. Es el problema clásico del doble escritura, y es exactamente lo
que el outbox resuelve.

### Enviar sin outbox, reenviar si falla, y confiar en un job de reconciliación —
descartada

Se pierde el mensaje si el proceso muere entre el commit y el publish. La reconciliación
existe, pero es un parche para una pérdida conocida, y el outbox no cuesta más que
escribir la fila.

### Asumir que los recordatorios pueden ser mensajes libres (sesión de 24 horas) —
descartada por la política de Meta

Es el error que haría fallar silenciosamente el §20 en producción: los primeros mensajes
funcionarían, porque el cliente acaba de escribir, y los recordatorios de días después
no se enviarían nunca. La restricción existe desde 2021 y no se negocia.

### Un solo número de WhatsApp de la plataforma para todos los negocios — descartada

Se trata en el ADR-0013, porque tiene consecuencias de arquitectura propias.

### Enviar el recordatorio a las 24 horas exactas de la reserva, en lugar de a las 2
horas antes — descartada

Haría el recordatorio inútil: un cliente que reserva para dentro de una semana necesita
recordarla el día del turno, no el día de la reserva. La ventana de 24 horas es de
**recepción del mensaje entrante**, no de antigüedad de la reserva.

## Consecuencias

**Positivas**

- Reserva y mensajes consistentes por construcción, no por esfuerzo.
- Ningún recordatorio duplicado, aunque el worker se reinicie tres veces.
- Cancelar una reserva cancela sus recordatorios pendientes en la misma transacción.
- Un recordatorio de 2 horas que se rechaza se reintenta sin intervención humana.
- El costo de Meta es visible antes de ser una sorpresa.

**Negativas / costos aceptados**

- **Lead time externo y no controlado**: Meta puede rechazar una plantilla, y el
  rechazo puede no avisar. Es el riesgo R-01, el más alto de la lista. Mitigación: fase
  0.5 arranca temprano, plantillas conservadoras, y degradación documentada a un
  mensaje simple si la de recordatorio no se aprueba.
- Un idioma sin plantilla aprobada recibe un mensaje en el idioma del negocio. Para un
  cliente que no habla ese idioma, eso es una mala experiencia. Aceptado en el MVP, con
  el fallback registrado.
- Los botones de las plantillas requieren payloads que Meta limita en cantidad y tamaño.
  Si la reserva necesita más información de la que entra, hay que resumir. El diseño del
  mensaje tiene que contemplarlo desde el principio.
- La tabla `notification_requests` crece rápido. Hay que purgarla con una retención
  distinta por estado, y los `cancelled` son los más voluminosos.
- Cifrar el token de cada negocio con Fernet añade una dependencia y una clave más que
  rotar.

**Revisar si**

- Meta cambia su modelo de precios o su política de plantillas de forma incompatible.
- Un mercado exige plantillas en un idioma para el que no tenemos aprobación. Ahí
  hace falta un proceso de aprobación por idioma, no más código.
