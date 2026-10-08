import type {
  Business,
  Service,
  Professional,
  AvailabilityResponse,
  Booking,
  BookingCreateRequest,
  BusinessRegisterRequest,
  BusinessRegisterResponse,
  SlugAvailability,
  LoginRequest,
  LoginResponse,
  SessionProfile,
  PanelService,
  PanelProfessional,
  ServicioCreateRequest,
  ServicioPatchRequest,
  ProfesionalCreateRequest,
  ProfesionalPatchRequest,
  AsignacionServicio,
  AsignacionesRequest,
  SemanaHorario,
  HorarioProfesional,
  ReservaDePanel,
  BloqueoDePanel,
  AusenciaDePanel,
  ReservaPagina,
  ReservasFiltro,
  ReprogramarReservaRequest,
  FeriadoDePanel,
  FeriadoCreateRequest,
  BloqueoCreateRequest,
  AusenciaCreateRequest,
  Estadisticas,
  ClienteDePanel,
  ClientesFiltro,
} from '@/types'

const API_BASE = '/api/v1'

/**
 * Error de la API con el cuerpo de la respuesta encima.
 *
 * **El mensaje solo no alcanza en los formularios.** La API responde RFC 9457: un
 * 422 de esquema trae `errors` con `field` y `message` por campo, y un 409 trae un
 * `detail` que hay que mostrar al lado del input que lo provoco. Tirar el body
 * obligaba a los formularios a hacer un `fetch` aparte para volver a leer lo que la
 * respuesta ya habia dicho.
 *
 * `message` sigue siendo el `detail` que se usaba antes, asi que las paginas que
 * solo muestran `error.message` no cambian de comportamiento.
 */
export class ApiError extends Error {
  /** Estado HTTP de la respuesta. */
  readonly status: number
  /** Cuerpo completo, ya parseado. Vacio si la respuesta no era JSON. */
  readonly body: Record<string, unknown>
  /**
   * Segundos que pide esperar antes de reintentar, en un 429.
   *
   * Se lee del header `Retry-After`--que es donde RFC 6585 lo pone y donde lo
   * copian los proxies-- y se cae a `body.retry_after` por si un cliente viejo no
   * lo propaga. `null` cuando el status no es 429 o el valor no es un numero: un
   * `NaN` disfrazado de segundos mostraria "proba en NaN segundos".
   */
  readonly retryAfter: number | null

  constructor(status: number, body: Record<string, unknown>, retryAfter: number | null = null) {
    const detail = typeof body.detail === 'string' ? body.detail : `Error ${status}`
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.body = body
    this.retryAfter = retryAfter
  }

  /** Errores por campo de un 422, indexados por nombre de campo. */
  get fieldErrors(): Record<string, string> {
    const errores = this.body.errors
    if (!Array.isArray(errores)) return {}
    const porCampo: Record<string, string> = {}
    for (const error of errores) {
      if (typeof error !== 'object' || error === null) continue
      const campo = error as { field?: unknown; message?: unknown }
      if (typeof campo.field === 'string' && typeof campo.message === 'string') {
        // `body.phone` y no `phone`: los 422 de FastAPI traen el nombre de la
        // parte del cuerpo, y sin pelar el prefijo la clave no matchea el campo.
        porCampo[campo.field.replace(/^body\./, '')] = campo.message
      }
    }
    return porCampo
  }
}

// --------------------------------------------------------------------------- //
// Sesion
// --------------------------------------------------------------------------- //

/**
 * El access token vive **en memoria**, en esta variable de modulo, y no en
 * `localStorage` ni en `sessionStorage`.
 *
 * No es purismo. Un XSS que corre en la pagina puede leer las dos cosas: lo que este
 * en `localStorage` esta ahi para siempre y sobrevive al cierre de la pestana, con lo
 * que un token robado sirve para siempre y no solo hasta que la persona se da cuenta.
 * En memoria, lo peor que consigue es usar la sesion de la persona hasta que recargue.
 *
 * El refresh token **no esta en ninguna variable**: viaja en una cookie `HttpOnly`
 * que el navegador manda sola y que el JavaScript de la pagina no puede leer. Esa es
 * justamente la razon de que se pueda refrescar sin guardar nada.
 *
 * **La consecuencia es que un F5 deja la pestana sin access token**, y hay que pedir
 * uno nuevo antes de poder hacer nada. Lo hace `ProtectedRoute` al montar. Perder el
 * refresh si cierra la sesion de verdad--que es lo que tiene que pasar-- mientras que
 * perder el access solo cuesta un viaje de ida y vuelta.
 */
