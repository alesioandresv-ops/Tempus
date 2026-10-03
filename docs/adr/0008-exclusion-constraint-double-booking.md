# ADR-0008: `EXCLUDE USING gist` como autoridad anti-doble-reserva

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §15, §48
- **Contexto en:** ARCHITECTURE.md §9

## Contexto

La §15 declara la prevención de doble reserva **crítica** y prohíbe explícitamente
confiar en React, JavaScript, validaciones de frontend o "consultas previas simples".
La §48 lo repite como principio: "si dos clientes intentan reservar simultáneamente,
solo uno debe conseguirlo".

La causa del bug es siempre la misma y no es un descuido de implementación: **check-then-
act**. El patrón es

```python
existe = await libre(slot)          # SELECT
if existe:
    await crear_reserva(slot)       # INSERT
```

Entre esas dos líneas, otra transacción puede pasar el mismo `SELECT`, obtener el mismo
`existe = True` y escribir también. Ninguna de las dos cometió un error: cada una hizo
lo que le pedían. El resultado son dos reservas confirmadas en el mismo horario, que es
exactamente lo que el §48 prohíbe.

Esto no se arregla con más cuidado en el código. Se arregla con una garantía que no
dependa del orden en que se ejecutan las sentencias.

## Decisión

**Una restricción `EXCLUDE` en PostgreSQL es la autoridad. El código la respeta, no la
sustituye.**

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE bookings ADD CONSTRAINT bookings_no_overlap
  EXCLUDE USING gist (
    professional_id WITH =,
    tstzrange(occupied_from, occupied_to, '[)') WITH &&
  )
  WHERE (status IN ('confirmed', 'pending_hold'));
