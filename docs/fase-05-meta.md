# Fase 0.5 — Meta (WhatsApp Business Platform)

**Estado: en curso (arranque sin chip).** La parte que no necesita el chip ya está
implementada y documentada acá; la parte que sí lo necesita queda como checklist
explícito al final.

Dependencias externas de esta fase:

- **Cuenta de Meta for Developers** (la del equipo Tempus) para crear la app.
- **Un chip físico** para registrar el número de WhatsApp: Meta verifica el número
  con un SMS/llamada al SIM. Sin chip no hay número y sin número no hay envío real.
  Es la única razón por la que esta fase se entrega parcial.

Todo lo que toca código (Embedded Signup, guardado cifrado de credenciales) está
hecho y testeado con Meta mockeada; lo que falta es configuración en la consola de
Meta y el hardware. Referencias: ADR-0013 (WABA por negocio con Embedded Signup),
ADR-0012 (outbox), Fase D-3 (`docs` de recordatorios, `backend/app/workers/outbox.py`).

---

## 1. Lo que ya está en el código

| Pieza | Dónde | Notas |
|---|---|---|
| Intercambio `code` → token | `backend/app/modules/notifications/meta_signup.py` | OAuth server-side con `client_secret`; el secreto nunca viaja al navegador |
| Endpoint de conexión | `POST /api/v1/business/config/whatsapp/connect` | Lee el WABA del usuario, guarda la conexión cifrada en `whatsapp_connections` como `pending`/inactiva |
| Botón "Conectar WhatsApp" | `frontend/src/pages/admin/ConfiguracionTab.tsx` | Popup de Facebook Login con scope `whatsapp_business_management`, `response_type: code` |
| Credenciales en reposo | `whatsapp_connections` (`access_token_encrypted`, Fernet vía `app/core/encryption.py`) | El frontend nunca ve el token; la API solo devuelve estado y últimos 4 caracteres |
| Webhook | `GET/POST /api/v1/webhooks/whatsapp` | Verificación GET con `META_WEBHOOK_VERIFY_TOKEN`; POST firmado con HMAC de `META_APP_SECRET` |
| Tests | `backend/tests/integration/test_whatsapp_connect.py` | 6 escenarios: connect feliz sin número, sin WABA, code rechazado, reconnect, scopes, token cifrado |

**Sin chip, no hay número y el registro del número queda pendiente a propósito:**
el WABA se conecta, la conexión se guarda `pending` e inactiva (la outbox la
saltea), y cuando llegue el chip se registra el número y se activa el switch.

---

## 2. En Meta for Developers (manual, requiere la cuenta del equipo)

Nada de esto es código: es configuración en `developers.facebook.com`. Guardar los
IDs/tokens resultantes en `.env` (jamás commitearlos).

### 2.1 Crear la app Business "Tempus"

1. developers.facebook.com → *My Apps* → *Create App*.
2. Tipo de app: **Business** (no Consumer). Nombre: `Tempus`.
3. Email de contacto: el de la plataforma (`ayuda@tempus.app`, ver `.env.example`).
4. Política de privacidad y términos: la app no entra en modo público hasta tener
   URLs reales. Convención propuesta (a publicar junto con el sitio):
   `https://tempus.app/legal/privacidad` y `https://tempus.app/legal/terminos`.
5. Anotar el **App ID** y generar/guardar el **App Secret** (App Settings → Basic).

### 2.2 Agregar los productos que usa el flujo

- **WhatsApp** (Cloud API): es el producto que provee el WABA y las plantillas.
- **Facebook Login** (para Embedded Signup): en *Settings → Valid OAuth Redirect
  URIs* cargar las URLs donde vive la pestaña Configuración del panel (`/admin`):
  - desarrollo: `http://localhost:5173/admin`
  - despliegue: `https://<dominio-publico>/admin`

### 2.3 WABA de plataforma, sin número

El flujo de Embedded Signup conecta el **WABA de cada negocio** (el del dueño que
autoriza). Además la plataforma necesita su **propio WABA** para las plantillas
base que se ofrecen a todos los negocios (Fase D-3, `WHATSAPP_PLATFORM_TEMPLATE_*`):

1. En la app de Meta, *WhatsApp → API Setup* (o *WhatsApp → Get Started*).
2. Crear o seleccionar el WABA de plataforma en Business Manager.
3. **No registrar ningún número todavía**: cada intento pide el SMS de verificación.
   Dejar el WABA sin números hasta tener el chip.

### 2.4 Plantillas `recordatorio_24h` y `recordatorio_2h` (draft)

