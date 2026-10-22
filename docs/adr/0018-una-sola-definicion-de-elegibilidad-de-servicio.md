# ADR-0018: Una sola definición de "qué profesional puede hacer qué servicio"

- **Estado:** Aceptada
- **Fecha:** 2026-10-22
- **Referencia:** PROJECT_MASTER §9 (las nueve condiciones), §24 (panel del negocio)
- **Contexto en:** ARCHITECTURE.md §5 (modelo de datos), §7 (multi-tenancy)

## Contexto

Un negocio real ("The Peluche", `/barber-the-peluche`) tenía horarios, servicios y
profesionales configurados, y su **link público no mostraba ni un horario**. El motivo no
era la agenda: `business_hours` estaba bien. La tabla `professional_services` —la que
dice qué profesional sabe hacer qué trabajo— estaba **vacía**, porque asignarle un
servicio a un profesional es un paso del panel distinto del de crear el servicio, y es
fácil no hacerlo sin enterarse.

El dato vacío no era el problema en sí: **el sistema lo escondió**. La página pública
mostraba el servicio, el cliente elegía la fecha, y la respuesta era "no hay horarios
disponibles para esta fecha". El mensaje señalaba a la fecha cuando el problema era otro,
así que ni el dueño ni el cliente podían llegar a la causa.

Detrás del dato faltaba un problema de código, y ese sí es una decisión de arquitectura.
La regla "este profesional puede hacer este servicio" estaba **copiada en cuatro lugares**:

- el cálculo de disponibilidad, en su rama de "cualquier profesional";
- el cálculo de disponibilidad, en su rama de **profesional concreto**;
- la creación de la reserva, en `_verificar_profesional_elegible`;
- el listado de profesionales del panel y el listado público, filtrados por servicio.

Las cuatro copias no eran iguales. La rama de profesional concreto miraba la **agenda**
del profesional pero no el **vínculo con el servicio**: ofrecía horarios que la reserva
después rechazaba con *"El profesional no puede realizar este servicio"*. Un horario que
el sistema muestra y después no deja tomar es peor que no mostrarlo: es una mentira con
consecuencias, y el mensaje de error caía sobre el cliente, en el peor momento.

Es el mismo tipo de falla que el ADR-0017 documentó para la publicabilidad: una regla
repartida en varios lugares, cada uno con su propia copia, es la forma mas barata de
que el sistema diga dos cosas a la vez.

## Decisión

**La regla tiene una sola definición, y todo el mundo la llama.**

`app.modules.services.service.profesionales_que_pueden_hacer(session, business_id,
service_id)` devuelve los profesionales **elegibles** para un servicio, y es la **única**
definición de "puede hacer este servicio". Son tres condiciones, y las tres son de
negocio:

1. el profesional es del negocio (un `professional_id` de otro tenant no entra por la
   puerta de atrás);
2. está **activo** y **no archivado** (un profesional desactivado no atiende, aunque
   conserve el vínculo);
3. tiene el **vínculo activo** en `professional_services` (es lo que dice que sabe hacer
   ese trabajo: un negocio con peluquero y barbero no ofrece los servicios de uno al
   otro).

El `ORDER BY sort_order, id` es parte del contrato: el índice de la lista es el
desempate de la estrategia de asignación, así que el orden tiene que ser estable entre
llamadas.

**Todo lo que ofrece o reserva un horario pasa por esa función.** La disponibilidad
—las dos ramas— y la reserva la llaman. Ninguna consulta a `professional_services` queda
suelta en el código de aplicación: las que quedan son las de **escritura** (asignar y
desasignar), que son otra cosa.

**Un servicio sin nadie que lo haga es un estado que el sistema dice, no que esconde.**
Tres piezas, y las tres usan la misma regla:

- **La página pública** no ofrece "cualquier profesional" para un servicio sin
  elegibles, y el mensaje no culpa a la fecha: dice que ese servicio todavía no lo
  atiende nadie.
- **El panel** muestra la cobertura por servicio (`GET /business/cobertura`: cada
  servicio ofrecido y cuántos profesionales pueden hacerlo) y avisa con un banner cuando
  un servicio tiene **cero**. La tabla de servicios muestra el número al lado de cada
  uno.
- **La disponibilidad** devuelve lista vacía, y la reserva rechaza: las dos cosas ya no
  pueden discrepar porque son la misma consulta.

