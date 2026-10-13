import { useState, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  Calendar,
  Users,
  Scissors,
  Users2,
  Package,
  BarChart3,
  CreditCard,
  Star,
  Settings,
  Bell,
  Search,
  Menu,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

const navItems = [
  { name: 'Panel', href: '/admin/dashboard', icon: LayoutDashboard },
  { name: 'Turnos', href: '/admin/appointments', icon: Calendar },
  { name: 'Clientes', href: '/admin/clients', icon: Users },
  { name: 'Servicios', href: '/admin/services', icon: Scissors },
  { name: 'Equipo', href: '/admin/staff', icon: Users2 },
  { name: 'Inventario', href: '/admin/inventory', icon: Package },
  { name: 'Estadísticas', href: '/admin/analytics', icon: BarChart3 },
  { name: 'Pagos', href: '/admin/payments', icon: CreditCard },
  { name: 'Reseñas', href: '/admin/reviews', icon: Star },
  { name: 'Ajustes', href: '/admin/settings', icon: Settings },
]

export default function AdminLayout({ children }: { children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const { pathname } = useLocation()

  return (
    <div className="min-h-screen bg-[#FAFBFC]">
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      )}

      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 flex w-[260px] flex-col bg-[#121212] transition-transform duration-200 md:translate-x-0',
          mobileOpen ? 'translate-x-0' : '-translate-x-full'
        )}
      >
        <div className="flex h-16 items-center gap-2 px-5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-teal-500">
            <Scissors className="h-5 w-5 text-white" />
          </span>
          <span className="text-lg font-semibold text-white">Tempus</span>
          <Button
            variant="ghost"
            size="icon"
            className="ml-auto text-white/70 hover:bg-white/10 hover:text-white md:hidden"
            onClick={() => setMobileOpen(false)}
            aria-label="Cerrar menú"
          >
            <X className="h-5 w-5" />
          </Button>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4">
          {navItems.map((item) => {
            const Icon = item.icon
            const isActive =
              pathname === item.href || pathname.startsWith(`${item.href}/`)
            return (
              <Link
                key={item.name}
                to={item.href}
                onClick={() => setMobileOpen(false)}
                className={cn(
                  'flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors',
                  isActive
                    ? 'bg-white/10 text-white'
                    : 'text-white/70 hover:bg-white/5 hover:text-white'
                )}
              >
                <Icon className="h-5 w-5" />
                <span>{item.name}</span>
              </Link>
            )
          })}
        </nav>
      </aside>

      <div className="flex min-h-screen flex-col md:pl-[260px]">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-4 border-b border-[#E5E7EB] bg-white px-4 md:px-8">
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Abrir menú"
          >
            <Menu className="h-5 w-5" />
          </Button>

          <div className="relative w-full max-w-md">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <Input
              placeholder="Buscar..."
              className="h-9 rounded-lg border-gray-200 bg-gray-50 pl-9"
            />
          </div>

          <div className="ml-auto flex items-center gap-1 md:gap-2">
            <Button
              variant="ghost"
              size="icon"
              className="text-gray-600"
              aria-label="Notificaciones"
            >
              <Bell className="h-5 w-5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="text-gray-600"
              aria-label="Ajustes"
            >
              <Settings className="h-5 w-5" />
            </Button>
            <button
              type="button"
              className="ml-1 flex h-9 w-9 items-center justify-center rounded-full bg-teal-500 text-sm font-medium text-white"
              aria-label="Cuenta"
            >
              JD
            </button>
          </div>
        </header>

        <main className="flex-1 bg-[#FAFBFC] p-6 md:p-8">{children}</main>
      </div>
    </div>
  )
}
