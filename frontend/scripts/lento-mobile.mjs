const ORIGIN = "https://tempus.tempusapp.workers.dev";
const URL = ORIGIN + "/the-peluche";

async function measure(page, label, throttle) {
  const cdp = await page.context().newCDPSession(page);
  if (throttle) {
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", {
      offline: false,
      latency: 150,
      downloadThroughput: (1.6 * 1024 * 1024) / 8,
      uploadThroughput: (750 * 1024) / 8,
    });
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  }

  const started = Date.now();
  const events = [];
  page.on("response", (r) => {
    if (r.url().includes("/api/") || r.url().includes("/assets/")) {
      events.push({ at: Date.now() - started, status: r.status(), url: r.url().replace(ORIGIN, "") });
    }
  });

  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 120000 });
  const dom = Date.now() - started;
  await page.waitForFunction(() => document.body.innerText.includes("Corte General"), { timeout: 60000 });
  const services = Date.now() - started;
  const perf = await page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0];
    const res = performance.getEntriesByType("resource").map((r) => ({
      n: r.name.replace(location.origin, ""),
      s: Math.round(r.startTime),
      d: Math.round(r.duration),
      t: r.transferSize,
    }));
    return {
      nav: nav ? { ttfb: Math.round(nav.responseStart), dom: Math.round(nav.domContentLoadedEventEnd), load: Math.round(nav.loadEventEnd) } : null,
      res,
      text: document.body.innerText.slice(0, 300),
    };
  });
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 }).catch(() => {});
  await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 }).catch(() => {});
  return { label, dom, services, events, perf };
}

export default async function run(page) {
  await page.setViewportSize({ width: 390, height: 844 });
  // Clear cache to measure a cold browser load.
  const fast = await measure(page, "mobile-fast", false);
  await page.context().clearCookies();
  const slow = await measure(page, "mobile-slow4g-cpu4x", true);
  return { fast, slow };
}
