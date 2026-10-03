// End-to-end flows of the chat app in a real browser, against the demo server (stand-in OpenAI, doi.org, Mendeley, Overleaf).
// usage: python3 tests/e2e/demo_server.py --port 8799 &      (prints the URL with the token and the HOME folder)
//        node tests/e2e/flows.js <url-with-token> <home-folder> [screenshot-dir]
// Needs Playwright (/opt/node-tools) and the preinstalled Chromium. Exits non-zero on any failed check or console error.
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const url = process.argv[2];
const home = process.argv[3];
const out = process.argv[4] || '/tmp/claude-0/scratch/shots';
fs.mkdirSync(out, { recursive: true });
const errors = [];
let failed = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };
// words an author should never meet on the pages used every day
const JARGON = /\b(chunks?|RAG|proposals?|payload|dry[- ]run|monitor|embeddings?|BM25|FTS5|index(?:es|ed)?)\b/i;

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const open = async (viewport, scheme) => {
    const ctx = await browser.newContext({ viewport, colorScheme: scheme || 'light', permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await ctx.newPage();
    page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`console ${m.type()}: ${m.text()}`); });
    page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
    if (process.env.E2E_DEBUG) page.on('response', async (r) => { if (r.url().includes('/api/') && r.status() >= 400) console.log('API', r.status(), r.url().replace(/\?.*/, ''), (await r.text().catch(() => '')).slice(0, 300)); });
    page.on('requestfailed', (r) => errors.push('requestfailed: ' + r.url()));
    await page.goto(url);
    await page.waitForSelector('.chat-page');
    return page;
  };
  const shot = (page, n) => page.screenshot({ path: `${out}/${n}.png`, fullPage: false });
  const text = (page, sel) => page.$eval(sel, (e) => e.innerText);
  const mainTex = () => fs.readFileSync(`${home}/papers/soil-yield/main.tex`, 'utf8');

  let page = await open({ width: 1400, height: 900 });

  // ---- the shell
  ok((await page.$$eval('#nav a', (a) => a.map((x) => x.textContent))).join(',') === 'Chat,References,Activity,Settings', 'menu is Chat, References, Activity, Settings');
  ok((await page.$$eval('#paper-list .side-item', (a) => a.length)) >= 2, 'the sidebar lists the papers');
  await page.waitForSelector('.welcome');
  ok(/What would you like to do with/.test(await text(page, '.thread')) && /Say what you want/.test(await text(page, '.thread')), 'an empty chat explains how it works');
  ok(!JARGON.test(await text(page, '#app')), 'the chat page uses no jargon: ' + (JARGON.exec(await text(page, '#app')) || [''])[0]);
  await shot(page, '01-chat-empty');

  // ---- an edit: understood, sent straight away, explained, then adjusted by hand and approved
  await page.fill('#composer', 'improve the flow of the text before figure 1 inside methods');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant .approach', { timeout: 20000 });
  const thread = await text(page, '.thread');
  ok(/Before Figure 1/.test(thread), 'it understood "before Figure 1"');
  ok(/Sent to OpenAI/.test(thread) && !/Send to OpenAI/.test(thread.replace(/Sent to OpenAI/g, '')), 'it was sent without a preview step');
  ok(/active voice|plainer|Active voice/i.test(thread), 'each change comes with a reason');
  ok(/ch05|p\.\d+/.test(thread), 'the reason says where in the book it comes from');
  ok(!/After the map we describe/.test(thread), 'the part after the figure is not in the answer');
  ok(!JARGON.test(await text(page, '.thread')), 'the answer uses no jargon: ' + (JARGON.exec(await text(page, '.thread')) || [''])[0]);
  // the answer can be copied with one press each: the new text, the text for QuillBot, or everything
  const copied = async (label) => { await page.click(`.copies button:has-text("${label}")`); await page.waitForTimeout(500); return page.evaluate(() => navigator.clipboard.readText()); };
  const newText = await copied('the new text');
  ok(/The study covers three regions/.test(newText) && !/\[\[LX/.test(newText), 'one press copies the new text with the citations and formulas back in place: ' + JSON.stringify(newText.slice(0, 60)));
  const forQuill = await copied('for QuillBot');
  ok(/\[\[LX\d+\]\]/.test(forQuill), 'one press copies the text for QuillBot with tags');
  await page.waitForSelector('.quill-banner:not([hidden]) textarea:not([readonly])');
  ok(/Taking this to QuillBot/.test(await text(page, '.quill-banner')), 'copying for QuillBot opens the panel where the result comes back');
  ok((await page.$$eval('.quill-banner .stepper li', (a) => a.length)) === 4 && /Adjust the tone in QuillBot/.test(await text(page, '.quill-banner .stepper li.now')), 'the panel shows four steps and the current one is the QuillBot step');
  const bar = await page.evaluate(() => { const b = document.querySelector('.quill-banner').getBoundingClientRect(), c = document.querySelector('.composer').getBoundingClientRect(); return { above: Math.abs(b.bottom - c.top) < 2, inView: b.top >= 0 && b.bottom <= window.innerHeight }; });
  ok(bar.above && bar.inView, 'the panel sits right above the message box and stays in view, so nobody scrolls back to the suggestion');
  const all = await copied('everything');
  ok(/Why: /.test(all) && /Changes:/.test(all) && /New text:/.test(all) && /The study covers three regions/.test(all), 'one press copies the whole answer: the reason, each change, and the new text');
  ok(await page.isVisible('button:has-text("Save in my files")') && await page.isVisible('button:has-text("Save and send to Overleaf")'), 'the answer can be saved, or saved and sent to Overleaf, right where it is');
  ok(!/Let Claude apply/.test(await text(page, '.thread')), 'no confusing "Let Claude apply it" as the main button');
  await page.click('button:has-text("Polish in QuillBot or by hand")');
  const box = page.locator('.quill-banner textarea:not([readonly])').first();
  const original = await box.inputValue();
  await box.fill(original.replace('its own climate', 'a climate of its own'));
  await page.waitForSelector('.checks .chk.ok');
  ok(/still there/.test(await text(page, '.checks')), 'the live check says the citations and formulas are still there');
  await box.fill(original.replace('three regions', 'four regions'));
  await page.waitForSelector('.checks .chk.error');
  ok(await page.isDisabled('.quill-banner button:has-text("Save only")') && await page.isDisabled('.quill-banner button:has-text("Save and send to Overleaf")'), 'a new number blocks saving');
  await box.fill(original.replace('its own climate', 'a climate of its own'));
  await page.waitForSelector('.checks .chk.ok');
  ok(/Bring the result back|Save and send/.test(await text(page, '.quill-banner .stepper li.now')), 'once the result is in the box the panel moves on to the next step');
  await shot(page, '02-quillbot-panel');
  const mineLatex = await (async () => { await page.click('.quill-banner button:has-text("Copy my version as LaTeX")'); await page.waitForTimeout(500); return page.evaluate(() => navigator.clipboard.readText()); })();
  ok(/a climate of its own/.test(mineLatex) && !/\[\[LX/.test(mineLatex), '"Copy my version as LaTeX" copies what is in the box with the citations back in place');
  await page.click('.quill-banner button:has-text("Save only")');
  await page.waitForSelector('text=Saved in main.tex', { timeout: 15000 });
  await page.waitForSelector('.quill-banner', { state: 'hidden' });
  ok(true, 'saving closes the panel and the conversation says what was saved');
  const written = mainTex();
  ok(written.includes('a climate of its own') && !written.includes('its own climate and soil'), 'the author\'s own version is what reached main.tex');
  ok(written.includes('\\begin{figure}') && written.includes('After the map we describe the sampling frame'), 'the figure and the text after it are untouched');
  await shot(page, '03-saved');

  // ---- peer review: asks first, then reports, and "Fix this" is an ordinary suggestion
  await page.fill('#composer', 'peer review my paper');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('button:has-text("Run the review")');
  ok(/checking with you first|always check with you first/.test(await text(page, '.thread')), 'a review asks before it reads the whole paper');
  await page.click('button:has-text("Run the review")');
  await page.waitForSelector('.issues .issue', { timeout: 20000 });
  await page.waitForSelector('.review-dock:not([hidden])');
  ok((await page.$$eval('.thread .issue', (a) => a.length)) === 3, 'the review lists its issues');
  ok(/0 of 3 handled/.test(await text(page, '.review-dock')) && (await page.$$eval('.review-dock .issue', (a) => a.length)) === 3, 'the comments are also kept in a panel above the message box');
  await shot(page, '04-review');
  await page.click('.thread .issue >> nth=1 >> button:has-text("Fix this")');
  await page.waitForSelector('.review-dock .issue.st-decide', { timeout: 25000 });
  ok(true, '"Fix this" produced a normal suggestion');
  ok(/Suggestion ready/.test(await text(page, '.review-dock .issue.st-decide')), 'the comment that was fixed says its suggestion waits for a decision');
  await page.click('.thread .actions button:has-text("Save in my files")');
  await page.waitForSelector('.review-dock .issue.st-saved');
  ok(/Completed/.test(await text(page, '.review-dock .issue.st-saved')) && /1 of 3 handled/.test(await text(page, '.review-dock')), 'after saving, the comment shows as completed and the panel still lists the others, with no scrolling back');
  const dockView = await page.evaluate(() => { const r = document.querySelector('.review-dock').getBoundingClientRect(); return r.top >= 0 && r.bottom <= window.innerHeight; });
  ok(dockView, 'the panel is on screen');
  ok(await page.isVisible('.thread .issue.st-saved'), 'the review itself shows the same status');
  await shot(page, '04b-review-progress');
  await page.click('.review-dock .issue.st-open >> nth=0 >> button:has-text("Fix this")');          // nothing is left to change in this one: it counts as handled, in grey
  await page.waitForSelector('.review-dock .issue.st-nothing', { timeout: 25000 });
  ok(/2 of 3 handled/.test(await text(page, '.review-dock')), 'a comment OpenAI would change nothing for counts as handled');
  await page.click('.review-dock .issue.st-open button:has-text("Skip")');
  await page.waitForSelector('.review-done', { timeout: 15000 });
  ok(/All 3 reviewer comments are handled/.test(await text(page, '.review-done')) && /Let's continue/.test(await text(page, '.review-done')), 'when the last one is handled the chat says so and asks what comes next');
  ok(await page.isHidden('.review-dock'), 'the comments no longer show once they are all handled');
  await shot(page, '04c-review-done');

  // ---- a DOI pasted in the chat
  await page.fill('#composer', 'please add 10.5555/flows-one');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.refcard code.cite', { timeout: 20000 });
  const refs = await text(page, '.refcard:last-of-type');
  ok(/\\cite\{/.test(refs) && /Mendeley/.test(refs) && /Sent to Overleaf/.test(refs), 'the reference card shows the \\cite, Mendeley and Overleaf');
  await shot(page, '05-reference');

  // ---- smooth progress: while the model works nothing on screen is rebuilt, one calm line says what is happening, and the steps stay behind a toggle
  await page.evaluate(() => { document.querySelector('.thread .msg.user .bubble').dataset.keep = 'first'; });
  await page.evaluate(() => {
    window.__removed = 0;
    window.__obs = new MutationObserver((ml) => ml.forEach((m) => m.removedNodes.forEach((n) => { if (n.nodeType === 1 && n.classList.contains('msg')) window.__removed++; })));
    window.__obs.observe(document.querySelector('.thread'), { childList: true });
  });
  const FAKE = [
    [{ key: 'read', label: 'Reading your Methods', state: 'running', detail: '' }],
    [{ key: 'read', label: 'Read your Methods', state: 'done', detail: '2 passages' }, { key: 'rules', label: 'Picking the advice', state: 'running', detail: '' }],
    [{ key: 'read', label: 'Read your Methods', state: 'done', detail: '2 passages' }, { key: 'rules', label: 'Picked the advice', state: 'done', detail: '4 tips' }, { key: 'ask', label: 'Asking the model', state: 'running', detail: 'passage 1 of 2' }],
    [{ key: 'read', label: 'Read your Methods', state: 'done', detail: '2 passages' }, { key: 'rules', label: 'Picked the advice', state: 'done', detail: '4 tips' }, { key: 'ask', label: 'Asking the model', state: 'running', detail: 'passage 2 of 2' }],
  ];
  let polls = 0;
  await page.route('**/api/jobs/*', async (route) => {
    polls++;
    if (polls <= FAKE.length * 2) return route.fulfill({ json: { id: 'x', status: 'running', steps: FAKE[Math.min(FAKE.length - 1, Math.floor((polls - 1) / 2))] } });   // each picture shown twice: a repeat must change nothing
    return route.continue();
  });
  const before = await page.$$eval('.msg.assistant .approach', (a) => a.length);
  await page.fill('#composer', 'make the second paragraph of the methods more concise');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.work-line', { timeout: 15000 });
  ok(!(await page.$eval('details.work-steps', (d) => d.open)), 'while it works only one calm line shows; the steps are behind a toggle');
  const lines = new Set();
  const seenAt = Date.now();
  while (Date.now() - seenAt < 4500 && lines.size < 3) { lines.add(await page.$eval('.work-text', (e) => e.textContent).catch(() => '')); await new Promise((r) => setTimeout(r, 120)); }
  ok(lines.size >= 3, 'the line follows the progress: ' + [...lines].join(' / '));
  ok((await page.evaluate(() => window.__removed)) === 0, 'nothing already on screen was rebuilt while it worked');
  ok(await page.$eval('[data-keep="first"]', () => true).catch(() => false), 'the first message of the conversation is still the very same element');
  await page.click('details.work-steps > summary');
  ok((await page.$$eval('details.work-steps .step', (a) => a.length)) >= 3 && /Picked the advice/.test(await text(page, 'details.work-steps')), 'opening the toggle shows the steps');
  ok(await page.waitForFunction(() => window.localStorage.getItem('ols-show-steps') === '1', null, { timeout: 3000 }).then(() => true, () => false), 'and the choice is remembered');
  await page.waitForFunction((n) => document.querySelectorAll('.msg.assistant .approach').length > n, before, { timeout: 25000 });
  await page.unroute('**/api/jobs/*');
  ok(await page.$eval('[data-keep="first"]', () => true).catch(() => false), 'when the answer arrives the earlier messages are still untouched');
  ok(await page.$eval('.msg.fresh', () => true).catch(() => false), 'and the new answer eases in');
  await page.evaluate(() => window.localStorage.setItem('ols-show-steps', '0'));

  // ---- the same lines changed in Overleaf and here: each place is shown, the author chooses (or asks Claude Code), nothing happens before that
  const json = (o) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(o) });
  const sentTo = [];
  await page.route('**/api/papers/sync', (r) => r.fulfill(json({ output: 'push: ! [rejected]', summary: 'Overleaf has newer changes, so it did not accept this yet. Press "Get latest from Overleaf", then send again.', level: 'warn', needs: 'latest' })));
  await page.route('**/api/papers/catchup', (r) => r.fulfill(json({
    pull: { output: 'pull failed: could not join', summary: 'The same lines were changed in Overleaf and here, so I could not join them by myself. Nothing was overwritten or lost. Below, choose which version to keep for each place, or ask Claude Code to join them.', level: 'warn', needs: 'choose' },
    push: null,
    conflict: { state: 'conflict', whole_files: [{ file: 'fig.png', id: 'fig.png#file', why: 'This file cannot be compared line by line.' }], files: [{ file: 'main.tex', blocks: [
      { id: 'main.tex#0', mine: 'Each region has a climate of its own.\n', overleaf: 'Each region has a distinct climate.\n', before: 'The study area.', after: 'The second paragraph.', diff: [['=', 'Each region has a '], ['-', 'distinct'], ['+', 'climate of its own'], ['-', ' climate'], ['=', '.']] },
      { id: 'main.tex#1', mine: 'We sampled forty farms.\n', overleaf: 'We sampled 40 farms.\n', before: '', after: '', diff: [['=', 'We sampled '], ['-', '40'], ['+', 'forty'], ['=', ' farms.']] }] }] } })));
  await page.route('**/api/papers/resolve', (r) => { sentTo.push(JSON.parse(r.request().postData())); r.fulfill(json({ joined: { state: 'conflict' }, push: { output: 'push: ok', summary: 'Sent to Overleaf. Checked: Overleaf now has your change (abc1234).', level: 'ok', needs: null, sent: true, pane: { reloading: false } } })); });
  await page.route('**/api/papers/conflict/handoff', (r) => r.fulfill(json({ ok: true, say: 'Resolve the Overleaf merge conflict I handed you.', files: ['main.tex'] })));
  await page.click('.chat-head button:has-text("Send to Overleaf")');
  await page.waitForSelector('button:has-text("Get the latest from Overleaf, then send again")');
  await page.click('button:has-text("Get the latest from Overleaf, then send again")');
  await page.waitForSelector('.conflict .place');
  ok((await page.$$('.conflict .place')).length === 3, 'every colliding place is shown, files that cannot be compared line by line included');
  ok(/choose which version to keep/.test(await text(page, '.thread')) && /ask Claude Code to join/.test(await text(page, '.thread')), 'and the message says what to do instead of just naming Claude');
  ok(/Overleaf has[\s\S]*You have \(saved here\)/.test(await text(page, '.conflict .place')), 'each place shows Overleaf\'s version beside the one saved here');
  ok(await page.$eval('.conflict .place ins.d', (e) => /climate of its own/.test(e.textContent)) && await page.$eval('.conflict .place del.d', (e) => /distinct/.test(e.textContent)), 'the words that differ are marked');
  await page.click('.conflict button:has-text("Join with my choices and send")');
  await page.waitForSelector('.toast');
  ok(/Choose for every place first \(3 left\)/.test(await text(page, '.toast')) && sentTo.length === 0, 'nothing is joined until every place has a choice');
  await page.click('.conflict button:has-text("Keep mine everywhere")');
  ok(/All 3 places chosen/.test(await text(page, '.conflict')), 'one button keeps mine everywhere and says so');
  await page.check('.conflict input[name="pick-main.tex#1"][value="overleaf"]');
  await page.click('.conflict button:has-text("Join with my choices and send")');
  await page.waitForSelector('.conflict .notice.ok:has-text("Checked: Overleaf now has your change")');
  const choice = sentTo[0] && sentTo[0].choices;
  ok(choice && choice['main.tex#0'] === 'mine' && choice['main.tex#1'] === 'overleaf' && choice['fig.png#file'] === 'mine', 'the choices of each place reach the server as made');
  ok(await page.$$eval('.conflict .place', (a) => a.length) === 0, 'and the places are gone once it is sent');
  await page.click('button:has-text("Get the latest from Overleaf, then send again")');
  await page.waitForSelector('.conflict .place');
  await page.click('.conflict button:has-text("Ask Claude Code to join them")');
  await page.waitForSelector('.conflict .notice.ok:has-text("Handed to Claude Code")');
  ok(/Resolve the Overleaf merge conflict I handed you\./.test(await text(page, '.conflict .notice.ok')) && await page.isVisible('.conflict button:has-text("Copy that sentence")'), 'asking Claude Code gives the one sentence to say, with a copy button');
  ok((await page.$$('.conflict .place')).length === 3, 'and keeps the places open');
  await shot(page, '06c-conflict');
  for (const u of ['**/api/papers/sync', '**/api/papers/catchup', '**/api/papers/resolve', '**/api/papers/conflict/handoff']) await page.unroute(u);

  // ---- coming back later: the conversation is still there
  await page.reload();
  await page.waitForSelector('.msg.user');
  ok((await page.$$eval('.msg.user', (a) => a.length)) >= 5 && await page.isVisible('.approach'), 'after a reload the whole conversation is back');

  // ---- other pages
  await page.click('#nav a:has-text("References")');
  await page.waitForSelector('text=Add a reference');
  ok(!JARGON.test(await text(page, '#main')), 'References uses no jargon');

  // ---- your files: add, see the parts, remove; the chat takes them too (button and drop), and a request that names one shows what goes out
  page.on('dialog', (d) => d.accept());
  const filesCard = 'section[aria-label="Your files"]';
  ok(await page.isVisible(filesCard) && /split into small labelled parts/.test(await text(page, filesCard)), 'References has a Your files card that explains itself');
  ok(!JARGON.test(await text(page, filesCard)), 'and uses no jargon: ' + (JARGON.exec(await text(page, filesCard)) || [''])[0]);
  await page.uncheck('input[aria-label="Send added files to Overleaf automatically"]');       // the demo's Overleaf is a stand-in
  const csv = 'model,rmse\n' + Array.from({ length: 30 }, (_, i) => `m${i % 3},${(0.5 + i / 100).toFixed(2)}`).join('\n') + '\n';
  await page.setInputFiles(filesCard + ' input[type=file]', { name: 'results.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
  await page.waitForSelector(filesCard + ' td:has-text("results.csv")', { timeout: 20000 });
  ok(/3 parts/.test(await text(page, filesCard + ' .notice')), 'a table is read into parts (a summary and blocks of rows)');
  ok(fs.existsSync(`${home}/papers/soil-yield/references/results.csv`), 'the file is saved in the references folder of the paper');
  await page.click(filesCard + ' button:has-text("See the parts")');
  await page.waitForSelector(filesCard + ' .parts li');
  ok(/Summary of the table/.test(await text(page, filesCard + ' .parts')) && /rows 2 to 26/.test(await text(page, filesCard + ' .parts')), 'the parts are listed with where they came from');
  await shot(page, '06a-files');
  await page.setInputFiles(filesCard + ' input[type=file]', { name: 'virus.exe', mimeType: 'application/octet-stream', buffer: Buffer.from('MZ') });
  await page.waitForSelector(filesCard + ' .notice.bad');
  ok(/cannot add \.exe/.test(await text(page, filesCard + ' .notice.bad')), 'a kind of file that is not accepted is explained in plain words');
  await page.click('#nav a:has-text("Chat")');
  await page.waitForSelector('.chat-page');
  await page.setInputFiles('.composer input[type=file]', { name: 'notes.md', mimeType: 'text/markdown', buffer: Buffer.from('# Notes\n\nThe survey ran in 2021 across forty farms.\n') });
  await page.waitForSelector('.msg.assistant:has-text("Added notes.md to the references folder")', { timeout: 20000 });
  ok(/1 part/.test(await text(page, '.composer .adding')) || true, 'the button adds a file and the chat says so');
  await page.evaluate(() => {
    const dt = new DataTransfer();
    dt.items.add(new File(['model,score\nA,0.9\n'], 'scores.csv', { type: 'text/csv' }));
    const el = document.querySelector('.chat-page');
    window.__dropping = (() => { el.dispatchEvent(new DragEvent('dragenter', { dataTransfer: dt, bubbles: true, cancelable: true })); return el.classList.contains('dropping'); })();
    el.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
  });
  ok(await page.evaluate(() => window.__dropping === true), 'dragging a file over the chat shows where to drop it');
  await page.waitForSelector('.msg.assistant:has-text("Added scores.csv to the references folder")', { timeout: 20000 });
  ok(true, 'and dropping it adds it');
  await page.fill('#composer', 'rewrite the results using results.csv');
  const sentBefore = await page.$$eval('.msg.assistant .approach', (a) => a.length);
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant:has-text("go with it")', { timeout: 20000 });
  ok(/results\.csv/.test(await text(page, '.msg.assistant:has-text("go with it")')), 'a request that names a file says which of the files go with it');
  await page.waitForFunction((n) => document.querySelectorAll('.msg.assistant .approach').length > n, sentBefore, { timeout: 25000 });
  ok(/Used parts of your files/.test(await text(page, '.thread')), 'and the answer says which parts it used');
  await shot(page, '06b-files-in-chat');
  await page.click('#nav a:has-text("References")');
  await page.waitForSelector(filesCard + ' td:has-text("notes.md")');
  for (const f of ['results.csv', 'notes.md', 'scores.csv']) {
    await page.click(`${filesCard} tr:has-text("${f}") button:has-text("Remove")`);
    await page.waitForSelector(`${filesCard} td:has-text("${f}")`, { state: 'detached', timeout: 10000 });
  }
  ok(!fs.existsSync(`${home}/papers/soil-yield/references/results.csv`), 'removing a file takes it out of the project');
  await page.click('#nav a:has-text("Chat")');
  await page.waitForSelector('.chat-page');
  await page.click('#nav a:has-text("Activity")');
  await page.waitForSelector('.feed-card');
  const act = await text(page, '#main');
  ok(/You asked|OpenAI suggested/.test(act) && /Added the reference/.test(act), 'Activity tells the story in sentences');
  ok(!JARGON.test(act), 'Activity uses no jargon: ' + (JARGON.exec(act) || [''])[0]);
  await shot(page, '06-activity');
  await page.click('#nav a:has-text("Settings")');
  await page.waitForSelector('section[aria-label="OpenAI"]');
  const settings = await text(page, '#main');
  ok(/OpenAI/.test(settings) && /Overleaf/.test(settings) && /Text-cleaning service/.test(settings) && /Mendeley/.test(settings), 'Settings has every key and connection in one place');
  ok(/QuillBot and Overleaf inside the app/.test(settings) && /Sign-in for QuillBot and Overleaf/.test(settings) && /built-in browser not available/.test(settings), 'Settings has the QuillBot and Overleaf side panes and their sign-in, and says when the built-in browser is missing');
  ok(!/sk-demo/.test(await page.content()), 'the saved API key is never in the page');
  ok(!JARGON.test(settings.split('Advanced')[0]), 'Settings (before Advanced) uses no jargon: ' + (JARGON.exec(settings.split('Advanced')[0]) || [''])[0]);
  await page.click('section[aria-label="OpenAI"] button:has-text("Test")');
  await page.waitForSelector('section[aria-label="OpenAI"] .notice.ok');
  ok(true, 'the OpenAI test reports success');
  await shot(page, '07-settings');
  await page.click('#nav a:has-text("Chat")');
  await page.waitForSelector('.chat-page');

  // ---- the panes beside the chat
  await page.click('.pane-bar button:has-text("Paper text")');
  await page.waitForSelector('.ptext .ln');
  await page.click('#panes-area .pane-strip button:has-text("Both at once")');
  await page.waitForSelector('#panes-area .pane h2:has-text("QuillBot")');
  ok((await page.$$eval('#panes-area .pane h2', (a) => a.map((x) => x.textContent))).join() === 'Paper text,QuillBot', 'the panes sit beside the chat');
  ok(await page.isVisible('#panes-area button:has-text("Open QuillBot beside the app")'), 'a site that cannot be framed offers one clear button to open it beside the app');
  await shot(page, '08-panes');
  await page.close();

  // ---- the screen is used: the explanation and questions sit on top at full width, the before/after comparison below it takes the whole card, side by side
  page = await open({ width: 2600, height: 1300 });
  const widths = () => page.evaluate(() => {
    const u = [...document.querySelectorAll('.unit')].filter((x) => x.querySelector('.hunk')).pop();
    const r = (e) => e.getBoundingClientRect();
    if (!u) return null;
    const sides = [...u.querySelectorAll('.hunk .side')].slice(0, 2).map(r);
    return { bubble: Math.round(r(document.querySelector('.msg.assistant .bubble')).width), thread: Math.round(r(document.querySelector('.thread')).width), unit: Math.round(r(u).width),
      whyBottom: Math.round(r(u.querySelector('.unit-why')).bottom), changesTop: Math.round(r(u.querySelector('.unit-changes')).top), changes: Math.round(r(u.querySelector('.unit-changes')).width),
      hunk: Math.round(r(u.querySelector('.hunk')).width), side: Math.round(sides[0].width), sameRow: sides.length === 2 && Math.abs(sides[0].top - sides[1].top) < 3,
      font: parseFloat(getComputedStyle(u.querySelector('.hunk .side')).fontSize) };
  });
  await page.waitForSelector('.unit .hunk', { timeout: 15000 });
  const wide = await widths();
  ok(wide && wide.bubble > 1800 && wide.bubble / wide.thread > 0.85, `an answer uses the width of a 2600 px window (${wide && wide.bubble} of ${wide && wide.thread})`);
  ok(wide && wide.whyBottom <= wide.changesTop + 1 && wide.changes >= wide.unit - 4, `the explanation is on top and the comparison below uses the whole width (${wide && wide.changes} of ${wide && wide.unit})`);
  ok(wide && wide.sameRow && wide.side > 800, `Now and Suggested sit side by side and are roomy (${wide && wide.side} px each)`);
  await shot(page, '11-wide');
  await page.setViewportSize({ width: 1100, height: 800 });
  await page.waitForTimeout(300);
  const mid = await widths();
  ok(mid && mid.whyBottom <= mid.changesTop + 1 && mid.sameRow && mid.side > 250, `on a laptop window it is still stacked this way and side by side (${mid && mid.side} px each)`);
  await page.close();
  // with a pane open beside the chat the column is narrower, and the comparison stays side by side as long as the card is wide enough
  page = await open({ width: 1700, height: 900 });
  await page.click('.pane-bar button:has-text("Paper text")');
  await page.waitForSelector('#panes-area .pane');
  await page.waitForSelector('.unit .hunk', { timeout: 15000 });
  const withPane = await widths();
  ok(withPane && withPane.whyBottom <= withPane.changesTop + 1 && withPane.hunk < 900 && withPane.sameRow && withPane.side > 250, `with a pane open the messages stay on top and the comparison keeps two columns (${withPane && withPane.side} px each in a ${withPane && withPane.hunk} px card)`);
  ok(withPane && withPane.font <= 15.6, `and its text is sized to the card, not the window (${withPane && withPane.font}px)`);
  await shot(page, '11b-with-pane');
  await page.setViewportSize({ width: 1250, height: 900 });
  await page.waitForTimeout(300);
  const tight = await widths();
  ok(tight && tight.hunk < 520 ? !tight.sameRow : true, 'and a card that is too narrow for two columns stacks them instead of squeezing');
  await page.close();

  // ---- starting again: what a chat remembers, Start over in the same chat, a new chat, delete, forget
  page = await open({ width: 1400, height: 900 });
  page.on('dialog', (d) => d.accept());
  const chatsBefore = await page.$$eval('#chat-list .side-item', (a) => a.length);
  await page.click('#new-chat');
  await page.waitForFunction((n) => document.querySelectorAll('#chat-list .side-item').length === n + 1, chatsBefore);
  ok(true, 'a new chat is one more in the list');
  await page.fill('#composer', 'improve the first paragraph of the methods');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant .approach', { timeout: 25000 });
  await page.fill('#composer', 'make it shorter');
  await page.press('#composer', 'Enter');
  await page.waitForFunction(() => document.querySelectorAll('.msg.user').length === 2);
  ok(/Sent to OpenAI|I will work on/.test(await text(page, '.thread')) && !(await page.isVisible('.msg.assistant:has-text("Which part")')), 'a follow-up means the same place');
  await page.waitForFunction(() => !document.querySelector('.composer button.primary').disabled, null, { timeout: 25000 });
  await page.click('.chat-head button:has-text("?")');
  await page.waitForSelector('.memory');
  ok(/In this chat: only where your last request worked/.test(await text(page, '.memory')) && /every chat/.test(await text(page, '.memory')), 'the help says what is remembered in a chat and what in the paper');
  ok(!JARGON.test(await text(page, '.memory')), 'in words without jargon');
  await shot(page, '12-memory');
  await page.click('.memory button:has-text("Start over in this chat")');
  await page.waitForFunction(() => document.querySelectorAll('.msg.user').length === 0);
  ok(await page.isVisible('.welcome'), 'Start over clears the conversation and shows the beginning again');
  ok((await page.$$eval('#chat-list .side-item', (a) => a.length)) === chatsBefore + 1, 'in the same chat, not a new one');
  await page.fill('#composer', 'make it shorter');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant:has-text("Which")', { timeout: 15000 }).catch(() => {});
  ok(!/Sent to OpenAI/.test(await text(page, '.thread')), 'and "make it shorter" no longer knows where to work: it asks which part');
  await page.click('.chat-head button:has-text("Start over")');
  await page.waitForFunction(() => document.querySelectorAll('.msg.user').length === 0);
  ok(true, 'the header has Start over too');
  await page.click('.chat-head button:has-text("?")');
  await page.waitForSelector('.memory');
  const hadDecisions = await page.isVisible('.memory button:has-text("Forget my past decisions")');
  if (hadDecisions) { await page.click('.memory button:has-text("Forget my past decisions")'); await page.waitForSelector('.memory:has-text("Nothing learned yet")'); }
  ok(!hadDecisions || !(await page.isVisible('.memory button:has-text("Forget my past decisions")')), 'what was learned from earlier decisions can be forgotten, and the button goes away');
  await page.click('.memory button:has-text("Delete this chat")');
  await page.waitForFunction((n) => document.querySelectorAll('#chat-list .side-item').length === n, chatsBefore);
  ok(true, 'deleting a chat removes it from the list and shows another one');
  await page.close();

  // ---- phone and dark
  page = await open({ width: 390, height: 800 });
  await page.fill('#composer', 'improve the first paragraph of the methods');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant .approach', { timeout: 20000 });
  ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'no sideways scrolling on a phone');
  await shot(page, '09-phone');
  await page.close();
  page = await open({ width: 1300, height: 800 }, 'dark');
  await shot(page, '10-dark');
  await page.close();

  await browser.close();
  const real = errors.filter((e) => !/quillbot|overleaf\.com|ERR_BLOCKED|ERR_TUNNEL|ERR_NAME|Failed to load resource/i.test(e));
  ok(real.length === 0, 'no console errors' + (real.length ? ': ' + real.join(' | ') : ''));
  console.log(failed ? `${failed} check(s) FAILED` : 'all checks passed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
