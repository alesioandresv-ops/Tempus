import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { api } from '@/services/api'
import type { BloqueoDePanel, ReservaDePanel } from '@/types'
import { formatearTelefono } from './admin/comun'

/**
 * Panel del profesional (Fase B).
 *
 * Cuatro secciones sobre los endpoints que un login `professional`/`staff`
 * puede usar: la agenda propia de hoy, la de los proximos 7 dias, el historial
 * y los bloqueos que le tocan. El backend resuelve el `professional_id` desde
 * el token: un profesional solo ve lo suyo, y los parametros de la URL no
 * pueden ampliarlo (ver `mi_agenda` en el router de negocio).
 *
 * **Las fechas se tratan en la zona del navegador**, igual que `BusinessPage` y
 * `ManageBookingPage`: la API devuelve `starts_at` en UTC y las paginas
 * existentes lo formatean con `toLocaleTimeString` sin zona explicita. Un
 * profesional que administra un negocio en otro huso veria la hora de su
 * navegador y no la del negocio; se acepta a proposito por consistencia con el
 * resto de la app, que todavia no lleva la zona del negocio al cliente.
 */

// --------------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------------- //

/** "YYYY-MM-DD" de la fecha de hoy en la zona del navegador. */
function hoyLocal(): string {
  const ahora = new Date()
  const mes = String(ahora.getMonth() + 1).padStart(2, '0')
  const dia = String(ahora.getDate()).padStart(2, '0')
  return `${ahora.getFullYear()}-${mes}-${dia}`
}

/** Suma dias a una fecha "YYYY-MM-DD" y devuelve otra "YYYY-MM-DD". */
function sumarDias(fechaIso: string, dias: number): string {
  const [anio, mes, dia] = fechaIso.split('-').map(Number)
  const fecha = new Date(anio, mes - 1, dia + dias)
  const mesOk = String(fecha.getMonth() + 1).padStart(2, '0')
  const diaOk = String(fecha.getDate()).padStart(2, '0')
  return `${fecha.getFullYear()}-${mesOk}-${diaOk}`
}

function aHoraLocal(iso: string): string {
  return new Date(iso).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' })
}

function aFechaLocal(iso: string): string {
  return new Date(iso).toLocaleDateString('es-AR', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  })
}

/** Los estados que puede tener una reserva (BookingStatus), en es-AR. */
const ETIQUETA_ESTADO: Record<string, string> = {
  pending_hold: 'Reservado',
  confirmed: 'Confirmado',
  cancelled: 'Cancelado',
  completed: 'Completado',
  no_show: 'No asistio',
}

const COLOR_ESTADO: Record<string, string> = {
  pending_hold: 'bg-amber-50 text-amber-700 border-amber-200',
  confirmed: 'bg-green-50 text-green-700 border-green-200',
  cancelled: 'bg-gray-100 text-gray-500 border-gray-200',
  completed: 'bg-blue-50 text-blue-700 border-blue-200',
  no_show: 'bg-red-50 text-red-600 border-red-200',
}

const ETIQUETA_BLOQUEO: Record<string, string> = {
  unpaid: 'No remunerado',
  private: 'Privado',
  blocked: 'Bloqueado',
}

function estadoDe(reserva: ReservaDePanel): string {
  return ETIQUETA_ESTADO[reserva.status] ?? reserva.status
}

/** Una reserva con su franja horaria, su estado y el nombre del cliente. */
function FilaDeReserva({ reserva }: { reserva: ReservaDePanel }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-gray-100 py-3 last:border-0">
      <div className="w-32 shrink-0">
        <p className="text-sm font-semibold text-gray-900">
          {aHoraLocal(reserva.starts_at)} - {aHoraLocal(reserva.ends_at)}
        </p>
        <p className="text-xs text-gray-400">{aFechaLocal(reserva.starts_at)}</p>
      </div>
      <div className="min-w-40 flex-1">
        <p className="text-sm text-gray-900">{reserva.servicio_nombre ?? 'Servicio'}</p>
        <p className="text-xs text-gray-400">
          <span className="text-gray-600">{reserva.cliente_nombre ?? 'Cliente sin nombre'}</span>
          {formatearTelefono(reserva.cliente_telefono) ? ` · ${formatearTelefono(reserva.cliente_telefono)}` : ''}
          {reserva.duration_minutes
            ? ` · ${reserva.duration_minutes} min · ${reserva.price_snapshot} ${reserva.currency}`
            : ''}
        </p>
      </div>
      <span
        className={`rounded-full border px-2 py-0.5 text-xs font-medium ${COLOR_ESTADO[reserva.status] ?? 'bg-gray-100 text-gray-600 border-gray-200'}`}
      >
        {estadoDe(reserva)}
      </span>
    </div>
  )
}

