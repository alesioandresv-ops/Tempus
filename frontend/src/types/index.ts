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

/**
 * Cuerpo del alta self-service.
 *
 * `slug` va **crudo**, tal cual lo escribe la persona. La canonicalizacion la hace
 * el servidor con la misma `slugify` que usa el panel: si el navegador hiciera su
 * propia version, las dos reglas divergen y la que responde primero se queda con el
 * nombre. Ver el docstring de `slugify` en `businesses/service.py`.
 */
export interface BusinessRegisterRequest {
  business_name: string
  slug: string
  owner_name: string
  email: string
  password: string
  phone?: string | null
  timezone: string
}

/**
 * Respuesta del alta. **No trae tokens**: el dueno entra por `/login` con las
 * credenciales que acaba de elegir.
 */
export interface BusinessRegisterResponse {
  business_id: string
  slug: string
  mensaje: string
}

/**
 * Respuesta del chequeo de disponibilidad del nombre de URL.
 *
 * `slug` viene **ya normalizado**: el formulario muestra la URL que va a quedar, no
 * un eco de lo que se escribio.
 */
export interface SlugAvailability {
  slug: string
  disponible: boolean
}

/**
 * Credenciales de login.
 *
 * `business_slug` es **opcional y de desambiguado**. Hace falta solo cuando el mismo
 * correo es miembro de dos negocios: sin el, el backend responde 401 en vez de
 * adivinar a cual entrar. Con una sola membresia se ignora--asi que mandarlo siempre no
 * hace dano-- y cuando no corresponde el resultado es el mismo 401 que una contrasena
 * incorrecta, no un 404.
 *
 * El campo se llama igual que en el backend a proposito: el `business_slug` del
 * `LoginRequest` de Python. Renombrarlo de un lado y no del otro produce un 422 que
 * no dice que el nombre del campo esta mal.
 */
export interface LoginRequest {
  email: string
  password: string
  business_slug?: string | null
}

/**
 * Respuesta del login. **No trae el refresh token**: ese va en una cookie `HttpOnly`
 * que el navegador manda solo y que el JavaScript no puede leer.
 */
export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in: number
}

/**
 * Identidad del token de ahora, tal como lo devuelve `GET /business/me`.
 *
 * `scopes` viene **ordenado** desde el servidor a proposito--va directo a la lista de
 * checks de la interfaz, y un `Set` de JavaScript no tiene orden estable entre
 * corridas.
 */
export interface SessionProfile {
  user_id: string
  business_id: string
  business_name: string | null
  role: string
  scopes: string[]
  is_admin: boolean
}

// --------------------------------------------------------------------------- //
// Panel del negocio (Fase 4)
//
// Los tipos de arriba son los de la API publica, que no trae `is_active`
// ni `sort_order`. Los del panel ven el registro completo--incluido el
// estado-- porque la unica forma de mostrar "archivado" es tenerlo.
// --------------------------------------------------------------------------- //

/** Servicio tal como lo devuelve el panel: incluye el estado y el orden. */
export interface PanelService {
  id: string
  name: string
  description: string | null
  duration_minutes: number
  price: string
  currency: string
  color: string | null
  is_active: boolean
  sort_order: number
  archived_at: string | null
}

/** Alta de servicio. El precio viaja como string: son centavos de decimal
 * exacto, no un float que redondea. */
export interface ServicioCreateRequest {
  name: string
  description?: string | null
  duration_minutes: number
  price: string
  currency: string
  color?: string | null
  sort_order?: number | null
}

/** Edicion parcial: solo lo que se manda cambia. */
export interface ServicioPatchRequest {
  name?: string
  description?: string | null
  duration_minutes?: number
  price?: string
  currency?: string
  color?: string | null
  is_active?: boolean
  sort_order?: number
}

/** Profesional tal como lo devuelve el panel. */
export interface PanelProfessional {
  id: string
  display_name: string
  bio: string | null
  color: string | null
  /** E.164 **sin `+`**, digitos nomas: es el formato en el que viaja
   *  todo telefono en este proyecto. */
  whatsapp: string | null
  avatar_url: string | null
  is_active: boolean
  sort_order: number
  archived_at: string | null
}

export interface ProfesionalCreateRequest {
  display_name: string
  bio?: string | null
  color?: string | null
  sort_order?: number | null
  whatsapp?: string | null
  avatar_url?: string | null
}

export interface ProfesionalPatchRequest {
  display_name?: string
  bio?: string | null
  color?: string | null
  is_active?: boolean
  sort_order?: number
  whatsapp?: string | null
  avatar_url?: string | null
}

/** Una fila de `professional_services`: un servicio que hace un profesional. */
export interface AsignacionServicio {
  service_id: string
  is_active: boolean
  custom_duration_minutes: number | null
  custom_price: string | null
}

/** Alta/edición de una asignación: los custom son overrides opcionales. */
export interface AsignacionServicioIn {
  service_id: string
  custom_duration_minutes?: number | null
  custom_price?: string | null
}

/** Reemplazo completo de los servicios de un profesional. */
export interface AsignacionesRequest {
  servicios: AsignacionServicioIn[]
}

/** Una ventana de horario. `start`/`end` viajan como "HH:MM". */
export interface Ventana {
  start: string
  end: string
}

/** El horario de un dia. Cero ventanas es un dia cerrado. */
export interface DiaHorario {
  /** 0 = lunes ... 6 = domingo. */
  weekday: number
  windows: Ventana[]
}

/** La semana completa. Reemplaza, no suma. */
export interface SemanaHorario {
  dias: DiaHorario[]
}

