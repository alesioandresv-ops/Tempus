# Tempus - Frontend (Vite + React + TS)

Panel de administración y páginas públicas de reservas.

## Deploy en Vercel

La app es una SPA con rutas del lado del cliente (`/admin`, `/admin/appointments`,
`/admin/clients`, `/admin/services`, `/admin/staff`, etc.). `vercel.json` incluye un
rewrite de `/(.*)` a `/index.html` para que esas rutas funcionen al refrescar
(F5 / acceso directo al link).

Pasos, desde `frontend/`:

```bash
npm i
npm run build
vercel --prod
```

- `npm run build` corre `tsc -b && vite build` y genera la carpeta `dist/`.
- `vercel --prod` publica el proyecto (detecta Vite y usa `dist/` como salida).

## Variables de entorno

Copiá `.env.example` a `.env` y completá lo que corresponda:

```bash
# Origen del backend, SIN el prefijo /api/v1 (ej: https://api.tempus.app).
VITE_API_URL=

# Dominio raíz para detectar el subdominio de negocio en el login (hoy sin uso).
VITE_ROOT_DOMAIN=

# Sitekey público de Cloudflare Turnstile.
VITE_TURNSTILE_SITE_KEY=
```

`VITE_API_URL` es solo el **origen**: el prefijo `/api/v1` lo agrega el código
(`src/services/api.ts` arma `${VITE_API_URL}/api/v1`), no la variable. Si le pusieras
`https://api.tempus.app/api/v1`, el frontend pegaría a `/api/v1/api/v1`.

En Vercel, definí esas variables en **Project → Settings → Environment Variables**.

## Desarrollo local

```bash
npm run dev
```

El server de Vite (puerto 5173) proxya `/api` a `http://127.0.0.1:8000` (backend).
