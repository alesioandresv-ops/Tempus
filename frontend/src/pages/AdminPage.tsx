import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
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
import { api, ApiError } from '@/services/api'
import type {
  AsignacionServicio,
  AsignacionServicioIn,
  DiaHorario,
  HorarioProfesional,
  PanelProfessional,
  PanelService,
  ProfesionalCreateRequest,
  ProfesionalPatchRequest,
  ServicioCreateRequest,
  ServicioPatchRequest,
  Ventana,
} from '@/types'
import { BloqueosTab } from './admin/BloqueosTab'
import { ClientesTab } from './admin/ClientesTab'
import { EstadisticasTab } from './admin/EstadisticasTab'
import { FeriadosTab } from './admin/FeriadosTab'
import { ReportesTab } from './admin/ReportesTab'
import { ReservasTab } from './admin/ReservasTab'
import { VacacionesTab } from './admin/VacacionesTab'

/**
 * Panel de administración del negocio (Fase 4 + Fase C).
 *
 * Pestañas con CRUD real sobre `/business`: servicios, profesionales,
 * horarios, reservas (con cancelar/reprogramar), bloqueos, feriados,
 * vacaciones y estadísticas.
 *
 * **Las validaciones del navegador son de conveniencia, no de
 * seguridad.** Zod evita un viaje de red por un campo mal
 * escrito; el servidor (Pydantic + el servicio + la base) es
 * la única garantía. Por eso los errores de 422 se muestran
 * tal cual llegan: son la verdad.
 */

/** El set cerrado de duraciones del catálogo. Es el mismo
 *  `DURACIONES_PERMITIDAS` del backend, escrito acá para el
 *  selector: si el negocio agrega una, se cambia en los dos
 *  lados y el 422 del servidor lo recuerda. */
const DURACIONES = [15, 30, 45, 60, 90, 120] as const

/** 0 = lunes ... 6 = domingo, el mismo índice que la base. */
const DIAS_DE_LA_SEMANA = [
  'Lunes',
  'Martes',
  'Miércoles',
  'Jueves',
  'Viernes',
  'Sábado',
  'Domingo',
] as const

/** Precio: hasta dos decimales, sin signos. El backend guarda
 *  `Numeric(12,2)` y el JSON lo viaja como string para no
 *  pasar por float. */
const PATRON_PRECIO = /^\d+(\.\d{1,2})?$/

/** WhatsApp: E.164 sin `+`, igual que el servidor. */
const PATRON_WHATSAPP = /^[1-9]\d{6,14}$/

// --------------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------------- //

/**
 * "09:00:00" (como lo devuelve la API) a "09:00" (lo que
 * acepta un `<input type="time">`).
 */
function normalizarHora(hora: string): string {
  return hora.length > 5 ? hora.slice(0, 5) : hora
}

/** El mensaje que le sirve a la persona, venga de donde venga. */
function mensajeDeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 422) {
      const mensajes = Object.values(error.fieldErrors)
      if (mensajes.length > 0) return mensajes.join(' ')
    }
    return error.message || `Error ${error.status}`
  }
  return 'No se pudo conectar con la API. Revisa que el servidor esté arriba.'
}

function MensajeDeError({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <p className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">
      {error}
    </p>
  )
}

/** Un campo con su label y su error de eschema, sin repetir el markup. */
function Campo({
  label,
  error,
  hint,
  children,
}: {
  label: string
  error?: string
  hint?: string
  children: ReactNode
}) {
  return (
    <label className="block space-y-1">
      <span className="text-sm font-medium">{label}</span>
      {children}
      {hint && !error && <span className="text-xs text-muted-foreground">{hint}</span>}
      {error && <span className="text-xs text-destructive">{error}</span>}
    </label>
  )
}

// --------------------------------------------------------------------------- //
// Servicios
// --------------------------------------------------------------------------- //

const esquemaServicio = z.object({
  name: z
    .string()
    .min(1, 'Falta el nombre')
    .max(160, 'El nombre no puede pasar de 160 caracteres'),
  duration_minutes: z
    .number({ invalid_type_error: 'Elegí una duración' })
    .refine((d) => (DURACIONES as readonly number[]).includes(d), {
      message: 'Tiene que ser una de las duraciones del catálogo',
    }),
  price: z.string().regex(PATRON_PRECIO, 'Precio con hasta dos decimales, sin signos'),
  currency: z.string().length(3, 'La divisa son 3 letras'),
  description: z.string().max(1024, 'La descripción no puede pasar de 1024 caracteres').optional(),
  color: z.string().max(32, 'El color no puede pasar de 32 caracteres').optional(),
})

type FormularioServicio = z.infer<typeof esquemaServicio>

