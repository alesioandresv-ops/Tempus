import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
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
import {
  ETIQUETAS_KIND_AUSENCIA,
  MensajeDeError,
  desdeDatetimeLocal,
  formatearFechaHora,
  mensajeDeError,
} from './comun'

/**
 * Pestaña Vacaciones del admin (Fase C).
 *
 * Vacaciones, licencias y ausencias de cada profesional. Desde el panel solo se
 * crean **aprobadas** (el estado `pending` existe para cuando las reporta el
 * propio profesional); crear una pendiente desde acá sería un estado que nadie
 * revisa. Una ausencia saca la disponibilidad de esa persona en la ventana.
 */
export function VacacionesTab() {
  const queryClient = useQueryClient()

  const { data: profesionales } = useQuery({
    queryKey: ['profesionales'],
    queryFn: api.listarProfesionales,
  })

  const { data: ausencias, isLoading, error } = useQuery({
    queryKey: ['ausencias-admin'],
    queryFn: () => api.getAusencias(),
  })

  const eliminar = useMutation({
    mutationFn: (id: string) => api.eliminarAusencia(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ausencias-admin'] }),
  })

  const [profesionalId, setProfesionalId] = useState('')
  const [kind, setKind] = useState('vacation')
  const [desde, setDesde] = useState('')
  const [hasta, setHasta] = useState('')
  const [motivo, setMotivo] = useState('')
  const [errorForm, setErrorForm] = useState<string | null>(null)

  const crear = useMutation({
    mutationFn: () =>
      api.crearAusencia({
        professional_id: profesionalId,
        starts_at: desdeDatetimeLocal(desde),
        ends_at: desdeDatetimeLocal(hasta),
        kind,
        reason: motivo.trim() || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ausencias-admin'] })
      setDesde('')
      setHasta('')
      setMotivo('')
    },
    onError: (e) => setErrorForm(mensajeDeError(e)),
  })

  const nombreDe = (id: string) =>
    profesionales?.find((p) => p.id === id)?.display_name ?? id

  const puedeCrear = profesionalId !== '' && desde !== '' && hasta !== ''
  const ordenInvalido = desde && hasta && new Date(hasta) <= new Date(desde)

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Nueva ausencia</CardTitle>
          <CardDescription>
            Vacaciones, licencia o enfermedad de un profesional. Sin ausencia el
            profesional figura en la agenda pública; con una, su disponibilidad
            desaparece en esa ventana.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="block space-y-1">
              <span className="text-sm font-medium">Profesional</span>
              <select
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={profesionalId}
                onChange={(e) => setProfesionalId(e.target.value)}
              >
                <option value="">Elegí un profesional...</option>
                {profesionales?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.display_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1">
              <span className="text-sm font-medium">Tipo</span>
              <select
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={kind}
                onChange={(e) => setKind(e.target.value)}
              >
                {Object.entries(ETIQUETAS_KIND_AUSENCIA).map(([v, etiqueta]) => (
                  <option key={v} value={v}>
                    {etiqueta}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1">
              <span className="text-sm font-medium">Desde</span>
              <Input
                type="datetime-local"
                value={desde}
                onChange={(e) => setDesde(e.target.value)}
              />
            </label>
            <label className="block space-y-1">
              <span className="text-sm font-medium">Hasta</span>
              <Input
                type="datetime-local"
                value={hasta}
                onChange={(e) => setHasta(e.target.value)}
              />
            </label>
            <label className="block space-y-1 sm:col-span-2 lg:col-span-4">
              <span className="text-sm font-medium">Motivo</span>
              <Input
                placeholder="Ej: vacaciones de verano"
                value={motivo}
                onChange={(e) => setMotivo(e.target.value)}
              />
            </label>
          </div>
          <div className="flex items-center gap-2">
            <Button disabled={!puedeCrear || ordenInvalido || crear.isPending} onClick={() => crear.mutate()}>
              {crear.isPending ? 'Creando...' : 'Crear ausencia'}
            </Button>
            {ordenInvalido && (
              <span className="text-sm text-destructive">Hasta debe ser posterior a desde.</span>
            )}
          </div>
          <MensajeDeError error={errorForm} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Ausencias</CardTitle>
          <CardDescription>
            Vacaciones y licencias cargadas. Borrar una devuelve la disponibilidad
            del profesional.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Cargando ausencias...</p>
          ) : ausencias && ausencias.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Profesional</TableHead>
                  <TableHead>Ventana</TableHead>
                  <TableHead>Tipo</TableHead>
                  <TableHead>Motivo</TableHead>
                  <TableHead className="text-right">Acción</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {ausencias.map((a) => (
                  <TableRow key={a.id}>
                    <TableCell className="font-medium">{nombreDe(a.professional_id)}</TableCell>
                    <TableCell className="whitespace-nowrap">
                      {formatearFechaHora(a.starts_at)} → {formatearFechaHora(a.ends_at)}
                    </TableCell>
                    <TableCell>{ETIQUETAS_KIND_AUSENCIA[a.kind] ?? a.kind}</TableCell>
                    <TableCell>{a.reason ?? '—'}</TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={eliminar.isPending}
                        onClick={() =>
                          window.confirm('¿Borrar esta ausencia?') && eliminar.mutate(a.id)
                        }
                      >
                        Borrar
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="text-sm text-muted-foreground">No hay ausencias cargadas.</p>
          )}
          {(eliminar.isError || error !== null) && (
            <MensajeDeError error={mensajeDeError(eliminar.error ?? error)} />
          )}
        </CardContent>
      </Card>
    </div>
  )
}