/** (b) Verificación completa del checkout con sesión real de la app. */
export default async function run(page) {
  const respuestas = []
  page.on('response', async (r) => {
    if (r.url().includes('/business/suscripcion') || r.url().includes('/payments/intent')) {
      let cuerpo = ''
      try { cuerpo = (await r.text()).slice(0, 500) } catch { /* vacío */ }
      respuestas.push({ status: r.status, url: r.url().slice(-40), cuerpo })
    }
  })
  // Login
  await page.goto('https://tempus.tempusapp.workers.dev/login', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2500)
  await page.fill('#email', 'qa-test-qa-mvmtvtxz@test.com')
  await page.fill('#password', 'SuperSegura2026!')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(6000)
  // Checkout
  await page.goto('https://tempus.tempusapp.workers.dev/checkout-pro', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(5000)
  const texto = (await page.evaluate(() => document.body.innerText)).slice(0, 1000)
  // ¿Botón copiar funciona?
  const botonesCopiar = await page.evaluate(() => document.querySelectorAll('button[aria-label^="Copiar"]').length)
  return { respuestas: respuestas.slice(0, 3), texto, botonesCopiar }
}
