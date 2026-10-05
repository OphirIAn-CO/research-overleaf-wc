// A paragraph of the paper pasted into the message ("for this paragraph: ...") is found in the paper and is the only thing worked on, in a real browser, against the demo server.
// usage: python3 tests/e2e/demo_server.py --port 8784 &      (prints the URL with the token and the HOME folder)
//        node tests/e2e/pasted_paragraph.js <url-with-token> [screenshot-dir]
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const url = process.argv[2];
const out = process.argv[3] || '/tmp/claude-0/scratch/shots';
fs.mkdirSync(out, { recursive: true });
const errors = [];
let failed = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };
const PARA = 'The second paragraph describes soils, slopes and the access roads that were used to reach every farm in the sample.';

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`console ${m.type()}: ${m.text()}`); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  await page.goto(url);
  await page.waitForSelector('.chat-page');
  await page.fill('#composer', `For this paragraph:\n\n${PARA}\n\nthe sentences are too long in the methodology`);
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant .approach', { timeout: 40000 });
  const thread = await page.$eval('.thread', (e) => e.innerText);
  ok(/I will work on the paragraph you pasted \(1 passage/.test(thread), 'the plan says it works on the paragraph that was pasted, one passage');
  ok(/Found it in your paper \(main\.tex line/.test(thread), 'and says where it found it');
  ok(!/bigger than a usual request/.test(thread), 'so it does not stop to ask about the size of a whole section');
  ok(!/Statistical analysis|Failure count|10 passages/.test(thread), 'and the rest of the methodology is not in it');
  await page.screenshot({ path: `${out}/pasted-paragraph.png` });

  await page.waitForFunction(() => document.querySelector('.composer button.primary') && document.querySelector('.composer button.primary').textContent === 'Send', null, { timeout: 40000 });
  // ---- the same paragraph, with an in-text citation asked for: the app looks for sources on the internet itself, for that paragraph, and does not hand a list back
  await page.fill('#composer', `For this paragraph:\n\n${PARA}\n\nthere was no in-text citation added in the methodology`);
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant:has-text("I will also look for sources on the internet")', { timeout: 30000 });
  ok(true, 'it says it will look for sources on the internet itself');
  await page.waitForFunction(() => document.querySelector('.composer button.primary') && document.querySelector('.composer button.primary').textContent === 'Send', null, { timeout: 60000 });
  ok(!(await page.$('.refcand')), 'and does not hand a list of candidates back to the author');
  const afterThread = await page.$eval('.thread', (e) => e.innerText);
  ok(!/Tick the ones you want/.test(afterThread), 'nothing waits to be ticked');
  await page.screenshot({ path: `${out}/pasted-paragraph-citation.png` });
  await browser.close();
  const real = errors.filter((e) => !/Failed to load resource/i.test(e));
  ok(real.length === 0, 'no console errors' + (real.length ? ': ' + real.join(' | ') : ''));
  console.log(failed ? `${failed} check(s) FAILED` : 'all checks passed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
