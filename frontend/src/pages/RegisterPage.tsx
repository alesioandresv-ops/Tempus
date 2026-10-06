import { useEffect, useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { useMutation, useQuery } from '@tanstack/react-query'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
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

export default function RegisterPage() {
  const navigate = useNavigate()
  const [slugParaConsultar, setSlugParaConsultar] = useState('')

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
    const limpio = slugEscrito.trim()
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
      // sin una consulta extra.
      navigate('/login', { state: { registrado: true } })
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
    const telefono = datos.phone?.trim()
    altaMutation.mutate({ ...datos, phone: telefono ? telefono : null })
  })

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4 py-8">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="text-2xl">Crear tu negocio</CardTitle>
          <CardDescription>
            Empezas con los horarios de lunes a sabado y los cambias cuando quieras.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={alEnviar} className="space-y-4" noValidate>
            <div className="space-y-2">
              <label htmlFor="business_name" className="text-sm text-gray-500">
                Nombre del negocio
              </label>
              <Input
                id="business_name"
                placeholder="Peluqueria El Peluche"
                {...register('business_name')}
              />
              {errors.business_name && (
                <p className="text-sm text-red-600">{errors.business_name.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <label htmlFor="slug" className="text-sm text-gray-500">
                Tu URL
              </label>
              <Input id="slug" placeholder="peluqueria-el-peluche" {...register('slug')} />
              {/*
                La vista previa usa el slug **devuelto por el servidor**, no lo
                escrito: muestra la direccion que va a quedar de verdad, con los
                acentos ya eliminados.
              */}
              <p className="text-xs text-gray-400">
                {slugNormalizado
                  ? `tempus.app/${slugNormalizado}`
                  : 'Solo letras, numeros y guiones. Sin tildes.'}
              </p>
              {consultaSlug.isFetching && !slugOcupado && (
                <p className="text-xs text-gray-400">Comprobando...</p>
              )}
              {slugOcupado && <p className="text-xs text-red-600">Esa URL no esta disponible</p>}
              {errors.slug && <p className="text-sm text-red-600">{errors.slug.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="owner_name" className="text-sm text-gray-500">
                Tu nombre
              </label>
              <Input id="owner_name" placeholder="Ana Perez" {...register('owner_name')} />
              {errors.owner_name && (
                <p className="text-sm text-red-600">{errors.owner_name.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <label htmlFor="email" className="text-sm text-gray-500">
                Email
              </label>
              <Input id="email" type="email" placeholder="tu@email.com" {...register('email')} />
              {errors.email && <p className="text-sm text-red-600">{errors.email.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="password" className="text-sm text-gray-500">
                Contrasena
              </label>
              <Input id="password" type="password" {...register('password')} />
              {/* El piso se muestra siempre--no solo al fallar-- porque 12 es una
                  regla que la persona no va a inventar. */}
              <p className="text-xs text-gray-400">Minimo 12 caracteres.</p>
              {errors.password && (
                <p className="text-sm text-red-600">{errors.password.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <label htmlFor="phone" className="text-sm text-gray-500">
                Telefono <span className="text-gray-400">(opcional)</span>
              </label>
              <Input
                id="phone"
                type="tel"
                placeholder="+54 9 11 2345-6789"
                {...register('phone')}
              />
              <p className="text-xs text-gray-400">
                Con codigo de pais, para poder enviarte WhatsApp.
              </p>
              {errors.phone && <p className="text-sm text-red-600">{errors.phone.message}</p>}
            </div>

            {/*
              La zona horaria **no** es un select con las ~400 zonas de la IANA: se
              escribe. Un select seria mas lindo y peor--obligaria a elegir de una
              lista enorme la mayoria de las veces correcta, y el que esta mal elige
              una que existe pero no es la suya. Escribiendo se ve el nombre real de
              la zona, y el backend la valida contra `ZoneInfo`.
            */}
            <div className="space-y-2">
              <label htmlFor="timezone" className="text-sm text-gray-500">
                Zona horaria
              </label>
              <Input id="timezone" {...register('timezone')} />
              {errors.timezone && (
                <p className="text-sm text-red-600">{errors.timezone.message}</p>
              )}
            </div>

            {/* Un error que no es de un campo--500, o un 409 que no se pudo
                atribuir-- va arriba, no escondido al lado de un input. */}
            {altaMutation.isError && Object.keys(errors).length === 0 && (
              <p className="text-sm text-red-600">
                {altaMutation.error instanceof Error
                  ? altaMutation.error.message
                  : 'No se pudo completar el alta'}
              </p>
            )}

            <Button type="submit" className="w-full" disabled={altaMutation.isPending}>
              {altaMutation.isPending ? 'Creando...' : 'Crear negocio'}
            </Button>
          </form>
        </CardContent>
        <div className="px-6 pb-6 text-center text-sm text-gray-500">
          <Link to="/login" className="text-blue-600 hover:text-blue-500">
            Ya tienes cuenta? Entrar
          </Link>
        </div>
      </Card>
    </div>
  )
}