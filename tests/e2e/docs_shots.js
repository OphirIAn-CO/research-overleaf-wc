// The pictures in README.md and docs/guide.md, taken from the demo server (a made-up paper and a stand-in model, so nothing here is anyone's real work).
// usage: python3 tests/e2e/demo_server.py --port 8799 &      (prints the URL with the token and the HOME folder)
//        node tests/e2e/docs_shots.js <url-with-token> <home-folder> [output-dir]      (default output: docs/images)
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const path = require('path');
const url = process.argv[2];
const home = process.argv[3];
const out = process.argv[4] || path.join(__dirname, '..', '..', 'docs', 'images');
fs.mkdirSync(out, { recursive: true });

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1, reducedMotion: 'reduce' });
  const page = await ctx.newPage();
  await page.goto(url);
  await page.waitForSelector('.chat-page');
  const settle = (ms) => page.waitForTimeout(ms || 1500);
  const shot = async (name, el) => {
    await page.evaluate(() => document.querySelectorAll('.toast').forEach((t) => t.remove()));
    return el ? el.screenshot({ path: path.join(out, name) }) : page.screenshot({ path: path.join(out, name) });
  };

  // an answer: the explanation, then the changes side by side
  await page.fill('#composer', 'improve the flow of the text before Figure 1 in the methods');
  await page.press('#composer', 'Enter');
  await page.waitForSelector('.msg.assistant .approach', { timeout: 30000 });
  await settle();
  await shot('chat-answer.png');

  // Settings: the literature library and the folder with the code and results
  await page.setViewportSize({ width: 1400, height: 1700 });
  await page.click('#nav a:has-text("Settings")');
  const lib = 'section[aria-label="Literature library"]';
  await page.waitForSelector(lib);
  await page.fill(lib + ' input[aria-label="Folder with your books and articles"]', `${home}-library/Literature`);
  await page.click(lib + ' button:has-text("Add this folder")');
  await page.waitForSelector(lib + ' .folder-row:has-text("Literature")');
  await page.click(lib + ' button:has-text("Read the folders")');
  await page.waitForSelector(lib + ' .notice.ok:has-text("Done.")', { timeout: 60000 });
  await page.click(lib + ' summary:has-text("Documents")');
  await page.click(lib + ' summary:has-text("Try a search")');
  await page.fill(lib + ' input[aria-label="Search the library"]', 'sampled farms capacitance probes');
  await page.click(lib + ' button:has-text("Search")');
  await page.waitForSelector(lib + ' .hit');
  await settle(800);
  await shot('library.png', await page.$(lib));

  const an = 'section[aria-label="Code and results"]';
  await page.fill(an + ' input[aria-label="Folder with your code and results"]', `${home}-library/Analysis`);
  await page.click(an + ' button:has-text("Add this folder")');
  await page.waitForSelector(an + ' p:has-text("What the app can see")', { timeout: 30000 });
  await page.click(an + ' summary:has-text("The files")');
  await settle(800);
  await shot('code-and-results.png', await page.$(an));

  // new text written from the analysis
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.click('#nav a:has-text("Chat")');
  await page.waitForSelector('.chat-page');
  await page.click('#new-chat');
  await page.waitForSelector('.welcome');
  await settle(500);
  await page.fill('#composer', 'write the results from my analysis');
  await page.press('#composer', 'Enter');
  await page.waitForFunction(() => [...document.querySelectorAll('.msg.assistant')].some((m) => m.innerText.includes('What each statement rests on')), null, { timeout: 40000 });
  await settle(2000);
  await page.evaluate(() => { const t = document.querySelector('.thread'); if (t) t.scrollTop = t.scrollHeight; });
  await settle(500);
  await shot('draft.png');

  await browser.close();
  console.log('pictures written to', out);
})().catch((e) => { console.error('failed:', e); process.exit(1); });
