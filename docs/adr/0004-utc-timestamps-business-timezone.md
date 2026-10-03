# ADR-0004: UTC en la base, timezone del negocio en la aplicación

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §11, §12, §48
- **Contexto en:** ARCHITECTURE.md §2, §8

## Contexto

Un negocio abre de 09:00 a 18:00. Eso no es una hora: es una hora **en una zona
horaria concreta**, y esa zona cambia según el negocio, no según el servidor.

La decisión de dónde persiste ese concepto es la que más clase de bug produce a
largo plazo en sistemas de reservas, y el síntoma típico no aparece hasta meses
después: en el cambio de hora de primavera, el sistema ofrece un horario que no existe,
o duplica un turno que ya estaba reservado.

Cada opción tiene un fallo característico:

- Guardar hora local ingenua: el mismo instante se representa distinto según el
  negocio, y no se puede ordenar ni comparar nada en la base.
- Guardar solo UTC: se pierde la noción de "día" del negocio, que es la unidad con la
  que el cliente piensa y con la que se filtran las agendas.

## Decisión

Triple representación, con una fuente de verdad explícita.

| Dato | Columna | Rol |
|---|---|---|
| Instante absoluto | `starts_at`, `ends_at` | **Fuente de verdad.** `timestamptz`, siempre UTC |
| Día del negocio | `local_date` | **Índice de agenda.** `date`, día local según `businesses.timezone` |
| Hora de la agenda | derivado | Se calcula en la capa de presentación con la zona del negocio |

Reglas:

1. Todo instante se persiste en UTC. `timestamptz` en PostgreSQL, que además
   almacena el offset con el que se escribió.
2. `businesses.timezone` es un identificador IANA (`America/Argentina/Buenos_Aires`).
   **Se usa `zoneinfo` de la biblioteca estándar**, nunca un offset numérico fijo: un
   offset fijo no sabe que Buenos Aires cambia de hora.
3. Toda la aritmética del motor de disponibilidad ocurre **en tiempo local del
   negocio** y se convierte a UTC al persistir. Es lo que hace correcto el manejo de
   los días de 23 y 25 horas.
4. `local_date` se calcula y se escribe en la misma transacción que el instante, para
   que nunca puedan quedar inconsistentes.
5. La función `now()` del negocio se calcula con la zona del tenant, no con la del
   servidor ni con la del usuario.

## Alternativas consideradas

### Solo UTC, derivando el día al consultar — descartada

Correcto en principio, pero obliga a calcular la fecha local en la capa de aplicación
para **cada fila de la agenda**, y peor: obliga a filtrar por rango de `starts_at`
convertido, lo que impide que un índice sobre `local_date` se use. La agenda es la
consulta más frecuente del sistema; pagarla en la base es la diferencia entre una
consulta indexada y un escaneo con aritmética por fila.

### Guardar hora local ingenua en la base — descartada

Es la opción que produce los bugs de cambio de hora. Además rompe el
`EXCLUDE USING gist` de ADR-0008: comparar rangos de hora local entre dos días con
distinto offset no significa nada.

### Guardar UTC más el offset del negocio como número — descartada

`America/Argentina/Buenos_Aires` como `-03:00` se rompe en el cambio de hora: la
regla histórica del negocio queda congelada en el valor del día que se creó. El
identificador IANA sí codifica la regla completa.

### `timestamp with time zone` sin `local_date` — descartada

Menos columnas que mantener, a cambio de perder el índice de agenda. Es la opción
atractiva por simplicidad e incorrecta por rendimiento en la consulta más frecuente del
producto.

## Consecuencias

**Positivas**

- Los cambios de horario de verano se manejan con una biblioteca que ya los conoce.
- Los intervalos semiabiertos funcionan correctamente en los días de 23 y 25 horas.
- `local_date` indexado hace que "la agenda de hoy" sea una consulta directa.
- Si un negocio cambia su zona horaria (se muda de ciudad), el histórico sigue siendo
  interpretable y las reservas nuevas se calculan en la zona nueva.

**Negativas / costos aceptados**

- Dos representaciones del mismo instante, que pueden desincronizarse. Se mitiga
  escribiendo ambas en la misma transacción y con un CHECK de consistencia en
  desarrollo.
- El motor de disponibilidad necesita `zoneinfo` y manejo explícito de DST, lo que lo
  hace más complejo que "restar horas". Es el precio de estar bien.
- Cualquier lugar que lea `starts_at` y lo muestre directamente sin pasar por la zona
  del negocio commitea un bug. Se cubre con un linter que prohíba el acceso directo a
  `starts_at` fuera del módulo de disponibilidad y del formateador de agenda.
- Riesgo R-03 documentado con un test dedicado de transición de horario de verano.

**Revisar si**

- Un negocio opera en más de una zona horaria a la vez. Hoy el modelo asume una sola
  por negocio, lo cual cubre el caso de uso real; multi-zona requeriría `local_date`
  por profesional.
