import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { AlertTriangle, Check, Copy, ShieldCheck, Sparkles } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { api } from '@/services/api'
import { cn } from '@/lib/utils'

/**
 * Checkout PRO: cobro manual por transferencia (Naranja X).
 *
 * **No hay pasarela.** El dueño transfiere, manda el comprobante por WhatsApp,
 * y un humano lo aprueba en `/admin`. Esta pantalla hace tres cosas:
 *
 * 1. Muestra los datos bancarios con botón de copiar (el CBU mal copiado es la
 *    causa #1 de comprobantes que no se pueden conciliar).
 * 2. Pide los datos que el admin necesita para cruzar el comprobante: email de
 *    la cuenta y fecha de la transferencia.
 * 3. Registra el intento (`POST /payments/intent`) y abre WhatsApp con el
 *    mensaje pre-armado, con el comprobante como adjunto del dueño.
 *
 * Después del click aparece el toast de confirmación. La cuenta sigue pausada
 * hasta que el admin aprueba: prometer activación instantánea sería mentir.
 */

/** Logo oficial de WhatsApp (SVG inline, color #25D366). */
function LogoWhatsApp({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
    </svg>
  )
}

/** Fila de dato copiable: valor + botón con feedback de 2s. */
function DatoCopiable({ etiqueta, valor }: { etiqueta: string; valor: string }) {
  const [copiado, setCopiado] = useState(false)
  const copiar = () => {
    navigator.clipboard
      ?.writeText(valor)
      .then(() => {
        setCopiado(true)
        setTimeout(() => setCopiado(false), 2000)
      })
      .catch(() => setCopiado(false))
  }
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-white/10 bg-white/5 px-3.5 py-2.5">
      <div className="min-w-0">
        <p className="text-[11px] uppercase tracking-wider text-slate-500">{etiqueta}</p>
        <p className="truncate font-mono text-sm text-white">{valor}</p>
      </div>
      <button
        type="button"
        onClick={copiar}
        aria-label={`Copiar ${etiqueta}`}
        className={cn(
          'shrink-0 rounded-lg border p-2 transition-colors',
          copiado
            ? 'border-emerald-400/40 bg-emerald-500/15 text-emerald-300'
            : 'border-white/10 bg-white/5 text-slate-400 hover:border-white/25 hover:text-white'
        )}
      >
        {copiado ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
      </button>
    </div>
  )
}

/** Toast fijo de confirmación, arriba del viewport. */
function ToastConfirmacion({ mensaje }: { mensaje: string }) {
  return (
    <div
      role="status"
      className="fixed inset-x-4 top-4 z-50 mx-auto max-w-md rounded-xl border border-emerald-400/30 bg-emerald-500/15 p-4 shadow-2xl backdrop-blur-xl sm:inset-x-8"
    >
      <div className="flex items-start gap-3">
        <Check className="mt-0.5 h-5 w-5 shrink-0 text-emerald-300" />
        <p className="text-sm leading-relaxed text-emerald-100">{mensaje}</p>
      </div>
    </div>
  )
}

