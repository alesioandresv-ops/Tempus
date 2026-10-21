import { useEffect, useState } from 'react'
import { useNavigate, Link, useSearchParams } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { useMutation, useQuery } from '@tanstack/react-query'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Check, Loader2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { InputContrasena } from '@/components/ui/input-contrasena'
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
  // El mismo piso de 12 que el backend. Bajarlo aca solo haria que el formulario
  // acepte algo que el alta va a rechazar con un 422.
  password: z.string().min(12, 'La contrasena necesita al menos 12 caracteres').max(128),
  // Opcional. El vacio a `null`--y no a `''`-- se hace al construir el payload: el
  // backend distingue "no vine" de "viene vacio", y el segundo es un 422. Con un
  // `.transform()` en el esquema, el resolvedor de RHF devuelve el tipo de salida y
  // no el de entrada, y los dos se mezclan en el `handleSubmit`.
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
  /** 0 a 4: cuantos de los chequeos blandos se cumplen. */
  puntaje: number
  etiqueta: string
  /** Clases de color para la barra y el texto. */
  color: string
}

/**
 * Que tan fuerte es la contrasena escrita.
 *
 * **Cuenta variedad, no longitud.** El piso de 12 ya lo exige el esquema y el
 * backend; repetirlo en el medidor no informa nada. Lo que alguien no adivina es
 * que una clave de 12 caracteres puede ser `aaaaaaaaaaaa`, y ese es el caso que
 * este medidor existe para desalentar.
 *
 * Es **consejo, no una validacion**: no bloquea el envio. Convertir el medidor en
 * una puerta mas--"tu contrasena no es lo bastante fuerte"--seria inventarle al
 * usuario una regla que el backend no tiene, y le rompe el alta a quien elige una
 * clave correcta por otros motivos.
 */
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

  if (puntaje <= 2) return { puntaje, etiqueta: 'Débil', color: 'bg-red-500 text-red-600' }
  if (puntaje === 3) return { puntaje, etiqueta: 'Aceptable', color: 'bg-amber-500 text-amber-600' }
  if (puntaje === 4) return { puntaje, etiqueta: 'Buena', color: 'bg-lime-500 text-lime-600' }
  return { puntaje, etiqueta: 'Excelente', color: 'bg-green-600 text-green-700' }
}

