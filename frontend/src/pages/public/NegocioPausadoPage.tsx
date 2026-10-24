import { PauseCircle } from 'lucide-react'

/**
 * Pantalla para el cliente final cuando el negocio no puede recibir reservas:
 * trial vencido o Pro sin pagar. Es la que ve un visitante de `/{slug}/reservar`.
 *
 * El mensaje no culpa ni al visitante ni a la fecha: nombra al negocio y le da
 * la salida (contactarlo por WhatsApp). Es la misma pantalla para ambos estados
 * de pausa, porque para el visitante la razón es la misma: no puede reservar.
 */
export default function NegocioPausadoPage() {
  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-x-hidden bg-[#070B1A] font-inter px-5 text-slate-100">
      <div aria-hidden="true" className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -left-32 -top-40 h-[520px] w-[520px] rounded-full bg-slate-700/30 blur-[120px]" />
        <div className="absolute -top-16 right-0 h-[460px] w-[460px] rounded-full bg-slate-600/20 blur-[130px]" />
      </div>

      <main className="relative mx-auto w-full max-w-md rounded-2xl border border-white/10 bg-[#0B1224]/90 p-8 text-center shadow-2xl backdrop-blur-xl">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-amber-500/15">
          <PauseCircle className="h-7 w-7 text-amber-300" />
        </div>

        <h1 className="mt-5 font-display text-2xl font-semibold tracking-tight text-white">
          Este negocio pausó sus reservas
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-slate-400">
          No se están tomando reservas en este momento. Contactalo directamente y
          te coordina un turno.
        </p>

        <a
          href="https://wa.me/5493772468309"
          target="_blank"
          rel="noreferrer"
          className="mt-7 flex w-full items-center justify-center gap-2.5 rounded-xl bg-[#25D366] py-3 text-sm font-bold text-white transition-all hover:brightness-110"
        >
          Contactar al negocio
        </a>

        <p className="mt-5 text-xs text-slate-500">
          ¿Sos el dueño? Activá tu plan PRO para reabrir tus reservas.
        </p>
      </main>
    </div>
  )
}
