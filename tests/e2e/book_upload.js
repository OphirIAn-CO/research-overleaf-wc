// Choosing the writing book's PDF in Settings, in a real browser: the file goes up, is prepared, and the card shows it ready.
// usage: python3 tests/e2e/demo_server.py --port 8797 --no-book &      (prints the URL with the token and the HOME folder)
//        node tests/e2e/book_upload.js <url-with-token> <home-folder> [screenshot-dir]
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const path = require('path');
const url = process.argv[2];
const home = process.argv[3];
const out = process.argv[4] || '/tmp/claude-0/scratch/shots';
fs.mkdirSync(out, { recursive: true });
let failed = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(url);
  await page.waitForSelector('.chat-page');
  await page.click('#nav a:has-text("Settings")');
  const card = 'section[aria-label="Writing book"]';
  await page.waitForSelector(card);
  ok(await page.isVisible(card + ' button:has-text("Choose the book PDF")'), 'with no book yet, Settings has a Choose the book PDF button');
  ok(!/\.config/.test(await page.$eval(card, (e) => e.innerText)), 'and no one is told to look for a hidden folder');
  await (await page.$(card)).screenshot({ path: `${out}/book-before.png` });

  // a file that is not a PDF is refused in plain words and nothing changes
  await page.setInputFiles(card + ' input[type=file]', { name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('hello') });
  await page.waitForSelector('.toast');
  ok(/PDF/.test(await page.$eval('.toast', (e) => e.innerText)), 'a file that is not a PDF is refused: ' + (await page.$eval('.toast', (e) => e.innerText)));
  await page.setInputFiles(card + ' input[type=file]', { name: 'fake.pdf', mimeType: 'application/pdf', buffer: Buffer.from('this is not really a pdf') });
  await page.waitForSelector(card + ' .notice.bad');
  ok(/does not look like a PDF/.test(await page.$eval(card + ' .notice.bad', (e) => e.innerText)), 'a PDF that is not one is refused by the app');
  ok(fs.readdirSync(`${home}/inbox`).filter((f) => f.endsWith('.pdf')).length === 0, 'and nothing is left in the inbox');

  // the real thing
  const pdf = path.join(__dirname, '..', 'fixtures', 'mini_book.pdf');
  await page.setInputFiles(card + ' input[type=file]', pdf);
  await page.waitForSelector(card + ' :text("is ready"), ' + card + ' .badge.ok', { timeout: 120000 });
  const text = await page.$eval(card, (e) => e.innerText);
  ok(/pieces of advice from/.test(text), 'the book is uploaded, read and shown as ready: ' + (/[^\n]*pieces of advice[^\n]*/.exec(text) || [''])[0]);
  ok(fs.existsSync(`${home}/inbox/mini_book.pdf`) && (fs.statSync(`${home}/inbox/mini_book.pdf`).mode & 0o077) === 0, 'the PDF is in the inbox, readable only by its owner');
  ok(fs.readdirSync(`${home}/inbox`).every((f) => !f.startsWith('.upload')), 'no temporary file is left');
  await (await page.$(card)).screenshot({ path: `${out}/book-after.png` });

  await browser.close();
  const real = errors.filter((e) => !/Failed to load resource/i.test(e));
  ok(real.length === 0, 'no console errors' + (real.length ? ': ' + real.join(' | ') : ''));
  console.log(failed ? `${failed} check(s) FAILED` : 'all checks passed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
