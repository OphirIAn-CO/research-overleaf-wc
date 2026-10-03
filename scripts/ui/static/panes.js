/* Side panes: QuillBot and Overleaf next to the chat. The built-in browser shows them inside the app (a live picture of a real browser that
   runs next to the app; the mouse and keyboard are passed on to it). Without it: a frame when the site allows it, otherwise a window beside
   the app. Nothing is loaded from a third-party site until a pane is opened. */
(function () {
  'use strict';
  const { h, clear, api, notice, busy, guarded, registerPage, toast, S } = window.OLS;

  const SANDBOX = 'allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads';

  /* Opens `url` in a window sized and placed on the right half of the screen (the app's window is expected on the left half). */
  function openBeside(url, name) {
    const sw = window.screen.availWidth, sh = window.screen.availHeight;
    const sl = window.screen.availLeft || 0, st = window.screen.availTop || 0;
    const w = Math.round(sw / 2);
    const features = `popup=yes,width=${w},height=${sh},left=${sl + sw - w},top=${st},noopener,noreferrer`;
    const win = window.open(url, name, features);
    if (!win && win !== null) toast('Your browser blocked the window. Allow pop-ups for this page and try again.');
    return win;
  }

  /* One pane: a header, and a frame that is only created when asked for. Returns {el, load, reload}. */
  function framePane(label, url, opts) {
    opts = opts || {};
    const body = h('div', { class: 'pane-body' });
    let frame = null;
    const load = () => {
      if (!url) { clear(body).append(notice('warn', 'There is no address for this pane yet.')); return; }
      frame = h('iframe', { class: 'pane-frame', src: url, title: label, sandbox: SANDBOX, referrerpolicy: 'no-referrer', allow: 'clipboard-write' });
      clear(body).append(frame);
      if (opts.onLoad) frame.addEventListener('load', opts.onLoad);
    };
    const reload = () => { if (frame) frame.src = url; else load(); };
    const el = h('section', { class: 'card pane', 'aria-label': label },
      h('div', { class: 'row' }, h('h2', { text: label }), h('span', { class: 'grow' }),
        h('button', { type: 'button', text: 'Show it here', onclick: load }),
        h('button', { type: 'button', text: 'Reload', onclick: reload }),
        h('button', { type: 'button', text: 'Open beside the app', onclick: () => openBeside(url, 'ols-' + label.toLowerCase()) , disabled: !url })),
      body);
    clear(body).append(h('p', { class: 'muted small', text: url ? 'Nothing is loaded until you press "Show it here".' : 'No address available.' }));
    return { el, load, reload };
  }

  // ------------------------------------------------------------------ the test page (step 0 of the new workspace)
  const REPORT = 'ols-panes-report';
  const answers = () => { try { return JSON.parse(window.OLS.store.get(REPORT) || '{}'); } catch (e) { return {}; } };

  registerPage('panes', 'Test panes', async (box) => {
    await window.OLS.loadState();
    if (!S.paper) { box.append(notice('warn', 'Register a paper first.')); return; }
    const info = await api.get('/api/panes?paper=' + encodeURIComponent(S.paper));
    const report = h('pre', { class: 'payload small', hidden: true });
    const probeOut = h('div');
    const rows = { server: null, clip: null };
    const refreshReport = () => {
      const a = answers();
      const p = rows.server;
      const line = (name, x) => `${name}: ${x ? (x.error ? 'not reachable from the app (' + x.error + ')' : `framing ${x.embeddable === true ? 'ALLOWED' : x.embeddable === false ? 'REFUSED' : 'unclear'} (${x.why})`) : 'not checked'}`;
      report.textContent = [
        'Pane test report', `Browser: ${navigator.userAgent}`,
        line('QuillBot (server check)', p && p.quillbot), line('Overleaf (server check)', p && p.overleaf),
        `QuillBot in the frame, what I see: ${a.quillbot || 'not answered'}`, `Overleaf in the frame, what I see: ${a.overleaf || 'not answered'}`,
        `Paste button: ${rows.clip || 'not tried'}`, `Window beside the app: ${a.window || 'not answered'}`].join('\n');
      report.hidden = false;
    };
    const ask = (key, label) => h('div', { class: 'row small' }, h('span', { class: 'secondary', text: label }),
      ['It shows the page', 'Blank or refused', 'Shows, but I cannot sign in'].map((t) => h('button', { type: 'button', class: 'small', text: t, onclick: () => {
        const a = answers(); a[key] = t; window.OLS.store.set(REPORT, JSON.stringify(a)); toast('Noted'); refreshReport();
      } })));

    const qb = framePane('QuillBot', info.quillbot.url);
    const ol = framePane('Overleaf', info.overleaf.url);
    box.append(h('div', { class: 'page-head' }, h('h1', { text: 'Test the side panes' }),
      h('span', { class: 'muted small', text: 'Checks, on your computer, whether QuillBot and Overleaf can sit next to the chat.' })),
      notice('ok', 'Some sites refuse to be shown inside another page, and Safari can stop the sign-in cookies of a framed page. This page finds out which of the three ways works for you: shown here, in a window beside the app, or only in a normal tab.'));

    box.append(h('section', { class: 'card', 'aria-label': 'Server check' }, h('h2', { text: '1. Ask the sites whether they allow framing' }),
      h('p', { class: 'small secondary', text: 'The app makes one plain request to each public page (no cookies, nothing about your paper) and reads the headers.' }),
      guarded(h('button', { type: 'button', class: 'primary', text: 'Check both sites' }), async () => {
        clear(probeOut).append(busy('Asking...'));
        const r = await api.post('/api/test/frames', { paper: S.paper });
        rows.server = r;
        const row = (n, x) => h('tr', null, h('td', { text: n }), h('td', { text: x.error ? 'no' : 'yes' }),
          h('td', null, x.error ? x.error : h('span', { class: 'badge ' + (x.embeddable ? 'ok' : 'bad'), text: x.embeddable === true ? 'allowed' : x.embeddable === false ? 'refused' : 'unclear' })),
          h('td', { class: 'small', text: x.error ? '' : x.why }));
        clear(probeOut).append(h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, ['Site', 'Reached', 'Framing', 'Why'].map((t) => h('th', { text: t })))),
          h('tbody', null, row('QuillBot', r.quillbot), row('Overleaf (sign-in page)', r.overleaf)))));
        refreshReport();
      }), probeOut));

    box.append(h('section', { class: 'card', 'aria-label': 'Frames' }, h('h2', { text: '2. Try them here, then tell me what you see' }),
      h('div', { class: 'panes-grid' }, qb.el, ol.el),
      ask('quillbot', 'QuillBot in the frame:'), ask('overleaf', 'Overleaf in the frame:'),
      h('p', { class: 'small muted', text: 'Signing in with Google cannot happen inside a frame (Google refuses). If you need it, use "Open beside the app" once, sign in there, then try the frame again.' })));

    const clip = h('textarea', { 'aria-label': 'Pasted text', placeholder: 'The pasted text will appear here', rows: '2' });
    box.append(h('section', { class: 'card', 'aria-label': 'Window and clipboard' }, h('h2', { text: '3. Window beside the app, and the paste button' }),
      h('p', { class: 'small secondary', text: 'Put this window on the left half of your screen (on a Mac: hold the green button and choose "Tile Window to Left of Screen"), then press the button. QuillBot should open on the right half.' }),
      h('div', { class: 'row' }, h('button', { type: 'button', text: 'Open QuillBot beside the app', onclick: () => openBeside(info.quillbot.url, 'ols-quillbot') }),
        info.overleaf.url ? h('button', { type: 'button', text: 'Open Overleaf beside the app', onclick: () => openBeside(info.overleaf.url, 'ols-overleaf') }) : null),
      ask('window', 'The window opened beside the app:'),
      h('p', { class: 'small secondary', text: 'Copy any text, then press this to see if the browser lets the app read your clipboard (it asks once).' }),
      clip, h('div', { class: 'row' }, h('button', { type: 'button', text: 'Paste what I copied', onclick: async () => {
        try { clip.value = await navigator.clipboard.readText(); rows.clip = 'works'; toast('Pasted'); } catch (e) { rows.clip = 'blocked (' + e.name + ')'; toast('The browser did not allow it: ' + e.name); }
        refreshReport();
      } }))));

    box.append(h('section', { class: 'card', 'aria-label': 'Report' }, h('h2', { text: '4. Copy this and paste it to Claude' }),
      h('div', { class: 'row' }, h('button', { type: 'button', class: 'primary', text: 'Show the report', onclick: refreshReport }),
        h('button', { type: 'button', text: 'Copy it', onclick: async () => { refreshReport(); try { await navigator.clipboard.writeText(report.textContent); toast('Copied'); } catch (e) { toast('Select the text and copy it by hand'); } } })),
      report));
  });

  // ------------------------------------------------------------------ the built-in browser
  const DESKTOP_W = 1100, NARROW_W = 720, MAX_W = 1920, MIN_H = 360, MAX_H = 1400;
  const NAMED = new Set(['Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown', 'Insert',
    'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12']);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  /* Copies text that is still being fetched. Safari only allows a copy inside the click, so the text is handed over as a promise. */
  async function copyAsync(promise, what) {
    try {
      if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
        await navigator.clipboard.write([new ClipboardItem({ 'text/plain': promise.then((t) => { if (!t) throw new Error('empty'); return new Blob([t], { type: 'text/plain' }); }) })]);
      } else {
        const t = await promise;
        if (!t) throw new Error('empty');
        await navigator.clipboard.writeText(t);
      }
      toast(what + ' copied');
    } catch (e) {
      toast(e && e.message === 'empty' ? 'Nothing is selected there. Select the text first.' : 'Your browser did not let the app copy. Select the text and copy it by hand.');
    }
  }

  /* One site in the built-in browser. d = {url, how, reason}. Returns {el, destroy, api}. */
  function browserPane(site, label, d) {
    const BIG = 'ols-panes-bigtext';
    const st = { alive: true, size: null, queue: [], flying: false, dragging: false, moveAt: 0, timers: [], poll: null, big: window.OLS.store.get(BIG) === '1', lat: [], pendingAt: 0 };
    const LAGKEY = 'ols-slow-ok-' + site;
    const post = (path, body) => api.post(`/api/browser/${site}/${path}`, body || {});
    const canvas = h('canvas', { class: 'bshot', role: 'img', 'aria-label': label + ' (live view of the built-in browser)' });
    const g = canvas.getContext('2d');
    const keys = h('textarea', { class: 'bkeys', 'aria-label': label + ': type here to type into the page', autocapitalize: 'off', autocomplete: 'off', spellcheck: 'false' });
    const overlay = h('div', { class: 'bmsg', role: 'status' });
    const view = h('div', { class: 'bview' }, canvas, keys, overlay);
    const where = h('span', { class: 'small muted ellipsis grow', title: '' });
    const line = h('div', { class: 'small secondary bline', 'aria-live': 'polite', hidden: true });
    const lag = h('div', { class: 'small bline warn', role: 'status', hidden: true });
    const check = h('div', { class: 'small bline warn challenge', role: 'status', hidden: true },
      h('strong', { text: label + ' is checking that you are human. ' }),
      'Click its checkbox in the picture below. If the check keeps coming back, your own browser will pass it: ',
      h('button', { type: 'button', class: 'small', text: `Open ${label} in a window instead`, onclick: () => window.OLS.panes.useWindow(site, d.url) }));
    const say = (kind, text) => { line.hidden = !text; line.className = 'small bline ' + (kind || 'secondary'); line.textContent = text || ''; };

    // ---- what the page looks like and where things are. The picture keeps its proportions (top-left aligned); clicks are mapped back through the same scale.
    const remote = () => (canvas.dataset.live ? { w: canvas.width, h: canvas.height } : st.size || { w: DESKTOP_W, h: 700 });
    const scale = () => { const r = view.getBoundingClientRect(), m = remote(); return Math.min(r.width / m.w, r.height / m.h) || 1; };
    const pt = (e) => { const r = view.getBoundingClientRect(), sc = scale(); return { x: Math.round((e.clientX - r.left) / sc), y: Math.round((e.clientY - r.top) / sc) }; };
    const measure = () => {
      const r = view.getBoundingClientRect();
      if (r.width < 50 || r.height < 50) return null;
      const w = Math.min(MAX_W, Math.max(r.width, st.big ? NARROW_W : DESKTOP_W));    // sites switch to a cramped phone layout below ~1000 px, so draw wide and shrink to fit (unless the author wants bigger text)
      return { w: Math.round(w), h: Math.round(Math.max(MIN_H, Math.min(MAX_H, r.height * w / r.width))) };
    };

    // ---- input goes to the page in order, one request at a time; mouse moves are thinned out
    async function pump() {
      if (st.flying || !st.queue.length || !st.alive) return;
      st.flying = true;
      const batch = st.queue.splice(0, 100);
      try { await post('input', { events: batch }); } catch (e) { problem(e); }
      st.flying = false;
      if (st.queue.length) pump();
    }
    const sameKeys = (a, b) => (a.mods || []).join() === (b.mods || []).join();
    function send(ev) {
      const last = st.queue[st.queue.length - 1];
      if (ev.t === 'move' && last && last.t === 'move') st.queue[st.queue.length - 1] = ev;
      else if (ev.t === 'wheel' && last && last.t === 'wheel' && sameKeys(last, ev) && Math.abs(last.x - ev.x) <= 12 && Math.abs(last.y - ev.y) <= 12) { last.dx += ev.dx; last.dy += ev.dy; }   // notches that wait their turn become one scroll
      else st.queue.push(ev);
      if (!st.pendingAt && (ev.t === 'wheel' || ev.t === 'key' || ev.t === 'type')) st.pendingAt = performance.now();           // how long until the page shows what was asked of it
      pump();
    }
    /* How long a scroll or a key takes to show. When it is slow again and again the pane says so and offers the way that is always smooth: a window of the author's own browser. */
    function showLag(ms) {
      clear(lag).append(h('strong', { text: `${label} is slow here (about ${(ms / 1000).toFixed(1)} s for a scroll to show). ` }),
        'This view is a live picture of a browser running inside Docker, so it can only be as fast as that browser on this computer. A window of your own browser beside the app is much smoother: ',
        h('button', { type: 'button', class: 'small primary', text: `Open ${label} in a window beside the app`, onclick: () => window.OLS.panes.useWindow(site, d.url) }), ' ',
        h('button', { type: 'button', class: 'small', text: 'Keep it here', onclick: () => { lag.hidden = true; st.lagOff = true; window.OLS.store.set(LAGKEY, String(Date.now())); } }));
      lag.hidden = false;
    }
    function noteFrame() {
      if (!st.pendingAt) return;
      const ms = performance.now() - st.pendingAt;
      st.pendingAt = 0;
      if (ms > 15000) return;
      st.lat.push(ms);
      if (st.lat.length > 7) st.lat.shift();
      if (st.lat.length < 5 || st.lagOff) return;
      const med = [...st.lat].sort((a, b) => a - b)[Math.floor(st.lat.length / 2)];
      if (med > 700) showLag(med); else if (med < 350) lag.hidden = true;
    }
    if (Date.now() - Number(window.OLS.store.get(LAGKEY) || 0) < 86400000) st.lagOff = true;       // "keep it here" is remembered for a day
    const idle = async () => { for (let i = 0; i < 100 && (st.flying || st.queue.length); i++) await sleep(20); };
    const mods = (e) => [e.shiftKey && 'Shift', (e.ctrlKey || e.metaKey) && 'Control', e.altKey && 'Alt'].filter(Boolean);
    const button = (e) => (e.button === 1 ? 1 : e.button === 2 ? 2 : 0);

    function moved(e) {
      const now = performance.now();
      const go = () => { st.moveAt = performance.now(); send(Object.assign({ t: 'move', mods: mods(e) }, pt(e))); };
      if (now - st.moveAt >= 40) go(); else { clearTimeout(st.moveTimer); st.moveTimer = setTimeout(go, 40); }
    }
    view.addEventListener('mousedown', (e) => {
      e.preventDefault();
      keys.focus({ preventScroll: true });
      send(Object.assign({ t: 'down', b: button(e), n: Math.min(e.detail || 1, 3), mods: mods(e) }, pt(e)));
      st.dragging = true;
      const up = (ev) => {
        st.dragging = false;
        window.removeEventListener('mouseup', up); window.removeEventListener('mousemove', moved);
        send(Object.assign({ t: 'up', b: button(ev), n: Math.min(ev.detail || 1, 3), mods: mods(ev) }, pt(ev)));
      };
      window.addEventListener('mouseup', up); window.addEventListener('mousemove', moved);
    });
    view.addEventListener('mousemove', (e) => { if (!st.dragging) moved(e); });
    view.addEventListener('contextmenu', (e) => e.preventDefault());
    view.addEventListener('wheel', (e) => {
      e.preventDefault();
      const k = (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1) / scale();
      send(Object.assign({ t: 'wheel', dx: Math.round(e.deltaX * k), dy: Math.round(e.deltaY * k), mods: mods(e) }, pt(e)));
    }, { passive: false });

    keys.addEventListener('keydown', (e) => {
      if (e.isComposing || e.key === 'Process' || e.key === 'Dead' || e.key === 'Unidentified') return;
      const mod = e.ctrlKey || e.metaKey, k = e.key;
      if (['Shift', 'Control', 'Alt', 'Meta', 'CapsLock', 'OS', 'AltGraph'].includes(k)) return;
      if (mod && /^[vV]$/.test(k)) return;                                  // the paste event below carries the text
      if (mod && /^[cCxX]$/.test(k)) { e.preventDefault(); copyOut(/^[xX]$/.test(k)); return; }
      if (!mod && k.length === 1) { e.preventDefault(); send({ t: 'type', text: k }); return; }
      if (NAMED.has(k) || (mod && k.length === 1)) { e.preventDefault(); send({ t: 'key', key: k.length === 1 ? k.toLowerCase() : k, mods: mods(e) }); }
    });
    keys.addEventListener('paste', (e) => {
      e.preventDefault();
      const t = e.clipboardData && e.clipboardData.getData('text/plain');
      if (t) send({ t: 'text', text: t }); else toast('Nothing to paste: copy some text first.');
    });
    const typed = () => { if (keys.value) send({ t: 'text', text: keys.value }); keys.value = ''; };   // accents, dead keys and other input methods
    keys.addEventListener('compositionend', () => setTimeout(typed, 0));
    keys.addEventListener('input', (e) => { if (!e.isComposing) typed(); });

    async function selectedText() { await idle(); return (await post('selection')).text; }
    async function copiedText() { await idle(); return (await post('clipboard')).text; }
    async function copyOut(cut) {
      await copyAsync(selectedText(), 'What you selected in ' + label);
      if (cut) send({ t: 'key', key: 'x', mods: ['Control'] });
    }

    // ---- the picture: ask for the newest one, wait (up to 8 s) until it changes, draw it, ask again
    async function pictures(run) {
      let seq = -1, failures = 0;
      while (st.alive && st.run === run) {
        try {
          const r = await fetch(`/api/browser/${site}/frame?after=${seq}&wait=8`, { credentials: 'same-origin', headers: { 'X-Requested-With': 'ols', 'X-Poll': '1' } });
          if (!st.alive || st.run !== run) return;
          if (r.status === 204) { failures = 0; continue; }
          if (!r.ok) { let m = 'The picture stopped. The built-in browser may have been closed.'; try { m = (await r.json()).error || m; } catch (e) { /* keep the generic text */ } throw new Error(m); }
          seq = Number(r.headers.get('X-Seq'));
          const bmp = await createImageBitmap(await r.blob());
          if (!st.alive || st.run !== run) { bmp.close(); return; }
          if (canvas.width !== bmp.width || canvas.height !== bmp.height) { canvas.width = bmp.width; canvas.height = bmp.height; }
          g.drawImage(bmp, 0, 0);
          bmp.close();
          noteFrame();
          if (!canvas.dataset.live) { canvas.dataset.live = '1'; show(null); }
          failures = 0;
          if (Date.now() - (st.fitAt || 0) > 1500) { st.fitAt = Date.now(); fit(); }    // a picture that does not match the pane is repaired by itself
        } catch (e) {
          if (!st.alive || st.run !== run) return;
          if (++failures >= 3) { problem(e); return; }
          await sleep(700);
        }
      }
    }
    const startStream = () => { st.run = (st.run || 0) + 1; pictures(st.run); };
    const stopStream = () => { st.run = (st.run || 0) + 1; delete canvas.dataset.live; };

    function show(text, ...actions) {
      clear(overlay);
      if (text == null) { overlay.hidden = true; return; }
      overlay.hidden = false;
      overlay.append(h('p', { class: 'small secondary', text }), actions.length ? h('div', { class: 'row' }, actions) : null);
    }
    function problem(e) {
      stopStream();
      show((e && e.message) || 'The built-in browser stopped.', h('button', { type: 'button', class: 'primary', text: 'Try again', onclick: () => begin() }), h('a', { class: 'small', href: '#/settings', text: 'Settings' }));
    }
    async function begin() {
      st.started = false;
      show('Starting the browser. The first time takes a few seconds.');
      const sz = measure() || { w: DESKTOP_W, h: 700 };
      try {
        const r = await post('open', { paper: S.paper, w: sz.w, h: sz.h });
        st.size = { w: r.w, h: r.h };
      } catch (e) { problem(e); return; }
      if (!st.alive) return;
      st.started = true;
      await fit();              // the pane may have settled into its final size while the browser was starting
      startStream();            // the first picture takes the "starting" message away
      watch();
    }
    async function watch() {
      clearTimeout(st.poll);
      if (!st.alive) return;
      try {
        const s = (await api.get('/api/browser', { poll: true })).sites[site] || {};
        if (!st.alive) return;
        if (s.running === false && st.started) { st.started = false; stopStream(); show('The browser was closed because nobody used it for a while. Your sign-in is kept.', h('button', { type: 'button', class: 'primary', text: 'Open it again', onclick: () => begin() })); }
        where.textContent = s.title ? `${s.title}` : (s.url || '');
        where.title = s.url || '';
        say('warn', s.notice || '');
        check.hidden = !s.challenge;
      } catch (e) { /* the next round tries again */ }
      st.poll = setTimeout(watch, 2500);
    }

    // ---- size follows the pane
    async function fit() {
      if (!st.alive || !st.started || st.fitting) return;
      const sz = measure();
      const cur = canvas.dataset.live ? { w: canvas.width, h: canvas.height } : st.size || {};
      if (!sz || (Math.abs(sz.w - cur.w) < 8 && Math.abs(sz.h - cur.h) < 8)) return;
      st.fitting = true;
      try { const r = await post('resize', sz); st.size = { w: r.w, h: r.h }; } catch (e) { /* shown by the next picture */ }
      st.fitting = false;
    }
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { clearTimeout(st.rsz); st.rsz = setTimeout(fit, 250); }) : null;
    if (ro) ro.observe(view);

    // ---- the toolbar
    const nav = (action) => post('nav', Object.assign({ action }, action === 'home' ? { paper: S.paper } : {})).catch((e) => { toast(e.message); });
    const tool = (text, title, fn) => h('button', { type: 'button', class: 'small tool', text, title, 'aria-label': title, onclick: fn });
    const bigText = h('button', { type: 'button', class: 'small tool', text: 'Aa', 'aria-pressed': String(st.big), title: st.big ? 'Smaller text, the page keeps its wide layout' : 'Bigger text (the site may switch to its narrow layout)', 'aria-label': 'Bigger text',
      onclick: async () => { st.big = !st.big; window.OLS.store.set(BIG, st.big ? '1' : '0'); bigText.setAttribute('aria-pressed', String(st.big)); bigText.title = st.big ? 'Smaller text, the page keeps its wide layout' : 'Bigger text (the site may switch to its narrow layout)'; await fit(); } });
    const signin = guarded(h('button', { type: 'button', class: 'small', text: 'Fill my sign-in', title: 'Fills the email and password you saved in Settings. You press the site\'s own Sign in button.' }), async () => {
      keys.focus({ preventScroll: true });
      try { say('ok', (await post('signin')).message); } catch (e) { say('warn', e.message); }
    });
    const el = h('section', { class: 'card pane fill bpane', 'aria-label': label },
      h('div', { class: 'row tight pane-head' }, h('h2', { text: label }), where,
        tool('←', 'Back', () => nav('back')), tool('→', 'Forward', () => nav('forward')), tool('↻', 'Reload', () => nav('reload')), tool('✕', 'Stop loading', () => nav('stop')), tool('Home', 'Back to the start page', () => nav('home')),
        bigText,
        tool('↗', `Open ${label} in a window of your own browser beside the app (always smooth; you can switch back in Settings)`, () => window.OLS.panes.useWindow(site, d.url)),
        h('a', { class: 'small', href: '#/settings', text: 'Settings' })),
      line, check, lag, view,
      h('div', { class: 'row tight pane-foot' }, signin,
        guarded(h('button', { type: 'button', class: 'small', text: 'Copy what I selected', title: 'Copies the text selected in ' + label + ' to your clipboard' }), () => copyOut(false)),
        h('span', { class: 'small muted grow', text: 'Click inside and type as usual. Cmd/Ctrl+V pastes your clipboard.' })));

    const ready = begin();
    return {
      el, ready,
      destroy() { st.alive = false; clearTimeout(st.poll); clearTimeout(st.moveTimer); clearTimeout(st.rsz); if (ro) ro.disconnect(); stopStream(); },
      api: {
        ready,
        selection: selectedText, clipboard: copiedText,
        async insert(text) { await ready; await idle(); const r = await post('insert', { text }); keys.focus({ preventScroll: true }); return r; },
        reload: () => nav('reload'),
      },
    };
  }

  // ------------------------------------------------------------------ the panes beside the chat
  const enc = encodeURIComponent;
  const KEY = 'ols-panes';
  const TITLES = { quillbot: 'QuillBot', overleaf: 'Overleaf', text: 'Paper text' };
  const A = { enabled: [], layout: 'tabs', wide: false, width: 0, ids: [], prev: {}, urls: {}, mounted: {}, bars: [], sig: '' };
  try {
    A.enabled = JSON.parse(window.OLS.store.get(KEY) || '[]').filter((k) => TITLES[k]);
    A.width = Number(window.OLS.store.get(KEY + '-w')) || 0;
    A.layout = window.OLS.store.get(KEY + '-layout') === 'stack' ? 'stack' : 'tabs';
    A.wide = window.OLS.store.get(KEY + '-wide') === '1';
  } catch (e) { A.enabled = []; }
  if (A.layout === 'tabs') A.enabled = A.enabled.slice(0, 1);

  const area = () => document.getElementById('panes-area');
  const save = () => {
    window.OLS.store.set(KEY, JSON.stringify(A.enabled)); window.OLS.store.set(KEY + '-layout', A.layout);
    window.OLS.store.set(KEY + '-wide', A.wide ? '1' : '0');
  };

  async function addressesFor(paper) {
    // how each pane is shown, and where it lives; asks the sites about framing only when the built-in browser is not there
    if (A.urls.paper !== paper || Date.now() - (A.urls.at || 0) > 120000) A.urls = Object.assign({ paper, at: Date.now() }, await api.get('/api/panes?check=1&paper=' + enc(paper)));
    return A.urls;
  }
  const howOf = (d) => (d && d.how) || (d && d.inside ? 'frame' : 'window');

  /* A site that cannot sit inside the app: one clear button that opens it in a window on the free half of the screen. */
  function windowPane(label, d) {
    const key = label.toLowerCase();
    return h('section', { class: 'card pane fill', 'aria-label': label },
      h('div', { class: 'row' }, h('h2', { text: label }), h('span', { class: 'grow' }), h('a', { class: 'small', href: '#/settings', text: 'Settings' })),
      d.url ? h('div', { class: 'stack' },
        h('p', { class: 'small secondary', text: d.reason || 'It opens in a window next to the app.' }),
        h('button', { type: 'button', class: 'primary', text: `Open ${label} beside the app`, onclick: () => openBeside(d.url, 'ols-' + key) }),
        h('ol', { class: 'small secondary' },
          h('li', { text: 'It opens in its own window on the right half of your screen.' }),
          h('li', { text: 'Sign in there once; the app never sees your ' + label + ' password.' }),
          h('li', { text: 'Put this app on the left half (on a Mac: hold the green button, then choose "Tile Window to Left of Screen").' }),
          label === 'QuillBot' ? h('li', { text: 'Use "Copy for QuillBot" under a suggestion, paste it there, then bring the result back with "Paste what I copied".' }) : h('li', { text: 'After "Send to Overleaf", reload that window to see the update.' })),
        h('p', { class: 'small muted', text: d.url })) :
        notice('warn', d.reason || 'There is no address yet.', ' ', h('a', { href: '#/settings', text: 'Open Settings' })));
  }

  function stripRange(id) { return id.replace(/@\d+-\d+$/, ''); }

  async function loadText(box, selectEl) {
    if (!S.paper) return;
    const ids = A.manual || A.ids;
    if (!ids.length) { clear(box).append(h('p', { class: 'muted small', text: 'Ask for a change and the part of your paper I am working on shows here. You can also pick a section above.' })); return; }
    const r = await api.get(`/api/paper/${enc(S.paper)}/text?ids=${enc(ids.join(','))}`);
    const frag = [];
    for (const piece of r.pieces) {
      const lines = piece.text.split('\n');
      const before = A.prev[piece.id] ? new Set(A.prev[piece.id]) : null;
      frag.push(h('h4', { class: 'small', text: `${piece.section.split(' > ').slice(-1)[0]} · ${piece.file} lines ${piece.lines[0]} to ${piece.lines[1]}` }));
      frag.push(h('div', { class: 'ptext mono' }, lines.map((l, i) => h('div', { class: 'ln' + (before && !before.has(l) && l.trim() ? ' changed' : '') },
        h('span', { class: 'no', 'aria-hidden': 'true', text: String(piece.lines[0] + i) }), h('span', { class: 'tx', text: l || ' ' })))));
      A.prev[piece.id] = lines;
    }
    clear(box).append(...frag);
  }

  async function mountText(host) {
    const box = h('div', { class: 'pane-scroll' });
    const sel = h('select', { 'aria-label': 'Show' }, h('option', { value: '', text: 'What I am working on' }));
    let sections = [];
    try { sections = (await api.get(`/api/paper/${enc(S.paper)}/sections`)).sections; } catch (e) { sections = []; }
    sections.forEach((x, i) => sel.append(h('option', { value: String(i), text: x.label })));
    sel.addEventListener('change', () => { A.manual = sel.value === '' ? null : sections[Number(sel.value)].chunk_ids; A.prev = {}; loadText(box).catch((e) => toast(e.message)); });
    host.append(h('div', { class: 'row' }, h('h2', { text: 'Paper text' }), h('span', { class: 'grow' }), sel,
      h('button', { type: 'button', text: 'Refresh', onclick: () => loadText(box).catch((e) => toast(e.message)) })), box);
    A.textBox = box;
    await loadText(box);
  }

  function unmountAll() {
    for (const m of Object.values(A.mounted)) { try { m.destroy(); } catch (e) { /* already gone */ } }
    A.mounted = {};
  }

  async function renderArea(force) {
    const host = area();
    if (!host) return;
    const on = A.enabled.length > 0 && document.body.dataset.page === 'chat' && !!S.paper;
    const sig = JSON.stringify([on, A.enabled, A.layout, A.wide, S.paper]);
    document.body.dataset.panes = on ? '1' : '0';
    if (!on) { unmountAll(); clear(host); host.hidden = true; A.sig = ''; A.urls = {}; A.textBox = null; return; }
    if (!force && sig === A.sig && host.firstChild) return;     // the same panes are already showing: leave the live views alone
    A.sig = sig;
    unmountAll();
    clear(host);
    A.textBox = null;
    host.hidden = false;
    host.dataset.layout = A.layout;
    if (A.width) host.style.setProperty('--panes-w', A.width + 'px');
    else host.style.setProperty('--panes-w', A.wide ? '62vw' : '46vw');
    const grip = h('div', { class: 'grip', role: 'separator', 'aria-orientation': 'vertical', title: 'Drag to resize' });
    grip.addEventListener('pointerdown', (ev) => {
      ev.preventDefault();
      const move = (e) => { A.width = Math.max(300, Math.min(window.innerWidth * 0.8, window.innerWidth - e.clientX)); host.style.setProperty('--panes-w', A.width + 'px'); };
      const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.OLS.store.set(KEY + '-w', String(Math.round(A.width))); };
      window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
    });
    host.append(grip, strip());
    const info = await addressesFor(S.paper).catch(() => ({}));
    if (A.sig !== sig) return;                                  // the author switched again while this was loading
    for (const key of A.enabled) {
      if (key === 'text') { const card = h('div', { class: 'card pane fill' }); host.append(card); await mountText(card); continue; }
      const d = info[key] || { url: null, how: 'window', reason: 'Could not read the settings.' };
      const how = howOf(d);
      if (how === 'browser') {
        const pane = browserPane(key, TITLES[key], d);
        A.mounted[key] = pane;
        host.append(pane.el);
      } else if (how === 'frame') {
        const pane = framePane(TITLES[key], d.url);
        pane.el.classList.add('fill');
        host.append(pane.el);
        pane.load();
        pane.el.append(h('p', { class: 'small muted', text: 'If it stays blank or will not sign in, use "Open beside the app" (or choose "Always a separate window" in Settings).' }));
      } else host.append(windowPane(TITLES[key], d));
    }
  }

  const redrawBars = () => { A.bars = A.bars.filter((b) => b.el.isConnected); A.bars.forEach((b) => b.draw()); };
  function setEnabled(list) { A.enabled = A.layout === 'tabs' ? list.slice(0, 1) : list; save(); redrawBars(); return renderArea(); }

  /* The three switches in the chat header. One pane at a time uses the whole side area; the strip at the top of the area switches to "both at once". */
  function bar() {
    const el = h('div', { class: 'row tight pane-bar', role: 'group', 'aria-label': 'Side panes' });
    const draw = () => {
      clear(el).append(h('span', { class: 'small muted', text: 'Beside the chat:' }), ...Object.entries(TITLES).map(([k, label]) =>
        h('button', { type: 'button', class: 'small', 'aria-pressed': String(A.enabled.includes(k)), text: label, onclick: () => {
          if (A.layout === 'tabs') setEnabled(A.enabled.length === 1 && A.enabled[0] === k ? [] : [k]);
          else setEnabled(A.enabled.includes(k) ? A.enabled.filter((x) => x !== k) : [...A.enabled, k]);
        } })));
    };
    A.bars.push({ el, draw });
    draw();
    return el;
  }

  /* The strip at the top of the side area: how the panes are laid out, how wide the area is, and a way to close it. */
  function strip() {
    const toggle = (text, title, on, fn) => h('button', { type: 'button', class: 'small', 'aria-pressed': String(on), text, title, onclick: fn });
    return h('div', { class: 'pane-strip', role: 'group', 'aria-label': 'Side area' },
      h('span', { class: 'small muted grow', text: A.layout === 'stack' ? 'Both panes, one above the other' : 'One pane at a time' }),
      toggle('Both at once', 'Show QuillBot and Overleaf one above the other', A.layout === 'stack', () => {
        A.layout = A.layout === 'stack' ? 'tabs' : 'stack';
        if (A.layout === 'tabs') A.enabled = A.enabled.slice(0, 1);
        else if (A.enabled.length < 2) { const other = ['quillbot', 'overleaf'].find((k) => !A.enabled.includes(k)); if (other) A.enabled = [...A.enabled, other]; }
        save(); redrawBars(); renderArea(true);
      }),
      toggle('Wider', 'Give the side area more room', A.wide, () => { A.wide = !A.wide; A.width = 0; window.OLS.store.set(KEY + '-w', '0'); save(); renderArea(true); }),
      h('button', { type: 'button', class: 'small', text: 'Close', title: 'Close the side area', onclick: () => setEnabled([]) }));
  }

  function setTargetFromThread(messages) {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      const ids = m.role === 'assistant' && m.data && m.data.chunk_ids;
      if (ids && ids.length) {
        const next = [...new Set(ids.map(stripRange))];
        if (JSON.stringify(next) !== JSON.stringify(A.ids)) { A.ids = next; A.prev = {}; if (A.textBox && !A.manual) loadText(A.textBox).catch(() => {}); }
        return;
      }
    }
  }
  function refreshText(afterApply) { if (A.textBox) loadText(A.textBox).catch(() => {}); }
  function onRoute(page) { document.body.dataset.page = page; renderArea(); }

  // ---- what the chat can do with the built-in browser
  const has = (site) => howOf(A.urls[site]) === 'browser';
  async function use(site) {          // shows the pane (if it is not showing) and waits until its browser is open
    if (!has(site)) throw new Error('The built-in browser is not available. See Settings.');
    if (!A.enabled.includes(site)) await setEnabled(A.layout === 'tabs' ? [site] : [...A.enabled, site]);
    const m = A.mounted[site];
    if (!m) throw new Error(TITLES[site] + ' is not showing yet. Try again in a moment.');
    await m.api.ready;
    return m.api;
  }
  function show(site) { if (has(site) && !A.enabled.includes(site)) setEnabled(A.layout === 'tabs' ? [site] : [...A.enabled, site]); }     // opens the pane beside the chat
  async function sendText(site, text) { const a = await use(site); await a.insert(text); }
  async function takeFrom(site, what) { const a = await use(site); return what === 'clipboard' ? (await a.clipboard()) : (await a.selection()); }
  /* Sends one site to a window of the author's own browser (it passes the human checks a built-in browser may not): remembers it in Settings. */
  async function useWindow(site, url) {
    try { await api.post('/api/settings', { ui: { ['builtin_' + site]: false } }); } catch (e) { toast(e.message); return; }
    A.urls = {};
    if (url) openBeside(url, 'ols-' + site);
    await renderArea(true);
    toast(TITLES[site] + ' now opens in a window beside the app. You can change this in Settings.');
  }
  /* After something was sent. The server has already reloaded the paper's Overleaf page in the built-in browser (and pressed Recompile), even when its tab is not showing;
     only when it could not tell is the visible pane reloaded from here. A window of the author's own browser cannot be reloaded from the app. */
  async function afterSend(r) {
    if (r && r.pane && r.pane.reloading) return;
    const m = A.mounted.overleaf;
    if (m) { try { await m.api.reload(); } catch (e) { /* it will show on the next reload */ } }
  }
  async function knows(paper) { try { await addressesFor(paper || S.paper); } catch (e) { /* no panes known */ } return has('quillbot'); }

  window.OLS.panes = { openBeside, framePane, bar, onRoute, setTargetFromThread, refreshText, has, sendText, takeFrom, afterSend, knows, copyAsync, useWindow, show };

}());
