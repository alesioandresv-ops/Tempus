import { useEffect, useState } from 'react'
import { useNavigate, useLocation, Link } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { useMutation } from '@tanstack/react-query'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { api, ApiError, guardarAccessToken } from '@/services/api'
import type { LoginRequest } from '@/types'

/**
 * Login del panel del negocio.
 *
 * **El formulario valida con Zod y el servidor valida con Pydantic**, igual que en el
 * alta: el del navegador evita un viaje por un campo de dos letras, y el del servidor
 * es el unico en el que se puede confiar. Lo que aca se valida es lo que no tiene
 * sentido enviar: correo con forma de correo, y una contrasena que no este vacia.
 *
 * **No se pone un minimo de 12 a la contrasena.** El alta si lo tiene--son reglas
 * distintas-- y bajarlo aca no haria nada: un login con una contrasena de 3 caracteres
 * es una credencial vieja o mal escrita, y el 401 del servidor lo dice sin que haga
 * falta anticiparlo. Poner el piso aca solo haria que una contrasena correcta de otra
 * epoca no se pueda escribir.
 */
const esquema = z.object({
  email: z.string().email('El correo no tiene un formato valido'),
  password: z.string().min(1, 'Falta la contrasena'),
  /**
   * El slug se manda **crudo y sin normalizar**, igual que en el alta: la
   * canonicalizacion es una sola funcion, la del servidor. Ademas el backend lo ignora
   * cuando hay una sola membresia, asi que un error de tipeo aqui no rompe el login.
   */
  business_slug: z.string().max(50, 'La URL es demasiado larga').optional(),
})

type FormularioLogin = z.infer<typeof esquema>

/**
 * El subdominio del que salio la persona, si la app esta montada bajo subdominios.
 *
 * **Hoy no hay subdominios**, asi que esto casi siempre devuelve cadena vacia y el
 * campo queda visible. Esta ahi para que, el dia que `peluqueria.tempus.app` exista, el
 * login ya sepa a que negocio se refiere sin pedirlo. Lo que **no** hace es decidir por
 * la URL a que panel ir: eso lo decide el token, y usar el subdominio para eso seria
 * confiar en algo que el cliente controla.
 */
function subdominioDelNavegador(): string {
  const host = window.location.hostname
  const raiz = import.meta.env.VITE_ROOT_DOMAIN as string | undefined
  if (!raiz || !host.endsWith(raiz)) return ''
  const prefijo = host.slice(0, -raiz.length).replace(/\.$/, '')
  return prefijo && !prefijo.includes('.') ? prefijo : ''
}

/**
 * Lo que se muestra cuando el login falla.
 *
 * **Todos los fallos de credenciales se muestran igual.** El backend responde el mismo
 * 401 para "no existe", "contrasena mala", "esta invitado" y "el slug no es el tuyo", y
 * por mucho que el formulario invente un mensaje distinto, ese 401 es lo unico que ve
 * la persona--no hay forma de que sepa cual de los cuatro casos fue. Un texto mas
 * especifico seria una promesa que el servidor no esta haciendo.
 *
 * El 429 si se distingue, y por el motivo contrario: es el unico caso donde la persona
 * **puede actuar**--esperar-- y donde `Retry-After` le dice cuanto. Es tambien el unico
 * 401/429 que no habla de la contrasena, asi que mezclarlo con los otros solo haria
 * que alguien que esta siendo limitado piense que se equivoco de clave.
 */
