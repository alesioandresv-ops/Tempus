import { useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { accessTokenActual, api, vencePronto } from '@/services/api'

/**
 * Puerta de las paginas que necesitan sesion.
 *
 * **Un F5 deja la pestana sin access token, y esta es la razon de que exista.** El
 * token vive en memoria--ver el comentario de `accessTokenActual`-- y la cookie del
 * refresh sobrevive a la recarga. Asi que al montar hay dos caminos: o hay un token
 * vigente, o hay que pedir uno nuevo con la cookie. Sin este componente, recargar el
 * panel echaba a la persona al login aunque su sesion siguiera viva.
 *
 * **El refresh se pide una vez y solo si hace falta.** Con un token vigente--aunque
 * sea por un segundo--no se toca la cookie: cada refresh es una rotacion, y rotar sin
 * necesidad solo consume el arbol de la familia y le acorta la vida a la sesion de la
 * persona.
 *
 * **No se decide la sesion leyendo el token y ya.** Que el token exista y no venza es
 * necesario pero no suficiente: una familia revocada--por reuso, o porque un
 * admin la corto--deja tokens validos en el navegador que el backend va a rechazar.
 * La unica manera de saberlo es preguntarle, y por eso el camino sin token pide
 * `/auth/refresh` en vez de asumir que la cookie esta.
 */
export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  const [estado, setEstado] = useState<'comprobando' | 'dentro' | 'fuera'>('comprobando')

  useEffect(() => {
    let vigente = true

    async function comprobar() {
      const token = accessTokenActual()
      if (token && !vencePronto(token)) {
        setEstado('dentro')
        return
      }

      // Sin token usable: lo unico que puede devolver algo es la cookie. Si falla--no
      // hay cookie, la familia esta revocada, no hay red--no hay nada mas que probar.
      const renovar = await api.refresh()
      if (vigente) setEstado(renovar ? 'dentro' : 'fuera')
    }

    void comprobar()

    return () => {
      vigente = false
    }
  }, [])

  /**
   * Mientras se comprueba se renderiza algo. Saltar directo a `Navigate` haria que un
   * F5 en el panel envoyara al login durante el tiempo del viaje al backend--y volver
   * atras si el refresh va bien, con lo que la persona ve un parpadeo de login en cada
   * recarga.
   */
  if (estado === 'comprobando') {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <p className="text-sm text-gray-500">Comprobando tu sesion...</p>
      </div>
    )
  }

  if (estado === 'fuera') {
    /*
     * `state.from` es para poder volver a donde estaba. Sin el, entrar y fallar--por
     * ejemplo, una sesion que expiro mientras completaba un formulario--deja a la
     * persona en la raiz, que es un lugar del que no se deduce adonde queria ir.
     */
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return <>{children}</>
}