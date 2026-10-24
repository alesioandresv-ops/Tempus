import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Clock, PauseCircle, Sparkles } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Panel administrativo de la plataforma (`/admin-plataforma?key=ADMIN_SECRET`).
 *
 * La consola del operador de Tempus: ve todos los negocios con su estado de
 * cobro, aprueba o rechaza comprobantes, y activa/bloquea a mano. No es el
 * panel del negocio (`/admin`) ni comparte autenticación con él: la guardia es
 * el `ADMIN_SECRET` en la query, chequeado por el backend en cada request.
 *
 * Los tabs filtran en el cliente: la API ya trae todo y el volumen es de miles,
 * no de millones. Si algún día crece, el filtro se muda al query string.
 */

type Fila = {
  id: string
  business_name: string
  email: string
  slug: string
  estado: 'trial' | 'activo' | 'pro_vencido' | 'vencido' | 'bloqueado'
  plan: string
  trial_dias_restantes: number
  trial_ends_at: string | null
  pro_until: string | null
  ultimo_pago_status: string | null
  ultimo_pago_at: string | null
}

type Stats = {
  en_prueba: number
  pendientes: number
  pro_activos: number
  vencidos: number
}

const TABS = [
  { id: 'todos', etiqueta: 'Todos' },
  { id: 'trial', etiqueta: 'En prueba gratis' },
  { id: 'pendientes', etiqueta: 'Pendientes' },
  { id: 'activos', etiqueta: 'Activos' },
  { id: 'vencidos', etiqueta: 'Vencidos' },
] as const

const BADGES: Record<Fila['estado'], string> = {
  trial: 'border-sky-400/30 bg-sky-500/15 text-sky-300',
  activo: 'border-emerald-400/30 bg-emerald-500/15 text-emerald-300',
  pro_vencido: 'border-amber-400/30 bg-amber-500/15 text-amber-300',
  vencido: 'border-rose-400/30 bg-rose-500/15 text-rose-300',
  bloqueado: 'border-slate-400/30 bg-slate-500/15 text-slate-300',
}

const ETIQUETAS: Record<Fila['estado'], string> = {
  trial: 'En prueba',
  activo: 'PRO activo',
  pro_vencido: 'PRO vencido',
  vencido: 'Vencido',
  bloqueado: 'Bloqueado',
}

async function llamadaAdmin(key: string, path: string, metodo: 'PUT' | 'POST' = 'PUT') {
  const res = await fetch(`/api/v1${path}?key=${encodeURIComponent(key)}`, {
    method: metodo,
    headers: { 'Content-Type': 'application/json' },
  })
  if (!res.ok) {
    const cuerpo = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error((cuerpo as { detail?: string }).detail ?? 'Error del servidor')
  }
  return res.json()
}

