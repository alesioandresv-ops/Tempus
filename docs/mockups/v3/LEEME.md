# Propuesta de diseño v3 — Registro + página pública de reservas

Mockups estáticos y capturas del rediseño de:

- `/register` → alta self-service del negocio.
- `/:slug` → página pública donde el cliente elige servicio, día y horario.
- Confirmación del turno.

> **No hay código de producción modificado en esta entrega.** Todo vive en
> `docs/mockups/v3/` para revisar la dirección visual antes de tocar
> `RegisterPage.tsx` / `BusinessPage.tsx`.

## Cómo verlo

Abrir `docs/mockups/v3/index.html` en el navegador: es el tablero de presentación
con todas las capturas, las decisiones de diseño y la tabla de cambios.

Si preferís una sola imagen de punta a punta, `tablero.png` es la captura completa
del tablero (1280 × 7492).

Prototipos navegables (estáticos):

| Pantalla | Archivo |
|---|---|
| Registro — paso 1 | `registro.html?paso=1` |
| Registro — paso 3 | `registro.html?paso=3` |
| Reserva — servicio | `reserva.html?paso=1` |
| Reserva — día y hora | `reserva.html?paso=2` |
| Reserva — datos | `reserva.html?paso=3` |
| Confirmación | `confirmacion.html` |

Las capturas respetan el mismo estado que muestran los prototipos. Para volver a
generarlas:

```powershell
node "C:\Users\Estudiante\.claude\skills\browser-automation\browser.mjs" `
  "file:///C:/Users/Estudiante/Desktop/Tempus/docs/mockups/v3/registro.html" `
  --script "C:\Users\Estudiante\Desktop\Tempus\docs\mockups\v3\_capturas.mjs" `
  --timeout 120000
```

## Dirección visual

**Tempus oscuro, página del negocio clara.** El producto conserva la estética
“aurora” (vidrio, violeta, tipografía Space Grotesk) porque es la herramienta que
usa el dueño. La página pública se viste con la identidad del negocio: papel
cálido, tinta, terracota y Fraunces. Así la vista previa del registro se lee como
*el resultado* y no como otra pantalla del formulario. Además, una página clara y
editorial convierte mejor en el celular.

### Registro

- Hero editorial con una sola promesa: la página de reservas lista en dos minutos.
- Cuatro beneficios concretos, sin testimonios ni métricas inventadas.
- Vista previa real de la página pública del negocio, con estado “Publicada”.
- Wizard con barra segmentada, link público con disponibilidad en vivo y
  `tempus.app/…` como protagonista.
- Paso 3 con resumen del negocio, zona horaria, Turnstile y términos.
- En mobile se ocultan los beneficios y la preview: queda el formulario, sin ruido.

### Reserva pública

- Portada con la marca del negocio, estado abierto/cerrado y dirección.
- Tres columnas en desktop: información del estudio, flujo de reserva y resumen
  sticky con precio y selección.
- Paso 1: servicios con duración, profesional y precio; uno seleccionado.
- Paso 2: profesionales, tira de próximos días y horarios agrupados en mañana,
  tarde y noche.
- Paso 3: solo nombre, apellido y WhatsApp. Sin cuenta, sin campos de más.
- Mobile: el resumen se convierte en una barra fija con la selección y la acción
  principal.
- Confirmación: ticket con fecha grande, QR, comprobante, calendario, WhatsApp y
  línea de tiempo de recordatorios (24 h / 2 h).

## Mapa con los datos existentes

**Registro:** `business_name`, `slug`, `owner_name`, `email`, `password`, `phone`,
`timezone`. No se agregó ningún campo.

**Reserva:** `service`, `professional`, fecha/slot, `first_name`, `last_name`,
`phone`. No se agregó ningún campo.

Los extras de la portada pública (descripción, dirección, horarios) ya existen en
el negocio; solo hay que exponerlos en la respuesta pública cuando se implemente.
El QR del ticket puede reutilizar el `secure_token` de la reserva.

## Notas de implementación

- Tokens nuevos para Tailwind: `paper`, `ink`, `terracotta`, `forest` y radios
  grandes. El acento de la página pública sale de `Business.brand_color`.
- La barra móvil se monta solo en `max-width: 860px`; el resumen sticky se oculta
  con la misma media query.
- El stepper actual se puede conservar, pero la lógica de pasos del registro ya
  existe: el rediseño es presentación, no un flujo nuevo.
- Para la tira de días y los horarios agrupados no hace falta endpoint nuevo:
  alcanza con agrupar los slots que ya devuelve la disponibilidad.

## Capturas

| Archivo | Pantalla |
|---|---|
| `registro-paso1.png` | Registro, paso 1, desktop 1440 |
| `registro-paso3.png` | Registro, paso 3, desktop 1440 |
| `registro-mobile.png` | Registro, mobile 390×932 |
| `reserva-servicios.png` | Reserva, servicio, desktop 1440 |
| `reserva-horario.png` | Reserva, día y hora, desktop 1440 |
| `reserva-datos.png` | Reserva, datos, desktop 1440 |
| `reserva-confirmacion.png` | Confirmación, desktop 1440 |
| `reserva-mobile-servicios.png` | Reserva, servicio, mobile 390×932 |
| `reserva-mobile-horario.png` | Reserva, día y hora, mobile 390×932 |