Categoría **UTILITY**, idioma **es_AR** (`WHATSAPP_DEFAULT_LOCALE`). El cuerpo usa
**exactamente 6 variables, en este orden** — es el orden con el que la outbox arma
el mensaje (`backend/app/workers/outbox.py`):

| Variable | Contenido | Ejemplo |
|---|---|---|
| `{{1}}` | Nombre del cliente | `Lucía` |
| `{{2}}` | Nombre del servicio | `Corte y peinado` |
| `{{3}}` | Fecha del turno (ISO, local del negocio) | `2026-10-15` |
| `{{4}}` | Hora de inicio (HH:MM, local del negocio) | `14:30` |
| `{{5}}` | Nombre del profesional | `María` |
| `{{6}}` | Nombre del negocio | `El Peluche` |

Borradores sugeridos (dejar en **draft**; la aprobación va en el checklist):

- `recordatorio_24h` (UTILITY, es_AR):
  `Hola {{1}}, te recordamos tu turno en {{6}} del servicio {{2}} para el {{3}} a las {{4}} hs. Te espera {{5}}. ¡Nos vemos!`
- `recordatorio_2h` (UTILITY, es_AR):
  `Hola {{1}}, tu turno de {{2}} en {{6}} empieza en 2 horas ({{4}} hs). Te atiende {{5}}. ¡Te esperamos!`

Fuera de la ventana de 24 h de Meta solo se pueden enviar plantillas aprobadas
(ADR-0012): los recordatorios no salen hasta que las plantillas estén aprobadas.

### 2.5 Webhook

El webhook ya existe en la API (`/api/v1/webhooks/whatsapp`). En la app de Meta:

1. *WhatsApp → Configuration → Webhook* → *Edit*.
2. URL callback: `https://<dominio-publico>/api/v1/webhooks/whatsapp`.
3. Verify token: el mismo valor que `META_WEBHOOK_VERIFY_TOKEN` del `.env`
   (generado, no commitearlo).
4. Suscribir el evento `messages`.

---

## 3. Variables de entorno (`.env`)

```dotenv
META_APP_ID=123456789012345              # App ID de la app "Tempus" (2.1)
META_APP_SECRET=<app-secret>             # App Secret de la app (2.1)
META_WEBHOOK_VERIFY_TOKEN=<generado>     # Firma del webhook (2.5)
META_EMBEDDED_SIGNUP_APP_ID=123456789012345        # Igual al App ID de 2.1
META_EMBEDDED_SIGNUP_CONFIG_ID=<config-id>         # Del paso "WhatsApp Embedded Signup" de la app
WHATSAPP_PLATFORM_TEMPLATE_REMINDER_2H=recordatorio_2h
WHATSAPP_PLATFORM_TEMPLATE_REMINDER_1H=recordatorio_1h
```

`META_APP_ID` y `META_EMBEDDED_SIGNUP_APP_ID` son públicos (un App ID de Facebook
lo es por definición): la API se los devuelve al panel para abrir el popup.
`META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN` y los tokens de cada negocio **no**
se commitean ni se devuelven por API.

---

## 4. Pruebas posibles hoy (sin chip)

1. **Lógica completa con Meta mockeada**: `cd backend && python -m pytest tests/integration/test_whatsapp_connect.py`.
2. **Flujo real hasta donde deja el chip**: con `META_APP_ID/SECRET` cargados y la
   app creada, abrir el panel → *Administración → Configuración → Conectar WhatsApp*:
   autorizar, elegir/crear el WABA sin seleccionar número → el backend guarda la
   conexión `pending` y el panel la muestra inactiva. Ese es el límite: el siguiente
   paso de Meta pide el SMS al número.
3. **Verificación del webhook**: llamar al GET de verificación con el token.

---

## 5. Checklist pendiente para el chip

Cuando exista el chip (SIM físico con SMS):

- [ ] Registrar el número de los WABA (plataforma y del negocio de prueba) — el
      SMS de verificación de Meta va al SIM.
- [ ] Cargar `phone_number_id` en la conexión (o repetir el connect con el número
      elegido) y activar el switch de recordatorios por el panel.
- [ ] Enviar las plantillas a revisión y esperar **aprobación** de Meta.
- [ ] Probar un recordatorio real con El Peluche (`admin@el-peluche.com.ar`).
- [ ] Confirmar que `whatsapp_connections` conserva el token cifrado después de un
      reconnect y que el panel muestra `Activo`.
- [ ] Publicar política de privacidad y términos (`/legal/...`) y pasar la app a
      modo público si se va a usar Embedded Signup con cuentas de terceros.
- [ ] Validación viva del intercambio OAuth y del flujo SDK con la app real
      (sin app creada, los tests usan mocks).