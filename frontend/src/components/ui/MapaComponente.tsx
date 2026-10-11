import React, { useState, useEffect, useRef } from "react";

/**
 * Componente de mapa arrastrable con MapLibre GL JS.
 * 
 * Permite al usuario:
 * - Mover el marker sobre el mapa
 * - Ver las coordenadas (lat, lng) en tiempo real
 * - Usar la ubicación actual del navegador
 * - Guardar las coordenadas en los campos ocultos del formulario
 * 
 * No requiere API key de Mapbox: usa el estilo open-source de MapLibre.
 */
export const MapaComponente: React.FC<{
    latInicial?: number;
    lngInicial?: number;
    onCoordenadasCambio: (lat: number, lng: number) => void;
}> = ({
    latInicial,
    lngInicial,
    onCoordenadasCambio,
}) => {
    const mapaRef = useRef<HTMLDivElement>(null);
    const [lat, setLat] = useState(latInicial ?? 0);
    const [lng, setLng] = useState(lngInicial ?? 0);
    const [ubicacionValida, setUbicacionValida] = useState(false);
    const [obteniendoUbicacion, setObteniendoUbicacion] = useState(false);

    // Cargar MapLibre desde CDN
    useEffect(() => {
        const script = document.createElement("script");
        script.src = "https://cdn.jsdelivr.net/npm/@maplibre/maplibre-gl@3.0.0/dist/maplibre-gl.js";
        script.async = true;
        script.onload = () => {
            const style = document.createElement("link");
            style.rel = "stylesheet";
            style.href = "https://cdn.jsdelivr.net/npm/@maplibre/maplibre-gl@3.0.0/dist/maplibre-gl.css";
            document.head.appendChild(style);
            if (mapaRef.current) {
                initMapa();
            }
        };
        document.head.appendChild(script);
    }, []);

    useEffect(() => {
        return () => {
            if (mapaRef.current) {
                const mapa = (mapaRef.current as any).map;
                if (mapa) {
                    mapa.off();
                    mapa.remove();
                }
            }
        };
    }, []);

    const initMapa = () => {
        const mapa = new (window as any).MaplibreGL.map({
            container: mapaRef.current,
            style: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            center: [lng, lat],
            zoom: 15,
        });

        // Marker inicial
        const marker = new (window as any).MaplibreGL.Marker({ color: "red" })
            .setLngLat([lng, lat])
            .addTo(mapa);

        // Hacer el marker arrastrable
        marker.setDraggable(true);

        // Evento al soltar el marker
        marker.on("dragend", (e: any) => {
            const nuevoLng = e.target.getLngLat().lng;
            const nuevoLat = e.target.getLngLat().lat;
            setLng(nuevoLng);
            setLat(nuevoLat);
            onCoordenadasCambio(nuevoLat, nuevoLng);
        });

        // Centrar en la ubicación inicial si existe
        if (!isNaN(lat) && !isNaN(lng)) {
            mapa.setCenter([lng, lat]);
        }

        // Guardar referencia para limpieza
        (mapaRef.current as any).map = mapa;
    };

    // Obtener ubicación actual del navegador
    const obtenerUbicacionActual = () => {
        if (!navigator.geolocation || obteniendoUbicacion) return;
        setObteniendoUbicacion(true);
        navigator.geolocation.getCurrentPosition(
            (posicion) => {
                const coords = posicion.coords;
                setLng(coords.longitude);
                setLat(coords.latitude);
                setUbicacionValida(true);
                setObteniendoUbicacion(false);
                onCoordenadasCambio(coords.latitude, coords.longitude);
            },
            (error) => {
                console.error("Error al obtener ubicación:", error);
                setObteniendoUbicacion(false);
            }
        );
    };

    return (
        <div className="space-y-4" ref={mapaRef} style={{ height: "400px", borderRadius: "8px", overflow: "hidden" }}>
            {/* Panel de control */}
            <div className="flex flex-col sm:flex-row gap-2">
                <button
                    onClick={obtenerUbicacionActual}
                    disabled={obteniendoUbicacion}
                    className="flex-1 px-3 py-2 rounded-md border border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                    {obteniendoUbicacion ? "Obteniendo..." : "Usar mi ubicación"}
                </button>

                <button
                    onClick={() => mapaRef.current?.click()}
                    className="flex-1 px-3 py-2 rounded-md border border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    title="Centrar mapa"
                >
                    Centro
                </button>
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
                            const valor = parseFloat(e.target.value);
                            if (!isNaN(valor)) {
                                setLat(valor);
                                setLng(NaN); // Invalidar lng si el usuario solo edita lat
                                onCoordenadasCambio(valor, lng);
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
                            const valor = parseFloat(e.target.value);
                            if (!isNaN(valor)) {
                                setLng(valor);
                                setLat(NaN); // Invalidar lat si el usuario solo edita lng
                                onCoordenadasCambio(lat, valor);
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
                    Haz clic en "Usar mi ubicación" o arrastra el marker para setear coordenadas
                </p>
            )}
        </div>
    );
};

export default MapaComponente;