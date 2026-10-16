import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowRight,
  BarChart3,
  BellRing,
  Calendar,
  CalendarCheck,
  Check,
  ChevronDown,
  Clock,
  CreditCard,
  Globe,
  Heart,
  Instagram,
  Link2,
  MessageCircle,
  Menu,
  QrCode,
  Rocket,
  ShieldCheck,
  Sparkles,
  Users2,
  Wallet,
  X,
  Zap,
} from "lucide-react";
import { CLAVES_TEMAS, TEMAS, esClaveTema, type ClaveTema, type Tema } from "./temas";

/**
 * Landing de captacion: la pagina que ve un negocio que todavia no es cliente.
 *
 * **Es 100% estatica y no llama a la API**: una pagina de marketing no tiene por que
 * caerse si el backend esta abajo (y asi se puede mirar y capturar sin levantar
 * Postgres).
 *
 * **Hay tres estilos y se elige con `?tema=`** (`aurora` por defecto). El contenido y
 * la estructura son los mismos; la piel sale de `temas.ts`. Cuando se elija uno, se
 * borra el selector y los otros dos temas, y el/los elegido/s queda fijo.
 *
 * **No hay testimonios ni cifras inventadas**: los que habia eran de relleno y
 * publicarlos como reales seria publicidad enganosa. En su lugar va una seccion de
 * garantias verificables (sin tarjeta, sin permanencia, soporte humano). Los
 * testimonios reales vuelven cuando existan clientes que los den.
 */

/** Instagram de Tempus. Unico canal publico por ahora; no hay WhatsApp todavia. */
const INSTAGRAM = "https://www.instagram.com/tempus.web?cplk=OGFwcDY0NzEwaXAw";

const NAV = [
  { etiqueta: "Funciones", href: "#funciones" },
  { etiqueta: "Cómo funciona", href: "#como-funciona" },
  { etiqueta: "Precios", href: "#precios" },
  { etiqueta: "Preguntas", href: "#preguntas" },
];

/** Tira de garantias bajo el hero. Cortas y verificables: nada que no podamos cumplir. */
const CONFIANZA = [
  { icono: ShieldCheck, texto: "Sin tarjeta para empezar" },
  { icono: Clock, texto: "El primer mes es gratis" },
  { icono: Heart, texto: "Cancelás cuando quieras" },
  { icono: MessageCircle, texto: "Te contesta una persona" },
];

const FUNCIONES = [
  {
    icono: CalendarCheck,
    titulo: "Agenda abierta 24/7",
    texto:
      "Tus clientes reservan desde el celular a cualquier hora. Se terminó el ida y vuelta por mensajes para buscar un hueco.",
  },
  {
    icono: MessageCircle,
    titulo: "Recordatorios por WhatsApp",
    texto:
      "Mensajes automáticos 24 h y 2 h antes del turno. Es la función que más ausencias evita, y funciona sola.",
  },
  {
    icono: CreditCard,
    titulo: "Señas y pagos con Mercado Pago",
    texto:
      "Pedí una seña al reservar y reducí los turnos perdidos. El dinero entra a tu cuenta, sin manejar efectivo.",
  },
  {
    icono: Users2,
    titulo: "Equipo y horarios",
    texto:
      "Cada profesional con su agenda, sus servicios y sus descansos. Vos decidís quién atiende qué y cuándo.",
  },
  {
    icono: BarChart3,
    titulo: "Reportes claros",
    texto:
      "Cuánto facturaste, qué servicios se piden más y quién rinde más. Números que se entienden de un vistazo.",
  },
  {
    icono: QrCode,
    titulo: "Tu link y tu QR",
    texto:
      "Una página propia con tu nombre y un QR para el mostrador. Pegalo en Instagram, WhatsApp o imprimilo.",
  },
];

const PASOS = [
  {
    icono: Rocket,
    titulo: "Creá tu cuenta",
    texto: "Cargás los datos de tu negocio en dos minutos. No hay nada que instalar.",
  },
  {
    icono: Calendar,
    titulo: "Sumá servicios, equipo y horarios",
    texto: "Definís precios, duración y quién atiende cada cosa. Tu agenda queda lista.",
  },
  {
    icono: Link2,
    titulo: "Compartí tu link",
    texto: "Pegalo en tu Instagram, mandalo por WhatsApp o poné el QR en el mostrador.",
  },
];

