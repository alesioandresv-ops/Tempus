import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export default function AdminPage() {
  const [activeTab, setActiveTab] = useState<'bookings' | 'professionals' | 'services' | 'schedules'>('bookings')

  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-6xl mx-auto">
        <h1 className="text-2xl font-bold mb-6">Panel de Administración</h1>

        {/* Tabs */}
        <div className="flex gap-2 mb-6">
          <Button
            variant={activeTab === 'bookings' ? 'default' : 'outline'}
            onClick={() => setActiveTab('bookings')}
          >
            Reservas
          </Button>
          <Button
            variant={activeTab === 'professionals' ? 'default' : 'outline'}
            onClick={() => setActiveTab('professionals')}
          >
            Profesionales
          </Button>
          <Button
            variant={activeTab === 'services' ? 'default' : 'outline'}
            onClick={() => setActiveTab('services')}
          >
            Servicios
          </Button>
          <Button
            variant={activeTab === 'schedules' ? 'default' : 'outline'}
            onClick={() => setActiveTab('schedules')}
          >
            Horarios
          </Button>
        </div>

        {/* Contenido */}
        {activeTab === 'bookings' && (
          <Card>
            <CardHeader>
              <CardTitle>Reservas</CardTitle>
              <CardDescription>Gestión de reservas del negocio</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-gray-500">Próximamente...</p>
            </CardContent>
          </Card>
        )}

        {activeTab === 'professionals' && (
          <Card>
            <CardHeader>
              <CardTitle>Profesionales</CardTitle>
              <CardDescription>Gestión del equipo</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <Button>Agregar profesional</Button>
                <div className="text-gray-500">Próximamente...</div>
              </div>
            </CardContent>
          </Card>
        )}

        {activeTab === 'services' && (
          <Card>
            <CardHeader>
              <CardTitle>Servicios</CardTitle>
              <CardDescription>Catálogo de servicios</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <Button>Agregar servicio</Button>
                <div className="text-gray-500">Próximamente...</div>
              </div>
            </CardContent>
          </Card>
        )}

        {activeTab === 'schedules' && (
          <Card>
            <CardHeader>
              <CardTitle>Horarios</CardTitle>
              <CardDescription>Configuración de horarios</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-gray-500">Próximamente...</p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
