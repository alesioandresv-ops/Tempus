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
import type { ReservaDePanel } from '@/types'
import {
  DURACIONES,
  ETIQUETAS_ESTADO,
  MensajeDeError,
  desdeDatetimeLocal,
  formatearFechaHora,
  formatearPrecio,
  formatearTelefono,
  mensajeDeError,
} from './comun'

const LIMITE = 50

const ESTADOS = ['confirmed', 'pending_hold', 'cancelled', 'completed', 'no_show'] as const

/**
 * Pestaña Reservas del admin (Fase C).
 *
 * Lista real de `/business/reservas` con filtros (incluida la búsqueda `q` por
 * nombre/teléfono del cliente) y dos acciones que no piden el secure token del
 * cliente: cancelar y reprogramar. La reprogramación manda los tres campos
 * juntos (`starts_at`, `ends_at`, `duration`), igual que el flujo público: el
 * fin se recalcula con la duración elegida.
 */
export function ReservasTab() {
  const queryClient = useQueryClient()

  // --- Filtros ---
  const [desde, setDesde] = useState('')
  const [hasta, setHasta] = useState('')
  const [profesionalId, setProfesionalId] = useState('')
  const [estado, setEstado] = useState('')
  const [q, setQ] = useState('')
  const [qAplicada, setQAplicada] = useState('')
  const [offset, setOffset] = useState(0)

  const { data: profesionales } = useQuery({
    queryKey: ['profesionales'],
    queryFn: api.listarProfesionales,
  })

  const { data: pagina, isLoading } = useQuery({
    queryKey: ['reservas-admin', { desde, hasta, profesionalId, estado, qAplicada, offset }],
    queryFn: () =>
      api.listarReservas({
        desde: desde || undefined,
        hasta: hasta || undefined,
        professional_id: profesionalId || undefined,
        estado: estado || undefined,
        q: qAplicada || undefined,
        limite: LIMITE,
        offset,
      }),
  })

  const exito = () => queryClient.invalidateQueries({ queryKey: ['reservas-admin'] })

  const cancelar = useMutation({
    mutationFn: (pedido: { id: string; motivo?: string }) =>
      api.cancelarReservaAdmin(pedido.id, pedido.motivo),
    onSuccess: exito,
  })

  const [reprogramando, setReprogramando] = useState<ReservaDePanel | null>(null)

  const aplicarFiltros = () => {
    setQAplicada(q.trim())
    setOffset(0)
  }

  const limpiarFiltros = () => {
    setDesde('')
    setHasta('')
    setProfesionalId('')
    setEstado('')
    setQ('')
    setQAplicada('')
    setOffset(0)
  }

  const filas = pagina?.items ?? []
  const total = pagina?.total ?? 0
  const comienzo = total === 0 ? 0 : offset + 1
  const fin = Math.min(offset + LIMITE, total)

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Filtros</CardTitle>
          <CardDescription>
            La búsqueda por nombre o teléfono matchea parcial. Las fechas son
            locales del negocio.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <label className="block space-y-1">
              <span className="text-sm font-medium">Búsqueda</span>
              <Input
                placeholder="Nombre o teléfono del cliente"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && aplicarFiltros()}
              />
            </label>
            <label className="block space-y-1">
              <span className="text-sm font-medium">Desde</span>
              <Input type="date" value={desde} onChange={(e) => setDesde(e.target.value)} />
            </label>
            <label className="block space-y-1">
              <span className="text-sm font-medium">Hasta</span>
              <Input type="date" value={hasta} onChange={(e) => setHasta(e.target.value)} />
            </label>
            <label className="block space-y-1">
              <span className="text-sm font-medium">Profesional</span>
              <select
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={profesionalId}
                onChange={(e) => setProfesionalId(e.target.value)}
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
                onChange={(e) => setEstado(e.target.value)}
              >
                <option value="">Todos</option>
                {ESTADOS.map((e) => (
                  <option key={e} value={e}>
                    {ETIQUETAS_ESTADO[e]}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={aplicarFiltros}>Buscar</Button>
            <Button variant="outline" onClick={limpiarFiltros}>
              Limpiar
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Reservas</CardTitle>
          <CardDescription>
            {total > 0
              ? `${comienzo}–${fin} de ${total} reservas`
              : 'Sin reservas para los filtros elegidos.'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Cargando reservas...</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Fecha</TableHead>
                  <TableHead>Hora</TableHead>
                  <TableHead>Cliente</TableHead>
                  <TableHead>Servicio</TableHead>
                  <TableHead>Profesional</TableHead>
                  <TableHead>Precio</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead className="text-right">Acciones</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filas.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="whitespace-nowrap">
                      {formatearFechaHora(r.starts_at).slice(0, 5)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {formatearFechaHora(r.starts_at).slice(6)}
                    </TableCell>
                    <TableCell>
                      <div className="font-medium">{r.cliente_nombre ?? '—'}</div>
                      {formatearTelefono(r.cliente_telefono) && (
                        <div className="text-xs text-muted-foreground">
                          {formatearTelefono(r.cliente_telefono)}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>{r.servicio_nombre ?? '—'}</TableCell>
                    <TableCell>{r.profesional_nombre ?? '—'}</TableCell>
                    <TableCell className="whitespace-nowrap">
                      {formatearPrecio(r.price_snapshot)}
                    </TableCell>
                    <TableCell>{ETIQUETAS_ESTADO[r.status] ?? r.status}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={
                            r.status === 'cancelled' ||
                            r.status === 'completed' ||
                            r.status === 'no_show'
                          }
                          onClick={() => setReprogramando(r)}
                        >
                          Reprogramar
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          disabled={r.status === 'cancelled'}
                          onClick={() => {
                            const motivo = window.prompt(
                              `Motivo de la cancelación de ${r.cliente_nombre ?? 'la reserva'} (opcional):`
                            )
                            if (motivo === null) return
                            cancelar.mutate({ id: r.id, motivo: motivo || undefined })
                          }}
                        >
                          Cancelar
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {cancelar.isError && <MensajeDeError error={mensajeDeError(cancelar.error)} />}
          <div className="mt-4 flex items-center justify-between">
            <Button
              variant="outline"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - LIMITE))}
            >
              Anterior
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={offset + LIMITE >= total}
              onClick={() => setOffset(offset + LIMITE)}
            >
              Siguiente
            </Button>
          </div>
        </CardContent>
      </Card>

      {reprogramando && (
        <ReprogramarModal
          reserva={reprogramando}
          onCerrar={() => setReprogramando(null)}
          onExito={exito}
        />
      )}
    </div>
  )
}

/** Modal de reprogramación: nuevo inicio + duración. El fin se recalcula. */
function ReprogramarModal({
  reserva,
  onCerrar,
  onExito,
}: {
  reserva: ReservaDePanel
  onCerrar: () => void
  onExito: () => void
}) {
  const actual = new Date(reserva.starts_at)
  const actualLocal = Number.isNaN(actual.getTime())
    ? ''
    : new Date(actual.getTime() - actual.getTimezoneOffset() * 60000).toISOString().slice(0, 16)

  const [inicioLocal, setInicioLocal] = useState(actualLocal)
  const [duracion, setDuracion] = useState(reserva.duration_minutes)
  const [error, setError] = useState<string | null>(null)

  const reprogramar = useMutation({
    mutationFn: () => {
      const inicio = desdeDatetimeLocal(inicioLocal)
      const fin = new Date(new Date(inicio).getTime() + duracion * 60000).toISOString()
      return api.reprogramarReservaAdmin(reserva.id, {
        new_starts_at: inicio,
        new_ends_at: fin,
        new_duration_minutes: duracion,
      })
    },
    onSuccess: () => {
      onExito()
      onCerrar()
    },
    onError: (e) => setError(mensajeDeError(e)),
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md space-y-4 rounded-lg border bg-background p-6 shadow-lg">
        <h2 className="text-lg font-semibold">Reprogramar reserva</h2>
        <p className="text-sm text-muted-foreground">
          {reserva.cliente_nombre ?? 'Cliente'} · {reserva.servicio_nombre ?? 'Servicio'} ·{' '}
          hoy {formatearFechaHora(reserva.starts_at)}
        </p>
        <label className="block space-y-1">
          <span className="text-sm font-medium">Nuevo inicio</span>
          <Input
            type="datetime-local"
            value={inicioLocal}
            onChange={(e) => setInicioLocal(e.target.value)}
          />
        </label>
        <label className="block space-y-1">
          <span className="text-sm font-medium">Duración</span>
          <select
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            value={duracion}
            onChange={(e) => setDuracion(Number(e.target.value))}
          >
            {DURACIONES.map((d) => (
              <option key={d} value={d}>
                {d} minutos
              </option>
            ))}
          </select>
        </label>
        <p className="text-xs text-muted-foreground">
          Termina a las{' '}
          {inicioLocal
            ? formatearFechaHora(
                new Date(new Date(desdeDatetimeLocal(inicioLocal)).getTime() + duracion * 60000).toISOString()
              ).slice(6)
            : '—'}
        </p>
        <MensajeDeError error={error} />
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onCerrar}>
            Cancelar
          </Button>
          <Button
            disabled={!inicioLocal || reprogramar.isPending}
            onClick={() => reprogramar.mutate()}
          >
            {reprogramar.isPending ? 'Reprogramando...' : 'Reprogramar'}
          </Button>
        </div>
      </div>
    </div>
  )
}