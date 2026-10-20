import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  Calendar,
  Users,
  Scissors,
  Users2,
  Clock,
  Ban,
  CalendarOff,
  Plane,
  BarChart3,
  FileText,
  CreditCard,
  Settings,
  Search,
  Menu,
  X,
  LogOut,
  type LucideIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { IsotipoTempus } from '@/components/MarcaTempus'
import { api } from '@/services/api'
import { cn } from '@/lib/utils'
import type { SessionProfile } from '@/types'

/*
 * El envoltorio visual del panel: sidebar oscuro + header, con el contenido real
 * (`AdminPage`) adentro.
 *
 * **Por que vive aca y no en `src/app/admin/`.** Antes habia un layout con datos de
 * ejemplo cableados a mano en `src/app/admin/`. Ese arbol se elimino porque mostraba
 * numeros inventados a un admin logueado. Este es el mismo diseno, pero el sidebar no
 * inventa nada: cada item es una pestaña real que ya existia, y el nombre del negocio
 * sale de la sesion.
 *
 * **El estado de la pestaña no vive aca.** El componente recibe `activa` y avisa por
 * `onSeleccionar`: quien manda es la URL (`?tab=`), que es lo que permite compartir un
 * link a una pestaña y que el boton atras funcione. Si el layout tuviera su propio
 * `useState`, la URL y la vista se podrian desincronizar.
 */

/**
 * Claves de pestaña del panel. La lista y la union viven juntas a proposito: son la
 * unica fuente de verdad de "que pestañas existen", y agregar una sin la otra es el
 * error que este archivo evita.
 *
 * `panel` es la primera y la que se abre por defecto, igual que en el sidebar.
 */
export type Pestana =
  | 'panel'
  | 'bookings'
  | 'clientes'
  | 'services'
  | 'professionals'
  | 'schedules'
  | 'bloqueos'
  | 'feriados'
  | 'vacaciones'
  | 'estadisticas'
  | 'reportes'
  | 'pagos'
  | 'configuracion'

export interface ItemDeNav {
  clave: Pestana
  etiqueta: string
  icono: LucideIcon
}

/**
 * El orden del arreglo **es** el orden del sidebar.
 *
 * No estan Inventario ni Reseñas: no existen en el backend (ni tabla ni endpoint), y
 * un item de menu que no lleva a ningun lado es peor que no tenerlo.
 */
export const NAV: readonly ItemDeNav[] = [
  { clave: 'panel', etiqueta: 'Panel', icono: LayoutDashboard },
  { clave: 'bookings', etiqueta: 'Reservas', icono: Calendar },
  { clave: 'clientes', etiqueta: 'Clientes', icono: Users },
  { clave: 'services', etiqueta: 'Servicios', icono: Scissors },
  { clave: 'professionals', etiqueta: 'Profesionales', icono: Users2 },
  { clave: 'schedules', etiqueta: 'Horarios', icono: Clock },
  { clave: 'bloqueos', etiqueta: 'Bloqueos', icono: Ban },
  { clave: 'feriados', etiqueta: 'Feriados', icono: CalendarOff },
  { clave: 'vacaciones', etiqueta: 'Vacaciones', icono: Plane },
  { clave: 'estadisticas', etiqueta: 'Estadísticas', icono: BarChart3 },
  { clave: 'reportes', etiqueta: 'Reportes', icono: FileText },
  { clave: 'pagos', etiqueta: 'Pagos', icono: CreditCard },
  { clave: 'configuracion', etiqueta: 'Configuración', icono: Settings },
] as const

/** Un `?tab=` de la URL solo vale si nombra una pestaña conocida. */
export function esPestana(valor: string | null): valor is Pestana {
  return valor !== null && NAV.some((item) => item.clave === valor)
}

/** Iniciales del negocio para el avatar del header ("Barber The Peluche" -> "BT"). */
function iniciales(nombre: string | null | undefined): string {
  if (!nombre) return 'T'
  const partes = nombre.trim().split(/\s+/).filter(Boolean)
  if (partes.length === 0) return 'T'
  if (partes.length === 1) return partes[0].slice(0, 2).toUpperCase()
  return (partes[0][0] + partes[1][0]).toUpperCase()
}

interface AdminLayoutProps {
  children: ReactNode
  /** La pestaña que manda la URL. */
  activa: Pestana
  /** Avisa que se eligio otra pestaña; quien navega es `AdminPage`. */
  onSeleccionar: (pestana: Pestana) => void
  /** Perfil de la sesion; puede no estar todavia (o fallar) y el panel no se rompe. */
  perfil: SessionProfile | null | undefined
  /** Termino inicial del buscador del header (el `?q=` de la URL). */
  busqueda?: string
  /** Enter en el buscador: salta a Clientes con el termino aplicado. */
  onBuscar: (termino: string) => void
}

