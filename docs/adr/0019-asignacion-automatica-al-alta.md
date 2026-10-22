# ADR-0019: El alta asigna los servicios; el paso manual restringe

- **Estado:** Aceptada
- **Fecha:** 2026-10-22
- **Referencia:** PROJECT_MASTER §9 (las nueve condiciones), §24 (panel del negocio)
- **Contexto en:** ARCHITECTURE.md §5 (modelo de datos)
- **Revisa:** ADR-0018, en la parte que hacía de la asignación un paso manual obligatorio

## Contexto

El ADR-0018 dejó una sola definición de "qué profesional puede hacer qué servicio" y
hizo visible el estado "un servicio que no lo hace nadie". Fue un arreglo correcto y no
alcanzó: **el mismo fallo volvió a pasar con un segundo negocio real** ("The Peluche",
`/the-peluche`). Servicios, profesionales y horarios cargados; `professional_services`
vacía; el link público sin un solo horario.

La causa de fondo no era el motor de disponibilidad ni la regla de elegibilidad: era que
el sistema pedía un paso que **nadie sabe que existe**. El dueño crea cuatro servicios,
crea tres profesionales y considera terminado el trabajo. El panel no le dice que falta
algo, porque para el panel "crear el profesional" y "asignarle los servicios" son dos
pantallas distintas, y la segunda se abre desde un botón que está en la primera. El
banner de cobertura del ADR-0018 avisa, pero avisa **después**: es un cartel de error, no
una prevención. Un negocio que se configura entero y no vuelve a entrar al panel nunca lo
ve.

Un negocio chico --el caso normal, no la excepción-- tiene un profesional que hace todo,
o tres que hacen lo mismo. En ese modelo, **el valor por defecto correcto es que todos
hagan todo**: el paso manual existe para el caso raro (el barbero y la manicura), no para
el caso común.

## Decisión

**Crear un servicio o un profesional ya deja el negocio funcionando.**

- `crear_servicio` enlaza el servicio nuevo con **todos los profesionales no archivados**
  del negocio.
- `crear_profesional` enlaza al profesional nuevo con **todos los servicios ofrecidos**
  del negocio.

Las dos funciones viven en `app.modules.services.service`
(`vincular_servicio_con_todos_los_profesionales`,
`vincular_profesional_con_todos_los_servicios`) y las llaman las funciones de alta que ya
usaba el panel; ningún router arma vínculos por su cuenta.

Son tres criterios, y los tres son parte de la decisión:

1. **Solo servicios ofrecidos** (`is_active` y no archivados). Un servicio archivado no se
   reserva: no hay nada que asignar.
2. **Solo profesionales no archivados** (`archived_at IS NULL`). Archivar es "esta persona
   ya no está"; no se le siguen asignando cosas.
3. **Los desactivados sí cuentan** (`is_active = False` se enlaza igual). Desactivar es
   "esta semana no atiende", no "no existe". Si el vínculo no se creara, reactivar a
   alguien lo dejaría sin poder hacer nada hasta que alguien se acordara de asignarlo a
   mano: exactamente el olvido que esta decisión existe para evitar.

**El paso manual cambia de sentido: de habilitar a restringir.** El panel sigue teniendo
"Servicios de X" con la lista tildada; destildar es cómo un negocio con especialidades
dice que el barbero no hace manicura. Guardar sigue **reemplazando** el conjunto completo
(ADR-0018 §Decisión, sin cambios): la lista que se manda es la verdad.

**El dato que ya está mal se repara con la migración `0026_asignar_servicios_al_alta`,
con un criterio conservador:** se mira **servicio por servicio** y se completa solo el que
**no tiene ningún profesional elegible**. Un negocio que ya configuró sus asignaciones a
mano no se toca--sus servicios tienen gente--; uno que quedó vacío se arregla entero. La
migración no borra ni cambia ningún vínculo existente.

## Consecuencias

### Positivas

- **Un negocio recién configurado funciona sin un paso que nadie conoce.** Es el fallo que
  se repitió dos veces con clientes reales, cerrado en el alta y no en el aviso.
- El orden en que se cargan las cosas deja de importar: servicios primero o profesionales
  primero, las dos secuencias terminan con la misma tabla.
