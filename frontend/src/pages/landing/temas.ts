/**
 * La piel de la landing de captacion de clientes.
 *
 * **Queda un solo estilo: aurora.** Antes habia tres direcciones visuales
 * (`aurora`, `calido`, `minimal`) para elegir con `?tema=`. Se eligio aurora, asi
 * que los otros dos temas y el selector se borraron. El tipo y el mapa se dejan
 * con forma de coleccion--no como constantes sueltas--para que reponer un tema
 * sea agregar una entrada aca, sin tocar `LandingPage.tsx`.
 *
 * Cada valor es una lista de clases Tailwind escrita literal (nada armado con
 * concatenacion) para que el detector de utilidades de Tailwind las vea.
 *
 * Ningun tema toca `font-sans`: son utilidades opt-in (`font-inter`, `font-display`,
 * `font-serif-brand`) para no reestilizar el panel ya aprobado.
 */

export type ClaveTema = "aurora";

export interface Tema {
  clave: ClaveTema;
  nombre: string;
  /** Frase corta que describe el estilo. */
  lema: string;

  // Superficies
  pagina: string;
  navFondo: string;
  pieFondo: string;
  bandaSuave: string;

  // Texto
  textoFuerte: string;
  textoSuave: string;
  borde: string;
  divisor: string;

  // Tarjetas
  tarjeta: string;
  tarjetaDestacada: string;

  // Acento
  acentoPill: string;
  acentoBg: string;
  acentoTexto: string;
  /** Acento legible sobre la tarjeta de precio destacada (siempre oscura). */
  acentoDestacado: string;
  gradienteTexto: string;

  // Botones
  botonPrimario: string;
  botonSecundario: string;

  // Tipografia y decoracion
  fuenteTitulo: string;
  blobs: string[];
  mockFondo: string;
  mockAcento: string;
}

export const TEMAS: Record<ClaveTema, Tema> = {
  /**
   * Aurora: oscuro profundo con auroras de color y superficies de vidrio.
   * El registro "SaaS premium" (Linear, Vercel). Impacta de noche y en pantallas
   * grandes; el texto claro sobre fondo casi negro hace que las capturas vuelen.
   */
  aurora: {
    clave: "aurora",
    nombre: "Aurora",
    lema: "Oscuro, con brillo de color y vidrio",

    pagina: "bg-[#070B1A] text-slate-100",
    navFondo: "border-b border-white/10 bg-[#070B1A]/80 backdrop-blur-xl",
    pieFondo: "border-t border-white/10 bg-[#050814]",
    bandaSuave: "bg-white/[0.02]",

    textoFuerte: "text-white",
    textoSuave: "text-slate-400",
    borde: "border-white/10",
    divisor: "border-white/10",

    tarjeta:
      "rounded-2xl border border-white/10 bg-white/[0.03] backdrop-blur-sm transition-colors hover:border-white/20",
    tarjetaDestacada:
      "rounded-2xl border border-violet-400/40 bg-gradient-to-b from-violet-500/[0.14] to-white/[0.02] shadow-[0_0_60px_-15px_rgba(139,92,246,0.55)]",

    acentoPill:
      "inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs font-medium text-violet-200",
    acentoBg: "bg-violet-500/15 text-violet-200 ring-1 ring-inset ring-violet-400/30",
    acentoTexto: "text-violet-300",
    acentoDestacado: "text-violet-300",
    gradienteTexto:
      "bg-gradient-to-r from-violet-400 via-fuchsia-400 to-cyan-300 bg-clip-text text-transparent bg-[length:200%_auto] animate-brillo",

    botonPrimario: "bg-white text-slate-900 hover:bg-slate-200",
    botonSecundario: "border border-white/15 bg-white/5 text-white hover:bg-white/10",

    fuenteTitulo: "font-display",
    blobs: [
      "absolute -top-40 -left-32 h-[520px] w-[520px] rounded-full bg-violet-600/25 blur-[120px] animate-flotar-lento",
      "absolute -top-16 right-0 h-[460px] w-[460px] rounded-full bg-cyan-500/20 blur-[130px] animate-flotar-lento",
      "absolute top-64 left-1/3 h-[420px] w-[420px] rounded-full bg-fuchsia-600/20 blur-[140px]",
    ],
    mockFondo: "border-white/10 bg-[#0B1224]/90 backdrop-blur-xl",
    mockAcento: "bg-violet-500/20 text-violet-200",
  },
};

export const CLAVES_TEMAS = Object.keys(TEMAS) as ClaveTema[];
