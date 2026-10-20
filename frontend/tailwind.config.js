import defaultTheme from "tailwindcss/defaultTheme";

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      // Familias extra para la landing. NO se toca `font-sans` (default): el panel
      // admin ya aprobado tiene que quedar igual. Son utilidades opt-in.
      fontFamily: {
        inter: ["Inter", ...defaultTheme.fontFamily.sans],
        display: ['"Space Grotesk"', ...defaultTheme.fontFamily.sans],
        "serif-brand": ["Fraunces", ...defaultTheme.fontFamily.serif],
      },
      keyframes: {
        "flotar-lento": {
          "0%, 100%": { transform: "translateY(0) scale(1)" },
          "50%": { transform: "translateY(-18px) scale(1.03)" },
        },
        "aparecer-arriba": {
          from: { opacity: "0", transform: "translateY(14px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "brillo": {
          "0%": { backgroundPosition: "0% 50%" },
          "100%": { backgroundPosition: "200% 50%" },
        },
        /**
         * Entradas del login Aurora. `entra-izq` es la columna izquierda (marca y
         * texto, que entran desplazandose de izquierda a derecha) y `entra-arriba`
         * es la columna derecha (la tarjeta, que cae de arriba hacia abajo).
         *
         * El desplazamiento es **chico a proposito**: 56px y 34px. Un recorrido
         * largo se siente lento aunque dure lo mismo, y ademas mueve mas pixeles
         * por cuadro--se nota el salto. La curva elegida es la de desaceleracion
         * fuerte: entra rapido y frena suave.
         */
        "entra-izq": {
          from: { opacity: "0", transform: "translate3d(-56px, 0, 0)" },
          to: { opacity: "1", transform: "translate3d(0, 0, 0)" },
        },
        "entra-arriba": {
          from: { opacity: "0", transform: "translate3d(0, -34px, 0)" },
          to: { opacity: "1", transform: "translate3d(0, 0, 0)" },
        },
        /** El "pop" final del boton: confirma sin llamar la atencion. */
        "pop-cta": {
          "0%": { transform: "scale(0.975)" },
          "55%": { transform: "scale(1.015)" },
          "100%": { transform: "scale(1)" },
        },
      },
      animation: {
        "flotar-lento": "flotar-lento 9s ease-in-out infinite",
        "aparecer-arriba": "aparecer-arriba 0.7s cubic-bezier(0.22,1,0.36,1) both",
        brillo: "brillo 6s linear infinite",
        "entra-izq": "entra-izq 0.8s cubic-bezier(0.22,1,0.36,1) both",
        "entra-arriba": "entra-arriba 0.8s cubic-bezier(0.22,1,0.36,1) both",
        "pop-cta": "pop-cta 0.5s cubic-bezier(0.34,1.56,0.64,1)",
      },
    },
  },
  plugins: [],
}
