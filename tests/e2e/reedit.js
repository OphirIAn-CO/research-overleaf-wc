// Writing a part again without starting over, in a real browser: a suggestion of three passages asks one question; the questions of all passages are in ONE panel, the passage that was answered
// is ticked for you, only it is written again, the other two are carried over as they were, and the one suggestion that results replaces the old one (which becomes a single line).
// usage: python3 tests/e2e/demo_server.py --port 8797 &      (prints the URL with the token and the HOME folder)
//        node tests/e2e/reedit.js <url-with-token> <home-folder> [screenshot-dir]
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const url = process.argv[2];
const home = process.argv[3];
const out = process.argv[4] || '/tmp/claude-0/scratch/shots';
fs.mkdirSync(out, { recursive: true });
const errors = [];
let failed = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };
const FILLER = Array.from({ length: 44 }, (_, i) => `The step number ${i + 1} of the procedure is carried out in the way that was described before.`).join(' ');
const DOC = ['\\documentclass{article}', '\\begin{document}', '',
  `\\section{Alpha}\nWe sampled 40 farms by stratified random sampling. Informed consent was obtained from all farmers. ${FILLER}\n`,
  `\\section{Beta}\nWe fitted a model to the data. ${FILLER}\n`,
  `\\section{Gamma}\nEach region has a distinct climate and soil. ${FILLER}\n`, '\\end{document}', ''].join('\n');
