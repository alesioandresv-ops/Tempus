import { useState } from 'react'
import { Clock, Pencil, Plus } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Sheet,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

interface Service {
  id: string
  name: string
  description: string
  duration: number
  price: number
  active: boolean
}

interface ServiceForm {
  name: string
  price: number
  duration: number
  description: string
  active: boolean
}

const DURATIONS = [15, 30, 45, 60, 75, 90, 105, 120]

const emptyForm: ServiceForm = {
  name: '',
  price: 0,
  duration: 30,
  description: '',
  active: true,
}

const initialServices: Service[] = [
  {
    id: 's1',
    name: 'Corte Degradado',
    description: 'Degradado a máquina y tijera con terminaciones.',
    duration: 45,
    price: 8500,
    active: true,
  },
  {
    id: 's2',
    name: 'Corte Clásico',
    description: 'Corte tradicional con tijera.',
    duration: 30,
    price: 7000,
    active: true,
  },
  {
    id: 's3',
    name: 'Recorte Barba',
    description: 'Perfilado y recorte de barba a máquina.',
    duration: 20,
    price: 5000,
    active: true,
  },
  {
    id: 's4',
    name: 'Corte + Barba',
    description: 'Corte completo más arreglo de barba.',
    duration: 60,
    price: 12000,
    active: true,
  },
  {
    id: 's5',
    name: 'Afeitado Toalla Caliente',
    description: 'Afeitado clásico a navaja con toalla caliente.',
    duration: 30,
    price: 6500,
    active: true,
  },
  {
    id: 's6',
    name: 'Color',
    description: 'Coloración completa con tratamiento.',
    duration: 90,
    price: 15000,
    active: false,
  },
]

function formatPrice(price: number) {
  return new Intl.NumberFormat('es-AR').format(price)
}

export default function ServicesPage() {
  const [services, setServices] = useState<Service[]>(initialServices)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<ServiceForm>(emptyForm)

  const isOpen = editingId !== null

  function openEdit(service: Service) {
    setEditingId(service.id)
    setForm({
      name: service.name,
      price: service.price,
      duration: service.duration,
      description: service.description,
      active: service.active,
    })
  }

  function openNew() {
    setEditingId('new')
    setForm(emptyForm)
  }

  function toggleActive(id: string) {
    setServices((prev) =>
      prev.map((service) =>
        service.id === id ? { ...service, active: !service.active } : service
      )
    )
  }

  function handleSave() {
    console.log('Guardar servicio', form)
    setEditingId(null)
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Servicios</h1>
          <p className="text-sm text-gray-500">Gestioná precios y duración</p>
        </div>
        <Button
          className="bg-teal-500 text-white hover:bg-teal-600"
          onClick={openNew}
        >
          Nuevo servicio
        </Button>
      </div>

      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {services.map((service) => (
          <Card
            key={service.id}
            className="flex flex-col rounded-xl border bg-white p-6 shadow-sm"
          >
            <h3 className="text-lg font-semibold text-gray-900">{service.name}</h3>
            <p className="mt-1 flex-1 text-sm text-gray-500">
              {service.description}
            </p>

            <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3 text-sm text-gray-600">
                <span className="flex items-center gap-1">
                  <Clock className="h-4 w-4" />
                  {service.duration} min
                </span>
                <span className="font-bold text-gray-900">
                  ${formatPrice(service.price)}
                </span>
              </div>

              <div className="flex items-center gap-2">
                <Badge
                  className={cn(
                    'border-0',
                    service.active
                      ? 'bg-green-100 text-green-700'
                      : 'bg-gray-100 text-gray-700'
                  )}
                >
                  {service.active ? 'Activo' : 'Inactivo'}
                </Badge>
                <Switch
                  checked={service.active}
                  onCheckedChange={() => toggleActive(service.id)}
                  aria-label={`Activar ${service.name}`}
                />
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Editar ${service.name}`}
                  onClick={() => openEdit(service)}
                >
                  <Pencil className="h-4 w-4" />
                </Button>
              </div>
            </div>
          </Card>
        ))}

        <button
          type="button"
          onClick={openNew}
          className="flex min-h-[180px] flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-gray-300 bg-white p-6 text-gray-500 transition-colors hover:border-teal-400 hover:text-teal-600"
        >
          <Plus className="h-6 w-6" />
          <span className="text-sm font-medium">Agregar servicio personalizado</span>
        </button>
      </div>

      <Sheet
        open={isOpen}
        onOpenChange={(open) => {
          if (!open) setEditingId(null)
        }}
      >
        <SheetHeader>
          <SheetTitle>
            {editingId === 'new' ? 'Nuevo servicio' : 'Editar servicio'}
          </SheetTitle>
          <SheetDescription>Gestioná precios y duración</SheetDescription>
        </SheetHeader>

        <div className="space-y-4 px-6 py-4">
          <div className="space-y-1.5">
            <label
              htmlFor="service-name"
              className="text-sm font-medium text-gray-700"
            >
              Nombre
            </label>
            <Input
              id="service-name"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="Ej: Corte Degradado"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label
                htmlFor="service-price"
                className="text-sm font-medium text-gray-700"
              >
                Precio
              </label>
              <Input
                id="service-price"
                type="number"
                value={form.price}
                onChange={(event) =>
                  setForm({ ...form, price: Number(event.target.value) })
                }
              />
            </div>
            <div className="space-y-1.5">
              <label
                htmlFor="service-duration"
                className="text-sm font-medium text-gray-700"
              >
                Duración
              </label>
              <select
                id="service-duration"
                value={form.duration}
                onChange={(event) =>
                  setForm({ ...form, duration: Number(event.target.value) })
                }
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                {DURATIONS.map((duration) => (
                  <option key={duration} value={duration}>
                    {duration} min
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="service-description"
              className="text-sm font-medium text-gray-700"
            >
              Descripción
            </label>
            <Input
              id="service-description"
              value={form.description}
              onChange={(event) =>
                setForm({ ...form, description: event.target.value })
              }
              placeholder="Descripción corta"
            />
          </div>

          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-gray-700">Activo</span>
            <Switch
              checked={form.active}
              onCheckedChange={(checked) =>
                setForm({ ...form, active: checked })
              }
              aria-label="Servicio activo"
            />
          </div>
        </div>

        <SheetFooter>
          <Button
            variant="outline"
            className="flex-1"
            onClick={() => setEditingId(null)}
          >
            Cancelar
          </Button>
          <Button
            className="flex-1 bg-teal-500 text-white hover:bg-teal-600"
            onClick={handleSave}
          >
            Guardar
          </Button>
        </SheetFooter>
      </Sheet>
    </div>
  )
}
