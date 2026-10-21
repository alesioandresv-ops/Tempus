const BACKEND = "https://tempus-h3uo.onrender.com"

/**
 * Worker del despliegue: proxya `/api/*` a Render y sirve el SPA para el resto.
 *
 * **Dos cosas tienen que estar bien para que las rutas del SPA no den 500:**
 *
 * 1. `binding = "ASSETS"` en `wrangler.toml`. Sin esa linea, `[assets]` sube los
 *    archivos pero **no** expone `env.ASSETS`, y el Worker muere con
 *    `TypeError: Cannot read properties of undefined (reading 'fetch')`, que es el
 *    Error 1101 que se ve en el navegador. El error no menciona `ASSETS` ni el
 *    `wrangler.toml`, asi que es dificil de encontrar desde el sintoma.
 * 2. Resolver la ruta del SPA a mano, como se hace abajo: `ASSETS.fetch('/login')`
 *    busca un asset llamado `login` y devuelve 404, que no es un error sino el caso
 *    normal de una ruta del cliente.
 *
 * El orden es explicito: primero `/api/*`, despues el asset real, y si no existe,
 * `index.html`.
 */
export default {
  async fetch(request: Request, env: any): Promise<Response> {
    const url = new URL(request.url)

    if (url.pathname.startsWith("/api/")) {
      return fetch(new Request(BACKEND + url.pathname + url.search, request))
    }

    // Si esto falta, es configuracion y conviene decirlo con esas palabras: el
    // TypeError que sale solo no nombra ni el binding ni el archivo donde se declara.
    if (!env.ASSETS) {
      return new Response(
        "Falta el binding ASSETS: declarar `binding = \"ASSETS\"` en la seccion [assets] de wrangler.toml.",
        { status: 500 }
      )
    }

    const asset = await env.ASSETS.fetch(request)
    if (asset.status !== 404) {
      return asset
    }

    // Ruta del SPA: el mismo request pero apuntando a la raiz, que siempre existe.
    // Se reusa `request` para no perder el metodo ni los headers; solo cambia el path.
    const raiz = new URL(request.url)
    raiz.pathname = "/"
    return env.ASSETS.fetch(new Request(raiz, request))
  },
} as ExportedHandler
