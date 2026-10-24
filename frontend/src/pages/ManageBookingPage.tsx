import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '@/services/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { MarcaTempus } from '@/components/MarcaTempus'
import { cn } from '@/lib/utils'
import type { Slot } from '@/types'

/**
 * Pantalla de gestion de una reserva por token (`/r/{token}`).
 *
 * Es la unica pagina autenticada por el token que el cliente recibio al reservar:
 * no hay cuenta ni password. Todo lo que se puede hacer aca --ver, cancelar,
 * reprogramar-- lo permite el backend por ese token.
 *
 * **La reprogramacion elige de la disponibilidad real.** Antes el cliente escribia
 * una fecha y una hora a mano y el frontend las mandaba como si fueran UTC: un
 * turno de las 15 en Buenos Aires viajaba como 15:00Z, tres horas corrido, y el
 * backend lo rechazaba o lo guardaba mal. Ahora se pide `availability` para el dia
 * elegido y se ofrecen los slots que el negocio realmente tiene; el slot ya viene
 * en UTC, asi que no hay conversion que hacer.
 */

function mensajeDeError(error: unknown, porDefecto: string): string {
  if (error instanceof ApiError) return error.message || porDefecto
  if (error instanceof Error) return error.message
  return porDefecto
}

/** Estados del backend traducidos a lo que el cliente lee. */
const ETIQUETAS_ESTADO: Record<string, string> = {
  pending_hold: 'Pendiente',
  pending_payment: 'Pendiente de pago',
  confirmed: 'Confirmada',
  cancelled: 'Cancelada',
  completed: 'Completada',
  no_show: 'No asistio',
}

/** Colores de cada estado sobre el fondo oscuro. */
const COLORES_ESTADO: Record<string, string> = {
  pending_hold: 'border-amber-500/30 bg-amber-500/15 text-amber-300',
  pending_payment: 'border-amber-500/30 bg-amber-500/15 text-amber-300',
  confirmed: 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300',
  cancelled: 'border-slate-500/30 bg-slate-500/15 text-slate-300',
  completed: 'border-slate-500/30 bg-slate-500/15 text-slate-300',
  no_show: 'border-rose-500/30 bg-rose-500/15 text-rose-300',
}

/** Un turno cancelado, completado o ausente ya no se reprograma. */
const ESTADOS_CERRADOS = new Set(['cancelled', 'completed', 'no_show'])

/** `YYYY-MM-DD` de `date` en la zona `timeZone`. */
function fechaEnZona(date: Date, timeZone: string | undefined): string {
  try {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: timeZone || undefined,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(date)
  } catch {
    // Zona invalida: mejor la del navegador que romper la pantalla.
    return date.toISOString().slice(0, 10)
  }
}