let accessToken: string | null = null

/** El access token actual, o `null` si esta pestana no tiene sesion. */
export function accessTokenActual(): string | null {
  return accessToken
}

/** Guarda el access token. `null` es lo mismo que cerrar la sesion en el navegador. */
export function guardarAccessToken(token: string | null): void {
  accessToken = token
}

/**
 * El token CSRF que mandan `/auth/refresh` y `/auth/logout`.
 *
 * **Ojo con lo que esto es hoy.** El backend valida **solo el formato** del header
 * `X-CSRF-Token`--alfanumerico, guion y guion bajo-- mas que el `Origin` este en la
 * lista de `CORS_ORIGINS`; el propio codigo lo dice: "En una implementacion completa
 * con tokens CSRF del lado servidor, aqui se verificaria que el token coincida con el
 * almacenado". O sea, hoy el `Origin` es la defense real y este valor es formato.
 *
 * Aun asi se genera uno por pestana y no se manda un string fijo: es lo que va a
 * funcionar el dia que el backend deje de aceptar cualquier formato, y cambiarlo
 * despues es tocar una sola linea. La defense de fondo no es el token sino que la
 * cookie es `SameSite=strict`, asi que un formulario de otro sitio ni la manda.
 */
function tokenCsrf(): string {
  return crypto.randomUUID()
}

/**
 * Lee los claims de un JWT sin verificarlo.
 *
 * **No es una validacion de seguridad.** Lo unico que necesita es el `exp`, para
 * decidir si conviene renovar antes de gastar un viaje--el backend es quien valida la
 * firma, y un token con la firma rota se revela solo cuando la API lo rechaza. Por eso
 * un token manipulado que devuelva un `exp` lejano no abre nada: lo unico que logra es
 * que el navegador espere un 401 que va a llegar igual.
 */
