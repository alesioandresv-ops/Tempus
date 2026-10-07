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
  ETIQUETAS_KIND_BLOQUEO,
  MensajeDeError,
  desdeDatetimeLocal,
  formatearFechaHora,
  mensajeDeError,
} from './comun'

/**
 * Pestaña Bloqueos del admin (Fase C).
 *
 * Un bloqueo con `professional_id: null` cierra **todo el negocio** (feriado
 * por motivo puntual, cierre por obra, día de mudanza); con id cierra la agenda
 * de una sola persona. El backend los sirve todos juntos y el frontend
 * distingue por el `professional_id`.
 *
 * "Editar" un bloqueo es borrarlo y crearlo de nuevo: no existe un PATCH de
 * bloqueos, el cambio real es la ventana de tiempo y recrearla es más
 * transparente que mutar una fila ya validada.
 */
export function BloqueosTab() {
  const queryClient = useQueryClient()

  const { data: profesionales } = useQuery({
    queryKey: ['profesionales'],
    queryFn: api.listarProfesionales,
  })

  const { data: bloqueos, isLoading, error } = useQuery({
    queryKey: ['bloqueos-admin'],
    queryFn: () => api.getBloqueos(),
  })

  const eliminar = useMutation({
    mutationFn: (id: string) => api.eliminarBloqueo(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['bloqueos-admin'] }),
  })

  const [profesionalId, setProfesionalId] = useState('')
  const [desde, setDesde] = useState('')
  const [hasta, setHasta] = useState('')
  const [kind, setKind] = useState('blocked')
  const [motivo, setMotivo] = useState('')
  const [errorForm, setErrorForm] = useState<string | null>(null)

  const crear = useMutation({
    mutationFn: () =>
      api.crearBloqueo({
        starts_at: desdeDatetimeLocal(desde),
        ends_at: desdeDatetimeLocal(hasta),
        kind,
        professional_id: profesionalId || null,
        reason: motivo.trim() || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['bloqueos-admin'] })
      setDesde('')
      setHasta('')
      setMotivo('')
    },
    onError: (e) => setErrorForm(mensajeDeError(e)),
  })

  const nombreDe = (id: string | null) =>
    id ? (profesionales?.find((p) => p.id === id)?.display_name ?? id) : 'Todo el negocio'

  const tipoSeleccionado = ETIQUETAS_KIND_BLOQUEO[kind] ?? kind

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Nuevo bloqueo</CardTitle>
          <CardDescription>
            Sin profesional es un cierre de todo el negocio: la disponibilidad
            pública queda sin turnos en esa ventana. Con profesional, solo su
            agenda.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="block space-y-1">
              <span className="text-sm font-medium">Alcance</span>
              <select
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={profesionalId}
                onChange={(e) => setProfesionalId(e.target.value)}
              >
                <option value="">Todo el negocio</option>
                {profesionales?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.display_name}
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
            <label className="block space-y-1">
              <span className="text-sm font-medium">Tipo</span>
              <select
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={kind}
                onChange={(e) => setKind(e.target.value)}
              >
                {Object.entries(ETIQUETAS_KIND_BLOQUEO).map(([v, etiqueta]) => (
                  <option key={v} value={v}>
                    {etiqueta}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1 sm:col-span-2 lg:col-span-4">
              <span className="text-sm font-medium">Motivo</span>
              <Input
                placeholder="Ej: feriado puente, mudanza, reparación"
                value={motivo}
                onChange={(e) => setMotivo(e.target.value)}
              />
            </label>
          </div>
          <div className="flex items-center gap-2">
            <Button
              disabled={!desde || !hasta || crear.isPending}
              onClick={() => crear.mutate()}
            >
              {crear.isPending ? 'Creando...' : `Crear bloqueo${profesionalId ? '' : ' del local'} (${tipoSeleccionado})`}
            </Button>
            {desde && hasta && new Date(hasta) <= new Date(desde) && (
              <span className="text-sm text-destructive">Hasta debe ser posterior a desde.</span>
            )}
          </div>
          <MensajeDeError error={errorForm} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Bloqueos existentes</CardTitle>
          <CardDescription>
            Los bloqueos del local y los de cada profesional. Borrar libera la
            agenda en esa ventana.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Cargando bloqueos...</p>
          ) : bloqueos && bloqueos.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Alcance</TableHead>
                  <TableHead>Ventana</TableHead>
                  <TableHead>Tipo</TableHead>
                  <TableHead>Motivo</TableHead>
                  <TableHead className="text-right">Acción</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {bloqueos.map((b) => (
                  <TableRow key={b.id}>
                    <TableCell className="font-medium">{nombreDe(b.professional_id)}</TableCell>
                    <TableCell className="whitespace-nowrap">
                      {formatearFechaHora(b.starts_at)} → {formatearFechaHora(b.ends_at)}
                    </TableCell>
                    <TableCell>{ETIQUETAS_KIND_BLOQUEO[b.kind] ?? b.kind}</TableCell>
                    <TableCell>{b.reason ?? '—'}</TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={eliminar.isPending}
                        onClick={() =>
                          window.confirm('¿Borrar este bloqueo?') && eliminar.mutate(b.id)
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
            <p className="text-sm text-muted-foreground">No hay bloqueos.</p>
          )}
          {(eliminar.isError || error !== null) && (
            <MensajeDeError error={mensajeDeError(eliminar.error ?? error)} />
          )}
        </CardContent>
      </Card>
    </div>
  )
}