/** Lista vacia con el texto de la seccion. */
function SinReservas({ texto }: { texto: string }) {
  return <p className="py-6 text-sm text-gray-500">{texto}</p>
}

/** Los bloqueos que le tocan al profesional: los del negocio entero y los suyos. */
function FiltrarBloqueosPropios(
  bloqueos: BloqueoDePanel[],
  miProfesionalId: string | null
): BloqueoDePanel[] {
  return bloqueos.filter((b) => b.professional_id === null || b.professional_id === miProfesionalId)
}

function FilaDeBloqueo({ bloqueo }: { bloqueo: BloqueoDePanel }) {
  const esGeneral = bloqueo.professional_id === null
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-gray-100 py-3 last:border-0">
      <div className="w-44 shrink-0">
        <p className="text-sm font-semibold text-gray-900">
          {aFechaLocal(bloqueo.starts_at)}
        </p>
        <p className="text-xs text-gray-400">
          {aHoraLocal(bloqueo.starts_at)} - {aHoraLocal(bloqueo.ends_at)}
        </p>
      </div>
      <div className="min-w-40 flex-1">
        <p className="text-sm text-gray-900">
          {ETIQUETA_BLOQUEO[bloqueo.kind] ?? bloqueo.kind}
        </p>
        <p className="text-xs text-gray-400">
          {esGeneral ? 'Todo el negocio' : 'Solo vos'}
          {bloqueo.reason ? ` · ${bloqueo.reason}` : ''}
        </p>
      </div>
      {esGeneral && (
        <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs font-medium text-gray-600">
          General
        </span>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Página
// --------------------------------------------------------------------------- //

type Pestaña = 'hoy' | 'semana' | 'historial' | 'bloqueos'

export default function ProfessionalPage() {
  const [pestana, setPestana] = useState<Pestaña>('hoy')
  const hoy = hoyLocal()

  // Las cuatro queries viven juntas a proposito: los bloqueos propios se
  // filtran con el `professional_id` que sale de la agenda, asi que la agenda
  // tiene que estar cargada aunque la persona este mirando los bloqueos.
  const hoyQuery = useQuery({
    queryKey: ['mi-agenda', 'hoy', hoy],
    queryFn: () => api.getMiAgenda(hoy, hoy),
  })
  const semanaQuery = useQuery({
    queryKey: ['mi-agenda', 'semana', hoy],
    queryFn: () => api.getMiAgenda(hoy, sumarDias(hoy, 6)),
  })
  const historialQuery = useQuery({
    queryKey: ['mi-agenda', 'historial', hoy],
    queryFn: () => api.getMiAgenda('2020-01-01', sumarDias(hoy, -1)),
  })
  const bloqueosQuery = useQuery({
    queryKey: ['bloqueos'],
    queryFn: () => api.getBloqueos(),
  })

  const carga = hoyQuery.isLoading || semanaQuery.isLoading || historialQuery.isLoading

  /*
   * El `professional_id` propio no lo devuelve ningun endpoint directamente; se
   * deduce de la propia agenda, que siempre trae el id en cada fila. Un
   * profesional con cero turnos en la historia no tiene de donde sacarlo, y en
   * ese caso se le muestran los bloqueos generales nomás: los personales no se
   * pueden distinguir de los de otro profesional sin saber cual es el propio.
   */
  const miProfesionalId = useMemo<string | null>(() => {
    const todas = [...hoyQuery.data ?? [], ...semanaQuery.data ?? [], ...historialQuery.data ?? []]
    const primero = todas.find((r) => r.professional_id)
    return primero ? primero.professional_id : null
  }, [hoyQuery.data, semanaQuery.data, historialQuery.data])

  const bloqueosPropios = useMemo(
    () => FiltrarBloqueosPropios(bloqueosQuery.data ?? [], miProfesionalId),
    [bloqueosQuery.data, miProfesionalId]
  )

  const pestanas: Array<{ id: Pestaña; etiqueta: string; cantidad: number }> = [
    { id: 'hoy', etiqueta: 'Hoy', cantidad: hoyQuery.data?.length ?? 0 },
    { id: 'semana', etiqueta: 'Proximos 7 dias', cantidad: semanaQuery.data?.length ?? 0 },
    { id: 'historial', etiqueta: 'Historial', cantidad: historialQuery.data?.length ?? 0 },
    { id: 'bloqueos', etiqueta: 'Bloqueos', cantidad: bloqueosPropios.length },
  ]

  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-4xl mx-auto">
        <h1 className="text-2xl font-bold mb-1">Panel del Profesional</h1>
        <p className="text-sm text-gray-500 mb-6">Tu agenda y tus bloqueos</p>

        <div className="flex flex-wrap gap-2 mb-6" role="tablist">
          {pestanas.map((p) => (
            <Button
              key={p.id}
              variant={pestana === p.id ? 'default' : 'outline'}
              onClick={() => setPestana(p.id)}
            >
              {p.etiqueta}
              <span className="ml-2 rounded-full bg-black/10 px-2 text-xs">{p.cantidad}</span>
            </Button>
          ))}
        </div>

        {carga && (
          <Card>
            <CardContent>
              <p className="py-6 text-sm text-gray-500">Cargando tu agenda...</p>
            </CardContent>
          </Card>
        )}

        {!carga && pestana === 'hoy' && (
          <Card>
            <CardHeader>
              <CardTitle>Turnos de hoy</CardTitle>
              <CardDescription>Lo que te toca atender hoy</CardDescription>
            </CardHeader>
            <CardContent>
              {(hoyQuery.data?.length ?? 0) === 0 ? (
                <SinReservas texto="No tenes turnos para hoy." />
              ) : (
                hoyQuery.data!.map((r) => <FilaDeReserva key={r.id} reserva={r} />)
              )}
            </CardContent>
          </Card>
        )}

        {!carga && pestana === 'semana' && (
          <Card>
            <CardHeader>
              <CardTitle>Proximos 7 dias</CardTitle>
              <CardDescription>Del dia de hoy a una semana</CardDescription>
            </CardHeader>
            <CardContent>
              {(semanaQuery.data?.length ?? 0) === 0 ? (
                <SinReservas texto="No tenes turnos en los proximos 7 dias." />
              ) : (
                semanaQuery.data!.map((r) => <FilaDeReserva key={r.id} reserva={r} />)
              )}
            </CardContent>
          </Card>
        )}

        {!carga && pestana === 'historial' && (
          <Card>
            <CardHeader>
              <CardTitle>Historial</CardTitle>
              <CardDescription>Tus turnos hasta ayer, como terminaron</CardDescription>
            </CardHeader>
            <CardContent>
              {(historialQuery.data?.length ?? 0) === 0 ? (
                <SinReservas texto="Todavia no tenes turnos en el historial." />
              ) : (
                historialQuery.data!.map((r) => <FilaDeReserva key={r.id} reserva={r} />)
              )}
            </CardContent>
          </Card>
        )}

        {!carga && pestana === 'bloqueos' && (
          <Card>
            <CardHeader>
              <CardTitle>Bloqueos que te tocan</CardTitle>
              <CardDescription>
                Los dias en que no se te puede reservar: cierres del negocio y bloqueos
                personales
              </CardDescription>
            </CardHeader>
            <CardContent>
              {bloqueosQuery.isLoading && (
                <p className="py-6 text-sm text-gray-500">Cargando bloqueos...</p>
              )}
              {!bloqueosQuery.isLoading && bloqueosPropios.length === 0 && (
                <p className="py-6 text-sm text-gray-500">No hay bloqueos que te afecten.</p>
              )}
              {!bloqueosQuery.isLoading &&
                bloqueosPropios.length > 0 &&
                bloqueosPropios.map((b) => <FilaDeBloqueo key={b.id} bloqueo={b} />)}
              {miProfesionalId === null && !bloqueosQuery.isLoading && (
                <p className="mt-4 text-xs text-gray-400">
                  Sin turnos registrados todavia, los bloqueos personales no se distinguen de
                  los de otro profesional; se muestran los de todo el negocio.
                </p>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}