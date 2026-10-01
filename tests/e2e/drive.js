// Drives the real web app in Chromium: pages, the Write flow, Book, Monitor hover, Settings. Fails on any console/page error.
// usage: node tests/e2e/drive.js <url-with-token> [outdir]     (needs /opt/node-tools playwright and the preinstalled Chromium)
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const url = process.argv[2];
const out = process.argv[3] || '/tmp/claude-0/scratch/shots';
fs.mkdirSync(out, { recursive: true });
const errors = [];
const log = (...a) => console.log(...a);

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  async function session(scheme, viewport) {
    const ctx = await browser.newContext({ viewport, colorScheme: scheme });
    const page = await ctx.newPage();
    page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`[${scheme}] console ${m.type()}: ${m.text()}`); });
    page.on('pageerror', (e) => errors.push(`[${scheme}] pageerror: ${e.message}`));
    page.on('requestfailed', (r) => errors.push(`[${scheme}] requestfailed: ${r.url()}`));
    await page.goto(url);
    await page.waitForSelector('#nav a');
    return page;
  }
  const shot = async (page, name) => page.screenshot({ path: `${out}/${name}.png`, fullPage: true });
  const go = async (page, hash, sel) => { await page.evaluate((h) => { location.hash = h; }, hash); await page.waitForSelector(sel, { timeout: 15000 }); await page.waitForTimeout(400); };

  const page = await session('light', { width: 1280, height: 900 });
  log('title:', await page.title(), '| nav:', (await page.$$eval('#nav a', (a) => a.map((x) => x.textContent))).join(', '));
  await go(page, '#/papers', 'section.card'); await shot(page, 'papers-light');
  log('papers cards:', await page.$$eval('section.card h2', (h) => h.map((x) => x.textContent).join(' | ')));
  await go(page, '#/book', '.tree details'); await shot(page, 'book-light');
  await go(page, '#/monitor', '.tile'); await page.waitForSelector('.chart svg'); await shot(page, 'monitor-light');
  await go(page, '#/settings', 'section.card'); await shot(page, 'settings-light');
  const dark = await session('dark', { width: 1280, height: 900 });
  await go(dark, '#/monitor', '.chart svg'); await shot(dark, 'monitor-dark');
  const phone = await session('light', { width: 390, height: 800 });
  await go(phone, '#/monitor', '.chart svg'); await shot(phone, 'monitor-phone');
  await go(phone, '#/write', 'section.card'); await shot(phone, 'write-phone');
  await browser.close();
  log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'no console errors');
  process.exit(errors.length ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
