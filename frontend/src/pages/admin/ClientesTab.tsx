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
import type { ClienteDePanel, ReservaDePanel } from '@/types'
import {
  ETIQUETAS_ESTADO,
  formatearFechaHora,
  formatearPrecio,
  formatearTelefono,
  mensajeDeError,
  MensajeDeError,
} from './comun'

const LIMITE = 50

/**
 * Pestaña Clientes del admin (Fase D).
 *
 * Lista real de `/business/clientes` con la búsqueda por nombre o teléfono, los
 * totales agregados sobre `bookings` (reservas, gasto de confirmadas/completadas,
 * última visita, profesional más frecuente) y, al hacer click en una fila, el
 * historial completo del cliente desde `/business/clientes/{id}/reservas`:
 * fecha, profesional, servicio, precio y estado.
 */
export function ClientesTab() {
  const [busqueda, setBusqueda] = useState('')
  const [busquedaAplicada, setBusquedaAplicada] = useState('')
  const [detalle, setDetalle] = useState<ClienteDePanel | null>(null)

  const { data: clientes, isLoading, isError, error } = useQuery({
    queryKey: ['clientes', busquedaAplicada],
    queryFn: () =>
      api.listarClientes({
        busqueda: busquedaAplicada || undefined,
        limite: LIMITE,
      }),
  })

  const aplicar = () => {
    setBusquedaAplicada(busqueda.trim())
    setDetalle(null)
  }

  const limpiar = () => {
    setBusqueda('')
    setBusquedaAplicada('')
    setDetalle(null)
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Clientes</CardTitle>
          <CardDescription>
            El historial se arma desde las reservas: cuántas veces vino, cuánto
            gastó en turnos confirmados o completados y con quién se atiende
            más. Click en una fila para ver el detalle completo.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block space-y-1">
              <span className="text-sm font-medium">Búsqueda</span>
              <Input
                placeholder="Nombre o teléfono del cliente"
                value={busqueda}
                onChange={(e) => setBusqueda(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && aplicar()}
              />
            </label>
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={aplicar}>Buscar</Button>
            <Button variant="outline" onClick={limpiar}>
              Limpiar
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Historial</CardTitle>
          <CardDescription>
            {clientes && clientes.length === 0
              ? 'Sin clientes para la búsqueda elegida.'
              : `${clientes?.length ?? 0} clientes${busquedaAplicada ? ` que matchean "${busquedaAplicada}"` : ''}`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Cargando clientes...</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Cliente</TableHead>
                  <TableHead>Reservas</TableHead>
                  <TableHead>Total gastado</TableHead>
                  <TableHead>Última visita</TableHead>
                  <TableHead>Profesional frecuente</TableHead>
                  <TableHead className="text-right">Acciones</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(clientes ?? []).map((c) => (
                  <TableRow key={c.id}>
                    <TableCell>
                      <div className="font-medium">
                        {c.first_name} {c.last_name ?? ''}
                      </div>
                      {formatearTelefono(c.phone_e164) && (
                        <div className="text-xs text-muted-foreground">
                          {formatearTelefono(c.phone_e164)}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>{c.total_reservas}</TableCell>
                    <TableCell className="whitespace-nowrap">
                      {formatearPrecio(c.total_gastado)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {c.ultima_reserva ? formatearFechaHora(c.ultima_reserva) : '—'}
                    </TableCell>
                    <TableCell>{c.profesional_mas_frecuente ?? '—'}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="outline" size="sm" onClick={() => setDetalle(c)}>
                        Ver historial
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {isError && <MensajeDeError error={mensajeDeError(error)} />}
        </CardContent>
      </Card>

      {detalle && (
        <HistorialModal cliente={detalle} onCerrar={() => setDetalle(null)} />
      )}
    </div>
  )
}

/**
 * Modal con el historial completo del cliente.
 *
 * Reutiliza la misma fila del listado de reservas (`ReservaDePanel`), que ya
 * trae servicio, profesional, precio y estado desnormalizados.
 */
function HistorialModal({
  cliente,
  onCerrar,
}: {
  cliente: ClienteDePanel
  onCerrar: () => void
}) {
  const { data: pagina, isLoading, isError, error } = useQuery({
    queryKey: ['historial-cliente', cliente.id],
    queryFn: () => api.historialCliente(cliente.id, { limite: LIMITE }),
  })

  const reservas = pagina?.items ?? []

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onCerrar}
    >
      <div
        className="max-h-[85vh] w-full max-w-2xl space-y-4 overflow-y-auto rounded-lg border bg-background p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">
              {cliente.first_name} {cliente.last_name ?? ''}
            </h2>
            <p className="text-sm text-muted-foreground">
              {formatearTelefono(cliente.phone_e164) || 'Sin teléfono'} ·{' '}
              {cliente.total_reservas} reservas ·{' '}
              {formatearPrecio(cliente.total_gastado)} gastados
            </p>
            <p className="text-sm text-muted-foreground">
              Última visita:{' '}
              {cliente.ultima_reserva
                ? formatearFechaHora(cliente.ultima_reserva)
                : '—'}
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={onCerrar}>
            Cerrar
          </Button>
        </div>

        {isLoading ? (
          <p className="text-sm text-muted-foreground">Cargando historial...</p>
        ) : reservas.length === 0 ? (
          <p className="text-sm text-muted-foreground">Sin reservas registradas.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Fecha</TableHead>
                <TableHead>Profesional</TableHead>
                <TableHead>Servicio</TableHead>
                <TableHead>Precio</TableHead>
                <TableHead>Estado</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {reservas.map((r: ReservaDePanel) => (
                <TableRow key={r.id}>
                  <TableCell className="whitespace-nowrap">
                    {formatearFechaHora(r.starts_at)}
                  </TableCell>
                  <TableCell>{r.profesional_nombre ?? '—'}</TableCell>
                  <TableCell>{r.servicio_nombre ?? '—'}</TableCell>
                  <TableCell className="whitespace-nowrap">
                    {formatearPrecio(r.price_snapshot)}
                  </TableCell>
                  <TableCell>{ETIQUETAS_ESTADO[r.status] ?? r.status}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {isError && <MensajeDeError error={mensajeDeError(error)} />}
      </div>
    </div>
  )
}