/** Garantias: lo que prometemos y podemos cumplir hoy, sin numeros ni testimonios. */
const GARANTIAS = [
  {
    icono: ShieldCheck,
    titulo: "Sin tarjeta para empezar",
    texto: "El primer mes es gratis y no te pedimos ningún medio de pago para probarlo.",
  },
  {
    icono: Heart,
    titulo: "Sin permanencia",
    texto: "Cancelás cuando quieras. Sin contratos, sin letra chica y sin llamadas para darte de baja.",
  },
  {
    icono: Check,
    titulo: "Sin instalar nada",
    texto: "Funciona en el navegador del celular. Ni vos ni tus clientes descargan una aplicación.",
  },
  {
    icono: MessageCircle,
    titulo: "Soporte con una persona",
    texto: "Te responde alguien del equipo, no un bot. Estamos del otro lado cuando lo necesitás.",
  },
  {
    icono: Globe,
    titulo: "Tus datos son tuyos",
    texto: "No vendemos ni compartimos la información de tu negocio. Se termina cuando vos decidís.",
  },
  {
    icono: Zap,
    titulo: "Listo en 2 minutos",
    texto: "Creás la cuenta, cargás tus servicios y ya podés compartir tu link para que reserven.",
  },
];

/**
 * Los tres planes de la landing. `nota` es el texto chico bajo el precio; va
 * separado del `periodo` porque no es lo mismo (periodo = "por mes", nota =
 * la referencia en dolares).
 */
interface Plan {
  nombre: string;
  precio: string;
  periodo: string;
  nota?: string;
  resumen: string;
  destacado: boolean;
  beneficios: string[];
  cta: string;
  href: string;
  externo: boolean;
}

const PLANES: Plan[] = [
  {
    nombre: "Básico",
    precio: "$ 0",
    periodo: "el primer mes",
    resumen: "Probalo un mes entero, sin tarjeta.",
    destacado: false,
    beneficios: [
      "Reservas online ilimitadas",
      "1 profesional",
      "Página pública + QR",
      "Recordatorios por WhatsApp (100 / mes)",
      "Al mes, si querés seguir, pasás a Pro",
    ],
    cta: "Empezar gratis",
    href: "/register",
    externo: false,
  },
  {
    nombre: "Pro",
    // ARS primero y el dolar abajo a modo de referencia: ver dolares de entrada
    // asusta al dueño de un negocio de aca.
    precio: "ARS 45.000",
    periodo: "por mes",
    nota: "≈ US$ 30 al dólar de hoy",
    resumen: "Para negocios con equipo que quieren llenar la agenda.",
    destacado: true,
    beneficios: [
      "Todo lo de Básico",
      "Profesionales ilimitados",
      "Recordatorios ilimitados",
      "Señas y cobros con Mercado Pago",
      "Reportes avanzados",
      "Soporte 24/7 por WhatsApp, con nosotros",
    ],
    cta: "Probar el primer mes gratis",
    href: "/register",
    externo: false,
  },
  {
    nombre: "Personalizado",
    // Sin precio: se cotiza uno a uno.
    precio: "A medida",
    periodo: "se cotiza",
    resumen: "Tu marca y tu diseño, con nosotros al lado.",
    destacado: false,
    beneficios: [
      "Plan Pro incluido",
      "Tu logo y tus colores",
      "Diseñamos tu página a tu gusto",
      "Capacitación 1 a 1",
      "Puesta en marcha con vos",
    ],
    cta: "Contactar por personalizado",
    href: INSTAGRAM,
    externo: true,
  },
];

const PREGUNTAS = [
  {
    p: "¿Necesito tarjeta de crédito para empezar?",
    r: "No. El primer mes es gratis y no te pedimos ningún medio de pago. Recién cuando decidas seguir, te pasamos el link para abonar.",
  },
  {
    p: "¿Qué pasa cuando se termina el mes gratis?",
    r: "Te avisamos unos días antes. Cuando se cumple el mes, el panel te avisa que para seguir usando Tempus hay que abonar el Pro. Si no abonás, no te cobramos nada.",
  },
  {
    p: "¿Cómo abono el Pro?",
    r: "Te pasamos el link de pago por Mercado Pago. Lo abonás una vez al mes y tu cuenta sigue funcionando igual que siempre.",
  },
  {
    p: "¿Mis clientes tienen que instalar una aplicación?",
    r: "No. Reservan desde el navegador del celular con tu link. Nada que descargar ni crear cuenta para ellos.",
  },
  {
    p: "¿Cómo funcionan los recordatorios de WhatsApp?",
    r: "Se envían solos 24 h y 2 h antes del turno, desde el WhatsApp de tu negocio. Vos no tocas nada.",
  },
  {
    p: "¿Puedo cobrar una seña al reservar?",
    r: "Sí. Conectás tu cuenta de Mercado Pago, definís el monto de la seña y se cobra al confirmar el turno.",
  },
  {
    p: "¿Y si hoy uso una libreta o un cuaderno?",
    r: "Cargás tu agenda una vez y seguís desde Tempus. Tus clientes no notan el cambio, solo ven que es más fácil.",
  },
];

