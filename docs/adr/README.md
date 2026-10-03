# Decisiones arquitectónicas (ADR)

Registro de decisiones con contexto, alternativa descartada y consecuencias. Una decisión
que no registra su alternativa descartada no está decidida: es una preferencia.

Los ADRs son inmutables una vez aceptados. Si una decisión se revisa, se escribe un ADR
nuevo que marque al anterior como `Reemplazada por ADR-XXXX`. No se editan a posteriori.

## Formato

- **Contexto**: qué problema o restricción obliga a decidir.
- **Decisión**: qué se hace, y lo suficiente de detalle para que se pueda implementar.
- **Alternativas consideradas**: qué se descartó y **por qué**. Es la parte más
  valuable del documento.
- **Consecuencias**: qué mejora, qué empeora, y bajo qué condición se revisa.

## Índice

| ADR | Decisión | Estado | Ámbito |
|---|---|---|---|
| [0001](0001-modular-monolith.md) | Monolito modular por dominios | Aceptada | Arquitectura |
| [0002](0002-multi-tenancy-shared-schema-rls.md) | Esquema compartido con FK compuestas y RLS | Aceptada | Datos |
| [0003](0003-uuidv7-primary-keys.md) | UUIDv7 como clave primaria | Aceptada | Datos |
| [0004](0004-utc-timestamps-business-timezone.md) | UTC en base, timezone del negocio en la aplicación | Aceptada | Datos |
| [0005](0005-professional-schedule-inheritance.md) | Horarios de profesional por herencia con override | Aceptada | Producto |
| [0006](0006-availability-as-pure-domain.md) | Disponibilidad como módulo puro de dominio | Aceptada | Dominio |
| [0007](0007-slot-grid-anchored-to-midnight.md) | Grilla de slots anclada a medianoche local | Aceptada | Producto |
| [0008](0008-exclusion-constraint-double-booking.md) | `EXCLUDE USING gist` como autoridad anti-doble-reserva | Aceptada | Datos |
| [0009](0009-hashed-secure-tokens.md) | Secure tokens opacos almacenados hasheados | Aceptada | Seguridad |
| [0010](0010-tenant-from-jwt.md) | `business_id` derivado del JWT, nunca del path | Aceptada | Seguridad |
| [0011](0011-postgres-job-queue.md) | Cola de jobs en PostgreSQL | Aceptada | Infraestructura |
| [0012](0012-transactional-outbox-whatsapp.md) | Outbox transaccional y plantillas obligatorias en WhatsApp | Aceptada | Integraciones |
| [0013](0013-whatsapp-per-business-waba.md) | Un WABA por negocio con OAuth Embedded Signup | Aceptada | Integraciones |
| [0014](0014-free-tier-deployment-profile.md) | Perfil de despliegue gratuito y heartbeat de Cloudflare | Aceptada | Infraestructura |
| [0015](0015-openapi-generated-frontend-types.md) | Tipos del frontend generados desde OpenAPI | Aceptada | Frontend |

## Decisiones agrupadas por lo que protegen

**Integridad de los datos**

- [0008](0008-exclusion-constraint-double-booking.md) — no hay doble reserva. La
  garantía vive en la base, no en el código.
- [0003](0003-uuidv7-primary-keys.md) — los identificadores no son enumerables.
- [0004](0004-utc-timestamps-business-timezone.md) — las horas significan lo que
  dicen, incluso con el cambio de horario de verano.

**Aislamiento entre tenants**

- [0002](0002-multi-tenancy-shared-schema-rls.md) — tres capas de defensa, con las FK
  compuestas como barrera física.
- [0010](0010-tenant-from-jwt.md) — el IDOR eliminado de raíz, no mitigado.
- [0009](0009-hashed-secure-tokens.md) — el token del cliente no se persiste en claro
  y no existe ninguna ruta que lo adivine.

**Corrección del dominio**

- [0006](0006-availability-as-pure-domain.md) — la disponibilidad es una función pura,
  testeable por completo.
- [0005](0005-professional-schedule-inheritance.md) — la configuración que el admin
  hace tiene que ser la mínima posible para ser correcta.
- [0007](0007-slot-grid-anchored-to-midnight.md) — horarios que un humano puede
  describir por teléfono.
- [0001](0001-modular-monolith.md) — las fronteras de dominio, para que una función
  nueva no se escriba en el lugar equivocado.

**Consistencia y entrega**

- [0012](0012-transactional-outbox-whatsapp.md) — la reserva y sus mensajes son
  consistentes por construcción.
- [0011](0011-postgres-job-queue.md) — el trabajo asíncrono vive en el mismo lugar que
  los datos.
- [0013](0013-whatsapp-per-business-waba.md) — la reputación de un tenant no puede
  afectar a los demás.
- [0015](0015-openapi-generated-frontend-types.md) — el contrato no puede desviarse del
  servidor en silencio.

**Operación**

- [0014](0014-free-tier-deployment-profile.md) — el costo cero no puede comprometer los
  requisitos duros del producto.

## Decisiones aplazadas

Están en la [sección 22 de ARCHITECTURE.md](../../ARCHITECTURE.md#22-decisiones-aplazadas)
con su motivo: pagos, marketplace, dominios personalizados, mapas, email al cliente,
retenciones `pending_hold`, particionado de `bookings`, localización de la UI y
analytics.

Una decisión aplazada no es una decisión pendiente: es una decisión de no decidir
todavía, por una razón concreta.
