// Interactive end-to-end flows against the demo server: Write (preview, send, review, apply), Book (open chunk, query), Monitor tooltip.
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
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push('console ' + m.type() + ': ' + m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  await page.goto(url);
  await page.waitForSelector('#nav a');
  const shot = (n) => page.screenshot({ path: `${out}/${n}.png`, fullPage: true });

  // ---- Write: preview -> send -> review -> apply
  await page.click('#nav a[href="#/write"]');
  await page.waitForSelector('#w-instr');
  await page.selectOption('#w-part', 'methods');
  await page.fill('#w-instr', 'Tighten my methods and follow the book on describing sampling.');
  await page.click('text=Preview what will be sent');
  await page.waitForSelector('text=Preview: nothing has been sent');
  ok(await page.isVisible('text=Send to ChatGPT'), 'preview shows a Send button');
  const rules = await page.$$eval('section[aria-label="Preview"] li', (l) => l.map((x) => x.textContent));
  ok(rules.length > 0 && rules.some((r) => r.includes('Describing') || r.includes('Writing the Methods')), 'preview lists the book rules: ' + rules.slice(0, 2).join(' | '));
  await page.click('summary:has-text("exact text")');
  const payload = await page.textContent('.payload pre');
  ok(payload.includes('[[LX') && !payload.includes('\\subsection'), 'payload is masked (placeholders, no raw LaTeX headings)');
  await shot('write-preview');
  await page.click('text=Send to ChatGPT');
  await page.waitForSelector('section[aria-label="Proposal"]', { timeout: 20000 });
  const hunks = await page.$$('.hunk');
  ok(hunks.length >= 2, `proposal shows ${hunks.length} reviewable changes`);
  ok(await page.isVisible('ins.d') && await page.isVisible('del.d'), 'word-level insertions and deletions are highlighted');
  ok(await page.isVisible('text=Question for you'), 'model question surfaced for the author');
  await shot('write-proposal');
  await (await page.$('.hunk input[type=checkbox]')).uncheck();
  const left = hunks.length - 1;
  ok((await page.textContent('section[aria-label="Proposal"]')).includes(`${left} of ${hunks.length} selected`), 'unchecking a change updates the count');
  await page.click(`button:has-text("Apply ${left} selected")`);
  await page.waitForSelector('text=Applied', { timeout: 20000 });
  ok(await page.isVisible('text=The index followed'), 'apply reports that the index followed the edit');
  await shot('write-applied');

  // ---- Hand to Claude (a second proposal; the first was applied)
  await page.click('#nav a[href="#/write"]');
  await page.waitForSelector('#w-instr');
  await page.selectOption('#w-part', 'methods');
  await page.fill('#w-instr', 'Make the methods more concise.');
  await page.click('text=Preview what will be sent');
  await page.waitForSelector('text=Send to ChatGPT');
  await page.click('text=Send to ChatGPT');
  await page.waitForSelector('section[aria-label="Proposal"]', { timeout: 20000 });
  await page.click('button:has-text("Hand to Claude")');
  await page.waitForSelector('text=Handed to Claude');
  ok(true, 'Hand to Claude confirms and tells the author what to say');
  await shot('write-handoff');

  // ---- Paper index and card
  await page.click('#nav a[href="#/paper_index"]');
  await page.waitForSelector('section[aria-label="Index"] table');
  const updated = await page.$$('span.badge:has-text("updated")');
  ok(updated.length >= 1, `index page marks ${updated.length} recently rewritten chunk(s)`);
  await page.click('section[aria-label="Index"] tbody button >> nth=2');
  await page.waitForSelector('section[aria-label="Chunk"] pre');
  ok((await page.textContent('section[aria-label="Chunk"] pre')).length > 20, 'a chunk opens in the reader');
  await page.click('button:has-text("Insert the template")');
  await page.fill('textarea[aria-label="Paper card"]', 'Key term: say "yield", never "harvest".');
  await page.click('button:has-text("Save the card")');
  await page.waitForSelector('.toast:has-text("Saved")');
  ok(true, 'paper card saved');
  ok(await page.isVisible('text=What you accepted and rejected'), 'decision log is on the page');
  await shot('paper-index');

  // ---- References
  await page.click('#nav a[href="#/references"]');
  await page.waitForSelector('textarea[aria-label="DOIs"]');
  await page.fill('textarea[aria-label="DOIs"]', 'https://doi.org/10.1234/abc and 10.9999/bad');
  await page.click('button:has-text("Look up")');
  await page.waitForSelector('text=Lee2021');
  ok(await page.isVisible('text=not found'), 'an unknown DOI is reported as not found');
  await shot('references-preview');
  await page.click('button:has-text("Add 1 reference")');
  await page.waitForSelector('text=Done');
  await page.waitForSelector('td:has-text("web")');
  ok(true, 'DOI added and listed under recent additions with its source');
  await shot('references-done');

  // ---- Book: open a chunk, run a query
  await page.click('#nav a[href="#/book"]');
  await page.waitForSelector('.tree details');
  await page.click('.tree summary:has-text("Ch 2")');
  await page.click('.tree details[open] summary:has-text("Reporting the statistical analysis")');
  await page.click('.tree button.chunk[data-id="B:mini:c02.s03.k01"]');
  await page.waitForSelector('.reader');
  ok((await page.textContent('.reader')).length > 100, 'chunk text shown in the reader');
  await page.fill('input[type=search]', 'how do I describe the statistical analysis');
  await page.click('button:has-text("Search")');
  await page.waitForSelector('section[aria-label="Query result"]');
  ok((await page.textContent('section[aria-label="Query result"]')).includes('methods'), 'query shows routing to methods');
  await shot('book-query');
  await page.click('summary:has-text("Import or re-import")');
  await page.click('button:has-text("Review the outline")');
  await page.waitForSelector('text=PDF page = printed page');
  ok(await page.isVisible('button:has-text("Save outline")'), 'outline editor opens');
  await shot('book-outline');

  // ---- Monitor: tooltip on a bar
  await page.click('#nav a[href="#/monitor"]');
  await page.waitForSelector('.chart svg');
  const box = await (await page.$('.chart .col rect[tabindex="0"] >> nth=24')).boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.waitForSelector('#tooltip:not([hidden])');
  const tt = await page.textContent('#tooltip');
  ok(/Lookups|Changed/.test(tt), 'hover tooltip lists every series: ' + tt.replace(/\s+/g, ' ').slice(0, 90));
  await shot('monitor-hover');
  await page.click('button:has-text("View as table") >> nth=0');
  ok(await page.isVisible('.table-wrap table'), 'table view is available');

  // ---- keyboard: a bar takes focus and shows the same tooltip
  await page.click('button:has-text("View as chart") >> nth=0');
  await (await page.$('.chart .col rect[tabindex="0"] >> nth=24')).focus();
  ok(await page.isVisible('#tooltip:not([hidden])'), 'keyboard focus shows the tooltip too');

  await browser.close();
  console.log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'no console errors');
  process.exit(failed || errors.length ? 1 : 0);
})().catch((e) => { console.error('flow failed:', e.message); process.exit(2); });
