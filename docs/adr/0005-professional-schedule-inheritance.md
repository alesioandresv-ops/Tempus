# ADR-0005: Horarios de profesional por herencia con override

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §12, §47
- **Contexto en:** ARCHITECTURE.md §5.4, §8.2

## Contexto

La §12 exige que la disponibilidad considere el horario del negocio **y** el del
profesional. El ejemplo del spec: negocio 08:00–18:00, profesional 09:00–17:00.

La lectura literal sugiere que hay que configurar los horarios de cada profesional para
cada día de la semana. La §47, en cambio, es explícita sobre la experiencia del
administrador: debe poder configurar su negocio "sin conocimientos técnicos" y entender
fácilmente horarios y disponibilidad.

Y estos dos requisitos entran en conflicto en la práctica. Un negocio con 6
profesionales y 7 días de la semana son 42 ventanas que el dueño tiene que configurar
correctamente. La consecuencia predecible no es un error visible: es que el dueño
configura dos o tres días, se aburre, y el resto queda mal. Después aparecen
disponibilidades que nadie entiende, y el dueño pierde la confianza en la herramienta
justo en la etapa donde más la necesita.

## Decisión

**El profesional hereda las ventanas del negocio. El override es explícito y
excepcional.**

- `professional_schedules` con `is_override = false` significa "hereda el negocio".
  **No se crean filas en ese caso.** La ausencia de filas es la herencia.
- Si existen filas para un profesional en un `weekday` concreto, esas **reemplazan**
  completamente las ventanas del negocio para ese día.
- Sustitución, no unión. Si un profesional tiene override de 10:00–16:00 un martes y
  el negocio abre 08:00–20:00, el profesional trabaja 10:00–16:00, no la unión de
  ambos.

Resolución en el motor:

```python
def ventanas(profesional, fecha):
    propias = professional_schedules[profesional][fecha.weekday()]
    if propias:
        return propias                    # override explícito
    return business_hours[fecha.weekday()]  # herencia
```

La UI del panel muestra el horario efectivo ya resuelto, con un indicador visible de
"heredado del negocio" y un botón para desviarlo. El admin nunca ve una pantalla de 42
ventanas vacías.

La asimetría de las excepciones es intencional: `business_exceptions` y `holidays`
reemplazan al negocio para **todos** sus profesionales (cerrar el negocio el lunes
cierra el lunes a todos), mientras que `time_off` y `blocks` afectan a un profesional
concreto.

## Alternativas consideradas

### Configuración explícita completa por profesional — descartada

Es lo que sugiere la lectura literal de la §12. La carga de 42 ventanas por profesional
es un desperdicio de la función: la mayoría de los profesionales trabajan el horario
del negocio, y modelar eso obliga al administrador a redigitar información que ya
existe. El costo no es la Base de Datos, es la tasa de errores de configuración y la
pérdida de confianza resultante.

### Un solo horario global para todos los profesionales del negocio — descartada

Simplifica al extremo, pero hace imposible el caso central del producto: un negocio con
profesionales que trabajan en turnos distintos. Y la §9 requiere que un profesional que
no puede trabajar un día **no aparezca disponible**, lo que necesita horarios
individuales.

### Unión en lugar de sustitución en el override — descartada

Permitiría "abrir" más horas de las que el negocio tiene, lo que contradice la §11
("el sistema NUNCA debe ofrecer un turno fuera de estas ventanas"). La intersección con
las ventanas del negocio se aplica igual y en ambos casos, pero la sustitución hace que
la intención del admin sea explícita y no ambigua.

### Un horario semanal de "tipo de turno" reutilizable (turno mañana, turno tarde) —
descartada como derivación de las anteriores

Buena idea de UX, y probablemente la correcta cuando haya volumen. Se descarta para el
MVP porque es una capa de indirección sobre un modelo que todavía no está validado, y
la §50 prioriza corrección sobre cantidad. Se anota como mejora de la UI futura: el
modelo de datos no necesita cambiar para soportarla.

## Consecuencias

**Positivas**

- Configurar un negocio nuevo es definir los horarios del negocio y, opcionalmente,
  ajustar dos o tres profesionales.
- El caso común (todos trabajan el horario del negocio) no requiere configuración
  adicional.
- La UI puede mostrar el horario efectivo resuelto, que es lo que el admin quiere ver.
- Agregar un profesional nuevo no requiere configurar 7 días.

**Negativas / costos aceptados**

- El modelo tiene un caso especial ("sin filas significa heredar") que hay que
  documentar y testear. Un test específico verifica que un profesional sin filas hereda
  y que uno con filas no.
- Un override de un solo día no puede expresar "los martes hasta las 16:00 y el resto
  igual que el negocio" sin redefinir el día entero. Se acepta: en la práctica el
  override suele diferir en varios días, y redefinir un día completo es fácil de
  entender.
- La UI necesita una noción de "origen" del horario (negocio o propio) para no
  confundir al admin. Es un detalle de presentación, no de modelo.
- Un profesional con override en todos los días pierde el vínculo con el negocio: si
  el negocio cambia sus horarios, ese profesional no se ve afectado. Es lo esperado,
  pero hay que comunicarlo en la UI o genera soporte.

**Revisar si**

- Aparecen negocios donde cada profesional tiene un patrón distinto y la herencia deja
  de ser el caso mayoritario. Ahí tiene sentido un modelo de "plantillas de horario".
- Un negocio opera en varias sedes con horarios distintos. Hoy una sede es un negocio
  distinto, lo cual es correcto.