/* ------------------------------------------------------------------ piezas chicas ----------------- */

function Marca({ t, compacta = false }: { t: Tema; compacta?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <span
        className={`grid h-9 w-9 place-items-center rounded-xl ${t.acentoBg}`}
        aria-hidden="true"
      >
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2.2}>
          <circle cx="12" cy="12" r="8.5" strokeLinecap="round" />
          <path d="M12 7.5V12l3 2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      <span className={`text-lg font-bold tracking-tight ${t.textoFuerte} ${t.fuenteTitulo}`}>
        Tempus
        {!compacta && <span className={t.acentoTexto}>.</span>}
      </span>
    </span>
  );
}

/** Boton/enlace con la piel del tema. Los tres estilos comparten forma, cambia el color. */
function Boton({
  t,
  variante = "primario",
  href,
  children,
  className = "",
}: {
  t: Tema;
  variante?: "primario" | "secundario";
  href: string;
  children: React.ReactNode;
  className?: string;
}) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-full px-5 py-3 text-sm font-semibold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-transparent hover:-translate-y-0.5";
  const piel = variante === "primario" ? t.botonPrimario : t.botonSecundario;
  return (
    <a href={href} className={`${base} ${piel} ${className}`}>
      {children}
    </a>
  );
}

function Seccion({
  id,
  children,
  className = "",
}: {
  id?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section id={id} className={`scroll-mt-24 ${className}`}>
      <div className="mx-auto w-full max-w-7xl px-6 lg:px-8">{children}</div>
    </section>
  );
}

