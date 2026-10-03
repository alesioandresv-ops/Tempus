import { test, expect } from '@playwright/test'

test.describe('Flujo de reserva', () => {
  test('puede ver la página de un negocio', async ({ page }) => {
    await page.goto('/test-business')
    await expect(page.getByText('Test Business')).toBeVisible()
  })

  test('puede seleccionar un servicio', async ({ page }) => {
    await page.goto('/test-business')
    await page.getByText('Corte').click()
    await expect(page.getByText('Seleccioná un profesional')).toBeVisible()
  })

  test('puede seleccionar un profesional', async ({ page }) => {
    await page.goto('/test-business')
    await page.getByText('Corte').click()
    await page.getByText('Gabriel').click()
    await expect(page.getByText('Seleccioná una fecha')).toBeVisible()
  })

  test('puede seleccionar una fecha', async ({ page }) => {
    await page.goto('/test-business')
    await page.getByText('Corte').click()
    await page.getByText('Gabriel').click()
    await page.locator('input[type="date"]').fill('2026-01-15')
    await page.getByText('Continuar').click()
    await expect(page.getByText('Seleccioná un horario')).toBeVisible()
  })

  test('puede completar el formulario de reserva', async ({ page }) => {
    await page.goto('/test-business')
    await page.getByText('Corte').click()
    await page.getByText('Gabriel').click()
    await page.locator('input[type="date"]').fill('2026-01-15')
    await page.getByText('Continuar').click()
    await page.getByText('10:00').click()
    await page.getByText('Continuar').click()
    
    await page.getByPlaceholder('Nombre').fill('Juan')
    await page.getByPlaceholder('Apellido').fill('Pérez')
    await page.getByPlaceholder('WhatsApp').fill('+5491112345678')
    await page.getByText('Confirmar reserva').click()
    
    await expect(page.getByText('¡Reserva confirmada!')).toBeVisible()
  })
})