export function AdminLayout({
  children,
  activa,
  onSeleccionar,
  perfil,
  busqueda = '',
  onBuscar,
}: AdminLayoutProps) {
  const [mobileAbierto, setMobileAbierto] = useState(false)
  const [termino, setTermino] = useState(busqueda)
  const navigate = useNavigate()

  /*
   * El buscador del header refleja el `?q=` de la URL. Va con efecto y no como valor
   * inicial de `useState` porque cuando ya se esta en Clientes y se busca de nuevo, el
   * componente no se remonta--y el input quedaria con el termino viejo mientras la lista
   * muestra el nuevo.
   */
  useEffect(() => {
    setTermino(busqueda)
  }, [busqueda])

  const buscar = (e: FormEvent) => {
    e.preventDefault()
    setMobileAbierto(false)
    onBuscar(termino.trim())
  }

  const cerrarSesion = async () => {
    // `api.logout` limpia la sesion y avisa a `AvisoDeFinDeSesion`, que navega a
    // `/login`. El `navigate` de abajo es un respaldo por si el aviso no llegara.
    await api.logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen bg-[#FAFBFC]">
      {mobileAbierto && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={() => setMobileAbierto(false)}
          aria-hidden="true"
        />
      )}

      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 flex w-[260px] flex-col bg-[#121212] transition-transform duration-200 md:translate-x-0',
          mobileAbierto ? 'translate-x-0' : '-translate-x-full'
        )}
      >
        <div className="flex h-16 items-center gap-2 px-5">
          {/*
            El isotipo de la marca en lugar del icono generico de tijeras. El
            cuadro de color no se conserva: el logo ya trae el anillo azul/dorado
            y ponerle un fondo macizo detras le come el corte del anillo.
          */}
          <IsotipoTempus size={32} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold leading-tight text-white">
              {perfil?.business_name ?? 'Tempus'}
            </p>
            <p className="text-xs leading-tight text-white/50">Tempus</p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="text-white/70 hover:bg-white/10 hover:text-white md:hidden"
            onClick={() => setMobileAbierto(false)}
            aria-label="Cerrar menú"
          >
            <X className="h-5 w-5" />
          </Button>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4">
          {NAV.map((item) => {
            const Icono = item.icono
            const estaActiva = activa === item.clave
            return (
              <button
                key={item.clave}
                type="button"
                onClick={() => {
                  onSeleccionar(item.clave)
                  setMobileAbierto(false)
                }}
                aria-current={estaActiva ? 'page' : undefined}
                className={cn(
                  'flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors',
                  estaActiva
                    ? 'bg-white/10 text-white'
                    : 'text-white/70 hover:bg-white/5 hover:text-white'
                )}
              >
                <Icono className="h-5 w-5 shrink-0" />
                <span>{item.etiqueta}</span>
              </button>
            )
          })}
        </nav>

        <div className="border-t border-white/10 p-3">
          <button
            type="button"
            onClick={cerrarSesion}
            className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-white/70 transition-colors hover:bg-white/5 hover:text-white"
          >
            <LogOut className="h-5 w-5 shrink-0" />
            <span>Cerrar sesión</span>
          </button>
        </div>
      </aside>

      <div className="flex min-h-screen flex-col md:pl-[260px]">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-4 border-b border-[#E5E7EB] bg-white px-4 md:px-8">
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            onClick={() => setMobileAbierto(true)}
            aria-label="Abrir menú"
          >
            <Menu className="h-5 w-5" />
          </Button>

          <form onSubmit={buscar} className="relative w-full max-w-md" role="search">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <Input
              value={termino}
              onChange={(e) => setTermino(e.target.value)}
              placeholder="Buscar clientes por nombre o teléfono…"
              aria-label="Buscar clientes"
              className="h-9 rounded-lg border-gray-200 bg-gray-50 pl-9"
            />
          </form>

          <div className="ml-auto flex items-center gap-3">
            <div className="hidden text-right sm:block">
              <p className="text-sm font-medium leading-tight text-gray-900">
                {perfil?.business_name ?? 'Tu negocio'}
              </p>
              <p className="text-xs leading-tight text-gray-500">
                {perfil?.role ?? 'admin'}
              </p>
            </div>
            <span
              className="flex h-9 w-9 items-center justify-center rounded-full bg-teal-500 text-sm font-medium text-white"
              aria-hidden="true"
            >
              {iniciales(perfil?.business_name)}
            </span>
          </div>
        </header>

        <main className="flex-1 bg-[#FAFBFC] p-4 md:p-8">{children}</main>
      </div>
    </div>
  )
}
