/* Side panes: QuillBot and Overleaf next to the app. Tries a frame first; "Open beside the app" opens a real window on the free half
   of the screen when a site refuses to be framed. Nothing is loaded from a third-party site until you ask for it. */
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

    const qb = framePane('QuillBot', info.quillbot);
    const ol = framePane('Overleaf', info.overleaf);
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
      h('div', { class: 'row' }, h('button', { type: 'button', text: 'Open QuillBot beside the app', onclick: () => openBeside(info.quillbot, 'ols-quillbot') }),
        info.overleaf ? h('button', { type: 'button', text: 'Open Overleaf beside the app', onclick: () => openBeside(info.overleaf, 'ols-overleaf') }) : null),
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

  // ------------------------------------------------------------------ the panes beside the chat
  const enc = encodeURIComponent;
  const KEY = 'ols-panes';
  const TITLES = { quillbot: 'QuillBot', overleaf: 'Overleaf', text: 'Paper text' };
  const A = { enabled: [], width: 0, ids: [], prev: {}, urls: {}, mounted: {} };
  try { A.enabled = JSON.parse(window.OLS.store.get(KEY) || '[]').filter((k) => TITLES[k]); A.width = Number(window.OLS.store.get(KEY + '-w')) || 0; } catch (e) { A.enabled = []; }

  const area = () => document.getElementById('panes-area');
  const save = () => { window.OLS.store.set(KEY, JSON.stringify(A.enabled)); };

  async function addressesFor(paper) {
    if (A.urls.paper !== paper) A.urls = Object.assign({ paper }, await api.get('/api/panes?paper=' + enc(paper)));
    return A.urls;
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

  async function renderArea() {
    const host = area();
    if (!host) return;
    clear(host);
    A.textBox = null;
    const on = A.enabled.length > 0 && document.body.dataset.page === 'chat' && !!S.paper;
    host.hidden = !on;
    document.body.dataset.panes = on ? '1' : '0';
    if (!on) return;
    if (A.width) host.style.setProperty('--panes-w', A.width + 'px');
    const grip = h('div', { class: 'grip', role: 'separator', 'aria-orientation': 'vertical', title: 'Drag to resize' });
    grip.addEventListener('pointerdown', (ev) => {
      ev.preventDefault();
      const move = (e) => { A.width = Math.max(300, Math.min(window.innerWidth * 0.7, window.innerWidth - e.clientX)); host.style.setProperty('--panes-w', A.width + 'px'); };
      const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.OLS.store.set(KEY + '-w', String(Math.round(A.width))); };
      window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
    });
    host.append(grip);
    const urls = await addressesFor(S.paper).catch(() => ({}));
    for (const key of A.enabled) {
      const card = h('div', { class: 'card pane fill' });
      host.append(card);
      if (key === 'text') { await mountText(card); continue; }
      const pane = framePane(TITLES[key], urls[key]);
      card.replaceWith(pane.el); pane.el.classList.add('fill');
      if (urls[key]) pane.load();
      if (key === 'quillbot') pane.el.append(h('p', { class: 'small muted', text: 'Only what you paste here goes to QuillBot. If it stays blank or will not sign in, use "Open beside the app".' }));
    }
  }

  /* The three switches shown in the chat header. */
  function bar() {
    const el = h('div', { class: 'row tight pane-bar', role: 'group', 'aria-label': 'Side panes' });
    const draw = () => {
      clear(el).append(h('span', { class: 'small muted', text: 'Beside the chat:' }), ...Object.entries(TITLES).map(([k, label]) =>
        h('button', { type: 'button', class: 'small', 'aria-pressed': String(A.enabled.includes(k)), text: label, onclick: () => {
          A.enabled = A.enabled.includes(k) ? A.enabled.filter((x) => x !== k) : [...A.enabled, k]; save(); draw(); renderArea();
        } })));
    };
    draw();
    return el;
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

  window.OLS.panes = { openBeside, framePane, bar, onRoute, setTargetFromThread, refreshText };

}());
