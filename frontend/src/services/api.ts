import type { Business, Service, Professional, AvailabilityResponse, Booking, BookingCreateRequest } from '@/types'

const API_BASE = '/api/v1'

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error.detail || `Error ${response.status}`)
  }

  return response.json()
}

export const api = {
  // Negocio
  getBusiness: (slug: string) =>
    fetchApi<Business>(`/public/businesses/${slug}`),

  // Servicios
  getServices: (slug: string) =>
    fetchApi<Service[]>(`/public/businesses/${slug}/services`),

  // Profesionales
  getProfessionals: (slug: string, serviceId?: string) =>
    fetchApi<Professional[]>(
      `/public/businesses/${slug}/professionals${serviceId ? `?service_id=${serviceId}` : ''}`
    ),

  // Disponibilidad
  getAvailability: (slug: string, serviceId: string, date: string, professionalId?: string) =>
    fetchApi<AvailabilityResponse>(
      `/public/businesses/${slug}/availability`,
      {
        method: 'POST',
        body: JSON.stringify({
          service_id: serviceId,
          professional_id: professionalId || null,
          date,
        }),
      }
    ),

  // Reservas
  createBooking: (data: BookingCreateRequest) =>
    fetchApi<Booking>('/public/bookings', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  getBooking: (token: string) =>
    fetchApi<Booking>(`/public/bookings/${token}`),

  cancelBooking: (token: string) =>
    fetchApi<Booking>(`/public/bookings/${token}/cancel`, {
      method: 'POST',
    }),

  rescheduleBooking: (token: string, newStartsAt: string, newEndsAt: string, newDurationMinutes: number) =>
    fetchApi<Booking>(`/public/bookings/${token}/reschedule`, {
      method: 'POST',
      body: JSON.stringify({
        new_starts_at: newStartsAt,
        new_ends_at: newEndsAt,
        new_duration_minutes: newDurationMinutes,
      }),
    }),
}
