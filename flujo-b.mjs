/** Captura la respuesta de /business/suscripcion hecha POR LA APP (con Bearer). */
export default async function run(page) {
  const capturas = []
  page.on('response', async (r) => {
    if (r.url().includes('/business/suscripcion') && r.status() >= 400) {
      let cuerpo = ''
      try { cuerpo = (await r.text()).slice(0, 400) } catch { cuerpo = '(sin cuerpo)' }
      capturas.push({ status: r.status, cuerpo })
    }
  })
  await page.goto('https://tempus.tempusapp.workers.dev/login', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2000)
  await page.fill('#email', 'qa-test-qa-mvmtvtxz@test.com')
  await page.fill('#password', 'SuperSegura2026!')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(6000)
  await page.goto('https://tempus.tempusapp.workers.dev/checkout-pro', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(5000)
  return { capturas: capturas.slice(0, 4) }
}
