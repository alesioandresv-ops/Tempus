/** Prueba directa de la causa del 500: stats (no toca DB admin) vs businesses (sí). */
export default async function run(page) {
  const KEY = 'j2l8x7v6n5m4p3q2r2e5b1f7a9c4e6f8'
  const stats = await page.evaluate(async (k) => {
    const r = await fetch(`/api/v1/admin/stats?key=${k}`)
    return { status: r.status, body: (await r.text()).slice(0, 150) }
  }, KEY)
  const businesses = await page.evaluate(async (k) => {
    const r = await fetch(`/api/v1/admin/businesses?key=${k}`)
    return { status: r.status, body: (await r.text()).slice(0, 150) }
  }, KEY)
  return { stats, businesses }
}
