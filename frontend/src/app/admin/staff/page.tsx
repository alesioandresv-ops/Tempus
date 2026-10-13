import { useState } from 'react'
import { Star } from 'lucide-react'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Sheet,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

type BarberoStatus = 'Activo' | 'Descanso' | 'Inactivo'

interface Barbero {
  id: string
  name: string
  role: string
  status: BarberoStatus
  top: boolean
  turnosHoy: number
  ticketPromedio: number
  rating: number
  horario: string
  phone: string
  email: string
  commission: number
}

interface DaySchedule {
  day: string
  enabled: boolean
  from: string
  to: string
}

const DAY_NAMES = [
  'Lunes',
  'Martes',
  'Miércoles',
  'Jueves',
  'Viernes',
  'Sábado',
  'Domingo',
]

const defaultSchedule: DaySchedule[] = DAY_NAMES.map((day, index) => ({
  day,
  enabled: index < 5,
  from: '9:00',
  to: '18:00',
}))

const barberos: Barbero[] = [
  {
    id: 'b1',
    name: 'Alex',
    role: 'Barbero Senior',
    status: 'Activo',
    top: true,
    turnosHoy: 4,
    ticketPromedio: 9200,
    rating: 4.9,
    horario: '9:00 - 18:00',
    phone: '+54 11 5555-0201',
    email: 'alex@tempus.app',
    commission: 60,
  },
  {
    id: 'b2',
    name: 'Ryan',
    role: 'Barbero',
    status: 'Activo',
    top: false,
    turnosHoy: 3,
    ticketPromedio: 8400,
    rating: 4.7,
    horario: '10:00 - 19:00',
    phone: '+54 11 5555-0202',
    email: 'ryan@tempus.app',
    commission: 55,
  },
  {
    id: 'b3',
    name: 'Jordan',
    role: 'Estilista',
    status: 'Descanso',
    top: false,
    turnosHoy: 0,
    ticketPromedio: 7900,
    rating: 4.6,
    horario: '11:00 - 20:00',
    phone: '+54 11 5555-0203',
    email: 'jordan@tempus.app',
    commission: 50,
  },
  {
    id: 'b4',
    name: 'Nico',
    role: 'Barbero',
    status: 'Inactivo',
    top: false,
    turnosHoy: 0,
    ticketPromedio: 6800,
    rating: 4.4,
    horario: '9:00 - 18:00',
    phone: '+54 11 5555-0204',
    email: 'nico@tempus.app',
    commission: 60,
  },
  {
    id: 'b5',
    name: 'Lean',
    role: 'Barbero',
    status: 'Inactivo',
    top: false,
    turnosHoy: 0,
    ticketPromedio: 6100,
    rating: 4.2,
    horario: '9:00 - 18:00',
    phone: '+54 11 5555-0205',
    email: 'lean@tempus.app',
    commission: 60,
  },
]

function initials(name: string) {
  const parts = name.trim().split(/\s+/)
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[1][0]).toUpperCase()
}

function formatPrice(price: number) {
  return new Intl.NumberFormat('es-AR').format(price)
}

const activeCount = barberos.filter((b) => b.status !== 'Inactivo').length

