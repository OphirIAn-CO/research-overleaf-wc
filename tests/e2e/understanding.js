// A request is read as a whole and, when it is not clearly right, the author sees what was understood before anything happens: a pasted paragraph with a verb in it is not a request to
// write; "the citation finding online" is a search of the internet for that paragraph. In a real browser, against the demo server.
// usage: python3 tests/e2e/demo_server.py --port 8795 &      (prints the URL with the token and the HOME folder)
//        node tests/e2e/understanding.js <url-with-token> [home-folder] [screenshot-dir]
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const url = process.argv[2];
const out = process.argv[4] || '/tmp/claude-0/scratch/shots';
fs.mkdirSync(out, { recursive: true });
const errors = [];
let failed = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };
const PARA = 'The second paragraph describes soils, slopes and the access roads that were used to reach every farm in the sample, and we extracted the farm list \\citep{smith2020} to create the sampling frame for every region.';

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`console ${m.type()}: ${m.text()}`); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  await page.goto(url);
  await page.waitForSelector('.chat-page');

  // ---- an unclear request: the reading is shown first, and nothing is done yet
  await page.fill('#composer', `Specifically for this paragraph, lets add the incite citation finding online for the statements in it\n\n${PARA}`);
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant:has-text("Is this what you want?")', { timeout: 30000 });
  const card = await page.$eval('.msg.assistant:has-text("Is this what you want?")', (e) => e.innerText);
  ok(/references found on the internet for the paragraph you pasted/.test(card), 'it says what it understood, in plain words');
  ok(/About: the paragraph you pasted/.test(card), 'and which text it is about');
  ok(await page.isVisible('button:has-text("Yes, go ahead")') && await page.isVisible('button:has-text("Not what I meant")') && await page.isVisible('button:has-text("Read it again with my correction")'), 'with a way to accept, correct or reject it');
  ok((await page.$$eval('.msg.assistant .bubble select option', (o) => o.length)) >= 7, 'and to choose another kind of request');
  ok(!(await page.$('.refcand')) && !(await page.$('.msg.assistant .approach')), 'nothing was searched or written yet');
  await page.screenshot({ path: `${out}/understanding-card.png` });

  // ---- the author adds a note and accepts
  await page.fill('textarea[aria-label="Anything to correct or add"]', 'only studies about farms and sampling');
  await page.click('button:has-text("Yes, go ahead")');
  await page.waitForSelector('.refcand', { timeout: 40000 });
  const thread = await page.$eval('.thread', (e) => e.innerText);
  ok(/Understood: .*references found on the internet/.test(thread), 'the card now says it was understood and carried out');
  ok(/Done as: Add in-text citations/.test(thread), 'as the kind of request it was confirmed to be');
  ok(await page.isVisible('button:has-text("Add them and cite them in the text")'), 'references offered for that paragraph, to be cited there');
  await page.screenshot({ path: `${out}/understanding-done.png` });

  // ---- a clear request goes ahead, with a line saying how it was read
  await page.waitForFunction(() => document.querySelector('.composer button.primary') && document.querySelector('.composer button.primary').textContent === 'Send', null, { timeout: 40000 });
  await page.fill('#composer', 'improve the flow of the text before figure 1 in methods');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.read-as', { timeout: 30000 });
  ok(/Read as:/.test(await page.$eval('.read-as', (e) => e.innerText)) && await page.isVisible('.read-as button:has-text("Not what I meant")'), 'a clear request goes ahead and shows how it was read, with a way to say it was wrong');
  await page.waitForSelector('.msg.assistant .approach', { timeout: 40000 });

  // ---- the author can say the kind of request themselves
  await page.waitForFunction(() => document.querySelector('.composer button.primary') && document.querySelector('.composer button.primary').textContent === 'Send', null, { timeout: 40000 });
  await page.selectOption('#request-kind', 'edit');
  await page.fill('#composer', 'make the first paragraph of the introduction shorter');
  await page.press('#composer', 'Enter');
  await page.waitForFunction(() => document.querySelectorAll('.msg.user').length >= 3);
  ok((await page.$eval('#request-kind', (e) => e.value)) === '', 'the chosen kind is for one request only');

  await browser.close();
  const real = errors.filter((e) => !/Failed to load resource/i.test(e));
  ok(real.length === 0, 'no console errors' + (real.length ? ': ' + real.join(' | ') : ''));
  console.log(failed ? `${failed} check(s) FAILED` : 'all checks passed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
