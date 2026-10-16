import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Calendar,
  DollarSign,
  Ticket,
  AlertCircle,
  Clock,
  Users2,
  BarChart3,
} from 'lucide-react'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { api } from '@/services/api'
import { cn } from '@/lib/utils'
import type { ReservaDePanel } from '@/types'
import {
  ETIQUETAS_ESTADO,
  formatearFechaHora,
  formatearPrecio,
} from './comun'

/**
 * Pestaña Panel: el resumen que se ve al entrar al admin.
 *
 * **Por que existe.** Antes `/admin` abria directo en Reservas, que es una tabla;
 * no habia una vista que contestara "como viene el dia" sin leerla entera. Aca se
 * juntan los numeros que ya devuelven los endpoints que existian--no hay backend
 * nuevo--en una sola pantalla.
 *
 * **De donde sale cada dato.** Los KPI de ingresos/reservas/cancelacion son de
 * `/business/estadisticas` (ultimos 30 dias). "Turnos de hoy" es un conteo del
 * listado de reservas de hoy, y "proximos turnos" usa `solo_pendientes`, que el
 * backend resuelve como turnos todavia cerrables (confirmados/en espera cuyo
 * `ends_at` no paso). El ranking de servicios se calcula acá sobre 30 dias porque
 * las estadisticas solo traen el corte por profesional.
 */

/** Cuántos turnos muestra la tarjeta de próximos turnos. */
const LIMITE_PROXIMOS = 6

/** Fecha local del navegador en "YYYY-MM-DD", el formato que espera la API. */
function isoLocal(d: Date): string {
  const anio = d.getFullYear()
  const mes = String(d.getMonth() + 1).padStart(2, '0')
  const dia = String(d.getDate()).padStart(2, '0')
  return `${anio}-${mes}-${dia}`
}

/** Los estados que cuentan como "turno pedido" para el ranking de servicios. */
const ESTADOS_QUE_CUENTAN = new Set(['confirmed', 'pending_hold', 'completed'])

const COLOR_ESTADO: Record<string, string> = {
  confirmed: 'bg-green-100 text-green-700',
  pending_hold: 'bg-amber-100 text-amber-700',
  cancelled: 'bg-red-100 text-red-700',
  completed: 'bg-blue-100 text-blue-700',
  no_show: 'bg-gray-100 text-gray-600',
}

function Insignia({ estado }: { estado: string }) {
  return (
    <span
      className={cn(
        'inline-flex rounded-full px-2 py-0.5 text-xs font-medium',
        COLOR_ESTADO[estado] ?? 'bg-gray-100 text-gray-600'
      )}
    >
      {ETIQUETAS_ESTADO[estado] ?? estado}
    </span>
  )
}

interface KpiProps {
  etiqueta: string
  valor: string
  detalle: string
  icono: typeof Calendar
  tono: string
}

function Kpi({ etiqueta, valor, detalle, icono: Icono, tono }: KpiProps) {
  return (
    <Card>
      <CardContent className="flex items-start justify-between gap-4 p-6">
        <div className="min-w-0">
          <p className="text-sm font-medium text-muted-foreground">{etiqueta}</p>
          <p className="mt-2 truncate text-3xl font-bold">{valor}</p>
          <p className="mt-1 text-xs text-muted-foreground">{detalle}</p>
        </div>
        <span
          className={cn(
            'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg',
            tono
          )}
        >
          <Icono className="h-5 w-5" />
        </span>
      </CardContent>
    </Card>
  )
}

