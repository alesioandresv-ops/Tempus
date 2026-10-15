import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { MapPin } from 'lucide-react'
import { api, ApiError } from '@/services/api'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Turnstile, turnstileHabilitado } from '@/components/ui/Turnstile'
import type { Service, Professional, Slot } from '@/types'

export default function BusinessPage() {
  const { slug } = useParams<{ slug: string }>()
  const [step, setStep] = useState(1)
  const [selectedService, setSelectedService] = useState<Service | null>(null)
  const [selectedProfessional, setSelectedProfessional] = useState<Professional | null>(null)
  const [selectedDate, setSelectedDate] = useState<string>('')
  const [selectedSlot, setSelectedSlot] = useState<Slot | null>(null)
  const [formData, setFormData] = useState({
    firstName: '',
    lastName: '',
    phone: '',
  })
  const [bookingResult, setBookingResult] = useState<{ secure_token: string } | null>(null)
  const [enviando, setEnviando] = useState(false)
  const [errorReserva, setErrorReserva] = useState<string | null>(null)
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null)
  const [enlaceCopiado, setEnlaceCopiado] = useState(false)

  const { data: business } = useQuery({
    queryKey: ['business', slug],
    queryFn: () => api.getBusiness(slug!),
    enabled: !!slug,
  })

  const { data: services } = useQuery({
    queryKey: ['services', slug],
    queryFn: () => api.getServices(slug!),
    enabled: !!slug,
  })

  const { data: professionals } = useQuery({
    queryKey: ['professionals', slug, selectedService?.id],
    queryFn: () => api.getProfessionals(slug!, selectedService?.id),
    enabled: !!slug && !!selectedService,
  })

  const { data: availability } = useQuery({
    queryKey: ['availability', slug, selectedService?.id, selectedDate, selectedProfessional?.id],
    queryFn: () =>
      api.getAvailability(
        slug!,
        selectedService!.id,
        selectedDate,
        selectedProfessional?.id
      ),
    enabled: !!slug && !!selectedService && !!selectedDate,
  })

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedService || !selectedSlot || !selectedDate || enviando) return
    // Con Turnstile montado, no se manda sin su token: el backend responde 403 y el
    // viaje de ida y vuelta solo agrega un error que ya se sabe de antemano.
    if (turnstileHabilitado && !turnstileToken) {
      setErrorReserva('Completa la verificacion anti-bot para confirmar.')
      return
    }

    setEnviando(true)
    setErrorReserva(null)
    try {
      const result = await api.createBooking({
        slug: slug!,
        service_id: selectedService.id,
        professional_id: selectedProfessional?.id || null,
        customer_first_name: formData.firstName,
        customer_last_name: formData.lastName,
        customer_phone_e164: formData.phone,
        starts_at: selectedSlot.starts_at,
        ends_at: selectedSlot.ends_at,
        local_date: selectedDate,
        turnstile_token: turnstileToken,
      })
      setBookingResult(result)
      setStep(5)
    } catch (error) {
      // Un 409 en este flujo es casi siempre "ese horario ya no esta": el mensaje del
      // backend ya lo explica, asi que se muestra tal cual en vez de un generico.
      setErrorReserva(
        error instanceof ApiError
          ? error.message
          : error instanceof Error
            ? error.message
            : 'Error al crear la reserva'
      )
    } finally {
      setEnviando(false)
    }
  }

  if (!business) {
    return <div className="flex items-center justify-center min-h-screen">Cargando...</div>
  }

  //: `0,0` es "sin coordenadas" (la API nace con 0/null): no se ofrece el
  //: mapa para un negocio que no seteo su ubicacion.
  const lat = business.latitude
  const lng = business.longitude
  const tieneUbicacion = lat != null && lng != null && lat !== 0 && lng !== 0

  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-2xl mx-auto">
        <Card>
          <CardHeader>
            <CardTitle className="text-2xl">{business.name}</CardTitle>
            {business.description && (
              <CardDescription>{business.description}</CardDescription>
            )}
            {business.address_text && (
              <p className="text-sm text-muted-foreground flex items-center gap-1">
                <MapPin className="w-4 h-4" /> {business.address_text}
              </p>
            )}
            {tieneUbicacion && (
              <div className="flex flex-wrap items-center gap-2 pt-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    window.open(
                      `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=17/${lat}/${lng}`,
                      '_blank'
                    )
                  }
                >
                  <MapPin className="mr-2 h-4 w-4" />
                  Cómo llegar - Ver en mapa
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    window.open(
                      `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`,
                      '_blank'
                    )
                  }
                >
                  Google Maps
                </Button>
              </div>
            )}
          </CardHeader>
          <CardContent>
            {/* Paso 1: Seleccionar servicio */}
            {step === 1 && (
              <div className="space-y-4">
                <h3 className="text-lg font-semibold">Seleccioná un servicio</h3>
                <div className="space-y-2">
                  {services?.map((service) => (
                    <button
                      key={service.id}
                      onClick={() => {
                        setSelectedService(service)
                        setStep(2)
                      }}
                      className="w-full p-4 text-left border rounded-lg hover:bg-gray-50 transition-colors"
                    >
                      <div className="font-medium">{service.name}</div>
                      <div className="text-sm text-gray-500">
                        {service.duration_minutes} min - ${service.price} {service.currency}
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Paso 2: Seleccionar profesional */}
            {step === 2 && (
              <div className="space-y-4">
                <h3 className="text-lg font-semibold">Seleccioná un profesional</h3>
                <div className="space-y-2">
                  <button
                    onClick={() => {
                      setSelectedProfessional(null)
                      setStep(3)
                    }}
                    className="w-full p-4 text-left border rounded-lg hover:bg-gray-50 transition-colors"
                  >
                    <div className="font-medium">Cualquier profesional</div>
                  </button>
                  {professionals?.map((prof) => (
                    <button
                      key={prof.id}
                      onClick={() => {
                        setSelectedProfessional(prof)
                        setStep(3)
                      }}
                      className="w-full p-4 text-left border rounded-lg hover:bg-gray-50 transition-colors"
                    >
                      <div className="font-medium">{prof.display_name}</div>
                    </button>
                  ))}
                </div>
                <Button variant="outline" onClick={() => setStep(1)}>
                  Volver
                </Button>
              </div>
            )}

            {/* Paso 3: Seleccionar fecha */}
            {step === 3 && (
              <div className="space-y-4">
                <h3 className="text-lg font-semibold">Seleccioná una fecha</h3>
                <Input
                  type="date"
                  value={selectedDate}
                  onChange={(e) => setSelectedDate(e.target.value)}
                  min={new Date().toISOString().split('T')[0]}
                />
                <div className="flex gap-2">
                  <Button variant="outline" onClick={() => setStep(2)}>
                    Volver
                  </Button>
                  <Button
                    onClick={() => setStep(4)}
                    disabled={!selectedDate}
                  >
                    Continuar
                  </Button>
                </div>
              </div>
            )}

            {/* Paso 4: Seleccionar horario */}
            {step === 4 && (
              <div className="space-y-4">
                <h3 className="text-lg font-semibold">Seleccioná un horario</h3>
                {availability?.slots.length === 0 ? (
                  <p className="text-gray-500">No hay horarios disponibles para esta fecha.</p>
                ) : (
                  <div className="grid grid-cols-3 gap-2">
                    {availability?.slots.map((slot) => (
                      <button
                        key={slot.starts_at}
                        onClick={() => setSelectedSlot(slot)}
                        className={`p-2 border rounded text-sm ${
                          selectedSlot?.starts_at === slot.starts_at
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
                <div className="flex gap-2">
                  <Button variant="outline" onClick={() => setStep(3)}>
                    Volver
                  </Button>
                  <Button
                    onClick={() => setStep(5)}
                    disabled={!selectedSlot}
                  >
                    Continuar
                  </Button>
                </div>
              </div>
            )}

            {/* Paso 5: Datos del cliente */}
            {step === 5 && !bookingResult && (
              <form onSubmit={handleSubmit} className="space-y-4" noValidate>
                <h3 className="text-lg font-semibold">Tus datos</h3>
                <div className="space-y-2">
                  <Input
                    placeholder="Nombre"
                    value={formData.firstName}
                    onChange={(e) => setFormData({ ...formData, firstName: e.target.value })}
                    required
                  />
                  <Input
                    placeholder="Apellido"
                    value={formData.lastName}
                    onChange={(e) => setFormData({ ...formData, lastName: e.target.value })}
                    required
                  />
                  <Input
                    placeholder="WhatsApp (ej: +5491112345678)"
                    value={formData.phone}
                    onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
                    required
                  />
                </div>

                {/* Sin sitekey configurada no renderiza nada, y el backend tampoco
                    exige el token: los dos lados comparten la misma bandera. */}
                <Turnstile onVerify={setTurnstileToken} />

                {errorReserva && (
                  <p role="alert" className="text-sm text-red-600">
                    {errorReserva}
                  </p>
                )}

                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    type="button"
                    onClick={() => setStep(4)}
                    disabled={enviando}
                  >
                    Volver
                  </Button>
                  <Button type="submit" disabled={enviando || (turnstileHabilitado && !turnstileToken)}>
                    {enviando ? 'Confirmando...' : 'Confirmar reserva'}
                  </Button>
                </div>
              </form>
            )}

            {/* Confirmación */}
            {step === 5 && bookingResult && (
              <div className="space-y-4 text-center">
                <h3 className="text-lg font-semibold text-green-600">¡Reserva confirmada!</h3>
                {/*
                  Se muestra el enlace de gestion, no el token crudo. Con el token suelto
                  el cliente no tenia como llegar a `/r/:token`--cancelar o reprogramar
                  era inalcanzable desde aca--, y el texto prometia un WhatsApp que sin
                  proveedor configurado nunca sale. El enlace depende solo de la reserva
                  recien creada, asi que es verdad siempre.
                */}
                <p className="text-sm text-gray-500">
                  Guardá este enlace para ver, reprogramar o cancelar tu reserva.
                </p>
                <div className="space-y-2">
                  <a
                    href={`/r/${bookingResult.secure_token}`}
                    className={buttonVariants({ className: 'w-full' })}
                  >
                    Gestionar mi reserva
                  </a>
                  <button
                    type="button"
                    onClick={() => {
                      navigator.clipboard
                        ?.writeText(`${window.location.origin}/r/${bookingResult.secure_token}`)
                        .then(() => setEnlaceCopiado(true))
                        .catch(() => setEnlaceCopiado(false))
                    }}
                    className="text-xs text-gray-500 underline underline-offset-2"
                  >
                    {enlaceCopiado ? 'Enlace copiado' : 'Copiar enlace'}
                  </button>
                </div>
                <p className="break-all text-xs text-gray-400">
                  {window.location.origin}/r/{bookingResult.secure_token}
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