export default function AdminPlataformaPage() {
  const [key, setKey] = useState(() => new URLSearchParams(window.location.search).get('key') ?? '')
  const [tab, setTab] = useState<(typeof TABS)[number]['id']>('todos')
  const [error, setError] = useState<string | null>(null)
  const qc = useQueryClient()

  const filas = useQuery<Fila[]>({
    queryKey: ['admin-plataforma', 'businesses'],
    queryFn: () =>
      fetch(`/api/v1/admin/businesses?key=${encodeURIComponent(key)}`).then((r) => {
        if (r.status === 401) throw new Error('Clave inválida')
        return r.json()
      }),
    enabled: !!key,
    retry: false,
  })

  const stats = useQuery<Stats>({
    queryKey: ['admin-plataforma', 'stats'],
    queryFn: () => fetch(`/api/v1/admin/stats?key=${encodeURIComponent(key)}`).then((r) => r.json()),
    enabled: !!key,
    retry: false,
  })

  const accion = useMutation({
    mutationFn: ({ path }: { path: string }) => llamadaAdmin(key, path),
    onSuccess: () => {
      setError(null)
      void qc.invalidateQueries({ queryKey: ['admin-plataforma'] })
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Error'),
  })

  if (!key) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#070B1A] px-5 font-inter text-slate-100">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            setTab('todos')
            void filas.refetch()
          }}
          className="w-full max-w-sm rounded-2xl border border-white/10 bg-[#0B1224]/90 p-8 backdrop-blur-xl"
        >
          <h1 className="font-display text-xl font-semibold text-white">Panel de la plataforma</h1>
          <p className="mt-1.5 text-sm text-slate-400">Ingresá la clave de administrador.</p>
          <input
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="ADMIN_SECRET"
            className="mt-5 w-full rounded-xl border border-white/10 bg-white/5 px-3.5 py-2.5 text-sm text-white outline-none focus:border-violet-400/50 focus:ring-2 focus:ring-violet-400/30"
          />
          <button
            type="submit"
            className="mt-4 w-full rounded-xl bg-white py-2.5 text-sm font-semibold text-slate-900 hover:bg-slate-200"
          >
            Entrar
          </button>
          {filas.isError && <p className="mt-3 text-sm text-rose-400">Clave inválida.</p>}
        </form>
      </div>
    )
  }

  const visibles =
    filas.data?.filter((f) => {
      if (tab === 'todos') return true
      if (tab === 'trial') return f.estado === 'trial'
      if (tab === 'pendientes') return f.ultimo_pago_status === 'pending'
      if (tab === 'activos') return f.estado === 'activo'
      return ['vencido', 'pro_vencido', 'bloqueado'].includes(f.estado)
    }) ?? []

  return (
    <div className="min-h-screen bg-[#070B1A] font-inter text-slate-100">
      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-8">
        <header className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-fuchsia-500">
            <Sparkles className="h-5 w-5 text-white" />
          </div>
          <div>
            <h1 className="font-display text-xl font-semibold text-white">Tempus · Admin</h1>
            <p className="text-xs text-slate-400">Plataforma y cobros Pro</p>
          </div>
        </header>

        {/* Stats */}
        <section className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[
            { etiqueta: '🆓 En prueba', valor: stats.data?.en_prueba, color: 'text-sky-300' },
            { etiqueta: '⏳ Pendientes', valor: stats.data?.pendientes, color: 'text-amber-300' },
            { etiqueta: '✅ PRO activos', valor: stats.data?.pro_activos, color: 'text-emerald-300' },
            { etiqueta: '🔴 Vencidos', valor: stats.data?.vencidos, color: 'text-rose-300' },
          ].map((s) => (
            <div key={s.etiqueta} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              <p className="text-xs text-slate-400">{s.etiqueta}</p>
              <p className={cn('mt-1 font-display text-2xl font-bold', s.color)}>{s.valor ?? '—'}</p>
            </div>
          ))}
        </section>

        {/* Tabs */}
        <div className="mt-6 flex flex-wrap gap-2">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                'rounded-full border px-4 py-1.5 text-xs font-medium transition-all',
                tab === t.id
                  ? 'border-violet-400/40 bg-violet-500/15 text-violet-200'
                  : 'border-white/10 bg-white/5 text-slate-300 hover:border-white/25'
              )}
            >
              {t.etiqueta}
            </button>
          ))}
        </div>

        {error && (
          <p role="alert" className="mt-4 rounded-lg border border-rose-400/20 bg-rose-500/10 p-3 text-sm text-rose-200">
            {error}
          </p>
        )}

        {/* Tabla */}
        <section className="mt-4 overflow-x-auto rounded-2xl border border-white/10 bg-white/[0.02]">
          <table className="w-full min-w-[900px] text-left text-sm">
            <thead>
              <tr className="border-b border-white/10 text-[11px] uppercase tracking-wider text-slate-500">
                <th className="px-4 py-3">Negocio</th>
                <th className="px-4 py-3">Email</th>
                <th className="px-4 py-3">URL reservas</th>
                <th className="px-4 py-3">Estado</th>
                <th className="px-4 py-3">Trial</th>
                <th className="px-4 py-3">PRO hasta</th>
                <th className="px-4 py-3">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {filas.isLoading && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-slate-400">Cargando…</td>
                </tr>
              )}
              {visibles.length === 0 && !filas.isLoading && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-slate-500">Sin negocios en este filtro.</td>
                </tr>
              )}
              {visibles.map((f) => (
                <tr key={f.id} className="border-b border-white/5 last:border-0 hover:bg-white/[0.03]">
                  <td className="px-4 py-3 font-medium text-white">{f.business_name}</td>
                  <td className="px-4 py-3 text-slate-300">{f.email || '—'}</td>
                  <td className="px-4 py-3">
                    <a
                      href={`/${f.slug}`}
                      target="_blank"
                      rel="noreferrer"
                      className="text-violet-300 hover:text-violet-200"
                    >
                      /{f.slug}
                    </a>
                  </td>
                  <td className="px-4 py-3">
                    <span className={cn('rounded-full border px-2.5 py-0.5 text-[11px] font-semibold', BADGES[f.estado])}>
                      {ETIQUETAS[f.estado]}
                    </span>
                  </td>
                  <td className={cn('px-4 py-3', f.estado === 'trial' && f.trial_dias_restantes < 3 ? 'font-semibold text-rose-400' : 'text-slate-300')}>
                    {f.estado === 'trial' ? `Quedan ${f.trial_dias_restantes} días` : '—'}
                  </td>
                  <td className="px-4 py-3 text-slate-300">
                    {f.pro_until ? new Date(f.pro_until).toLocaleDateString('es-AR') : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1.5">
                      {/* Pendiente de aprobar */}
                      {f.ultimo_pago_status === 'pending' && (
                        <>
                          <button
                            onClick={() => accion.mutate({ path: `/admin/businesses/${f.id}/activate` })}
                            className="flex items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-emerald-500"
                            title="Aprobar el último comprobante"
                          >
                            <Check className="h-3 w-3" /> Activar 30 días
                          </button>
                          <a
                            href={`https://wa.me/5493772468309?text=${encodeURIComponent(`Hola! Te escribimos de Tempus por la activación PRO de ${f.business_name} 🚀`)}`}
                            target="_blank"
                            rel="noreferrer"
                            className="rounded-lg bg-[#25D366] px-2.5 py-1 text-[11px] font-semibold text-white hover:brightness-110"
                          >
                            WhatsApp
                          </a>
                        </>
                      )}
                      {f.estado === 'trial' && (
                        <button
                          onClick={() => accion.mutate({ path: `/admin/businesses/${f.id}/activate` })}
                          className="rounded-lg border border-emerald-500/40 px-2.5 py-1 text-[11px] text-emerald-300 hover:bg-emerald-500/10"
                        >
                          Activar PRO
                        </button>
                      )}
                      {f.estado === 'activo' && (
                        <>
                          <button
                            onClick={() => accion.mutate({ path: `/admin/businesses/${f.id}/activate` })}
                            className="flex items-center gap-1 rounded-lg border border-violet-500/40 px-2.5 py-1 text-[11px] text-violet-300 hover:bg-violet-500/10"
                          >
                            <Clock className="h-3 w-3" /> +30 días
                          </button>
                          <button
                            onClick={() => accion.mutate({ path: `/admin/businesses/${f.id}/block` })}
                            className="flex items-center gap-1 rounded-lg border border-rose-500/40 px-2.5 py-1 text-[11px] text-rose-300 hover:bg-rose-500/10"
                          >
                            <PauseCircle className="h-3 w-3" /> Bloquear
                          </button>
                        </>
                      )}
                      {['vencido', 'pro_vencido'].includes(f.estado) && (
                        <button
                          onClick={() => accion.mutate({ path: `/admin/businesses/${f.id}/activate` })}
                          className="rounded-lg border border-emerald-500/40 px-2.5 py-1 text-[11px] text-emerald-300 hover:bg-emerald-500/10"
                        >
                          Re-activar
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {filas.isError && (
          <p className="mt-4 text-center text-sm text-rose-400">
            {filas.error instanceof Error ? filas.error.message : 'Error al cargar'}
          </p>
        )}
      </main>
    </div>
  )
}
