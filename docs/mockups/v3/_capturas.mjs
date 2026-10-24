const BASE = "file:///C:/Users/Estudiante/Desktop/Tempus/docs/mockups/v3/";
const OUT = "C:/Users/Estudiante/Desktop/Tempus/docs/mockups/v3/";

const CASOS = [
  { name: "registro-paso1", file: "registro.html?paso=1", w: 1440, h: 1024, full: true },
  { name: "registro-paso3", file: "registro.html?paso=3", w: 1440, h: 1024, full: true },
  { name: "registro-mobile", file: "registro.html?paso=1", w: 390, h: 932, full: false },
  { name: "reserva-servicios", file: "reserva.html?paso=1", w: 1440, h: 1024, full: true },
  { name: "reserva-horario", file: "reserva.html?paso=2", w: 1440, h: 1024, full: true },
  { name: "reserva-datos", file: "reserva.html?paso=3", w: 1440, h: 1024, full: true },
  { name: "reserva-confirmacion", file: "confirmacion.html", w: 1440, h: 1024, full: true },
  { name: "reserva-mobile-servicios", file: "reserva.html?paso=1", w: 390, h: 932, full: false },
  { name: "reserva-mobile-horario", file: "reserva.html?paso=2", w: 390, h: 932, full: false },
];

export default async function run(page) {
  const hechas = [];
  await page.emulateMedia({ reducedMotion: "reduce" });

  for (const c of CASOS) {
    await page.setViewportSize({ width: c.w, height: c.h });
    await page.goto(BASE + c.file, { waitUntil: "load", timeout: 120000 });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForFunction(
      () => document.querySelectorAll("svg.lucide").length > 0,
      { timeout: 60000 }
    );
    await page.waitForTimeout(450);

    await page.screenshot({
      path: OUT + c.name + ".png",
      fullPage: c.full,
      animations: "disabled",
    });

    const dims = await page.evaluate(() => ({
      w: document.body.scrollWidth,
      h: document.body.scrollHeight,
      fonts: document.fonts.status,
      icons: document.querySelectorAll("svg.lucide").length,
    }));
    hechas.push({ name: c.name, viewport: `${c.w}x${c.h}`, fullPage: c.full, ...dims });
  }
  return hechas;
}
