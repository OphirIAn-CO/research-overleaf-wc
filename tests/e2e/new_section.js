// Asking for a section the paper does not have yet, with a long outline pasted into the request, in a real browser: the right section is chosen (not one named in the middle of the
// pasted text), the card says a new section with its heading is added, and saving puts the heading and the text into the paper's file.
// usage: python3 tests/e2e/demo_server.py --port 8786 &      (prints the URL with the token and the HOME folder)
//        node tests/e2e/new_section.js <url-with-token> <home-folder> [screenshot-dir]
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const url = process.argv[2];
const home = process.argv[3];
const out = process.argv[4] || '/tmp/claude-0/scratch/shots';
fs.mkdirSync(out, { recursive: true });
const errors = [];
let failed = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };
const EMPTY = String.raw`\documentclass{article}
\title{Risk perception}
\begin{document}
\maketitle
\begin{abstract}
We study how people perceive risk and why aggregate models hide heterogeneity.
\end{abstract}
\end{document}
`;
const REQUEST = `I need you to, based on the literature review I am going to give you, write the introduction of my paper.

3. Why the methodological problem is hard
This section explains why the methodological problem is difficult before discussing how researchers have attempted to solve it.
4. How risk perception has traditionally been measured and modeled
Now move from theory to methods.
Synthesize the major approaches identified in your literature review rather than describing papers individually: psychometric scales, factor approaches, regression models, structural models, Bayesian approaches.
5. Risk perception is heterogeneous and context dependent
Your literature should establish that risk perception is not homogeneous across people, hazards or countries.
This leads to an important methodological implication: models based exclusively on aggregate relationships may obscure substantial heterogeneity.`;

(async () => {
  const file = `${home}/papers/traffic-gnn/main.tex`;
  fs.writeFileSync(file, EMPTY);
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 1100 } })).newPage();
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`console ${m.type()}: ${m.text()}`); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  await page.goto(url);
  await page.waitForSelector('.chat-page');
  await page.click('#paper-list .side-item:has-text("traffic-gnn")');
  await page.waitForFunction(() => document.querySelector('#paper-list [aria-current="true"]')?.innerText.includes('traffic-gnn'));
  await page.fill('#composer', REQUEST);
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant:has-text("A new section is added")', { timeout: 40000 });
  const card = await page.$eval('.thread', (e) => e.innerText);
  ok(!/has no methods section/.test(card), 'it does not answer that the paper has no methods section');
  ok(/as a new introduction section after the abstract/.test(card), 'it chose the introduction, to go after the abstract');
  ok(/\\section\{Introduction\}/.test(card), 'the card shows the heading that will be added');
  ok(/Risk perception is not homogeneous/.test(card), 'and the text written from the outline');
  await page.screenshot({ path: `${out}/new-section.png` });
  await page.click('button:has-text("Save in my files")');
  await page.waitForFunction(() => document.body.innerText.includes('Saved in'), null, { timeout: 20000 });
  const saved = fs.readFileSync(file, 'utf8');
  ok(/\\end\{abstract\}\n\n\\section\{Introduction\}\n\nRisk perception is not homogeneous/.test(saved), 'saving puts the heading and the text after the abstract, with blank lines around them');
  ok(/heterogeneity\.\n\n\\end\{document\}/.test(saved), 'and the document still closes after them');

  await browser.close();
  const real = errors.filter((e) => !/Failed to load resource/i.test(e));
  ok(real.length === 0, 'no console errors' + (real.length ? ': ' + real.join(' | ') : ''));
  console.log(failed ? `${failed} check(s) FAILED` : 'all checks passed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
