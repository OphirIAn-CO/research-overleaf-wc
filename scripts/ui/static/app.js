/* Overleaf skill web app: core, router, Papers / Write / Book / Settings. Vanilla JS, no dependencies.
   Every piece of manuscript, book or model text goes in through textContent / text nodes, never innerHTML. */
(function () {
  'use strict';

  // ------------------------------------------------------------------ helpers
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, v);
    }
    for (const c of kids.flat(Infinity)) {
      if (c == null || c === false) continue;
      el.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
  const fmtTok = (n) => (n == null ? '-' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e4 ? Math.round(n / 1000) + 'k' : n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(Math.round(n)));
  const ago = (ts) => {
    if (!ts) return 'never';
    const s = Math.max(0, Date.now() / 1000 - ts);
    return s < 90 ? 'just now' : s < 5400 ? Math.round(s / 60) + ' min ago' : s < 129600 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' days ago';
  };
  const store = {
    get(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* private window: fine */ } },
  };

  /* What the author has typed and not sent is kept while they look at another page: the chat page is built again every time it is opened. It lives in memory and in the tab's
     sessionStorage (so a reload keeps it too, and closing the tab forgets it). Nothing secret goes through here: never bind a password or token field. */
  const drafts = (() => {
    const mem = new Map();
    const K = (k) => 'ols-draft:' + k;
    const MAX = 200000;
    const get = (k) => {
      if (mem.has(k)) return mem.get(k);
      try { const v = window.sessionStorage.getItem(K(k)); if (v != null) { mem.set(k, v); return v; } } catch (e) { /* storage blocked: memory only */ }
      return '';
    };
    const set = (k, v) => {
      v = String(v || '').slice(0, MAX);
      if (v) mem.set(k, v); else mem.delete(k);
      try { if (v) window.sessionStorage.setItem(K(k), v); else window.sessionStorage.removeItem(K(k)); } catch (e) { /* full or blocked: memory only */ }
    };
    const keys = (prefix) => {
      const out = new Set(Array.from(mem.keys()).filter((k) => k.startsWith(prefix)));
      try { for (let i = 0; i < window.sessionStorage.length; i++) { const sk = window.sessionStorage.key(i); if (sk && sk.startsWith(K(prefix))) out.add(sk.slice(K('').length)); } } catch (e) { /* none */ }
      return Array.from(out);
    };
    return {
      get, set, clear: (k) => set(k, ''),
      has: (prefix) => keys(prefix).some((k) => get(k)),
      clearPrefix: (prefix) => keys(prefix).forEach((k) => set(k, '')),
      // give a text box its saved text back, and save what is typed from now on (a program that sets .value must send an "input" event afterwards)
      bind(el, k) { const v = get(k); if (v) el.value = v; el.addEventListener('input', () => set(k, el.value)); return el; },
    };
  })();

  async function api(method, path, body, opts) {
    const headers = { 'X-Requested-With': 'ols' };
    if (method === 'POST') headers['Content-Type'] = 'application/json';
    if (opts && opts.poll) headers['X-Poll'] = '1';
    const r = await fetch(path, { method, headers, body: method === 'POST' ? JSON.stringify(body || {}) : undefined, credentials: 'same-origin' });
    let data = null;
    try { data = await r.json(); } catch (e) { /* not json */ }
    if (!r.ok) {
      const err = new Error((data && data.error) || r.statusText || 'request failed');
      err.status = r.status; err.data = data;
      if (r.status === 401) err.message = 'Signed out. Open the URL printed by ui.sh again.';
      throw err;
    }
    return data;
  }
  api.get = (p, o) => api('GET', p, null, o);
  api.post = (p, b) => api('POST', p, b);

  async function waitJob(id, onTick) {  // onTick(job) is called on every poll; job.progress = [done, total] when the job reports it
    for (;;) {
      const j = await api.get('/api/jobs/' + id, { poll: true });
      if (onTick) onTick(j);
      if (j.status !== 'running') {
        if (j.status === 'error') throw new Error(j.error);
        return j.result;
      }
      await new Promise((r) => setTimeout(r, 700));
    }
  }

  let toastTimer = null;
  function toast(msg) {
    const old = document.querySelector('.toast');
    if (old) old.remove();
    const t = h('div', { class: 'toast', role: 'status', text: msg });
    document.body.append(t);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.remove(), 3500);
  }
  const notice = (kind, ...kids) => h('div', { class: 'notice ' + kind, role: kind === 'bad' ? 'alert' : null }, ...kids);
  const busy = (text) => h('span', { class: 'row tight' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), h('span', { text }));
  function guarded(btn, fn) {  // run an async click handler once at a time, show errors as a toast
    btn.addEventListener('click', async () => {
      if (btn.disabled) return;
      btn.disabled = true;
      try { await fn(); } catch (e) { toast(e.message); } finally { btn.disabled = false; }
    });
    return btn;
  }
  const tile = (label, value, sub) => h('div', { class: 'tile' }, h('div', { class: 'label', text: label }), h('div', { class: 'value', text: value }), sub ? h('div', { class: 'sub', text: sub }) : null);

  // ------------------------------------------------------------------ state, pages, router
  const S = { st: null, page: 'chat', pages: {}, order: [], threads: [], chatId: null };
  function registerPage(id, title, render) { S.pages[id] = { title, render }; S.order.push(id); }
  async function loadState() {
    S.st = await api.get('/api/state', { poll: true });
    const known = (n) => S.st.papers.some((p) => p.name === n);
    S.paper = known(S.st.active) ? S.st.active : (S.st.papers[0] || {}).name;
    const mins = Math.ceil(S.st.idle_seconds_left / 60);
    document.getElementById('local-badge').textContent = 'Local only, closes after ' + mins + ' min idle';
    renderPapers();
    return S.st;
  }
  const paperObj = (name) => (S.st.papers || []).find((p) => p.name === (name || S.paper));

  async function setPaper(name) {
    if (name === S.paper) return;
    await api.post('/api/papers/active', { name });
    S.chatId = null; S.threads = [];
    await loadState();
    await loadThreads();
    if (S.page !== 'chat') location.hash = '#/chat'; else route();
  }
  function renderPapers() {
    const box = clear(document.getElementById('paper-list'));
    if (!S.st.papers.length) { box.append(h('p', { class: 'muted small', text: 'No papers yet. Add one in Settings.' })); return; }
    for (const p of S.st.papers) {
      box.append(h('button', { type: 'button', class: 'side-item', 'aria-current': p.name === S.paper ? 'true' : null, title: p.path, onclick: () => setPaper(p.name).catch((e) => toast(e.message)) },
        h('span', { class: 'dot ' + (p.problem ? 'bad' : 'ok'), 'aria-hidden': 'true' }), h('span', { class: 'grow ellipsis', text: p.name })));
    }
  }
  async function loadThreads() {
    if (!S.paper) { S.threads = []; renderThreads(); return; }
    try { S.threads = (await api.get('/api/chat/threads?paper=' + encodeURIComponent(S.paper), { poll: true })).threads; } catch (e) { S.threads = []; }
    if (!S.chatId || !S.threads.some((t) => t.id === S.chatId)) S.chatId = (S.threads[0] || {}).id || null;
    renderThreads();
  }
  function renderThreads() {
    const box = clear(document.getElementById('chat-list'));
    for (const t of S.threads.slice(0, 12)) {
      box.append(h('button', { type: 'button', class: 'side-item', 'aria-current': t.id === S.chatId && S.page === 'chat' ? 'true' : null, onclick: () => { S.chatId = t.id; if (S.page === 'chat') route(); else location.hash = '#/chat'; } },
        h('span', { class: 'grow ellipsis', text: t.title })));
    }
  }

  const NAV = [['chat', 'Chat'], ['references', 'References'], ['activity', 'Activity'], ['settings', 'Settings']];
  const HIDDEN_PAGES = { write: 'chat', papers: 'chat' };  // old addresses still land somewhere sensible
  function buildNav() {
    const nav = clear(document.getElementById('nav'));
    for (const [id, label] of NAV.filter(([x]) => S.pages[x])) nav.append(h('a', { href: '#/' + id, 'aria-current': id === S.page ? 'page' : null, text: label }));
  }
  async function route() {
    let id = (location.hash.match(/^#\/(\w+)/) || [])[1];
    id = HIDDEN_PAGES[id] || id;
    S.page = S.pages[id] ? id : 'chat';
    document.body.dataset.page = S.page;
    document.getElementById('app').classList.remove('drawer-open');
    buildNav();
    const main = clear(document.getElementById('main'));
    main.append(h('p', { class: 'muted', text: 'Loading...' }));
    try {
      if (!S.st) { await loadState(); await loadThreads(); }
      const box = h('div', { class: 'page-box' });
      await S.pages[S.page].render(box);
      clear(main).append(box);
      renderThreads();
      if (window.OLS.panes && window.OLS.panes.onRoute) window.OLS.panes.onRoute(S.page);
    } catch (e) {
      clear(main).append(notice('bad', h('strong', { text: 'Could not load this page. ' }), e.message));
    }
    main.focus({ preventScroll: true });
  }

  // ------------------------------------------------------------------ Book
  const B = { id: null, chunk: null };
  registerPage('book', 'Book', async (box) => {
    await loadState();
    const st = S.st;
    box.append(h('div', { class: 'page-head' }, h('h1', { text: 'Book' }), h('span', { class: 'muted small', text: 'Imported once; each request reads only the few chunks that match.' })));
    if (!st.books.length) box.append(notice('warn', 'No book imported yet. Put the PDF in your inbox folder (~/.config/overleaf-skill/inbox/), then import it below.'));
    box.append(importCard(st, () => route()));
    if (!st.books.length) return;
    if (!B.id || !st.books.some((b) => b.id === B.id)) B.id = st.books[0].id;
    const bookSel = h('select', { 'aria-label': 'Book' }, st.books.map((b) => h('option', { value: b.id, selected: b.id === B.id, text: `${b.id} (${b.chunks} chunks, ${b.pages} pages)` })));
    bookSel.addEventListener('change', () => { B.id = bookSel.value; route(); });
    const reader = h('section', { class: 'card', 'aria-label': 'Chunk' }, h('p', { class: 'muted', text: 'Choose a chunk on the left, or try a query above.' }));
    const results = h('div');
    const q = h('input', { type: 'search', class: 'grow', placeholder: 'Try a request, e.g. "how do I describe sampling in my methods"', 'aria-label': 'Try a query' });
    const partSel = h('select', { 'aria-label': 'Part' }, h('option', { value: '', text: 'any part (routed from the text)' }), (st.parts || []).map((x) => h('option', { value: x, text: x })));
    const tryBtn = guarded(h('button', { type: 'button', class: 'primary', text: 'Search' }), async () => {
      const r = await api.get(`/api/book/${encodeURIComponent(B.id)}/query?q=${encodeURIComponent(q.value)}&part=${encodeURIComponent(partSel.value)}`);
      clear(results).append(h('section', { class: 'card', 'aria-label': 'Query result' },
        h('p', { class: 'small secondary', text: `${r.trace} routed to: ${r.parts.join(', ') || 'nothing (whole-book search)'} · terms: ${r.terms.join(', ') || '-'}` }),
        h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, ['Chunk', 'Score', 'Tokens', 'Why'].map((t) => h('th', { text: t })))),
          h('tbody', null, r.chunks.map((c) => h('tr', null,
            h('td', null, h('button', { type: 'button', class: 'ghost small', text: c.label, onclick: () => openChunk(reader, c.id) })),
            h('td', { class: 'num', text: c.score.toFixed(2) }), h('td', { class: 'num', text: String(c.tokens) }), h('td', { text: c.via }))))))));
    });
    q.addEventListener('keydown', (e) => { if (e.key === 'Enter') tryBtn.click(); });
    box.append(await ruleCardPanel(B.id));
    box.append(bookCheckCard(B.id));
    box.append(h('div', { class: 'row' }, h('label', { class: 'row tight small secondary' }, 'Book', bookSel)),
      h('section', { class: 'card' }, h('div', { class: 'row' }, q, partSel, tryBtn)), results);
    const treeBox = h('div', { class: 'tree' }, busy('Loading...'));
    box.append(h('div', { class: 'cols' }, h('section', { class: 'card', 'aria-label': 'Chapters and chunks' }, h('h2', { text: 'In reading order' }), treeBox), reader));
    const t = await api.get(`/api/book/${encodeURIComponent(B.id)}/tree`);
    clear(treeBox);
    t.tree.forEach((c, ci) => {
      treeBox.append(h('details', { open: ci === 0 }, h('summary', { text: `Ch ${c.chapter}: ${c.title}` }),
        c.sections.map((s) => h('details', { open: false }, h('summary', { class: 'small', text: s.title }),
          s.chunks.map((k) => h('button', { type: 'button', class: 'chunk small', 'data-id': k.id, text: `${k.id.split(':').pop()} · ${k.tokens} tok · p.${k.pages[0]}${k.pages[1] !== k.pages[0] ? '-' + k.pages[1] : ''} · ${k.parts.join(', ')}`, onclick: () => openChunk(reader, k.id) }))))));
    });
    box.append(notice('ok', `${t.info.n_chunks} chunks, ${fmtTok(t.info.tokens)} tokens in total; outline found by ${t.info.method}. Use "Review the outline" above if chapters look wrong.`));
  });

  function bookCheckCard(id) {
    const out = h('div');
    return h('section', { class: 'card', 'aria-label': 'Book check' }, h('h2', { text: 'Book check' }),
      h('p', { class: 'small secondary', text: 'For each part of a paper, the pages of the book that would be used when you ask about it. If a part shows pages that are clearly about something else, the matching needs tuning: tell Claude which part and what you expected.' }),
      guarded(h('button', { type: 'button', text: 'Run the check' }), async () => {
        const r = await api.get(`/api/book/${encodeURIComponent(id)}/check`);
        clear(out).append(h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, ['Part', 'Pages that would be used'].map((t) => h('th', { text: t })))),
          h('tbody', null, r.rows.map((row) => h('tr', null, h('td', { text: row.label }),
            h('td', null, row.chunks.length ? row.chunks.map((c) => h('div', { class: 'small', text: `${c.label}${c.has_card ? ' (summary ready)' : ''}` })) : h('span', { class: 'muted small', text: 'nothing matched' }),
              row.fallback ? h('div', { class: 'muted small', text: 'No page is tagged for this part, so the whole book was searched.' }) : null)))))));
      }), out);
  }

  async function ruleCardPanel(id) {
    const st = await api.get(`/api/book/${encodeURIComponent(id)}/rules`);
    const out = h('div');
    const body = h('div', { class: 'stack' });
    const done = st.todo === 0;
    body.append(h('p', { class: 'small secondary', text: done ? `Every chunk (${st.chunks}) has a rule card. Later requests carry these short rules instead of raw book text.` : `${st.with_cards} of ${st.chunks} chunks have a rule card. A card is 3 to 6 short rules distilled from a chunk: later requests carry the rules (with the page) instead of the raw book text, which is cheaper, sends less of the book out, and makes the right chunk easier to find.` }));
    if (!done) {
      body.append(guarded(h('button', { type: 'button', text: 'Preview the cost' }), async () => {
        const pl = await api.post(`/api/book/${encodeURIComponent(id)}/rules/preview`, {});
        const bar = h('progress', { max: '1', value: '0', class: 'grow', hidden: true });
        const go = guarded(h('button', { type: 'button', class: 'primary', text: 'Send the book to OpenAI once and create the cards' }), async () => {
          bar.hidden = false;
          const r = await api.post(`/api/book/${encodeURIComponent(id)}/rules/run`, { confirm: pl.payload_hash });
          try {
            const res = await waitJob(r.job, (j) => { if (j.progress) { bar.value = j.progress[0] / j.progress[1]; } });
            toast(res.status === 'done' ? 'Rule cards created' : 'Stopped: ' + (res.error || res.status));
          } catch (e) { toast(e.message); }
          route();
        });
        clear(out).append(notice('warn', h('strong', { text: 'This sends the book text to OpenAI. ' }), `${pl.chunks} chunks in ${pl.calls} requests to ${pl.model} (${pl.endpoint}), about ${fmtTok(pl.est_tokens_in)} tokens in and ${fmtTok(pl.est_tokens_out)} out${pl.est_cost != null ? ', about ' + pl.est_cost + ' in your price units' : ''}. It happens once and can resume if interrupted.`),
          h('div', { class: 'row' }, go, bar));
      }), out);
    }
    return h('section', { class: 'card', 'aria-label': 'Rule cards' }, h('h2', { text: 'Rule cards' }), body);
  }

  async function openChunk(reader, id) {
    const c = await api.get(`/api/book/${encodeURIComponent(B.id)}/chunk?id=${encodeURIComponent(id)}`);
    B.chunk = id;
    document.querySelectorAll('.tree .chunk').forEach((b) => b.setAttribute('aria-current', String(b.dataset.id === id)));
    clear(reader).append(h('h2', { text: c.label }), h('p', { class: 'small secondary', text: `${c.id} · tagged: ${c.parts.join(', ') || '-'} · ~${c.tokens} tokens · PDF pages ${c.pdf_start}-${c.pdf_end}` }),
      h('div', { class: 'reader', tabindex: '0', text: c.text }),
      h('div', { class: 'row' }, c.prev ? h('button', { type: 'button', text: 'Previous', onclick: () => openChunk(reader, c.prev) }) : null, c.next ? h('button', { type: 'button', text: 'Next', onclick: () => openChunk(reader, c.next) }) : null));
  }

  function importCard(st, reload) {
    const status = h('div');
    const pdf = h('select', { 'aria-label': 'PDF in the inbox' }, st.inbox.length ? st.inbox.map((f) => h('option', { value: f, text: f })) : h('option', { value: '', text: '(inbox is empty)' }));
    const bid = h('input', { type: 'text', value: (st.books[0] || {}).id || 'handbook', 'aria-label': 'Book id', size: '14' });
    const run = (mode, label) => guarded(h('button', { type: 'button', text: label }), async () => {
      if (!pdf.value) throw new Error('Put the PDF in the inbox folder first.');
      clear(status).append(busy('Working on the book (a few seconds for a long one)...'));
      try {
        const r = await api.post('/api/book/ingest', { pdf: pdf.value, id: bid.value.trim(), mode });
        const res = await waitJob(r.job);
        clear(status).append(notice(res.status === 'skipped' ? 'warn' : 'ok', res.status === 'skipped' ? 'Already imported: nothing was written. ' + (res.note || '') : `${res.status}: ${res.chunks != null ? res.chunks + ' chunks in ' + res.chapters + ' chapters, ' : ''}outline by ${res.method}.`),
          (res.warnings || []).map((w) => notice('warn', w)));
        await loadState();
      } catch (e) { clear(status).append(notice('bad', e.message)); throw e; }
    });
    const outlineBox = h('div');
    return h('details', { class: 'card', open: !st.books.length }, h('summary', { text: 'Import or re-import a book' }),
      h('div', { class: 'stack' },
        h('p', { class: 'small secondary', text: 'Importing the same PDF again does nothing. Steps: analyze the outline, review it, then build the chunks.' }),
        h('div', { class: 'row' }, pdf, h('label', { class: 'row tight small secondary' }, 'Book id', bid)),
        h('div', { class: 'row' }, run('outline', '1. Analyze the outline'), run('rebuild', '2. Build chunks from my outline'), run('build', 'Import in one go'), run('force', 'Start over (re-analyze)')),
        h('div', { class: 'row' }, guarded(h('button', { type: 'button', text: 'Review the outline' }), async () => { await outlineEditor(outlineBox, bid.value.trim()); })),
        status, outlineBox));
  }

  async function outlineEditor(box, id) {
    const o = await api.get(`/api/book/${encodeURIComponent(id)}/outline`);
    const rows = o.entries.map((e) => ({ level: h('input', { type: 'number', min: '1', max: '4', value: String(e.level), 'aria-label': 'Level' }), number: h('input', { type: 'text', value: e.number || '', size: '5', 'aria-label': 'Number' }),
      title: h('input', { type: 'text', value: e.title, 'aria-label': 'Title', class: 'grow' }), page: h('input', { type: 'number', value: String(e.pdf_page), 'aria-label': 'PDF page' }), skip: h('input', { type: 'checkbox', checked: !!e.skip, 'aria-label': 'Skip' }), e }));
    clear(box).append(h('section', { class: 'stack' },
      h('p', { class: 'small secondary', text: `Found by ${o.method}; PDF page = printed page + ${o.page_offset}. Fix titles, levels (1 = chapter, 2 = section) or pages, tick Skip for parts you do not want searched (index, bibliography), save, then run step 2.` }),
      (o.warnings || []).map((w) => notice('warn', w)),
      h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, ['Level', 'No.', 'Title', 'PDF page', 'Skip'].map((t) => h('th', { text: t })))),
        h('tbody', null, rows.map((r) => h('tr', null, h('td', null, r.level), h('td', null, r.number), h('td', null, r.title), h('td', null, r.page), h('td', null, r.skip)))))),
      guarded(h('button', { type: 'button', class: 'primary', text: 'Save outline' }), async () => {
        const entries = rows.map((r) => Object.assign({}, r.e, { level: Number(r.level.value), number: r.number.value, title: r.title.value, pdf_page: Number(r.page.value), skip: r.skip.checked }));
        const res = await api.post(`/api/book/${encodeURIComponent(id)}/outline`, { outline: Object.assign({}, o, { entries }) });
        toast('Saved ' + res.saved + ' entries. Now run step 2.');
      })));
  }

  // ------------------------------------------------------------------ chrome
  function applyTheme(t) {
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  }
  function init() {
    applyTheme(store.get('ols-theme'));
    document.documentElement.dataset.texture = store.get('ols-texture') === 'on' ? 'on' : 'off';
    document.getElementById('theme-btn').addEventListener('click', () => {
      const cur = store.get('ols-theme') || 'auto';
      const next = cur === 'auto' ? 'light' : cur === 'light' ? 'dark' : 'auto';
      store.set('ols-theme', next); applyTheme(next); toast('Theme: ' + next);
    });
    document.getElementById('menu-btn').addEventListener('click', () => document.getElementById('app').classList.toggle('drawer-open'));
    document.getElementById('new-chat').addEventListener('click', async () => {
      if (!S.paper) return;
      try {
        const r = await api.post('/api/chat/new', { paper: S.paper });
        S.chatId = r.thread.id; await loadThreads();
        if (S.page === 'chat') route(); else location.hash = '#/chat';
      } catch (e) { toast(e.message); }
    });
    window.addEventListener('hashchange', route);
    setInterval(() => { if (S.st) loadState().catch(() => {}); }, 60000);
    route();
  }

  window.OLS = { h, clear, api, waitJob, toast, notice, busy, guarded, tile, fmtTok, ago, registerPage, S, route, loadState, loadThreads, renderThreads, paperObj, setPaper, store, drafts };
  /* The frame of the chat page (the page itself, the main area, the side area) is never meant to scroll; only the conversation, the source and the PDF do. Something
     that scrolls it anyway (a focus, a link to an anchor, a browser quirk) would slide the buttons at the top out of sight, so it is put back at once. */
  document.addEventListener('scroll', (e) => {
    const t = e.target === document ? document.scrollingElement : e.target;
    if (!t || !t.scrollTop || !t.classList) return;
    const chat = document.body.dataset.page === 'chat';
    if (t === document.scrollingElement || t.classList.contains('app') || (chat && (t.id === 'main' || t.classList.contains('chat-page') || t.classList.contains('lx-card'))) ||
        (t.id === 'panes-area' && t.dataset.layout === 'tabs')) t.scrollTop = 0;
  }, true);
  window.addEventListener('load', init);
}());
