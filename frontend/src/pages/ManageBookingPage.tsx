import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/services/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'


export default function ManageBookingPage() {
  const { token } = useParams<{ token: string }>()
  const [isCancelling, setIsCancelling] = useState(false)
  const [isRescheduling, setIsRescheduling] = useState(false)
  const [newDate, setNewDate] = useState('')
  const [newTime, setNewTime] = useState('')

  const { data: booking, isLoading } = useQuery({
    queryKey: ['booking', token],
    queryFn: () => api.getBooking(token!),
    enabled: !!token,
  })

  const handleCancel = async () => {
    if (!confirm('¿Estás seguro de que querés cancelar la reserva?')) return
    setIsCancelling(true)
    try {
      await api.cancelBooking(token!)
      alert('Reserva cancelada')
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Error al cancelar')
    } finally {
      setIsCancelling(false)
    }
  }

  const handleReschedule = async () => {
    if (!newDate || !newTime) return
    setIsRescheduling(true)
    try {
      const startsAt = new Date(`${newDate}T${newTime}:00Z`).toISOString()
      const endsAt = new Date(new Date(`${newDate}T${newTime}:00Z`).getTime() + 60 * 60 * 1000).toISOString()
      await api.rescheduleBooking(token!, startsAt, endsAt, 60)
      alert('Reserva reprogramada')
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Error al reprogramar')
    } finally {
      setIsRescheduling(false)
    }
  }

  if (isLoading) {
    return <div className="flex items-center justify-center min-h-screen">Cargando...</div>
  }

  if (!booking) {
    return <div className="flex items-center justify-center min-h-screen">Reserva no encontrada</div>
  }

  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-2xl mx-auto">
        <Card>
          <CardHeader>
            <CardTitle className="text-2xl">Tu reserva</CardTitle>
            <CardDescription>
              Estado: <span className="font-medium">{booking.status}</span>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="space-y-2">
              <div className="text-sm text-gray-500">Fecha y hora</div>
              <div className="text-lg font-medium">
                {new Date(booking.starts_at).toLocaleString('es-AR', {
                  dateStyle: 'full',
                  timeStyle: 'short',
                })}
              </div>
            </div>

            <div className="space-y-2">
              <div className="text-sm text-gray-500">Profesional</div>
              <div className="text-lg font-medium">{booking.professional_id}</div>
            </div>

            {booking.status !== 'cancelled' && (
              <>
                <div className="border-t pt-6">
                  <h3 className="text-lg font-semibold mb-4">Cancelar reserva</h3>
                  <Button
                    variant="destructive"
                    onClick={handleCancel}
                    disabled={isCancelling}
                  >
                    {isCancelling ? 'Cancelando...' : 'Cancelar reserva'}
                  </Button>
                </div>

                <div className="border-t pt-6">
                  <h3 className="text-lg font-semibold mb-4">Reprogramar reserva</h3>
                  <div className="space-y-4">
                    <div>
                      <label className="text-sm text-gray-500">Nueva fecha</label>
                      <input
                        type="date"
                        value={newDate}
                        onChange={(e) => setNewDate(e.target.value)}
                        className="w-full mt-1 p-2 border rounded"
                      />
                    </div>
                    <div>
                      <label className="text-sm text-gray-500">Nueva hora</label>
                      <input
                        type="time"
                        value={newTime}
                        onChange={(e) => setNewTime(e.target.value)}
                        className="w-full mt-1 p-2 border rounded"
                      />
                    </div>
                    <Button
                      onClick={handleReschedule}
                      disabled={isRescheduling || !newDate || !newTime}
                    >
                      {isRescheduling ? 'Reprogramando...' : 'Reprogramar'}
                    </Button>
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
