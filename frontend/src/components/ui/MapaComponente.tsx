import { useEffect, useRef, useState } from 'react'
import { Map as MapaGL, Marker as MarkerGL } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

/**
 * Componente de mapa arrastrable con MapLibre GL JS.
 *
 * Permite al usuario:
 * - Mover el marker sobre el mapa
 * - Ver las coordenadas (lat, lng) en tiempo real
 * - Usar la ubicación actual del navegador
 * - Guardar las coordenadas en los campos ocultos del formulario
 *
 * No requiere API key: usa el estilo demo de MapLibre (`demotiles.maplibre.org`,
 * datos de OpenStreetMap). `maplibre-gl` v6 exporta `Map`/`Marker` como named
 * exports, no hay default export.
 */

//: Fallback cuando el negocio no tiene coordenadas guardadas: la API
//: devuelve `0,0` hasta que se setean (ver `ConfiguracionTab`), y `0,0` es
//: mar abierto. Paso de los Libres (Corrientes) es el default util.
const CENTRO_DEFECTO = { lat: -29.6868, lng: -57.0976 } as const

function coordenadasValidas(v: unknown): v is number {
  return typeof v === 'number' && !Number.isNaN(v) && v !== 0
}

export const MapaComponente: React.FC<{
  latInicial?: number
  lngInicial?: number
  onCoordenadasCambio: (lat: number, lng: number) => void
}> = ({ latInicial, lngInicial, onCoordenadasCambio }) => {
  const mapaContenedorRef = useRef<HTMLDivElement>(null)
  const mapaRef = useRef<MapaGL | null>(null)
  const [lat, setLat] = useState<number>(
    coordenadasValidas(latInicial) ? latInicial : CENTRO_DEFECTO.lat,
  )
  const [lng, setLng] = useState<number>(
    coordenadasValidas(lngInicial) ? lngInicial : CENTRO_DEFECTO.lng,
  )
  const [ubicacionValida, setUbicacionValida] = useState(false)
  const [obteniendoUbicacion, setObteniendoUbicacion] = useState(false)

  // Inicializa el mapa una sola vez. El centro inicial queda fijado en el
  // mount; los cambios posteriores se mueven por el drag del marker o por
  // el boton "Centro" (`flyTo`). El cleanup aguanta el doble-mount de
  // `React.StrictMode`: con `mapaRef.current` seteado a null al desmontar,
  // el segundo mount re-crea el mapa sobre el mismo contenedor.
  useEffect(() => {
    if (!mapaContenedorRef.current || mapaRef.current) return

    const mapa = new MapaGL({
      container: mapaContenedorRef.current,
      style: 'https://demotiles.maplibre.org/style.json',
      center: [lng, lat],
      zoom: 15,
    })
    mapaRef.current = mapa

    const marker = new MarkerGL({ color: 'red' })
      .setLngLat([lng, lat])
      .addTo(mapa)

    // Hacer el marker arrastrable
    marker.setDraggable(true)

    // Evento al soltar el marker
    marker.on('dragend', () => {
      const { lng: nuevoLng, lat: nuevoLat } = marker.getLngLat()
      setLng(nuevoLng)
      setLat(nuevoLat)
      onCoordenadasCambio(nuevoLat, nuevoLng)
    })

    return () => {
      marker.remove()
      mapa.remove()
      mapaRef.current = null
    }
    // Centro inicial solo en mount: el mapa no se recrea por cada setLng/setLat.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Obtener ubicación actual del navegador
  const obtenerUbicacionActual = () => {
    if (!navigator.geolocation || obteniendoUbicacion) return
    setObteniendoUbicacion(true)
    navigator.geolocation.getCurrentPosition(
      (posicion) => {
        const coords = posicion.coords
        setLng(coords.longitude)
        setLat(coords.latitude)
        setUbicacionValida(true)
        setObteniendoUbicacion(false)
        onCoordenadasCambio(coords.latitude, coords.longitude)
      },
      (error) => {
        console.error('Error al obtener ubicación:', error)
        setObteniendoUbicacion(false)
      },
    )
  }

  return (
    <div className="space-y-4">
      {/* Panel de control */}
      <div className="flex flex-col sm:flex-row gap-2">
        <button
          onClick={obtenerUbicacionActual}
          disabled={obteniendoUbicacion}
          className="flex-1 px-3 py-2 rounded-md border border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {obteniendoUbicacion ? 'Obteniendo...' : 'Usar mi ubicación'}
        </button>

        <button
          onClick={() => mapaRef.current?.flyTo({ center: [lng, lat] })}
          className="flex-1 px-3 py-2 rounded-md border border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500"
          title="Centrar mapa"
        >
          Centro
        </button>
      </div>

      {/* Mapa: contenedor con altura explicita. El ref va en un div hijo que
          llena ese contenedor; si el contenedor colapsa no hay mapa. */}
      <div className="mt-4 w-full h-[350px] rounded-xl overflow-hidden border border-white/10 bg-slate-800">
        <div ref={mapaContenedorRef} className="w-full h-full" />
      </div>

      {/* Coordenadas */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="block text-sm font-medium mb-1">Latitud</label>
          <input
            type="number"
            step="any"
            value={lat}
            onChange={(e) => {
              const valor = parseFloat(e.target.value)
              if (!isNaN(valor)) {
                setLat(valor)
                setLng(NaN) // Invalidar lng si el usuario solo edita lat
                onCoordenadasCambio(valor, lng)
              }
            }}
            className="w-full px-3 py-2 rounded-md border border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500"
            disabled={obteniendoUbicacion}
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Longitud</label>
          <input
            type="number"
            step="any"
            value={lng}
            onChange={(e) => {
              const valor = parseFloat(e.target.value)
              if (!isNaN(valor)) {
                setLng(valor)
                setLat(NaN) // Invalidar lat si el usuario solo edita lng
                onCoordenadasCambio(lat, valor)
              }
            }}
            className="w-full px-3 py-2 rounded-md border border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500"
            disabled={obteniendoUbicacion}
          />
        </div>
      </div>

      {/* Estado */}
      {obteniendoUbicacion && (
        <p className="text-sm text-gray-500">Obteniendo ubicación...</p>
      )}
      {ubicacionValida && (
        <p className="text-sm text-green-600">
          Coordenadas guardadas: Lat {lat.toFixed(6)}, Lng {lng.toFixed(6)}
        </p>
      )}
      {!ubicacionValida && !obteniendoUbicacion && (
        <p className="text-sm text-gray-500">
          Haz clic en "Usar mi ubicación" o arrastra el marker para setear
          coordenadas
        </p>
      )}
    </div>
  )
}

export default MapaComponente