`GET /business/cobertura` es una **lectura**, así que la guardia de plan (§ADR-0017) no la
corta: un negocio con el mes gratis vencido igual tiene que poder ver qué le falta
asignar, porque es justo lo que va a tener que arreglar.

## Consecuencias

### Positivas

- La disponibilidad y la reserva no pueden divergir: son la misma regla llamada desde
  dos lugares, no dos consultas que hay que mantener iguales.
- El fallo se vuelve visible en el panel **antes** de que lo descubra un cliente, y con
  el nombre del servicio y no con un mensaje sobre la fecha.
- Un profesional desactivado o archivado deja de atender sus servicios en todas partes,
  no solo en la reserva.
- Un negocio nuevo tiene que tomar una decisión explícita (asignar el servicio a
  alguien) para que el servicio funcione. El sistema no lo adivina.

### Negativas / costos aceptados

- **Un paso más para el dueño.** Crear el servicio ya no basta: hay que asignarlo. Es
  el costo de que la reserva signifique algo, y el banner existe para que el paso no se
  pierda.
- **Una consulta más** en el listado de profesionales con filtro por servicio: ahora
  consulta la lista de elegibles y la filtra por `id`. La lista es corta (decenas de
  profesionales) y la corrección vale el costo.
- **`GET /business/cobertura` es un endpoint nuevo** que hay que mantener junto al
  listado de servicios. Se aceptó porque el número no es derivable del listado sin una
  consulta extra, y porque el aviso necesita ser consistente con la regla.

## Alternativas consideradas

### A) Arreglar solo el dato de los negocios afectados

Descarte: el bug de producción era el dato, pero el **código** que lo escondió--una
rama de la disponibilidad que miraba la agenda y no el vínculo-- seguía ahí. Un negocio
nuevo con el mismo forgot tenía el mismo agujero, y el próximo cliente perdía el turno
igual. Arreglar el dato sin la regla deja el sistema capaz de repetir el error.

### B) Un solo `EXISTS` dentro de la consulta de disponibilidad

Descarte: la reserva necesita el mismo predicado en un contexto distinto (elegir a quién
asignarle el turno, con su desempate por carga). Con la regla en SQL, cada consulta
nueva que la use reescribe el predicado y vuelve a poder divergir. Una función en
Python es un solo lugar que se puede testear y que todos llaman.

### C) Filtrar por el vínculo solo, sin mirar `is_active` ni `archived_at`

Descarte: el vínculo activo sobrevive a la baja del profesional, porque
`bookings.professional_id` tiene FK `RESTRICT` y archivar no borra la historia. Un
negocio que "dejó de trabajar" con alguien seguiría ofreciendo sus servicios con ese
profesional en la grilla.

### D) Ocultar de la página pública los servicios sin elegibles

Descarte: el cliente que llega por un servicio concreto tiene derecho a saber que existe
y a que no hay nadie: esconderlo del catálogo lo vuelve invisible y no lo arregla. La
página dice la verdad; el panel avisa antes.

### E) Dejar la cobertura como un calculo del frontend

Descarte: el frontend contaría con su propia versión de la regla y volvería a poder
divergir de la reserva. El numero tiene que salir de la misma función que usa la
reserva, o el aviso miente.

## Verificación

- `tests/integration/test_availability_public.py`: un profesional **sin** el vínculo con
  el servicio devuelve `slots: []` (GET y POST), y el listado público de profesionales
  filtrado por servicio devuelve los que lo pueden hacer y nada más.
- `tests/integration/test_fase4_panel.py`: `GET /business/cobertura` cuenta con la misma
  regla--cero sin asignar, uno con el vínculo, cero de nuevo si el profesional se
  desactiva-- y los servicios archivados no aparecen.

## Revisar si

- Un profesional puede hacer **todos** los servicios del negocio por defecto (por
  ejemplo, con una marca "hago todo"): la función tiene que seguir siendo el único
  lugar, pero su predicado cambia.
- El mismo profesional atiende en **varios negocios**: la función recibe un solo
  `business_id` y hay que decidir si la elegibilidad es por negocio o por profesional.
- Se quiere que un servicio sin elegibles **no se publique**: es publicabilidad (§ADR-0017)
  y va en `businesses.publicacion`, no acá.
