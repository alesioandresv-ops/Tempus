import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  CalendarCheck,
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  MessageCircle,
  Sparkles,
  Users2,
} from 'lucide-react'
import { api, ApiError } from '@/services/api'
import NegocioPausadoPage from './public/NegocioPausadoPage'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Turnstile, turnstileHabilitado } from '@/components/ui/Turnstile'
import { cn } from '@/lib/utils'
import type { Service, Professional, Slot, Booking } from '@/types'

/**
 * Página pública de reserva (`/:slug`).
 *
 * El diseño sigue los mockups aprobados (`docs/mockups/reserva-paso*.html`):
 * header sticky con la marca del negocio, stepper de 3 pasos — Servicio,
 * Día y hora, Tus datos — y una pantalla de confirmación con el enlace de gestión.
 *
 * La lógica de datos se conservó de la versión anterior: mismas queries, mismo
 * `createBooking`, mismos mensajes de error. Lo que cambió es la presentación y
 * que el profesional se elige con chips dentro del paso de fecha, no en un paso
 * propio: menos pasos, misma información.
 *
 * `Business` no trae `phone` en la respuesta pública actual, así que el acceso
 * es por `(business as { phone?: string | null }).phone ?? null` — si mañana el
 * backend lo agrega, el enlace de WhatsApp aparece solo.
 */

