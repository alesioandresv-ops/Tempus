import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { api } from '@/services/api'

/**
 * Clave de la consulta de cobertura (`GET /business/cobertura`).
 *
 * **La invalidan todos los cambios que la mueven**: asignar o desasignar un
 * servicio a un profesional, activar o archivar un profesional, crear o archivar
 * un servicio. Sin esas invalidaciones el aviso "este servicio no lo hace nadie"
 * seguiria despues de arreglarlo, y un aviso que no se va es un aviso que nadie
 * lee.
 */
export const CLAVE_COBERTURA = ['panel', 'cobertura']

/**
 * Banner que avisa cuando un servicio del negocio **no tiene a nadie que lo
 * atienda**.
 *
 * **Este es el aviso que faltaba.** Un servicio sin profesionales no se puede
 * reservar, aunque el negocio tenga horarios, precios y una pagina publica que se
 * ve entera y correcta: el cliente llega, elige el servicio y se encuentra con
 * que no hay nadie. El paso del panel que lo arregla--asignarle el servicio a un
 * profesional-- es un paso aparte, y es facil no hacerlo sin enterarse.
 *
 * El numero viene de `GET /business/cobertura`, que cuenta con la **misma** regla
 * que usa la reserva (`professional_services`, profesional activo y no archivado).
 * No es un conteo propio del frontend: si divergieran, el aviso mentiria.
 */
export function AvisoCobertura() {
  const { data: cobertura } = useQuery({
    queryKey: CLAVE_COBERTURA,
    queryFn: api.obtenerCobertura,
    staleTime: 60_000,
    retry: false,
  })

  if (!cobertura) return null

  const sinNadie = cobertura.filter((c) => c.profesionales_elegibles === 0)
  if (sinNadie.length === 0) return null

  const nombres = sinNadie.map((c) => c.nombre).join(', ')

  return (
    <div className="mb-4 rounded-lg border border-red-300 bg-red-50 p-4 text-red-900 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <div>
            <p className="font-semibold">
              {sinNadie.length === 1
                ? 'Un servicio no tiene a nadie que lo atienda'
                : `${sinNadie.length} servicios no tienen a nadie que los atienda`}
            </p>
            <p className="text-sm text-red-800">
              {nombres}. Nadie puede reservarlos hasta que le asignes el servicio a
              un profesional: tu pagina publica los muestra y despues no los puede
              tomar.
            </p>
          </div>
        </div>
        <Link
          to="/admin?tab=professionals"
          className={cn(
            buttonVariants({ size: 'sm' }),
            'bg-red-600 text-white hover:bg-red-700'
          )}
        >
          Asignar profesionales
        </Link>
      </div>
    </div>
  )
}