function claimsDelToken(token: string): Record<string, unknown> | null {
  const partes = token.split('.')
  if (partes.length !== 3) return null
  try {
    const base64 = partes[1].replace(/-/g, '+').replace(/_/g, '/')
    const relleno = '='.repeat((4 - (base64.length % 4)) % 4)
    const binario = atob(base64 + relleno)
    const bytes = Uint8Array.from(binario, (caracter) => caracter.charCodeAt(0))
    const claims = JSON.parse(new TextDecoder().decode(bytes))
    return typeof claims === 'object' && claims !== null ? (claims as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/**
 * Cuando vence el access token, en segundos epoch. `null` si no se puede leer.
 *
 * Un token ilegible devuelve `null`, que `vencePronto` trata como vencido. La duda
 * razonable es si conviene fallar abierto--"no se, mandalo"--o cerrado; fallo cerrado,
 * porque mandar un token que el servidor va a rechazar produce un 401 igual, solo que
 * con un viaje de ida y vuelta de por medio.
 */
export function expiracionDelToken(token: string | null): number | null {
  if (!token) return null
  const claims = claimsDelToken(token)
  const exp = claims?.exp
  return typeof exp === 'number' ? exp : null
}

/**
 * Margen de renovacion anticipada.
 *
 * Sin esto, un token que expira en 200 ms se da por bueno, se manda, y el request tarda
 * mas que esos 200 ms en volver: el 401 llega despues de que la pagina ya se habia
 * dibujado. Con 30 s de margen, esa carrera no llega a ocurrir--salvo en una red
 * anormalmente lenta, y ahi el interceptor del 401 la cubre igual.
 */
const MARGEN_DE_RENOVACION_S = 30

/** `true` si no hay token, si no se puede leer, o si vence dentro del margen. */
export function vencePronto(token: string | null): boolean {
  const exp = expiracionDelToken(token)
  if (exp === null) return true
  return exp - MARGEN_DE_RENOVACION_S <= Math.floor(Date.now() / 1000)
}

// --------------------------------------------------------------------------- //
// Fin de sesion
// --------------------------------------------------------------------------- //

/**
 * A quien se le avisa que la sesion se termino.
 *
 * `api.ts` no importa `react-router`: el cliente HTTP no deberia saber que existe un
 * router, y `App.tsx` ya esta dentro de `<Router>`, asi que es el lugar natural para
 * tener la `navigate`. Con la inversion--registrar la funcion-- ninguno de los dos
 * importa al otro y no hay ciclo.
 */
type AvisoDeFinDeSesion = () => void

const avisosDeFinDeSesion = new Set<AvisoDeFinDeSesion>()

/** Se registra el callback y se devuelve la funcion para darlo de baja. */
export function avisarFinDeSesion(fn: AvisoDeFinDeSesion): () => void {
  avisosDeFinDeSesion.add(fn)
  return () => avisosDeFinDeSesion.delete(fn)
}

function terminarSesion(): void {
  accessToken = null
  for (const avisar of avisosDeFinDeSesion) avisar()
}

// --------------------------------------------------------------------------- //
// Cliente HTTP
// --------------------------------------------------------------------------- //

/**
 * Rutas que van **sin** `Authorization`.
 *
 * El login es la que importa: mandarle un access token caducado al login haria que un
 * 401 de "tu sesion vencio" se viera como "credenciales invalidas", y el formulario
 * le diria a la persona que su password esta mal. Las demas van sin token porque son
 * publicas y mandarlo no aporta nada.
 */
const RUTAS_SIN_TOKEN = ['/auth/login', '/auth/platform/login', '/auth/refresh', '/auth/logout']

/** Peticiones que renuevan el access token: las que van con cookie, no con Bearer. */
const RUTAS_CON_COOKIE = ['/auth/refresh', '/auth/logout']

/**
 * Un unico refresh en vuelo, compartido por todas las peticiones que muevan un 401.
 *
 * Sin esto, cinco queries que fallan a la vez--que es exactamente lo que pasa al abrir
 * el panel--hacian cinco `POST /auth/refresh`. Y no es solo trafico: la rotacion es
 * **destructiva**, cada refresh invalida el token anterior de la familia. Cinco
 * rotaciones en paralelo dejan cuatro tokens presenting un token ya rotado, y eso el
 * backend lo detecta como **reuso** y revoca la familia entera: la sesion muere por
 * haber tenido cinco pestanas abiertas, y el siguiente refresh da 401 para siempre.
 */
let refrescoEnVuelo: Promise<boolean> | null = null

/**
 * Renueva el access token con la cookie. `true` si quedo uno usable.
 *
 * El 401 de esta llamada **no** dispara otro refresh: es lo unico que hacia que el
 * `catch` tiene sentido, y sin el un refresh caducado se reintentaria para siempre.
 */
async function renovarAccessToken(): Promise<boolean> {
  if (refrescoEnVuelo) return refrescoEnVuelo

  refrescoEnVuelo = (async () => {
    try {
      const respuesta = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'X-CSRF-Token': tokenCsrf() },
        credentials: 'same-origin',
      })
      if (!respuesta.ok) {
        accessToken = null
        return false
      }
      const cuerpo = (await respuesta.json()) as { access_token?: unknown }
      accessToken = typeof cuerpo.access_token === 'string' ? cuerpo.access_token : null
      return accessToken !== null
    } catch {
      // Sin red no hay sesion que recuperar, y no es un error que la persona pueda
      // actuar: se trata como "no se pudo renovar" y sigue el camino del 401. El token
      // se limpia tambien aca y no solo en el `!ok`, para que "el token que tengo" y
      // "el token que sirve" no puedan quedar desincronizados: uno caduca solo, y un
      // token valido guardado en memoria con la red caida parece una sesion viva.
      accessToken = null
      return false
    } finally {
      refrescoEnVuelo = null
    }
  })()

  return refrescoEnVuelo
}

async function cuerpoDe(respuesta: Response): Promise<Record<string, unknown>> {
  return (await respuesta.json().catch(() => ({}))) as Record<string, unknown>
}

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  return ejecutar<T>(path, options ?? {}, 0)
}

/**
 * El `fetch` de verdad, con el token puesto y la renovacion en el medio.
 *
 * `intentos` es lo que hace que la renovacion pase una sola vez por peticion: es un
 * numero y no un booleano porque "ya lo intente" y "lo estoy intentando" no son lo
 * mismo cuando dos peticiones comparten la renovacion--una puede haber rotado el token
 * mientras la otra ya habia decidido reintentar.
 */
