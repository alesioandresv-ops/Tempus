import { useState } from 'react'
import { Filter, MessageCircle, MoreHorizontal, Search } from 'lucide-react'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Sheet,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'

interface Visit {
  id: string
  service: string
  barber: string
  daysAgo: number
  amount: number
  note: string
}

interface Client {
  id: string
  name: string
  phone: string
  visits: number
  totalSpent: number
  lastVisitDaysAgo: number
  note: string
  history: Visit[]
}

const MONTHS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

// Referencia fija del mock: lunes 13 de octubre de 2025.
const TODAY = new Date(2025, 9, 13)

const SERVICE_OPTIONS = [
  'Corte + Barba',
  'Corte Degradado',
  'Recorte de Barba',
  'Afeitado con Toalla Caliente',
  'Corte Clásico',
]
const BARBER_OPTIONS = ['Alex', 'Ryan', 'Jordan']

function initials(name: string) {
  return name
    .split(' ')
    .map((part) => part[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()
}

function formatDate(daysAgo: number) {
  const date = new Date(TODAY)
  date.setDate(date.getDate() - daysAgo)
  return `${date.getDate()} ${MONTHS[date.getMonth()]}`
}

function lastVisitLabel(daysAgo: number) {
  return daysAgo === 0 ? 'Hoy' : `Hace ${daysAgo} días`
}

function buildHistory(clientId: string, lastVisitDaysAgo: number, note: string): Visit[] {
  const amounts = [45, 60, 52, 48, 70]
  return amounts.map((amount, index) => ({
    id: `${clientId}-v${index + 1}`,
    service: SERVICE_OPTIONS[(index + lastVisitDaysAgo) % SERVICE_OPTIONS.length],
    barber: BARBER_OPTIONS[(index + lastVisitDaysAgo) % BARBER_OPTIONS.length],
    daysAgo: lastVisitDaysAgo + index * 18,
    amount,
    note: index === 0 ? note : '',
  }))
}

const clientData: Omit<Client, 'history'>[] = [
  { id: 'c1', name: 'James Wilson', phone: '+54 11 5555-0101', visits: 4, totalSpent: 210, lastVisitDaysAgo: 2, note: 'Le gusta navaja' },
  { id: 'c2', name: 'Marcus Lee', phone: '+54 11 5555-0102', visits: 12, totalSpent: 680, lastVisitDaysAgo: 5, note: 'Cliente leal' },
  { id: 'c3', name: 'Ethan Brooks', phone: '+54 11 5555-0103', visits: 7, totalSpent: 340, lastVisitDaysAgo: 12, note: 'Prefiere turnos de mañana' },
  { id: 'c4', name: 'Oliver Chen', phone: '+54 11 5555-0104', visits: 2, totalSpent: 95, lastVisitDaysAgo: 41, note: 'Le gusta navaja' },
  { id: 'c5', name: 'Lucas Fernández', phone: '+54 11 5555-0105', visits: 9, totalSpent: 520, lastVisitDaysAgo: 1, note: 'Siempre pide a Alex' },
  { id: 'c6', name: 'Mateo Ruiz', phone: '+54 11 5555-0106', visits: 15, totalSpent: 860, lastVisitDaysAgo: 20, note: 'Cliente leal' },
  { id: 'c7', name: 'Santiago Díaz', phone: '+54 11 5555-0107', visits: 3, totalSpent: 150, lastVisitDaysAgo: 35, note: 'Prefiere los sábados' },
  { id: 'c8', name: 'Nicolás Gómez', phone: '+54 11 5555-0108', visits: 6, totalSpent: 410, lastVisitDaysAgo: 9, note: 'Le gusta navaja' },
  { id: 'c9', name: 'Tomás Acosta', phone: '+54 11 5555-0109', visits: 1, totalSpent: 45, lastVisitDaysAgo: 60, note: 'Primera visita, sin preferencias' },
  { id: 'c10', name: 'Franco Molina', phone: '+54 11 5555-0110', visits: 11, totalSpent: 640, lastVisitDaysAgo: 3, note: 'Cliente leal' },
]

const clients: Client[] = clientData.map((client) => ({
  ...client,
  history: buildHistory(client.id, client.lastVisitDaysAgo, client.note),
}))

const stats = [
  { label: 'Total clientes', value: '128' },
  { label: 'Nuevos este mes', value: '12' },
  { label: 'Clientes inactivos', value: '18' },
]

function isActive(client: Client) {
  return client.lastVisitDaysAgo <= 30
}

export default function ClientsPage() {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Client | null>(null)

  const normalized = query.trim().toLowerCase()
  const filtered = clients.filter(
    (client) =>
      client.name.toLowerCase().includes(normalized) ||
      client.phone.replace(/\s/g, '').includes(normalized.replace(/\s/g, ''))
  )

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <h1 className="text-xl font-semibold text-gray-900">Clientes (128)</h1>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="relative w-full sm:w-64">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Buscar por nombre o teléfono..."
              className="h-9 rounded-lg border-gray-200 bg-gray-50 pl-9"
            />
          </div>
          <Button variant="outline" className="shrink-0">
            <Filter className="h-4 w-4" />
            Filtros
          </Button>
          <Button className="shrink-0 bg-teal-500 text-white hover:bg-teal-600">
            Nuevo cliente
          </Button>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        {stats.map((stat) => (
          <Card key={stat.label} className="rounded-xl border shadow-sm p-4">
            <p className="text-sm text-gray-500">{stat.label}</p>
            <p className="mt-1 text-2xl font-bold text-gray-900">{stat.value}</p>
          </Card>
        ))}
      </div>

      <Card className="rounded-xl border shadow-sm">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Cliente</TableHead>
              <TableHead>Última visita</TableHead>
              <TableHead>Visitas totales</TableHead>
              <TableHead>Gasto total</TableHead>
              <TableHead>Estado</TableHead>
              <TableHead className="text-right">Acciones</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((client) => (
              <TableRow
                key={client.id}
                onClick={() => setSelected(client)}
                className="cursor-pointer"
              >
                <TableCell>
                  <div className="flex items-center gap-3">
                    <Avatar className="h-9 w-9">
                      <AvatarFallback className="bg-teal-500 text-white">
                        {initials(client.name)}
                      </AvatarFallback>
                    </Avatar>
                    <div>
                      <p className="font-medium text-gray-900">{client.name}</p>
                      <p className="text-sm text-gray-500">{client.phone}</p>
                    </div>
                  </div>
                </TableCell>
                <TableCell className="text-gray-600">
                  {lastVisitLabel(client.lastVisitDaysAgo)}
                </TableCell>
                <TableCell className="text-gray-600">{client.visits}</TableCell>
                <TableCell className="font-medium text-gray-900">
                  ${client.totalSpent}
                </TableCell>
                <TableCell>
                  <Badge
                    className={cn(
                      'border-0',
                      isActive(client)
                        ? 'bg-green-100 text-green-700'
                        : 'bg-gray-100 text-gray-700'
                    )}
                  >
                    {isActive(client) ? 'Activo' : 'Inactivo'}
                  </Badge>
                </TableCell>
                <TableCell className="text-right">
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Acciones"
                    onClick={(event) => {
                      event.stopPropagation()
                      setSelected(client)
                    }}
                  >
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

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
                <Avatar className="h-16 w-16">
                  <AvatarFallback className="bg-teal-500 text-lg text-white">
                    {initials(selected.name)}
                  </AvatarFallback>
                </Avatar>
                <div>
                  <SheetTitle>{selected.name}</SheetTitle>
                  <SheetDescription>{selected.phone}</SheetDescription>
                </div>
              </div>
            </SheetHeader>

            <div className="space-y-5 px-6 py-4">
              <Button
                className="w-full bg-green-500 text-white hover:bg-green-600"
                onClick={() =>
                  console.log('Mensaje WhatsApp a', selected.name, selected.phone)
                }
              >
                <MessageCircle className="h-4 w-4" />
                Mensaje WhatsApp
              </Button>

              <div className="grid grid-cols-3 gap-2 text-center">
                <div className="rounded-lg border p-2">
                  <p className="text-lg font-bold text-gray-900">{selected.visits}</p>
                  <p className="text-xs text-gray-500">Visitas</p>
                </div>
                <div className="rounded-lg border p-2">
                  <p className="text-lg font-bold text-gray-900">
                    ${selected.totalSpent}
                  </p>
                  <p className="text-xs text-gray-500">Gastado</p>
                </div>
                <div className="rounded-lg border p-2">
                  <p className="text-sm font-medium text-gray-900">
                    {isActive(selected) ? 'Activo' : 'Inactivo'}
                  </p>
                  <p className="text-xs text-gray-500">Estado</p>
                </div>
              </div>

              <div>
                <h3 className="text-sm font-semibold text-gray-900">Historial</h3>
                <ul className="mt-2 divide-y">
                  {selected.history.map((visit) => (
                    <li key={visit.id} className="py-3">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-gray-900">
                          {visit.service}
                        </span>
                        <span className="text-sm font-medium text-gray-900">
                          ${visit.amount}
                        </span>
                      </div>
                      <p className="text-xs text-gray-500">
                        {visit.barber} • {formatDate(visit.daysAgo)}
                      </p>
                      {visit.note && (
                        <p className="text-xs italic text-gray-500">{visit.note}</p>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </>
        )}
      </Sheet>
    </div>
  )
}