export default function CheckoutProPage() {
  const [email, setEmail] = useState('')
  const [fecha, setFecha] = useState('')
  const [confirmado, setConfirmado] = useState<string | null>(null)

  const { data: estado } = useQuery({
    queryKey: ['panel', 'suscripcion'],
    queryFn: () => api.getSuscripcion(),
    retry: false,
  })

  const intento = useMutation({
    mutationFn: () =>
      api.crearPaymentIntent({
        email: email.trim(),
        fecha_transferencia: fecha.trim() || undefined,
      }),
    onSuccess: (respuesta) => {
      setConfirmado(respuesta.mensaje_confirmacion)
      // Abrir WhatsApp despues de registrar el intento: si el popup lo bloquea,
      // el enlace queda en pantalla para copiarlo.
      window.open(respuesta.enlace_whatsapp, '_blank')
    },
  })

  const transferencia = estado?.transferencia
  const enTrial = estado ? estado.plan === 'free' && estado.dias_restantes > 0 : false
  const vencido = estado ? estado.plan === 'free' && estado.dias_restantes === 0 : false

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[#070B1A] font-inter text-slate-100">
      <div aria-hidden="true" className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -left-32 -top-40 h-[520px] w-[520px] rounded-full bg-violet-600/25 blur-[120px]" />
        <div className="absolute -top-16 right-0 h-[460px] w-[460px] rounded-full bg-cyan-500/20 blur-[130px]" />
        <div className="absolute left-1/3 top-64 h-[420px] w-[420px] rounded-full bg-fuchsia-600/20 blur-[140px]" />
      </div>

      {confirmado && <ToastConfirmacion mensaje={confirmado} />}

      <main className="relative mx-auto max-w-lg px-5 pb-16 pt-10 sm:px-8">
        {/* Banner de trial */}
        {enTrial && estado && (
          <div className="mb-5 flex items-center gap-3 rounded-xl border border-amber-400/30 bg-amber-500/10 p-4">
            <AlertTriangle className="h-5 w-5 shrink-0 text-amber-300" />
            <p className="text-sm text-amber-100">
              Te quedan <strong>{estado.dias_restantes}</strong> {estado.dias_restantes === 1 ? 'día' : 'días'} de prueba.
            </p>
          </div>
        )}
        {vencido && (
          <div className="mb-5 rounded-xl border border-rose-400/30 bg-rose-500/10 p-4">
            <p className="font-semibold text-rose-100">Tu prueba gratis de 14 días terminó.</p>
            <p className="mt-1 text-sm text-rose-200">
              Activá PRO para seguir usando Tempus y que tus clientes puedan reservar.
            </p>
          </div>
        )}

        <section className="text-center">
          <span className="inline-flex items-center gap-2 rounded-full border border-violet-400/30 bg-violet-500/15 px-3 py-1 text-xs font-medium text-violet-200">
            <Sparkles className="h-3 w-3" /> Tempus PRO · $45.000/mes
          </span>
          <h1 className="mt-4 font-display text-3xl font-semibold tracking-tight text-white">
            Activar plan PRO
          </h1>
          <p className="mx-auto mt-2 max-w-sm text-sm text-slate-400">
            Transferí el monto exacto, enviá el comprobante por WhatsApp y activamos
            tu cuenta en menos de 2 horas.
          </p>
        </section>

        {/* Card de pago */}
        <section className="mt-8 rounded-2xl border border-white/10 bg-[#0B1224]/90 p-6 shadow-[0_0_60px_-20px_rgba(139,92,246,0.5)] backdrop-blur-xl sm:p-7">
          {transferencia ? (
            <>
              <div className="flex items-center justify-between">
                <h2 className="font-display text-lg font-semibold text-white">
                  Datos de transferencia
                </h2>
                <span className="rounded-full bg-white/10 px-2.5 py-1 text-[11px] font-semibold text-white">
                  {transferencia.banco}
                </span>
              </div>

              <div className="mt-4 space-y-2.5">
                <DatoCopiable etiqueta="CBU" valor={transferencia.cbu} />
                <DatoCopiable etiqueta="Alias" valor={transferencia.alias} />
                <div className="flex items-center justify-between rounded-lg border border-white/10 bg-white/5 px-3.5 py-2.5">
                  <div className="min-w-0">
                    <p className="text-[11px] uppercase tracking-wider text-slate-500">Titular</p>
                    <p className="truncate text-sm text-white">{transferencia.titular}</p>
                  </div>
                  <ShieldCheck className="h-4 w-4 shrink-0 text-slate-500" />
                </div>
                <div className="flex items-center justify-between rounded-lg border border-violet-400/30 bg-violet-500/10 px-3.5 py-2.5">
                  <div>
                    <p className="text-[11px] uppercase tracking-wider text-violet-300">Monto exacto</p>
                    <p className="font-display text-xl font-bold text-white">
                      ${transferencia.monto.toLocaleString('es-AR', { maximumFractionDigits: 0 })}
                    </p>
                  </div>
                  <p className="text-xs text-slate-400">{transferencia.moneda}</p>
                </div>
              </div>
            </>
          ) : (
            <p className="text-sm text-slate-400">Cargando datos de pago...</p>
          )}

          {/* Formulario de identificación */}
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (!email.trim()) return
              intento.mutate()
            }}
            className="mt-6 space-y-4 border-t border-white/10 pt-6"
            noValidate
          >
            <div className="space-y-2">
              <label htmlFor="email" className="text-sm text-slate-300">
                Email de tu cuenta <span className="text-slate-500">(identificador del pago)</span>
              </label>
              <Input
                id="email"
                type="email"
                placeholder="tu@email.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                className="border-white/10 bg-white/5 text-white placeholder:text-slate-500 focus-visible:ring-violet-400"
              />
            </div>
            <div className="space-y-2">
              <label htmlFor="fecha" className="text-sm text-slate-300">
                Fecha de la transferencia <span className="text-slate-500">(opcional)</span>
              </label>
              <Input
                id="fecha"
                type="date"
                value={fecha}
                onChange={(e) => setFecha(e.target.value)}
                style={{ colorScheme: 'dark' }}
                className="border-white/10 bg-white/5 text-white focus-visible:ring-violet-400"
              />
            </div>

            {intento.isError && (
              <p role="alert" className="rounded-lg border border-rose-400/20 bg-rose-500/10 p-3 text-sm text-rose-200">
                {intento.error instanceof Error ? intento.error.message : 'No se pudo registrar el intento.'}
              </p>
            )}

            {confirmado && transferencia && (
              <a
                href={transferencia.enlace_whatsapp}
                target="_blank"
                rel="noreferrer"
                className="block text-center text-xs text-emerald-300 hover:text-emerald-200"
              >
                ¿No se abrió WhatsApp? Abrilo acá
              </a>
            )}

            <button
              type="submit"
              disabled={intento.isPending || !email.trim()}
              className="flex w-full items-center justify-center gap-2.5 rounded-xl bg-[#25D366] py-3 text-sm font-bold text-white shadow-[0_0_30px_-8px_rgba(37,211,102,0.6)] transition-all hover:brightness-110 disabled:opacity-50"
            >
              <LogoWhatsApp className="h-5 w-5" />
              {intento.isPending ? 'Registrando...' : 'Enviar comprobante por WhatsApp y activar'}
            </button>
          </form>
        </section>

        <p className="mt-5 text-center text-xs text-slate-500">
          La activación la hace el equipo de Tempus al recibir tu comprobante.
          Sin cargos automáticos.
        </p>
      </main>
    </div>
  )
}
