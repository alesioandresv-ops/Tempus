/**
 * Tres direcciones visuales para la landing de captacion de clientes.
 *
 * Todas comparten el mismo contenido y la misma estructura; lo que cambia es la
 * "piel": color, tipografia, superficies y decoracion. Es a proposito: para elegir
 * un estilo conviene mover una sola variable, no tres. Cada valor es una lista de
 * clases Tailwind escrita literal (nada armado con concatenacion) para que el
 * detector de utilidades de Tailwind las vea.
 *
 * Ningun tema toca `font-sans`: son utilidades opt-in (`font-inter`, `font-display`,
 * `font-serif-brand`) para no reestilizar el panel ya aprobado.
 */

export type ClaveTema = "aurora" | "calido" | "minimal";

export interface Tema {
  clave: ClaveTema;
  nombre: string;
  /** Frase corta para el selector de estilos. */
  lema: string;
  /** Muestrario de color del boton del selector. */
  muestra: string;

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
    muestra: "bg-gradient-to-br from-violet-500 via-fuchsia-500 to-cyan-400",

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

  /**
   * Calido: fondo crema, tipografia serif para titulos y acentos tierra.
   * Habla el idioma de peluquerias, barberias y centros de estetica: cercano,
   * artesanal, "de barrio". Baja la frialdad del software.
   */
  calido: {
    clave: "calido",
    nombre: "Cálido",
    lema: "Crema, serif y acentos tierra",
    muestra: "bg-gradient-to-br from-orange-500 to-rose-500",

    pagina: "bg-[#FBF7F1] text-stone-900",
    navFondo: "border-b border-stone-200/80 bg-[#FBF7F1]/85 backdrop-blur-xl",
    pieFondo: "border-t border-stone-200 bg-[#F5EFE6]",
    bandaSuave: "bg-[#F5EFE6]",

    textoFuerte: "text-stone-900",
    textoSuave: "text-stone-500",
    borde: "border-stone-200",
    divisor: "border-stone-200",

    tarjeta:
      "rounded-2xl border border-stone-200 bg-white shadow-[0_1px_2px_rgba(28,25,23,0.04)] transition-shadow hover:shadow-[0_12px_30px_-12px_rgba(28,25,23,0.18)]",
    tarjetaDestacada:
      "rounded-2xl border border-stone-900 bg-stone-900 text-stone-50 shadow-[0_20px_50px_-20px_rgba(28,25,23,0.6)]",

    acentoPill:
      "inline-flex items-center gap-2 rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-xs font-medium text-orange-800",
    acentoBg: "bg-orange-100 text-orange-700",
    acentoTexto: "text-orange-700",
    acentoDestacado: "text-orange-300",
    gradienteTexto:
      "bg-gradient-to-r from-orange-600 via-rose-600 to-amber-600 bg-clip-text text-transparent",

    botonPrimario: "bg-stone-900 text-white hover:bg-stone-800",
    botonSecundario: "border border-stone-300 bg-white text-stone-900 hover:bg-stone-50",

    fuenteTitulo: "font-serif-brand",
    blobs: [
      "absolute -top-32 -left-24 h-[420px] w-[420px] rounded-full bg-orange-200/60 blur-[110px]",
      "absolute top-24 right-0 h-[380px] w-[380px] rounded-full bg-amber-200/60 blur-[110px]",
      "absolute top-96 left-1/4 h-[360px] w-[360px] rounded-full bg-rose-200/50 blur-[120px]",
    ],
    mockFondo: "border-stone-200 bg-white shadow-xl",
    mockAcento: "bg-orange-100 text-orange-700",
  },

  /**
   * Minimal: blanco puro, tipografia enorme y apretada, un unico acento lima.
   * Registro editorial/brutalista-moderno. Muy limpio y muy "2026"; se apoya en
   * la tipografia y el espacio en blanco, no en el color.
   */
  minimal: {
    clave: "minimal",
    nombre: "Minimal",
    lema: "Blanco, tipografía grande y un acento lima",
    muestra: "bg-gradient-to-br from-neutral-900 to-lime-400",

    pagina: "bg-white text-neutral-900",
    navFondo: "border-b border-neutral-200 bg-white/80 backdrop-blur-xl",
    pieFondo: "border-t border-neutral-200 bg-white",
    bandaSuave: "bg-neutral-50",

    textoFuerte: "text-neutral-900",
    textoSuave: "text-neutral-500",
    borde: "border-neutral-200",
    divisor: "border-neutral-200",

    tarjeta: "rounded-xl border border-neutral-200 bg-white transition-colors hover:border-neutral-900",
    tarjetaDestacada:
      "rounded-xl border-2 border-neutral-900 bg-neutral-900 text-white shadow-[8px_8px_0_0_#a3e635]",

    acentoPill:
      "inline-flex items-center gap-2 rounded-full border border-neutral-300 bg-white px-3 py-1 text-xs font-medium text-neutral-700",
    acentoBg: "bg-lime-300 text-neutral-900",
    acentoTexto: "text-neutral-900",
    acentoDestacado: "text-lime-300",
    gradienteTexto: "underline decoration-lime-400 decoration-8 underline-offset-4",

    botonPrimario: "bg-neutral-900 text-white hover:bg-neutral-700",
    botonSecundario: "border border-neutral-300 bg-white text-neutral-900 hover:bg-neutral-100",

    fuenteTitulo: "font-inter tracking-tight",
    blobs: [
      "absolute -top-24 right-10 h-[360px] w-[360px] rounded-full bg-lime-200/50 blur-[110px]",
      "absolute top-40 -left-20 h-[320px] w-[320px] rounded-full bg-neutral-200/60 blur-[110px]",
    ],
    mockFondo: "border-neutral-900 bg-white shadow-[10px_10px_0_0_#a3e635]",
    mockAcento: "bg-lime-300 text-neutral-900",
  },
};

export const CLAVES_TEMAS = Object.keys(TEMAS) as ClaveTema[];

export function esClaveTema(valor: string | null | undefined): valor is ClaveTema {
  return !!valor && valor in TEMAS;
}