const file = `${home}/papers/traffic-gnn/main.tex`;

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 1100 } })).newPage();
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`console ${m.type()}: ${m.text()}`); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  await page.goto(url);
  await page.waitForSelector('.chat-page');
  const api = (method, path, body) => page.evaluate(async ([m, p, b]) => {
    const r = await fetch(p, { method: m, headers: { 'X-Requested-With': 'ols', 'Content-Type': 'application/json' }, body: m === 'POST' ? JSON.stringify(b) : undefined });
    return { status: r.status, body: await r.json() };
  }, [method, path, body]);

  // a paper of three long sections: each is a passage of its own
  fs.writeFileSync(file, DOC);
  await page.click('#paper-list .side-item:has-text("traffic-gnn")');
  await page.waitForSelector('.chat-page');
  const idx = await api('GET', '/api/paper/traffic-gnn/index?kind=tex');
  const ids = ['Alpha', 'Beta', 'Gamma'].map((n) => (idx.body.chunks.find((c) => (c.section_path || '').includes(n)) || {}).id);
  ok(ids.every(Boolean), 'the three sections are in the paper index');
  const cid = await page.evaluate(() => window.OLS.S.chatId);
  const sent = await api('POST', '/api/chat/send', { paper: 'traffic-gnn', id: cid, text: 'tighten these three sections', chunk_ids: ids });
  ok(sent.status === 200, 'a request over the three sections is sent');
  await page.reload();
  await page.waitForSelector('.chat-page');
  await page.waitForSelector('.msg.assistant .approach', { timeout: 60000 });

  // ---- one panel for every question of the part
  const panel = page.locator('.ask-panel:not([hidden])');
  await panel.waitFor({ timeout: 20000 });
  ok((await page.$$eval('.thread .unit', (a) => a.length)) === 3, 'the suggestion has three passages');
  ok((await page.$$eval('.ask-panel textarea.answer', (a) => a.length)) === 1, 'its one question has one box for the answer, in the panel');
  ok(await page.isVisible('.thread .notice:has-text("1 question for you")'), 'and the card says at the top that a question is waiting');
  ok(!(await page.isVisible('.thread .unit textarea.answer')), 'no answer box is spread among the passages');
  const ticks = await page.$$eval('.ask-panel .redo input[type=checkbox]', (a) => a.map((x) => x.checked));
  ok(ticks.length === 3 && ticks[0] && !ticks[1] && !ticks[2], 'the passage with the question is ticked for writing again; the other two are not: ' + JSON.stringify(ticks));
  ok(/\(1 passage of 3\)/.test(await page.$eval('.ask-panel .primary', (b) => b.textContent)), 'and the button says how many will be written again');
  await page.screenshot({ path: `${out}/reedit-panel.png`, fullPage: true });

  // ---- answer, add a word, send: one suggestion comes back with the three passages
  const before = fs.readFileSync(file, 'utf8');
  await page.fill('.ask-panel textarea.answer', '62 percent of the farmers answered.');
  await page.fill('.ask-panel textarea[aria-label="Anything to add or change"]', 'Keep it short.');
  await page.click('.ask-panel .primary');
  await page.waitForSelector('.thread details.earlier', { timeout: 60000 });
  ok(/replaced by the one below/.test(await page.$eval('.thread details.earlier', (e) => e.textContent)), 'the old suggestion is now one line: it was replaced');
  await page.waitForFunction(() => document.querySelectorAll('.thread .unit').length >= 3 && /Kept as it was/.test(document.querySelector('.thread').innerText), null, { timeout: 30000 });
  const live = await page.$$eval('.thread .unit', (a) => a.length);
  const kept = await page.$$eval('.thread .unit-why p', (a) => a.filter((p) => /Kept as it was/.test(p.textContent)).length);
  ok(kept === 2, 'in the new suggestion two passages say they were kept as they were: ' + kept);
  ok(live >= 3, 'and it still has all three passages (' + live + ' units shown, with the replaced suggestion collapsed)');
  ok(fs.readFileSync(file, 'utf8') === before, 'nothing was saved by itself');
  await page.screenshot({ path: `${out}/reedit-after.png`, fullPage: true });

  // ---- the style changes are accepted on their own: the question stays open and can still be answered, on the saved text
  const acceptLabel = await page.locator('.thread .actions .primary').last().textContent();
  ok(/style changes/.test(acceptLabel) && /questions stay open/.test(acceptLabel), 'with a question waiting, the accept button says it accepts the style changes and the questions stay open: ' + acceptLabel);
  await page.locator('.thread .actions .primary').last().click();
  await page.waitForSelector('.thread .notice:has-text("Saved in your files")', { timeout: 30000 });
  const after = fs.readFileSync(file, 'utf8');
  ok(/We drew a stratified random sample of 40 farms\./.test(after) && /We estimated a model/.test(after) && /Each region has its own climate and soil\./.test(after), 'saving the one suggestion changes all three passages');
  const stillAsking = page.locator('.thread .ask-panel:not([hidden]) textarea.answer');
  await stillAsking.first().waitFor({ timeout: 20000 });
  ok((await stillAsking.count()) === 1, 'the question is still there to answer, under the saved suggestion');
  ok(/style changes are saved/.test(await page.$eval('.thread .notice.warn', (e) => e.textContent).catch(() => '')) || (await page.isVisible('.thread .notice:has-text("The style changes are saved")')), 'and the card says the style changes are saved');
  await page.screenshot({ path: `${out}/reedit-saved.png`, fullPage: true });
  await stillAsking.first().fill('Eighty percent answered, after two reminders.');
  const saved = fs.readFileSync(file, 'utf8');
  await page.locator('.thread .ask-panel:not([hidden]) .primary').click();
  await page.waitForSelector('.thread .notice:has-text("Your answers went into a newer suggestion")', { timeout: 60000 });
  ok(true, 'answering writes a new suggestion, and the saved one says its answers went there');
  ok(fs.readFileSync(file, 'utf8') === saved, 'the file does not change until that new suggestion is accepted');
  ok((await page.$$eval('.thread .ask-panel:not([hidden]) textarea.answer', (a) => a.length)) <= 1, 'only the newest suggestion asks');
  await browser.close();
  const real = errors.filter((e) => !/Failed to load resource/i.test(e));
  ok(real.length === 0, 'no console errors' + (real.length ? ': ' + real.join(' | ') : ''));
  console.log(failed ? `${failed} check(s) FAILED` : 'all checks passed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
