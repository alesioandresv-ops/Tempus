/** (d) Admin: verificar pendiente. (e) Activar 30 días. Luego verificar público. */
export default async function run(page) {
  const KEY = 'j2l8x7v6n5m4p3q2r2e5b1f7a9c4e6f8'
  const out = {}

  // (d) stats + lista
  out.stats = await page.evaluate(async (k) => await (await fetch(`/api/v1/admin/stats?key=${k}`)).json(), KEY)
  const lista = await page.evaluate(async (k) => await (await fetch(`/api/v1/admin/businesses?key=${k}`)).json(), KEY)
  const qa = lista.find((n) => n.slug.startsWith('test-qa-'))
  out.qa_negocio = qa
  const pagos = await page.evaluate(async (k) => {
    // Busco el pago pendiente del negocio QA: filtro en la lista de pagos vía businesses
    // El admin no tiene endpoint de pagos por negocio; uso la fila del negocio.
    return null
  }, KEY)

  // (e) Activar
  const KEY_ = KEY
  out.activar = await page.evaluate(async ({ k, id }) => {
    const r = await fetch(`/api/v1/admin/businesses/${id}/activate?key=${k}`, { method: 'PUT' })
    return { status: r.status, body: (await r.text()).slice(0, 200) }
  }, { k: KEY_, id: qa.id })

  // Verificar post-activación: negocio en lista con pro_until
  const lista2 = await page.evaluate(async (k) => await (await fetch(`/api/v1/admin/businesses?key=${k}`)).json(), KEY)
  const qa2 = lista2.find((n) => n.slug.startsWith('test-qa-'))
  out.qa_post = qa2

  // URL pública ya no debe dar 403 business_paused
  const publico = await page.evaluate(async (slug) => {
    const r = await fetch(`/api/v1/public/businesses/${slug}`)
    return { status: r.status, body: (await r.text()).slice(0, 120) }
  }, qa.slug)
  out.publico = publico

  return out
}
