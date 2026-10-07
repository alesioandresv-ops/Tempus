import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { api } from '@/services/api'
import { MensajeDeError, formatearFecha, formatearPrecio, mensajeDeError } from './comun'

/**
 * Pestaña Estadísticas del admin (Fase C).
 *
 * Un GET a `/business/estadisticas` con una ventana de fechas locales. Sin
 * fechas el backend devuelve los últimos 30 días. `ingresos` suma el
 * `price_snapshot` de reservas confirmadas y completadas (las canceladas no
 * generan ingreso), y `tasa_cancelacion` es canceladas sobre el total.
 */
export function EstadisticasTab() {
  const [desde, setDesde] = useState('')
  const [hasta, setHasta] = useState('')
  const [rango, setRango] = useState<{ desde?: string; hasta?: string }>({})

  const { data: servicios } = useQuery({
    queryKey: ['servicios'],
    queryFn: api.listarServicios,
  })
  const divisa = servicios?.[0]?.currency ?? 'ARS'

  const { data: stats, isLoading, error, isFetching } = useQuery({
    queryKey: ['estadisticas', rango],
    queryFn: () => api.getEstadisticas(rango.desde, rango.hasta),
  })

  const aplicar = () => {
    setRango({ desde: desde || undefined, hasta: hasta || undefined })
  }

  const cancelacion = (() => {
    if (!stats) return null
    return `${(stats.tasa_cancelacion * 100).toFixed(1).replace('.', ',')}%`
  })()

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Ventana</CardTitle>
          <CardDescription>
            Fechas locales del negocio. Vacías = últimos 30 días.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <label className="block space-y-1">
              <span className="text-sm font-medium">Desde</span>
              <Input type="date" value={desde} onChange={(e) => setDesde(e.target.value)} />
            </label>
            <label className="block space-y-1">
              <span className="text-sm font-medium">Hasta</span>
              <Input type="date" value={hasta} onChange={(e) => setHasta(e.target.value)} />
            </label>
            <Button onClick={aplicar} disabled={isFetching}>
              {isFetching ? 'Actualizando...' : 'Actualizar'}
            </Button>
            {error !== null && <span className="text-sm text-destructive">{mensajeDeError(error)}</span>}
          </div>
        </CardContent>
      </Card>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Calculando estadísticas...</p>
      ) : stats ? (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Ingresos</CardTitle>
                <CardDescription>Confirmadas + completadas</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-bold">{formatearPrecio(stats.ingresos, divisa)}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Reservas</CardTitle>
                <CardDescription>Total en la ventana</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-bold">{stats.total_reservas}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Cancelación</CardTitle>
                <CardDescription>Canceladas / total</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-bold">{cancelacion}</p>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Ocupación por profesional</CardTitle>
              <CardDescription>
                Turnos concretados o por concretarse (confirmadas + completadas) en{' '}
                {formatearFecha(stats.desde)} – {formatearFecha(stats.hasta)}.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {stats.por_profesional.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Profesional</TableHead>
                      <TableHead className="text-right">Turnos</TableHead>
                      <TableHead className="text-right">Cancelados</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {stats.por_profesional.map((p) => (
                      <TableRow key={p.professional_id}>
                        <TableCell className="font-medium">{p.nombre}</TableCell>
                        <TableCell className="text-right">{p.turnos}</TableCell>
                        <TableCell className="text-right">{p.cancelados}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <p className="text-sm text-muted-foreground">
                  Sin turnos concretados en la ventana.
                </p>
              )}
            </CardContent>
          </Card>
        </>
      ) : error !== null ? (
        <MensajeDeError error={mensajeDeError(error)} />
      ) : null}
    </div>
  )
}