import path from "node:path";

const BASE = "file:///C:/Users/Estudiante/Desktop/Tempus/docs/mockups/v3/";
const OUT = "C:/Users/Estudiante/Desktop/Tempus/docs/mockups/v3/";

const CASOS = [
  { name: "registro-paso1", file: "registro.html?paso=1", w: 1440, h: 1024, full: true },
  { name: "registro-paso3", file: "registro.html?paso=3", w: 1440, h: 1024, full: true },
  { name: "registro-mobile", file: "registro.html?paso=1", w: 390, h: 844, full: false },
  { name: "reserva-servicios", file: "reserva.html?paso=1", w: 1440, h: 1024, full: true },
  { name: "reserva-horario", file: "reserva.html?paso=2", w: 1440, h: 1024, full: true },
  { name: "reserva-datos", file: "reserva.html?paso=3", w: 1440, h: 1024, full: true },
  { name: "reserva-confirmacion", file: "confirmacion.html", w: 1440, h: 1024, full: true },
  { name: "reserva-mobile-servicios", file: "reserva.html?paso=1", w: 390, h: 844, full: false },
  { name: "reserva-mobile-horario", file: "reserva.html?paso=2", w: 390, h: 844, full: false },
];

export default async function run(page) {
  const report = [];
  for (const c of CASOS) {
    await page.setViewportSize({ width: c.w, height: c.h });
    await page.goto(BASE + c.file, { waitUntil: "load", timeout: 120000 });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForFunction(
      () => document.querySelectorAll("svg.lucide").length > 0,
      { timeout: 60000 }
    );
    await page.waitForTimeout(350);
    await page.emulateMedia({ reducedMotion: "reduce" });

    const info = await page.evaluate(() => {
      const body = document.body;
      const wide = [...document.querySelectorAll("*")]
        .filter((el) => {
          const r = el.getBoundingClientRect();
          return r.width > 0 && (r.right > innerWidth + 1 || r.left < -1);
        })
        .slice(0, 8)
        .map((el) => ({
          tag: el.tagName.toLowerCase(),
          cls: String(el.className).slice(0, 70),
          left: Math.round(el.getBoundingClientRect().left),
          right: Math.round(el.getBoundingClientRect().right),
        }));

      const get = (sel) => {
        const el = document.querySelector(sel);
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { top: Math.round(r.top + scrollY), bottom: Math.round(r.bottom + scrollY), height: Math.round(r.height), width: Math.round(r.width) };
      };

      return {
        scrollH: body.scrollHeight,
        scrollW: body.scrollWidth,
        fonts: document.fonts.status,
        icons: document.querySelectorAll("svg.lucide").length,
        wide,
        card: get(".reg-card"),
        flow: get(".bk-flow"),
        summary: get(".bk-summary"),
        ticket: get(".ticket"),
        mobileBar: get(".bk-mobile-bar.only-1,.bk-mobile-bar.only-2,.bk-mobile-bar.only-3"),
      };
    });

    report.push({ name: c.name, viewport: `${c.w}x${c.h}`, ...info });
  }
  return report;
}