async function ejecutar<T>(path: string, options: RequestInit, intentos: number): Promise<T> {
  const conToken = !RUTAS_SIN_TOKEN.includes(path)
  const cabeceras: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) ?? {}),
  }
  if (conToken && accessToken) cabeceras['Authorization'] = `Bearer ${accessToken}`
  if (RUTAS_CON_COOKIE.includes(path)) cabeceras['X-CSRF-Token'] = tokenCsrf()

  const respuesta = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: cabeceras,
    credentials: 'same-origin',
  })

  // El 204 va antes del parseo y no por casualidad. `/auth/logout` responde 204 **sin
  // cuerpo** --es lo que dice el handler-- y `respuesta.json()` sobre un cuerpo vacio
  // lanza `SyntaxError`. Sin esta linea el logout se va a la red, revoca la sesion de
  // verdad, y despues tira una excepcion: el `finally` de `api.logout` limpia la
  // memoria, pero la UI ve un fallo en un cierre de sesion que si funciono.
  if (respuesta.status === 204) return undefined as T

  if (respuesta.ok) return (await respuesta.json()) as T

  const cuerpo = await cuerpoDe(respuesta)

  // El 429 no se renueva: repetir el intento quemaria el limite que acaba de
  // dispararse, y `Retry-After` ya dice cuando volver.
  if (respuesta.status === 401 && conToken && intentos < 1) {
    if (await renovarAccessToken()) {
      return ejecutar<T>(path, options, intentos + 1)
    }
    /*
     * El refresh tambien fallo: la cookie no esta, la familia esta revocada o no hay
     * red. Ya no hay nada que reintentar.
     *
     * **El aviso va aca y no en el 401 a proposito.** Con N peticiones en vuelo--que es
     * justo lo que pasa al abrir el panel-- las N reciben un 401, pero solo las que
     * compartieron el refresh fallido llegan aca: las demas ya se fueron por el camino
     * de `renovarAccessToken() === true`. Con el aviso en el 401, un token que se pudo
     * renovar bien botanaria a la persona al login en medio de la carga.
     *
     * `terminarSesion()` no esta memoizado: N llamadas Navigan a `/login` N veces. Es
     * inocuo porque el destino es identico y es `replace`, asi que React Router
     * descarta las repetidas; no se memoiza porque un aviso que se come a si mismo
     * dejaria sin notificar a un suscriptor registrado mas tarde.
     */
    terminarSesion()
  }

  // `Retry-After` es header primero y body despues: el header es lo que ve un
  // proxy, el body lo que quedo de antes en el backend. `NaN`, negativo o cero se
  // normalizan a `null`--nunca a un numero que mienta.
  const headerRetryAfter = respuesta.headers.get('Retry-After')
  const candidato = Number(headerRetryAfter ?? cuerpo.retry_after ?? NaN)
  const retryAfter =
    respuesta.status === 429 && Number.isFinite(candidato) && candidato > 0 ? candidato : null

  throw new ApiError(respuesta.status, cuerpo, retryAfter)
}

/**
 * Versión de `ejecutar` para descargas de archivos: el mismo fetch con token y la
 * misma renovación ante 401, pero devuelve la `Response` cruda en vez de parsear
 * el JSON.
 *
 * `GET /business/reportes/mensual?formato=csv` responde un archivo y usa el 404
 * como "no hay datos": tirar un `ApiError` ahí obligaría a la pestaña a adivinar
 * por el mensaje, cuando el status ya dice todo. Quejarse de los detalles--que
 * el cuerpo no es JSON--sería parsear una respuesta que no está hecha para eso.
 */
async function descargar(path: string, options?: RequestInit, intentos = 0): Promise<Response> {
  const conToken = !RUTAS_SIN_TOKEN.includes(path)
  const cabeceras: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options?.headers as Record<string, string>) ?? {}),
  }
  if (conToken && accessToken) cabeceras['Authorization'] = `Bearer ${accessToken}`

  const respuesta = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: cabeceras,
    credentials: 'same-origin',
  })

  // El mismo reintento único del 401 que `ejecutar`: la renovación rota el token,
  // y repetir el pedido con el nuevo es lo que distingue "venció" de "no sesión".
  if (respuesta.status === 401 && conToken && intentos < 1) {
    if (await renovarAccessToken()) {
      return descargar(path, options, intentos + 1)
    }
    terminarSesion()
  }
  return respuesta
}

