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
 *
 * **`roles` es el segundo filtro, despues de la sesion.** Con la prop definida, una
 * sesion que pasa el primer filtro todavia tiene que declarar un rol de la lista, y
 * eso solo lo sabe la API: el claim del token no se toca porque es una cache de 15
 * minutos (ver `scopes.py`), y un token viejo puede mentir sobre el rol que ya no se
 * tiene. `api.me()` es la verdad, y el rol sale de ahi--la misma llamada que va a
 * usar la pagina para pintar distinto segun el scope.
 */
export default function ProtectedRoute({
  children,
  roles,
}: {
  children: React.ReactNode
  roles?: string[]
}) {
  const location = useLocation()
  const [estado, setEstado] = useState<'comprobando' | 'dentro' | 'fuera' | 'sin_rol'>('comprobando')

  useEffect(() => {
    let vigente = true

    /** Decide si la sesion (ya confirmada) puede entrar a esta ruta. */
    async function verificarRol() {
      // Sin lista de roles, cualquier sesion entra: es lo que hace /admin hoy.
      if (!roles) {
        setEstado('dentro')
        return
      }
      try {
        const perfil = await api.me()
        // Un catch no deberia llegar aca con sesion viva: el 401 ya lo maneja el
        // interceptor y el 403 de un token de plataforma es este mismo caso--un
        // principal que existe pero no es de negocio y no tiene rol que matchear.
        if (vigente) setEstado(roles.includes(perfil.role) ? 'dentro' : 'sin_rol')
      } catch {
        if (vigente) setEstado('sin_rol')
      }
    }

    async function comprobar() {
      const token = accessTokenActual()
      if (token && !vencePronto(token)) {
        await verificarRol()
        return
      }

      // Sin token usable: lo unico que puede devolver algo es la cookie. Si falla--no
      // hay cookie, la familia esta revocada, no hay red--no hay nada mas que probar.
      const renovar = await api.refresh()
      if (vigente && renovar) await verificarRol()
      else if (vigente) setEstado('fuera')
    }

    void comprobar()

    return () => {
      vigente = false
    }
    // `roles` es parte de la decision de la puerta: si cambia, hay que volver a
    // decidir. El resto de los datos del render no participan del chequeo.
  }, [roles])

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

  if (estado === 'sin_rol') {
    /*
     * Sesion viva pero rol que no matchea la ruta. No es un logout--la persona sigue
     * adentro-- asi que no va al login: va al panel que su rol si puede usar. Si el
     * backend dice que el principal no pertenece a ningun negocio, /admin tampoco le
     * va a servir, pero es un caso que hoy no tiene donde caer y el login no lo
     * mejoraria.
     */
    return <Navigate to="/admin" replace />
  }

  return <>{children}</>
}