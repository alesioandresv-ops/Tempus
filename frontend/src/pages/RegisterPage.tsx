import { useEffect, useState } from 'react'
import { useNavigate, Link, useSearchParams } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { useMutation, useQuery } from '@tanstack/react-query'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Globe,
  Loader2,
  Lock,
  Mail,
  MapPin,
  Phone,
  Sparkles,
  User,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { InputContrasena } from '@/components/ui/input-contrasena'
import { MarcaTempus } from '@/components/MarcaTempus'
import { Turnstile, turnstileHabilitado } from '@/components/ui/Turnstile'
import { api, ApiError } from '@/services/api'
import type { BusinessRegisterRequest } from '@/types'

/**
 * Alta self-service de un negocio.
 *
 * **El formulario valida con Zod y el servidor valida con Pydantic.** Es duplicado a
 * proposito: el del navegador evita un viaje de ida y vuelta por un nombre de dos
 * letras, y el del servidor es el unico en el que se puede confiar--el navegador no
 * es una frontera--. Los dos tienen que aceptar lo mismo, asi que los minimos de
 * abajo replican los `Field(...)` de `BusinessRegisterRequest`.
 *
 * **El slug se manda crudo y el navegador no lo normaliza.** No hay
 * `toLowerCase().replace(...)` en este archivo a proposito: la canonicalizacion--
 * acentos, mayusculas, espacios-- es una sola funcion, la `slugify` del servidor, y
 * una segunda implementacion en el navegador diverge apenas alguien agrega un caso.
 * El unico requisito del lado del cliente es que queden tres caracteres, que es lo
 * que hace util el chequeo en vivo.
 */
const esquema = z.object({
  business_name: z.string().min(2, 'El nombre del negocio es demasiado corto').max(200),
  slug: z.string().min(3, 'La URL necesita al menos 3 caracteres').max(50),
  owner_name: z.string().min(2, 'El nombre es demasiado corto').max(200),
  email: z.string().email('El correo no tiene un formato valido'),
  password: z.string().min(12, 'La contrasena necesita al menos 12 caracteres').max(128),
  phone: z.string().max(32, 'El telefono es demasiado largo').optional(),
  timezone: z.string().min(1, 'La zona horaria es obligatoria'),
})

type FormularioAlta = z.infer<typeof esquema>

/** Cuanto se espera antes de preguntarle al servidor por el nombre de URL. */
const ESPERA_SLUG_MS = 400

/** Menos que esto y el nombre canonico no se muestra, porque no hay nada que ver. */
const LARGO_MINIMO_SLUG = 3

/** El piso del backend, replicado aca para el medidor. No es una regla nuestra. */
const LARGO_MINIMO_CONTRASENA = 12

type NivelContrasena = {
  puntaje: number
  etiqueta: string
  color: string
}

function medirContrasena(valor: string): NivelContrasena {
  if (!valor) return { puntaje: 0, etiqueta: '', color: '' }

  const chequeos = [
    valor.length >= LARGO_MINIMO_CONTRASENA,
    /[a-z]/.test(valor) && /[A-Z]/.test(valor),
    /\d/.test(valor),
    /[^A-Za-z0-9]/.test(valor),
    new Set(valor).size >= valor.length / 2,
  ]
  const puntaje = chequeos.filter(Boolean).length

  if (puntaje <= 2) return { puntaje, etiqueta: 'Debil', color: 'bg-red-500 text-red-400' }
  if (puntaje === 3) return { puntaje, etiqueta: 'Aceptable', color: 'bg-amber-500 text-amber-400' }
  if (puntaje === 4) return { puntaje, etiqueta: 'Buena', color: 'bg-lime-500 text-lime-400' }
  return { puntaje, etiqueta: 'Excelente', color: 'bg-emerald-500 text-emerald-400' }
}

/** Los 3 pasos del alta. */
const PASOS = [
  { numero: 1, titulo: 'Tu negocio', descripcion: 'Nombre y direccion web' },
  { numero: 2, titulo: 'Tus datos', descripcion: 'Cuenta y contrasena' },
  { numero: 3, titulo: 'Configuracion', descripcion: 'Zona horaria y seguridad' },
] as const

