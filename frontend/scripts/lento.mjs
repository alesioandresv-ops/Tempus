const ORIGIN = "https://tempus.tempusapp.workers.dev";

function rel(url) {
  return url.replace(ORIGIN, "");
}

export default async function run(page) {
  const started = Date.now();
  const reqs = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/")) reqs.push({ phase: "request", url: rel(r.url()), t: Date.now() - started });
  });
  page.on("response", (r) => {
    if (r.url().includes("/api/")) reqs.push({ phase: "response", url: rel(r.url()), status: r.status(), t: Date.now() - started });
  });
  page.on("requestfailed", (r) => {
    if (r.url().includes("/api/")) reqs.push({ phase: "failed", url: rel(r.url()), t: Date.now() - started, err: r.failure()?.errorText });
  });

  await page.goto(ORIGIN + "/the-peluche", { waitUntil: "domcontentloaded", timeout: 120000 });
  const domAt = Date.now() - started;

  let waited = false;
  try {
    await page.waitForFunction(() => !document.body.innerText.includes("Cargando..."), { timeout: 30000 });
    waited = true;
  } catch {}
  const contentAt = Date.now() - started;

  const state = await page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0];
    const resources = performance.getEntriesByType("resource").map((r) => ({
      name: r.name.replace(location.origin, ""),
      type: r.initiatorType,
      start: Math.round(r.startTime),
      dur: Math.round(r.duration),
      transfer: r.transferSize,
      status: r.responseStatus,
    }));
    const text = document.body.innerText;
    return {
      title: document.title,
      bodyChars: text.length,
      text: text.slice(0, 1500),
      nav: nav ? { ttfb: Math.round(nav.responseStart), dom: Math.round(nav.domContentLoadedEventEnd), load: Math.round(nav.loadEventEnd) } : null,
      resources,
      buttons: [...document.querySelectorAll("button")].map((b) => b.innerText.trim()).filter(Boolean),
      headings: [...document.querySelectorAll("h1,h2,h3")].map((h) => h.innerText.trim()),
    };
  });

  return { domAt, waited, contentAt, reqs, state };
}
