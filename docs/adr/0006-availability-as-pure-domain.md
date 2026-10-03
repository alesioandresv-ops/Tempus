# ADR-0006: Disponibilidad como módulo puro de dominio

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §12, §32
- **Contexto en:** ARCHITECTURE.md §8

## Contexto

La §32 es explícita: el motor de disponibilidad es uno de los componentes más
importantes del sistema, debe ser una pieza de dominio independiente, debe existir una
**única fuente de verdad** y **no se puede duplicar la lógica entre frontend y backend**.

"Única fuente de verdad" tiene una consecuencia que suele pasarse por alto: significa
que el motor debe poder correr **sin** el servidor. Y no por rendimiento, sino por la
verificabilidad. Si el cálculo de disponibilidad necesita una sesión de base de datos,
cada test tiene que montar una base, y la cobertura de la lógica de intervalos se
vuelve costosa. Si es una función pura, se testea en microsegundos y con Hypothesis se
pueden explorar miles de combinaciones de ventanas, reservas, bloqueos y ausencias
contra invariantes que deben cumplirse siempre.

## Decisión

**`app/modules/availability/` es un módulo puro.** Sin imports de SQLAlchemy, sin
cliente HTTP, sin acceso a red, sin lectura del reloj del sistema.

El contrato recibe **datos ya leídos** y devuelve slots:

```python
def compute_slots(
    *, business_hours, professional_hours, opening_windows,
    busy, duration, slot_interval, local_date_range, min_lead, now
) -> list[Slot]
```

- `now` **se inyecta**. Un módulo que lee el reloj no es determinista y sus tests
  dependen del momento en que corren.
- `busy` es una lista de intervalos ya fusionados (reservas, bloqueos, ausencias
  combinadas). El módulo no sabe qué son reservas y qué son vacaciones: solo sabe
  que hay regiones no disponibles.
- La lectura de datos vive en un servicio vecino (`availability` tiene un `reader` que
  sí consulta la base y arma `busy`), y el cálculo en el módulo puro.

El algoritmo es álgebra de intervalos semiabiertos `[inicio, fin)` sobre tiempo local
del negocio. Un turno que termina 11:45 y otro que empieza 11:45 **no** se solapan: es
lo que hace que no se pierdan 15 minutos en cada frontera.

La perpendicular a esto: **el frontend no replica el cálculo**. Consulta el endpoint de
disponibilidad y renderiza lo que recibe. Puede previsualizar, como permite la §32,
pero no puede decidir.

## Alternativas consideradas

### Calcular la disponibilidad con SQL puro (CTEs recursivas, `generate_series`) —
descartada

Es la opción por defecto en muchos sistemas de reservas, y tiene una ventaja real:
todo el cálculo ocurre donde están los datos, sin transferencia.

Se descarta por dos razones concretas:

1. **Testabilidad.** Un motor en SQL se testea integrándolo contra una base, es más
   lento, y las invariantes hay que comprobarlas sobre datos concretos en lugar de
   sobre miles de combinaciones generadas.
2. **La lógica se escapa.** Un `generate_series` con `JOIN` y `EXCEPT` es difícil de
   leer, difícil de modificar sin romper algo y difícil de razonar sobre casos de
   borde. Y en cuanto haya que aplicar una regla nueva —"no ofrecer slots a menos de
   dos horas de anticipación si el cliente es recurring"— aparece un `CASE` más en una
   query que ya es ilegible.

La objeción legítima de rendimiento —"calcular en la app significa traer los datos"—
se responde con lo que dice la propia §32: la lectura es de unos pocos KB por día
consultado, y el rango está acotado por `max_advance_days` y por un tope duro de 31
días. Y la decisión no es "SQL o aplicación" sino "el cálculo va en un sitio". Con un
módulo puro, esa elección se puede revertir más adelante sin tocar la lógica.

### Motor de reglas declarativo genérico (json-rules, Drools, DSL propio) — descartada

Muy flexible y muy caro de empezar. La §50 prioriza corrección sobre cantidad, y un
motor de reglas genérico necesita meses de trabajo antes de resolver el primer caso
real. El coste de aprender un DSL propio es alto, y esa flexibilidad no se justifica
hasta que los casos de uso estén conocidos.

### Reescribir el cálculo en el frontend para respuesta instantánea — descartada por la
§32

La §32 lo prohíbe explícitamente: "NO duplicar la lógica de disponibilidad entre
frontend y backend". Y tiene razón por un motivo concreto: la §48 exige que la
plataforma represente la realidad. Dos implementaciones divergen, y la que diverge es
la del cliente, que muestra un horario que el backend va a rechazar. El usuario pierde
la confianza en el sistema.

## Consecuencias

**Positivas**

- Tests unitarios en microsegundos, sin base de datos.
- Property-based testing con Hypothesis sobre las invariantes: ningún slot dentro de un
  intervalo ocupado, todo slot cabe íntegro en una ventana efectiva, dos slots del
  mismo profesional nunca se solapan, agregar un bloqueo nunca aumenta la cantidad de
  slots, y una duración mayor que la ventana total produce cero slots.
- El cálculo es determinista y reproducible: el mismo input da el mismo output, lo que
  hace que un bug de disponibilidad sea depurable.
- La revalidación en el momento de la reserva es trivial: se llama a la misma función
  con el mismo input.
- La caché de disponibilidad es segura por definición, porque no tiene efectos.

**Negativas / costos aceptados**

- Hay que leer los datos antes de calcular, con el riesgo de N+1. Se controla con una
  query por tipo de recurso por rango, nunca una por profesional ni por slot.
- La frontera entre "leer datos" y "calcular" dentro del módulo requiere disciplina:
  el `reader` importa SQLAlchemy, el módulo puro no. Se verifica en CI con una
  dependencia de análisis que prohíbe los imports de persistencia en el subdirectorio
  del cálculo.
- El cálculo no puede usar optimizaciones de base de datos que existieran para la
  versión en SQL. Se acepta: el volumen no lo justifica.
- Reimplementar el cálculo en SQL más adelante implicaría duplicar la lógica o
  reescribirla, con el riesgo de divergencia. Aceptado como riesgo consciente.

**Revisar si**

- El volumen de consultas de disponibilidad crece de forma que el cálculo en la
  aplicación se vuelva el cuello de botella. Señal de alarma: latencia de la consulta
  de disponibilidad por encima de 200 ms con el volumen de tenants actual.
- Se necesita calcular disponibilidad masiva (un marketplace que consulte miles de
  negocios a la vez). Ahí el cálculo en la aplicación no escala y habría que moverlo.
