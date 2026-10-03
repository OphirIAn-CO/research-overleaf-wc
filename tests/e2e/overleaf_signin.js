// The Overleaf "Test" in Settings, in a real browser: a password written into the paper's address is found, named, removed with one button, and never shown.
// usage: python3 tests/e2e/demo_server.py --port 8796 --no-book &      (prints the URL with the token and the HOME folder)
//        node tests/e2e/overleaf_signin.js <url-with-token> <home-folder> [screenshot-dir]
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const { execFileSync } = require('child_process');
const url = process.argv[2];
const home = process.argv[3];
const out = process.argv[4] || '/tmp/claude-0/scratch/shots';
fs.mkdirSync(out, { recursive: true });
let failed = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failed++; };
const SECRET = 'olp_OLDOLDOLDOLD';

(async () => {
  // the way a clone made with a token in the address looks, and a token saved in Settings
  const reg = JSON.parse(fs.readFileSync(`${home}/projects.json`, 'utf8'));
  const name = Object.keys(reg.projects)[0];
  const dir = reg.projects[name].path;
  const clean = execFileSync('git', ['-C', dir, 'remote', 'get-url', 'origin']).toString().trim();
  const dirty = clean.replace('https://', `https://git:${SECRET}@`);
  execFileSync('git', ['-C', dir, 'remote', 'set-url', 'origin', dirty]);
  reg.projects[name].remote = dirty;
  fs.writeFileSync(`${home}/projects.json`, JSON.stringify(reg));
  fs.writeFileSync(`${home}/overleaf_git.json`, JSON.stringify({ token: 'olp_NEWNEWNEWNEWNEW' }), { mode: 0o600 });

  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(url);
  await page.waitForSelector('.chat-page');
  await page.click('#nav a:has-text("Settings")');
  const card = 'section[aria-label="Overleaf"]';
  await page.waitForSelector(card);
  await page.click(card + ' button:has-text("Test with")');
  await page.waitForSelector(card + ' .notice.bad');
  const first = await page.$eval(card, (e) => e.innerText);
  ok(/password \(or token\) written into it/.test(first), 'the test says the address has a password inside');
  ok(/never looks at the token saved in Settings/.test(first), 'and that this is why a new token cannot help');
  ok(!first.includes(SECRET), 'the old password is not shown');
  ok(await page.isVisible(card + ' button:has-text("Remove it from the address")'), 'a button offers to remove it');
  await (await page.$(card)).screenshot({ path: `${out}/overleaf-signin-before.png` });

  await page.click(card + ' button:has-text("Remove it from the address")');
  await page.waitForFunction((c) => !document.querySelector(c + ' button').textContent.includes('Remove it') && !/password \(or token\)/.test(document.querySelector(c).innerText), card);
  const second = await page.$eval(card, (e) => e.innerText);
  ok(!/Remove it from the address/.test(second) && !/written into it/.test(second), 'after the button the address problem is gone: ' + (/[^\n]*(never sent|refused|accepted)[^\n]*/.exec(second) || [''])[0]);
  ok(!second.includes(SECRET), 'and the old password is still not shown');
  ok(execFileSync('git', ['-C', dir, 'remote', 'get-url', 'origin']).toString().trim() === clean, 'the paper\'s address is back to the plain Overleaf address');
  ok(!fs.readFileSync(`${home}/projects.json`, 'utf8').includes(SECRET), 'and the old password is gone from the app\'s own list too');
  await (await page.$(card)).screenshot({ path: `${out}/overleaf-signin-after.png` });

  await browser.close();
  const real = errors.filter((e) => !/Failed to load resource/i.test(e));
  ok(real.length === 0, 'no console errors' + (real.length ? ': ' + real.join(' | ') : ''));
  console.log(failed ? `${failed} check(s) FAILED` : 'all checks passed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('driver failed:', e); process.exit(2); });
