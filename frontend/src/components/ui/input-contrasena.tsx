import { forwardRef, useState, type InputHTMLAttributes } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { Input } from './input'
import { cn } from '@/lib/utils'

export interface InputContrasenaProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {}

/**
 * Input de contrasena con boton para mostrar/ocultar lo escrito.
 *
 * Existe como componente--y no como estado en cada formulario--porque hay cuatro
 * campos de contrasena (registro, login, WhatsApp y pagos) y el toggle no es una
 * decision de cada pantalla: es la misma en todas. Copiarlo cuatro veces garantiza
 * que tres queden sin el.
 *
 * **Arranca oculto**, que es el default correcto: mostrar en claro por defecto
 * expone lo que se escribe a quien mire por encima del hombro. El ojo esta para
 * cuando la persona duda de lo que tipeo, no para el caso normal.
 *
 * El boton es `type="button"`: adentro de un `<form>`, un boton sin `type` es
 * `submit` y ocultar la contrasena mandaria el formulario.
 */
export const InputContrasena = forwardRef<HTMLInputElement, InputContrasenaProps>(
  ({ className, ...props }, ref) => {
    const [visible, setVisible] = useState(false)

    return (
      <div className="relative">
        <Input
          {...props}
          ref={ref}
          type={visible ? 'text' : 'password'}
          // Deja lugar al boton para que el texto largo no pase por debajo del icono.
          className={cn('pr-10', className)}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          // `aria-label` en vez de un tooltip: el boton no tiene texto, asi que sin
          // esto un lector de pantalla anuncia "boton" y nada mas.
          aria-label={visible ? 'Ocultar contrasena' : 'Mostrar contrasena'}
          aria-pressed={visible}
          className="absolute right-0 top-0 flex h-10 w-10 items-center justify-center text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-md"
        >
          {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
    )
  }
)
InputContrasena.displayName = 'InputContrasena'
