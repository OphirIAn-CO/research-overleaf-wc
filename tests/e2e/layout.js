// Changing the form of the paper in a real browser: "move this table to the appendix and add the reference" is read as a layout request, shown first, sent, checked and shown as the exact
// before/after of each edit; applying it writes the files. Against the demo server.
// usage: python3 tests/e2e/demo_server.py --port 8796 &      (prints the URL with the token and the HOME folder)
//        node tests/e2e/layout.js <url-with-token> <home-folder> [screenshot-dir]
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const url = process.argv[2];
const home = process.argv[3];
const out = process.argv[4] || '/tmp/claude-0/scratch/shots';
fs.mkdirSync(out, { recursive: true });
const errors = [];
let failed = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };

(async () => {
  const file = `${home}/papers/soil-yield/main.tex`;
  const before = fs.readFileSync(file, 'utf8');
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 1100 } })).newPage();
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`console ${m.type()}: ${m.text()}`); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  await page.goto(url);
  await page.waitForSelector('.chat-page');
  await page.fill('#composer', 'move this table to appendix and add the the respective reference in tex');
  await page.press('#composer', 'Enter');

  // a change of layout is always shown first
  await page.waitForSelector('.msg.assistant:has-text("Is this what you want?")', { timeout: 30000 });
  ok((await page.$$eval('.msg.assistant select option', (o) => o.map((x) => x.textContent))).some((t) => /Change the LaTeX itself/.test(t)), 'a change of layout is a kind of request the author can see and choose');
  await page.click('button:has-text("Yes, go ahead")');

  // the plan says what changes and what goes out; then the suggestion
  await page.waitForSelector('.msg.assistant:has-text("I will change the form of")', { timeout: 30000 });
  const plan = await page.$eval('.msg.assistant:has-text("I will change the form of")', (e) => e.innerText);
  ok(/table 1/.test(plan) && /I do not change what the paper says/.test(plan), 'the plan names the table and promises the words stay');
  await page.waitForSelector('.layout-edit', { timeout: 40000 });
  const card = await page.$eval('.thread', (e) => e.innerText);
  ok(/What changes and why/.test(card) && /new appendix/.test(card), 'the suggestion says what changes and why');
  ok((await page.$$('.layout-edit')).length === 3, 'with each change shown as it is now and as it will be');
  ok(/Checked: labels, references, citations and the words of tables and figures are unchanged/.test(card) || /already had 1 LaTeX error\(s\) and still has 1/.test(card), 'and says what was checked (the demo paper already has a LaTeX error of its own, and says so instead of blaming the change)');
  ok(/saved together or not at all/.test(card), 'and that the changes go together');
  ok(fs.readFileSync(file, 'utf8') === before, 'nothing is written yet');
  await page.screenshot({ path: `${out}/layout-suggestion.png` });

  // apply
  await page.click('button:has-text("Apply and save in my files")');
  await page.waitForSelector('text=Saved in main.tex', { timeout: 20000 });
  const after = fs.readFileSync(file, 'utf8');
  ok(/\\appendix[\s\S]*\\label\{tab:a\}[\s\S]*\\end\{document\}/.test(after), 'the table is now in the appendix, with its label');
  ok(/Table~\\ref\{tab:a\} \(Appendix~\\ref\{app:tables\}\)/.test(after), 'and the text points to it');
  ok((after.match(/\\label\{tab:a\}/g) || []).length === 1, 'the label is defined once');

  await browser.close();
  const real = errors.filter((e) => !/Failed to load resource/i.test(e));
  ok(real.length === 0, 'no console errors' + (real.length ? ': ' + real.join(' | ') : ''));
  console.log(failed ? `${failed} check(s) FAILED` : 'all checks passed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
