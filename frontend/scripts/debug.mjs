const ORIGIN = "https://tempus.tempusapp.workers.dev";
export default async function run(page) {
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") errors.push({ type: m.type(), text: m.text() });
  });
  page.on("pageerror", (e) => errors.push({ type: "pageerror", text: e.message }));
  page.on("requestfailed", (r) => errors.push({ type: "reqfailed", url: r.url(), err: r.failure()?.errorText }));

  await page.goto(ORIGIN + "/the-peluche", { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForTimeout(5000);

  const state = await page.evaluate(() => {
    return {
      rootLen: document.getElementById("root")?.innerHTML?.length || 0,
      bodyText: document.body.innerText.slice(0, 500),
      hasReact: !!window.__REACT_DEVTOOLS_GLOBAL_HOOK__,
    };
  });

  return { errors, state };
}
