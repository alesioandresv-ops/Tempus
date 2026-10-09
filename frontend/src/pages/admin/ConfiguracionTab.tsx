import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import type { WhatsappConfig, WhatsappConfigSave } from '@/types'
import { api } from '@/services/api'
import { mensajeDeError, MensajeDeError } from './comun'

/**
 * Pestaña Configuración del admin (Fase D-3 + Fase 0.5).
 *
 * De los recordatorios por WhatsApp del negocio: el switch,
 * el Phone Number ID y el token de la cuenta de Meta, y el
 * nombre de la plantilla de cada recordatorio (24h y 2h).
 *
 * **El token se pega una sola vez.** La API no lo devuelve:
 * el campo va vacío y con placeholder de "conservar", y el
 * estado muestra solo los últimos 4 caracteres, que alcanzan
 * para reconocer de qué cuenta se trata.
 *
 * **Conectar WhatsApp (Fase 0.5)** es el camino "sin pegar nada":
 * el admin autoriza con su cuenta de Facebook en un popup (el SDK
 * de Meta), el navegador recibe un `code` de un solo uso, y un
 * endpoint del backend lo cambia por las credenciales del WABA.
 * Ese código nunca es un token: el intercambio ocurre del lado
 * del servidor, donde vive el `client_secret` de la app de Meta.
 *
 * Sin configuración la API responde `activo: false` con los
 * defaults: todo negocio nace con los recordatorios apagados.
 */

/** Superficie mínima del SDK de Facebook que usa Conectar WhatsApp. */
interface FacebookSDK {
  init(opciones: { appId: string; version: string }): void
  login(
    callback: (respuesta: FacebookLoginResponse) => void,
    opciones: {
      scope: string
      response_type: string
      override_default_response_type: boolean
      config_id?: string
    },
  ): void
}

interface FacebookLoginResponse {
  status: 'connected' | 'not_authorized' | 'unknown' | string
  authResponse?: {
    code?: string
    userID?: string
  } | null
}

//: El SDK se carga una sola vez por sesión: pedirlo en cada click
//: recargaría el script y reabriría el popup con doble init.
let sdkPromesa: Promise<FacebookSDK> | null = null

function sdkDeFacebook(appId: string): Promise<FacebookSDK> {
  const conSDK = window as unknown as { FB?: FacebookSDK }
  if (conSDK.FB) {
    return Promise.resolve(conSDK.FB)
  }
  sdkPromesa ??= new Promise<FacebookSDK>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = 'https://connect.facebook.net/es_LA/sdk.js'
    script.async = true
    script.crossOrigin = 'anonymous'
    script.onload = () => {
      const FB = (window as unknown as { FB?: FacebookSDK }).FB
      if (!FB) {
        reject(new Error('El SDK de Facebook cargó sin inicializar.'))
        return
      }
      FB.init({ appId, version: 'v21.0' })
      resolve(FB)
    }
    script.onerror = () => {
      reject(new Error('No se pudo cargar el SDK de Facebook.'))
    }
    document.head.appendChild(script)
  })
  return sdkPromesa
}

