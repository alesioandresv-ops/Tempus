import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { api } from '@/services/api'
import { MensajeDeError, formatearFecha, mensajeDeError } from './comun'

/**
 * Pestaña Feriados del admin (Fase C).
 *
 * Un feriado es un día entero cerrado, con nombre (sin nombre el panel muestra
 * un día tachado y nadie sabe qué era). Crear un feriado saca la
 * disponibilidad pública de ese día: el servicio de horarios lo trata como un
 * cierre total.
 */
export function FeriadosTab() {
  const queryClient = useQueryClient()

  const { data: feriados, isLoading, error } = useQuery({
    queryKey: ['feriados-admin'],
    queryFn: api.listarFeriados,
  })

  const eliminar = useMutation({
    mutationFn: (id: string) => api.eliminarFeriado(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['feriados-admin'] }),
  })

  const [fecha, setFecha] = useState('')
  const [nombre, setNombre] = useState('')
  const [errorForm, setErrorForm] = useState<string | null>(null)

  const crear = useMutation({
    mutationFn: () =>
      api.crearFeriado({
        local_date: fecha,
        name: nombre.trim(),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feriados-admin'] })
      setFecha('')
      setNombre('')
    },
    onError: (e) => setErrorForm(mensajeDeError(e)),
  })

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Nuevo feriado</CardTitle>
          <CardDescription>
            Cierra la disponibilidad pública de todo el día. El nombre explica
            el cierre cuando alguien pregunta dos meses después.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block space-y-1">
              <span className="text-sm font-medium">Fecha</span>
              <Input type="date" value={fecha} onChange={(e) => setFecha(e.target.value)} />
            </label>
            <label className="block space-y-1">
              <span className="text-sm font-medium">Nombre</span>
              <Input
                placeholder="Ej: Día de la Soberanía"
                value={nombre}
                onChange={(e) => setNombre(e.target.value)}
              />
            </label>
          </div>
          <Button
            disabled={!fecha || !nombre.trim() || crear.isPending}
            onClick={() => crear.mutate()}
          >
            {crear.isPending ? 'Creando...' : 'Crear feriado'}
          </Button>
          <MensajeDeError error={errorForm} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Feriados</CardTitle>
          <CardDescription>Días enteros en los que el negocio no atiende.</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Cargando feriados...</p>
          ) : feriados && feriados.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Fecha</TableHead>
                  <TableHead>Nombre</TableHead>
                  <TableHead className="text-right">Acción</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {feriados.map((f) => (
                  <TableRow key={f.id}>
                    <TableCell className="whitespace-nowrap">{formatearFecha(f.local_date)}</TableCell>
                    <TableCell className="font-medium">{f.name}</TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={eliminar.isPending}
                        onClick={() =>
                          window.confirm(`¿Borrar el feriado "${f.name}"?`) && eliminar.mutate(f.id)
                        }
                      >
                        Borrar
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="text-sm text-muted-foreground">No hay feriados cargados.</p>
          )}
          {(eliminar.isError || error !== null) && (
            <MensajeDeError error={mensajeDeError(eliminar.error ?? error)} />
          )}
        </CardContent>
      </Card>
    </div>
  )
}