/** Detalle del 500: cuerpo completo y response headers. */
export default async function run(page) {
  const KEY = 'j2l8x7v6n5m4p3q2r2e5b1f7a9c4e6f8'
  const r = await page.evaluate(async (k) => {
    const resp = await fetch(`/api/v1/admin/businesses?key=${k}`)
    return { status: resp.status, body: await resp.text(), headers: Object.fromEntries(resp.headers) }
  }, KEY)
  return r
}