- El panel sigue sirviendo para el caso con especialidades, y el frontend invalida la
  lista de cada profesional al crear o archivar un servicio: el próximo "Guardar
  asignaciones" no borra la asignación automática que el dueño no vio.
- La cobertura por servicio (ADR-0018) sigue siendo la red: ahora avisa de un caso que casi
  no puede ocurrir--todos los profesionales archivados o desactivados--en vez de avisar de
  un olvido esperable.

### Negativas / costos aceptados

- **Un vínculo de más es posible.** Un negocio que deliberadamente tiene un servicio que
  nadie hace (p. ej. "próximamente") se lo ve asignado a todos y tiene que destildarlo.
  Se aceptó porque el estado contrario--un servicio publicado que no se puede reservar--es
  el fallo que se está arreglando, y porque el panel lo muestra tildado, no escondido.
- **La migración toca una tabla de tenant en producción.** Corre negocio por negocio con
  `set_config('app.current_business_id', ..., true)` en vez de esquivar la RLS, y es
  idempotente (`ON CONFLICT DO NOTHING`) por si el alta automática ya creó el vínculo.
- **El downgrade de la migración no hace nada**, a propósito: las filas que agrega son
  indistinguibles de las que crea el panel y borrarlas dejaría negocios sin horarios.
  Una migración de datos reversible tiene que poder identificar lo que agregó; esta no
  puede.

## Alternativas consideradas

### A) Dejar solo el aviso del ADR-0018 (banner de cobertura)

Descarte: ya existía cuando pasó el segundo caso, y no lo evitó. Un aviso sirve para el
que entra al panel; el negocio roto es el que ya no entra porque cree que terminó.

### B) Un botón "asignar todo" en el panel

Descarte: mueve el problema de lugar. Sigue dependiendo de que el dueño sepa que el paso
existe y lo apriete; el default sigue estando mal.

### C) Que la elegibilidad sea "todos" cuando la tabla está vacía (regla implícita)

Descarte: convierte la ausencia de configuración en un significado--"nadie restringió,
entonces todos"--y hace que destildar el último profesional de un servicio lo vuelva a
habilitar para todos. El estado vacío tiene que ser un dato, no una interpretación.

### D) Backfill con "todo con todo" para todos los negocios

Descarte: pisa decisiones tomadas. Un negocio que restringió servicios a propósito
(peluquero y manicura, con vínculos ya cargados) recibiría vínculos que nunca eligió. El
criterio de la migración es el servicio huérfano, que es un estado roto en cualquier
lectura.

### E) Asignación automática solo en el alta de servicios, no en el de profesionales

Descarte: deja la mitad del agujero. Un negocio que primero carga los servicios y después
los profesionales es igual de común, y terminaría con un profesional que no aparece en
ninguna parte de la página pública.

## Verificación

- `tests/integration/test_fase4_panel.py` (`TestAsignacionAutomatica`): crear un servicio
  con profesionales ya cargados los deja asignados a todos y la cobertura deja de ser
  cero; crear un profesional con servicios ya cargados lo deja asignado a todos; un
  profesional **archivado** no recibe servicios nuevos (la cobertura queda en cero, que es
  la verdad); uno **desactivado** recibe el vínculo y atiende al reactivarse.
- `tests/integration/test_availability_public.py`
  (`TestElAltaDelPanelSeVeEnLaUrlPublica`): las funciones de alta del panel, sin ningún
  `PUT /business/profesionales/{id}/servicios`, dejan el listado público y la grilla de
  disponibilidad con horarios para el servicio nuevo. Es el recorrido exacto del cliente
  que se rompió dos veces.
- `backend/migrations/versions/0026_asignar_servicios_al_alta.py`: `alembic check` sin
  drift y `downgrade base` hasta el piso de 0003.

## Revisar si

- Un negocio puede tener **decenas** de servicios y profesionales: el alta crea
  `N` vínculos de una vez y el default "todos hacen todo" deja de ser razonable; ahí
  conviene un "grupo de trabajo" o una marca por servicio.
- Se agrega **importación masiva** de servicios o profesionales: el alta automática corre
  una vez por fila y conviene pasar los vínculos a la misma transacción del import.
- Se decide que un servicio **inactivo** (`is_active = False`, sin archivar) tampoco reciba
  vínculos nuevos: hoy se enlaza igual para que reactivarlo funcione sin volver al panel.
