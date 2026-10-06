// The agent in a real browser, against the demo server: a request in the author's own words is planned by the model (read the paper, edit the place, say what was done), with no card in front of
// the work, and the suggestion that comes out is the usual one to read and accept.
// usage: python3 tests/e2e/demo_server.py --port 8797 &      (prints the URL with the token and the HOME folder)
//        node tests/e2e/agent.js <url-with-token> <home-folder> [screenshot-dir]
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const url = process.argv[2];
const home = process.argv[3];
const out = process.argv[4] || '/tmp/claude-0/scratch/shots';
fs.mkdirSync(out, { recursive: true });
const errors = [];
let failed = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };
const main = () => fs.readFileSync(`${home}/papers/soil-yield/main.tex`, 'utf8');

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`console ${m.type()}: ${m.text()}`); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  await page.goto(url);
  await page.waitForSelector('.chat-page');
  const post = (path, body) => page.evaluate(async ([pth, b]) => (await fetch(pth, { method: 'POST', headers: { 'X-Requested-With': 'ols', 'Content-Type': 'application/json' }, body: JSON.stringify(b) })).status, [path, body]);
  ok((await post('/api/settings', { paper: 'soil-yield', agent: 'agent' })) === 200, 'the paper is set to take requests as an assistant that plans the work');
  await page.reload();
  await page.waitForSelector('.chat-page');

  const before = main();
  await page.fill('#composer', 'the study area paragraph reads badly, please fix it');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant .approach', { timeout: 60000 });
  await page.waitForSelector('.msg.assistant:has-text("tightened how its sentences flow")', { timeout: 60000 });
  const thread = await page.$eval('.thread', (e) => e.innerText);
  ok(!/I will work on/.test(thread) && !/Is this what you want\?/.test(thread), 'no plan card and no "did I understand" card stands in front of the work');
  ok(/tightened how its sentences flow/.test(thread), 'it says what it did, in plain words');
  ok(await page.isVisible('button:has-text("insert in my files"), button:has-text("save in my files")'), 'and the suggestion is the usual one to read and accept');
  ok(main() === before, 'nothing was saved by itself');
  await page.screenshot({ path: `${out}/agent.png` });
  await browser.close();
  const real = errors.filter((e) => !/Failed to load resource/i.test(e));
  ok(real.length === 0, 'no console errors' + (real.length ? ': ' + real.join(' | ') : ''));
  console.log(failed ? `${failed} check(s) FAILED` : 'all checks passed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