/**
 * Horario de un profesional.
 *
 * `hereda` dice si tiene horario propio o sigue el del negocio
 * (ADR-0005). Cero ventanas con `hereda: false` es "no atiendo
 * ningun dia"; cero ventanas con `hereda: true` es "atiendo cuando
 * atiende el negocio".
 */
export interface HorarioProfesional {
  hereda: boolean
  dias: DiaHorario[]
}

// --------------------------------------------------------------------------- //
// Panel del profesional (Fase B)
//
// Los tres tipos de abajo son los que devuelven `/business/profesional/agenda`,
// `/business/bloqueos` y `/business/ausencias`. Un profesional solo puede leer
// los suyos: el backend resuelve el `professional_id` desde el token, nunca
// desde un parametro de la URL.
// --------------------------------------------------------------------------- //

/** Una reserva de la agenda del profesional, con nombres desnormalizados. */
export interface ReservaDePanel {
  id: string
  status: string
  source: string
  starts_at: string
  ends_at: string
  local_date: string
  duration_minutes: number
  price_snapshot: string
  currency: string
  service_id: string
  professional_id: string
  customer_id: string
  notes: string | null
  cancel_reason: string | null
  cancelled_at: string | null
  created_at: string
  servicio_nombre: string | null
  profesional_nombre: string | null
  cliente_nombre: string | null
  cliente_telefono: string | null
}

/** Un bloqueo de calendario. `professional_id: null` es un cierre de todo el negocio. */
export interface BloqueoDePanel {
  id: string
  professional_id: string | null
  kind: string
  starts_at: string
  ends_at: string
  reason: string | null
}

/** Vacaciones / licencia de un profesional. */
export interface AusenciaDePanel {
  id: string
  professional_id: string
  kind: string
  status: string
  starts_at: string
  ends_at: string
  reason: string | null
}

// --------------------------------------------------------------------------- //
// Panel admin (Fase C)
//
// Reservas filtrables con `q`, reprogramación, bloqueos/feriados/vacaciones y
// el resumen estadístico. Todo vive bajo `/business` con el token del admin.
// --------------------------------------------------------------------------- //

/** Página de reservas con el total, para paginar de verdad. */
export interface ReservaPagina {
  items: ReservaDePanel[]
  total: number
  limite: number
  offset: number
}

/** Filtros del listado de reservas del admin. `q` busca por nombre o teléfono. */
export interface ReservasFiltro {
  desde?: string
  hasta?: string
  professional_id?: string
  estado?: string
  q?: string
  limite?: number
  offset?: number
}

/** Reprogramar desde el panel. Los tres campos van juntos (como el flujo público). */
export interface ReprogramarReservaRequest {
  new_starts_at: string
  new_ends_at: string
  new_duration_minutes: number
}

/** Un feriado del negocio: un día entero cerrado, con nombre. */
export interface FeriadoDePanel {
  id: string
  local_date: string
  name: string
}

export interface FeriadoCreateRequest {
  local_date: string
  name: string
}

/** Alta de un bloqueo. `professional_id: null` cierra todo el negocio. */
export interface BloqueoCreateRequest {
  starts_at: string
  ends_at: string
  kind: string
  professional_id?: string | null
  reason?: string | null
}

/** Alta de vacaciones/licencia de un profesional (siempre aprobada). */
export interface AusenciaCreateRequest {
  professional_id: string
  starts_at: string
  ends_at: string
  kind: string
  reason?: string | null
}

/** Ocupación por profesional en la ventana de estadísticas. */
export interface OcupacionProfesional {
  professional_id: string
  nombre: string
  turnos: number
  cancelados: number
}

/** Resumen del panel admin para una ventana de fechas locales. */
export interface Estadisticas {
  desde: string
  hasta: string
  total_reservas: number
  ingresos: string
  tasa_cancelacion: number
  por_profesional: OcupacionProfesional[]
}

// --------------------------------------------------------------------------- //
// Panel admin (Fase D)
//
// Historial del cliente: listado con totales agregados sobre `bookings` y
// detalle por cliente. Todo bajo `/business` con el token del admin.
// --------------------------------------------------------------------------- //

/** Un cliente del listado, con sus totales agregados sobre `bookings`. */
export interface ClienteDePanel {
  id: string
  first_name: string
  last_name: string | null
  phone_e164: string
  notes: string | null
  is_opted_out: boolean
  marketing_opt_in: boolean
  created_at: string
  total_reservas: number
  ultima_reserva: string | null
  /** Suma de `price_snapshot` de reservas confirmadas/completadas (string, viaja en JSON). */
  total_gastado: string
  profesional_mas_frecuente: string | null
}

/** Filtros del listado de clientes: búsqueda parcial por nombre o teléfono. */
export interface ClientesFiltro {
  busqueda?: string
  limite?: number
  offset?: number
}

// --------------------------------------------------------------------------- //
// Configuración de WhatsApp (Fase D-3)
// --------------------------------------------------------------------------- //

/**
 * Estado de los recordatorios por WhatsApp del negocio.
 *
 * `token_ultimos` es el token real enmascarado (`••••` + últimos 4),
 * que basta para reconocer de qué cuenta de Meta se trata sin exponer
 * el secreto. `null` = no hay token guardado.
 */
export interface WhatsappConfig {
  activo: boolean
  phone_number_id: string | null
  token_ultimos: string | null
  reminder_24h_template: string
  reminder_2h_template: string
}

/**
 * Cuerpo del PUT. Los campos omitidos conservan lo que haya: el token
 * solo se pega la vez que se configura (o se cambia), y un `activo`
 * true sin token nuevo reactiva con el que ya está guardado.
 */
export interface WhatsappConfigSave {
  activo: boolean
  phone_number_id?: string
  access_token?: string
  reminder_24h_template?: string
  reminder_2h_template?: string
}
