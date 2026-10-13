import { Link } from 'react-router-dom'
import {
  Calendar,
  DollarSign,
  Ticket,
  BarChart3,
  TrendingUp,
  TrendingDown,
  ArrowRight,
} from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'

const kpis = [
  {
    label: 'Turnos de hoy',
    value: '24',
    change: '+4 desde ayer',
    positive: true,
    icon: Calendar,
  },
  {
    label: 'Recaudación',
    value: '$1,240',
    change: '+12% vs semana pasada',
    positive: true,
    icon: DollarSign,
  },
  {
    label: 'Ticket promedio',
    value: '$52',
    change: '-2% vs semana pasada',
    positive: false,
    icon: Ticket,
  },
  {
    label: 'Ocupación',
    value: '78%',
    change: '+6% vs semana pasada',
    positive: true,
    icon: BarChart3,
  },
]

const appointments = [
  {
    time: '10:00 AM',
    name: 'James Wilson',
    service: 'Corte Degradado y Recorte de Barba • con Alex',
    status: 'Confirmado',
    statusClass: 'bg-green-100 text-green-700',
  },
  {
    time: '10:45 AM',
    name: 'Marcus Lee',
    service: 'Corte Clásico • con Ryan',
    status: 'En silla',
    statusClass: 'bg-amber-100 text-amber-700',
  },
  {
    time: '11:30 AM',
    name: 'Ethan Brooks',
    service: 'Recorte de Barba • con Jordan',
    status: 'Pendiente',
    statusClass: 'bg-gray-100 text-gray-700',
  },
  {
    time: '12:15 PM',
    name: 'Oliver Chen',
    service: 'Afeitado con Toalla Caliente • con Alex',
    status: 'Confirmado',
    statusClass: 'bg-green-100 text-green-700',
  },
]

const staff = [
  { initials: 'AX', name: 'Alex', role: 'Barbero senior', status: 'Activo', dot: 'bg-green-500' },
  { initials: 'RY', name: 'Ryan', role: 'Barbero', status: 'Ocupado', dot: 'bg-amber-500' },
  { initials: 'JD', name: 'Jordan', role: 'Estilista', status: 'Descanso', dot: 'bg-gray-400' },
]

const topServices = [
  { name: 'Corte Degradado', value: 42 },
  { name: 'Recorte de Barba', value: 28 },
  { name: 'Toalla Caliente', value: 15 },
]

export default function DashboardPage() {
  return (
    <>
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4">
        {kpis.map((kpi) => {
          const Icon = kpi.icon
          const Trend = kpi.positive ? TrendingUp : TrendingDown
          return (
            <Card key={kpi.label} className="rounded-xl border shadow-sm p-6">
              <span className="flex h-10 w-10 items-center justify-center rounded-full bg-teal-500/10 text-teal-500">
                <Icon className="h-5 w-5" />
              </span>
              <p className="mt-4 text-sm text-gray-500">{kpi.label}</p>
              <p className="mt-1 text-3xl font-bold text-gray-900">{kpi.value}</p>
              <div
                className={`mt-4 flex items-center gap-1 text-sm font-medium ${
                  kpi.positive ? 'text-green-600' : 'text-red-600'
                }`}
              >
                <Trend className="h-4 w-4" />
                <span>{kpi.change}</span>
              </div>
            </Card>
          )
        })}
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="rounded-xl border shadow-sm p-6 lg:col-span-2">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900">Próximos turnos</h2>
            <Link
              to="/admin/appointments"
              className="flex items-center gap-1 text-sm font-medium text-teal-600 hover:text-teal-700"
            >
              Ver todos <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
          <div className="mt-4 divide-y divide-gray-100">
            {appointments.map((appointment) => (
              <div
                key={appointment.name}
                className="flex items-center justify-between py-3"
              >
                <div className="flex items-center gap-3">
                  <span className="rounded bg-teal-500/10 px-2 py-1 text-xs font-medium text-teal-600">
                    {appointment.time}
                  </span>
                  <div>
                    <p className="font-bold text-gray-900">{appointment.name}</p>
                    <p className="text-sm text-gray-500">{appointment.service}</p>
                  </div>
                </div>
                <Badge className={`border-0 ${appointment.statusClass}`}>
                  {appointment.status}
                </Badge>
              </div>
            ))}
          </div>
        </Card>

        <div className="space-y-6 lg:col-span-1">
          <Card className="rounded-xl border shadow-sm p-6">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-gray-900">Equipo en turno</h2>
              <span className="text-sm text-gray-500">3 de 5 activos</span>
            </div>
            <div className="mt-4 space-y-4">
              {staff.map((member) => (
                <div key={member.initials} className="flex items-center gap-3">
                  <span className="flex h-9 w-9 items-center justify-center rounded-full bg-teal-500 text-sm font-medium text-white">
                    {member.initials}
                  </span>
                  <div className="flex-1">
                    <p className="text-sm font-semibold text-gray-900">{member.name}</p>
                    <p className="text-xs text-gray-500">
                      {member.role} • {member.status}
                    </p>
                  </div>
                  <span className={`h-2 w-2 rounded-full ${member.dot}`} />
                </div>
              ))}
            </div>
          </Card>

          <Card className="rounded-xl border shadow-sm p-6">
            <h2 className="text-lg font-semibold text-gray-900">Servicio top de la semana</h2>
            <div className="mt-4 space-y-4">
              {topServices.map((service) => (
                <div key={service.name} className="space-y-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-medium text-gray-700">{service.name}</span>
                    <span className="text-gray-500">{service.value}%</span>
                  </div>
                  <Progress
                    value={service.value}
                    indicatorClassName="bg-teal-500"
                  />
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </>
  )
}
