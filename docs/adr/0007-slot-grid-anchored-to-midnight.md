# ADR-0007: Grilla de slots anclada a medianoche local

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §12, §46
- **Contexto en:** ARCHITECTURE.md §5.1, §8.2

## Contexto

Después de calcular los huecos libres de un profesional para un día, hay que decidir
**qué horas concretas se ofrecen**. Es una decisión de producto disfrazada de detalle
técnico, y se nota en la experiencia del cliente.

La §46 pide que reservar sea en "pocos pasos" y que la experiencia sea "extremadamente
sencilla desde el móvil". Un cliente que ve horarios 14:07, 15:22 y 16:37 no sabe qué
significa; uno que ve 14:00, 15:00 y 16:00 entiende de inmediato, y además puede
decir "el jueves a las 15" por teléfono.

El paso (`slot_interval_minutes`) es configurable por negocio, con 15 minutos por
defecto. La pregunta es contra qué se ancla la grilla.

## Decisión

**La grilla se ancla a medianoche del día local del negocio, no al inicio de cada hueco
libre.**

La generación recorre `00:00, 00:15, 00:30, ...` y toma cada instante `t` tal que
`[t, t + duration)` esté íntegramente contenido en un hueco libre. La consecuencia es
que los horarios ofrecidos siempre caen en múltiplos del paso desde medianoche.

Resultado: con paso de 15 minutos, el cliente ve `09:00, 09:15, 09:30, 11:30, 14:00`.
Nunca `14:07`.

## Alternativas consideradas

### Anclar la grilla al inicio de cada hueco libre — descartada

Genera el primer slot justo cuando termina un bloqueo: tras un bloqueo de 14:00–15:30 se
ofrecería 15:30, y si otro bloqueo empieza a las 16:07, se ofrecería 16:07. Maximiza
los huecos aprovechables, porque no desperdicia los minutos parciales.

Se descarta por dos razones:

1. **Rompe la comunicación humana.** " fifteen" no existe como concepto para un cliente;
   "15:30" sí. Un cliente que intenta confirmar por teléfono un turno de "16:07" no
   tiene forma de describirlo, y el profesional tampoco lo va a decir así.
2. **Genera slots que desalinean la agenda.** Con bloqueos frecuentes, la agenda del
   negocio se llena de horarios que no encajan con nada, y el profesional termina
   saltándose turnos.

Es la opción que optimiza el modelo de datos por sobre la experiencia humana, y en un
producto cuyo cliente es una persona con un teléfono en la mano, eso es un mal
intercambio.

### Grilla fija global (siempre 30 minutos) — descartada

Más simple, pero incompatible con servicios de 45 minutos, que son la norma en el
mercado objetivo. Con paso de 30 y duración de 45, la intersección de un hueco con la
grilla produce slots que no caen en la grilla, o huecos que no se pueden llenar. El paso
tiene que ser configurable por negocio, y la granularidad viene del servicio.

### Derivar el paso del servicio (duración / 2, etc.) — descartada

Acopla dos conceptos que no tienen por qué coincidir. Un servicio de 90 minutos
Ofrece 45 intervalos de 15, no 90, y eso no es información útil. El paso describe la
**granularidad de la agenda** del negocio, que es una decisión operativa, no una
propiedad del servicio.

### Ofrecer horas de corte de "inicio de turno" configurables — descartada por ahora

Tiene un caso de uso legítimo: un negocio donde todos los turnos duran exactamente
90 minutos quiere 8:00, 9:30, 11:00 y no 8:00, 8:15, 8:30. Cubrirlo exigiría que la
duración del servicio sea múltiplo del paso y una noción de turno con hora fija.

Se descarta para el MVP, pero el modelo ya lo soporta sin migración: basta con que el
negocio configure `slot_interval_minutes` en un valor que sea divisor de la duración
típica (por ejemplo 45 para servicios de 90). Queda anotado como mejora de la UI.

## Consecuencias

**Positivas**

- Horarios redondos y predecibles, en todos los negocios y para todos los clientes.
- La agenda del profesional queda alineada, sin huecos visualmente irregulares.
- Coincide con la forma en que los clientes ya piden turnos por teléfono.
- Determinista y trivial de testear: la grilla depende solo de la medianoche, no del
  estado del día.

**Negativas / costos aceptados**

- Se desaprovechan los huecos parciales que dejan los bloqueos. Con paso de 15 minutos y
  un bloqueo de 14:07, se pierde hasta 15 minutos de la mañana. Es el costo
  consciente de la decisión.
- Si el negocio abre a las 09:07, el primer slot es 09:15 y los primeros 8 minutos
  no se pueden reservar. Es un caso raro y la UI de configuración avisa cuando la hora
  de apertura no es múltiplo del paso.
- Un servicio cuya duración no es múltiplo del paso puede generar slots que no encajan
  con la grilla visualmente. No es incorrecto: la disponibilidad es real, la grilla es
  una visualización. Se documenta para que nadie lo trate como bug.
- Con paso de 5 minutos y durations de 45, se ofrecen 9 slots por hora de ventana: en
  pantalla móvil son más opciones de las que un cliente quiere ver. Por eso el
  default es 15, y la UI de la agenda puede agrupar por hora en pantallas chicas.

**Revisar si**

- Aparecen negocios donde el paso de 15 minutos deja demasiados huecos por semana
  como para ignorar la pérdida. Señal: uso frecuente de ventanas de duración no múltiplo
  de 15.
- Un negocio exige horarios de inicio fijo con duración fija. Ahí tiene sentido
  modelar la noción de "turno" y este ADR se revisa.
