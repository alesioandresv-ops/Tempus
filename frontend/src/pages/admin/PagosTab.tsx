import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CreditCard, Wallet, CheckCircle2, AlertCircle } from 'lucide-react'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { api } from '@/services/api'
import { cn } from '@/lib/utils'
import type {
  DepositMode,
  PaymentPolicy,
  SubscriptionStatus,
} from '@/types'
import { formatearFecha, MensajeDeError, mensajeDeError } from './comun'

/**
 * Pestaña Pagos del admin (Fase D-2).
 *
 * Tres bloques sobre el mismo tema, cada uno con su endpoint:
 *
 * 1. **Cuenta de Mercado Pago**: las credenciales que cobran las señas. El access
 *    token es write-only--la API solo devuelve la máscara--así que el campo se
 *    deja vacío para conservar el guardado; solo se manda si se pega uno nuevo.
 * 2. **Política de pago**: qué se exige al reservar online (nada, seña o pago
 *    completo). Sin política el flujo de reserva no cambia.
 * 3. **Suscripción Pro**: el plan de la plataforma. Activar devuelve un
 *    `init_point` de MP al que hay que ir a autorizar; el plan pasa a `pro`
 *    cuando MP confirma por webhook, no al volver de esa pantalla.
 */

const ETIQUETAS_POLITICA: Record<PaymentPolicy, string> = {
  none: 'No se exige pago',
  deposit: 'Seña',
  full: 'Pago completo',
}

const ETIQUETAS_ESTADO_SUSCRIPCION: Record<string, string> = {
  none: 'Sin suscripción',
  pending: 'Pendiente de autorización',
  active: 'Activa',
  paused: 'Pausada',
  in_arrears: 'Con pagos pendientes',
  cancelled: 'Cancelada',
}

/** Estilo del `<select>` nativo, para que pegue con el `Input` del sistema. */
const CLASE_SELECT =
  'flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2'

