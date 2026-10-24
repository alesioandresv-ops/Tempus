/** (c) Completar el form y enviar el comprobante -> payments/intent. */
export default async function run(page) {
  const respuestas = []
  page.on('response', async (r) => {
    if (r.url().includes('/payments/intent')) {
      let cuerpo = ''
      try { cuerpo = (await r.text()).slice(0, 400) } catch { /* vacío */ }
      respuestas.push({ status: r.status, cuerpo })
    }
  })
  const popups = []
  page.on('popup', (p) => popups.push(p.url().slice(0, 200)))

  await page.goto('https://tempus.tempusapp.workers.dev/login', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2500)
  await page.fill('#email', 'qa-test-qa-mvmtvtxz@test.com')
  await page.fill('#password', 'SuperSegura2026!')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(6000)
  await page.goto('https://tempus.tempusapp.workers.dev/checkout-pro', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(4000)
  await page.fill('#email', 'qa-test-qa-mvmtvtxz@test.com')
  await page.click('button[type="submit"]:has-text("Enviar comprobante")')
  await page.waitForTimeout(5000)
  const texto = (await page.evaluate(() => document.body.innerText)).slice(0, 600)
  return { respuestas, popups, texto }
}
