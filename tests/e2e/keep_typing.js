// What the author has typed and not sent stays when they open Settings (or another page) and come back, when the tab is reloaded, and when a send does not go through.
// usage: python3 tests/e2e/demo_server.py --port 8789 &      (prints the URL with the token and the HOME folder)
//        node tests/e2e/keep_typing.js <url-with-token> [screenshot-dir]
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const url = process.argv[2];
const out = process.argv[3] || '/tmp/claude-0/scratch/shots';
fs.mkdirSync(out, { recursive: true });
const errors = [];
let failed = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`console ${m.type()}: ${m.text()}`); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  const goTo = async (name) => { await page.click(`#nav a:has-text("${name}")`); await page.waitForSelector(name === 'Chat' ? '.chat-page' : 'h1'); };
  await page.goto(url);
  await page.waitForSelector('.chat-page');

  // ---- the message box
  const draft = 'improve the flow of the text before figure 1 but keep the numbers';
  await page.fill('#composer', draft);
  await goTo('Settings');
  await goTo('Chat');
  ok((await page.inputValue('#composer')) === draft, 'text typed in the message box is still there after a visit to Settings');
  await goTo('References');
  await goTo('Chat');
  ok((await page.inputValue('#composer')) === draft, 'and after a visit to References');
  await page.goto(url);
  await page.waitForSelector('.chat-page');
  ok((await page.inputValue('#composer')) === draft, 'and after the tab is reloaded');
  // each paper keeps its own
  const onPaper = (name) => page.waitForFunction((n) => document.querySelector('.chat-page .page-head, .chat-page')?.innerText.includes(n) && document.querySelector('#paper-list [aria-current="true"]')?.innerText.includes(n), name, { timeout: 15000 });
  await page.click('#paper-list .side-item:has-text("traffic-gnn")');
  await onPaper('traffic-gnn');
  ok((await page.inputValue('#composer')) === '', 'another paper has its own, empty box');
  await page.fill('#composer', 'second paper note');
  await page.click('#paper-list .side-item:has-text("soil-yield")');
  await onPaper('soil-yield');
  ok((await page.inputValue('#composer')) === draft, 'the first paper gets its text back');

  // ---- a send that does not go through gives the text back
  await page.route('**/api/chat/send', (r) => r.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'the connection dropped' }) }));
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.toast');
  await page.waitForFunction(() => document.querySelector('#composer').value.length > 0);
  ok((await page.inputValue('#composer')) === draft, 'a send that fails does not lose the text');
  await page.unroute('**/api/chat/send');

  // ---- a send that goes through empties it for good
  await page.fill('#composer', 'improve the flow of the text before figure 1 inside methods');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant .approach', { timeout: 30000 });
  ok((await page.inputValue('#composer')) === '', 'a sent message empties the box');
  await goTo('Settings');
  await goTo('Chat');
  ok((await page.inputValue('#composer')) === '', 'and it does not come back');

  // ---- an answer to a question, and the "anything to add" box of a suggestion
  await page.fill('#composer', 'Address this reviewer comment: the description of the wells is insufficient. For the text after figure 1 in methods.');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('textarea.answer', { timeout: 40000 });
  const answer = 'Wells with at least six months of production were kept.';
  await page.fill('textarea.answer >> nth=0', answer);
  await page.click('button:has-text("Add instructions or change the request")');
  await page.waitForSelector('textarea[aria-label="Anything to add or change"]');
  const more = 'Keep it to two sentences.';
  await page.fill('textarea[aria-label="Anything to add or change"]', more);
  await page.screenshot({ path: `${out}/typing-before.png` });
  await goTo('Settings');
  await goTo('Chat');
  await page.waitForSelector('textarea.answer', { timeout: 20000 });
  ok((await page.inputValue('textarea.answer >> nth=0')) === answer, 'the answer written under a question is still there after a visit to Settings');
  await page.waitForSelector('textarea[aria-label="Anything to add or change"]', { timeout: 5000 });
  ok((await page.inputValue('textarea[aria-label="Anything to add or change"]')) === more, 'and the "anything to add" box is open again, with its text');
  await page.reload();
  await page.waitForSelector('textarea.answer', { timeout: 20000 });
  ok((await page.inputValue('textarea.answer >> nth=0')) === answer, 'and after a reload');
  await page.screenshot({ path: `${out}/typing-after.png` });

  await browser.close();
  const real = errors.filter((e) => !/Failed to load resource/i.test(e));
  ok(real.length === 0, 'no console errors' + (real.length ? ': ' + real.join(' | ') : ''));
  console.log(failed ? `${failed} check(s) FAILED` : 'all checks passed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