function EstadoCuenta() {
  const qc = useQueryClient()
  const config = useQuery({
    queryKey: ['panel', 'payment-config'],
    queryFn: () => api.getPaymentConfig(),
  })

  const [activo, setActivo] = useState(false)
  const [token, setToken] = useState('')
  const [publicKey, setPublicKey] = useState('')
  const [sandbox, setSandbox] = useState(false)
  // La API no devuelve `sandbox`: si no se toca, se omite al guardar para no
  // pisar un modo que el panel no puede ver.
  const [sandboxTocado, setSandboxTocado] = useState(false)

  useEffect(() => {
    if (config.data) setActivo(config.data.activo)
  }, [config.data])

  const guardar = useMutation({
    mutationFn: () =>
      api.guardarPaymentConfig({
        activo,
        access_token: token.trim() || undefined,
        public_key: publicKey.trim() || undefined,
        ...(sandboxTocado ? { sandbox } : {}),
      }),
    onSuccess: () => {
      setToken('')
      setPublicKey('')
      setSandboxTocado(false)
      qc.invalidateQueries({ queryKey: ['panel', 'payment-config'] })
    },
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-lg">
          <CreditCard className="h-5 w-5 text-muted-foreground" />
          Cuenta de Mercado Pago
        </CardTitle>
        <CardDescription>
          Cobra las señas y los pagos online. El access token se guarda cifrado y
          no se vuelve a mostrar.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {config.isLoading ? (
          <p className="text-sm text-muted-foreground">Cargando…</p>
        ) : config.isError ? (
          <MensajeDeError error={mensajeDeError(config.error)} />
        ) : (
          <>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={activo}
                onChange={(e) => setActivo(e.target.checked)}
                className="h-4 w-4 rounded border-gray-300"
              />
              Cuenta activa (habilita el cobro online)
            </label>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block space-y-1">
                <span className="text-sm font-medium">Public Key</span>
                <Input
                  placeholder="APP_USR-… (se conserva si se deja vacío)"
                  value={publicKey}
                  onChange={(e) => setPublicKey(e.target.value)}
                  autoComplete="off"
                />
              </label>
              <label className="block space-y-1">
                <span className="text-sm font-medium">Access Token</span>
                <Input
                  type="password"
                  placeholder={
                    config.data?.token_masked
                      ? config.data.token_masked
                      : 'APP_USR-…'
                  }
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  autoComplete="new-password"
                />
                <span className="block text-xs text-muted-foreground">
                  Dejalo vacío para conservar el token guardado.
                </span>
              </label>
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={sandbox}
                onChange={(e) => {
                  setSandbox(e.target.checked)
                  setSandboxTocado(true)
                }}
                className="h-4 w-4 rounded border-gray-300"
              />
              Modo sandbox (pruebas)
            </label>

            <div className="flex items-center gap-3">
              <Button onClick={() => guardar.mutate()} disabled={guardar.isPending}>
                {guardar.isPending ? 'Guardando…' : 'Guardar cuenta'}
              </Button>
              {guardar.isError && (
                <span className="text-sm text-destructive">
                  {mensajeDeError(guardar.error)}
                </span>
              )}
              {guardar.isSuccess && !guardar.isPending && (
                <span className="flex items-center gap-1 text-sm text-green-600">
                  <CheckCircle2 className="h-4 w-4" />
                  Guardado
                </span>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function PoliticaDePago() {
  const qc = useQueryClient()
  const politica = useQuery({
    queryKey: ['panel', 'payment-policy'],
    queryFn: () => api.getPaymentPolicy(),
  })

  const [policy, setPolicy] = useState<PaymentPolicy>('none')
  const [depositMode, setDepositMode] = useState<DepositMode>('fixed')
  const [depositValue, setDepositValue] = useState('')
  const [noReembolsable, setNoReembolsable] = useState(false)

  useEffect(() => {
    if (!politica.data) return
    setPolicy(politica.data.policy)
    setDepositMode(politica.data.deposit_mode ?? 'fixed')
    setDepositValue(
      politica.data.deposit_value === null ? '' : String(politica.data.deposit_value)
    )
    setNoReembolsable(politica.data.deposit_non_refundable)
  }, [politica.data])

  const guardar = useMutation({
    mutationFn: () =>
      api.guardarPaymentPolicy({
        policy,
        deposit_mode: policy === 'deposit' ? depositMode : null,
        deposit_value:
          policy === 'deposit' && depositValue.trim()
            ? Number(depositValue)
            : null,
        deposit_non_refundable: policy === 'deposit' ? noReembolsable : false,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['panel', 'payment-policy'] }),
  })

  // Validación del lado del cliente para no mandar un 422 evitable: la seña
  // necesita un valor positivo, y un porcentaje no puede pasar de 100.
  const valorNumerico = Number(depositValue)
  const errorLocal = (() => {
    if (policy !== 'deposit') return null
    if (!depositValue.trim() || Number.isNaN(valorNumerico) || valorNumerico <= 0) {
      return 'La seña necesita un monto mayor a cero.'
    }
    if (depositMode === 'percent' && valorNumerico > 100) {
      return 'El porcentaje no puede pasar de 100.'
    }
    return null
  })()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-lg">
          <Wallet className="h-5 w-5 text-muted-foreground" />
          Política de pago
        </CardTitle>
        <CardDescription>
          Qué se le pide al cliente al reservar online. Por defecto no se exige
          nada.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {politica.isLoading ? (
          <p className="text-sm text-muted-foreground">Cargando…</p>
        ) : politica.isError ? (
          <MensajeDeError error={mensajeDeError(politica.error)} />
        ) : (
          <>
            <label className="block space-y-1">
              <span className="text-sm font-medium">Exigencia</span>
              <select
                className={CLASE_SELECT}
                value={policy}
                onChange={(e) => setPolicy(e.target.value as PaymentPolicy)}
              >
                {(Object.keys(ETIQUETAS_POLITICA) as PaymentPolicy[]).map((p) => (
                  <option key={p} value={p}>
                    {ETIQUETAS_POLITICA[p]}
                  </option>
                ))}
              </select>
            </label>

            {policy === 'deposit' && (
              <div className="space-y-3 rounded-lg border bg-muted/30 p-3">
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="block space-y-1">
                    <span className="text-sm font-medium">Tipo de seña</span>
                    <select
                      className={CLASE_SELECT}
                      value={depositMode}
                      onChange={(e) => setDepositMode(e.target.value as DepositMode)}
                    >
                      <option value="fixed">Monto fijo</option>
                      <option value="percent">Porcentaje del precio</option>
                    </select>
                  </label>
                  <label className="block space-y-1">
                    <span className="text-sm font-medium">
                      {depositMode === 'percent' ? 'Porcentaje (0-100)' : 'Monto'}
                    </span>
                    <Input
                      type="number"
                      min={0}
                      step={depositMode === 'percent' ? 1 : 0.01}
                      value={depositValue}
                      onChange={(e) => setDepositValue(e.target.value)}
                    />
                  </label>
                </div>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={noReembolsable}
                    onChange={(e) => setNoReembolsable(e.target.checked)}
                    className="h-4 w-4 rounded border-gray-300"
                  />
                  La seña no es reembolsable
                </label>
              </div>
            )}

            <div className="flex items-center gap-3">
              <Button
                onClick={() => guardar.mutate()}
                disabled={guardar.isPending || errorLocal !== null}
              >
                {guardar.isPending ? 'Guardando…' : 'Guardar política'}
              </Button>
              {errorLocal && (
                <span className="text-sm text-destructive">{errorLocal}</span>
              )}
              {guardar.isError && (
                <span className="text-sm text-destructive">
                  {mensajeDeError(guardar.error)}
                </span>
              )}
              {guardar.isSuccess && !guardar.isPending && (
                <span className="flex items-center gap-1 text-sm text-green-600">
                  <CheckCircle2 className="h-4 w-4" />
                  Guardado
                </span>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function Suscripcion() {
  const qc = useQueryClient()
  const suscripcion = useQuery({
    queryKey: ['panel', 'suscripcion'],
    queryFn: () => api.getSuscripcion(),
  })

  /*
   * Activar redirige al `init_point` de MP. El plan no cambia acá: MP confirma
   * por webhook y recién ahí el negocio pasa a `pro`. Por eso, al volver, esta
   * tarjeta puede seguir mostrando `pending` hasta que llegue el webhook.
   */
  const activar = useMutation({
    mutationFn: () =>
      api.activarSuscripcion({
        back_url: `${window.location.origin}/admin?tab=pagos`,
      }),
    onSuccess: (data) => {
      window.location.href = data.init_point
    },
  })

  const cancelar = useMutation({
    mutationFn: () => api.cancelarSuscripcion(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['panel', 'suscripcion'] }),
  })

  const estado: SubscriptionStatus | undefined = suscripcion.data
  const esPro = estado?.plan === 'pro'
  const activa = estado?.status === 'active'

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-lg">
          {esPro ? (
            <CheckCircle2 className="h-5 w-5 text-green-600" />
          ) : (
            <AlertCircle className="h-5 w-5 text-muted-foreground" />
          )}
          Suscripción
        </CardTitle>
        <CardDescription>
          Plan de la plataforma. Pro habilita las funciones de recordatorios y
          reportes avanzados.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {suscripcion.isLoading ? (
          <p className="text-sm text-muted-foreground">Cargando…</p>
        ) : suscripcion.isError ? (
          <MensajeDeError error={mensajeDeError(suscripcion.error)} />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <span
                className={cn(
                  'inline-flex rounded-full px-3 py-1 text-sm font-semibold',
                  esPro ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-700'
                )}
              >
                Plan {esPro ? 'Pro' : 'Free'}
              </span>
              <span className="text-sm text-muted-foreground">
                {ETIQUETAS_ESTADO_SUSCRIPCION[estado?.status ?? 'none'] ??
                  estado?.status}
              </span>
            </div>

            {estado?.next_billing_at && activa && (
              <p className="text-sm text-muted-foreground">
                Próximo cobro: {formatearFecha(estado.next_billing_at)}
              </p>
            )}

            {!esPro && (
              <div className="space-y-2">
                <Button
                  onClick={() => activar.mutate()}
                  disabled={activar.isPending}
                >
                  {activar.isPending ? 'Generando pago…' : 'Activar plan Pro'}
                </Button>
                <p className="text-xs text-muted-foreground">
                  Te lleva a Mercado Pago a autorizar el cobro. El plan se activa
                  cuando MP lo confirma.
                </p>
                {activar.isError && (
                  <MensajeDeError error={mensajeDeError(activar.error)} />
                )}
              </div>
            )}

            {esPro && (
              <div className="space-y-2">
                <Button
                  variant="outline"
                  onClick={() => cancelar.mutate()}
                  disabled={cancelar.isPending}
                >
                  {cancelar.isPending ? 'Cancelando…' : 'Cancelar suscripción'}
                </Button>
                {cancelar.isError && (
                  <MensajeDeError error={mensajeDeError(cancelar.error)} />
                )}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

export function PagosTab() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Pagos</h1>
        <p className="text-sm text-muted-foreground">
          Cobros online, señas y el plan de la plataforma.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <EstadoCuenta />
        <PoliticaDePago />
      </div>

      <Suscripcion />
    </div>
  )
}
