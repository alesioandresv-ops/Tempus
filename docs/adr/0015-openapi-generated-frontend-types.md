# ADR-0015: Tipos del frontend generados desde OpenAPI

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §31, §39
- **Contexto en:** ARCHITECTURE.md §15.5

## Contexto

La §31 exige separar correctamente UI, estado, API, lógica de negocio, tipos y
validación. Y la §39 prohíbe "afirmar que algo funciona si no fue ejecutado/verificado",
y también prohíbe "llamadas API dispersas".

En la práctica, la fuente más común de bugs silenciosos en un frontend con un backend
separado es que **los tipos del cliente se escriben a mano y dejan de coincidir con los
del servidor**. No hay error de compilación: el backend cambia el nombre de un campo,
el TypeScript del frontend no se entera porque su tipo todavía dice el nombre viejo, y
el sistema lee `undefined` en producción. El bug no aparece en la revisión ni en los
tests del frontend: aparece cuando un usuario real toca ese flujo.

Es un problema de contrato, y la solución es tratar el contrato como generado y no
como escrito.

## Decisión

**Los tipos del frontend se generan desde el OpenAPI del backend**, con
`openapi-typescript`, y el resultado se commitea en `frontend/src/types/`.

- El backend define el contrato con Pydantic, que genera el OpenAPI.
- Un comando de CI regenera los tipos y falla si el resultado **difiere** de lo
  commiteado.
- El cliente HTTP (TanStack Query) usa esos tipos en los wrappers de `services/`, de
  modo que un endpoint no puede usarse sin su tipo.
- Ningún archivo de tipos se edita a mano. Si falta un tipo, se agrega el schema en el
  backend, que es donde vive la verdad.

## Alternativas consideradas

### Escribir los tipos a mano en el frontend — descartada

Es lo que se hace por inercia y es exactamente el problema. El costo no es el effort
inicial, es que la deriva es silenciosa y aparece en producción.

### Usar `zod` como fuente de verdad y derivar el backend de ahí — descartada

Invierte la dependencia: el contrato pasa a estar en el cliente y el backend lo consume.
Tiene un problema concreto: los esquemas de Pydantic hacen validación y coerciones en el
servidor (por ejemplo, el formato de teléfono o el rango de `duration`) que no son
expresables de forma natural en Zod, y terminan siendo dos definiciones del mismo
concepto.

Mantener **Zod en el frontend** (que la §31 pide) y Pydantic en el backend no es
contradicción: cada uno hace lo que le corresponde. Zod valida y controla los forms
—que es donde está el valor para el usuario— y Pydantic valida la entrada real de la
API, que es donde está el valor de seguridad. La frontera entre ambos es el cliente
HTTP tipado, que es justo lo que los tipos generados HACEN posible.

### Generar el cliente HTTP completo (openapi-typescript-codegen, Orval) — descartada
por ahora

Ir más allá de los tipos y generar también los servicios elimina por completo el
escritorio a mano de los clientes, que es un problema conocido. Se descarta por ahora
porque acopla el frontend a un generador específico y porque la §31 pide una capa
`services/` propia con lógica (invalidación de queries, manejo de errores, reintentos).
Se puede adoptar más adelante sin contradecir esta decisión: los tipos siguen siendo
generados, lo que cambia es quién escribe las funciones.

### Un contrato compartido en un paquete aparte (un monorepo con la API) — descartada

En un repositorio separado, un paquete de contrato es una dependencia circular entre
proyectos: el backend lo publica, el frontend lo consume, y cada cambio requiere
publicar una versión. Es más ceremonia que el problema que resuelve, y el proyecto es un
solo equipo.

## Consecuencias

**Positivas**

- La deriva entre el contrato del servidor y los tipos del cliente se convierte en un
  fallo de CI, no en un bug de producción.
- El frontend tiene tipos completos de las respuestas de la API, no `any` disfrazado.
- El contrato es la documentación: no puede quedar desactualizado respecto del código.
- `openapi-typescript` genera también las utilidades de la API, lo que hace que los
  nombres de los tipos coincidan exactamente con la respuesta real.

**Negativas / costos aceptados**

- Hay un paso de generación que hay que recordar. Se mitiga con un `make generate` y con
  la comprobación en CI, que es la que de verdad garantiza que no se olvide.
- Los tipos generados no se pueden limpiar ni poner comentarios. Se acepta: no son
  código que alguien lea, son código que el compilador usa.
- Cualquier error tipado en el backend se propaga al frontend como un error de
  compilación allí. Es el comportamiento correcto, pero se siente como que el frontend
  "se rompió" cuando el cambio era en el backend. Se informa al equipo para que no se
  lea como un defecto.
- Requiere discipline en el backend: los schemas tienen que estar bien definidos. Si un
  endpoint devuelve un `dict` sin schema, el tipo generado es inútil para ese endpoint.
- Un `additionalProperties: true` en un schema produce un tipo permisivo que no ayuda.
  Los schemas de Pydantic deben ser explícitos, que es de todas formas lo que la §39
  pide para evitar mass assignment.

**Revisar si**

- La generación pasa a ser un cuello de botella en el ciclo de desarrollo, lo que
  señalaría que hace falta adoptar un generador de cliente completo (Orval).
- El proyecto pasa a ser multi-repositorio, momento en el que un paquete de contrato
  compartido vuelve a tener sentido.
