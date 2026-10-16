export default {
  async fetch(request: Request, env: any): Promise<Response> {
    const url = new URL(request.url)
    const BACKEND = "https://tempus-h3uo.onrender.com"

    // Proxy /api/ -> Render (arregla tu 405)
    if (url.pathname.startsWith("/api/")) {
      const dest = BACKEND + url.pathname + url.search
      const newReq = new Request(dest, request)
      return fetch(newReq)
    }

    // Todo lo demás -> frontend SPA
    return env.ASSETS.fetch(request)
  }
} as ExportedHandler
