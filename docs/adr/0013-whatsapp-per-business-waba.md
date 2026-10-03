# ADR-0013: Un WABA por negocio con OAuth Embedded Signup

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §18, §24
- **Contexto en:** ARCHITECTURE.md §12.1

## Contexto

La §18 exige usar la Meta WhatsApp Business Platform / Cloud API y prohíbe explícitamente
automatizaciones basadas en WhatsApp Web, scraping o sesiones persistentes. Eso está
claro y no hay decisión que tomar: Web es frágil, viola los términos de Meta y además se
rompe con cada cambio de su interfaz.

La decisión que sí hay que tomar es **de quién es el número de WhatsApp desde el que
salen los mensajes**: uno de la plataforma compartido por todos los negocios, o uno de
cada negocio.

No es una decisión de infraestructura. Afecta quién es responsable de qué, qué pasa
cuando un cliente se va, y cómo se comporta la reputación del canal con el uso.

## Decisión

**Cada negocio conecta su propia WABA (WhatsApp Business Account) y su propio número.**

- El onboarding es por **OAuth Embedded Signup** de Meta, con el flujo manual —pegar
  `phone_number_id` y el token permanente— como camino de fallback documentado, porque
  hay negocios que no pueden completar el flujo OAuth.
- El token se guarda **cifrado en reposo** (Fernet) en `whatsapp_connections`, y nunca
  se devuelve por la API ni aparece en un log.
- `messaging_limit_tier` y `quality_rating` se mantienen actualizados desde los
  webhooks, de modo que la cuota real se conoce **por negocio**.

## Alternativas consideradas

### Un único número de la plataforma para todos los negocios — descartada

Mucho más simple de operar y de vender. Se descarta por cuatro razones, y tres de ellas
no son reparables con trabajo:

1. **La política de Meta en un contexto multi-tenant lo prohíbe.** Meta es explícita
   sobre esto. Un solo número para muchos negocios emisores es exactamente el patrón que
   la plataforma revoca, y cuando lo revoca se lleva por delante a todos los tenants a la
   vez.
2. **La reputación es compartida y no transferible.** `quality_rating` y
   `messaging_limit_tier` son por número. Un tenant que spammea o recibe muchos
   reportes degrada el canal de todos los demás, y los demás no pueden hacer nada al
   respecto. En una plataforma SaaS eso es un problema que no se puede resolver con
   engineering: es un problema de incentivos.
3. **El cliente final no sabe a quién está hablando.** Un mensaje "Tu turno en la peluquería
   de Juan" que llega de un número desconocido, con el nombre de otro negocio en el
   remitente, es un mensaje que se reporta como spam. La tasa de reportes es exactamente
   lo que degrada la calidad del canal.
4. **El cliente no puede irse con su número.** Un negocio que crece y quiere negociar
   con su proveedor, o quiere migrar, se queda atrapado. Eso es un problema comercial,
   y en un SaaS el cliente lo nota.

## Consecuencias

**Positivas**

- La reputación queda aislada: lo que hace un negocio no afecta a los demás. El nivel de
  calidad es accionable por el negocio, y por lo tanto mejorable.
- El negocio es dueño de su número y de su historial. Puede migrar su WABA fuera de la
  plataforma sin perder nada, lo que es un argumento de venta.
- El remitente es reconocible para el cliente final, lo que reduce los reportes de spam.
- La separación de responsabilidades es clara: si un negocio viola las políticas de
  Meta, es ese negocio el que pierde su WABA.
- El costo de mensajes se puede atribuir por negocio, lo que hace posible el modelo de
  precios por uso y el control de presupuesto por tenant.

**Negativas / costos aceptados**

- El onboarding es un flujo real con pasos de Meta, y muchos negocios se atascan ahí.
  Requiere soporte. Mitigación: el flujo OAuth reduce la copia manual del token, y hay
  un camino de fallback manual bien documentado.
- Hay N conexiones que gestionar: N tokens que cifrar, N números que verificar, N
  estados que sincronizar por webhook. Es operativamente más pesado, y es el precio de
  esa aislación.
- Hay que construir y mantener la gestión de credenciales: cifrado, rotación, y un
  camino seguro para que el negocio pegue su token sin que pase por los logs.
- Un negocio con la WABA mal configurada rompe solo. Eso es correcto y también es un
  ticket de soporte.
- Los webhooks tienen que resolver a qué negocio pertenecen, por número. Es un lookup en
  `whatsapp_connections` y hay que indexarlo.

**Revisar si**

- El modelo de negocio termina siendo intermediario (la plataforma compra los mensajes y
  los revende a un markup). Ahí el número del negocio sigue siendo el correcto por
  política de Meta, pero la facturación se vuelve un problema de contabilidad aparte.
- Meta introduce un modelo donde la plataforma pueda operar el número del cliente con
  autorización del cliente. Eso sería un ADR nuevo, porque cambia de nuevo quién es
  responsable de la calidad.
