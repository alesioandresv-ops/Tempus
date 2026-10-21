# Tempus - Frontend (Vite + React + TS)

Panel de administración y páginas públicas de reservas.

## Deploy en Cloudflare (Worker)

La app es una SPA con rutas del lado del cliente (`/admin`, `/admin/bookings`,
`/<slug>`, etc.). El Worker (`worker.ts`, declarado en `wrangler.toml`) sirve los
assets de `dist/` y resuelve esas rutas al refrescar (F5 / acceso directo al link).

**Dos cosas tienen que estar bien juntas, y las dos son necesarias:**

- `binding = "ASSETS"` en la sección `[assets]` de `wrangler.toml`. **Sin esa línea el
  Worker no ve los assets**: `[assets]` los sube, pero no expone `env.ASSETS`, y toda
  ruta del SPA muere con un **500 / Error 1101 "Worker threw exception"**. El error
  real es `TypeError: Cannot read properties of undefined (reading 'fetch')` y no
  nombra ni el binding ni el archivo, así que desde el navegador parece un problema de
  rutas. Es la trampa más cara de este deploy.
- `worker.ts` resuelve el fallback del SPA a mano: intenta el asset y, si da 404
  (una ruta del cliente no es un archivo), sirve `index.html`. `ASSETS.fetch("/login")`
  por sí solo devuelve 404, no el index.

`public/_routes.json` (que `vite build` copia a `dist/`) declara qué rutas invocan al
Worker, para que los assets estáticos ni siquiera pasen por él.

`not_found_handling = "single-page-application"` en `wrangler.toml` **no alcanza** por
sí solo, a pesar de lo que sugiere el nombre.

**El Worker además proxya `/api/*` al backend en Render.** Eso no es un detalle de
comodidad: es lo que hace que el frontend y la API queden en el **mismo origen**, y de
ahí depende la cookie de refresh. Con `SameSite=strict`, una respuesta cross-site hace
que el navegador **descarte** la cookie, la sesión no sobrevive a un F5 y el panel
expulsa a la persona al login.

Pasos, desde `frontend/`:

```bash
npm i
npm run build
npx wrangler deploy
```

- `npm run build` corre `tsc -b && vite build` y genera la carpeta `dist/` (con
  `_routes.json` adentro).

> `CORS_ORIGINS` del backend tiene que incluir el dominio del Worker
> (`https://tempus.tempusapp.workers.dev`): es el `Origin` que manda el navegador. Si
> falta, `/auth/refresh` devuelve 403 y la sesión no se restaura.

> `vercel.json` se eliminó junto con el deploy en Vercel (ADR-0014 eligió Cloudflare).

## Variables de entorno

Copiá `.env.example` a `.env` y completá lo que corresponda:

```bash
# Origen del backend, SIN el prefijo /api/v1 (ej: https://api.tempus.app).
# DEJALO VACÍO en producción: ver abajo.
VITE_API_URL=

# Dominio raíz para detectar el subdominio de negocio en el login (hoy sin uso).
VITE_ROOT_DOMAIN=

# Sitekey público de Cloudflare Turnstile.
VITE_TURNSTILE_SITE_KEY=
```

**`VITE_API_URL` vacío es lo correcto en producción.** Vacío hace que el frontend use
la ruta relativa `/api/v1` (`src/services/api.ts`), que el Worker proxya a Render:
mismo origen, cookie `SameSite=strict` intacta. Si en cambio exportás el dominio de
Render, el navegador hace una petición cross-site, descarta la cookie y **el F5 echa a
la persona al login**. El mismo efecto tiene poner `http://localhost:8000` en
`frontend/.env` durante el desarrollo: ahí no se usa el proxy de Vite, se rompe la
misma cookie, y el síntoma aparece solo al recargar.

Si definís `VITE_API_URL`, es solo el **origen**: el prefijo `/api/v1` lo agrega el
código, no la variable. Con `https://api.tempus.app/api/v1` el frontend pegaría a
`/api/v1/api/v1`.

En Cloudflare Pages, definí esas variables en **Settings → Environment Variables**
(build). En local, `frontend/.env`.

## Desarrollo local

```bash
npm run dev
```

El server de Vite (puerto 5173) proxya `/api` a `http://127.0.0.1:8000` (backend).
Para que ese proxy se use, `VITE_API_URL` tiene que quedar **vacío** en
`frontend/.env`: si apunta a `http://localhost:8000`, la petición sale cross-site y el
navegador descarta la cookie de refresh (ver arriba).
