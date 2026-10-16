import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { api } from '@/services/api'
import type { SubscriptionStatus } from '@/types'

/**
 * Banner que avisa cuando el mes gratis terminó.
 *
 * El backend marca el estado con `code="plan_vencido"` en los 402, y expone
 * `dias_restantes` y `checkout_url` en `GET /business/suscripcion`. El aviso se
 * decide con `dias_restantes === 0 && plan !== 'pro'`, que es la misma condicion
 * que usa la guardia del backend (`publicacion.mes_gratis_vencido`) sin tener que
 * reinterpretar la regla: mostrar el aviso acá (lectura libre) es lo que permite
 * que solo las escrituras se bloqueen y la lectura siga andando.
 *
 * **No** se muestra en la pestaña `pagos`: ahi ya aparece el CTA para activar Pro.
 */
export function BloqueoVencimiento() {
  const [searchParams] = useSearchParams()
  const tab = searchParams.get('tab') ?? 'panel'
  const { data: estado } = useQuery<SubscriptionStatus>({
    queryKey: ['panel', 'suscripcion'],
    queryFn: () => api.getSuscripcion(),
    staleTime: 60_000,
    retry: false,
  })

  if (!estado) return null
  // El aviso es de "mes gratis vencido", no de "no publicable": un negocio
  // suspendido tiene `es_publicable === false` sin haber vencido, y decirle "tu mes
  // gratis terminó" sería mentira. `dias_restantes` es 0 exactamente cuando el mes
  // venció (o cuando es Pro, que ya descartamos), así que es la condición precisa y
  // con los mismos campos que expone el backend.
  if (estado.plan === 'pro') return null
  if (estado.dias_restantes > 0) return null

  // No duplicar el aviso en la pestaña de pagos: el card de suscripción ya lo explica
  if (tab === 'pagos') return null

  return (
    <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 p-4 text-amber-900 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <div>
            <p className="font-semibold">Tu mes gratis terminó</p>
            <p className="text-sm text-amber-800">
              Para seguir cargando turnos, servicios o cambios en el panel, activá
              el plan Pro.
            </p>
          </div>
        </div>
        <Link
          to="/admin?tab=pagos"
          className={cn(
            buttonVariants({ size: 'sm' }),
            'bg-amber-600 text-white hover:bg-amber-700'
          )}
        >
          Activar plan Pro
        </Link>
      </div>
    </div>
  )
}
