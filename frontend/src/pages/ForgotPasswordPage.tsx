import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { z } from 'zod'
import { ArrowLeft, CheckCircle2, Loader2, Mail } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { InputContrasena } from '@/components/ui/input-contrasena'
import { MarcaTempus } from '@/components/MarcaTempus'
import { api, ApiError } from '@/services/api'

/**
 * Recuperación de contraseña, en sus dos pantallas:
 *
 * 1. `/forgot-password` pide el email. La respuesta es la misma exista o no
 *    la cuenta (anti-enumeración, el backend la garantiza), y la pantalla dice
 *    exactamente eso: "si el correo tiene cuenta, te mandamos el enlace".
 * 2. `/reset-password?token=...` llega desde el email. Si el token no sirve,
 *    el 400 del backend se muestra con el botón de volver a pedir el enlace.
 *
 * El mínimo de contraseña replica el del backend (`Field(min_length=8)`).
 */

const esquemaContrasena = z.object({
  password: z.string().min(8, 'La contraseña necesita al menos 8 caracteres').max(128),
})

function EsquemaClases() {
  return 'border-white/10 bg-white/5 text-white placeholder:text-slate-500 focus-visible:ring-violet-400 focus-visible:border-violet-400/50'
}

/** Pantalla 1: pedir el enlace. */
function PedirEnlace() {
  const [email, setEmail] = useState('')
  const [enviado, setEnviado] = useState(false)

  const pedido = useMutation({
    mutationFn: () => api.forgotPassword(email.trim()),
    onSuccess: () => setEnviado(true),
  })

  if (enviado) {
    return (
      <div className="space-y-4 text-center">
        <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-400" />
        <p className="text-sm leading-relaxed text-slate-300">
          Si el correo <span className="font-medium text-white">{email.trim()}</span> tiene cuenta,
          te enviamos un enlace para restablecer la contraseña. Revisá también la carpeta de spam.
        </p>
        <p className="text-xs text-slate-500">
          El enlace vence en 1 hora y sirve una sola vez.
        </p>
        <Link
          to="/login"
          className="inline-block text-sm font-medium text-violet-300 hover:text-violet-200"
        >
          Volver a iniciar sesión
        </Link>
      </div>
    )
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (email.trim()) pedido.mutate()
      }}
      className="space-y-5"
      noValidate
    >
      <div className="space-y-2">
        <label htmlFor="email" className="flex items-center gap-2 text-sm font-medium text-slate-300">
          <Mail className="h-3.5 w-3.5 text-violet-400" />
          Email de tu cuenta
        </label>
        <Input
          id="email"
          type="email"
          autoComplete="email"
          placeholder="tu@email.com"
          className={EsquemaClases()}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
      </div>

      {pedido.isError && (
        <p className="rounded-lg border border-rose-400/20 bg-rose-500/10 p-3 text-sm text-rose-200">
          {pedido.error instanceof ApiError ? pedido.error.message : 'No se pudo enviar el pedido'}
        </p>
      )}

      <Button
        type="submit"
        disabled={pedido.isPending || !email.trim()}
        className="w-full rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-600 py-2.5 font-semibold text-white shadow-lg shadow-violet-500/25 transition-all hover:shadow-xl hover:shadow-violet-500/40 hover:-translate-y-0.5"
      >
        {pedido.isPending ? (
          <>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Enviando...
          </>
        ) : (
          'Enviar enlace de recuperación'
        )}
      </Button>

      <p className="text-center text-xs text-slate-500">
        <Link to="/login" className="text-violet-300 hover:text-violet-200">
          Volver a iniciar sesión
        </Link>
      </p>
    </form>
  )
}

/** Pantalla 2: fijar la contraseña nueva con el token del email. */
function FijarContrasena({ token }: { token: string }) {
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  const reset = useMutation({
    mutationFn: () => api.resetPassword(token, password),
    onSuccess: () => {
      navigate('/login', { state: { contrasena_actualizada: true } })
    },
    onError: (e) => {
      setError(e instanceof ApiError ? e.message : 'No se pudo actualizar la contraseña')
    },
  })

  const valida = esquemaContrasena.safeParse({ password })

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        const resultado = valida
        if (!resultado.success) {
          setError(resultado.error.issues[0].message)
          return
        }
        setError(null)
        reset.mutate()
      }}
      className="space-y-5"
      noValidate
    >
      <div className="space-y-2">
        <label htmlFor="password" className="text-sm font-medium text-slate-300">
          Contraseña nueva
        </label>
        <InputContrasena
          id="password"
          autoComplete="new-password"
          className={EsquemaClases()}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <p className="text-xs text-slate-500">
          Mínimo 8 caracteres. Mezcla mayúsculas, números y símbolos.
        </p>
      </div>

      {(error || reset.isError) && (
        <p className="rounded-lg border border-rose-400/20 bg-rose-500/10 p-3 text-sm text-rose-200">
          {error ?? (reset.error instanceof ApiError ? reset.error.message : 'Error inesperado')}
        </p>
      )}

      <Button
        type="submit"
        disabled={reset.isPending}
        className="w-full rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-600 py-2.5 font-semibold text-white shadow-lg shadow-violet-500/25 transition-all hover:shadow-xl hover:shadow-violet-500/40 hover:-translate-y-0.5"
      >
        {reset.isPending ? (
          <>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Guardando...
          </>
        ) : (
          'Guardar contraseña'
        )}
      </Button>
    </form>
  )
}

export default function ForgotPasswordPage() {
  const [parametros] = useSearchParams()
  const token = parametros.get('token')

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#070B1A] font-inter text-slate-100">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -left-32 -top-40 h-[520px] w-[520px] rounded-full bg-violet-600/20 blur-[120px] animate-flotar-lento" />
        <div className="absolute -top-16 right-0 h-[460px] w-[460px] rounded-full bg-cyan-500/15 blur-[130px] animate-flotar-lento" />
      </div>

      <div className="relative mx-auto flex min-h-screen w-full max-w-md flex-col justify-center px-6 py-12">
        <Link
          to="/"
          className="mb-8 inline-flex w-fit items-center gap-2 text-sm text-slate-400 transition-colors hover:text-white"
        >
          <ArrowLeft className="h-4 w-4" />
          Volver
        </Link>

        <div className="mb-6 flex justify-center">
          <MarcaTempus size={72} soloSimbolo={false} />
        </div>

        <div className="w-full rounded-2xl border border-white/10 bg-[#0B1224]/90 p-7 shadow-[0_0_60px_-20px_rgba(139,92,246,0.5)] backdrop-blur-xl sm:p-8">
          <h1 className="font-display text-2xl font-semibold tracking-tight text-white">
            {token ? 'Elegí tu contraseña nueva' : 'Recuperar contraseña'}
          </h1>
          <p className="mt-1.5 mb-6 text-sm text-slate-400">
            {token
              ? 'Escribí la contraseña nueva y entra de nuevo al panel.'
              : 'Te mandamos un enlace al correo de tu cuenta para elegir una nueva.'}
          </p>

          {token ? <FijarContrasena token={token} /> : <PedirEnlace />}
        </div>
      </div>
    </div>
  )
}
