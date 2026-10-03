export interface Business {
  id: string
  name: string
  description: string | null
  timezone: string
  locale: string
  currency: string
  slot_interval_minutes: number
  min_lead_minutes: number
  brand_color: string | null
}

export interface Service {
  id: string
  name: string
  description: string | null
  duration_minutes: number
  price: string
  currency: string
}

export interface Professional {
  id: string
  display_name: string
  bio: string | null
  color: string | null
}

export interface Slot {
  starts_at: string
  ends_at: string
}

export interface AvailabilityResponse {
  slots: Slot[]
}

export interface Booking {
  booking_id: string
  secure_token: string
  starts_at: string
  ends_at: string
  professional_id: string
  status: string
}

export interface BookingCreateRequest {
  slug: string
  service_id: string
  professional_id?: string | null
  customer_first_name: string
  customer_last_name: string
  customer_phone_e164: string
  starts_at: string
  ends_at: string
  local_date: string
  idempotency_key?: string | null
}
