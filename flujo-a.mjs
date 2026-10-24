/** Test de flujo completo: alta -> checkout -> intento -> admin -> activación. */
export default async function run(page, ui) {
  const out = {}
  const KEY = 'j2l8x7v6n5m4p3q2r2e5b1f7a9c4e6f8'
  const SLUG = `test-qa-${Date.now().toString(36)}`

  // ---------- (a) Alta de negocio nuevo ----------
  await page.goto('https://tempus.tempusapp.workers.dev/register', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3000)
  out.alta_url = page.url()
  // Paso 1: negocio
  await page.fill('#business_name', 'QA Test Salon')
  await page.fill('#slug', SLUG)
  await page.waitForTimeout(1200)
  // Paso 2: datos
  const next1 = await ui.snapshot()
  out.paso1 = next1.slice(0, 100)
  // Rellenar y avanzar
  await page.click('button[type="button"]:has-text("Continuar")')
  await page.waitForTimeout(1000)
  await page.fill('#owner_name', 'QA Tester')
  await page.fill('#email', `qa-${SLUG}@test.com`)
  await page.fill('#password', 'SuperSegura2026!')
  await page.click('button[type="button"]:has-text("Continuar")')
  await page.waitForTimeout(800)
  // Paso 3: submit
  await page.click('button[type="submit"]')
  await page.waitForTimeout(4000)
  out.despues_alta_url = page.url()
  return out
}
