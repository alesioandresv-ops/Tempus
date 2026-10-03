# ADR-0003: UUIDv7 como clave primaria

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §5, §7
- **Contexto en:** ARCHITECTURE.md §2, §5

## Contexto

Dos requisitos tiran en direcciones opuestas sobre la clave primaria.

- La §5 exige que el `business_id` sea **estable aunque el nombre o el slug cambie**,
  y que el slug no se use como identificador interno.
- La §7 exige que el `secure_token` no sea derivado del `booking_id`, "no secuencial",
  "no derivado" y "nunca expongas IDs internos sensibles innecesariamente".

Si la clave fuera un entero autoincremental, el `booking_id` sería enumerable: con
`/api/v1/public/bookings/1042` un atacante puede recorrer reservas ajenas. Eso choca de
frente con el caso 9 de la §37.

Al mismo tiempo, un identificador opaco no puede ser un entero secuencial, y un UUIDv4
puro tiene un costo real de rendimiento.

## Decisión

**UUIDv7** en todas las tablas. Generado por la aplicación al crear el registro (y por
Alembic en las migraciones que los creen).

- Formato `uuid` nativo de PostgreSQL (16 bytes, no texto de 36).
- Los primeros 48 bits son un timestamp en milisegundos; el resto es aleatorio.
- Ordenable por tiempo: los inserts novos van al final del índice B-tree, no en una
  posición arbitraria.

## Alternativas consideradas

### `bigserial` autoincremental — descartada

La más eficiente en teoría, y la peor opción aquí. Un id enumerable convierte cada
endpoint que lo exponga en un vector de IDOR y de fuga de información: el atacante
sabe cuántos clientes tiene el negocio sin haber leído un solo dato. La mitigación
habitual es no exponer nunca el id, pero "nunca" es una promesa que depende de que
todas las rutas futuras la cumplan. Con UUIDv7 la defensa no depende de la disciplina.

Costo real que no se suele mencionar: en PostgreSQL, un `bigserial` sobre una tabla muy
concurrente produce contención en la secuencia, y los ids exponen el volumen de
negocio a cualquiera que haga una cantidad razonable de peticiones.

### UUIDv4 — descartada

Opaque y seguro, pero aleatorio. En un índice B-tree, cada insert queda en una hoja
distinta: la página se calienta de forma dispersa, el cache pierde effectiveness y las
lecturas por rango sobre el índice no aprovechan la localidad. En tablas de agenda, que
se consultan por rango de fechas constantemente, se paga en cada consulta.

Costaba una ventaja concreta: el orden temporal. Con UUIDv7 se conserva.

### Identificador opaco propio (base62 de un entero cifrado) — descartada

Interesante y compacto, pero no da orden temporal, y agrega un formato más que
entender y que puede filtrar información si el rango se nota. UUIDv7 es un estándar
(RFC 9562), con soporte nativo de PostgreSQL y de las librerías del stack. No vale la
pena inventar un formato.

### Clave primaria compuesta `(business_id, id)` — descartada

Refuerza el aislamiento, pero infla el tamaño de cada clave ajena y complica las FK.
El mismo objetivo se logra con las FK compuestas del ADR-0002, que solo se aplican
donde aporta valor.

## Consecuencias

**Positivas**

- Ids no enumerables: los casos 9 y 10 de la §37 quedan cubiertos por construcción.
- El `business_id` sobrevive a cambios de nombre y slug, como exige la §5.
- Orden temporal: localidad del índice en B-tree, inserciones al final del árbol y
  rango de fechas eficiente en la agenda.
- Tipo nativo `uuid`: 16 bytes, índices más chicos que `text`, y soporte de generación
  en la base si alguna vez hace falta.

**Negativas / costos aceptados**

- 16 bytes por índice contra 8 de un `bigint`. A la escala de este producto,
  irrelevante.
- UUIDv7 contiene un timestamp, así que el `created_at` aproximado se puede inferir de
  la id. Es una fuga aceptada: no es un secreto, y saber que una reserva se creó en
  2026 no revela nada que un `created_at` no revele ya.
- Requiere generar los ids en la aplicación, no con `gen_random_uuid()` de la base,
  para controlar la versión. Se encapsula en un tipo de columna propio.
- Generar v7 correctamente (timestamp + aleatorio) necesita una librería o una
  implementación cuidada. Se usa `uuid7` de la librería estándar con un fallback
  probado, y hay un test que verifica monotonía y unicidad.

**Revisar si**

- Se necesita Ordering estable entre bases de datos distintas (sharding), caso en el
  cual UUIDv7 sigue siendo la elección correcta.
