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
VITE_API_URL=
```

En Vercel, definí esas variables en **Project → Settings → Environment Variables**.

## Desarrollo local

```bash
npm run dev
```

El server de Vite (puerto 5173) proxya `/api` a `http://127.0.0.1:8000` (backend).
