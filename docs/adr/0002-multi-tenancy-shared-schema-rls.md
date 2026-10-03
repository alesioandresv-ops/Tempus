# ADR-0002: Esquema compartido con FK compuestas y RLS

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §4, §25
- **Contexto en:** ARCHITECTURE.md §7

## Contexto

La plataforma es multi-tenant y la §4 es categórica: nunca un usuario de un negocio
puede acceder a datos de otro, y la separación debe estar contemplada **desde el
diseño de la base de datos**, no solo en la capa de aplicación.

Eso descarta la lectura fácil de "agregar `business_id` a todas las tablas y filtrar en
las consultas". Ese enfoque depende de que **ninguna consulta olvide el filtro**, y en
un sistema con cientos de queries, repositorios y joins, es una carga permanente sin
fin. Un solo `join` mal escrito es una fuga silenciosa que ningún test funcional detecta.

## Decisión

Esquema compartido, con tres capas de defensa de la más fuerte a la más débil.

### Capa 1: FK compuestas — la base lo hace imposible

Cada tabla padre que se referencia desde una hija declara unicidad del par:

```sql
ALTER TABLE professionals
  ADD CONSTRAINT professionals_id_business_key UNIQUE (id, business_id);
```

Y la hija referencia el par, con `business_id` desnormalizado:

```sql
ALTER TABLE professional_services
  ADD CONSTRAINT ps_tenant_fk_professional
  FOREIGN KEY (professional_id, business_id)
  REFERENCES professionals (id, business_id) ON DELETE CASCADE;
```

Consecuencia: es **físicamente imposible** que exista
`professional_id = P` (del tenant A) junto a `business_id = B` (del tenant B). No es una
convención que se pueda violar por descuido: es una restricción que rechaza la fila.

El precio es desnormalizar `business_id` en las tablas de unión. Es un costo de
espacio insignificante a cambio de mover un invariante de "confiar en el código" a
"garantizado por el motor de almacenamiento".

### Capa 2: Row Level Security

```sql
ALTER TABLE bookings ENABLE ROW LEVEL SECURITY;
ALTER TABLE bookings FORCE ROW LEVEL SECURITY;

CREATE POLICY bookings_tenant_isolation ON bookings
  USING      (business_id = current_setting('app.current_business_id', true)::uuid)
  WITH CHECK (business_id = current_setting('app.current_business_id', true)::uuid);
```

`FORCE` es necesario: sin él, el dueño de la tabla la bypasea. El rol de aplicación
**no** es el dueño de las tablas.

El contexto se inyecta al inicio de **cada transacción** con `SET LOCAL`, nunca con
`SET` a nivel de sesión. La razón es concreta y está en el riesgo R-02: con un
connection pooler en modo transacción —que es el modo por defecto en plataformas
gestionada— un `SET` de sesión puede quedar en una conexión que se reutiliza para el
siguiente tenant. `SET LOCAL` ata el valor a la transacción y lo descarta al hacer
commit. Un test con conexiones nuevas nunca reproduciría el bug.

### Capa 3: aplicación

- El `business_id` sale del JWT (ADR-0010), nunca de un parámetro del cliente.
- Todo acceso a datos pasa por un repositorio que exige un `TenantContext`. No existe
  `session.query(Booking)` a secas.
- El worker abre su propia transacción con el `SET LOCAL` del tenant del job. Un job
  sin `business_id` falla ruidosamente en vez de operar sin filtro.

## Alternativas consideradas

### Schema por tenant — descartada

Aislamiento perfecto y una forma de "olvidarse" el filtro. Descartada por costo
operativo: miles de negocios significan miles de schemas. Migraciones, índices,
`pg_dump` y el pool de conexiones se multiplican por el número de tenants, y el
mantenimiento de un solo cliente se vuelve una operación de riesgo para todos los
demás. Es un modelo que funciona para unos cientos de tenants, no para un SaaS.

### Base de datos por tenant — descartada

El aislamiento más fuerte posible, a un costo desproporcionado: un proceso de
conexión por tenant, migraciones por tenant, backups por tenant, y por encima de todo
la pérdida de las consultas agregadas entre tenants, que son necesarias para reportes y
para el modelo de precios.

### Solo `business_id` con filtro en la aplicación — descartada

Es la opción por defecto de la industria y la razón principal de las filtraciones de
datos entre tenants. Funciona hasta la primera vez que alguien escribe un `join` sin
el filtro, y ese error no produce una falla visible: produce datos de otro negocio en
la pantalla de un cliente. La RLS y las FK compuestas hacen que esa clase de error sea
imposible en lugar de improbable.

## Consecuencias

**Positivas**

- Un forgetting en una capa no abre una fuga: las otras dos la cierran.
- Los tests de multi-tenancy no son el único mecanismo de seguridad, son una capa más
  de una defensa de tres.
- No hay costo operativo por cantidad de tenants.

**Negativas / costos aceptados**

- `business_id` duplicado en toda tabla de unión: una columna más, una condición más en
  cada `WHERE`, y una clave foránea compuesta más en cada migraciones.
- RLS agrega un `SET LOCAL` por transacción, y un fallo ahí produce resultados vacíos,
  no un error visible. Mitigación: la política usa
  `current_setting(..., true)`, que devuelve NULL en vez de fallar, y hay un test
  dedicado (R-02) que verifica el aislamiento bajo concurrencia.
- Los tests deben correr contra Postgres real. SQLite no tiene RLS, `citext` ni
  `tstzrange`, así que un suite sobre SQLite probaría otra base de datos.
- Las consultas del panel del profesional necesitan una política adicional (acceder
  solo a las reservas propias), o una policy separada por rol.

**Revisar si**

- Un requisito regulatorio exige aislamiento físico por tenant.
- El número de tenants crece a un orden de magnitud que haga inviable el esquema
  compartido.