export const api = {
  // --- Sesion ---
  login: (datos: LoginRequest) =>
    fetchApi<LoginResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(datos),
    }),

  /**
   * Renueva la sesion con la cookie. Es la misma llamada que hace el interceptor, pero
   * expuesta para `ProtectedRoute`, que la necesita al montar--un F5 deja el access
   * token en el aire y sin esto la pestana quedaria como desconectada.
   *
   * **No pasa por `ejecutar` a proposito.** Un refresh que falla tiene que fallar,
   * sin intentar arreglarse a si mismo.
   */
  refresh: async (): Promise<boolean> => renovarAccessToken(),

  logout: async (): Promise<void> => {
    try {
      await fetchApi<never>('/auth/logout', { method: 'POST' })
    } finally {
      // El logout del backend es idempotente--204 aunque no haya cookie--pero si la
      // peticion no sale--sin red, con el servidor caido--el `finally` es lo que
      // asegura que la pestana no quede con un token que el servidor ya dio de baja.
      terminarSesion()
    }
  },

  /** Identidad, rol y scopes del token de ahora. */
  me: () => fetchApi<SessionProfile>('/business/me'),

  // --- Negocio ---
  getBusiness: (slug: string) =>
    fetchApi<Business>(`/public/businesses/${slug}`),

  // --- Servicios ---
  getServices: (slug: string) =>
    fetchApi<Service[]>(`/public/businesses/${slug}/services`),

  // --- Profesionales ---
  getProfessionals: (slug: string, serviceId?: string) =>
    fetchApi<Professional[]>(
      `/public/businesses/${slug}/professionals${serviceId ? `?service_id=${serviceId}` : ''}`
    ),

  // --- Disponibilidad ---
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

  // --- Reservas ---
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

  // --- Onboarding ---
  registerBusiness: (data: BusinessRegisterRequest) =>
    fetchApi<BusinessRegisterResponse>('/auth/register-business', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /**
   * Chequeo de disponibilidad del nombre de URL.
   *
   * Devuelve `slug` ya normalizado, asi que el formulario muestra la URL que va a
   * quedar. Responde 200 con `disponible: false`--no 409--para que el formulario
   * pueda consultarlo en cada tecla sin manear errores.
   */
  checkSlug: (slug: string) =>
    fetchApi<SlugAvailability>(`/public/slug-disponible?slug=${encodeURIComponent(slug)}`),

  // --- Panel del negocio (Fase 4) ---
  //
  // Todos estos endpoints piden el token del dueno y viven
  // bajo `/business`, con la RLS del tenant puesta por el
  // token. Las listas devuelven solo activos; los archivados
  // se piden explicitamente con `incluir_archivados`.

  // --- Servicios ---
  listarServicios: () => fetchApi<PanelService[]>('/business/servicios'),

  crearServicio: (datos: ServicioCreateRequest) =>
    fetchApi<PanelService>('/business/servicios', {
      method: 'POST',
      body: JSON.stringify(datos),
    }),

  actualizarServicio: (serviceId: string, cambios: ServicioPatchRequest) =>
    fetchApi<PanelService>(`/business/servicios/${serviceId}`, {
      method: 'PATCH',
      body: JSON.stringify(cambios),
    }),

  /**
   * Archiva, no borra. Devuelve el servicio con `is_active: false`
   * y `archived_at` seteado.
   */
  archivarServicio: (serviceId: string) =>
    fetchApi<PanelService>(`/business/servicios/${serviceId}`, {
      method: 'DELETE',
    }),

  // --- Profesionales ---
  listarProfesionales: () => fetchApi<PanelProfessional[]>('/business/profesionales'),

  crearProfesional: (datos: ProfesionalCreateRequest) =>
    fetchApi<PanelProfessional>('/business/profesionales', {
      method: 'POST',
      body: JSON.stringify(datos),
    }),

  actualizarProfesional: (professionalId: string, cambios: ProfesionalPatchRequest) =>
    fetchApi<PanelProfessional>(`/business/profesionales/${professionalId}`, {
      method: 'PATCH',
      body: JSON.stringify(cambios),
    }),

  archivarProfesional: (professionalId: string) =>
    fetchApi<PanelProfessional>(`/business/profesionales/${professionalId}`, {
      method: 'DELETE',
    }),

  /** Servicios asignados a un profesional, con sus overrides. */
  listarServiciosDeProfesional: (professionalId: string) =>
    fetchApi<AsignacionServicio[]>(
      `/business/profesionales/${professionalId}/servicios`
    ),

  /**
   * Reemplaza el conjunto completo de servicios del profesional.
   *
   * Es un PUT de reemplazo, no un PATCH: lo que se manda es lo
   * que queda. Mandar la lista vacia desasigna todo.
   */
  asignarServicios: (professionalId: string, datos: AsignacionesRequest) =>
    fetchApi<AsignacionServicio[]>(
      `/business/profesionales/${professionalId}/servicios`,
      {
        method: 'PUT',
        body: JSON.stringify(datos),
      }
    ),

  // --- Horarios ---
  /** Horario semanal del negocio. */
  obtenerHorarios: () => fetchApi<SemanaHorario>('/business/horarios'),

  /** Reemplaza la semana entera. Lo que no se manda queda cerrado. */
  reemplazarHorarios: (semana: SemanaHorario) =>
    fetchApi<SemanaHorario>('/business/horarios', {
      method: 'PUT',
      body: JSON.stringify(semana),
    }),

  /**
   * Horario del profesional. `hereda: true` significa que sigue
   * el del negocio y `dias` es lo que heredaria (lo que devuelve
   * el GET del negocio).
   */
  obtenerHorariosProfesional: (professionalId: string) =>
    fetchApi<HorarioProfesional>(`/business/profesionales/${professionalId}/horarios`),

  reemplazarHorariosProfesional: (professionalId: string, semana: SemanaHorario) =>
    fetchApi<HorarioProfesional>(
      `/business/profesionales/${professionalId}/horarios`,
      {
        method: 'PUT',
        body: JSON.stringify(semana),
      }
    ),

  // --- Panel del profesional (Fase B) ---
  //
  // El backend resuelve el `professional_id` desde el token: un profesional
  // solo puede leer su propia agenda. `desde`/`hasta` son fechas locales del
  // negocio (YYYY-MM-DD) y delimitan el rango de `local_date`.

  /** Agenda propia en un rango de fechas locales (`desde`..`hasta` inclusive). */
  getMiAgenda: (desde: string, hasta: string) =>
    fetchApi<ReservaDePanel[]>(
      `/business/profesional/agenda?desde=${encodeURIComponent(desde)}&hasta=${encodeURIComponent(hasta)}`
    ),

  /**
   * Bloqueos del negocio. Sin `professionalId` devuelve todos (los generales y
   * los de cada profesional): el frontend distingue por `professional_id`.
   */
  getBloqueos: (professionalId?: string) =>
    fetchApi<BloqueoDePanel[]>(
      `/business/bloqueos${professionalId ? `?professional_id=${professionalId}` : ''}`
    ),

  /** Ausencias del negocio, opcionalmente filtradas por profesional. */
  getAusencias: (professionalId?: string) =>
    fetchApi<AusenciaDePanel[]>(
      `/business/ausencias${professionalId ? `?professional_id=${professionalId}` : ''}`
    ),

  // --- Panel admin (Fase C) ---
  //
  // Reservas filtrables, reprogramación, bloqueos/feriados/vacaciones y el
  // resumen estadístico. Requieren el token del admin (scopes `*_ANY`).

  /** Reservas del negocio filtrables. `q` busca por nombre o teléfono del cliente. */
  listarReservas: (filtros: ReservasFiltro) => {
    const params = new URLSearchParams()
    if (filtros.desde) params.set('desde', filtros.desde)
    if (filtros.hasta) params.set('hasta', filtros.hasta)
    if (filtros.professional_id) params.set('professional_id', filtros.professional_id)
    if (filtros.estado) params.set('estado', filtros.estado)
    if (filtros.q) params.set('q', filtros.q)
    if (filtros.limite) params.set('limite', String(filtros.limite))
    if (filtros.offset) params.set('offset', String(filtros.offset))
    return fetchApi<ReservaPagina>(`/business/reservas?${params.toString()}`)
  },

  /** Cancela una reserva desde el panel (sin el secure token del cliente). */
  cancelarReservaAdmin: (bookingId: string, motivo?: string) =>
    fetchApi<ReservaDePanel>(`/business/reservas/${bookingId}/cancelar`, {
      method: 'POST',
      body: JSON.stringify(motivo ? { motivo } : {}),
    }),

  /** Reprograma una reserva desde el panel (sin el secure token del cliente). */
  reprogramarReservaAdmin: (bookingId: string, datos: ReprogramarReservaRequest) =>
    fetchApi<ReservaDePanel>(`/business/reservas/${bookingId}/reprogramar`, {
      method: 'POST',
      body: JSON.stringify(datos),
    }),

  /** Crea un bloqueo. Sin `professional_id` es un cierre de todo el negocio. */
  crearBloqueo: (datos: BloqueoCreateRequest) =>
    fetchApi<BloqueoDePanel>('/business/bloqueos', {
      method: 'POST',
      body: JSON.stringify(datos),
    }),

  eliminarBloqueo: (blockId: string) =>
    fetchApi<never>(`/business/bloqueos/${blockId}`, { method: 'DELETE' }),

  listarFeriados: () => fetchApi<FeriadoDePanel[]>('/business/feriados'),

  crearFeriado: (datos: FeriadoCreateRequest) =>
    fetchApi<FeriadoDePanel>('/business/feriados', {
      method: 'POST',
      body: JSON.stringify(datos),
    }),

  eliminarFeriado: (holidayId: string) =>
    fetchApi<never>(`/business/feriados/${holidayId}`, { method: 'DELETE' }),

  crearAusencia: (datos: AusenciaCreateRequest) =>
    fetchApi<AusenciaDePanel>('/business/ausencias', {
      method: 'POST',
      body: JSON.stringify(datos),
    }),

  eliminarAusencia: (timeOffId: string) =>
    fetchApi<never>(`/business/ausencias/${timeOffId}`, { method: 'DELETE' }),

  /** Tarjetas del admin. Sin fechas, los últimos 30 días. */
  getEstadisticas: (desde?: string, hasta?: string) => {
    const params = new URLSearchParams()
    if (desde) params.set('desde', desde)
    if (hasta) params.set('hasta', hasta)
    const sufijo = params.toString() ? `?${params.toString()}` : ''
    return fetchApi<Estadisticas>(`/business/estadisticas${sufijo}`)
  },

  // --- Panel admin (Fase D) ---
  //
  // Historial del cliente: listado con totales (reservas, gasto, última
  // visita, profesional frecuente) y el detalle por cliente para el modal.

  /** Clientes con sus totales. `busqueda` matchea parcial nombre o teléfono. */
  listarClientes: (filtros: ClientesFiltro) => {
    const params = new URLSearchParams()
    if (filtros.busqueda) params.set('busqueda', filtros.busqueda)
    if (filtros.limite) params.set('limite', String(filtros.limite))
    if (filtros.offset) params.set('offset', String(filtros.offset))
    return fetchApi<ClienteDePanel[]>(`/business/clientes?${params.toString()}`)
  },

  /** Historial completo de un cliente: fecha, profesional, servicio, precio, estado. */
  historialCliente: (customerId: string, filtros?: { limite?: number; offset?: number }) => {
    const params = new URLSearchParams()
    if (filtros?.limite) params.set('limite', String(filtros.limite))
    if (filtros?.offset) params.set('offset', String(filtros.offset))
    const sufijo = params.toString() ? `?${params.toString()}` : ''
    return fetchApi<ReservaPagina>(`/business/clientes/${customerId}/reservas${sufijo}`)
  },

  // --- Reportes (Fase D-2) ---

  /**
   * Exporta las reservas de un mes. Devuelve la `Response` cruda a propósito:
   * el 404 es el "no hay datos" (mostrar "Sin datos", no descargar vacío) y el
   * resto de la respuesta es el archivo. Quien la usa lee el blob y el nombre
   * del `Content-Disposition`.
   */
  descargarReporteMensual: (filtros: {
    mes: string
    formato: 'csv' | 'xlsx'
    professional_id?: string
    estado?: string
  }): Promise<Response> => {
    const params = new URLSearchParams()
    params.set('mes', filtros.mes)
    params.set('formato', filtros.formato)
    if (filtros.professional_id) params.set('professional_id', filtros.professional_id)
    if (filtros.estado) params.set('estado', filtros.estado)
    return descargar(`/business/reportes/mensual?${params.toString()}`)
  },
}