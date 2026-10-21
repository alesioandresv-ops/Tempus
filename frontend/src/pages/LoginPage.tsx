import { useEffect, useState } from 'react'
import { useNavigate, useLocation, Link } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { useMutation } from '@tanstack/react-query'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { MarcaTempus } from '@/components/MarcaTempus'
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
  /*
   * Viene del CTA de Pro en la landing (`/register?plan=pro` -> alta -> aca). El pago
   * se autoriza desde el panel, asi que despues de entrar se manda a la pestana de
   * pagos. Solo aplica al admin--un profesional no administra la suscripcion--y el
   * parametro no pisa un `from` explicito, que es una intencion mas fuerte.
   */
  const estadoEntrada = location.state as { from?: string; irAPagar?: boolean } | null
  const quierePagar = estadoEntrada?.irAPagar === true && !estadoEntrada?.from
  const destino = estadoEntrada?.from ?? (quierePagar ? '/admin?tab=pagos' : '/admin')

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

  /*
   * ---------------------------------------------------------------------------
   * Layout Aurora.
   *
   * La logica de arriba--validacion, mutacion, cuenta atras del 429 y destino
   * por rol--no cambia: lo que cambia es la presentacion. El formulario sigue
   * siendo el mismo `handleSubmit` y los mismos `register`.
   *
   * Dos columnas en escritorio y una sola en movil. **En movil no hay
   * desplazamiento horizontal**: el bloque de marca no entra al costado de la
   * tarjeta, entra arriba, y por eso ahi no se usa `entra-izq` (que mueve en
   * X) sino `entra-arriba`. Un `translateX` en una pantalla angosta genera
   * scroll lateral aunque el contenido termine en su lugar.
   * ---------------------------------------------------------------------------
   */
  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[#070B1A] font-inter text-slate-100">
      {/* Fondo: auroras de color difuminadas. Decorativo, `aria-hidden`. */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -left-32 -top-40 h-[520px] w-[520px] rounded-full bg-violet-600/25 blur-[120px] animate-flotar-lento" />
        <div className="absolute -top-16 right-0 h-[460px] w-[460px] rounded-full bg-cyan-500/20 blur-[130px] animate-flotar-lento" />
        <div className="absolute left-1/3 top-64 h-[420px] w-[420px] rounded-full bg-fuchsia-600/20 blur-[140px]" />
      </div>

      <div className="relative mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-10 px-6 py-10 lg:flex-row lg:items-center lg:gap-16 lg:px-8">
        {/* ------------------------------------------------------ columna izquierda */}
        {/*
          Solo el logo va centrado en su columna (`lg:items-center`); el resto del
          bloque--badge, titulo, parrafo y tarjeta--sigue alineado a la izquierda
          como estaba. Por eso `lg:items-center` va en el contenedor del logo y no
          en la columna: aplicado a la columna centraria tambien el texto.
        */}
        <div className="flex flex-1 flex-col justify-center lg:max-w-xl">
          <div className="flex animate-entra-izq lg:justify-center">
            {/*
              El logo completo--simbolo **y** palabra--porque aca hay lugar: la
              columna mide mas de 500 px y el logo entra comodo. El isotipo solo
              se reserva para espacios chicos (la barra lateral del panel).

              El tamano es 128 y no 56: medido sobre la captura a 56 el anillo
              quedaba en ~50 px visibles y se leia como un detalle en vez de como
              la marca. A 128 el anillo ronda los 115 px, que es el peso visual que
              corresponde a la primera pantalla.
            */}
            <MarcaTempus size={128} soloSimbolo={false} />
          </div>

          <span
            className="mt-8 inline-flex w-fit items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs font-medium text-violet-200 animate-entra-izq"
            style={{ animationDelay: '110ms' }}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-violet-400" aria-hidden="true" />
            Panel de administracion
          </span>

          <h1
            className="mt-6 font-display text-4xl font-semibold leading-tight tracking-tight text-white sm:text-5xl animate-entra-izq"
            style={{ animationDelay: '220ms' }}
          >
            Tu agenda,
            <br />
            <span className="bg-gradient-to-r from-violet-400 via-fuchsia-400 to-cyan-300 bg-clip-text text-transparent">
              siempre en orden
            </span>
          </h1>

          <p
            className="mt-5 max-w-md text-base leading-relaxed text-slate-400 animate-entra-izq"
            style={{ animationDelay: '330ms' }}
          >
            Turnos, profesionales y cobros en un solo lugar. Entra con el correo y la
            contrasena que usaste al crear tu negocio.
          </p>

          {/*
            La agenda de vidrio. Es un mockup decorativo: no muestra datos reales
            de nadie, asi que va `aria-hidden` para que un lector de pantalla no
            lea turnos inventados.
          */}
          <div
            aria-hidden="true"
            className="mt-10 hidden max-w-md animate-entra-izq rounded-2xl border border-white/10 bg-white/[0.03] p-5 backdrop-blur-sm sm:block"
            style={{ animationDelay: '440ms' }}
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-slate-300">Hoy</span>
              <span className="rounded-full bg-violet-500/20 px-2 py-0.5 text-[10px] font-medium text-violet-200">
                4 turnos
              </span>
            </div>
            <div className="mt-4 space-y-2.5">
              {[
                ['09:00', 'Corte y barba', 'bg-violet-500/70'],
                ['10:30', 'Color completo', 'bg-cyan-500/70'],
                ['12:00', 'Perfilado', 'bg-fuchsia-500/70'],
              ].map(([hora, servicio, punto]) => (
                <div key={hora} className="flex items-center gap-3">
                  <span className="w-10 shrink-0 text-[11px] tabular-nums text-slate-400">
                    {hora}
                  </span>
                  <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${punto}`} />
                  <span className="truncate text-xs text-slate-300">{servicio}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ------------------------------------------------------ columna derecha */}
        <div className="flex w-full flex-col justify-center lg:max-w-md">
          <div
            className="w-full animate-entra-arriba rounded-2xl border border-white/10 bg-[#0B1224]/90 p-7 shadow-[0_0_60px_-20px_rgba(139,92,246,0.5)] backdrop-blur-xl sm:p-8"
            style={{ animationDelay: '180ms' }}
          >
            <h2 className="font-display text-2xl font-semibold tracking-tight text-white">
              Iniciar sesion
            </h2>
            <p className="mt-1.5 text-sm text-slate-400">
              Entra al panel de tu negocio
            </p>

            <form onSubmit={alEnviar} className="mt-7 space-y-4" noValidate>
              {recienRegistrado && (
                <div className="rounded-lg border border-emerald-400/20 bg-emerald-500/10 p-3 text-sm text-emerald-200">
                  Tu negocio ya esta creado. Entra con el correo y la contrasena que
                  elegiste.
                </div>
              )}

              <div className="space-y-2">
                <label htmlFor="email" className="text-sm text-slate-300">
                  Email
                </label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="username"
                  placeholder="tu@email.com"
                  /*
                   * El input de `ui/input` trae el look claro del panel. En el
                   * fondo oscuro hay que repintarlo: sin esto queda una caja
                   * blanca que rompe el tema.
                   */
                  className="border-white/10 bg-white/5 text-white placeholder:text-slate-500 focus-visible:ring-violet-400"
                  {...register('email')}
                />
                {errors.email && (
                  <p className="text-sm text-rose-400">{errors.email.message}</p>
                )}
              </div>

              <div className="space-y-2">
                <label htmlFor="password" className="text-sm text-slate-300">
                  Contrasena
                </label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  placeholder="........"
                  className="border-white/10 bg-white/5 text-white placeholder:text-slate-500 focus-visible:ring-violet-400"
                  {...register('password')}
                />
                {errors.password && (
                  <p className="text-sm text-rose-400">{errors.password.message}</p>
                )}
              </div>

              <div className="space-y-2">
                <label htmlFor="business_slug" className="text-sm text-slate-300">
                  Tu URL <span className="text-slate-500">(opcional)</span>
                </label>
                <Input
                  id="business_slug"
                  placeholder="peluqueria-el-peluche"
                  autoComplete="off"
                  className="border-white/10 bg-white/5 text-white placeholder:text-slate-500 focus-visible:ring-violet-400"
                  {...register('business_slug')}
                />
                {/*
                  El texto importa porque el campo se autocompleta--con el subdominio--y
                  una persona que no lo espere no va a entender para que esta. Y el "solo
                  si trabajas para dos negocios" es la condicion real: con uno solo, el
                  backend lo ignora.
                */}
                <p className="text-xs text-slate-500">
                  Solo si el mismo correo esta en dos negocios.
                </p>
                {errors.business_slug && (
                  <p className="text-sm text-rose-400">{errors.business_slug.message}</p>
                )}
              </div>

              {/*
                El error va arriba y no al lado de un campo a proposito: no pertenece a
                ninguno. Un 401 puede venir del correo o de la contrasena--el backend no
                dice cual-- y ponerlo junto a uno de los dos accuse al otro.
              */}
              {loginMutation.isError && restantes === 0 && (
                <p className="rounded-lg border border-rose-400/20 bg-rose-500/10 p-3 text-sm text-rose-200">
                  {mensajeDeError(loginMutation.error, slugDelIntento !== null)}
                </p>
              )}

              {/*
                La cuenta atras reemplaza al error estatico: los dos a la vez darian
                "deja 30 segundos" y "deja 30 segundos" que se contradicen al tic-taquear.
              */}
              {restantes > 0 && (
                <p className="rounded-lg border border-rose-400/20 bg-rose-500/10 p-3 text-sm text-rose-200">
                  Demasiados intentos. Proba de nuevo en {restantes} segundos.
                </p>
              )}

              {/*
                El boton blanco es el unico CTA de la pantalla y el unico elemento
                macizo: sobre el fondo oscuro, el contraste hace el trabajo que en
                un panel claro haria el color. El `pop` es la ultima pieza de la
                cascada--entra despues que el resto y confirma el fin del movimiento.
              */}
              <Button
                type="submit"
                className="w-full animate-pop-cta rounded-xl bg-white py-2.5 font-semibold text-slate-900 transition-colors hover:bg-slate-200 disabled:opacity-60"
                style={{ animationDelay: '760ms' }}
                disabled={loginMutation.isPending || restantes > 0}
              >
                {loginMutation.isPending
                  ? 'Entrando...'
                  : restantes > 0
                    ? `Espera ${restantes}s`
                    : 'Entrar'}
              </Button>
            </form>

            <div className="mt-6 border-t border-white/10 pt-5 text-center text-sm text-slate-400">
              <Link to="/register" className="text-violet-300 hover:text-violet-200">
                No tenes negocio? Crear uno
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}