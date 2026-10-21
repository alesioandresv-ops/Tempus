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

/**
 * El color de marca del negocio como estilo inline, o `undefined` si no hay.
 *
 * **Existe porque `brand_color` se guardaba, se validaba y se devolvia por la API...
 * y no lo usaba nadie.** El negocio elegia su color y la pagina publica lo ignoraba,
 * que es peor que no ofrecerlo: da la sensacion de que la personalizacion no anda.
 *
 * El valor llega validado del servidor (`_validar_color` acepta `#rgb`, `#rrggbb`,
 * `rgb(...)` o un nombre de CSS y rechaza el resto), asi que se aplica **tal cual**.
 * Aun asi se pinta con `color` y no como `background` ni `border`, y solo sobre el
 * titulo: un color claro sobre fondo claro dejaria el nombre del negocio ilegible, y
 * el titulo es lo unico que no puede desaparecer de esa pagina.
 */
function colorDeMarca(color: string | null | undefined): React.CSSProperties | undefined {
  return color ? { color } : undefined
}

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

  const {
    data: business,
    isLoading: cargandoNegocio,
    error: errorNegocio,
  } = useQuery({
    queryKey: ['business', slug],
    queryFn: () => api.getBusiness(slug!),
    enabled: !!slug,
    // Un negocio que no existe no se arregla reintentando: el 404 es la respuesta,
    // no un fallo transitorio. Sin esto la pagina espera los reintentos y parece
    // colgada aunque el backend ya contesto.
    retry: false,
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

  /*
   * Tres estados distintos, y la distincion importa.
   *
   * Antes esto era un solo `if (!business) return "Cargando..."`, que confundia "no
   * llego todavia" con "no existe": un slug inexistente--o cualquiera de los 404 que
   * da la API publica--dejaba la pagina girando para siempre, sin decir nada. Un
   * negocio que no existe tiene que decirlo, y la persona tiene que poder hacer algo
   * (volver, revisar el enlace) en vez de esperar una pagina que nunca va a cargar.
   */
  if (cargandoNegocio) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <p className="text-muted-foreground" role="status">Cargando...</p>
      </div>
    )
  }

  if (errorNegocio || !business) {
    // El backend distingue "no existe" (404) del resto. El mensaje del 404 ya es
    // claro y esta en castellano, asi que se muestra tal cual; para lo demas--sin
    // red, 500--un texto generico es mas honesto que propagar un error tecnico.
    const esNoEncontrado = errorNegocio instanceof ApiError && errorNegocio.status === 404
    return (
      <div className="min-h-screen bg-gray-50 py-8 px-4">
        <div className="max-w-md mx-auto">
          <Card>
            <CardHeader>
              <CardTitle className="text-2xl">
                {esNoEncontrado ? 'No encontramos esta página' : 'No pudimos cargar la página'}
              </CardTitle>
              <CardDescription>
                {esNoEncontrado
                  ? 'El enlace puede estar mal escrito o el negocio ya no está disponible.'
                  : 'Puede ser un problema de conexión. Probá recargar en un momento.'}
              </CardDescription>
            </CardHeader>
          </Card>
        </div>
      </div>
    )
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
            <CardTitle className="text-2xl" style={colorDeMarca(business.brand_color)}>
              {business.name}
            </CardTitle>
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
                        // Cambiar de servicio reencuadra la disponibilidad entera:
                        // el profesional (filtrado por servicio) y el horario
                        // elegidos antes dejan de corresponder, asi que se sueltan.
                        setSelectedProfessional(null)
                        setSelectedSlot(null)
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
                      setSelectedSlot(null)
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
                        setSelectedSlot(null)
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
                  onChange={(e) => {
                    setSelectedDate(e.target.value)
                    // El horario es de la fecha anterior: al cambiarla deja de ser
                    // valido y no puede quedar elegido.
                    setSelectedSlot(null)
                  }}
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
