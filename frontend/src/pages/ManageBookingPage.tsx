import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '@/services/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
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
    return <div className="flex items-center justify-center min-h-screen">Cargando...</div>
  }

  if (!booking) {
    return (
      <div className="flex items-center justify-center min-h-screen px-4 text-center">
        No encontramos esta reserva. Revisá que el enlace esté completo.
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-2xl mx-auto">
        <Card>
          <CardHeader>
            <CardTitle className="text-2xl">
              {booking.business_name ? `Tu reserva en ${booking.business_name}` : 'Tu reserva'}
            </CardTitle>
            <CardDescription>
              Estado:{' '}
              <span className="font-medium">
                {ETIQUETAS_ESTADO[booking.status] ?? booking.status}
              </span>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {mensaje && (
              <p
                className="rounded-md border border-green-500/50 bg-green-50 px-3 py-2 text-sm text-green-700"
                role="status"
              >
                {mensaje}
              </p>
            )}
            {errorAccion && (
              <p
                className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                role="alert"
              >
                {errorAccion}
              </p>
            )}

            <dl className="space-y-3">
              <div className="flex items-center justify-between gap-4">
                <dt className="text-sm text-gray-500">Fecha y hora</dt>
                <dd className="text-right font-medium">
                  {new Date(booking.starts_at).toLocaleString('es-AR', {
                    dateStyle: 'full',
                    timeStyle: 'short',
                  })}
                </dd>
              </div>
              {booking.service_name && (
                <div className="flex items-center justify-between gap-4">
                  <dt className="text-sm text-gray-500">Servicio</dt>
                  <dd className="text-right font-medium">{booking.service_name}</dd>
                </div>
              )}
              {booking.professional_name && (
                <div className="flex items-center justify-between gap-4">
                  <dt className="text-sm text-gray-500">Profesional</dt>
                  <dd className="text-right font-medium">{booking.professional_name}</dd>
                </div>
              )}
              {booking.price && (
                <div className="flex items-center justify-between gap-4">
                  <dt className="text-sm text-gray-500">Precio</dt>
                  <dd className="text-right font-medium">
                    {booking.price} {booking.currency}
                  </dd>
                </div>
              )}
            </dl>

            {puedeGestionarse && (
              <>
                <div className="border-t pt-6">
                  <h3 className="text-lg font-semibold mb-4">Reprogramar</h3>
                  <div className="space-y-4">
                    <label className="block space-y-1">
                      <span className="text-sm text-gray-500">Nuevo día</span>
                      <Input
                        type="date"
                        value={fecha}
                        min={fechaEnZona(new Date(), booking.business_timezone ?? undefined)}
                        onChange={(e) => {
                          setFecha(e.target.value)
                          setSlotElegido(null)
                        }}
                      />
                    </label>

                    {!booking.service_id ? (
                      <p className="text-sm text-gray-500">
                        No podemos ofrecer horarios para esta reserva. Contactá al negocio.
                      </p>
                    ) : cargandoSlots ? (
                      <p className="text-sm text-gray-500" role="status">
                        Buscando horarios...
                      </p>
                    ) : errorSlots ? (
                      <p className="text-sm text-destructive" role="alert">
                        {mensajeDeError(errorSlots, 'No pudimos cargar los horarios de ese día.')}
                      </p>
                    ) : slots.length === 0 ? (
                      <p className="text-sm text-gray-500">
                        No hay horarios libres ese día. Probá con otro.
                      </p>
                    ) : (
                      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
                        {slots.map((slot) => (
                          <button
                            key={slot.starts_at}
                            type="button"
                            onClick={() => setSlotElegido(slot)}
                            className={`rounded border p-2 text-sm ${
                              slotElegido?.starts_at === slot.starts_at
                                ? 'bg-primary text-primary-foreground'
                                : 'hover:bg-gray-50'
                            }`}
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
                    >
                      {isRescheduling ? 'Reprogramando...' : 'Confirmar nuevo horario'}
                    </Button>
                  </div>
                </div>

                <div className="border-t pt-6">
                  <h3 className="text-lg font-semibold mb-4">Cancelar reserva</h3>
                  {confirmandoCancelacion ? (
                    <div className="space-y-3">
                      <p className="text-sm text-gray-500">
                        ¿Cancelar la reserva? Esta acción no se puede deshacer.
                      </p>
                      <div className="flex gap-2">
                        <Button
                          variant="destructive"
                          onClick={handleCancel}
                          disabled={isCancelling}
                        >
                          {isCancelling ? 'Cancelando...' : 'Sí, cancelar'}
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => setConfirmandoCancelacion(false)}
                          disabled={isCancelling}
                        >
                          Volver
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <Button
                      variant="outline"
                      className="text-red-600 hover:text-red-700"
                      onClick={() => setConfirmandoCancelacion(true)}
                    >
                      Cancelar reserva
                    </Button>
                  )}
                </div>
              </>
            )}

            {booking.business_phone && (
              <p className="border-t pt-4 text-sm text-gray-500">
                ¿Necesitás hablar con el negocio? {booking.business_phone}
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
