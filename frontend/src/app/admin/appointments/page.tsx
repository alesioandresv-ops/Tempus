import { useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { cn } from '@/lib/utils'

type Status = 'Confirmado' | 'En silla' | 'Pendiente'

interface Appointment {
  id: string
  day: number
  time: string
  name: string
  service: string
  staff: string
  status: Status
}

const DAYS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
const MONTHS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

// Lunes 13 de octubre de 2025 (semana base del mock).
const BASE_DATE = new Date(2025, 9, 13)

const statusStyles: Record<Status, { block: string; badge: string }> = {
  Confirmado: {
    block: 'border-green-500 bg-green-50 text-green-800 hover:bg-green-100',
    badge: 'bg-green-100 text-green-700',
  },
  'En silla': {
    block: 'border-amber-500 bg-amber-50 text-amber-800 hover:bg-amber-100',
    badge: 'bg-amber-100 text-amber-700',
  },
  Pendiente: {
    block: 'border-gray-400 bg-gray-50 text-gray-700 hover:bg-gray-100',
    badge: 'bg-gray-100 text-gray-700',
  },
}

const appointments: Appointment[] = [
  { id: 'a1', day: 0, time: '10:00', name: 'James Wilson', service: 'Corte + Barba', staff: 'Alex', status: 'Confirmado' },
  { id: 'a2', day: 0, time: '11:30', name: 'Marcus Lee', service: 'Corte Clásico', staff: 'Ryan', status: 'Confirmado' },
  { id: 'a3', day: 1, time: '09:00', name: 'Ethan Brooks', service: 'Recorte de Barba', staff: 'Jordan', status: 'En silla' },
  { id: 'a4', day: 1, time: '15:30', name: 'Oliver Chen', service: 'Afeitado con Toalla Caliente', staff: 'Alex', status: 'Confirmado' },
  { id: 'a5', day: 2, time: '12:00', name: 'Lucas Fernández', service: 'Corte Degradado', staff: 'Alex', status: 'Confirmado' },
  { id: 'a6', day: 3, time: '16:00', name: 'Mateo Ruiz', service: 'Color', staff: 'Jordan', status: 'Pendiente' },
  { id: 'a7', day: 4, time: '10:30', name: 'Santiago Díaz', service: 'Corte + Barba', staff: 'Ryan', status: 'En silla' },
  { id: 'a8', day: 5, time: '13:00', name: 'Nicolás Gómez', service: 'Corte Degradado', staff: 'Alex', status: 'Confirmado' },
]

const timeSlots: string[] = []
for (let hour = 8; hour <= 20; hour++) {
  timeSlots.push(`${String(hour).padStart(2, '0')}:00`)
  if (hour < 20) timeSlots.push(`${String(hour).padStart(2, '0')}:30`)
}

function addDays(date: Date, days: number) {
  const next = new Date(date)
  next.setDate(next.getDate() + days)
  return next
}

export default function AppointmentsPage() {
  const [weekOffset, setWeekOffset] = useState(0)
  const [selected, setSelected] = useState<Appointment | null>(null)

  const weekStart = addDays(BASE_DATE, weekOffset * 7)
  const weekEnd = addDays(weekStart, 6)

  const weekLabel =
    weekStart.getMonth() === weekEnd.getMonth()
      ? `Semana del ${weekStart.getDate()}-${weekEnd.getDate()} ${MONTHS[weekStart.getMonth()]}`
      : `Semana del ${weekStart.getDate()} ${MONTHS[weekStart.getMonth()]} - ${weekEnd.getDate()} ${MONTHS[weekEnd.getMonth()]}`

  const weekDays = DAYS.map((label, index) => {
    const date = addDays(weekStart, index)
    return { label, date: date.getDate(), month: date.getMonth() }
  })

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <h1 className="text-xl font-semibold text-gray-900">{weekLabel}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="icon"
            aria-label="Semana anterior"
            onClick={() => setWeekOffset((offset) => offset - 1)}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            aria-label="Semana siguiente"
            onClick={() => setWeekOffset((offset) => offset + 1)}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button variant="outline" onClick={() => setWeekOffset(0)}>
            Hoy
          </Button>
          <Button className="bg-teal-500 text-white hover:bg-teal-600">
            Nuevo turno
          </Button>
        </div>
      </div>

      <div className="rounded-xl border bg-white shadow-sm">
        <div className="overflow-x-auto">
          <div className="min-w-[900px]">
            <div className="grid grid-cols-[64px_repeat(7,minmax(0,1fr))] border-b">
              <div className="p-2 text-xs font-medium text-gray-500">Hora</div>
              {weekDays.map((day) => (
                <div key={day.label} className="border-l p-2 text-center">
                  <p className="text-xs font-medium text-gray-500">{day.label}</p>
                  <p className="text-sm font-semibold text-gray-900">{day.date}</p>
                </div>
              ))}
            </div>

            <div className="max-h-[60vh] overflow-y-auto">
              {timeSlots.map((slot) => (
                <div
                  key={slot}
                  className="grid grid-cols-[64px_repeat(7,minmax(0,1fr))]"
                >
                  <div className="border-b p-1 text-[11px] text-gray-400">{slot}</div>
                  {weekDays.map((day, dayIndex) => {
                    const appointment = appointments.find(
                      (item) => item.day === dayIndex && item.time === slot
                    )
                    return (
                      <div key={day.label} className="h-14 border-b border-l p-1">
                        {appointment && (
                          <button
                            type="button"
                            onClick={() => setSelected(appointment)}
                            className={cn(
                              'flex h-full w-full flex-col justify-center overflow-hidden rounded-md border-l-4 px-2 text-left transition-colors',
                              statusStyles[appointment.status].block
                            )}
                          >
                            <span className="truncate text-xs font-semibold">
                              {appointment.name}
                            </span>
                            <span className="truncate text-[11px] opacity-80">
                              {appointment.service} con {appointment.staff}
                            </span>
                          </button>
                        )}
                      </div>
                    )
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
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
              <SheetTitle>{selected.name}</SheetTitle>
              <SheetDescription>
                {selected.service} con {selected.staff}
              </SheetDescription>
            </SheetHeader>

            <div className="space-y-3 px-6 py-4 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-gray-500">Fecha</span>
                <span className="font-medium text-gray-900">
                  {weekDays[selected.day].label} {weekDays[selected.day].date}{' '}
                  {MONTHS[weekDays[selected.day].month]}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-500">Hora</span>
                <span className="font-medium text-gray-900">{selected.time} hs</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-500">Profesional</span>
                <span className="font-medium text-gray-900">{selected.staff}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-500">Estado</span>
                <Badge className={cn('border-0', statusStyles[selected.status].badge)}>
                  {selected.status}
                </Badge>
              </div>
            </div>

            <SheetFooter>
              <Button variant="outline" className="flex-1">
                Reprogramar
              </Button>
              <Button
                variant="outline"
                className="flex-1 text-red-600 hover:text-red-700"
              >
                Cancelar
              </Button>
              <Button className="flex-1 bg-teal-500 text-white hover:bg-teal-600">
                Editar
              </Button>
            </SheetFooter>
          </>
        )}
      </Sheet>
    </div>
  )
}
