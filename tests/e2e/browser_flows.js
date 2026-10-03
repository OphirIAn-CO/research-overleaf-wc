// End-to-end flows of the built-in browser panes in a real browser, against the demo server started with --browser.
// The "QuillBot" and "Overleaf" here are a stand-in site that refuses to be framed (X-Frame-Options: DENY), like the real ones.
// usage: python3 tests/e2e/demo_server.py --port 8798 --browser &      (prints SITE, URL and HOME; --real shows an ordinary browser on a virtual screen instead)
//        node tests/e2e/browser_flows.js <url-with-token> <home-folder> <site-url> [screenshot-dir] [toolbar-height]   (toolbar-height 87 with --real: the page starts below the browser's own toolbar)
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const http = require('http');
const url = process.argv[2];
const home = process.argv[3];
const site = process.argv[4];
const out = process.argv[5] || '/tmp/claude-0/scratch/shots-browser';
const TOP = Number(process.argv[6] || 0);
fs.mkdirSync(out, { recursive: true });
const errors = [];
let failed = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const seen = () => new Promise((res, rej) => http.get(site + 'seen', (r) => { let d = ''; r.on('data', (c) => { d += c; }); r.on('end', () => res(JSON.parse(d))); }).on('error', rej));
async function until(fn, ms) { const end = Date.now() + (ms || 12000); for (;;) { const v = await fn(); if (v) return v; if (Date.now() > end) return v; await sleep(150); } }

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] });
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`console ${m.type()}: ${m.text()}`); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('requestfailed', (r) => { if (!/\/frame\?/.test(r.url())) errors.push('requestfailed: ' + r.method() + ' ' + r.url() + ' ' + ((r.failure() || {}).errorText || '')); });   // leaving a pane ends its waiting request on purpose
  await page.goto(url);
  await page.waitForSelector('.chat-page');
  const shot = (n) => page.screenshot({ path: `${out}/${n}.png` });
  const text = (sel) => page.$eval(sel, (e) => e.innerText);

  // where something at (rx, ry) of the remote page is on screen: the remote page is drawn at least 1100 wide and shrunk to fit the pane
  const onScreen = async (rx, ry) => page.$eval('.bview', (v, [x, y]) => {
    const r = v.getBoundingClientRect();
    const rw = Math.min(1920, Math.max(r.width, 1100));
    const s = r.width / rw;
    return { x: r.left + x * s, y: r.top + y * s };
  }, [rx, ry + TOP]);
  const clickAt = async (rx, ry) => { const p = await onScreen(rx, ry); await page.mouse.click(p.x, p.y); };
  const shown = () => page.waitForFunction(() => { const i = document.querySelector('.bshot'); const o = document.querySelector('.bmsg'); return i && i.dataset.live === '1' && o && o.hidden; }, null, { timeout: 40000 });

  // ---- the side panes use the built-in browser, one at a time, and the page behind refuses framing
  ok(!(await page.isVisible('#panes-area')), 'the side area is closed until asked for');
  await page.click('.pane-bar button:has-text("QuillBot")');
  await page.waitForSelector('#panes-area .bpane h2:has-text("QuillBot")');
  await shown();
  ok(await page.$eval('.bshot', (i) => i.width >= 600), 'a live picture of the page appears inside the app');
  ok(!(await page.isVisible('#panes-area button:has-text("beside the app")')), 'no "open in a window" button is needed');
  ok((await page.$$eval('#panes-area .bpane', (a) => a.length)) === 1, 'one pane at a time uses the whole side area');
  await shot('b1-quillbot');

  // ---- typing and clicking reach the page
  await clickAt(60, 95);
  await page.keyboard.type('hello from the app');
  await clickAt(60, 180);
  await page.keyboard.type('the draft text');
  await clickAt(60, 280);                                 // Go
  let s = await until(async () => { const v = await seen(); return v.log.length ? v : null; });
  ok(s && s.log[s.log.length - 1].a === 'hello from the app' && s.log[s.log.length - 1].b === 'the draft text', 'clicks and typed text reach the page: ' + JSON.stringify(s && s.log[s.log.length - 1]));
  await sleep(700);
  await shot('b2-typed');

  // ---- copy out and paste in
  await clickAt(60, 180);
  await page.keyboard.press('Control+a');
  await page.keyboard.press('Control+c');
  const copied = await until(async () => { const t = await page.evaluate(() => navigator.clipboard.readText()); return t === 'the draft text' ? t : null; }, 5000);
  ok(copied === 'the draft text', 'Ctrl+C on selected text puts it on your own clipboard');
  await page.evaluate(() => navigator.clipboard.writeText('pasted \\cite{x} text'));
  await clickAt(60, 180);
  await page.keyboard.press('Control+a');
  await page.keyboard.press('Control+v');
  await clickAt(60, 280);
  s = await until(async () => { const v = await seen(); return v.log.length >= 2 ? v : null; });
  ok(s && s.log[s.log.length - 1].b === 'pasted \\cite{x} text', 'Ctrl+V pastes your clipboard into the page: ' + JSON.stringify(s && s.log[s.log.length - 1]));

  // ---- sign-in details are saved in Settings and filled in with one button
  await page.click('#nav a:has-text("Settings")');
  await page.waitForSelector('section[aria-label="Sign-in for QuillBot and Overleaf"]');
  const st = await text('#main');
  ok(/QuillBot and Overleaf inside the app/.test(st) && /built-in browser ready/.test(st), 'Settings says the built-in browser is ready');
  ok(/file that only you can read/.test(st) && /not encrypted/.test(st), 'Settings is honest about where the password is kept');
  for (const [name, email, pw] of [['QuillBot', 'me@quill.test', 'quill pass 1'], ['Overleaf', 'me@leaf.test', 'leaf pass 2']]) {
    await page.fill(`input[aria-label="${name} email"]`, email);
    await page.fill(`input[aria-label="${name} password"]`, pw);
    await page.click(`.site-form:has(input[aria-label="${name} email"]) button:has-text("Save")`);
    await page.waitForSelector(`.toast:has-text("${name} sign-in saved")`);
    await sleep(300);
    await page.waitForSelector('section[aria-label="Sign-in for QuillBot and Overleaf"]');
  }
  const saved = JSON.parse(fs.readFileSync(`${home}/sites.json`, 'utf8'));
  ok(saved.quillbot.password === 'quill pass 1' && saved.overleaf.email === 'me@leaf.test', 'both sign-ins are in sites.json');
  ok((fs.statSync(`${home}/sites.json`).mode & 0o777) === 0o600, 'sites.json is readable by you only');
  const html = await page.content();
  ok(!/quill pass 1|leaf pass 2/.test(html), 'a saved password is never put back into the page');
  ok(await page.$eval('input[aria-label="QuillBot password"]', (e) => e.value === '' && /saved/.test(e.placeholder)), 'the password box only says one is saved');
  await shot('b3-settings-signin');
  await page.click('#nav a:has-text("Chat")');
  await page.waitForSelector('.chat-page');
  await page.waitForSelector('#panes-area .bpane h2:has-text("QuillBot")');          // the choice of pane is remembered
  await page.click('.pane-bar button:has-text("Overleaf")');
  await page.waitForSelector('#panes-area .bpane h2:has-text("Overleaf")');
  await shown();
  ok((await page.$$eval('#panes-area .bpane h2', (a) => a.map((x) => x.textContent))).join() === 'Overleaf', 'choosing Overleaf replaces QuillBot in the side area');
  await page.waitForFunction(() => /Sign in/.test(document.querySelector('.pane-head span.grow').textContent), null, { timeout: 15000 });
  await page.click('#panes-area button:has-text("Fill my sign-in")');
  await page.waitForSelector('.bline:has-text("Filled in your email and password")');
  ok(!(await seen()).posts.length, 'filling the form does not submit it');
  await page.keyboard.press('Enter');
  s = await until(async () => { const v = await seen(); return v.posts.length ? v : null; });
  ok(s && s.posts[0].email === 'me@leaf.test' && s.posts[0].password === 'leaf pass 2', 'the saved email and password were typed into the page: ' + JSON.stringify(s && s.posts[0]));
  await shot('b4-overleaf');

  // ---- the picture keeps its proportions, both at once, wider, and back
  const undistorted = () => page.$eval('.bview', (v) => { const c = v.querySelector('canvas'); const r = v.getBoundingClientRect(); return Math.abs(c.width / c.height - r.width / r.height) / (r.width / r.height) < 0.06; });
  ok(await until(undistorted, 6000), 'the picture has the proportions of the pane (nothing is stretched)');
  await page.click('#panes-area button[aria-label="Bigger text"]');
  ok(await page.waitForFunction(() => document.querySelector('.bshot').width <= 760, null, { timeout: 8000 }).then(() => true, () => false), '"Aa" draws the page narrower so its text is bigger');
  ok(await until(undistorted, 6000), 'bigger text is not stretched either');
  await page.click('#panes-area button[aria-label="Bigger text"]');
  await page.waitForFunction(() => document.querySelector('.bshot').width >= 1000, null, { timeout: 8000 });
  await page.click('#panes-area .pane-strip button:has-text("Both at once")');
  await page.waitForFunction(() => document.querySelectorAll('#panes-area .bpane').length === 2);
  await shown();
  const boxes = await page.$$eval('#panes-area .bview', (a) => a.map((v) => { const r = v.getBoundingClientRect(); return [Math.round(r.top), Math.round(r.height)]; }));
  ok(boxes.length === 2 && boxes[1][0] >= boxes[0][0] + boxes[0][1] - 2 && boxes.every((b) => b[1] >= 240), 'two panes sit one above the other, each with room');
  await page.click('#panes-area .pane-strip button:has-text("Wider")');
  await page.waitForFunction(() => document.getElementById('panes-area').getBoundingClientRect().width > 800);
  ok(await page.$eval('.chat-page', (e) => e.getBoundingClientRect().width) >= 380, '"Wider" gives the side area more room but never squeezes the chat away');
  await sleep(1200);
  ok(await until(undistorted, 6000), 'still not stretched after the pane changed size');
  await shot('b5-both');
  await page.click('#panes-area .pane-strip button:has-text("Both at once")');
  await page.click('#panes-area .pane-strip button:has-text("Wider")');
  await page.waitForFunction(() => document.querySelectorAll('#panes-area .bpane').length === 1);
  await page.click('#panes-area .pane-strip button:has-text("Close")');
  await page.waitForFunction(() => document.getElementById('panes-area').hidden);
  ok(true, 'the side area closes');
  await page.click('.pane-bar button:has-text("QuillBot")');
  await page.waitForSelector('#panes-area .bpane h2:has-text("QuillBot")');
  await shown();

  // ---- a human check on the page: the pane says so and offers the author's own browser
  await clickAt(210, 330);                                   // the stand-in's link to a page with a Cloudflare check frame
  await page.waitForSelector('#panes-area .challenge:not([hidden])', { timeout: 20000 });
  ok(/checking that you are human/.test(await text('#panes-area .challenge')), 'a human check on the page is announced in the pane');
  await shot('b6a-human-check');
  const popup = ctx.waitForEvent('page', { timeout: 15000 }).catch(() => null);
  await page.click('#panes-area .challenge button');
  ok(!!(await popup), 'the site opens in a window of the author\'s own browser');
  await page.waitForSelector('#panes-area button:has-text("Open QuillBot beside the app")');
  ok((await page.evaluate(async () => (await (await fetch('/api/connections', { headers: { 'X-Requested-With': 'ols' } })).json()).panes.builtin.quillbot)) === false, 'and the choice is remembered in Settings');
  await page.evaluate(async () => { await fetch('/api/settings', { method: 'POST', headers: { 'X-Requested-With': 'ols', 'Content-Type': 'application/json' }, body: JSON.stringify({ ui: { builtin_quillbot: true } }) }).then((r) => r.text()); });
  await page.reload();
  await page.waitForSelector('#panes-area .bpane h2:has-text("QuillBot")');
  await shown();
  await page.evaluate(async () => { await fetch('/api/browser/quillbot/nav', { method: 'POST', headers: { 'X-Requested-With': 'ols', 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'home', paper: 'soil-yield' }) }).then((r) => r.text()); });
  await sleep(1500);

  // ---- a view that is slow to answer: the pane says so after a few keystrokes or scrolls, offers the smooth way, and remembers "keep it here"; a quick view never nags
  const typeSome = async (n, wait) => { await clickAt(60, 180); for (let i = 0; i < n; i++) { await page.keyboard.type('x'); await sleep(wait); } };
  const hint = '#panes-area .bpane .warn:has-text("is slow here")';
  await typeSome(8, 250);
  ok(!(await page.isVisible(hint)), 'a quick view does not nag');
  await page.route('**/api/browser/quillbot/frame*', async (r) => { await sleep(1300); try { await r.continue(); } catch (e) { /* the pane moved on while this picture was being held back */ } });
  await page.evaluate(() => { localStorage.removeItem('ols-slow-ok-quillbot'); });
  await page.reload();
  await page.waitForSelector('#panes-area .bpane h2:has-text("QuillBot")');
  await shown();
  await clickAt(60, 180);
  for (let i = 0; i < 12 && !(await page.isVisible(hint)); i++) { await page.keyboard.type('y'); await sleep(1700); }
  ok(await page.isVisible(hint), 'when keys and scrolls are slow to show again and again, the pane says so');
  ok(/much smoother/.test(await text(hint)) && await page.isVisible('#panes-area .bpane .warn button:has-text("Open QuillBot in a window beside the app")'), 'and offers a window of the author\'s own browser');
  await shot('b6b-slow');
  await page.click('#panes-area .bpane .warn button:has-text("Keep it here")');
  ok(!(await page.isVisible(hint)), '"keep it here" closes it');
  for (let i = 0; i < 7; i++) { await page.keyboard.type('z'); await sleep(1500); }
  ok(!(await page.isVisible(hint)), 'and it does not come back (it is remembered for a day)');
  await page.unroute('**/api/browser/quillbot/frame*');
  ok(await page.isVisible('#panes-area .bpane button[title*="your own browser"]'), 'the header always has the quiet way to a window of the own browser');
  await page.evaluate(() => { localStorage.removeItem('ols-slow-ok-quillbot'); });
  await page.evaluate(async () => { await fetch('/api/browser/quillbot/nav', { method: 'POST', headers: { 'X-Requested-With': 'ols', 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'home', paper: 'soil-yield' }) }).then((r) => r.text()); });   // the typing above left text in the page: start it afresh
  await sleep(1500);

  // ---- an edit, and the hand-off to QuillBot and back
  await page.fill('#composer', 'improve the first paragraph of the methods');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant .approach', { timeout: 30000 });
  await page.click('button:has-text("Polish in QuillBot or by hand")');
  await page.waitForSelector('.quill-banner textarea:not([readonly])');
  ok(await page.isVisible('.quill-banner button:has-text("Put it in QuillBot")'), 'the QuillBot panel offers to put the suggestion into QuillBot');
  const suggestion = await page.locator('.quill-banner textarea[readonly]').first().inputValue();
  await page.click('.quill-banner button:has-text("Put it in QuillBot")');
  await page.waitForSelector('#panes-area .bpane h2:has-text("QuillBot")');
  await shown();
  await page.waitForSelector('.toast:has-text("It is in QuillBot")', { timeout: 30000 });
  await sleep(700);
  await shot('b6-in-quillbot');
  await clickAt(380, 280);                                // the stand-in's own "Copy" button
  await page.click('.quill-banner button:has-text("Take QuillBot\'s result")');
  await page.waitForSelector('.toast:has-text("Took")');
  const mine = await page.locator('.quill-banner textarea:not([readonly])').first().inputValue();
  ok(mine === suggestion, 'what the page copied with its own Copy button is taken back into "Your final version"' + (mine === suggestion ? '' : ` (got ${JSON.stringify(mine.slice(0, 80))} of ${mine.length}, wanted ${JSON.stringify(suggestion.slice(0, 80))} of ${suggestion.length})`));
  await clickAt(60, 180);
  await page.keyboard.press('End');
  await page.keyboard.type(' TWEAKED');
  await page.keyboard.press('Control+a');
  await page.click('.quill-banner button:has-text("Take the text I selected")');
  await page.waitForFunction(() => /TWEAKED/.test(document.querySelector('.quill-banner textarea:not([readonly])').value));
  ok(true, 'the text selected in QuillBot is taken back as well');
  await page.waitForSelector('.checks .chk');
  await shot('b7-taken-back');

  // ---- phone width has no side area to break the page
  await ctx.close();
  const phone = await browser.newContext({ viewport: { width: 390, height: 800 } });
  const p2 = await phone.newPage();
  await p2.goto(url);
  await p2.waitForSelector('.chat-page');
  ok(await p2.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'no sideways scrolling on a phone');
  await phone.close();

  await browser.close();
  const real = errors.filter((e) => !/ERR_BLOCKED|ERR_TUNNEL|ERR_NAME|Failed to load resource/i.test(e));
  ok(real.length === 0, 'no console errors' + (real.length ? ': ' + real.join(' | ') : ''));
  console.log(failed ? `${failed} check(s) FAILED` : 'all checks passed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