/**
 * Formulario de alta y edición de servicio.
 *
 * `inicial === null` es alta; un servicio es edición. La
 * diferencia es solo el verbo: POST crea, PATCH cambia lo que
 * se manda. El componente se monta de nuevo por cada fila
 * editada (ver la `key` en la pestaña) para que los
 * `defaultValues` del formulario sean siempre los de la fila.
 */
function ServicioFormulario({
  inicial,
  onCerrar,
}: {
  inicial: PanelService | null
  onCerrar: () => void
}) {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormularioServicio>({
    resolver: zodResolver(esquemaServicio),
    defaultValues: inicial
      ? {
          name: inicial.name,
          duration_minutes: inicial.duration_minutes,
          price: inicial.price,
          currency: inicial.currency,
          description: inicial.description ?? '',
          color: inicial.color ?? '',
        }
      : {
          name: '',
          duration_minutes: 30,
          price: '',
          currency: 'ARS',
          description: '',
          color: '',
        },
  })

  const mutation = useMutation({
    mutationFn: (datos: ServicioCreateRequest | ServicioPatchRequest) =>
      inicial
        ? api.actualizarServicio(inicial.id, datos as ServicioPatchRequest)
        : api.crearServicio(datos as ServicioCreateRequest),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['servicios'] })
      onCerrar()
    },
    onError: (err) => setError(mensajeDeError(err)),
  })

  const onSubmit = (f: FormularioServicio) => {
    setError(null)
    mutation.mutate({
      name: f.name.trim(),
      duration_minutes: f.duration_minutes,
      price: f.price,
      currency: f.currency.trim().toUpperCase(),
      description: f.description?.trim() || null,
      color: f.color?.trim() || null,
    })
  }

  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>{inicial ? 'Editar servicio' : 'Nuevo servicio'}</CardTitle>
        <CardDescription>
          {inicial
            ? 'Solo lo que cambies se actualiza.'
            : 'El servicio se crea activo y aparece en la reserva online.'}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <Campo label="Nombre" error={errors.name?.message}>
              <Input {...register('name')} placeholder="Corte" />
            </Campo>
            <Campo label="Duración" error={errors.duration_minutes?.message}>
              <select
                {...register('duration_minutes', { valueAsNumber: true })}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                {DURACIONES.map((d) => (
                  <option key={d} value={d}>
                    {d} minutos
                  </option>
                ))}
              </select>
            </Campo>
            <Campo label="Precio" error={errors.price?.message} hint="Ejemplo: 1000.00">
              <Input {...register('price')} placeholder="1000.00" inputMode="decimal" />
            </Campo>
            <Campo label="Divisa" error={errors.currency?.message}>
              <Input {...register('currency')} placeholder="ARS" maxLength={3} />
            </Campo>
            <Campo
              label="Descripción"
              error={errors.description?.message}
            >
              <Input {...register('description')} placeholder="Opcional" />
            </Campo>
            <Campo label="Color" error={errors.color?.message} hint="Opcional, para el calendario">
              <Input {...register('color')} placeholder="#4f46e5" />
            </Campo>
          </div>
          <MensajeDeError error={error} />
          <div className="flex gap-2">
            <Button type="submit" disabled={mutation.isPending}>
              {inicial ? 'Guardar cambios' : 'Crear servicio'}
            </Button>
            <Button type="button" variant="outline" onClick={onCerrar}>
              Cancelar
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function ServiciosTab() {
  const queryClient = useQueryClient()
  const [editando, setEditando] = useState<PanelService | 'nuevo' | null>(null)

  const { data: servicios, isLoading } = useQuery({
    queryKey: ['servicios'],
    queryFn: api.listarServicios,
  })

  const archivar = useMutation({
    mutationFn: (id: string) => api.archivarServicio(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['servicios'] }),
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle>Servicios</CardTitle>
        <CardDescription>
          Catálogo de lo que se reserva. Archivar saca el servicio de la
          reserva online y lo deja en la historia.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex justify-between">
          <Button
            variant={editando === 'nuevo' ? 'default' : 'outline'}
            onClick={() => setEditando(editando === 'nuevo' ? null : 'nuevo')}
          >
            {editando === 'nuevo' ? 'Cancelar' : 'Agregar servicio'}
          </Button>
        </div>

        {editando === 'nuevo' && <ServicioFormulario inicial={null} onCerrar={() => setEditando(null)} />}
        {editando && editando !== 'nuevo' && (
          <ServicioFormulario
            key={editando.id}
            inicial={editando}
            onCerrar={() => setEditando(null)}
          />
        )}

        {isLoading ? (
          <p className="text-sm text-muted-foreground">Cargando servicios...</p>
        ) : !servicios || servicios.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Sin servicios. Crea el primero para empezar a recibir reservas.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Nombre</TableHead>
                <TableHead>Duración</TableHead>
                <TableHead>Precio</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead className="text-right">Acciones</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {servicios.map((s) => (
                <TableRow key={s.id}>
                  <TableCell className="font-medium">
                    {s.name}
                    {s.description && (
                      <span className="block text-xs font-normal text-muted-foreground">
                        {s.description}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>{s.duration_minutes} min</TableCell>
                  <TableCell>
                    {s.price} {s.currency}
                  </TableCell>
                  <TableCell>
                    <span
                      className={
                        s.is_active
                          ? 'text-xs font-medium text-green-600'
                          : 'text-xs font-medium text-muted-foreground'
                      }
                    >
                      {s.is_active ? 'Activo' : 'Inactivo'}
                    </span>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex gap-2 justify-end">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setEditando(s)}
                      >
                        Editar
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={archivar.isPending}
                        onClick={() => {
                          if (
                            window.confirm(
                              `Archivar "${s.name}"? Saldrá de la reserva online, pero sus reservas pasadas quedan intactas.`
                            )
                          ) {
                            archivar.mutate(s.id)
                          }
                        }}
                      >
                        Archivar
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {archivar.isError && <MensajeDeError error={mensajeDeError(archivar.error)} />}
      </CardContent>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Profesionales
// --------------------------------------------------------------------------- //

const esquemaProfesional = z.object({
  display_name: z
    .string()
    .min(1, 'Falta el nombre')
    .max(160, 'El nombre no puede pasar de 160 caracteres'),
  whatsapp: z
    .string()
    .regex(PATRON_WHATSAPP, 'E.164 sin "+": de 8 a 15 dígitos, sin cero al inicio')
    .optional()
    .or(z.literal('')),
  bio: z.string().max(2048, 'La bio no puede pasar de 2048 caracteres').optional(),
  color: z.string().max(32).optional(),
  avatar_url: z
    .string()
    .regex(/^https?:\/\/.+/, 'Tiene que empezar con http:// o https://')
    .optional()
    .or(z.literal('')),
})

type FormularioProfesional = z.infer<typeof esquemaProfesional>

/**
 * Formulario de alta y edición de profesional.
 *
 * El WhatsApp viaja sin `+` y vacío significa "borrar el
 * número" (la única forma de limpiarlo por PATCH). El avatar
 * vacío borra la foto por la misma razón.
 */
function ProfesionalFormulario({
  inicial,
  onCerrar,
}: {
  inicial: PanelProfessional | null
  onCerrar: () => void
}) {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormularioProfesional>({
    resolver: zodResolver(esquemaProfesional),
    defaultValues: inicial
      ? {
          display_name: inicial.display_name,
          whatsapp: inicial.whatsapp ?? '',
          bio: inicial.bio ?? '',
          color: inicial.color ?? '',
          avatar_url: inicial.avatar_url ?? '',
        }
      : {
          display_name: '',
          whatsapp: '',
          bio: '',
          color: '',
          avatar_url: '',
        },
  })

  const mutation = useMutation({
    mutationFn: (datos: ProfesionalCreateRequest | ProfesionalPatchRequest) =>
      inicial
        ? api.actualizarProfesional(inicial.id, datos as ProfesionalPatchRequest)
        : api.crearProfesional(datos as ProfesionalCreateRequest),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['profesionales'] })
      onCerrar()
    },
    onError: (err) => setError(mensajeDeError(err)),
  })

  const onSubmit = (f: FormularioProfesional) => {
    setError(null)
    mutation.mutate({
      display_name: f.display_name.trim(),
      // '' (vacío) borra el campo; undefined (no tocado) no lo cambia.
      whatsapp: f.whatsapp ?? undefined,
      bio: f.bio?.trim() || null,
      color: f.color?.trim() || null,
      avatar_url: f.avatar_url ?? undefined,
    })
  }

  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>{inicial ? 'Editar profesional' : 'Nuevo profesional'}</CardTitle>
        <CardDescription>
          Las personas que atienden. La mayoría no necesita login: solo se
          cargan para la agenda.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <Campo label="Nombre" error={errors.display_name?.message}>
              <Input {...register('display_name')} placeholder="Ana Perez" />
            </Campo>
            <Campo
              label="WhatsApp"
              error={errors.whatsapp?.message}
              hint="Sin +, solo dígitos: 5491123456789"
            >
              <Input {...register('whatsapp')} placeholder="5491123456789" inputMode="numeric" />
            </Campo>
            <Campo label="Bio" error={errors.bio?.message}>
              <Input {...register('bio')} placeholder="Opcional" />
            </Campo>
            <Campo label="Color" error={errors.color?.message}>
              <Input {...register('color')} placeholder="#4f46e5" />
            </Campo>
            <Campo
              label="Foto (avatar)"
              error={errors.avatar_url?.message}
              hint="URL de la foto, http o https"
            >
              <Input {...register('avatar_url')} placeholder="https://ejemplo.com/ana.png" />
            </Campo>
          </div>
          <MensajeDeError error={error} />
          <div className="flex gap-2">
            <Button type="submit" disabled={mutation.isPending}>
              {inicial ? 'Guardar cambios' : 'Crear profesional'}
            </Button>
            <Button type="button" variant="outline" onClick={onCerrar}>
              Cancelar
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

/**
 * Editor de los servicios que hace un profesional.
 *
 * Es un reemplazo completo, no un delta: la lista de
 * checkboxes refleja lo que se va a mandar entero. Los
 * campos de duración y precio custom son overrides
 * opcionales sobre el servicio base; vacío significa
 * "usa los del servicio".
 */
function EditorServiciosProfesional({ profesional }: { profesional: PanelProfessional }) {
  const queryClient = useQueryClient()

  const { data: servicios } = useQuery({
    queryKey: ['servicios'],
    queryFn: api.listarServicios,
  })

  const { data: asignaciones, isLoading } = useQuery({
    queryKey: ['servicios-profesional', profesional.id],
    queryFn: () => api.listarServiciosDeProfesional(profesional.id),
  })

  /** Lo que va en el PUT: por servicio, si está activo y sus overrides.
   *  Se inicializa desde la consulta una sola vez (la primera vez que
   *  llegan las asignaciones) y después lo maneja el usuario. */
  interface Fila {
    activo: boolean
    duracion: string
    precio: string
  }
  const [filas, setFilas] = useState<Record<string, Fila>>({})
  const [cargado, setCargado] = useState(false)

  useEffect(() => {
    if (!asignaciones || cargado) return
    const inicial: Record<string, Fila> = {}
    for (const a of asignaciones) {
      inicial[a.service_id] = {
        activo: a.is_active,
        duracion: a.custom_duration_minutes?.toString() ?? '',
        precio: a.custom_price ?? '',
      }
    }
    setFilas(inicial)
    setCargado(true)
  }, [asignaciones, cargado])

  const mutation = useMutation({
    mutationFn: (datos: AsignacionServicioIn[]) =>
      api.asignarServicios(profesional.id, { servicios: datos }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ['servicios-profesional', profesional.id],
      })
    },
  })

  if (!servicios) return null

  const toggle = (serviceId: string) =>
    setFilas((prev) => {
      const actual = prev[serviceId] ?? { activo: false, duracion: '', precio: '' }
      return { ...prev, [serviceId]: { ...actual, activo: !actual.activo } }
    })

  const setDato = (serviceId: string, dato: 'duracion' | 'precio', valor: string) =>
    setFilas((prev) => {
      const actual = prev[serviceId] ?? { activo: false, duracion: '', precio: '' }
      return { ...prev, [serviceId]: { ...actual, [dato]: valor } }
    })

  const seleccionadas = Object.entries(filas).filter(([, f]) => f.activo)

  /** Overrides inválidos: el backend los acepta de 5 a 480 minutos y
   *  precio con dos decimales. Si algo no cierra, el guardado no sale. */
  let overridesInvalidos: string | null = null
  for (const [serviceId, f] of seleccionadas) {
    if (f.duracion !== '') {
      const d = Number(f.duracion)
      if (!Number.isInteger(d) || d < 5 || d > 480) {
        overridesInvalidos = `Duración custom de "${serviceId}" fuera de 5-480 minutos.`
        break
      }
    }
    if (f.precio !== '' && !PATRON_PRECIO.test(f.precio)) {
      overridesInvalidos = `Precio custom de "${serviceId}" mal formado.`
      break
    }
  }

  const guardar = () =>
    mutation.mutate(
      seleccionadas.map(([service_id, f]) => ({
        service_id,
        custom_duration_minutes: f.duracion === '' ? null : Number(f.duracion),
        custom_price: f.precio === '' ? null : f.precio,
      }))
    )

  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>Servicios de {profesional.display_name}</CardTitle>
        <CardDescription>
          Qué servicios atiende. Guardar reemplaza la lista completa.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Cargando asignaciones...</p>
        ) : servicios.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Crea servicios antes de asignarlos.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Servicio</TableHead>
                <TableHead>Duración custom</TableHead>
                <TableHead>Precio custom</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {servicios.map((s) => {
                const f = filas[s.id]
                const marcado = f?.activo ?? asignaciones?.some(
                  (a: AsignacionServicio) => a.service_id === s.id && a.is_active
                ) ?? false
                return (
                  <TableRow key={s.id}>
                    <TableCell>
                      <label className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={marcado}
                          onChange={() => toggle(s.id)}
                        />
                        <span className="font-medium">{s.name}</span>
                        <span className="text-xs text-muted-foreground">
                          {s.duration_minutes} min · {s.price} {s.currency}
                        </span>
                      </label>
                    </TableCell>
                    <TableCell>
                      <Input
                        value={f?.duracion ?? ''}
                        disabled={!marcado}
                        placeholder={`${s.duration_minutes}`}
                        inputMode="numeric"
                        className="w-24"
                        onChange={(e) => setDato(s.id, 'duracion', e.target.value)}
                      />
                    </TableCell>
                    <TableCell>
                      <Input
                        value={f?.precio ?? ''}
                        disabled={!marcado}
                        placeholder={s.price}
                        inputMode="decimal"
                        className="w-28"
                        onChange={(e) => setDato(s.id, 'precio', e.target.value)}
                      />
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
        {overridesInvalidos && (
          <p className="text-sm text-destructive" role="alert">
            {overridesInvalidos}
          </p>
        )}
        {mutation.isError && <MensajeDeError error={mensajeDeError(mutation.error)} />}
        <div className="flex items-center gap-2">
          <Button
            onClick={guardar}
            disabled={mutation.isPending || overridesInvalidos !== null}
          >
            Guardar asignaciones
          </Button>
          <span className="text-xs text-muted-foreground">
            Duración y precio custom opcionales: vacío usa los del servicio.
          </span>
        </div>
      </CardContent>
    </Card>
  )
}

function ProfesionalesTab() {
  const queryClient = useQueryClient()
  const [editando, setEditando] = useState<PanelProfessional | 'nuevo' | null>(null)
  const [gestionandoServicios, setGestionandoServicios] = useState<string | null>(null)

  const { data: profesionales, isLoading } = useQuery({
    queryKey: ['profesionales'],
    queryFn: api.listarProfesionales,
  })

  const archivar = useMutation({
    mutationFn: (id: string) => api.archivarProfesional(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['profesionales'] }),
  })

  const alternarActivo = useMutation({
    mutationFn: ({ id, activo }: { id: string; activo: boolean }) =>
      api.actualizarProfesional(id, { is_active: activo }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['profesionales'] }),
  })

  const profesionalDeServicios = profesionales?.find(
    (p) => p.id === gestionandoServicios
  )

  return (
    <Card>
      <CardHeader>
        <CardTitle>Profesionales</CardTitle>
        <CardDescription>
          El equipo. Archivar saca a la persona de la reserva online; su
          cuenta de acceso, si tiene, sobrevive al archivado.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex justify-between">
          <Button
            variant={editando === 'nuevo' ? 'default' : 'outline'}
            onClick={() => setEditando(editando === 'nuevo' ? null : 'nuevo')}
          >
            {editando === 'nuevo' ? 'Cancelar' : 'Agregar profesional'}
          </Button>
        </div>

        {editando === 'nuevo' && (
          <ProfesionalFormulario inicial={null} onCerrar={() => setEditando(null)} />
        )}
        {editando && editando !== 'nuevo' && (
          <ProfesionalFormulario
            key={editando.id}
            inicial={editando}
            onCerrar={() => setEditando(null)}
          />
        )}

        {isLoading ? (
          <p className="text-sm text-muted-foreground">Cargando profesionales...</p>
        ) : !profesionales || profesionales.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Sin profesionales. Crea el primero para empezar a asignar servicios.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Nombre</TableHead>
                <TableHead>WhatsApp</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead className="text-right">Acciones</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {profesionales.map((p) => (
                <TableRow key={p.id}>
                  <TableCell className="font-medium">
                    {p.display_name}
                    {p.bio && (
                      <span className="block text-xs font-normal text-muted-foreground">
                        {p.bio}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>{p.whatsapp ?? '—'}</TableCell>
                  <TableCell>
                    <span
                      className={
                        p.is_active
                          ? 'text-xs font-medium text-green-600'
                          : 'text-xs font-medium text-muted-foreground'
                      }
                    >
                      {p.is_active ? 'Activo' : 'Inactivo'}
                    </span>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex gap-2 justify-end">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          setGestionandoServicios(
                            gestionandoServicios === p.id ? null : p.id
                          )
                        }
                      >
                        Servicios
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setEditando(p)}
                      >
                        Editar
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={alternarActivo.isPending}
                        onClick={() =>
                          alternarActivo.mutate({ id: p.id, activo: !p.is_active })
                        }
                      >
                        {p.is_active ? 'Desactivar' : 'Activar'}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={archivar.isPending}
                        onClick={() => {
                          if (
                            window.confirm(
                              `Archivar a ${p.display_name}? Dejará de aparecer en la reserva online.`
                            )
                          ) {
                            archivar.mutate(p.id)
                          }
                        }}
                      >
                        Archivar
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {archivar.isError && <MensajeDeError error={mensajeDeError(archivar.error)} />}

        {profesionalDeServicios && (
          <EditorServiciosProfesional
            key={profesionalDeServicios.id}
            profesional={profesionalDeServicios}
          />
        )}
      </CardContent>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Horarios
// --------------------------------------------------------------------------- //

/**
 * Editor de una semana de ventanas.
 *
 * Controlado: el padre tiene el estado y lo cambia por
 * inmutabilidad. El orden de las ventanas lo define el
 * backend (las ordena por inicio), así que acá no hace
 * falta ordenar para guardar.
 */
function EditorSemana({
  value,
  onChange,
}: {
  value: DiaHorario[]
  onChange: (dias: DiaHorario[]) => void
}) {
  const dia = (i: number): DiaHorario =>
    value.find((d) => d.weekday === i) ?? { weekday: i, windows: [] }

  const actualizarDia = (i: number, windows: Ventana[]) => {
    onChange([...value.filter((d) => d.weekday !== i), { weekday: i, windows }])
  }

  const actualizarVentana = (
    i: number,
    j: number,
    campo: 'start' | 'end',
    valor: string
  ) => {
    const ventanas = dia(i).windows.map((v, k) =>
      k === j ? { ...v, [campo]: valor } : v
    )
    actualizarDia(i, ventanas)
  }

  const agregarVentana = (i: number) =>
    actualizarDia(i, [...dia(i).windows, { start: '09:00', end: '12:00' }])

  const quitarVentana = (i: number, j: number) =>
    actualizarDia(
      i,
      dia(i).windows.filter((_, k) => k !== j)
    )

  return (
    <div className="space-y-3">
      {DIAS_DE_LA_SEMANA.map((nombre, i) => {
        const ventanas = dia(i).windows
        return (
          <div key={i} className="rounded-md border p-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">{nombre}</span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => agregarVentana(i)}
              >
                + Ventana
              </Button>
            </div>
            {ventanas.length === 0 ? (
              <p className="mt-1 text-xs text-muted-foreground">
                Cerrado. Sin ventanas, este día no atiende.
              </p>
            ) : (
              <div className="mt-2 space-y-2">
                {ventanas.map((v, j) => (
                  <div key={j} className="flex items-center gap-2">
                    <Input
                      type="time"
                      value={normalizarHora(v.start)}
                      onChange={(e) => actualizarVentana(i, j, 'start', e.target.value)}
                      className="w-32"
                    />
                    <span className="text-sm text-muted-foreground">a</span>
                    <Input
                      type="time"
                      value={normalizarHora(v.end)}
                      onChange={(e) => actualizarVentana(i, j, 'end', e.target.value)}
                      className="w-32"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => quitarVentana(i, j)}
                    >
                      Quitar
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

/**
 * La semana es inválida si alguna ventana termina antes de
 * empezar o si dos del mismo día se pisan. El backend lo
 * vuelve a chequear (defensa en profundidad): esto solo
 * evita el viaje.
 */
function problemaDeSemana(dias: DiaHorario[]): string | null {
  for (const d of dias) {
    for (const v of d.windows) {
      if (v.start >= v.end) {
        return (
          `El ${DIAS_DE_LA_SEMANA[d.weekday]} la ventana ${v.start}-${v.end} ` +
          'termina antes de empezar.'
        )
      }
    }
    const ordenadas = [...d.windows].sort((a, b) => a.start.localeCompare(b.start))
    for (let k = 1; k < ordenadas.length; k++) {
      if (ordenadas[k].start < ordenadas[k - 1].end) {
        return (
          `El ${DIAS_DE_LA_SEMANA[d.weekday]} las ventanas ` +
          `${ordenadas[k - 1].start}-${ordenadas[k - 1].end} y ` +
          `${ordenadas[k].start}-${ordenadas[k].end} se pisan.`
        )
      }
    }
  }
  return null
}

/**
 * Horarios del negocio y de cada profesional.
 *
 * Dos editores, la misma forma: PUT reemplaza la semana
 * entera. El del profesional tiene el caso extra de la
 * herencia (ADR-0005): sin horario propio sigue el del
 * negocio, y "volver a heredar" es mandar una semana vacía.
 */
function HorariosTab() {
  const queryClient = useQueryClient()

  // --- Horario del negocio ---
  const { data: semana, isLoading: cargandoNegocio } = useQuery({
    queryKey: ['horarios'],
    queryFn: api.obtenerHorarios,
  })
  /** `null` es "sin cambios locales": se muestra lo del servidor. */
  const [borrador, setBorrador] = useState<DiaHorario[] | null>(null)
  const diasNegocio = borrador ?? semana?.dias ?? []
  const problemaNegocio = problemaDeSemana(diasNegocio)

  const guardarNegocio = useMutation({
    mutationFn: (d: DiaHorario[]) => api.reemplazarHorarios({ dias: d }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['horarios'] })
      // Prefijo: invalida los horarios propios de todos los profesionales.
      queryClient.invalidateQueries({ queryKey: ['horarios-profesional'] })
      setBorrador(null)
    },
  })

  // --- Horarios por profesional ---
  const { data: profesionales } = useQuery({
    queryKey: ['profesionales'],
    queryFn: api.listarProfesionales,
  })
  const [profesionalId, setProfesionalId] = useState<string | null>(null)

  const {
    data: horarioPropio,
    isLoading: cargandoPropio,
    refetch: refetchPropio,
  } = useQuery<HorarioProfesional>({
    queryKey: ['horarios-profesional', profesionalId],
    queryFn: () => api.obtenerHorariosProfesional(profesionalId as string),
    enabled: profesionalId !== null,
  })

  const [borradorPropio, setBorradorPropio] = useState<DiaHorario[] | null>(null)
  // Cambiar de profesional tira el borrador: editar el de uno y saltar
  // a otro no debe llevarse los cambios sin querer.
  useEffect(() => {
    setBorradorPropio(null)
  }, [profesionalId])

  const diasProfe = borradorPropio ?? horarioPropio?.dias ?? []
  const problemaProfe = problemaDeSemana(diasProfe)

  const guardarProfe = useMutation({
    mutationFn: (d: DiaHorario[]) =>
      api.reemplazarHorariosProfesional(profesionalId as string, { dias: d }),
    onSuccess: () => {
      refetchPropio()
      setBorradorPropio(null)
    },
  })

  /** `dias: []` es la forma de la API de decir "sin horario propio":
   *  el profesional vuelve a heredar el del negocio. */
  const heredar = useMutation({
    mutationFn: () =>
      api.reemplazarHorariosProfesional(profesionalId as string, { dias: [] }),
    onSuccess: () => {
      refetchPropio()
      setBorradorPropio(null)
    },
  })

  const profesionalSeleccionado = profesionales?.find((p) => p.id === profesionalId)

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Horario del negocio</CardTitle>
          <CardDescription>
            La semana completa. Un día sin ventanas es un día cerrado. Guardar
            reemplaza la semana entera.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {cargandoNegocio ? (
            <p className="text-sm text-muted-foreground">Cargando horario...</p>
          ) : (
            <EditorSemana value={diasNegocio} onChange={setBorrador} />
          )}
          {problemaNegocio && (
            <p className="text-sm text-destructive" role="alert">
              {problemaNegocio}
            </p>
          )}
          <div className="flex items-center gap-2">
            <Button
              onClick={() => guardarNegocio.mutate(diasNegocio)}
              disabled={
                guardarNegocio.isPending || problemaNegocio !== null || borrador === null
              }
            >
              Guardar horario del negocio
            </Button>
            {borrador !== null && (
              <Button variant="outline" onClick={() => setBorrador(null)}>
                Descartar cambios
              </Button>
            )}
          </div>
          {guardarNegocio.isError && (
            <MensajeDeError error={mensajeDeError(guardarNegocio.error)} />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Horario por profesional</CardTitle>
          <CardDescription>
            Sin horario propio, el profesional hereda el del negocio (lo más
            común). Cero ventanas propias es "no atiendo ningún día": otra
            cosa.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <select
              className="flex h-10 w-full max-w-xs rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={profesionalId ?? ''}
              onChange={(e) => setProfesionalId(e.target.value || null)}
            >
              <option value="">Elegí un profesional...</option>
              {profesionales?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.display_name}
                </option>
              ))}
            </select>
          </div>

          {profesionalId === null ? (
            <p className="text-sm text-muted-foreground">
              Elegí un profesional para ver o editar su horario.
            </p>
          ) : cargandoPropio ? (
            <p className="text-sm text-muted-foreground">Cargando horario...</p>
          ) : horarioPropio?.hereda ? (
            <div className="space-y-3">
              <div className="rounded-md border border-muted bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
                {profesionalSeleccionado?.display_name} hereda el horario del
                negocio. Editá el de arriba para cambiárselo a todos, o poné
                un horario propio.
              </div>
              <EditorSemana value={diasProfe} onChange={setBorradorPropio} />
              {problemaProfe && (
                <p className="text-sm text-destructive" role="alert">
                  {problemaProfe}
                </p>
              )}
              <div className="flex items-center gap-2">
                <Button
                  onClick={() => guardarProfe.mutate(diasProfe)}
                  disabled={guardarProfe.isPending || problemaProfe !== null}
                >
                  Poner horario propio
                </Button>
                <Button
                  variant="outline"
                  disabled={heredar.isPending}
                  onClick={() => heredar.mutate()}
                >
                  Seguir heredando (sin cambios)
                </Button>
              </div>
              {guardarProfe.isError && (
                <MensajeDeError error={mensajeDeError(guardarProfe.error)} />
              )}
            </div>
          ) : (
            <div className="space-y-3">
              <EditorSemana value={diasProfe} onChange={setBorradorPropio} />
              {problemaProfe && (
                <p className="text-sm text-destructive" role="alert">
                  {problemaProfe}
                </p>
              )}
              <div className="flex items-center gap-2">
                <Button
                  onClick={() => guardarProfe.mutate(diasProfe)}
                  disabled={
                    guardarProfe.isPending ||
                    problemaProfe !== null ||
                    borradorPropio === null
                  }
                >
                  Guardar horario propio
                </Button>
                {borradorPropio !== null && (
                  <Button variant="outline" onClick={() => setBorradorPropio(null)}>
                    Descartar cambios
                  </Button>
                )}
                <Button
                  variant="outline"
                  disabled={heredar.isPending}
                  onClick={() =>
                    window.confirm(
                      'Volver al horario del negocio? Se borra el horario propio.'
                    ) && heredar.mutate()
                  }
                >
                  Volver a heredar el del negocio
                </Button>
              </div>
              {guardarProfe.isError && (
                <MensajeDeError error={mensajeDeError(guardarProfe.error)} />
              )}
              {heredar.isError && (
                <MensajeDeError error={mensajeDeError(heredar.error)} />
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Página
// --------------------------------------------------------------------------- //

type Pestaña =
  | 'bookings'
  | 'clientes'
  | 'reportes'
  | 'professionals'
  | 'services'
  | 'schedules'
  | 'bloqueos'
  | 'feriados'
  | 'vacaciones'
  | 'estadisticas'

export default function AdminPage() {
  const [activeTab, setActiveTab] = useState<Pestaña>('bookings')

  /*
   * El nombre del negocio va en el header para que un admin logueado en el
   * negocio equivocado lo note de una: la sesion es la del token, y no hay
   * nada mas en la pagina que diga de que negocio es el panel. Si el perfil
   * falla--red cortada, sesion rota--no se muestra nada: el panel no se
   * rompe por una decoracion.
   */
  const { data: perfil } = useQuery({
    queryKey: ['perfil'],
    queryFn: api.me,
    staleTime: 2 * 60 * 1000,
    retry: false,
  })

  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-6xl mx-auto">
        <h1 className="text-2xl font-bold mb-6">
          Panel de Administración
          {perfil?.business_name ? (
            <span className="ml-3 align-middle text-lg font-semibold text-gray-500">
              · {perfil.business_name}
            </span>
          ) : null}
        </h1>

        <div className="flex flex-wrap gap-2 mb-6" role="tablist">
          <Button
            variant={activeTab === 'bookings' ? 'default' : 'outline'}
            onClick={() => setActiveTab('bookings')}
          >
            Reservas
          </Button>
          <Button
            variant={activeTab === 'clientes' ? 'default' : 'outline'}
            onClick={() => setActiveTab('clientes')}
          >
            Clientes
          </Button>
          <Button
            variant={activeTab === 'reportes' ? 'default' : 'outline'}
            onClick={() => setActiveTab('reportes')}
          >
            Reportes
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
          <Button
            variant={activeTab === 'bloqueos' ? 'default' : 'outline'}
            onClick={() => setActiveTab('bloqueos')}
          >
            Bloqueos
          </Button>
          <Button
            variant={activeTab === 'feriados' ? 'default' : 'outline'}
            onClick={() => setActiveTab('feriados')}
          >
            Feriados
          </Button>
          <Button
            variant={activeTab === 'vacaciones' ? 'default' : 'outline'}
            onClick={() => setActiveTab('vacaciones')}
          >
            Vacaciones
          </Button>
          <Button
            variant={activeTab === 'estadisticas' ? 'default' : 'outline'}
            onClick={() => setActiveTab('estadisticas')}
          >
            Estadísticas
          </Button>
        </div>

        {activeTab === 'bookings' && <ReservasTab />}
        {activeTab === 'clientes' && <ClientesTab />}
        {activeTab === 'reportes' && <ReportesTab />}
        {activeTab === 'bloqueos' && <BloqueosTab />}
        {activeTab === 'feriados' && <FeriadosTab />}
        {activeTab === 'vacaciones' && <VacacionesTab />}
        {activeTab === 'estadisticas' && <EstadisticasTab />}

        {activeTab === 'professionals' && <ProfesionalesTab />}
        {activeTab === 'services' && <ServiciosTab />}
        {activeTab === 'schedules' && <HorariosTab />}
      </div>
    </div>
  )
}
