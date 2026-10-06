// "Your request is ready": a chime, a notification when the tab is out of sight, a tick in the tab title, and the Settings card that controls them.
// The sound system and the notification are stood in for (a headless browser has neither), so what is checked is that the app uses them at the right moments.
// usage: python3 tests/e2e/demo_server.py --port 8797 &      (prints the URL with the token and the HOME folder)
//        node tests/e2e/ready.js <url-with-token> <home-folder> [screenshot-dir]
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const url = process.argv[2];
const out = process.argv[4] || '/tmp/claude-0/scratch/shots';
fs.mkdirSync(out, { recursive: true });
const errors = [];
let failed = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
  await page.addInitScript(() => {
    window.__notes = []; window.__osc = 0;
    window.AudioContext = class { constructor() { this.currentTime = 0; this.state = 'running'; this.destination = {}; }
      resume() {} createGain() { return { gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }; }
      createOscillator() { window.__osc++; return { frequency: {}, connect() {}, start() {}, stop() {} }; } };
    window.Notification = class { constructor(t, o) { window.__notes.push([t, o && o.body]); } close() {} static get permission() { return 'granted'; } static requestPermission() { return Promise.resolve('granted'); } };
  });
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`console ${m.type()}: ${m.text()}`); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  await page.goto(url);
  await page.waitForSelector('.chat-page');
  const post = (path, body) => page.evaluate(async ([pth, b]) => (await fetch(pth, { method: 'POST', headers: { 'X-Requested-With': 'ols', 'Content-Type': 'application/json' }, body: JSON.stringify(b) })).status, [path, body]);
  const state = () => page.evaluate(async () => (await (await fetch('/api/state', { headers: { 'X-Requested-With': 'ols' } })).json()).settings.ui);
  const osc = () => page.evaluate(() => window.__osc);
  const notes = () => page.evaluate(() => window.__notes.length);
  const away = (on) => page.evaluate((v) => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => v }); document.hasFocus = () => !v; }, on);
  const announce = (seconds) => page.evaluate((s) => window.OLS.Ready.announce({ title: 'Ready: thesis', body: '2 suggestions to review.', seconds: s }), seconds);

  // ---- the moments
  const ui0 = await state();
  ok(ui0.notify_sound === true && ui0.notify_min_seconds === 20, 'on by default: a sound, after 20 seconds');
  await announce(5);
  ok((await osc()) === 0 && (await notes()) === 0, 'a request quicker than that is not announced');
  await announce(60);
  ok((await osc()) === 2, 'a long one plays the chime (two notes)');
  ok((await notes()) === 0 && !/Ready/.test(await page.title()), 'while the tab is in front there is no notification and the title is left alone');
  await away(true);
  const base = await page.title();
  await announce(60);
  ok((await notes()) === 1 && (await page.evaluate(() => window.__notes[0][0])) === 'Ready: thesis', 'with the tab out of sight a notification appears');
  ok((await page.title()).startsWith('✓ Ready'), 'and the tab title gets a tick');
  await announce(60);
  ok((await page.title()) === '✓ Ready · ' + base, 'a second one does not stack the tick');
  await away(false);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  ok((await page.title()) === base, 'coming back to the tab removes the tick');

  // ---- Settings
  await page.evaluate(() => { location.hash = '#/settings'; });
  await page.waitForSelector('section[aria-label="When a request is ready"]');
  const card = page.locator('section[aria-label="When a request is ready"]');
  ok(/Telegram not set up/.test(await card.innerText()), 'the card says Telegram is not set up here');
  ok(await card.locator('label:has-text("Send me a message on Telegram") input').isDisabled(), 'and that choice cannot be ticked');
  const before = await osc(), shownBefore = await notes();
  await card.locator('button:has-text("Test")').click();
  await card.locator('.notice').waitFor();
  ok((await osc()) === before + 2, 'Test plays the chime');
  ok(/chime/.test(await card.locator('.notice').innerText()) && /notification/.test(await card.locator('.notice').innerText()), 'and says what you should have heard and seen');
  ok((await notes()) === shownBefore + 1, 'Test shows a real notification');
  await page.screenshot({ path: `${out}/ready-settings.png`, fullPage: true });
  await card.locator('label:has-text("Play a short sound") input').uncheck();
  await card.locator('input[aria-label="Seconds before a request is announced"]').fill('0');
  await card.locator('button:has-text("Save")').click();
  await page.waitForFunction(async () => (await (await fetch('/api/state', { headers: { 'X-Requested-With': 'ols' } })).json()).settings.ui.notify_sound === false);
  const ui1 = await state();
  ok(ui1.notify_sound === false && ui1.notify_min_seconds === 0, 'Save keeps the choices');
  const o1 = await osc();
  await announce(1);
  ok((await osc()) === o1, 'with the sound off there is no chime');
  await card.locator('label:has-text("Play a short sound") input').check();
  await card.locator('button:has-text("Save")').click();
  await page.waitForFunction(async () => (await (await fetch('/api/state', { headers: { 'X-Requested-With': 'ols' } })).json()).settings.ui.notify_sound === true);

  // ---- the real wiring: a request in the chat that ends announces itself
  ok((await post('/api/settings', { paper: 'soil-yield', agent: 'agent' })) === 200, 'the paper takes requests as an assistant');
  await page.evaluate(() => { location.hash = '#/chat'; });
  await page.reload();
  await page.waitForSelector('.chat-page');
  const o2 = await osc();
  await page.fill('#composer', 'the study area paragraph reads badly, please fix it');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant .approach', { timeout: 60000 });
  await page.waitForFunction((n) => window.__osc > n, o2, { timeout: 10000 });
  ok((await osc()) >= o2 + 2, 'when the assistant finishes in the chat, the chime plays');
  await browser.close();
  const real = errors.filter((e) => !/Failed to load resource/i.test(e));
  ok(real.length === 0, 'no console errors' + (real.length ? ': ' + real.join(' | ') : ''));
  console.log(failed ? `${failed} check(s) FAILED` : 'all checks passed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