export default function ManageBookingPage() {
  const { token } = useParams<{ token: string }>()
  const [isCancelling, setIsCancelling] = useState(false)
  const [isRescheduling, setIsRescheduling] = useState(false)
  const [confirmandoCancelacion, setConfirmandoCancelacion] = useState(false)
  const [fecha, setFecha] = useState('')
  const [slotElegido, setSlotElegido] = useState<Slot | null>(null)
  const [mensaje, setMensaje] = useState<string | null>(null)
  const [errorAccion, setErrorAccion] = useState<string | null>(null)

  const {
    data: booking,
    isLoading,
    refetch,
  } = useQuery({
    queryKey: ['booking', token],
    queryFn: () => api.getBooking(token!),
    enabled: !!token,
  })

  // El dia por defecto de la reprogramacion es el del negocio, no el del
  // navegador: es el que entiende el endpoint de disponibilidad.
  useEffect(() => {
    if (booking && fecha === '') {
      setFecha(booking.local_date ?? fechaEnZona(new Date(), booking.business_timezone ?? undefined))
    }
  }, [booking, fecha])

  const puedeGestionarse = booking !== undefined && !ESTADOS_CERRADOS.has(booking.status)

  const {
    data: disponibilidad,
    isLoading: cargandoSlots,
    error: errorSlots,
  } = useQuery({
    queryKey: ['disponibilidad-gestion', booking?.business_slug, booking?.service_id, fecha, booking?.professional_id],
    queryFn: () =>
      api.getAvailability(
        booking!.business_slug as string,
        booking!.service_id as string,
        fecha,
        booking?.professional_id
      ),
    enabled: !!booking?.business_slug && !!booking?.service_id && !!fecha && puedeGestionarse,
    retry: false,
  })

  const slots = disponibilidad?.slots ?? []

  const handleCancel = async () => {
    setIsCancelling(true)
    setErrorAccion(null)
    try {
      await api.cancelBooking(token!)
      setConfirmandoCancelacion(false)
      setMensaje('Reserva cancelada.')
      await refetch()
    } catch (error) {
      setErrorAccion(mensajeDeError(error, 'No se pudo cancelar la reserva.'))
    } finally {
      setIsCancelling(false)
    }
  }

  const handleReschedule = async () => {
    if (!slotElegido || !booking) return
    setIsRescheduling(true)
    setErrorAccion(null)
    try {
      // La duracion sale de la reserva actual: el servicio no cambia, solo el
      // horario. `ends_at` del slot ya contempla la duracion real del servicio.
      const duracion = Math.max(
        1,
        Math.round(
          (new Date(slotElegido.ends_at).getTime() - new Date(slotElegido.starts_at).getTime()) / 60000
        )
      )
      await api.rescheduleBooking(token!, slotElegido.starts_at, slotElegido.ends_at, duracion)
      setSlotElegido(null)
      setMensaje('Reserva reprogramada.')
      await refetch()
    } catch (error) {
      setErrorAccion(
        mensajeDeError(error, 'No se pudo reprogramar. Elegí otro horario e intentá de nuevo.')
      )
    } finally {
      setIsRescheduling(false)
    }
  }

  if (isLoading) {
    return (
      <div className="relative flex min-h-screen items-center justify-center bg-[#070B1A] font-inter text-slate-100">
        <p className="text-slate-400" role="status">Cargando...</p>
      </div>
    )
  }

  if (!booking) {
    return (
      <div className="relative flex min-h-screen items-center justify-center bg-[#070B1A] px-6 font-inter text-slate-100">
        <div className="max-w-md rounded-2xl border border-white/10 bg-[#0B1224]/90 p-8 text-center backdrop-blur-xl">
          <h1 className="font-display text-2xl font-semibold tracking-tight text-white">
            No encontramos esta reserva
          </h1>
          <p className="mt-2 text-sm text-slate-400">
            Revisá que el enlace esté completo.
          </p>
        </div>
      </div>
    )
  }

  const estadoNormalizado = booking.status ?? 'pending_hold'
  const etiquetaEstado = ETIQUETAS_ESTADO[estadoNormalizado] ?? booking.status
  const claseEstado = COLORES_ESTADO[estadoNormalizado] ?? COLORES_ESTADO.confirmed

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[#070B1A] font-inter text-slate-100">
      {/* Fondo: aurora */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -left-32 -top-40 h-[520px] w-[520px] rounded-full bg-violet-600/20 blur-[120px] animate-flotar-lento" />
        <div className="absolute -top-16 right-0 h-[460px] w-[460px] rounded-full bg-cyan-500/15 blur-[130px] animate-flotar-lento" />
        <div className="absolute left-1/3 top-64 h-[420px] w-[420px] rounded-full bg-fuchsia-600/15 blur-[140px]" />
      </div>

      <div className="relative mx-auto w-full max-w-2xl px-6 pb-16 pt-8 sm:px-8 lg:pt-12">
        <div className="animate-entra-arriba rounded-2xl border border-white/10 bg-[#0B1224]/90 p-6 shadow-[0_0_60px_-20px_rgba(139,92,246,0.5)] backdrop-blur-xl sm:p-8">
          {/* Cabecera */}
          <div className="mb-6 flex items-start justify-between gap-4">
            <div className="flex items-center gap-3">
              <MarcaTempus size={40} soloSimbolo />
              <div>
                <h1 className="font-display text-lg font-semibold tracking-tight text-white">
                  {booking.business_name ? `Tu reserva en ${booking.business_name}` : 'Tu reserva'}
                </h1>
                <p className="mt-0.5 text-xs text-slate-500">Gestioná tu turno desde acá</p>
              </div>
            </div>
            <span
              className={cn(
                'shrink-0 rounded-full border px-3 py-1 text-xs font-semibold',
                claseEstado
              )}
            >
              {etiquetaEstado}
            </span>
          </div>

          {mensaje && (
            <p
              className="mb-5 rounded-lg border border-emerald-400/20 bg-emerald-500/10 p-3 text-sm text-emerald-200"
              role="status"
            >
              {mensaje}
            </p>
          )}
          {errorAccion && (
            <p
              className="mb-5 rounded-lg border border-rose-400/20 bg-rose-500/10 p-3 text-sm text-rose-200"
              role="alert"
            >
              {errorAccion}
            </p>
          )}

          <div className="space-y-6">
            {/* Resumen de la reserva */}
            <dl className="space-y-3 rounded-xl border border-white/10 bg-white/[0.03] p-5">
              <div className="flex items-center justify-between gap-4">
                <dt className="text-sm text-slate-400">Fecha y hora</dt>
                <dd className="text-right font-medium text-slate-100">
                  {new Date(booking.starts_at).toLocaleString('es-AR', {
                    dateStyle: 'full',
                    timeStyle: 'short',
                  })}
                </dd>
              </div>
              {booking.service_name && (
                <div className="flex items-center justify-between gap-4">
                  <dt className="text-sm text-slate-400">Servicio</dt>
                  <dd className="text-right font-medium text-slate-100">{booking.service_name}</dd>
                </div>
              )}
              {booking.professional_name && (
                <div className="flex items-center justify-between gap-4">
                  <dt className="text-sm text-slate-400">Profesional</dt>
                  <dd className="text-right font-medium text-slate-100">{booking.professional_name}</dd>
                </div>
              )}
              {booking.price && (
                <div className="flex items-center justify-between gap-4">
                  <dt className="text-sm text-slate-400">Precio</dt>
                  <dd className="text-right font-medium text-slate-100">
                    {booking.price} {booking.currency}
                  </dd>
                </div>
              )}
            </dl>

            {puedeGestionarse && (
              <>
                <div className="border-t border-white/10 pt-6">
                  <h2 className="font-display text-lg font-semibold tracking-tight text-white">
                    Reprogramar
                  </h2>
                  <div className="mt-4 space-y-4">
                    <div className="space-y-2">
                      <label htmlFor="fecha" className="block text-sm text-slate-400">
                        Nuevo día
                      </label>
                      <Input
                        id="fecha"
                        type="date"
                        value={fecha}
                        min={fechaEnZona(new Date(), booking.business_timezone ?? undefined)}
                        onChange={(e) => {
                          setFecha(e.target.value)
                          setSlotElegido(null)
                        }}
                        style={{ colorScheme: 'dark' }}
                        className="border-white/10 bg-white/5 text-white focus-visible:ring-violet-400"
                      />
                    </div>

                    {!booking.service_id ? (
                      <p className="text-sm text-slate-400">
                        No podemos ofrecer horarios para esta reserva. Contactá al negocio.
                      </p>
                    ) : cargandoSlots ? (
                      <p className="text-sm text-slate-400" role="status">
                        Buscando horarios...
                      </p>
                    ) : errorSlots ? (
                      <p className="text-sm text-rose-300" role="alert">
                        {mensajeDeError(errorSlots, 'No pudimos cargar los horarios de ese día.')}
                      </p>
                    ) : slots.length === 0 ? (
                      <p className="text-sm text-slate-400">
                        No hay horarios libres ese día. Probá con otro.
                      </p>
                    ) : (
                      <div className="grid grid-cols-3 gap-2">
                        {slots.map((slot) => (
                          <button
                            key={slot.starts_at}
                            type="button"
                            onClick={() => setSlotElegido(slot)}
                            className={cn(
                              'rounded-xl border px-2 py-3 text-sm font-medium tabular-nums transition-all duration-300',
                              slotElegido?.starts_at === slot.starts_at
                                ? 'border-transparent bg-gradient-to-br from-violet-600 to-fuchsia-600 text-white shadow-lg shadow-violet-500/30'
                                : 'border-white/10 bg-white/[0.03] text-slate-200 hover:border-violet-400/40 hover:bg-white/[0.06]'
                            )}
                          >
                            {new Date(slot.starts_at).toLocaleTimeString('es-AR', {
                              hour: '2-digit',
                              minute: '2-digit',
                            })}
                          </button>
                        ))}
                      </div>
                    )}

                    <Button
                      onClick={handleReschedule}
                      disabled={isRescheduling || !slotElegido}
                      className="rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-600 py-2.5 font-semibold text-white shadow-lg shadow-violet-500/25 transition-all hover:shadow-xl hover:shadow-violet-500/40 hover:-translate-y-0.5 disabled:opacity-50"
                    >
                      {isRescheduling ? 'Reprogramando...' : 'Confirmar nuevo horario'}
                    </Button>
                  </div>
                </div>

                <div className="border-t border-white/10 pt-6">
                  <h2 className="font-display text-lg font-semibold tracking-tight text-white">
                    Cancelar reserva
                  </h2>
                  {confirmandoCancelacion ? (
                    <div className="mt-4 space-y-3">
                      <p className="text-sm text-slate-400">
                        ¿Cancelar la reserva? Esta acción no se puede deshacer.
                      </p>
                      <div className="flex gap-2">
                        <Button
                          variant="destructive"
                          onClick={handleCancel}
                          disabled={isCancelling}
                          className="rounded-xl"
                        >
                          {isCancelling ? 'Cancelando...' : 'Sí, cancelar'}
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => setConfirmandoCancelacion(false)}
                          disabled={isCancelling}
                          className="rounded-xl border-white/10 bg-transparent text-slate-300 hover:bg-white/5 hover:text-white"
                        >
                          Volver
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <Button
                      variant="outline"
                      className="mt-4 rounded-xl border-rose-500/30 bg-transparent text-rose-300 hover:bg-rose-500/10 hover:text-rose-200"
                      onClick={() => setConfirmandoCancelacion(true)}
                    >
                      Cancelar reserva
                    </Button>
                  )}
                </div>
              </>
            )}

            {booking.business_phone && (
              <p className="border-t border-white/10 pt-4 text-sm text-slate-400">
                ¿Necesitás hablar con el negocio? {booking.business_phone}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
