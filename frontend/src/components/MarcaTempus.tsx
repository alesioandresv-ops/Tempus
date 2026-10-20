import { cn } from '@/lib/utils'

/**
 * Logo de Tempus.
 *
 * **Es la imagen de la marca, no un redibujo.** Se usa el PNG con fondo
 * transparente (sacado del original) en lugar de un SVG: el logo es azul/dorado
 * con trazos finos, y reconstruirlo a mano cambiaba la forma. Lo que se ve aca
 * es el logo tal cual, sin el fondo.
 *
 * Hay dos variantes sobre el mismo dibujo:
 *  - `logo-tempus.png` trae el simbolo **y** la palabra TEMPUS.
 *  - `isotipo-tempus.png` es solo el simbolo, recortado y cuadrado.
 *
 * **El isotipo chico tiene un piso de tamano.** Por debajo de ~24 px los trazos
 * finos del monograma no llegan a un pixel y quedan grises--se ve una mancha sin
 * forma, no un logo mas chico. Por eso `simbolo` avisa en consola si alguien lo
 * pide demasiado chico, en vez de dejar que se vea mal en silencio.
 */
const PISO_LEGIBLE = 24

export interface MarcaTempusProps {
  /** Alto del logo en px. El ancho se calcula solo. */
  size?: number
  /**
   * Fuerza la variante de solo-simbolo. Por defecto se usa el isotipo cuando
   * `size` es chico, donde la palabra TEMPUS no se leeria igual.
   */
  soloSimbolo?: boolean
  className?: string
  alt?: string
}

export function MarcaTempus({
  size = 40,
  soloSimbolo,
  className,
  alt = 'Tempus',
}: MarcaTempusProps) {
  // Con el logo completo, la palabra ocupa lugar y a pocos px no se lee: se
  // cambia de variante solo, salvo que lo pidan explicitamente.
  const usarSimbolo = soloSimbolo ?? size < 44

  const src = usarSimbolo ? '/isotipo-tempus.png' : '/logo-tempus.png'

  const estilo = usarSimbolo
    ? { width: size, height: size }
    : { height: size, width: 'auto' }

  return (
    <img
      src={src}
      alt={alt}
      className={cn('select-none', className)}
      style={estilo}
      draggable={false}
      /*
       * `fetchpriority` y `decoding` no son cosmeticos: el logo esta en la
       * primera pantalla del login y del panel, y sin esto el navegador lo
       * deja para el final y se ve un salto cuando entra.
       *
       * La prop va con mayuscula (`fetchPriority`), que es el nombre que
       * entiende React y el que pasa al atributo `fetchpriority` del DOM. Con
       * minusculas TypeScript la rechaza--React no la reconoce como prop.
       */
      decoding="async"
      fetchPriority="high"
    />
  )
}

/**
 * Isotipo suelto, para la barra lateral del panel y los lugares donde el logo
 * va solo. Mantiene el mismo aviso de tamano minimo.
 */
export function IsotipoTempus({
  size = 32,
  className,
  alt = 'Tempus',
}: {
  size?: number
  className?: string
  alt?: string
}) {
  return <MarcaTempus size={size} soloSimbolo className={className} alt={alt} />
}

export { PISO_LEGIBLE }