function EncabezadoSeccion({
  t,
  etiqueta,
  titulo,
  bajada,
}: {
  t: Tema;
  etiqueta: string;
  titulo: React.ReactNode;
  bajada?: string;
}) {
  return (
    <div className="mx-auto max-w-2xl text-center">
      <span className={t.acentoPill}>{etiqueta}</span>
      <h2 className={`mt-5 text-3xl font-bold leading-tight sm:text-4xl lg:text-[2.75rem] ${t.fuenteTitulo} ${t.textoFuerte}`}>
        {titulo}
      </h2>
      {bajada && <p className={`mt-4 text-base leading-relaxed sm:text-lg ${t.textoSuave}`}>{bajada}</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ maqueta de agenda ------------- */

/** Mini panel de la agenda, hecho con HTML/CSS. Es el "producto" que se ve en el hero. */
function MaquetaAgenda({ t }: { t: Tema }) {
  const turnos = [
    { hora: "10:00", servicio: "Corte + barba", cliente: "Joaquín M.", quien: "Nico", estado: "Confirmado" },
    { hora: "11:30", servicio: "Color", cliente: "Sofía T.", quien: "Vale", estado: "Seña pagada" },
    { hora: "13:00", servicio: "Corte clásico", cliente: "Bruno A.", quien: "Nico", estado: "Confirmado" },
  ];
  return (
    <div className={`relative w-full max-w-md rounded-2xl border p-5 ${t.mockFondo}`}>
      {/* barra superior */}
      <div className="flex items-center justify-between">
        <div>
          <p className={`text-xs font-medium uppercase tracking-wider ${t.textoSuave}`}>Hoy · Miércoles 16</p>
          <p className={`text-base font-bold ${t.textoFuerte} ${t.fuenteTitulo}`}>Tu negocio</p>
        </div>
        <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${t.acentoBg}`}>3 turnos</span>
      </div>

      {/* turnos */}
      <div className="mt-4 space-y-2.5">
        {turnos.map((turno) => (
          <div
            key={turno.hora}
            className={`flex items-center gap-3 rounded-xl border px-3 py-2.5 ${t.borde}`}
          >
            <span className={`text-sm font-bold tabular-nums ${t.textoFuerte}`}>{turno.hora}</span>
            <span className={`h-8 w-px ${t.borde} border-l`} aria-hidden="true" />
            <span className="min-w-0 flex-1">
              <span className={`block truncate text-sm font-semibold ${t.textoFuerte}`}>{turno.servicio}</span>
              <span className={`block truncate text-xs ${t.textoSuave}`}>
                {turno.cliente} · {turno.quien}
              </span>
            </span>
            <span className={`hidden shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold sm:block ${t.acentoBg}`}>
              {turno.estado}
            </span>
          </div>
        ))}
      </div>

      {/* ocupacion */}
      <div className="mt-4">
        <div className="flex items-center justify-between text-xs">
          <span className={t.textoSuave}>Ocupación del día</span>
          <span className={`font-bold ${t.textoFuerte}`}>92%</span>
        </div>
        <div className={`mt-2 h-2 w-full overflow-hidden rounded-full ${t.acentoBg}`}>
          <div className="h-full w-[92%] rounded-full bg-current opacity-80" />
        </div>
      </div>

      {/* aviso flotante: recordatorio enviado */}
      <div
        className={`absolute -bottom-5 -left-4 flex items-center gap-2.5 rounded-xl border px-3.5 py-2.5 shadow-lg sm:-left-8 ${t.mockFondo}`}
      >
        <span className={`grid h-8 w-8 place-items-center rounded-lg ${t.mockAcento}`}>
          <BellRing className="h-4 w-4" />
        </span>
        <span className="leading-tight">
          <span className={`block text-xs font-semibold ${t.textoFuerte}`}>Recordatorio enviado</span>
          <span className={`block text-[11px] ${t.textoSuave}`}>por WhatsApp · hace un instante</span>
        </span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ selector de estilo ----------- */

/**
 * Barra flotante para comparar los tres estilos. Es una herramienta de revision:
 * cuando el usuario elija uno, se borra junto con los temas que sobran.
 */
function SelectorTema({ t, actual, onElegir }: { t: Tema; actual: ClaveTema; onElegir: (c: ClaveTema) => void }) {
  return (
    <div className="fixed bottom-5 left-1/2 z-50 -translate-x-1/2 px-4">
      <div className="flex items-center gap-2 rounded-full border border-white/15 bg-neutral-900/90 p-1.5 pl-3 text-white shadow-2xl backdrop-blur-xl">
        <span className="hidden items-center gap-1.5 pr-1 text-[11px] font-medium text-white/70 sm:flex">
          <Sparkles className="h-3.5 w-3.5" />
          Estilo
        </span>
        {CLAVES_TEMAS.map((clave) => {
          const tema = TEMAS[clave];
          const activo = clave === actual;
          return (
            <button
              key={clave}
              type="button"
              onClick={() => onElegir(clave)}
              className={`flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${
                activo ? "bg-white text-neutral-900" : "text-white/70 hover:bg-white/10 hover:text-white"
              }`}
              title={tema.lema}
            >
              <span className={`h-3.5 w-3.5 rounded-full ${tema.muestra}`} aria-hidden="true" />
              {tema.nombre}
            </button>
          );
        })}
        <span className="sr-only">Estilo actual: {t.nombre}</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ pagina ------------------------ */

export default function LandingPage() {
  const [params, setParams] = useSearchParams();
  const paramTema = params.get("tema");
  const clave: ClaveTema = esClaveTema(paramTema) ? paramTema : "aurora";
  const t = TEMAS[clave];

  const [menuAbierto, setMenuAbierto] = useState(false);

  useEffect(() => {
    setMenuAbierto(false);
  }, [clave]);

  // Scroll suave solo mientras esta la landing; se restaura al salir.
  useEffect(() => {
    const previo = document.documentElement.style.scrollBehavior;
    document.documentElement.style.scrollBehavior = "smooth";
    return () => {
      document.documentElement.style.scrollBehavior = previo;
    };
  }, []);

  const elegirTema = (nueva: ClaveTema) => {
    const siguientes = new URLSearchParams(params);
    siguientes.set("tema", nueva);
    setParams(siguientes, { replace: true });
  };

  return (
    <div className={`relative min-h-screen overflow-x-hidden ${t.pagina} font-inter`}>
      {/* ---------------------------------------------------------------- barra de navegacion */}
      <header className={`sticky top-0 z-40 ${t.navFondo}`}>
        <nav className="mx-auto flex w-full max-w-7xl items-center justify-between px-6 py-3.5 lg:px-8">
          <a href="#inicio" className="shrink-0">
            <Marca t={t} />
          </a>

          <div className="hidden items-center gap-8 md:flex">
            {NAV.map((item) => (
              <a
                key={item.href}
                href={item.href}
                className={`text-sm font-medium transition-colors hover:opacity-100 ${t.textoSuave}`}
              >
                {item.etiqueta}
              </a>
            ))}
          </div>

          <div className="hidden items-center gap-3 md:flex">
            <Link to="/login" className={`text-sm font-semibold ${t.textoFuerte} hover:opacity-80`}>
              Ingresar
            </Link>
            <Link
              to="/register"
              className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-semibold transition-all hover:-translate-y-0.5 ${t.botonPrimario}`}
            >
              Empezar gratis
            </Link>
          </div>

          <button
            type="button"
            onClick={() => setMenuAbierto((v) => !v)}
            className={`grid h-10 w-10 place-items-center rounded-lg md:hidden ${t.botonSecundario}`}
            aria-label={menuAbierto ? "Cerrar menú" : "Abrir menú"}
          >
            {menuAbierto ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </nav>

        {menuAbierto && (
          <div className={`border-t px-6 py-4 md:hidden ${t.divisor}`}>
            <div className="flex flex-col gap-1">
              {NAV.map((item) => (
                <a
                  key={item.href}
                  href={item.href}
                  onClick={() => setMenuAbierto(false)}
                  className={`rounded-lg px-3 py-2.5 text-sm font-medium ${t.textoSuave}`}
                >
                  {item.etiqueta}
                </a>
              ))}
            </div>
            <div className="mt-3 flex flex-col gap-2">
              <Link
                to="/register"
                className={`inline-flex items-center justify-center rounded-full px-4 py-2.5 text-sm font-semibold ${t.botonPrimario}`}
              >
                Empezar gratis
              </Link>
              <Link
                to="/login"
                className={`inline-flex items-center justify-center rounded-full px-4 py-2.5 text-sm font-semibold ${t.botonSecundario}`}
              >
                Ingresar
              </Link>
            </div>
          </div>
        )}
      </header>

      {/* ---------------------------------------------------------------- hero */}
      <section id="inicio" className="relative isolate overflow-hidden">
        {/* decoracion de fondo por tema */}
        <div className="pointer-events-none absolute inset-0 -z-10" aria-hidden="true">
          {t.blobs.map((clase, i) => (
            <div key={i} className={clase} />
          ))}
        </div>

        <div className="mx-auto grid w-full max-w-7xl items-center gap-14 px-6 pb-24 pt-16 lg:grid-cols-[1.05fr_0.95fr] lg:gap-10 lg:px-8 lg:pb-32 lg:pt-24">
          <div className="animate-aparecer-arriba">
            <span className={t.acentoPill}>
              <Sparkles className="h-3.5 w-3.5" />
              Turnos online para tu negocio
            </span>

            <h1
              className={`mt-6 text-4xl font-bold leading-[1.05] sm:text-5xl lg:text-[3.75rem] ${t.fuenteTitulo} ${t.textoFuerte}`}
            >
              Llena tu agenda{" "}
              <span className={t.gradienteTexto}>sin atender el teléfono</span>
            </h1>

            <p className={`mt-6 max-w-xl text-lg leading-relaxed ${t.textoSuave}`}>
              Tempus le da a tu peluquería, barbería o centro de estética una página para que
              tus clientes reserven solos, recordatorios por WhatsApp que bajan las ausencias y
              cobros con Mercado Pago. Vos te dedicás a atender.
            </p>

            <div className="mt-9 flex flex-col gap-3 sm:flex-row sm:items-center">
              <Link
                to="/register"
                className={`inline-flex items-center justify-center gap-2 rounded-full px-6 py-3.5 text-sm font-semibold transition-all hover:-translate-y-0.5 ${t.botonPrimario}`}
              >
                Crear mi cuenta gratis
                <ArrowRight className="h-4 w-4" />
              </Link>
              <Boton t={t} variante="secundario" href="#como-funciona">
                Ver cómo funciona
              </Boton>
            </div>

            <p className={`mt-5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm ${t.textoSuave}`}>
              <Check className={`h-4 w-4 ${t.acentoTexto}`} />
              Gratis para empezar · sin tarjeta · listo en 2 minutos
            </p>
          </div>

          <div className="flex justify-center lg:justify-end">
            <MaquetaAgenda t={t} />
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- garantias (tira) */}
      <Seccion className={`border-y py-10 ${t.divisor} ${t.bandaSuave}`}>
        <ul className="grid grid-cols-2 gap-6 lg:grid-cols-4">
          {CONFIANZA.map(({ icono: Icono, texto }) => (
            <li key={texto} className="flex items-center gap-3">
              <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-full ${t.acentoBg}`}>
                <Icono className="h-4 w-4" />
              </span>
              <span className={`text-sm font-medium ${t.textoFuerte}`}>{texto}</span>
            </li>
          ))}
        </ul>
      </Seccion>

      {/* ---------------------------------------------------------------- funciones */}
      <Seccion id="funciones" className="py-24">
        <EncabezadoSeccion
          t={t}
          etiqueta="Todo en un solo lugar"
          titulo="Lo que tu negocio necesita, sin planillas sueltas"
          bajada="De la reserva al cobro, sin saltar entre aplicaciones ni perseguir mensajes."
        />
        <div className="mt-16 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {FUNCIONES.map(({ icono: Icono, titulo, texto }) => (
            <article key={titulo} className={`group p-7 ${t.tarjeta}`}>
              <span className={`grid h-12 w-12 place-items-center rounded-xl ${t.acentoBg}`}>
                <Icono className="h-6 w-6" />
              </span>
              <h3 className={`mt-5 text-lg font-bold ${t.textoFuerte} ${t.fuenteTitulo}`}>{titulo}</h3>
              <p className={`mt-2.5 text-sm leading-relaxed ${t.textoSuave}`}>{texto}</p>
            </article>
          ))}
        </div>
      </Seccion>

      {/* ---------------------------------------------------------------- como funciona */}
      <Seccion id="como-funciona" className={`border-y py-24 ${t.divisor} ${t.bandaSuave}`}>
        <EncabezadoSeccion
          t={t}
          etiqueta="En 3 pasos"
          titulo="De cero a tu primera reserva hoy mismo"
        />
        <ol className="mt-16 grid gap-6 lg:grid-cols-3">
          {PASOS.map(({ icono: Icono, titulo, texto }, i) => (
            <li key={titulo} className={`relative p-7 ${t.tarjeta}`}>
              <span className={`inline-flex h-9 w-9 items-center justify-center rounded-full text-sm font-bold ${t.acentoBg}`}>
                {i + 1}
              </span>
              <span className={`mt-5 flex items-center gap-2 text-lg font-bold ${t.textoFuerte} ${t.fuenteTitulo}`}>
                <Icono className={`h-5 w-5 ${t.acentoTexto}`} />
                {titulo}
              </span>
              <p className={`mt-2.5 text-sm leading-relaxed ${t.textoSuave}`}>{texto}</p>
            </li>
          ))}
        </ol>
      </Seccion>

      {/* ---------------------------------------------------------------- pagos */}
      <Seccion className="relative isolate overflow-hidden py-24">
        <div className="pointer-events-none absolute inset-0 -z-10" aria-hidden="true">
          <div className={t.blobs[0]} />
        </div>
        <div className="grid items-center gap-14 lg:grid-cols-2">
          <div>
            <span className={t.acentoPill}>
              <Wallet className="h-3.5 w-3.5" />
              Cobros integrados
            </span>
            <h2 className={`mt-5 text-3xl font-bold leading-tight sm:text-4xl ${t.fuenteTitulo} ${t.textoFuerte}`}>
              Cobra la seña y olvidate de los plantones
            </h2>
            <p className={`mt-4 text-base leading-relaxed sm:text-lg ${t.textoSuave}`}>
              Conectá tu cuenta de Mercado Pago y pedí una seña al reservar. El cliente que paga,
              viene; y el dinero entra directo a tu cuenta.
            </p>
            <ul className="mt-8 space-y-4">
              {[
                { icono: ShieldCheck, texto: "Pagos protegidos por Mercado Pago, sin manejar efectivo." },
                { icono: CreditCard, texto: "Vos definís el monto de la seña y qué servicios la piden." },
                { icono: Globe, texto: "Cobros en pesos, acreditados en tu cuenta como siempre." },
              ].map(({ icono: Icono, texto }) => (
                <li key={texto} className="flex items-start gap-3">
                  <span className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg ${t.acentoBg}`}>
                    <Icono className="h-4 w-4" />
                  </span>
                  <span className={`text-sm leading-relaxed ${t.textoSuave}`}>{texto}</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="flex justify-center lg:justify-end">
            <div className={`w-full max-w-sm p-6 ${t.tarjeta}`}>
              <div className="flex items-center justify-between">
                <span className={`text-sm font-semibold ${t.textoFuerte}`}>Seña de reserva</span>
                <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${t.acentoBg}`}>Acreditada</span>
              </div>
              <p className={`mt-4 text-4xl font-bold ${t.fuenteTitulo} ${t.textoFuerte}`}>$ 4.500</p>
              <p className={`mt-1 text-sm ${t.textoSuave}`}>Color · Sofía T. · Miércoles 16, 11:30</p>
              <div className={`my-5 border-t ${t.divisor}`} />
              <div className="space-y-2.5">
                <div className="flex items-center justify-between text-sm">
                  <span className={t.textoSuave}>Medio de pago</span>
                  <span className={`font-medium ${t.textoFuerte}`}>Mercado Pago</span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className={t.textoSuave}>Estado del turno</span>
                  <span className={`inline-flex items-center gap-1.5 font-medium ${t.textoFuerte}`}>
                    <Check className={`h-4 w-4 ${t.acentoTexto}`} />
                    Confirmado
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </Seccion>

      {/* ---------------------------------------------------------------- garantias */}
      <Seccion className={`border-y py-24 ${t.divisor} ${t.bandaSuave}`}>
        <EncabezadoSeccion
          t={t}
          etiqueta="Sin letra chica"
          titulo="Cosas que te garantizamos"
          bajada="Preferimos prometer poco y cumplirlo. Estas son las que sostienen todo lo demás."
        />
        <div className="mt-16 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {GARANTIAS.map(({ icono: Icono, titulo, texto }) => (
            <article key={titulo} className={`p-7 ${t.tarjeta}`}>
              <span className={`grid h-11 w-11 place-items-center rounded-xl ${t.acentoBg}`}>
                <Icono className="h-5 w-5" />
              </span>
              <h3 className={`mt-5 text-base font-bold ${t.textoFuerte} ${t.fuenteTitulo}`}>{titulo}</h3>
              <p className={`mt-2 text-sm leading-relaxed ${t.textoSuave}`}>{texto}</p>
            </article>
          ))}
        </div>
      </Seccion>

      {/* ---------------------------------------------------------------- precios */}
      <Seccion id="precios" className="py-24">
        <EncabezadoSeccion
          t={t}
          etiqueta="Precios"
          titulo="Simple y sin sorpresas"
          bajada="Arranca gratis. Cuando el negocio crezca, pasas a Pro y listo."
        />
        <div className="mx-auto mt-16 grid max-w-6xl items-stretch gap-6 lg:grid-cols-3">
          {PLANES.map((plan) => (
            <div
              key={plan.nombre}
              className={`relative flex flex-col p-8 ${plan.destacado ? t.tarjetaDestacada : t.tarjeta}`}
            >
              {plan.destacado && (
                <span className="absolute -top-3 left-8 rounded-full bg-lime-300 px-3 py-1 text-[11px] font-bold text-neutral-900">
                  Recomendado
                </span>
              )}
              <h3 className={`text-lg font-bold ${plan.destacado ? "text-white" : t.textoFuerte} ${t.fuenteTitulo}`}>
                {plan.nombre}
              </h3>
              <p className={`mt-1 text-sm ${plan.destacado ? "text-white/70" : t.textoSuave}`}>{plan.resumen}</p>
              <p className="mt-6 flex items-end gap-2">
                <span className={`text-4xl font-bold ${plan.destacado ? "text-white" : t.textoFuerte} ${t.fuenteTitulo}`}>
                  {plan.precio}
                </span>
                <span className={`pb-1.5 text-sm ${plan.destacado ? "text-white/60" : t.textoSuave}`}>
                  {plan.periodo}
                </span>
              </p>
              {plan.nota && (
                <p className={`mt-1.5 text-xs ${plan.destacado ? "text-white/60" : t.textoSuave}`}>
                  {plan.nota}
                </p>
              )}
              <ul className="mt-7 flex-1 space-y-3">
                {plan.beneficios.map((b) => (
                  <li key={b} className="flex items-start gap-2.5">
                    <Check
                      className={`mt-0.5 h-4 w-4 shrink-0 ${plan.destacado ? t.acentoDestacado : t.acentoTexto}`}
                    />
                    <span className={`text-sm ${plan.destacado ? "text-white/85" : t.textoSuave}`}>{b}</span>
                  </li>
                ))}
              </ul>
              {/* El plan Personalizado se cierra por Instagram: no es autoservicio. */}
              <a
                href={plan.href}
                {...(plan.externo ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                className={`mt-8 inline-flex items-center justify-center gap-2 rounded-full px-5 py-3 text-sm font-semibold transition-all hover:-translate-y-0.5 ${
                  plan.destacado ? "bg-white text-neutral-900 hover:bg-white/90" : t.botonPrimario
                }`}
              >
                {plan.cta}
                <ArrowRight className="h-4 w-4" />
              </a>
            </div>
          ))}
        </div>
        <p className={`mt-6 text-center text-xs ${t.textoSuave}`}>
          El primer mes de Básico es gratis y no te pedimos tarjeta. El precio de Pro en dólares se cobra
          en pesos por Mercado Pago.
        </p>
      </Seccion>

      {/* ---------------------------------------------------------------- preguntas */}
      <Seccion id="preguntas" className={`border-y py-24 ${t.divisor} ${t.bandaSuave}`}>
        <EncabezadoSeccion t={t} etiqueta="Preguntas frecuentes" titulo="Lo que todos nos preguntan" />
        <div className="mx-auto mt-14 max-w-3xl space-y-3">
          {PREGUNTAS.map(({ p, r }) => (
            <details key={p} className={`group px-6 py-5 ${t.tarjeta}`}>
              <summary className="flex cursor-pointer list-none items-center justify-between gap-4">
                <span className={`text-base font-semibold ${t.textoFuerte}`}>{p}</span>
                <ChevronDown
                  className={`h-5 w-5 shrink-0 transition-transform group-open:rotate-180 ${t.acentoTexto}`}
                />
              </summary>
              <p className={`mt-3 text-sm leading-relaxed ${t.textoSuave}`}>{r}</p>
            </details>
          ))}
        </div>
      </Seccion>

      {/* ---------------------------------------------------------------- cierre */}
      <Seccion className="py-24">
        <div className={`relative isolate overflow-hidden rounded-3xl px-8 py-16 text-center sm:px-16 ${t.tarjeta}`}>
          <div className="pointer-events-none absolute inset-0 -z-10" aria-hidden="true">
            <div className={t.blobs[0]} />
          </div>
          <h2 className={`mx-auto max-w-2xl text-3xl font-bold leading-tight sm:text-4xl ${t.fuenteTitulo} ${t.textoFuerte}`}>
            Tu próximo cliente ya está buscando turno
          </h2>
          <p className={`mx-auto mt-4 max-w-xl text-base ${t.textoSuave}`}>
            Creá tu cuenta gratis y tenés tu página de reservas lista en dos minutos.
          </p>
          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Link
              to="/register"
              className={`inline-flex items-center justify-center gap-2 rounded-full px-6 py-3.5 text-sm font-semibold transition-all hover:-translate-y-0.5 ${t.botonPrimario}`}
            >
              Empezar gratis
              <ArrowRight className="h-4 w-4" />
            </Link>
            <Boton t={t} variante="secundario" href="#funciones">
              Ver funciones
            </Boton>
          </div>
        </div>
      </Seccion>

      {/* ---------------------------------------------------------------- pie */}
      <footer className={`border-t ${t.pieFondo}`}>
        <div className="mx-auto w-full max-w-7xl px-6 py-14 lg:px-8">
          <div className="grid gap-10 lg:grid-cols-[1.4fr_1fr_1fr]">
            <div>
              <Marca t={t} />
              <p className={`mt-4 max-w-xs text-sm leading-relaxed ${t.textoSuave}`}>
                Reservas, recordatorios y cobros para peluquerías, barberías y centros de estética.
              </p>
              <div className="mt-5 flex gap-3">
                <a
                  href={INSTAGRAM}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label="Instagram de Tempus"
                  className={`grid h-9 w-9 place-items-center rounded-lg transition-colors ${t.acentoBg}`}
                >
                  <Instagram className="h-4 w-4" />
                </a>
              </div>
            </div>

            <div>
              <p className={`text-sm font-bold ${t.textoFuerte}`}>Producto</p>
              <ul className="mt-4 space-y-2.5">
                {NAV.map((item) => (
                  <li key={item.href}>
                    <a href={item.href} className={`text-sm transition-colors ${t.textoSuave}`}>
                      {item.etiqueta}
                    </a>
                  </li>
                ))}
              </ul>
            </div>

            <div>
              <p className={`text-sm font-bold ${t.textoFuerte}`}>Contacto</p>
              <ul className="mt-4 space-y-2.5">
                <li>
                  <a
                    href={INSTAGRAM}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`inline-flex items-center gap-2 text-sm transition-colors ${t.textoSuave}`}
                  >
                    <Instagram className="h-4 w-4" />
                    Escribinos por Instagram
                  </a>
                </li>
                <li className={`text-sm ${t.textoSuave}`}>Soporte 24/7 para clientes Pro</li>
              </ul>
            </div>
          </div>

          <div className={`mt-12 flex flex-col items-center justify-between gap-4 border-t pt-6 sm:flex-row ${t.divisor}`}>
            <p className={`text-xs ${t.textoSuave}`}>© {new Date().getFullYear()} Tempus. Todos los derechos reservados.</p>
            <p className={`flex items-center gap-1.5 text-xs ${t.textoSuave}`}>
              <Globe className="h-3.5 w-3.5" />
              Hecho en Argentina
            </p>
          </div>
        </div>
      </footer>

      <SelectorTema t={t} actual={clave} onElegir={elegirTema} />
    </div>
  );
}