export default function StaffPage() {
  const [selected, setSelected] = useState<Barbero | null>(null)
  const [schedule, setSchedule] = useState<DaySchedule[]>(defaultSchedule)
  const [commission, setCommission] = useState(60)

  function openSheet(barbero: Barbero) {
    setSelected(barbero)
    setSchedule(defaultSchedule)
    setCommission(barbero.commission)
  }

  function toggleDay(index: number, enabled: boolean) {
    setSchedule((prev) =>
      prev.map((day, i) => (i === index ? { ...day, enabled } : day))
    )
  }

  const freeDays = schedule.filter((day) => !day.enabled).map((day) => day.day)

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Equipo</h1>
          <p className="text-sm text-gray-500">
            {activeCount} de {barberos.length} activos
          </p>
        </div>
        <Button className="bg-teal-500 text-white hover:bg-teal-600">
          Agregar barbero
        </Button>
      </div>

      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {barberos.map((barbero) => (
          <Card
            key={barbero.id}
            onClick={() => openSheet(barbero)}
            className="cursor-pointer rounded-xl border bg-white p-6 shadow-sm"
          >
            <div className="flex items-start gap-4">
              <Avatar className="h-14 w-14">
                <AvatarFallback className="bg-teal-500 text-lg text-white">
                  {initials(barbero.name)}
                </AvatarFallback>
              </Avatar>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <h3 className="text-lg font-semibold text-gray-900">
                    {barbero.name}
                  </h3>
                  {barbero.top && (
                    <Badge className="border-0 bg-amber-100 text-amber-700">
                      <Star className="h-3 w-3 fill-amber-500 text-amber-500" />
                      Top
                    </Badge>
                  )}
                </div>
                <p className="text-sm text-gray-500">{barbero.role}</p>
                <div className="mt-1 flex items-center gap-1.5">
                  <span
                    className={cn(
                      'h-2 w-2 rounded-full',
                      barbero.status === 'Activo'
                        ? 'bg-green-500'
                        : 'bg-gray-400'
                    )}
                  />
                  <span className="text-xs text-gray-500">{barbero.status}</span>
                </div>
              </div>
            </div>

            <div className="mt-4 grid grid-cols-3 gap-2 rounded-lg border border-gray-100 p-3 text-center">
              <div>
                <p className="text-lg font-bold text-gray-900">
                  {barbero.turnosHoy}
                </p>
                <p className="text-xs text-gray-500">Turnos hoy</p>
              </div>
              <div>
                <p className="text-lg font-bold text-gray-900">
                  ${formatPrice(barbero.ticketPromedio)}
                </p>
                <p className="text-xs text-gray-500">Ticket promedio</p>
              </div>
              <div>
                <p className="flex items-center justify-center gap-1 text-lg font-bold text-gray-900">
                  {barbero.rating}
                  <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" />
                </p>
                <p className="text-xs text-gray-500">Valoración</p>
              </div>
            </div>

            <div className="mt-4 flex items-center justify-between">
              <span className="text-sm text-gray-600">Hoy {barbero.horario}</span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={(event) => {
                    event.stopPropagation()
                    console.log('Ver agenda de', barbero.name)
                  }}
                >
                  Ver agenda
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={(event) => {
                    event.stopPropagation()
                    openSheet(barbero)
                  }}
                >
                  Editar
                </Button>
              </div>
            </div>
          </Card>
        ))}
      </div>

      <Sheet
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null)
        }}
      >
        {selected && (
          <>
            <SheetHeader>
              <div className="flex items-center gap-4">
                <Avatar className="h-20 w-20">
                  <AvatarFallback className="bg-teal-500 text-2xl text-white">
                    {initials(selected.name)}
                  </AvatarFallback>
                </Avatar>
                <div>
                  <SheetTitle>{selected.name}</SheetTitle>
                  <SheetDescription>{selected.role}</SheetDescription>
                </div>
              </div>
            </SheetHeader>

            <div className="space-y-5 px-6 py-4">
              <div className="space-y-1 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-gray-500">Teléfono</span>
                  <span className="font-medium text-gray-900">
                    {selected.phone}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-gray-500">Email</span>
                  <span className="font-medium text-gray-900">
                    {selected.email}
                  </span>
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-gray-700">
                    Comisión
                  </span>
                  <span className="text-sm font-medium text-gray-900">
                    {commission}%
                  </span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={commission}
                  onChange={(event) => setCommission(Number(event.target.value))}
                  className="mt-2 w-full accent-teal-500"
                  aria-label="Comisión"
                />
              </div>

              <div>
                <h3 className="text-sm font-semibold text-gray-900">
                  Horario semanal
                </h3>
                <div className="mt-2 space-y-2">
                  {schedule.map((day, index) => (
                    <div
                      key={day.day}
                      className="flex items-center justify-between"
                    >
                      <span className="text-sm text-gray-700">{day.day}</span>
                      <div className="flex items-center gap-3">
                        <span className="text-xs text-gray-500">
                          {day.enabled ? `${day.from} - ${day.to}` : 'Libre'}
                        </span>
                        <Switch
                          checked={day.enabled}
                          onCheckedChange={(checked) =>
                            toggleDay(index, checked)
                          }
                          aria-label={day.day}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-lg border p-3 text-sm">
                <p className="text-gray-500">Días libres</p>
                <p className="font-medium text-gray-900">
                  {freeDays.length > 0 ? freeDays.join(', ') : 'Ninguno'}
                </p>
              </div>

              <Button
                variant="outline"
                className="w-full text-red-600 hover:text-red-700"
                onClick={() => console.log('Desactivar barbero', selected.name)}
              >
                Desactivar barbero
              </Button>
            </div>

            <SheetFooter>
              <Button
                variant="outline"
                className="flex-1"
                onClick={() => setSelected(null)}
              >
                Cancelar
              </Button>
              <Button
                className="flex-1 bg-teal-500 text-white hover:bg-teal-600"
                onClick={() => {
                  console.log('Guardar barbero', {
                    ...selected,
                    commission,
                    schedule,
                  })
                  setSelected(null)
                }}
              >
                Guardar
              </Button>
            </SheetFooter>
          </>
        )}
      </Sheet>
    </div>
  )
}
