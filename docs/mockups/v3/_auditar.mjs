import path from "node:path";

const BASE = "file:///C:/Users/Estudiante/Desktop/Tempus/docs/mockups/v3/";

const CASOS = [
  { name: "registro-mobile", file: "registro.html?paso=1", w: 390, h: 932 },
  { name: "reserva-mobile-servicios", file: "reserva.html?paso=1", w: 390, h: 932 },
  { name: "reserva-mobile-horario", file: "reserva.html?paso=2", w: 390, h: 932 },
  { name: "reserva-datos", file: "reserva.html?paso=3", w: 1440, h: 1024 },
  { name: "reserva-confirmacion", file: "confirmacion.html", w: 1440, h: 1024 },
  { name: "registro-paso1", file: "registro.html?paso=1", w: 1440, h: 1024 },
  { name: "registro-paso3", file: "registro.html?paso=3", w: 1440, h: 1024 },
];

function box(el) {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {
    top: Math.round(r.top + scrollY),
    bottom: Math.round(r.bottom + scrollY),
    left: Math.round(r.left),
    right: Math.round(r.right),
    w: Math.round(r.width),
    h: Math.round(r.height),
  };
}

export default async function run(page) {
  const out = [];
  for (const c of CASOS) {
    await page.setViewportSize({ width: c.w, height: c.h });
    await page.goto(BASE + c.file, { waitUntil: "load", timeout: 120000 });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForFunction(() => document.querySelectorAll("svg.lucide").length > 0, { timeout: 60000 });
    await page.waitForTimeout(250);

    const info = await page.evaluate(() => {
      const picks = [".reg-card", ".bk-flow", ".bk-hero", ".bk-mobile-bar", ".ticket", ".ticket-actions", ".summary-cta", ".reg-form", "footer"];
      const boxes = {};
      for (const sel of picks) {
        const el = [...document.querySelectorAll(sel)].find((n) => {
          const r = n.getBoundingClientRect();
          return r.width > 0 && r.height > 0 && getComputedStyle(n).display !== "none";
        });
        boxes[sel] = el ? (() => { const r = el.getBoundingClientRect(); return { top: Math.round(r.top + scrollY), bottom: Math.round(r.bottom + scrollY), left: Math.round(r.left), right: Math.round(r.right), w: Math.round(r.width), h: Math.round(r.height) }; })() : null;
      }

      const over = [];
      for (const el of document.querySelectorAll("h1,h2,h3,.service,.srow,.ticket-actions,.reg-form,.reg-card,.bk-flow")) {
        const r = el.getBoundingClientRect();
        if (!r.width) continue;
        if (r.right > innerWidth + 1 || r.left < -1) {
          over.push({ tag: el.tagName, cls: String(el.className).slice(0, 40), left: Math.round(r.left), right: Math.round(r.right) });
        }
        if (el.scrollWidth > el.clientWidth + 2 && el.clientWidth > 0 && getComputedStyle(el).overflowX === "visible") {
          over.push({ tag: el.tagName, cls: String(el.className).slice(0, 40), scrollW: el.scrollWidth, clientW: el.clientWidth, overflow: "text" });
        }
      }

      const tiny = [...document.querySelectorAll("button, a.btn-dark, a.btn-terra, .btn-outline, .summary-cta a")].filter((el) => {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && (r.height < 28 || r.width < 28);
      }).map((el) => ({ tag: el.tagName, cls: String(el.className).slice(0, 40), w: Math.round(el.getBoundingClientRect().width), h: Math.round(el.getBoundingClientRect().height), text: (el.textContent || "").trim().slice(0, 30) }));

      const qr = document.querySelector("#qr-canvas");
      return {
        scrollH: document.body.scrollHeight,
        scrollY,
        innerH: innerHeight,
        boxes,
        over: over.slice(0, 12),
        tiny,
        qr: qr ? { w: qr.width, h: qr.height, painted: (() => { try { const d = qr.getContext("2d").getImageData(0, 0, qr.width, qr.height).data; let dark = 0; for (let i = 0; i < d.length; i += 4) if (d[i] < 80 && d[i + 1] < 80 && d[i + 2] < 80) dark++; return dark; } catch (e) { return -1; } })() } : null,
      };
    });
    out.push({ name: c.name, viewport: `${c.w}x${c.h}`, ...info });
  }
  return out;
}
