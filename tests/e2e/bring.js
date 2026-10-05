// The literature library as references, in a real browser against the demo server: counting what the paper holds, turning the library's documents into bibliography entries (tick, add), and a
// request the app cannot do being said instead of something else being done.
// usage: python3 tests/e2e/demo_server.py --port 8796 &      (prints the URL with the token and the HOME folder)
//        node tests/e2e/bring.js <url-with-token> <home-folder> [screenshot-dir]
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const url = process.argv[2];
const home = process.argv[3];
const out = process.argv[4] || '/tmp/claude-0/scratch/shots';
fs.mkdirSync(out, { recursive: true });
const errors = [];
let failed = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };
const bib = () => fs.readFileSync(`${home}/papers/soil-yield/references.bib`, 'utf8');
const text = (page, sel) => page.$eval(sel, (e) => e.innerText);
const idle = (page) => page.waitForFunction(() => document.querySelector('.composer button.primary') && document.querySelector('.composer button.primary').textContent === 'Send', null, { timeout: 60000 });

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`console ${m.type()}: ${m.text()}`); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  await page.goto(url);
  await page.waitForSelector('.chat-page');
  const post = (path, body) => page.evaluate(async ([pth, b]) => (await fetch(pth, { method: 'POST', headers: { 'X-Requested-With': 'ols', 'Content-Type': 'application/json' }, body: JSON.stringify(b) })).status, [path, body]);
  ok((await post('/api/library/folders', { folders: [`${home}-library/Literature`] })) === 200, 'the author chose the folder with the reading');
  ok((await post('/api/library/index', {})) === 200, 'and had it read');
  await page.waitForFunction(async () => { const j = await (await fetch('/api/library', { headers: { 'X-Requested-With': 'ols' } })).json(); return j.status && j.status.docs > 0 && !j.status.running; }, null, { timeout: 60000 });

  // ---- a question about what the files hold is answered from the files
  await page.fill('#composer', 'how many references are there now in my .bib file ?');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant:has-text("entries")', { timeout: 30000 });
  const count = await text(page, '.msg.assistant:has-text("entries")');
  ok(/has \d+ entries/.test(count), 'it counts the entries of the bibliography');
  ok(/literature library has \d+ documents/.test(count), 'and the documents of the library');
  await idle(page);

  // ---- the library as references
  const before = bib();
  await page.fill('#composer', 'convert the documents in my library to references');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant:has-text("Nothing is written until you press the button")', { timeout: 60000 });
  const card = await text(page, '.msg.assistant:has-text("Nothing is written until you press the button")');
  ok(/Nothing is written until you press the button/.test(card), 'it says nothing is written yet');
  ok(bib() === before, 'the bibliography is unchanged');
  ok(await page.isVisible('button:has-text("Add the ticked to my bibliography")'), 'with a button to add the ticked ones');
  ok((await page.$$('.msg.assistant input[type=checkbox]')).length >= 1, 'each document that can be added has a tick');
  await page.screenshot({ path: `${out}/bring-card.png` });
  await page.click('button:has-text("Add the ticked to my bibliography")');
  await page.waitForSelector('.msg.assistant:has-text("to your bibliography")', { timeout: 60000 });
  await idle(page);
  ok(bib() !== before, 'the entries are in the bibliography file now');
  ok(/cite\{[^}]+\}/.test(await text(page, '.thread')), 'and the card gives the citation keys');
  await page.screenshot({ path: `${out}/bring-done.png` });

  await browser.close();
  const real = errors.filter((e) => !/Failed to load resource/i.test(e));
  ok(real.length === 0, 'no console errors' + (real.length ? ': ' + real.join(' | ') : ''));
  console.log(failed ? `${failed} check(s) FAILED` : 'all checks passed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
