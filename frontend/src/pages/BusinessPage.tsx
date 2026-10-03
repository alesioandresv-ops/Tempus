import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/services/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
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
    if (!selectedService || !selectedSlot || !selectedDate) return

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
      })
      setBookingResult(result)
      setStep(5)
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Error al crear la reserva')
    }
  }

  if (!business) {
    return <div className="flex items-center justify-center min-h-screen">Cargando...</div>
  }

  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-2xl mx-auto">
        <Card>
          <CardHeader>
            <CardTitle className="text-2xl">{business.name}</CardTitle>
            {business.description && (
              <CardDescription>{business.description}</CardDescription>
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
            {step === 5 && (
              <form onSubmit={handleSubmit} className="space-y-4">
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
                <div className="flex gap-2">
                  <Button variant="outline" type="button" onClick={() => setStep(4)}>
                    Volver
                  </Button>
                  <Button type="submit">Confirmar reserva</Button>
                </div>
              </form>
            )}

            {/* Confirmación */}
            {step === 5 && bookingResult && (
              <div className="space-y-4 text-center">
                <h3 className="text-lg font-semibold text-green-600">¡Reserva confirmada!</h3>
                <p className="text-sm text-gray-500">
                  Te enviamos un WhatsApp con los detalles y el enlace para gestionar tu reserva.
                </p>
                <p className="text-xs text-gray-400">
                  Token: {bookingResult.secure_token}
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