/** `YYYY-MM-DD` **local**. `toISOString()` es UTC y corre días para el oeste. */
function fechaLocal(date: Date): string {
  const mes = String(date.getMonth() + 1).padStart(2, '0')
  const dia = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${mes}-${dia}`
}

/** Pasos del wizard. El índice del array corresponde a `step - 1`. */
const PASOS_RESERVA = ['Servicio', 'Día y hora', 'Tus datos'] as const

/** Iniciales para el avatar del negocio (máximo dos letras). */
function inicialesDe(nombre: string): string {
  return (
    nombre
      .split(' ')
      .map((p) => p[0])
      .filter(Boolean)
      .slice(0, 2)
      .join('')
      .toUpperCase() || 'T'
  )
}

/** `YYYYMMDDTHHMMSSZ` para el enlace de Google Calendar. */
function fechaGoogle(date: Date): string {
  return date.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '')
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
  const [bookingResult, setBookingResult] = useState<Booking | null>(null)
  const [enviando, setEnviando] = useState(false)
  const [errorReserva, setErrorReserva] = useState<string | null>(null)
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null)
  const [enlaceCopiado, setEnlaceCopiado] = useState(false)
  const [mesOffset, setMesOffset] = useState(0)

  const {
    data: business,
    isLoading: cargandoNegocio,
    error: errorNegocio,
  } = useQuery({
    queryKey: ['business', slug],
    queryFn: () => api.getBusiness(slug!),
    enabled: !!slug,
    // Un negocio que no existe no se arregla reintentando: el 404 es la respuesta,
    // no un fallo transitorio.
    retry: false,
  })

  const { data: services } = useQuery({
    queryKey: ['services', slug],
    queryFn: () => api.getServices(slug!),
    enabled: !!slug,
  })

  const {
    data: professionals,
    isLoading: cargandoProfesionales,
    isError: errorProfesionales,
  } = useQuery({
    queryKey: ['professionals', slug, selectedService?.id],
    queryFn: () => api.getProfessionals(slug!, selectedService!.id),
    enabled: !!slug && !!selectedService,
  })

  /**
   * El servicio elegido **no lo puede hacer nadie**.
   *
   * La consulta viene filtrada por servicio, así que una lista vacía significa
   * exactamente eso. Antes la página ofrecía "cualquier profesional" y terminaba
   * en "no hay horarios", que no era lo que pasaba: el servicio no lo hacía nadie.
   */
  const sinProfesionales =
    !cargandoProfesionales && !errorProfesionales && professionals?.length === 0

  const { data: availability, isFetching: cargandoHorarios } = useQuery({
    queryKey: ['availability', slug, selectedService?.id, selectedDate, selectedProfessional?.id],
    queryFn: () =>
      api.getAvailability(slug!, selectedService!.id, selectedDate, selectedProfessional?.id),
    enabled: !!slug && !!selectedService && !!selectedDate,
  })

  const confirmar = async () => {
    if (!selectedService || !selectedSlot || !selectedDate || enviando) return
    // Con Turnstile montado no se manda sin su token: el backend responde 403 y
    // el viaje de ida y vuelta solo agrega un error que ya se sabe de antemano.
    if (turnstileHabilitado && !turnstileToken) {
      setErrorReserva('Completa la verificación anti-bot para confirmar.')
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
      setStep(4)
    } catch (error) {
      // Un 409 en este flujo es casi siempre "ese horario ya no está": el mensaje
      // del backend ya lo explica, así que se muestra tal cual.
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
   * Calendario del mes navegable: celdas vacías hasta el primer día (la semana
   * arranca el lunes) y un botón por día. Los días pasados se deshabilitan contra
   * el mínimo local, no UTC.
   *
   * **Va ANTES de los return condicionales de abajo.** React exige el mismo número
   * y orden de hooks en todos los renders: si este useMemo viviera después de un
   * `if (cargandoNegocio) return ...`, el render que pasa de "cargando" a "datos"
   * ejecutaría un hook más que el anterior y la página entera se desmonta en
   * silencio — pantalla vacía, sin error en consola.
   */
  const hoyIso = fechaLocal(new Date())

  const calendario = useMemo(() => {
    const base = new Date()
    base.setDate(1)
    base.setMonth(base.getMonth() + mesOffset)
    const anio = base.getFullYear()
    const mes = base.getMonth()
    const vacias = (new Date(anio, mes, 1).getDay() + 6) % 7
    const total = new Date(anio, mes + 1, 0).getDate()
    const celdas: (string | null)[] = Array.from({ length: vacias }, () => null)
    for (let dia = 1; dia <= total; dia++) {
      celdas.push(fechaLocal(new Date(anio, mes, dia)))
    }
    return {
      titulo: base.toLocaleDateString('es-AR', { month: 'long', year: 'numeric' }),
      celdas,
    }
  }, [mesOffset])

  const puedeRetrocederMes = mesOffset > 0

  if (cargandoNegocio) {
    return (
      <div className="relative flex min-h-screen items-center justify-center bg-[#070B1A] font-inter text-slate-100">
        <p className="text-slate-400" role="status">Cargando...</p>
      </div>
    )
  }

  if (errorNegocio || !business) {
    // El backend distingue "no existe" (404) de "pausado" (403 business_paused).
    // El segundo NO es un error: es un negocio real al que su dueño no le pagó
    // el plan, y merece su pantalla -- decirle al cliente que la URL esta mal
    // escrita seria mentir.
    if (errorNegocio instanceof ApiError && errorNegocio.status === 403) {
      const cuerpo = errorNegocio.body as { code?: string } | undefined
      if (cuerpo?.code === 'business_paused') return <NegocioPausadoPage />
    }
    const esNoEncontrado = errorNegocio instanceof ApiError && errorNegocio.status === 404
    return (
      <div className="relative flex min-h-screen items-center justify-center bg-[#070B1A] px-6 font-inter text-slate-100">
        <div className="w-full max-w-md animate-entra-arriba rounded-2xl border border-white/10 bg-[#0B1224]/90 p-8 text-center shadow-[0_0_60px_-20px_rgba(139,92,246,0.5)] backdrop-blur-xl">
          <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-violet-500/15">
            <Sparkles className="h-7 w-7 text-violet-300" />
          </div>
          <h1 className="mt-5 font-display text-2xl font-semibold tracking-tight text-white">
            {esNoEncontrado ? 'No encontramos esta página' : 'No pudimos cargar la página'}
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-slate-400">
            {esNoEncontrado
              ? 'El enlace puede estar mal escrito o el negocio ya no está disponible.'
              : 'Puede ser un problema de conexión. Probá recargar en un momento.'}
          </p>
          <div className="mt-6">
            <Button
              variant="outline"
              className="rounded-xl border-white/10 bg-transparent text-slate-200 hover:bg-white/5 hover:text-white"
              onClick={() => window.location.reload()}
            >
              Reintentar
            </Button>
          </div>
        </div>
      </div>
    )
  }

  //: `0,0` es "sin coordenadas" (la API nace con 0/null): no se ofrece el mapa
  //: para un negocio que no seteó su ubicación.
  const lat = business.latitude
  const lng = business.longitude
  const tieneUbicacion = lat != null && lng != null && lat !== 0 && lng !== 0

  const indiceActual = Math.min(Math.max(step - 1, 0), PASOS_RESERVA.length - 1)
  const progreso = indiceActual / Math.max(PASOS_RESERVA.length - 1, 1)

  /* Confirmación: la reserva recién creada trae todo lo que se muestra. */
  const enConfirmacion = step === 4 && bookingResult !== null
  const enlaceGestion = bookingResult ? `/r/${bookingResult.secure_token}` : ''
  const telefonoNegocio = (business as { phone?: string | null }).phone ?? null
  const enlaceGoogleCalendar = bookingResult
    ? `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${encodeURIComponent(
      `${bookingResult.service_name ?? 'Turno'} en ${business.name}`
    )}&dates=${fechaGoogle(new Date(bookingResult.starts_at))}/${fechaGoogle(
      new Date(bookingResult.ends_at)
    )}&location=${encodeURIComponent(business.address_text ?? '')}`
    : ''

  /* ---------- Pantalla de confirmación: página propia, sin stepper ---------- */
  if (enConfirmacion && bookingResult) {
    return (
      <div className="relative min-h-screen overflow-x-hidden bg-[#070B1A] font-inter text-slate-100">
        <div aria-hidden="true" className="pointer-events-none fixed inset-0 overflow-hidden">
          <div className="absolute -left-40 -top-40 h-[500px] w-[500px] rounded-full bg-violet-600/20 blur-[130px]" />
          <div className="absolute -bottom-40 right-0 h-[460px] w-[460px] rounded-full bg-cyan-500/15 blur-[140px]" />
          <div className="absolute left-1/2 top-1/3 h-[400px] w-[400px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-emerald-500/10 blur-[120px]" />
        </div>

        <main className="relative mx-auto max-w-lg px-5 pb-24 pt-14 sm:px-8">
          <section className="text-center">
            <div className="mx-auto grid h-24 w-24 place-items-center rounded-full bg-emerald-500/15 shadow-[0_0_60px_-10px_rgba(16,185,129,0.5)]">
              <svg width="52" height="52" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <circle cx="12" cy="12" r="10" stroke="#34d399" strokeWidth="2" />
                <path
                  d="M7.5 12.5l3 3 6-6.5"
                  stroke="#34d399"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            <h1 className="mt-8 font-display text-3xl font-semibold tracking-tight text-white sm:text-4xl">
              ¡Turno confirmado!
            </h1>
            <p className="mx-auto mt-3 max-w-sm text-sm text-slate-400">
              Te esperamos
              {bookingResult.customer_first_name ? `, ${bookingResult.customer_first_name}` : ''}.
              Guardá el enlace de abajo para ver, reprogramar o cancelar tu reserva.
            </p>
          </section>

          <section className="mt-10 rounded-2xl border border-white/10 bg-[#0B1224]/90 p-6 shadow-[0_0_50px_-20px_rgba(16,185,129,0.35)] backdrop-blur-xl sm:p-7">
            <div className="border-b border-white/10 pb-5">
              <p className="text-xs text-slate-500">Tu reserva</p>
              <p className="mt-1 font-display text-lg font-semibold text-white">{business.name}</p>
            </div>

            <div className="mt-5 space-y-4">
              <div className="flex items-start gap-4">
                <span className="mt-0.5 text-xl" aria-hidden="true">📅</span>
                <div>
                  <p className="text-sm font-semibold capitalize text-white">
                    {new Date(bookingResult.starts_at).toLocaleDateString('es-AR', {
                      weekday: 'long',
                      day: 'numeric',
                      month: 'long',
                    })}
                  </p>
                  <p className="text-sm text-slate-400">
                    {new Date(bookingResult.starts_at).toLocaleTimeString('es-AR', {
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                    {' — '}
                    {new Date(bookingResult.ends_at).toLocaleTimeString('es-AR', {
                      hour: '2-digit',
                      minute: '2-digit',
                    })}{' '}
                    hs
                  </p>
                  {bookingResult.business_timezone && (
                    <p className="mt-0.5 text-xs text-slate-500">
                      Horario del negocio ({bookingResult.business_timezone})
                    </p>
                  )}
                </div>
              </div>

              {bookingResult.service_name && (
                <div className="flex items-start gap-4">
                  <span className="mt-0.5 text-xl" aria-hidden="true">✂️</span>
                  <div>
                    <p className="text-sm font-semibold text-white">{bookingResult.service_name}</p>
                    {bookingResult.professional_name && (
                      <p className="text-sm text-slate-400">
                        con {bookingResult.professional_name}
                      </p>
                    )}
                  </div>
                  {bookingResult.price && (
                    <p className="ml-auto text-sm font-semibold text-white">
                      {bookingResult.price} {bookingResult.currency}
                    </p>
                  )}
                </div>
              )}

              {business.address_text && (
                <div className="flex items-start gap-4">
                  <span className="mt-0.5 text-xl" aria-hidden="true">📍</span>
                  <div>
                    <p className="text-sm font-semibold text-white">{business.name}</p>
                    <p className="text-sm text-slate-400">{business.address_text}</p>
                  </div>
                </div>
              )}
            </div>

            <div className="mt-6 grid grid-cols-2 gap-3 border-t border-white/10 pt-5">
              <a
                href={enlaceGoogleCalendar}
                target="_blank"
                rel="noreferrer"
                className="flex items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/5 py-2.5 text-sm font-medium text-slate-200 transition-colors hover:border-white/25 hover:text-white"
              >
                <CalendarCheck className="h-4 w-4" />
                Agendar
              </a>
              <button
                type="button"
                onClick={() => {
                  if (telefonoNegocio) {
                    window.open(
                      `https://wa.me/${telefonoNegocio.replace(/[^0-9]/g, '')}`,
                      '_blank'
                    )
                    return
                  }
                  navigator.clipboard
                    ?.writeText(`${window.location.origin}${enlaceGestion}`)
                    .then(() => setEnlaceCopiado(true))
                    .catch(() => setEnlaceCopiado(false))
                }}
                className="flex items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/5 py-2.5 text-sm font-medium text-slate-200 transition-colors hover:border-white/25 hover:text-white"
              >
                {telefonoNegocio ? (
                  <>
                    <MessageCircle className="h-4 w-4" />
                    WhatsApp
                  </>
                ) : (
                  <>
                    <Copy className="h-4 w-4" />
                    {enlaceCopiado ? 'Copiado' : 'Copiar enlace'}
                  </>
                )}
              </button>
            </div>
          </section>

          <div className="mt-8 space-y-3 text-center">
            <p className="text-xs text-slate-500">
              ¿No podés venir? Desde el enlace podés reprogramar o cancelar.
            </p>
            <a
              href={enlaceGestion}
              className="inline-block text-sm font-medium text-violet-300 hover:text-violet-200"
            >
              Gestionar mi reserva →
            </a>
          </div>
        </main>
      </div>
    )
  }

  /* ---------- Wizard de reserva ---------- */
  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[#070B1A] font-inter text-slate-100">
      <div aria-hidden="true" className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -left-40 -top-40 h-[500px] w-[500px] rounded-full bg-violet-600/20 blur-[130px]" />
        <div className="absolute -bottom-40 right-0 h-[460px] w-[460px] rounded-full bg-cyan-500/15 blur-[140px]" />
      </div>

      {/* Header sticky con la marca del negocio */}
      <header className="sticky top-0 z-20 border-b border-white/10 bg-[#070B1A]/80 backdrop-blur-lg">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-5 py-3.5 sm:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <span
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-fuchsia-500 font-display text-sm font-bold text-white"
              style={business.brand_color ? { background: business.brand_color } : undefined}
            >
              {inicialesDe(business.name)}
            </span>
            <div className="min-w-0">
              <p className="truncate font-display text-base font-semibold leading-tight text-white">
                {business.name}
              </p>
              <p className="text-[11px] text-slate-400">Reservá tu turno online</p>
            </div>
          </div>
          {tieneUbicacion && (
            <button
              type="button"
              onClick={() =>
                window.open(
                  `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`,
                  '_blank'
                )
              }
              className="hidden shrink-0 rounded-lg border border-white/10 bg-white/5 px-3.5 py-1.5 text-xs text-slate-200 hover:border-white/20 sm:block"
            >
              Ver cómo llegar
            </button>
          )}
        </div>
      </header>

      <main className="relative mx-auto max-w-5xl px-5 pb-24 pt-8 sm:px-8">
        <section className="text-center">
          <h1 className="font-display text-3xl font-semibold tracking-tight text-white sm:text-4xl">
            {step === 1 ? 'Reservá tu turno' : step === 2 ? 'Elegí día y hora' : 'Confirmá tu turno'}
          </h1>
          <p className="mx-auto mt-2.5 max-w-md text-sm text-slate-400">
            {step === 1
              ? 'Elegí servicio, día y hora. Te confirmamos al instante — sin llamadas ni esperas.'
              : step === 2
                ? selectedService
                  ? `${selectedService.name} · ${selectedService.duration_minutes} min`
                  : 'Primero elegí un servicio.'
                : 'Último paso: dejános tus datos y te confirmamos al instante.'}
          </p>
        </section>

        {/* Stepper */}
        <div className="mx-auto mt-8 flex max-w-lg items-center gap-2">
          {PASOS_RESERVA.map((titulo, i) => (
            <div key={titulo} className="contents">
              {i > 0 && <div className="mb-4 h-px flex-1 bg-white/15" aria-hidden="true" />}
              <div className="flex flex-1 flex-col items-center gap-1.5">
                <span
                  className={cn(
                    'flex h-8 w-8 items-center justify-center rounded-full font-display text-xs font-bold',
                    i < indiceActual
                      ? 'bg-emerald-500/20 text-emerald-300'
                      : i === indiceActual
                        ? 'bg-violet-500 text-white ring-4 ring-violet-500/25'
                        : 'border border-white/15 text-slate-400'
                  )}
                >
                  {i < indiceActual ? <Check className="h-4 w-4" /> : i + 1}
                </span>
                <span
                  className={cn(
                    'text-[11px]',
                    i === indiceActual ? 'font-medium text-white' : 'text-slate-500'
                  )}
                >
                  {titulo}
                </span>
              </div>
            </div>
          ))}
        </div>
        <div className="mx-auto mt-2 h-0.5 max-w-lg overflow-hidden rounded-full bg-white/5">
          <div
            className="h-full rounded-full bg-gradient-to-r from-violet-500 to-fuchsia-500 transition-all duration-500"
            style={{ width: `${progreso * 100}%` }}
          />
        </div>

        <div className="mx-auto mt-10 max-w-2xl">
          {/* ================= Paso 1: servicio ================= */}
          {step === 1 && (
            <section className="animate-entra-arriba">
              <h2 className="font-display text-lg font-semibold text-white">
                ¿Qué te gustaría hacer?
              </h2>
              <div className="mt-4 space-y-3">
                {(!services || services.length === 0) && (
                  <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 text-center">
                    <CalendarCheck className="mx-auto h-8 w-8 text-violet-400" />
                    <p className="mt-3 text-sm text-slate-300">
                      Todavía no publicamos servicios para este negocio.
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      Probá de nuevo más adelante o escribinos por Instagram.
                    </p>
                  </div>
                )}
                {services?.map((service) => (
                  <button
                    key={service.id}
                    type="button"
                    onClick={() => {
                      setSelectedService(service)
                      // Cambiar de servicio reencuadra la disponibilidad entera:
                      // profesional y horario elegidos dejan de corresponder.
                      setSelectedProfessional(null)
                      setSelectedSlot(null)
                      setStep(2)
                    }}
                    className="group flex w-full items-center gap-4 rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-left transition-all hover:border-violet-400/40 hover:bg-white/[0.06]"
                  >
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-white/5">
                      <CalendarCheck className="h-5 w-5 text-violet-300" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block font-medium text-white">{service.name}</span>
                      {service.description && (
                        <span className="mt-0.5 block truncate text-xs text-slate-400">
                          {service.duration_minutes} min · {service.description}
                        </span>
                      )}
                    </span>
                    <span className="shrink-0 text-right">
                      <span className="block font-display text-lg font-semibold text-white">
                        {service.price} {service.currency}
                      </span>
                      <span className="text-[11px] text-slate-500 group-hover:text-violet-300">
                        Elegir →
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}

          {/* ================= Paso 2: día y hora ================= */}
          {step === 2 && (
            <section className="animate-entra-arriba">
              {/* Chips de profesional */}
              {!cargandoProfesionales && !errorProfesionales && (
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedProfessional(null)
                      setSelectedSlot(null)
                    }}
                    className={cn(
                      'rounded-full border px-4 py-1.5 text-xs font-medium transition-all',
                      selectedProfessional === null
                        ? 'border-violet-400/40 bg-violet-500/15 text-violet-200'
                        : 'border-white/10 bg-white/5 text-slate-300 hover:border-white/25'
                    )}
                  >
                    <span className="inline-flex items-center gap-1.5">
                      <Users2 className="h-3.5 w-3.5" /> Cualquiera
                    </span>
                  </button>
                  {professionals?.map((prof) => (
                    <button
                      key={prof.id}
                      type="button"
                      onClick={() => {
                        setSelectedProfessional(prof)
                        setSelectedSlot(null)
                      }}
                      className={cn(
                        'rounded-full border px-4 py-1.5 text-xs font-medium transition-all',
                        selectedProfessional?.id === prof.id
                          ? 'border-violet-400/40 bg-violet-500/15 text-violet-200'
                          : 'border-white/10 bg-white/5 text-slate-300 hover:border-white/25'
                      )}
                    >
                      {prof.display_name}
                    </button>
                  ))}
                </div>
              )}

              {sinProfesionales && (
                <p className="mt-4 rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-sm text-slate-300">
                  Este servicio todavía no tiene ningún profesional que lo atienda, así que no hay
                  horarios para reservar.
                </p>
              )}

              {/* Calendario del mes */}
              {!sinProfesionales && (
                <div className="mt-5 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                  <div className="flex items-center justify-between">
                    <button
                      type="button"
                      onClick={() => puedeRetrocederMes && setMesOffset((m) => m - 1)}
                      disabled={!puedeRetrocederMes}
                      aria-label="Mes anterior"
                      className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 text-slate-400 hover:border-white/25 hover:text-white disabled:opacity-30"
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </button>
                    <p className="font-display text-sm font-semibold capitalize text-white">
                      {calendario.titulo}
                    </p>
                    <button
                      type="button"
                      onClick={() => setMesOffset((m) => m + 1)}
                      aria-label="Mes siguiente"
                      className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 text-slate-400 hover:border-white/25 hover:text-white"
                    >
                      <ChevronRight className="h-4 w-4" />
                    </button>
                  </div>

                  <div className="mt-4 grid grid-cols-7 gap-1 text-center">
                    {['lu', 'ma', 'mi', 'ju', 'vi', 'sá', 'do'].map((d) => (
                      <span
                        key={d}
                        className="text-[10px] font-medium uppercase tracking-wider text-slate-500"
                      >
                        {d}
                      </span>
                    ))}
                    {calendario.celdas.map((iso, i) =>
                      iso === null ? (
                        <span key={`v-${i}`} />
                      ) : (
                        <button
                          key={iso}
                          type="button"
                          disabled={iso < hoyIso}
                          onClick={() => {
                            setSelectedDate(iso)
                            setSelectedSlot(null)
                          }}
                          className={cn(
                            'mx-auto flex h-9 w-9 items-center justify-center rounded-lg text-xs transition-all',
                            iso < hoyIso
                              ? 'text-slate-600'
                              : selectedDate === iso
                                ? 'bg-violet-500 font-bold text-white shadow-[0_0_16px_-4px_rgba(139,92,246,0.7)]'
                                : iso === hoyIso
                                  ? 'font-medium text-white hover:bg-white/5'
                                  : 'text-slate-400 hover:bg-white/5'
                          )}
                        >
                          {Number(iso.slice(-2))}
                        </button>
                      )
                    )}
                  </div>
                </div>
              )}

              {/* Horarios del día elegido */}
              {!sinProfesionales && selectedDate && (
                <div className="mt-6">
                  <div className="flex items-center justify-between">
                    <h3 className="font-display text-base font-semibold capitalize text-white">
                      Horarios ·{' '}
                      {new Date(`${selectedDate}T12:00:00`).toLocaleDateString('es-AR', {
                        weekday: 'long',
                        day: 'numeric',
                        month: 'long',
                      })}
                    </h3>
                    {availability && availability.slots.length > 0 && (
                      <span className="rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-[10px] font-medium text-emerald-300">
                        {availability.slots.length} libres
                      </span>
                    )}
                  </div>

                  {cargandoHorarios ? (
                    <p className="mt-3 text-sm text-slate-400" role="status">
                      Buscando horarios...
                    </p>
                  ) : !availability || availability.slots.length === 0 ? (
                    <p className="mt-3 text-sm text-slate-400">
                      No hay horarios disponibles para esta fecha. Probá con otro día.
                    </p>
                  ) : (
                    <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-5">
                      {availability.slots.map((slot) => (
                        <button
                          key={slot.starts_at}
                          type="button"
                          onClick={() => setSelectedSlot(slot)}
                          className={cn(
                            'rounded-xl border py-2.5 text-sm font-medium tabular-nums transition-all',
                            selectedSlot?.starts_at === slot.starts_at
                              ? 'border-violet-400/40 bg-violet-500/15 font-semibold text-violet-200 shadow-[0_0_18px_-6px_rgba(139,92,246,0.6)]'
                              : 'border-white/10 bg-white/5 text-slate-300 hover:border-white/25 hover:bg-white/10 hover:text-white'
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
                  <p className="mt-3 text-xs text-slate-500">
                    Los horarios están en la zona horaria del negocio.
                  </p>
                </div>
              )}

              <div className="mt-8 flex gap-3">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setStep(1)}
                  className="flex-1 rounded-xl border-white/10 bg-transparent text-slate-300 hover:bg-white/5 hover:text-white"
                >
                  ← Cambiar servicio
                </Button>
                <Button
                  type="button"
                  onClick={() => setStep(3)}
                  disabled={!selectedSlot}
                  className="flex-1 rounded-xl bg-white py-2.5 font-semibold text-slate-900 transition-colors hover:bg-slate-200 disabled:opacity-50"
                >
                  Continuar
                </Button>
              </div>
            </section>
          )}

          {/* ================= Paso 3: datos del cliente ================= */}
          {step === 3 && (
            <section className="animate-entra-arriba">
              <div className="rounded-2xl border border-white/10 bg-[#0B1224]/90 p-6 backdrop-blur-xl sm:p-7">
                <h2 className="font-display text-lg font-semibold text-white">Tus datos</h2>
                <p className="mt-1 text-sm text-slate-400">
                  Solo lo necesario para confirmarte y avisarte si hay cambios.
                </p>

                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    void confirmar()
                  }}
                  className="mt-6 space-y-4"
                  noValidate
                >
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-2">
                      <label htmlFor="firstName" className="text-sm text-slate-300">
                        Nombre
                      </label>
                      <Input
                        id="firstName"
                        placeholder="Juan"
                        value={formData.firstName}
                        onChange={(e) => setFormData({ ...formData, firstName: e.target.value })}
                        required
                        className="border-white/10 bg-white/5 text-white placeholder:text-slate-500 focus-visible:ring-violet-400"
                      />
                    </div>
                    <div className="space-y-2">
                      <label htmlFor="lastName" className="text-sm text-slate-300">
                        Apellido
                      </label>
                      <Input
                        id="lastName"
                        placeholder="Perez"
                        value={formData.lastName}
                        onChange={(e) => setFormData({ ...formData, lastName: e.target.value })}
                        required
                        className="border-white/10 bg-white/5 text-white placeholder:text-slate-500 focus-visible:ring-violet-400"
                      />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <label htmlFor="phone" className="text-sm text-slate-300">
                      WhatsApp <span className="text-slate-500">(con código de país)</span>
                    </label>
                    <Input
                      id="phone"
                      type="tel"
                      placeholder="+54 9 11 2345-6789"
                      value={formData.phone}
                      onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
                      required
                      className="border-white/10 bg-white/5 text-white placeholder:text-slate-500 focus-visible:ring-violet-400"
                    />
                    <p className="text-xs text-slate-500">
                      Para el recordatorio del turno. Sin esto, el negocio no te avisa.
                    </p>
                  </div>

                  {/* Sin sitekey configurada no renderiza nada, y el backend tampoco
                      exige el token: los dos lados comparten la misma bandera. */}
                  <Turnstile onVerify={setTurnstileToken} />

                  {errorReserva && (
                    <p
                      role="alert"
                      className="rounded-lg border border-rose-400/20 bg-rose-500/10 p-3 text-sm text-rose-200"
                    >
                      {errorReserva}
                    </p>
                  )}

                  <button
                    type="submit"
                    disabled={enviando || (turnstileHabilitado && !turnstileToken)}
                    className="w-full rounded-xl bg-gradient-to-r from-violet-500 to-fuchsia-500 py-3 text-sm font-semibold text-white shadow-[0_0_30px_-8px_rgba(139,92,246,0.7)] transition-all hover:shadow-[0_0_40px_-8px_rgba(139,92,246,0.9)] disabled:opacity-50"
                  >
                    {enviando ? 'Confirmando...' : 'Confirmar turno'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setStep(2)}
                    disabled={enviando}
                    className="w-full rounded-xl py-1 text-sm text-slate-400 hover:text-slate-200"
                  >
                    ← Elegir otro horario
                  </button>
                </form>
              </div>
            </section>
          )}
        </div>
      </main>
    </div>
  )
}