export function PanelTab({ onIrAReservas }: { onIrAReservas?: () => void }) {
  const hoy = isoLocal(new Date())
  const hace30 = isoLocal(new Date(Date.now() - 30 * 24 * 60 * 60 * 1000))

  const estadisticas = useQuery({
    queryKey: ['panel', 'estadisticas'],
    queryFn: () => api.getEstadisticas(),
  })

  const deHoy = useQuery({
    queryKey: ['panel', 'reservas-hoy', hoy],
    queryFn: () => api.listarReservas({ desde: hoy, hasta: hoy, limite: 100 }),
  })

  /*
   * Próximos turnos. El listado del backend viene ordenado por `starts_at`
   * descendente--el más lejano primero--así que pedir "los primeros 6" daría
   * justo lo contrario. Se piden los 6 del **final** (la última página) usando el
   * `total`, que la misma respuesta trae, y se reordenan ascendente más abajo.
   * Solo son dos GETs cuando hay más de 6 turnos pendientes.
   */
  const proximos = useQuery({
    queryKey: ['panel', 'proximos'],
    queryFn: async () => {
      const primera = await api.listarReservas({ solo_pendientes: true, limite: 1 })
      const total = primera.total
      if (total <= LIMITE_PROXIMOS) {
        return api.listarReservas({ solo_pendientes: true, limite: LIMITE_PROXIMOS })
      }
      return api.listarReservas({
        solo_pendientes: true,
        limite: LIMITE_PROXIMOS,
        offset: total - LIMITE_PROXIMOS,
      })
    },
  })

  /** Los pendientes de más próximo a más lejano (el backend los da al revés). */
  const proximosOrdenados = useMemo(
    () =>
      [...(proximos.data?.items ?? [])].sort((a, b) =>
        a.starts_at.localeCompare(b.starts_at)
      ),
    [proximos.data]
  )

  const equipo = useQuery({
    queryKey: ['panel', 'equipo'],
    queryFn: () => api.listarProfesionales(),
  })

  const treintaDias = useQuery({
    queryKey: ['panel', 'reservas-30d', hace30, hoy],
    queryFn: () => api.listarReservas({ desde: hace30, hasta: hoy, limite: 200 }),
  })

  const divisa = treintaDias.data?.items[0]?.currency ?? 'ARS'

  /*
   * Ranking de servicios sobre los ultimos 30 dias. Se cuenta por `servicio_nombre`
   * (desnormalizado en la reserva) y se descartan las canceladas y los no-show: un
   * turno que no se hizo no dice que servicio se pide mas.
   */
  const serviciosTop = useMemo(() => {
    const items = treintaDias.data?.items ?? []
    const conteo = new Map<string, number>()
    for (const r of items) {
      if (!ESTADOS_QUE_CUENTAN.has(r.status)) continue
      const nombre = r.servicio_nombre ?? 'Sin servicio'
      conteo.set(nombre, (conteo.get(nombre) ?? 0) + 1)
    }
    return [...conteo.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
  }, [treintaDias.data])

  const maxServicio = serviciosTop[0]?.[1] ?? 0
  const stats = estadisticas.data
  const cancelacion = stats
    ? `${(stats.tasa_cancelacion * 100).toFixed(1).replace('.', ',')}%`
    : '—'

  /*
   * Turnos de hoy sin contar cancelados ni ausencias: un turno que no se va a
   * hacer no es "un turno de hoy". Se cuenta sobre los items (no el `total`, que
   * los incluye a todos), acotado por el limite del listado de hoy.
   */
  const turnosHoy = useMemo(
    () =>
      (deHoy.data?.items ?? []).filter(
        (r) => r.status !== 'cancelled' && r.status !== 'no_show'
      ).length,
    [deHoy.data]
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Panel</h1>
        <p className="text-sm text-muted-foreground">
          Cómo viene el negocio: hoy, los próximos turnos y los últimos 30 días.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          etiqueta="Turnos de hoy"
          valor={deHoy.isLoading ? '…' : String(turnosHoy)}
          detalle="Agendados para hoy"
          icono={Calendar}
          tono="bg-teal-50 text-teal-600"
        />
        <Kpi
          etiqueta="Recaudación"
          valor={estadisticas.isLoading ? '…' : formatearPrecio(stats?.ingresos ?? '0', divisa)}
          detalle="Últimos 30 días"
          icono={DollarSign}
          tono="bg-green-50 text-green-600"
        />
        <Kpi
          etiqueta="Reservas"
          valor={estadisticas.isLoading ? '…' : String(stats?.total_reservas ?? 0)}
          detalle="Últimos 30 días"
          icono={Ticket}
          tono="bg-blue-50 text-blue-600"
        />
        <Kpi
          etiqueta="Cancelación"
          valor={estadisticas.isLoading ? '…' : cancelacion}
          detalle="Canceladas sobre el total"
          icono={AlertCircle}
          tono="bg-amber-50 text-amber-600"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-start justify-between space-y-0">
            <div className="space-y-1">
              <CardTitle className="flex items-center gap-2 text-lg">
                <Clock className="h-5 w-5 text-muted-foreground" />
                Próximos turnos
              </CardTitle>
              <CardDescription>Confirmados o en espera que todavía no terminaron.</CardDescription>
            </div>
            {onIrAReservas && (
              <Button variant="ghost" size="sm" onClick={onIrAReservas}>
                Ver todos
              </Button>
            )}
          </CardHeader>
          <CardContent>
            {proximos.isLoading ? (
              <p className="text-sm text-muted-foreground">Cargando…</p>
            ) : proximosOrdenados.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No hay turnos próximos agendados.
              </p>
            ) : (
              <ul className="divide-y">
                {proximosOrdenados.map((r: ReservaDePanel) => (
                  <li key={r.id} className="flex items-center gap-3 py-3">
                    <div className="w-24 shrink-0 text-sm font-medium tabular-nums text-gray-900">
                      {formatearFechaHora(r.starts_at)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">
                        {r.cliente_nombre ?? 'Cliente'}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {r.servicio_nombre ?? 'Servicio'}
                        {r.profesional_nombre ? ` • ${r.profesional_nombre}` : ''}
                      </p>
                    </div>
                    <Insignia estado={r.status} />
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <BarChart3 className="h-5 w-5 text-muted-foreground" />
              Servicios más pedidos
            </CardTitle>
            <CardDescription>Últimos 30 días.</CardDescription>
          </CardHeader>
          <CardContent>
            {treintaDias.isLoading ? (
              <p className="text-sm text-muted-foreground">Cargando…</p>
            ) : serviciosTop.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Sin turnos en los últimos 30 días.
              </p>
            ) : (
              <ul className="space-y-4">
                {serviciosTop.map(([nombre, cantidad]) => (
                  <li key={nombre} className="space-y-1.5">
                    <div className="flex items-center justify-between text-sm">
                      <span className="truncate font-medium">{nombre}</span>
                      <span className="text-muted-foreground">
                        {cantidad} {cantidad === 1 ? 'turno' : 'turnos'}
                      </span>
                    </div>
                    <Progress
                      value={maxServicio ? (cantidad / maxServicio) * 100 : 0}
                      indicatorClassName="bg-teal-500"
                    />
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <Users2 className="h-5 w-5 text-muted-foreground" />
            Equipo
          </CardTitle>
          <CardDescription>
            {equipo.data
              ? `${equipo.data.length} ${equipo.data.length === 1 ? 'profesional' : 'profesionales'} cargados.`
              : 'Quiénes atienden en el negocio.'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {equipo.isLoading ? (
            <p className="text-sm text-muted-foreground">Cargando…</p>
          ) : (equipo.data?.length ?? 0) === 0 ? (
            <p className="text-sm text-muted-foreground">
              Todavía no hay profesionales cargados.
            </p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {(equipo.data ?? []).map((p) => (
                <li
                  key={p.id}
                  className="flex items-center gap-2 rounded-full border bg-white py-1 pl-1 pr-3"
                >
                  <span className="flex h-7 w-7 items-center justify-center rounded-full bg-teal-500 text-xs font-medium text-white">
                    {p.display_name.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="text-sm">{p.display_name}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