export function ConfiguracionTab() {
  const {
    data: config,
    isLoading,
    error,
    refetch,
  } = useQuery<WhatsappConfig>({
    queryKey: ['config-whatsapp'],
    queryFn: api.obtenerConfigWhatsapp,
    retry: false,
  })

  const [activo, setActivo] = useState(false)
  const [phoneNumberId, setPhoneNumberId] = useState('')
  const [accessToken, setAccessToken] = useState('')
  const [template24h, setTemplate24h] = useState('')
  const [template2h, setTemplate2h] = useState('')
  const [cargados, setCargados] = useState(false)
  const [guardando, setGuardando] = useState(false)
  const [guardado, setGuardado] = useState(false)
  const [errorGuardar, setErrorGuardar] = useState<string | null>(null)

  const [conectando, setConectando] = useState(false)
  const [mensajeConnect, setMensajeConnect] = useState<string | null>(null)
  const [errorConnect, setErrorConnect] = useState<string | null>(null)

  // Rellena el formulario con lo que vino de la API, una sola
  // vez por carga: un refetch posterior (p. ej. después de
  // guardar) no debe pisar lo que la persona está editando.
  useEffect(() => {
    if (config && !cargados) {
      setActivo(config.activo)
      setPhoneNumberId(config.phone_number_id ?? '')
      setTemplate24h(config.reminder_24h_template)
      setTemplate2h(config.reminder_2h_template)
      setCargados(true)
    }
  }, [config, cargados])

  const toca = (cambia: () => void) => {
    setGuardado(false)
    cambia()
  }

  const guardar = async () => {
    setErrorGuardar(null)
    setGuardando(true)
    try {
      // Los campos vacíos se omiten a propósito: la API los
      // lee como "conservar lo que haya", y el token solo se
      // manda para pegarlo o cambiarlo.
      const datos: WhatsappConfigSave = { activo }
      if (phoneNumberId.trim()) datos.phone_number_id = phoneNumberId.trim()
      if (accessToken) datos.access_token = accessToken
      if (template24h.trim()) datos.reminder_24h_template = template24h.trim()
      if (template2h.trim()) datos.reminder_2h_template = template2h.trim()
      await api.guardarConfigWhatsapp(datos)
      setGuardado(true)
      await refetch()
    } catch (e) {
      setErrorGuardar(mensajeDeError(e))
    } finally {
      setGuardando(false)
    }
  }

  const conectar = async () => {
    if (!config?.meta_app_id) return
    setConectando(true)
    setMensajeConnect(null)
    setErrorConnect(null)
    try {
      const FB = await sdkDeFacebook(config.meta_app_id)
      FB.login(
        async (respuesta) => {
          try {
            if (respuesta.status !== 'connected' || !respuesta.authResponse) {
              setMensajeConnect(
                'Conectar WhatsApp requiere autorizar la app de Meta con tu cuenta de Facebook.',
              )
              return
            }
            const code = respuesta.authResponse.code
            if (!code) {
              setErrorConnect(
                'El login de Facebook no devolvió el code de un solo uso. Volvé a intentar.',
              )
              return
            }
            // `redirect_uri` lo deriva la propia página: es el patrón del
            // login con `response_type=code` del SDK de Facebook.
            const resultado = await api.conectarWhatsapp(code, window.location.href)
            setMensajeConnect(resultado.mensaje)
            await refetch()
          } catch (e) {
            setErrorConnect(mensajeDeError(e))
          } finally {
            setConectando(false)
          }
        },
        {
          scope: 'whatsapp_business_management',
          response_type: 'code',
          override_default_response_type: true,
          ...(config.connect_config_id
            ? { config_id: config.connect_config_id }
            : {}),
        },
      )
    } catch (e) {
      setErrorConnect(mensajeDeError(e))
      setConectando(false)
    }
  }

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Configuración</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground" role="status">
            Cargando configuración...
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Conectar WhatsApp</CardTitle>
          <CardDescription>
            Autoriza con tu cuenta de Facebook a través de Meta (Embedded
            Signup). El número de WhatsApp se registra cuando esté disponible:
            sin él, la conexión queda pendiente y los recordatorios no salen.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {config?.meta_app_id ? (
            <div className="flex flex-col gap-3">
              <Button
                type="button"
                onClick={conectar}
                disabled={conectando}
                className="w-fit"
              >
                {conectando ? 'Conectando...' : 'Conectar WhatsApp'}
              </Button>
              {mensajeConnect && (
                <p
                  className="text-sm text-muted-foreground"
                  role="status"
                >
                  {mensajeConnect}
                </p>
              )}
              <MensajeDeError error={errorConnect} />
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              La plataforma no habilitó aún este flujo: falta configurar la
              app de Meta (META_APP_ID). Mientras tanto, el número y el token
              se cargan abajo.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle>Configuración</CardTitle>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                activo
                  ? 'bg-primary/10 text-primary'
                  : 'bg-muted text-muted-foreground'
              }`}
            >
              {activo ? 'Activo' : 'Inactivo'}
            </span>
          </div>
          <CardDescription>
            Recordatorios por WhatsApp: se mandan 24 horas y 2 horas antes
            de cada reserva confirmada. Sin configurar, no se manda nada.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            <button
              type="button"
              role="switch"
              aria-checked={activo}
              onClick={() => toca(() => setActivo((v) => !v))}
              className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
                activo ? 'bg-primary' : 'bg-input'
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  activo ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
            <div className="space-y-0.5">
              <span className="text-sm font-medium">Recordatorios WhatsApp</span>
              <p className="text-xs text-muted-foreground">
                {activo
                  ? 'Se avisa al cliente 24h y 2h antes de su turno.'
                  : 'Apagado: ningún recordatorio sale de este negocio.'}
              </p>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <label className="block space-y-1">
              <span className="text-sm font-medium">Phone Number ID</span>
              <Input
                value={phoneNumberId}
                placeholder="ID del número de WhatsApp (Meta)"
                onChange={(e) =>
                  toca(() => setPhoneNumberId(e.target.value))
                }
              />
              <span className="text-xs text-muted-foreground">
                Lo da Meta al configurar el número de la cuenta.
              </span>
            </label>
            <label className="block space-y-1">
              <span className="text-sm font-medium">Token de acceso</span>
              <Input
                type="password"
                value={accessToken}
                placeholder={
                  config?.token_ultimos
                    ? `Guardado (${config.token_ultimos}); dejar vacío para conservar`
                    : 'Token de acceso de Meta (se guarda cifrado)'
                }
                onChange={(e) => toca(() => setAccessToken(e.target.value))}
              />
              <span className="text-xs text-muted-foreground">
                Se guarda cifrado y no se muestra: solo se pide para
                configurar o cambiar.
              </span>
            </label>
            <label className="block space-y-1">
              <span className="text-sm font-medium">Plantilla recordatorio 24h</span>
              <Input
                value={template24h}
                placeholder="recordatorio_24h"
                onChange={(e) => toca(() => setTemplate24h(e.target.value))}
              />
            </label>
            <label className="block space-y-1">
              <span className="text-sm font-medium">Plantilla recordatorio 2h</span>
              <Input
                value={template2h}
                placeholder="recordatorio_2h"
                onChange={(e) => toca(() => setTemplate2h(e.target.value))}
              />
            </label>
          </div>

          <div className="flex items-center gap-3">
            <Button onClick={guardar} disabled={guardando}>
              {guardando ? 'Guardando...' : 'Guardar'}
            </Button>
            {guardado && (
              <span className="text-sm text-muted-foreground" role="status">
                Configuración guardada.
              </span>
            )}
          </div>

          <MensajeDeError error={errorGuardar} />
          {error && (
            <p
              className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              role="alert"
            >
              No se pudo cargar la configuración: {mensajeDeError(error)}
            </p>
          )}
        </CardContent>
      </Card>
    </>
  )
}