function mensajeDeError(error: unknown, slugProvisto: boolean): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      const espera = error.retryAfter ?? Number(error.body.retry_after ?? 0)
      return espera > 0
        ? `Demasiados intentos. Proba de nuevo en ${espera} segundos.`
        : 'Demasiados intentos. Proba de nuevo en un momento.'
    }
    if (error.status === 401) {
      const base = 'Credenciales invalidas. Revisa el correo y la contrasena.'
      /*
       * El backend devuelve el mismo 401 para contrasena mala y para
       * `membresia_ambigua`--el correo esta en dos negocios y no vino slug-- y no
       * lo distingue a proposito (un enumerador de cuentas vale mas que la pista
       * de UX). Lo unico que puede hacer el formulario es, cuando NO vino slug,
       * recordar que dos negocios requieren el campo: la unica causa del 401 que
       * se arregla escribiendo en un input distinto.
       */
      return slugProvisto
        ? base
        : `${base} Si el mismo correo trabaja en dos negocios, completa "Tu URL".`
    }
    if (error.status === 403) {
      return 'El navegador no puede hablar con la API. Revisa la configuracion de CORS.'
    }
    return error.message || 'No se pudo iniciar sesion.'
  }
  return 'No se pudo conectar con la API. Revisa que el servidor este arriba.'
}

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [slugSugerido] = useState(subdominioDelNavegador)
  /*
   * Cuenta atras del 429. Arranca en epoch ms; `0` significa "no hay bloqueo".
   * No se guarda el numero de segundos y ya: guardarlo congela el mensaje en
   * "60 segundos" para siempre, porque nada lo decrementa salvo un reintento.
   */
  const [bloqueadoHasta, setBloqueadoHasta] = useState(0)
  const [restantes, setRestantes] = useState(0)
  /* Slug que se mando en el ultimo intento: lo usa el mensaje del 401 para saber
     si falta la pista de `membresia_ambigua`. */
  const [slugDelIntento, setSlugDelIntento] = useState<string | null>(null)

  useEffect(() => {
    if (bloqueadoHasta === 0) return
    const tick = () => {
      const quedan = Math.ceil((bloqueadoHasta - Date.now()) / 1000)
      if (quedan <= 0) {
        setBloqueadoHasta(0)
        setRestantes(0)
      } else {
        setRestantes(quedan)
      }
    }
    tick()
    const id = window.setInterval(tick, 500)
    return () => window.clearInterval(id)
  }, [bloqueadoHasta])

  /*
   * El alta manda `state: { registrado: true }` al terminar el `POST`. El aviso importa
   * porque el alta **no devuelve token**--el dueno entra por aca-- y sin el, la pagina
   * seria indistinguible de la de siempre: no sabria si el negocio se creo o si lo perdio
   * en el camino de vuelta. Vive en el `state` y no en un store porque es un dato de una
   * sola navegacion: al recargar desaparece, y es lo correcto.
   *
   * `from` lo deja `ProtectedRoute` cuando echa a alguien que estaba en el panel.
   */
  const recienRegistrado = (location.state as { registrado?: boolean } | null)?.registrado === true
  const destino = (location.state as { from?: string } | null)?.from ?? '/admin'

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormularioLogin>({
    resolver: zodResolver(esquema),
    defaultValues: { business_slug: slugSugerido },
  })

  const loginMutation = useMutation({
    mutationFn: (datos: LoginRequest) => api.login(datos),
    onSuccess: async (respuesta) => {
      /*
       * El token va a memoria--`guardarAccessToken` lo deja en una variable de modulo de
       * `api.ts`-- y el refresh se queda en la cookie `HttpOnly` que el backend acaba de
       * poner. Nada de esto se guarda en `localStorage`: un XSS lee las dos.
       *
       * Se ignora `respuesta.expires_in` a proposito. El unico uso razonable del valor
       * seria un temporizador de renovacion, y eso lo hace mejor el interceptor de 401
       * de `api.ts`, que solo renewa cuando algo falla de verdad.
       */
      guardarAccessToken(respuesta.access_token)
      /*
       * El destino por defecto depende del rol, y lo decide la API y no el token:
       * `api.me()` lee de la base y el claim del JWT es una cache de 15 minutos (si
       * el rol bajo hace 5 minutos, el token todavia dice el viejo). Un admin va a
       * /admin y un profesional o staff a /panel; el `from` de `ProtectedRoute`
       * gana en los dos casos.
       */
      try {
        const perfil = await api.me()
        const aDonde =
          perfil.is_admin || destino.startsWith('/panel')
            ? destino
            : '/panel'
        navigate(aDonde, { replace: true })
      } catch {
        // Sin perfil no hay con que decidir el panel: /admin es el destino de
        // siempre y el interceptor de 401 ya se ocupa si la sesion esta rota.
        navigate(destino, { replace: true })
      }
    },
    onError: (error) => {
      /*
       * El 429 arma la cuenta atras. `retryAfter` viene del header `Retry-After`
       * que `api.ts` ya extrajo; sin el, el mensaje igual dice "en un momento"
       * y el boton no se bloquea--un boton bloqueado sin saber cuanto es peor
       * que ninguno.
       */
      if (error instanceof ApiError && error.status === 429 && error.retryAfter !== null) {
        setBloqueadoHasta(Date.now() + error.retryAfter * 1000)
      }
    },
  })

  const alEnviar = handleSubmit((datos) => {
    /*
     * El slug vacio va como `null` y no como `''`. El backend distingue "no vine" de
     * "viene vacio", y mandarlo vacio hace que se canonice a cadena vacia--que es un
     * desambiguador que no puede resolver nada--en vez de tomar el camino de "no vino".
     */
    const slug = datos.business_slug?.trim()
    setSlugDelIntento(slug ? slug : null)
    loginMutation.mutate({
      email: datos.email,
      password: datos.password,
      business_slug: slug ? slug : null,
    })
  })

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="text-2xl">Iniciar sesion</CardTitle>
          <CardDescription>Panel de administracion</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={alEnviar} className="space-y-4" noValidate>
            {recienRegistrado && (
              <div className="rounded-md bg-green-50 p-3 text-sm text-green-700">
                Tu negocio ya esta creado. Entra con el correo y la contrasena que elegiste.
              </div>
            )}

            <div className="space-y-2">
              <label htmlFor="email" className="text-sm text-gray-500">
                Email
              </label>
              <Input
                id="email"
                type="email"
                autoComplete="username"
                placeholder="tu@email.com"
                {...register('email')}
              />
              {errors.email && <p className="text-sm text-red-600">{errors.email.message}</p>}
            </div>

            <div className="space-y-2">
              <label htmlFor="password" className="text-sm text-gray-500">
                Contrasena
              </label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                placeholder="........"
                {...register('password')}
              />
              {errors.password && (
                <p className="text-sm text-red-600">{errors.password.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <label htmlFor="business_slug" className="text-sm text-gray-500">
                Tu URL <span className="text-gray-400">(opcional)</span>
              </label>
              <Input
                id="business_slug"
                placeholder="peluqueria-el-peluche"
                autoComplete="off"
                {...register('business_slug')}
              />
              {/*
                El texto importa porque el campo se autocompleta--con el subdominio--y
                una persona que no lo espere no va a entender para que esta. Y el "solo
                si trabajas para dos negocios" es la condicion real: con uno solo, el
                backend lo ignora.
              */}
              <p className="text-xs text-gray-400">
                Solo si el mismo correo esta en dos negocios.
              </p>
              {errors.business_slug && (
                <p className="text-sm text-red-600">{errors.business_slug.message}</p>
              )}
            </div>

            {/*
              El error va arriba y no al lado de un campo a proposito: no pertenece a
              ninguno. Un 401 puede venir del correo o de la contrasena--el backend no
              dice cual-- y ponerlo junto a uno de los dos accuse al otro.
            */}
            {loginMutation.isError && restantes === 0 && (
              <p className="text-sm text-red-600">
                {mensajeDeError(loginMutation.error, slugDelIntento !== null)}
              </p>
            )}

            {/*
              La cuenta atras reemplaza al error estatico: los dos a la vez darian
              "deja 30 segundos" y "deja 30 segundos" que se contradicen al tic-taquear.
            */}
            {restantes > 0 && (
              <p className="text-sm text-red-600">
                Demasiados intentos. Proba de nuevo en {restantes} segundos.
              </p>
            )}

            <Button
              type="submit"
              className="w-full"
              disabled={loginMutation.isPending || restantes > 0}
            >
              {loginMutation.isPending
                ? 'Entrando...'
                : restantes > 0
                  ? `Espera ${restantes}s`
                  : 'Entrar'}
            </Button>
          </form>
        </CardContent>
        <div className="px-6 pb-6 text-center text-sm text-gray-500">
          <Link to="/register" className="text-blue-600 hover:text-blue-500">
            No tenes negocio? Crear uno
          </Link>
        </div>
      </Card>
    </div>
  )
}