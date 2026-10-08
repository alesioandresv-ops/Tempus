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
import { api } from '@/services/api'
import { mensajeDeError, MensajeDeError } from './comun'

/**
 * Los estados que tiene sentido exportar en un reporte: lo que se hizo y lo que
 * no. `pending_hold` y `no_show` existen en la base pero no son una categoría
 * de cierre que alguien quiera descargar por mes.
 */
const ESTADOS = [
  { valor: '', etiqueta: 'Todos los estados' },
  { valor: 'confirmed', etiqueta: 'Confirmadas' },
  { valor: 'completed', etiqueta: 'Completadas' },
  { valor: 'cancelled', etiqueta: 'Canceladas' },
] as const

/** El mes actual en YYYY-MM, con la fecha local del navegador y no UTC. */
function mesActual(): string {
  const ahora = new Date()
  return `${ahora.getFullYear()}-${String(ahora.getMonth() + 1).padStart(2, '0')}`
}

/**
 * Pestaña Reportes del admin (Fase D-2).
 *
 * Exporta las reservas de un mes en CSV o XLSX desde
 * `GET /business/reportes/mensual`. El XLSX trae además una hoja "Resumen"
 * con ingresos (solo confirmadas/completadas) y canceladas del mes. Sin
 * reservas para los filtros, la API responde 404 y acá se muestra "Sin datos":
 * nunca se descarga un archivo vacío que parezca un reporte.
 */
export function ReportesTab() {
  const [mes, setMes] = useState(mesActual())
  const [profesionalId, setProfesionalId] = useState('')
  const [estado, setEstado] = useState('')
  const [descargando, setDescargando] = useState<'csv' | 'xlsx' | null>(null)
  const [sinDatos, setSinDatos] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data: profesionales } = useQuery({
    queryKey: ['profesionales'],
    queryFn: api.listarProfesionales,
  })

  const descargar = async (formato: 'csv' | 'xlsx') => {
    setError(null)
    setSinDatos(false)
    setDescargando(formato)
    try {
      const respuesta = await api.descargarReporteMensual({
        mes,
        formato,
        professional_id: profesionalId || undefined,
        estado: estado || undefined,
      })

      if (respuesta.status === 404) {
        setSinDatos(true)
        return
      }
      if (!respuesta.ok) {
        const cuerpo = (await respuesta.json().catch(() => ({}))) as { detail?: unknown }
        setError(typeof cuerpo.detail === 'string' ? cuerpo.detail : `Error ${respuesta.status}`)
        return
      }

      const blob = await respuesta.blob()
      const disposicion = respuesta.headers.get('Content-Disposition') ?? ''
      const nombre =
        /filename="?([^";]+)"?/.exec(disposicion)?.[1] ?? `reservas_${mes}.${formato}`

      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = nombre
      link.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(mensajeDeError(e))
    } finally {
      setDescargando(null)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Reportes mensuales</CardTitle>
        <CardDescription>
          Descargá las reservas del mes elegido. El Excel incluye una hoja
          "Resumen" con los ingresos (turnos confirmados o completados) y las
          canceladas, por profesional y por servicio.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 md:grid-cols-3">
          <label className="block space-y-1">
            <span className="text-sm font-medium">Mes</span>
            <Input
              type="month"
              value={mes}
              onChange={(e) => {
                setMes(e.target.value)
                setSinDatos(false)
              }}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-sm font-medium">Profesional</span>
            <select
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={profesionalId}
              onChange={(e) => {
                setProfesionalId(e.target.value)
                setSinDatos(false)
              }}
            >
              <option value="">Todos</option>
              {profesionales?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.display_name}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-sm font-medium">Estado</span>
            <select
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={estado}
              onChange={(e) => {
                setEstado(e.target.value)
                setSinDatos(false)
              }}
            >
              {ESTADOS.map((s) => (
                <option key={s.valor} value={s.valor}>
                  {s.etiqueta}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex items-center gap-2">
          <Button onClick={() => descargar('csv')} disabled={descargando !== null}>
            {descargando === 'csv' ? 'Descargando...' : 'Descargar CSV'}
          </Button>
          <Button variant="outline" onClick={() => descargar('xlsx')} disabled={descargando !== null}>
            {descargando === 'xlsx' ? 'Descargando...' : 'Descargar Excel'}
          </Button>
        </div>

        {sinDatos && (
          <p className="rounded-md border border-muted bg-muted/30 px-3 py-2 text-sm text-muted-foreground" role="status">
            Sin datos para {mes}
            {profesionalId || estado ? ' con los filtros elegidos' : ''}. El archivo no se
            descarga porque estaría vacío.
          </p>
        )}
        <MensajeDeError error={error} />
      </CardContent>
    </Card>
  )
}