export default function RegisterPage() {
  const navigate = useNavigate()
  const [parametros] = useSearchParams()
  const [slugParaConsultar, setSlugParaConsultar] = useState('')
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null)
  const [pasoActual, setPasoActual] = useState(1)

  const quierePro = parametros.get('plan') === 'pro'

  const {
    register,
    handleSubmit,
    watch,
    setError,
    trigger,
    formState: { errors },
  } = useForm<FormularioAlta>({
    resolver: zodResolver(esquema),
    defaultValues: { timezone: 'America/Argentina/Buenos_Aires' },
    mode: 'onBlur',
  })

  const slugEscrito = watch('slug')
  const contrasenaEscrita = watch('password') || ''
  const nombreNegocio = watch('business_name') || ''

  // Debounce del slug
  useEffect(() => {
    const limpio = (slugEscrito || '').trim()
    if (limpio.length < LARGO_MINIMO_SLUG) {
      setSlugParaConsultar('')
      return
    }
    const temporizador = setTimeout(() => setSlugParaConsultar(limpio), ESPERA_SLUG_MS)
    return () => clearTimeout(temporizador)
  }, [slugEscrito])

  const consultaSlug = useQuery({
    queryKey: ['slug-disponible', slugParaConsultar],
    queryFn: () => api.checkSlug(slugParaConsultar),
    enabled: slugParaConsultar.length >= LARGO_MINIMO_SLUG,
    staleTime: 0,
    refetchOnWindowFocus: true,
  })

  const slugNormalizado = consultaSlug.data?.slug ?? ''
  const slugOcupado = consultaSlug.data?.disponible === false

  const altaMutation = useMutation({
    mutationFn: (datos: BusinessRegisterRequest) => api.registerBusiness(datos),
    onSuccess: () => {
      navigate('/login', { state: { registrado: true, irAPagar: quierePro } })
    },
    onError: (error) => {
      if (!(error instanceof ApiError)) return
      const porCampo = error.fieldErrors
      const campos = Object.keys(porCampo)
      if (campos.length > 0) {
        for (const campo of campos) {
          setError(campo as keyof FormularioAlta, { message: porCampo[campo] })
        }
        return
      }
      if (error.status === 409) {
        setError('slug', { message: error.message })
      }
    },
  })

  const alEnviar = handleSubmit((datos) => {
    if (slugOcupado) {
      setError('slug', { message: 'Esa URL no esta disponible' })
      return
    }
    const telefono = (datos.phone || '').trim()
    altaMutation.mutate({ ...datos, phone: telefono ? telefono : null, turnstile_token: turnstileToken })
  })

  const fuerza = medirContrasena(contrasenaEscrita)

  /*
   * Progreso del stepper (0 → 100%) para la linea de conexion: los pasos ya
   * completados la llenan de violet a fuchsia. Es la misma formula que el
   * wizard de reservas, para que la sensacion de avance sea identica en toda
   * la app.
   */
  const indicePaso = Math.max(pasoActual - 1, 0)
  const progresoPaso = (indicePaso / Math.max(PASOS.length - 1, 1)) * 100

  const raiz = (import.meta.env.VITE_ROOT_DOMAIN as string | undefined) || window.location.host

  // Avanzar al siguiente paso (valida los campos del paso actual)
  const avanzarPaso = async () => {
    const camposPorPaso: Record<number, (keyof FormularioAlta)[]> = {
      1: ['business_name', 'slug'],
      2: ['owner_name', 'email', 'password'],
      3: ['timezone'],
    }
    const valido = await trigger(camposPorPaso[pasoActual])
    if (valido) setPasoActual((p) => Math.min(p + 1, 3))
  }

  const retrocederPaso = () => setPasoActual((p) => Math.max(p - 1, 1))

  // Clases reutilizables para inputs en tema oscuro
  const inputOscuro =
    'border-white/10 bg-white/5 text-white placeholder:text-slate-500 focus-visible:ring-violet-400 focus-visible:border-violet-400/50'

  // Iniciales del negocio para el preview
  const iniciales = nombreNegocio
    ? nombreNegocio
        .split(' ')
        .map((w) => w[0])
        .join('')
        .slice(0, 2)
        .toUpperCase()
    : 'T'

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[#070B1A] font-inter text-slate-100">
      {/* Fondo: auroras de color difuminadas */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -left-32 -top-40 h-[520px] w-[520px] rounded-full bg-violet-600/20 blur-[120px] animate-flotar-lento" />
        <div className="absolute -top-16 right-0 h-[460px] w-[460px] rounded-full bg-cyan-500/15 blur-[130px] animate-flotar-lento" />
        <div className="absolute left-1/3 top-64 h-[420px] w-[420px] rounded-full bg-fuchsia-600/15 blur-[140px]" />
      </div>

      <div className="relative mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-8 px-6 py-8 lg:flex-row lg:items-start lg:gap-12 lg:px-8">
        {/* ================================================================ COLUMNA IZQUIERDA */}
        <div className="flex flex-1 flex-col justify-center lg:max-w-xl lg:sticky lg:top-8">
          {/* Logo */}
          <div className="flex animate-entra-izq lg:justify-center">
            <MarcaTempus size={96} soloSimbolo={false} />
          </div>

          {/* Badge */}
          <span
            className="mt-6 inline-flex w-fit items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs font-medium text-violet-200 animate-entra-izq"
            style={{ animationDelay: '110ms' }}
          >
            <Sparkles className="h-3 w-3" aria-hidden="true" />
            Alta de negocio
          </span>

          {/* Titulo */}
          <h1
            className="mt-5 font-display text-3xl font-semibold leading-tight tracking-tight text-white sm:text-4xl animate-entra-izq"
            style={{ animationDelay: '220ms' }}
          >
            Tu pagina de reservas,
            <br />
            <span className="bg-gradient-to-r from-violet-400 via-fuchsia-400 to-cyan-300 bg-clip-text text-transparent">
              lista en 2 minutos
            </span>
          </h1>

          <p
            className="mt-4 max-w-md text-base leading-relaxed text-slate-400 animate-entra-izq"
            style={{ animationDelay: '330ms' }}
          >
            Crea tu negocio, configura tus horarios y comparte el link. Sin tarjeta, sin
            contratos, sin complicaciones.
          </p>

          {/* Beneficios */}
          <div
            className="mt-6 space-y-3 animate-entra-izq"
            style={{ animationDelay: '440ms' }}
          >
            {[
              'Primer mes gratis, sin pedir tarjeta',
              'Reservas online ilimitadas',
              'Recordatorios por WhatsApp automaticos',
              'Tu pagina propia con tu nombre y QR',
            ].map((beneficio) => (
              <div key={beneficio} className="flex items-center gap-3">
                <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-emerald-500/20">
                  <Check className="h-3 w-3 text-emerald-400" />
                </div>
                <span className="text-sm text-slate-300">{beneficio}</span>
              </div>
            ))}
          </div>

          {/* Preview en vivo de la pagina publica */}
          <div
            className="mt-8 hidden animate-entra-izq sm:block"
            style={{ animationDelay: '550ms' }}
          >
            <p className="mb-3 text-xs font-medium uppercase tracking-wider text-slate-500">
              Vista previa de tu pagina
            </p>
            <div className="overflow-hidden rounded-2xl border border-white/10 bg-white/[0.03] backdrop-blur-sm">
              {/* Barra superior del preview */}
              <div className="flex items-center gap-2 border-b border-white/5 px-4 py-2.5">
                <div className="flex gap-1.5">
                  <div className="h-2.5 w-2.5 rounded-full bg-rose-500/60" />
                  <div className="h-2.5 w-2.5 rounded-full bg-amber-500/60" />
                  <div className="h-2.5 w-2.5 rounded-full bg-emerald-500/60" />
                </div>
                <div className="ml-3 flex-1 rounded-md bg-white/5 px-3 py-1 text-[10px] text-slate-500">
                  {raiz}/{slugNormalizado || 'tu-negocio'}
                </div>
              </div>
              {/* Contenido del preview */}
              <div className="p-5">
                <div className="flex items-center gap-3">
                  <div
                    className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl text-lg font-bold text-white shadow-lg"
                    style={{ backgroundColor: '#7C3AED' }}
                  >
                    {iniciales}
                  </div>
                  <div>
                    <h3 className="text-sm font-semibold text-white">
                      {nombreNegocio || 'Nombre del negocio'}
                    </h3>
                    <p className="text-xs text-slate-400">
                      {slugNormalizado
                        ? `${raiz}/${slugNormalizado}`
                        : 'tu-url-aqui'}
                    </p>
                  </div>
                </div>
                <div className="mt-4 space-y-2">
                  {[
                    { nombre: 'Corte de cabello', duracion: '45 min', precio: '$9.000' },
                    { nombre: 'Barba', duracion: '30 min', precio: '$5.000' },
                  ].map((s) => (
                    <div
                      key={s.nombre}
                      className="flex items-center justify-between rounded-lg border border-white/5 bg-white/[0.02] px-3 py-2"
                    >
                      <div>
                        <p className="text-xs font-medium text-slate-200">{s.nombre}</p>
                        <p className="text-[10px] text-slate-500">{s.duracion}</p>
                      </div>
                      <span className="text-xs font-semibold text-violet-300">{s.precio}</span>
                    </div>
                  ))}
                </div>
                <div className="mt-4 flex items-center justify-center rounded-lg bg-gradient-to-r from-violet-600 to-fuchsia-600 py-2 text-xs font-semibold text-white">
                  Reservar turno
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* ================================================================ COLUMNA DERECHA */}
        <div className="flex w-full flex-col justify-center lg:max-w-md">
          <div
            className="w-full animate-entra-arriba rounded-2xl border border-white/10 bg-[#0B1224]/90 p-7 shadow-[0_0_60px_-20px_rgba(139,92,246,0.5)] backdrop-blur-xl sm:p-8"
            style={{ animationDelay: '180ms' }}
          >
            {/* Header */}
            <div className="mb-6">
              <h2 className="font-display text-2xl font-semibold tracking-tight text-white">
                Crear tu negocio
              </h2>
              <p className="mt-1.5 text-sm text-slate-400">
                {quierePro
                  ? 'Despues de crear tu cuenta, te llevamos al checkout PRO.'
                  : 'Empezas gratis por 14 dias, sin tarjeta.'}
              </p>
            </div>

            {/* Stepper */}
            <div className="mb-7">
              <ol className="relative flex items-start">
                <div aria-hidden="true" className="absolute left-0 right-0 top-4 h-0.5 bg-white/10" />
                <div
                  aria-hidden="true"
                  className="absolute left-0 top-4 h-0.5 rounded-full bg-gradient-to-r from-violet-500 to-fuchsia-500 transition-all duration-500"
                  style={{ width: `${progresoPaso}%` }}
                />
                {PASOS.map((paso) => {
                  const completo = pasoActual > paso.numero
                  const actual = pasoActual === paso.numero
                  return (
                    <li
                      key={paso.numero}
                      className="relative z-10 flex flex-1 flex-col items-center gap-2"
                    >
                      <div
                        className={`flex h-8 w-8 items-center justify-center rounded-full border text-xs font-bold transition-all duration-300 ${
                          completo
                            ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300'
                            : actual
                              ? 'border-transparent bg-gradient-to-br from-violet-500 to-fuchsia-500 text-white shadow-lg shadow-violet-500/30'
                              : 'border-white/10 bg-white/5 text-slate-500'
                        }`}
                      >
                        {completo ? <Check className="h-4 w-4 text-emerald-300" /> : paso.numero}
                      </div>
                      <span
                        className={`text-center text-[10px] font-medium leading-tight ${
                          completo || actual ? 'text-violet-300' : 'text-slate-500'
                        }`}
                      >
                        {paso.titulo}
                      </span>
                    </li>
                  )
                })}
              </ol>
            </div>

            <form onSubmit={alEnviar} noValidate>
              {/* ============================================ PASO 1: TU NEGOCIO */}
              {pasoActual === 1 && (
                <div className="space-y-5 animate-entra-arriba">
                  <div className="space-y-2">
                    <label htmlFor="business_name" className="flex items-center gap-2 text-sm font-medium text-slate-300">
                      <Sparkles className="h-3.5 w-3.5 text-violet-400" />
                      Nombre del negocio
                    </label>
                    <Input
                      id="business_name"
                      autoComplete="organization"
                      placeholder="Peluqueria El Peluche"
                      className={inputOscuro}
                      {...register('business_name')}
                    />
                    {errors.business_name && (
                      <p className="flex items-center gap-1.5 text-sm text-rose-400">
                        <X className="h-3.5 w-3.5" /> {errors.business_name.message}
                      </p>
                    )}
                  </div>

                  <div className="space-y-2">
                    <label htmlFor="slug" className="flex items-center gap-2 text-sm font-medium text-slate-300">
                      <Globe className="h-3.5 w-3.5 text-violet-400" />
                      Tu URL
                    </label>
                    <div className="relative">
                      <Input
                        id="slug"
                        autoComplete="off"
                        spellCheck={false}
                        placeholder="peluqueria-el-peluche"
                        className={`${inputOscuro} pr-10`}
                        {...register('slug')}
                      />
                      <div className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2">
                        {consultaSlug.isFetching && slugParaConsultar ? (
                          <Loader2 className="h-4 w-4 animate-spin text-slate-500" />
                        ) : slugNormalizado && !slugOcupado ? (
                          <Check className="h-4 w-4 text-emerald-400" />
                        ) : slugOcupado ? (
                          <X className="h-4 w-4 text-rose-400" />
                        ) : null}
                      </div>
                    </div>
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-slate-500">
                        {slugNormalizado ? (
                          <>
                            {raiz}/<span className="font-medium text-violet-300">{slugNormalizado}</span>
                          </>
                        ) : (
                          'Solo letras, numeros y guiones. Sin tildes.'
                        )}
                      </span>
                      {slugOcupado && (
                        <span className="text-rose-400">No disponible</span>
                      )}
                      {slugNormalizado && !slugOcupado && !consultaSlug.isFetching && (
                        <span className="text-emerald-400">Disponible</span>
                      )}
                    </div>
                    {errors.slug && (
                      <p className="flex items-center gap-1.5 text-sm text-rose-400">
                        <X className="h-3.5 w-3.5" /> {errors.slug.message}
                      </p>
                    )}
                  </div>

                  <Button
                    type="button"
                    onClick={avanzarPaso}
                    className="w-full rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-600 py-2.5 font-semibold text-white shadow-lg shadow-violet-500/25 transition-all hover:shadow-xl hover:shadow-violet-500/40 hover:-translate-y-0.5"
                  >
                    Continuar
                    <ArrowRight className="ml-2 h-4 w-4" />
                  </Button>
                </div>
              )}

              {/* ============================================ PASO 2: TUS DATOS */}
              {pasoActual === 2 && (
                <div className="space-y-5 animate-entra-arriba">
                  <div className="space-y-2">
                    <label htmlFor="owner_name" className="flex items-center gap-2 text-sm font-medium text-slate-300">
                      <User className="h-3.5 w-3.5 text-violet-400" />
                      Tu nombre
                    </label>
                    <Input
                      id="owner_name"
                      autoComplete="name"
                      placeholder="Ana Perez"
                      className={inputOscuro}
                      {...register('owner_name')}
                    />
                    {errors.owner_name && (
                      <p className="flex items-center gap-1.5 text-sm text-rose-400">
                        <X className="h-3.5 w-3.5" /> {errors.owner_name.message}
                      </p>
                    )}
                  </div>

                  <div className="space-y-2">
                    <label htmlFor="email" className="flex items-center gap-2 text-sm font-medium text-slate-300">
                      <Mail className="h-3.5 w-3.5 text-violet-400" />
                      Email
                    </label>
                    <Input
                      id="email"
                      type="email"
                      autoComplete="email"
                      placeholder="tu@email.com"
                      className={inputOscuro}
                      {...register('email')}
                    />
                    <p className="text-xs text-slate-500">Con este email vas a entrar al panel.</p>
                    {errors.email && (
                      <p className="flex items-center gap-1.5 text-sm text-rose-400">
                        <X className="h-3.5 w-3.5" /> {errors.email.message}
                      </p>
                    )}
                  </div>

                  <div className="space-y-2">
                    <label htmlFor="password" className="flex items-center gap-2 text-sm font-medium text-slate-300">
                      <Lock className="h-3.5 w-3.5 text-violet-400" />
                      Contrasena
                    </label>
                    <InputContrasena
                      id="password"
                      autoComplete="new-password"
                      className={inputOscuro}
                      {...register('password')}
                    />
                    {contrasenaEscrita ? (
                      <div className="space-y-1.5">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
                            <div
                              className={`h-full rounded-full transition-all duration-500 ${fuerza.color.split(' ')[0]}`}
                              style={{ width: `${(fuerza.puntaje / 5) * 100}%` }}
                            />
                          </div>
                          <span className={`text-xs font-medium ${fuerza.color.split(' ')[1]}`}>
                            {fuerza.etiqueta}
                          </span>
                        </div>
                        {contrasenaEscrita.length < LARGO_MINIMO_CONTRASENA && (
                          <p className="text-xs text-slate-500">
                            Minimo {LARGO_MINIMO_CONTRASENA} caracteres. Mezcla mayusculas, numeros y simbolos.
                          </p>
                        )}
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500">
                        Minimo {LARGO_MINIMO_CONTRASENA} caracteres. Mezcla mayusculas, numeros y simbolos.
                      </p>
                    )}
                    {errors.password && (
                      <p className="flex items-center gap-1.5 text-sm text-rose-400">
                        <X className="h-3.5 w-3.5" /> {errors.password.message}
                      </p>
                    )}
                  </div>

                  <div className="space-y-2">
                    <label htmlFor="phone" className="flex items-center gap-2 text-sm font-medium text-slate-300">
                      <Phone className="h-3.5 w-3.5 text-violet-400" />
                      Telefono <span className="font-normal text-slate-500">(opcional)</span>
                    </label>
                    <Input
                      id="phone"
                      type="tel"
                      autoComplete="tel"
                      placeholder="+54 9 11 2345-6789"
                      className={inputOscuro}
                      {...register('phone')}
                    />
                    <p className="text-xs text-slate-500">Con codigo de pais, para WhatsApp.</p>
                    {errors.phone && (
                      <p className="flex items-center gap-1.5 text-sm text-rose-400">
                        <X className="h-3.5 w-3.5" /> {errors.phone.message}
                      </p>
                    )}
                  </div>

                  <div className="flex gap-3">
                    <Button
                      type="button"
                      onClick={retrocederPaso}
                      variant="ghost"
                      className="flex-1 rounded-xl border border-white/10 text-slate-300 hover:bg-white/5"
                    >
                      <ArrowLeft className="mr-2 h-4 w-4" />
                      Atras
                    </Button>
                    <Button
                      type="button"
                      onClick={avanzarPaso}
                      className="flex-1 rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-600 py-2.5 font-semibold text-white shadow-lg shadow-violet-500/25 transition-all hover:shadow-xl hover:shadow-violet-500/40 hover:-translate-y-0.5"
                    >
                      Continuar
                      <ArrowRight className="ml-2 h-4 w-4" />
                    </Button>
                  </div>
                </div>
              )}

              {/* ============================================ PASO 3: CONFIGURACION */}
              {pasoActual === 3 && (
                <div className="space-y-5 animate-entra-arriba">
                  <div className="space-y-2">
                    <label htmlFor="timezone" className="flex items-center gap-2 text-sm font-medium text-slate-300">
                      <MapPin className="h-3.5 w-3.5 text-violet-400" />
                      Zona horaria
                    </label>
                    <Input
                      id="timezone"
                      placeholder="America/Argentina/Buenos_Aires"
                      className={inputOscuro}
                      {...register('timezone')}
                    />
                    <p className="text-xs text-slate-500">
                      Define a que hora abren y cierran tus turnos. Podes cambiarla despues.
                    </p>
                    {errors.timezone && (
                      <p className="flex items-center gap-1.5 text-sm text-rose-400">
                        <X className="h-3.5 w-3.5" /> {errors.timezone.message}
                      </p>
                    )}
                  </div>

                  <Turnstile onVerify={setTurnstileToken} />

                  {altaMutation.isError && Object.keys(errors).length === 0 && (
                    <p className="rounded-lg border border-rose-400/20 bg-rose-500/10 p-3 text-sm text-rose-200">
                      {altaMutation.error instanceof Error
                        ? altaMutation.error.message
                        : 'No se pudo completar el alta'}
                    </p>
                  )}

                  <div className="flex gap-3">
                    <Button
                      type="button"
                      onClick={retrocederPaso}
                      variant="ghost"
                      className="flex-1 rounded-xl border border-white/10 text-slate-300 hover:bg-white/5"
                    >
                      <ArrowLeft className="mr-2 h-4 w-4" />
                      Atras
                    </Button>
                    <Button
                      type="submit"
                      className="flex-1 rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-600 py-2.5 font-semibold text-white shadow-lg shadow-violet-500/25 transition-all hover:shadow-xl hover:shadow-violet-500/40 hover:-translate-y-0.5"
                      disabled={altaMutation.isPending || (turnstileHabilitado && !turnstileToken)}
                    >
                      {altaMutation.isPending ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Creando...
                        </>
                      ) : (
                        <>
                          Crear negocio
                          <Sparkles className="ml-2 h-4 w-4" />
                        </>
                      )}
                    </Button>
                  </div>

                  <p className="text-center text-xs text-slate-500">
                    Al crear tu negocio aceptas que te contactemos por email sobre tu cuenta.
                  </p>
                </div>
              )}
            </form>
          </div>

          {/* Footer */}
          <div className="mt-5 text-center text-sm text-slate-500">
            Ya tenes cuenta?{' '}
            <Link to="/login" className="font-medium text-violet-300 hover:text-violet-200">
              Entrar
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}