export default function RegisterPage() {
  const navigate = useNavigate()
  const [parametros] = useSearchParams()
  const [slugParaConsultar, setSlugParaConsultar] = useState('')
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null)

  /**
   * Si la persona llego desde el CTA de Pro (`/register?plan=pro`).
   *
   * El pago se autoriza en el panel--`/admin?tab=pagos`--, no en una pasarela
   * publica: no hay una URL de Mercado Pago a la que mandar a alguien que todavia no
   * tiene cuenta. Lo que si se puede es no perder la intencion en el camino: se
   * arrastra hasta el login para que, despues de entrar, caiga directo en el paso de
   * pago en vez de tener que buscarlo.
   */
  const quierePro = parametros.get('plan') === 'pro'

  const {
    register,
    handleSubmit,
    watch,
    setError,
    formState: { errors },
  } = useForm<FormularioAlta>({
    resolver: zodResolver(esquema),
    defaultValues: { timezone: 'America/Argentina/Buenos_Aires' },
  })

  const slugEscrito = watch('slug')
  const contrasenaEscrita = watch('password') || ''

  /**
   * Debounce del slug.
   *
   * Va en un `useEffect` y no en la `queryKey` porque TanStack Query no debouncea:
   * sin esto, escribir "peluqueria" son once requests--y el endpoint tiene rate
   * limit--once letras, y la respuesta de la cuarta puede llegar despues de la de la
   * quinta y dejar el campo diciendo que un nombre que ya se libero sigue ocupado.
   * React Query descarta el resultado de una query que se cancela porque otra con
   * la misma clave la reemplazo, asi que el efecto se queda con `slugParaConsultar` y
   * la cancelacion la hace el.
   */
  useEffect(() => {
    const limpio = (slugEscrito || "").trim()
    if (limpio.length < LARGO_MINIMO_SLUG) {
      setSlugParaConsultar('')
      return
    }
    const temporizador = setTimeout(() => setSlugParaConsultar(limpio), ESPERA_SLUG_MS)
    return () => clearTimeout(temporizador)
  }, [slugEscrito])

  /**
   * Disponibilidad del nombre de URL.
   *
   * **`staleTime: 0` a proposito.** Es una comprobacion previa a un alta, no una
   * lectura: un nombre puede estar libre al mirarlo y tomado por otro alta al
   * segundo siguiente, asi que el resultado tiene que ser fresco antes de dejar
   * enviar.
   */
  const consultaSlug = useQuery({
    queryKey: ['slug-disponible', slugParaConsultar],
    queryFn: () => api.checkSlug(slugParaConsultar),
    enabled: slugParaConsultar.length >= LARGO_MINIMO_SLUG,
    staleTime: 0,
    refetchOnWindowFocus: true,
  })

  /** El nombre canonico tal como lo devuelve el servidor--acentos fuera, y todo. */
  const slugNormalizado = consultaSlug.data?.slug ?? ''
  /** El nombre esta tomado si el servidor lo dijo y todavia no se consulto otro. */
  const slugOcupado = consultaSlug.data?.disponible === false

  const altaMutation = useMutation({
    mutationFn: (datos: BusinessRegisterRequest) => api.registerBusiness(datos),
    onSuccess: () => {
      // No hay token que guardar: el alta no abre sesion, el dueno entra por
      // `/login`. El `state` le permite a esa pagina decir "tu negocio ya esta"
      // sin una consulta extra, y `irAPagar` le dice a donde mandarlo despues.
      navigate('/login', { state: { registrado: true, irAPagar: quierePro } })
    },
    onError: (error) => {
      // Un 422 trae el mensaje por campo--el unico que se puede poner al lado del
      // input-- y un 409 trae un `detail` que es del slug. Se tratan distinto
      // porque van a distinto lado de la pantalla.
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
    // Frena el envio si el chequeo en vivo ya sabe que el nombre no sirve. Es una
    // comodidad--el 409 igual llega-- pero convierte un error en un mensaje antes de
    // que la persona lo provoque.
    if (slugOcupado) {
      setError('slug', { message: 'Esa URL no esta disponible' })
      return
    }
    const telefono = (datos.phone || "").trim()
    altaMutation.mutate({ ...datos, phone: telefono ? telefono : null, turnstile_token: turnstileToken })
  })

  const fuerza = medirContrasena(contrasenaEscrita)

  /**
   * El dominio que se muestra en la vista previa.
   *
   * **Sale de `VITE_ROOT_DOMAIN`, no de un literal.** Antes decia `tempus.app` fijo:
   * en el despliegue real--que vive en otro dominio--la vista previa mostraba una URL
   * que no era la que quedaba, justo en el campo donde la persona esta decidiendo su
   * direccion publica. Sin la variable configurada cae al dominio actual del
   * navegador, que al menos es real.
   */
  const raiz = (import.meta.env.VITE_ROOT_DOMAIN as string | undefined) || window.location.host

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4 py-10">
      <Card className="w-full max-w-lg">
        <CardHeader className="space-y-1">
          <CardTitle className="text-2xl">Crear tu negocio</CardTitle>
          <CardDescription>
            Empezás gratis por un mes, sin tarjeta. Se crea con horarios de lunes a sábado y
            los cambiás cuando quieras.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={alEnviar} className="space-y-6" noValidate>
            {/* --- Tu negocio ------------------------------------------------ */}
            <fieldset className="space-y-4">
              <legend className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                Tu negocio
              </legend>

              <div className="space-y-2">
                <label htmlFor="business_name" className="text-sm font-medium text-gray-700">
                  Nombre del negocio
                </label>
                <Input
                  id="business_name"
                  autoComplete="organization"
                  placeholder="Peluquería El Peluche"
                  {...register('business_name')}
                />
                {errors.business_name && (
                  <p className="text-sm text-red-600">{errors.business_name.message}</p>
                )}
              </div>

              <div className="space-y-2">
                <label htmlFor="slug" className="text-sm font-medium text-gray-700">
                  Tu URL
                </label>
                <Input
                  id="slug"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="peluqueria-el-peluche"
                  {...register('slug')}
                />
                {/*
                  La vista previa usa el slug **devuelto por el servidor**, no lo
                  escrito: muestra la direccion que va a quedar de verdad, con los
                  acentos ya eliminados. Mientras no haya respuesta todavia se muestra
                  lo escrito, para que no parezca que el campo se vacio al tipear.
                */}
                <p className="text-xs text-gray-500 break-all">
                  {slugNormalizado ? (
                    <>
                      {raiz}/<span className="font-medium text-gray-700">{slugNormalizado}</span>
                    </>
                  ) : (
                    'Solo letras, números y guiones. Sin tildes.'
                  )}
                </p>
                {consultaSlug.isFetching && !slugOcupado && slugParaConsultar && (
                  <p className="flex items-center gap-1 text-xs text-gray-400">
                    <Loader2 className="h-3 w-3 animate-spin" /> Comprobando disponibilidad...
                  </p>
                )}
                {slugOcupado && (
                  <p className="flex items-center gap-1 text-xs text-red-600">
                    <X className="h-3 w-3" /> Esa URL no está disponible
                  </p>
                )}
                {slugNormalizado && !slugOcupado && !consultaSlug.isFetching && (
                  <p className="flex items-center gap-1 text-xs text-green-600">
                    <Check className="h-3 w-3" /> Disponible
                  </p>
                )}
                {errors.slug && <p className="text-sm text-red-600">{errors.slug.message}</p>}
              </div>
            </fieldset>

            {/* --- Tus datos -------------------------------------------------- */}
            <fieldset className="space-y-4 border-t border-gray-100 pt-6">
              <legend className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                Tus datos
              </legend>

              <div className="space-y-2">
                <label htmlFor="owner_name" className="text-sm font-medium text-gray-700">
                  Tu nombre
                </label>
                <Input
                  id="owner_name"
                  autoComplete="name"
                  placeholder="Ana Pérez"
                  {...register('owner_name')}
                />
                {errors.owner_name && (
                  <p className="text-sm text-red-600">{errors.owner_name.message}</p>
                )}
              </div>

              <div className="space-y-2">
                <label htmlFor="email" className="text-sm font-medium text-gray-700">
                  Email
                </label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  placeholder="tu@email.com"
                  {...register('email')}
                />
                <p className="text-xs text-gray-400">Con este email vas a entrar al panel.</p>
                {errors.email && <p className="text-sm text-red-600">{errors.email.message}</p>}
              </div>

              <div className="space-y-2">
                <label htmlFor="password" className="text-sm font-medium text-gray-700">
                  Contraseña
                </label>
                <InputContrasena
                  id="password"
                  autoComplete="new-password"
                  {...register('password')}
                />
                {/* El piso se muestra siempre--no solo al fallar-- porque 12 es una
                    regla que la persona no va a inventar. El medidor es consejo y no
                    bloquea: ver el comentario de `medirContrasena`. */}
                {contrasenaEscrita ? (
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-200">
                        <div
                          className={`h-full rounded-full transition-all ${fuerza.color.split(' ')[0]}`}
                          style={{ width: `${(fuerza.puntaje / 5) * 100}%` }}
                        />
                      </div>
                      <span className={`text-xs font-medium ${fuerza.color.split(' ')[1]}`}>
                        {fuerza.etiqueta}
                      </span>
                    </div>
                    {contrasenaEscrita.length < LARGO_MINIMO_CONTRASENA && (
                      <p className="text-xs text-gray-400">
                        Mínimo {LARGO_MINIMO_CONTRASENA} caracteres.
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="text-xs text-gray-400">
                    Mínimo {LARGO_MINIMO_CONTRASENA} caracteres. Mezclá mayúsculas, números y
                    símbolos.
                  </p>
                )}
                {errors.password && (
                  <p className="text-sm text-red-600">{errors.password.message}</p>
                )}
              </div>

              <div className="space-y-2">
                <label htmlFor="phone" className="text-sm font-medium text-gray-700">
                  Teléfono <span className="font-normal text-gray-400">(opcional)</span>
                </label>
                <Input
                  id="phone"
                  type="tel"
                  autoComplete="tel"
                  placeholder="+54 9 11 2345-6789"
                  {...register('phone')}
                />
                <p className="text-xs text-gray-400">
                  Con código de país, para poder enviarte WhatsApp.
                </p>
                {errors.phone && <p className="text-sm text-red-600">{errors.phone.message}</p>}
              </div>
            </fieldset>

            {/* --- Configuracion --------------------------------------------- */}
            <fieldset className="space-y-4 border-t border-gray-100 pt-6">
              <legend className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                Configuración
              </legend>

              {/*
                La zona horaria **no** es un select con las ~400 zonas de la IANA: se
                escribe. Un select seria mas lindo y peor--obligaria a elegir de una
                lista enorme la mayoria de las veces correcta, y el que esta mal elige
                una que existe pero no es la suya. Escribiendo se ve el nombre real de
                la zona, y el backend la valida contra `ZoneInfo`.
              */}
              <div className="space-y-2">
                <label htmlFor="timezone" className="text-sm font-medium text-gray-700">
                  Zona horaria
                </label>
                <Input id="timezone" {...register('timezone')} />
                <p className="text-xs text-gray-400">
                  Define a qué hora abren y cierran tus turnos. Podés cambiarla después.
                </p>
                {errors.timezone && (
                  <p className="text-sm text-red-600">{errors.timezone.message}</p>
                )}
              </div>
            </fieldset>

            {/* Sin sitekey configurada no renderiza nada; el backend tampoco exige el
                token, y los dos lados se prenden con la misma bandera. */}
            <Turnstile onVerify={setTurnstileToken} />

            {/* Un error que no es de un campo--500, o un 409 que no se pudo
                atribuir-- va arriba, no escondido al lado de un input. */}
            {altaMutation.isError && Object.keys(errors).length === 0 && (
              <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
                {altaMutation.error instanceof Error
                  ? altaMutation.error.message
                  : 'No se pudo completar el alta'}
              </p>
            )}

            <Button
              type="submit"
              className="w-full"
              size="lg"
              disabled={altaMutation.isPending || (turnstileHabilitado && !turnstileToken)}
            >
              {altaMutation.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Creando...
                </>
              ) : (
                'Crear negocio'
              )}
            </Button>

            <p className="text-center text-xs text-gray-400">
              Al crear tu negocio aceptás que te contactemos por email sobre tu cuenta.
            </p>
          </form>
        </CardContent>
        <div className="border-t border-gray-100 px-6 py-4 text-center text-sm text-gray-500">
          ¿Ya tenés cuenta?{' '}
          <Link to="/login" className="font-medium text-blue-600 hover:text-blue-500">
            Entrar
          </Link>
        </div>
      </Card>
    </div>
  )
}
