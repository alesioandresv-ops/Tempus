/** Verifica el negocio recién creado vía admin: trial de 14 días. */
export default async function run(page) {
  const KEY = 'j2l8x7v6n5m4p3q2r2e5b1f7a9c4e6f8'
  const lista = await page.evaluate(async (k) => {
    const r = await fetch(`/api/v1/admin/businesses?key=${k}`)
    return r.json()
  }, KEY)
  const qa = lista.filter((n) => n.slug.startsWith('test-qa-'))
  const hoy = new Date()
  const resultado = qa.map((n) => {
    const fin = new Date(n.trial_ends_at)
    const dias = Math.round((fin - hoy) / 86400000)
    return { slug: n.slug, trial_ends_at: n.trial_ends_at, dias_calculados: dias, dias_reportados: n.trial_dias_restantes, es_14: dias === 14 }
  })
  return { total_negocios: lista.length, qa: resultado }
}
