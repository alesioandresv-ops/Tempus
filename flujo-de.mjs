/** Verificación final completa: lista, activate, público, cron. */
export default async function run(page) {
  const KEY = 'j2l8x7v6n5m4p3q2r2e5b1f7a9c4e6f8'
  const out = {}

  // Lista completa de negocios
  out.lista = await page.evaluate(async (k) => await (await fetch(`/api/v1/admin/businesses?key=${k}`)).json(), KEY)
  out.lista = out.lista.map((n) => ({ slug: n.slug, estado: n.estado, dias: n.trial_dias_restantes, plan: n.plan, pro_until: n.pro_until }))

  // Negocio QA
  const qa = (await page.evaluate(async (k) => await (await fetch(`/api/v1/admin/businesses?key=${k}`)).json(), KEY))
    .find((n) => n.slug.startsWith('test-qa-'))
  out.qa_id = qa?.id

  // (e1) Activar 30 días
  out.activate = await page.evaluate(async ({ k, id }) => {
    const r = await fetch(`/api/v1/admin/businesses/${id}/activate?key=${k}`, { method: 'PUT' })
    return { status: r.status, body: (await r.text()).slice(0, 250) }
  }, { k: KEY, id: qa.id })

  // (e2) Después: estado del negocio y URL pública
  const lista2 = await page.evaluate(async (k) => await (await fetch(`/api/v1/admin/businesses?key=${k}`)).json(), KEY)
  const qa2 = lista2.find((n) => n.slug.startsWith('test-qa-'))
  out.qa_post = { estado: qa2.estado, plan: qa2.plan, pro_until: qa2.pro_until }

  out.publico = await page.evaluate(async (slug) => {
    const r = await fetch(`/api/v1/public/businesses/${slug}`)
    return { status: r.status, body: (await r.text()).slice(0, 120) }
  }, qa.slug)

  // (4) Cron
  out.cron = await page.evaluate(async (k) => {
    const r = await fetch(`/api/v1/admin/cron/vencer?key=${k}`, { method: 'POST' })
    return { status: r.status, body: (await r.text()).slice(0, 200) }
  }, KEY)

  // Stats finales
  out.stats_final = await page.evaluate(async (k) => await (await fetch(`/api/v1/admin/stats?key=${k}`)).json(), KEY)

  return out
}
