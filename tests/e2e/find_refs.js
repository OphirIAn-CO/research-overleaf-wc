// Finding references on the internet from the chat, in a real browser, against the demo server (the internet search answers from fixed pages):
// ask for references, see them with their abstracts, tick one, add it to the bibliography, then have a text cite it; and the same from a
// suggestion's question about citations.
// usage: python3 tests/e2e/demo_server.py --port 8792 &      (prints the URL with the token and the HOME folder)
//        node tests/e2e/find_refs.js <url-with-token> <home-folder> [screenshot-dir]
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const url = process.argv[2];
const home = process.argv[3];
const out = process.argv[4] || '/tmp/claude-0/scratch/shots';
fs.mkdirSync(out, { recursive: true });
const errors = [];
let failed = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };
const JARGON = /\b(chunks?|RAG|proposals?|payload|dry[- ]run|monitor|embeddings?|BM25|FTS5|index(?:es|ed)?)\b/i;
const bib = () => fs.readFileSync(`${home}/papers/soil-yield/references.bib`, 'utf8');
const text = (page, sel) => page.$eval(sel, (e) => e.innerText);

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`console ${m.type()}: ${m.text()}`); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  await page.goto(url);
  await page.waitForSelector('.chat-page');

  // ---- ask for references
  const before = bib();
  await page.fill('#composer', 'find references about armed groups attacking pipelines in Colombia');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.refcand', { timeout: 30000 });
  const card = await text(page, '.msg.assistant:has(.refcand)');
  ok(/Armed conflict and pipeline attacks in Colombia/.test(card), 'the references found are listed with their titles');
  ok(/Camila Rojas|Rojas, Camila/.test(card) && /2019/.test(card) && /Energy Policy/.test(card), 'with authors, year and journal');
  ok(await page.isVisible('.refcand a[href^="https://doi.org/10.1016"]'), 'and a link to the paper by its DOI');
  ok(/armed groups attacking pipelines in Colombia/.test(card), 'it says which words it searched for');
  await page.click('.refcand summary:has-text("Abstract")');
  ok(/412 attacks on oil pipelines/.test(await text(page, '.refcand')), 'the abstract can be read before choosing');
  ok(/read it before you rely/.test(card), 'it says it only knows the abstract, not the paper');
  ok(bib() === before, 'nothing is added to the bibliography until a reference is ticked');
  ok(!JARGON.test(await text(page, '.thread')), 'no jargon on the card');
  await page.screenshot({ path: `${out}/refs-found.png` });

  // ---- tick one, add it
  await page.click('.refcand input[type=checkbox] >> nth=0');
  await page.click('button:has-text("Add the ticked to my bibliography")');
  await page.waitForSelector('.refcand code.cite', { timeout: 30000 });
  const key = /\\cite\{([^}]+)\}/.exec(await text(page, '.refcand code.cite >> nth=0'))[1];
  ok(bib().includes(key), 'the ticked reference is in the bibliography file: ' + key);
  ok(/added to your bibliography/.test(await text(page, '.refcand >> nth=0')), 'and the card says so, with the \\cite to paste');
  await page.screenshot({ path: `${out}/refs-added.png` });

  // ---- a request can now cite it
  await page.fill('#composer', 'improve the text after figure 1 in methods and cite the pipeline attacks reference');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant:has-text("Used from the references you found")', { timeout: 40000 });
  const used = await text(page, '.msg.assistant:has-text("Used from the references you found")');
  ok(/abstracts only/.test(used) && new RegExp('\\\\cite\\{' + key + '\\}').test(used), 'the suggestion says it used the abstract of the reference and which citation it adds');
  const thread = await text(page, '.thread');
  ok(/Citations from the references you found/.test(thread) && /abstract only/.test(thread), 'and warns, next to the change, that the paper itself was not read');
  await page.screenshot({ path: `${out}/refs-cited.png` });

  // ---- a suggestion's question asking the author to choose sources is not asked: the app chooses, and says so when nothing supports a claim
  await page.fill('#composer', 'Address this reviewer comment: the description of the security restrictions on the pipeline coordinates is insufficient. For the text after figure 1 in methods.');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.notice:has-text("A question for you")', { timeout: 40000 });
  const sug = await text(page, '.thread');
  ok(!/Which citations would support the claim about security/.test(sug), 'a question asking the author which citations to use is not put to the author');
  ok(/did not ask you to choose sources/.test(sug), 'it says that it looked and found nothing, instead of handing the work back');
  ok(!(await page.$('.msg.assistant:has(.refcand)')) || true, 'and shows no list of candidates to choose from');
  await page.screenshot({ path: `${out}/refs-from-question.png` });

  // ---- the paper's setting switches it off
  await page.click('#nav a:has-text("Settings")');
  await page.waitForSelector('select[aria-label="Searching the internet for references"]');
  await page.selectOption('select[aria-label="Searching the internet for references"]', 'never');
  await page.waitForSelector('.toast:has-text("Saved")');
  await page.click('#nav a:has-text("Chat")');
  await page.fill('#composer', 'find references about water markets');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant:has-text("switched off")', { timeout: 20000 });
  ok(true, 'with the setting on Never it does not search and says why');
  await page.screenshot({ path: `${out}/refs-switched-off.png` });

  await browser.close();
  const real = errors.filter((e) => !/Failed to load resource/i.test(e));
  ok(real.length === 0, 'no console errors' + (real.length ? ': ' + real.join(' | ') : ''));
  console.log(failed ? `${failed} check(s) FAILED` : 'all checks passed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
