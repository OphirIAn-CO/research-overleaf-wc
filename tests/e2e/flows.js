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
  await page.click('button:has-text("Adjust the tone myself")');
  await page.waitForSelector('.tone textarea:not([readonly])');
  const box = page.locator('.tone textarea:not([readonly])').first();
  const original = await box.inputValue();
  await box.fill(original.replace('its own climate', 'a climate of its own'));
  await page.waitForSelector('.checks .chk.ok');
  ok(/still there/.test(await text(page, '.checks')), 'the live check says the citations and formulas are still there');
  await box.fill(original.replace('three regions', 'four regions'));
  await page.waitForSelector('.checks .chk.error');
  ok(await page.isDisabled('.tone button:has-text("Approve and save")'), 'a new number blocks approval');
  await box.fill(original.replace('its own climate', 'a climate of its own'));
  await page.waitForSelector('.checks .chk.ok');
  await shot(page, '02-tone-panel');
  await page.click('.tone >> text=Approve and save');
  await page.waitForSelector('text=Saved in main.tex', { timeout: 15000 });
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
  ok((await page.$$eval('.issue', (a) => a.length)) === 3, 'the review lists its issues');
  await shot(page, '04-review');
  await page.click('.issue button:has-text("Fix this") >> nth=0');
  await page.waitForFunction(() => document.querySelectorAll('.msg.assistant .approach').length >= 2, null, { timeout: 20000 });
  ok(true, '"Fix this" produced a normal suggestion');

  // ---- a DOI pasted in the chat
  await page.fill('#composer', 'please add 10.5555/flows-one');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.refcard code.cite', { timeout: 20000 });
  const refs = await text(page, '.refcard:last-of-type');
  ok(/\\cite\{/.test(refs) && /Mendeley/.test(refs) && /Sent to Overleaf/.test(refs), 'the reference card shows the \\cite, Mendeley and Overleaf');
  await shot(page, '05-reference');

  // ---- coming back later: the conversation is still there
  await page.reload();
  await page.waitForSelector('.msg.user');
  ok((await page.$$eval('.msg.user', (a) => a.length)) >= 4 && await page.isVisible('.approach'), 'after a reload the whole conversation is back');

  // ---- other pages
  await page.click('#nav a:has-text("References")');
  await page.waitForSelector('text=Add a reference');
  ok(!JARGON.test(await text(page, '#main')), 'References uses no jargon');
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
  await page.click('.pane-bar button:has-text("QuillBot")');
  await page.waitForSelector('#panes-area .pane h2:has-text("QuillBot")');
  ok((await page.$$eval('#panes-area .pane h2', (a) => a.map((x) => x.textContent))).join() === 'Paper text,QuillBot', 'the panes sit beside the chat');
  await shot(page, '08-panes');
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
