# ADR-0001: Monolito modular por dominios

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §28, §50
- **Contexto en:** ARCHITECTURE.md §3

## Contexto

El sistema tiene diez dominios (auth, businesses, professionals, services, schedules,
availability, bookings, customers, notifications, reports) con reglas complejas entre
ellos: la disponibilidad depende de horarios, reservas, bloqueos y ausencias; la reserva
depende de la disponibilidad y dispara notificaciones; las notificaciones dependen de
reservas.

Con un equipo chico, dividir en servicios introduce un costo operativo real —red,
despliegue, observabilidad distribuida, contratos de API, transacciones entre
servicios— antes de que exista un problema de escala que lo justifique.

## Decisión

Un solo proceso desplegable, un solo esquema de base de datos, un solo repositorio.
**Modular**, no monolítico-desordenado: cada dominio es un directorio en
`app/modules/` y sus capas (router, schemas, service, repository, models).

Las dependencias apuntan hacia adentro y en una sola dirección:

```
api/routers  →  services  →  repositories  →  db
                    ↓
              core (config, security, errors, logging)
```

Regla de imports, aplicada por tooling y verificada en CI: **ningún módulo importa
repositorios de otro módulo**. Los dominios se comunican por dos vías y solo dos:

1. **Imports de servicio** en la dirección permitida. La única dependencia entre
   dominios del camino crítico es `bookings → availability`, y es de una sola
   dirección: el motor de disponibilidad **no** importa `bookings`. Las reservas
   bloqueantes se le pasan como datos al motor, que no sabe qué son reservas.
2. **Eventos de dominio** para efectos colaterales, procesados por el worker.
   `bookings.confirmed` produce el outbox de WhatsApp sin que `bookings` conozca el
   proveedor de mensajes.

## Alternativas consideradas

### Microservicios por dominio — descartada

No hay un problema que resuelvan. El volumen esperado de un SaaS de reservas de
negocios pequeños no justifica la complejidad. El costo no es escribir el código: es
la orquestación de despliegue, los contratos, la observabilidad distribuida, los
retries, el tracing, y sobre todo **perder la transacción de base de datos**, que es
justo lo que garantiza que no haya doble reserva.

Se revisa si alguna vez aparece un componente con un perfil de escalado genuinamente
distinto (por ejemplo, un motor de disponibilidad que deba escalar a CPU para
calcular disponibilidad masiva de miles de profesionales).

### Monolito con capas horizontales (`models/`, `schemas/`, `services/`) — descartada

Es la estructura propuesta en la §29 y es perfectamente válida. La descartamos por
legibilidad: con capas horizontales, agregar un campo a un servicio obliga a tocar
cuatro carpetas separadas, y el alcance de un dominio no se ve de un vistazo. Con
directorios verticales, todo lo de `bookings` está junto y el límite entre dominios es
una carpeta visible en el árbol.

Costo de esta decisión: consultar "qué usa la tabla `bookings`" exige un `grep`, no
una mirada. Aceptable a esta escala.

### Base de código única sin módulos (un solo `main.py` grande) — descartada

Rechazada explícitamente por la §50. Se traduce en un archivo que nadie puede
modificar sin miedo.

## Consecuencias

**Positivas**

- La reserva, su outbox de notificaciones y su registro de idempotencia se escriben
  **en una única transacción SQL**. Es la garantía central del producto y no depende
  de compensar ningún patrón distribuido.
- Un solo despliegue. El perfil de hosting gratuito es viable solo porque no hay
  cinco servicios que coordinar.
- El motor de disponibilidad es una función pura y se testea sin base de datos ni red.
- La regla de imports entre dominios es verificable con una dependencia de análisis,
  no con una convención social.

**Negativas / costos aceptados**

- Acoplamiento entre dominios dentro del mismo proceso. Un cambio en el modelo de
  `bookings` puede forzar cambios en `reports`. Se acepta: la alternativa es un bus
  de eventos y consistencia eventual por un problema que no tenemos.
- Un error en un módulo puede tumbar todo el proceso. Se acepta: el aislamiento de
  fallos se compra con complejidad, y no tenemos un perfil de carga que lo justifique.
- Los tests de integración pueden correr en paralelo sin aislamiento de proceso
  (comparten base). Se compensa con transacciones con rollback por test.

**Revisar si**

- Aparece un dominio con requisitos de escalado o de disponibilidad radicalmente
  distintos.
- El equipo crece a más de ~5 personas y el desarrollo paralelo en un mismo proceso
  se vuelve un cuello de botella real.
