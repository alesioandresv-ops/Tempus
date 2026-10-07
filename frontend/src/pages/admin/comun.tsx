import { ApiError } from '@/services/api'

/**
 * Helpers comunes de las pestañas del panel admin (Fase C).
 *
 * Viven acá y no en `AdminPage.tsx` para que las pestañas nuevas no importen de
 * la página que las monta (un import circular que se rompe al primer refactor).
 */

/** El mensaje que le sirve a la persona, venga de donde venga. */
export function mensajeDeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 422) {
      const mensajes = Object.values(error.fieldErrors)
      if (mensajes.length > 0) return mensajes.join(' ')
    }
    return error.message || `Error ${error.status}`
  }
  return 'No se pudo conectar con la API. Revisa que el servidor esté arriba.'
}

export function MensajeDeError({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <p
      className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
      role="alert"
    >
      {error}
    </p>
  )
}

/** Instante ISO (UTC) a "14/10 09:00" en la hora local del navegador. */
export function formatearFechaHora(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const dd = String(d.getDate()).padStart(2, '0')
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${dd}/${mm} ${hh}:${mi}`
}

/** Fecha "2026-10-14" a "14/10/2026". */
export function formatearFecha(iso: string): string {
  const [anio, mes, dia] = iso.slice(0, 10).split('-')
  if (!anio || !mes || !dia) return iso
  return `${dia}/${mes}/${anio}`
}

/**
 * El valor de un `<input type="datetime-local">` ("2026-10-14T09:00", sin zona)
 * a un ISO UTC ("2026-10-14T12:00:00Z"). El `<input>` interpreta el valor como
 * hora local, que es justo lo que quiere mandar quien lo escribe.
 */
export function desdeDatetimeLocal(valor: string): string {
  const d = new Date(valor)
  return Number.isNaN(d.getTime()) ? valor : d.toISOString()
}

/** El set cerrado de duraciones del catálogo (mismo `DURACIONES_PERMITIDAS`). */
export const DURACIONES = [15, 30, 45, 60, 90, 120] as const

export const ETIQUETAS_ESTADO: Record<string, string> = {
  confirmed: 'Confirmada',
  pending_hold: 'En espera',
  cancelled: 'Cancelada',
  completed: 'Completada',
  no_show: 'No asistió',
}

export const ETIQUETAS_KIND_BLOQUEO: Record<string, string> = {
  blocked: 'Bloqueado',
  private: 'Privado',
  unpaid: 'No remunerado',
}

export const ETIQUETAS_KIND_AUSENCIA: Record<string, string> = {
  vacation: 'Vacaciones',
  leave: 'Licencia',
  sick: 'Enfermedad',
  absence: 'Ausencia',
}

/** Precio "9000.00" (y su divisa, si se conoce) a "$ 9.000,00" es-AR. */
export function formatearPrecio(precio: string, divisa = 'ARS'): string {
  const numero = Number(precio)
  if (Number.isNaN(numero)) return precio
  return new Intl.NumberFormat('es-AR', {
    style: 'currency',
    currency: divisa,
    minimumFractionDigits: 2,
  }).format(numero)
}

/**
 * Teléfono E.164 a "línea de WhatsApp". La base guarda el E.164 a veces con
 * `+` (seed) y a veces sin (alta por flujo público): normalizar acá evita que
 * el panel muestre `++54911...`.
 */
export function formatearTelefono(e164: string | null | undefined): string {
  if (!e164) return ''
  const limpio = e164.startsWith('+') ? e164.slice(1) : e164
  return limpio ? `+${limpio}` : ''
}