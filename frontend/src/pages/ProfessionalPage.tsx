import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export default function ProfessionalPage() {
  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-4xl mx-auto">
        <h1 className="text-2xl font-bold mb-6">Panel del Profesional</h1>
        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Mi agenda</CardTitle>
              <CardDescription>Turnos de hoy</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-gray-500">Próximamente...</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Próximos turnos</CardTitle>
              <CardDescription>Agenda de la semana</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-gray-500">Próximamente...</p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
