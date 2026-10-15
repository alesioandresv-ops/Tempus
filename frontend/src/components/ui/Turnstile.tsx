import { useEffect, useRef } from 'react'

/**
 * Cloudflare Turnstile, cableado a mano.
 *
 * **Por que no el script que se auto-renderiza.** El widget automatico busca
 * elementos con `class="cf-turnstile"` en el momento en que corre el script. En una
 * SPA el formulario suele montarse despues--y en el caso de `BusinessPage`, despues
 * de tres pasos--, asi que el script ya paso y el widget nunca aparece. El modo
 * `render=explicit` deja el montaje en manos de React, que es quien sabe cuando el
 * formulario existe.
 *
 * **Sin `VITE_TURNSTILE_SITE_KEY` no se renderiza nada y `token` queda en `null`.**
 * El backend tampoco exige el token con `TURNSTILE_ENABLED=false`, asi que local y
 * tests siguen funcionando con los dos lados apagados por la misma razon.
 */

const SCRIPT_ID = 'cf-turnstile-script'
const SCRIPT_SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'

/** Clave publica del sitio. Vacia = Turnstile no montado en esta instalacion. */
export const turnstileSiteKey = (import.meta.env.VITE_TURNSTILE_SITE_KEY as string | undefined) ?? ''

/** `true` cuando el widget va a renderizarse (y por lo tanto hay token que exigir). */
export const turnstileHabilitado = turnstileSiteKey.length > 0

interface TurnstileApi {
  render: (
    contenedor: HTMLElement,
    opciones: {
      sitekey: string
      callback: (token: string) => void
      'expired-callback'?: () => void
      'error-callback'?: () => void
      theme?: 'light' | 'dark' | 'auto'
    }
  ) => string
  remove: (id: string) => void
}

declare global {
  interface Window {
    turnstile?: TurnstileApi
  }
}

/**
 * Carga el script una sola vez, aunque monten diez widgets a la vez.
 *
 * El `<script>` se busca por `id` en vez de por una variable de modulo: si dos
 * componentes lo piden en el mismo tick, el segundo encuentra la etiqueta del primero
 * ya en el DOM y espera a su `load` en vez de insertar una copia.
 */
let cargaEnCurso: Promise<void> | null = null

function cargarScript(): Promise<void> {
  if (window.turnstile) return Promise.resolve()
  if (cargaEnCurso) return cargaEnCurso

  cargaEnCurso = new Promise<void>((resolver, rechazar) => {
    const existente = document.getElementById(SCRIPT_ID) as HTMLScriptElement | null
    const script = existente ?? document.createElement('script')
    const alCargar = () => resolver()
    const alFallar = () => {
      cargaEnCurso = null
      rechazar(new Error('No se pudo cargar Turnstile'))
    }
    script.addEventListener('load', alCargar, { once: true })
    script.addEventListener('error', alFallar, { once: true })
    if (!existente) {
      script.id = SCRIPT_ID
      script.src = SCRIPT_SRC
      script.async = true
      script.defer = true
      document.head.appendChild(script)
    }
  })

  return cargaEnCurso
}

interface TurnstileProps {
  /** Recibe el token al resolver el desafio, y `null` si expira o falla. */
  onVerify: (token: string | null) => void
  className?: string
}

export function Turnstile({ onVerify, className }: TurnstileProps) {
  const contenedorRef = useRef<HTMLDivElement>(null)
  // La funcion va en un ref y no en las dependencias del efecto: `setState` desde el
  // padre crea una identidad nueva en cada render, y con `onVerify` en las deps el
  // widget se desmontaria y volveria a montar--perdiendo el token-- en cada tecla.
  const onVerifyRef = useRef(onVerify)
  onVerifyRef.current = onVerify

  useEffect(() => {
    if (!turnstileHabilitado) return
    let cancelado = false
    let widgetId: string | null = null

    cargarScript()
      .then(() => {
        if (cancelado || !contenedorRef.current || !window.turnstile) return
        widgetId = window.turnstile.render(contenedorRef.current, {
          sitekey: turnstileSiteKey,
          theme: 'auto',
          callback: (token) => onVerifyRef.current(token),
          'expired-callback': () => onVerifyRef.current(null),
          'error-callback': () => onVerifyRef.current(null),
        })
      })
      .catch(() => {
        // El backend es quien decide: sin token, un `TURNSTILE_ENABLED` de verdad
        // responde 403 con un mensaje que la persona ve. Aca no se inventa un error.
        onVerifyRef.current(null)
      })

    return () => {
      cancelado = true
      if (widgetId && window.turnstile) window.turnstile.remove(widgetId)
    }
  }, [])

  if (!turnstileHabilitado) return null
  return <div ref={contenedorRef} className={className} />
}