```

Por qué cada parte:

- `professional_id WITH =` — dos reservas del mismo profesional son candidatas a
  conflicto. Las de profesionales distintos no se comparan entre sí.
- `tstzrange(occupied_from, occupied_to, '[)')` — intervalo semiabierto. Un turno que
  termina 11:45 y otro que empieza 11:45 **no** se solapan. Con `[start, end]` cerrado,
  toda agenda con turnos contiguos se rechazaría a sí misma.
- `btree_gist` — extensión que permite mezclar una condición de igualdad con una de
  rango en la misma restricción `EXCLUDE`. Sin ella, esto no es expresable.
- `WHERE (status IN ('confirmed', 'pending_hold'))` — predicado parcial. Una reserva
  **cancelada sale del predicado** y deja de participar de la restricción. Es la
  implementación más limpia posible de "al cancelar, liberar inmediatamente el
  horario": no hay que borrar nada, no hay que mover la fila a otra tabla, no hay
  ningún estado especial que mantener.

`occupied_from` / `occupied_to` existen para separar el tiempo de servicio del tiempo
ocupado. Si el negocio configura 5 minutos de limpieza entre turnos, esos 5 minutos
forman parte del intervalo ocupado pero no del servicio. Cancelar libera el servicio y
recalcula el intervalo ocupado, sin que las reservas vecinas se solapen mal.

### Las capas alrededor

La restricción no basta sola. Encima:

1. **Orden de locks.** La transacción hace `SELECT ... FOR UPDATE ORDER BY id` sobre
   los profesionales candidatos. El orden por id es lo que **previene deadlocks**: sin
   un orden global de adquisición, dos reservas concurrentes que bloqueen profesionales
   en distinto orden se bloquean mutuamente.
2. **Revalidación en la transacción.** Se reevalúa el slot concreto contra el estado
   actual, no contra lo que el cliente vio hace cinco minutos. El cliente pudo haber
   cargado la página antes de que alguien más reservara.
3. **Traducción de error.** `ExclusionViolation` (SQLSTATE `23P01`) se traduce a
   **409**, no a 500. En modo "cualquier profesional" se reintenta con el siguiente
   candidato una sola vez.
4. **Idempotencia.** `Idempotency-Key` en `POST /bookings`, para que un doble clic o un
   reintento de red no cuenten como dos intentos.
5. **Estado reservado.** `pending_hold` existe en el enum y en el predicado desde el
   día uno, aunque el MVP no lo use. Agregar retenciones temporales después no requiere
   migración ni cambiar la restricción.

## Alternativas consideradas

### `SELECT ... FOR UPDATE` sobre la fila del profesional y luego verificar — descartada
como garantía suficiente

Es correcto **solo** si todas las escrituras de `bookings` pasan por ese patrón. Es una
regla de convención, y las convenciones se rompen: un script de importación, una
migración, un job de walk-in, un endpoint futuro. La restricción `EXCLUDE` no depende de
que nadie se acuerde de ella.

Se conserva como **capa adicional**, porque aporta algo que la restricción no da: el
lock ordenado evita el deadlock antes de que se produzca, y reduce los 409 por
contención. Las dos son complementarias.

### Nivel de aislamiento `SERIALIZABLE` — descartada

`SERIALIZABLE` en PostgreSQL detecta many anomalías, entre ellas el problema de
escritura perdida, lanzando un error de serialización que la aplicación debe reintentar.

Descartada porque protege contra un problema más general del que necesitamos y a cambio
cuesta: decrementa el paralelismo de forma significativa (abortar y reintentar es
frecuente), y obliga a reintentar **toda** la transacción, no solo la inserción. Se
reserva el reintento para cuando una consulta concreta demuestre necesitar más
aislamiento.

### `SERIALIZABLE` solo para la creación de reservas — descartada

Mismo problema, con el agregado de que el nivel de aislamiento es una propiedad de la
transacción y habría que acordarse de configurarlo justo en el camino correcto. La
`EXCLUDE` no requiere ninguna disciplina de este tipo.

### Advisory locks (`pg_advisory_xact_lock`) — descartada

Un lock por profesional acquired en la transacción impediría el solapamiento, pero el
lock es una convención: si algo escribe sin tomarlo, la garantía desaparece. Además
`pg_advisory_xact_lock` no es componible con nada y no te dice qué se solapó cuando
falla. La restricción te da el conflicto concreto.

### Exclusión solo en la aplicación con `SELECT` reintentando en bucle — descartada

Es el patrón check-then-act con más pasos. Mismo problema, más latencia y más riesgo de
carreras que la restricción declarativa no tiene.

## Consecuencias

**Positivas**

- La garantía vive en el motor de almacenamiento. No depende de que todas las
  escrituras la respeten.
- Cancelar libera el horario de forma inmediata y natural, por el predicado parcial.
- El error es específico: se sabe exactamente qué profesional y qué rango chocaron.
- Escala bien: la restricción usa los índices GiST, no un escaneo.

**Negativas / costos aceptados**

- Requiere la extensión `btree_gist`. Disponible en `postgres` estándar y habilitable en
  Neon y Render; en plataformas que no la permitan habría que revisar el plan de
  despliegue.
- Una migración de Alembic que agregue la restricción no se puede expresar con las
  abstracciones de alto nivel: se escribe con `op.execute()` y SQL crudo. Se acepta.
- `ExclusionViolation` aborta la transacción. El reintento con el siguiente profesional
  debe abrir una transacción nueva, no reintentar dentro de la abortada.
- El predicado parcial tiene una consecuencia que hay que recordar: si alguien agrega
  un estado nuevo a `bookings.status` y olvida agregarlo al predicado, esas reservas
  quedan sin protección. Mitigación: el enum es cerrado y un test verifica que todo
  valor del enum aparece en el predicado de la restricción.
- En "cualquier profesional" con muchos candidatos, un cliente puede recibir 409 aunque
  haya disponibilidad para otro profesional. Mitigación: reintento acotado con el
  siguiente candidato antes de devolver el error.

**Revisar si**

- Un requisito de negocio obliga a permitir solapamientos (por ejemplo, permitir
  double-booking con advertencia al administrador). Sería cambiar el predicado, no la
  arquitectura.
- El volumen de bookings por profesional hace que el índice GiST crezca de forma
  significativa. Con los volúmenes esperados, no es una preocupación.
