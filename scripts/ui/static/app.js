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
  const S = { st: null, page: 'papers', pages: {}, order: [] };
  function registerPage(id, title, render) { S.pages[id] = { title, render }; S.order.push(id); }
  async function loadState() {
    S.st = await api.get('/api/state', { poll: true });
    const sel = document.getElementById('active-paper');
    clear(sel);
    for (const p of S.st.papers) sel.append(h('option', { value: p.name, selected: p.name === S.st.active, text: p.name }));
    if (!S.st.papers.length) sel.append(h('option', { text: '(no papers registered)' }));
    S.paper = S.st.active && S.st.papers.some((p) => p.name === S.st.active) ? S.st.active : (S.st.papers[0] || {}).name;
    sel.value = S.paper || '';
    const mins = Math.ceil(S.st.idle_seconds_left / 60);
    document.getElementById('local-badge').textContent = 'Local only, closes after ' + mins + ' min idle';
    return S.st;
  }
  const paperObj = (name) => (S.st.papers || []).find((p) => p.name === (name || S.paper));

  const NAV = ['papers', 'write', 'book', 'monitor', 'paper_index', 'references', 'settings'];
  function buildNav() {
    const nav = clear(document.getElementById('nav'));
    for (const id of NAV.filter((x) => S.pages[x])) nav.append(h('a', { href: '#/' + id, 'aria-current': id === S.page ? 'page' : null, text: S.pages[id].title }));
  }
  async function route() {
    const id = (location.hash.match(/^#\/(\w+)/) || [])[1];
    S.page = S.pages[id] ? id : 'papers';
    buildNav();
    const main = clear(document.getElementById('main'));
    main.append(h('p', { class: 'muted' }, busy('Loading...')));
    try {
      if (!S.st) await loadState();
      const box = h('div');
      await S.pages[S.page].render(box);
      clear(main).append(box);
    } catch (e) {
      clear(main).append(notice('bad', h('strong', { text: 'Could not load this page. ' }), e.message));
    }
    main.focus({ preventScroll: true });
  }

  // ------------------------------------------------------------------ Papers
  registerPage('papers', 'Papers', async (box) => {
    await loadState();
    const st = S.st;
    box.append(h('div', { class: 'page-head' }, h('h1', { text: 'Papers' }),
      h('span', { class: 'muted small', text: st.active ? `Active: ${st.active} (set by ${st.active_source}, ${ago(Date.now() / 1000 - st.active_age)})` : 'No paper has been chosen yet; the first one is shown' })));
    if (!st.papers.length) {
      box.append(notice('warn', 'No papers are registered. In a terminal: ', h('code', { text: 'python3 scripts/common.py add /path/to/overleaf/clone --group "Mendeley group"' }),
        '. Papers registered after this app started need a restart (ui.sh down, ui.sh up) to be visible here.'));
    }
    for (const p of st.papers) box.append(paperCard(p, st));
  });

  function paperCard(p, st) {
    const out = h('pre', { class: 'small payload', hidden: true });
    const msg = h('input', { type: 'text', value: 'Update via the Overleaf skill web app', 'aria-label': 'Commit message', class: 'grow' });
    const run = (action, body) => async () => {
      const r = await api.post('/api/papers/' + action, Object.assign({ name: p.name }, body || {}));
      out.hidden = false; out.textContent = r.output;
      await loadState();
    };
    const idx = p.index;
    const idxLine = idx ? `${idx.chunks} chunks (${Object.entries(idx.counts).map(([k, v]) => v + ' ' + k).join(', ')}), ~${fmtTok(idx.manuscript_tokens)} manuscript tokens, refreshed ${ago(idx.indexed_at)}${idx.unreadable ? ' (unreadable, rebuilds itself)' : ''}` : 'not indexed yet (it is built the first time you query or revise)';
    const dry = h('input', { type: 'checkbox', checked: p.settings.dry_run, 'aria-label': 'Preview before sending' });
    dry.addEventListener('change', async () => {
      try { await api.post('/api/settings', { paper: p.name, dry_run: dry.checked }); toast(dry.checked ? 'Preview required before anything is sent' : 'Preview no longer required for this paper'); await loadState(); }
      catch (e) { toast(e.message); dry.checked = !dry.checked; }
    });
    return h('section', { class: 'card', 'aria-label': p.name },
      h('div', { class: 'row' }, h('h2', { text: p.name }), p.name === st.active ? h('span', { class: 'badge ok', text: 'active' }) : null,
        p.problem ? h('span', { class: 'badge bad', text: 'problem' }) : h('span', { class: 'badge ok', text: 'healthy' }),
        h('span', { class: 'grow' }),
        p.name !== st.active ? guarded(h('button', { type: 'button', text: 'Make active' }), async () => { await api.post('/api/papers/active', { name: p.name }); await loadState(); route(); }) : null),
      p.problem ? notice('bad', p.problem) : null,
      h('p', { class: 'small secondary' }, h('code', { text: p.path }), ` · Overleaf project ${p.remote_id} · bib ${p.bib} · Mendeley ${p.group || 'not linked'}`),
      h('p', { class: 'small secondary', text: 'Index: ' + idxLine }),
      h('div', { class: 'row' },
        guarded(h('button', { type: 'button', text: 'Pull from Overleaf' }), run('pull')),
        guarded(h('button', { type: 'button', text: 'What changed' }), run('status')),
        guarded(h('button', { type: 'button', text: 'Refresh index' }), async () => { const r = await api.post('/api/paper/' + encodeURIComponent(p.name) + '/refresh', {}); out.hidden = false; out.textContent = `${r.added} added, ${r.changed} changed, ${r.removed} removed, ${r.unchanged} unchanged`; await loadState(); }),
        h('a', { href: '#/write', onclick: async () => { await api.post('/api/papers/active', { name: p.name }); } }, 'Improve the writing')),
      h('div', { class: 'row' }, msg, guarded(h('button', { type: 'button', class: 'primary', text: 'Commit and push to Overleaf' }), async () => { await run('sync', { message: msg.value })(); })),
      h('label', { class: 'check small secondary' }, dry, 'Show me exactly what will be sent to ChatGPT, and require my confirmation, before every pass'),
      out);
  }

  // ------------------------------------------------------------------ Write
  const W = { preview: null, proposal: null, selected: new Set() };

  registerPage('write', 'Write', async (box) => {
    await loadState();
    const st = S.st;
    if (!S.paper) { box.append(notice('warn', 'Register a paper first (Papers page).')); return; }
    const ostate = st.settings.openai.state;
    box.append(h('div', { class: 'page-head' }, h('h1', { text: 'Write' }), h('span', { class: 'muted small', text: 'ChatGPT edits one passage at a time, following the book. Nothing is written until you apply it.' })));
    if (ostate !== 'ok') box.append(notice('warn', 'ChatGPT is not configured yet: ', st.settings.openai.detail, ' See Settings.'));

    let outline = { outline: [] };
    try { outline = await api.get('/api/paper/' + encodeURIComponent(S.paper) + '/outline'); } catch (e) { box.append(notice('bad', e.message)); }
    const parts = [];
    for (const o of outline.outline) if (o.part && !parts.some((x) => x.part === o.part) && o.chunks) parts.push({ part: o.part, title: o.title });

    const partSel = h('select', { id: 'w-part' }, h('option', { value: '', text: 'Work it out from my instruction' }),
      parts.map((p) => h('option', { value: p.part, text: `${p.part} (${p.title})` })));
    const bookSel = h('select', { id: 'w-book' }, st.books.length ? st.books.map((b) => h('option', { value: b.id, text: `${b.id} (${b.chunks} chunks)` })) : null, h('option', { value: '', text: 'No book rules' }));
    const instr = h('textarea', { id: 'w-instr', placeholder: 'e.g. Tighten the methods and follow the book on describing sampling.', 'aria-label': 'Instruction' });
    instr.value = W.instruction || '';
    const result = h('div');
    const previewBox = h('div');

    const body = () => ({ paper: S.paper, instruction: instr.value, part: partSel.value || null, book_id: bookSel.value || null, use_book: !!bookSel.value });
    const previewBtn = guarded(h('button', { type: 'button', class: 'primary', text: 'Preview what will be sent' }), async () => {
      if (!instr.value.trim()) throw new Error('Say what to do first.');
      W.instruction = instr.value;
      clear(previewBox).append(busy('Preparing the exact request...'));
      try {
        W.preview = await api.post('/api/revise/preview', body());
        W.previewBody = JSON.stringify(body());
        await showPreview(previewBox, result);
      } catch (e) { clear(previewBox).append(notice('bad', e.message)); throw e; }
    });

    box.append(h('div', { class: 'cols' },
      h('div', null,
        h('section', { class: 'card', 'aria-label': 'What to improve' }, h('h2', { text: 'What to improve' }),
          h('div', { class: 'stack' },
            h('label', { class: 'field' }, h('span', { text: 'Part of the paper (' + S.paper + ')' }), partSel),
            h('label', { class: 'field' }, h('span', { text: 'Writing book' }), bookSel),
            h('label', { class: 'field' }, h('span', { text: 'Instruction' }), instr),
            h('div', { class: 'row' }, previewBtn))),
        previewBox,
        h('section', { class: 'card', 'aria-label': 'Recent proposals' }, h('h2', { text: 'Recent proposals' }), await recentProposals(result))),
      result));
    if (W.proposal && W.proposal.paper === S.paper) renderProposal(result, W.proposal);
    else result.append(notice('ok', 'Pick the part, write what you want, and press Preview. You will see the exact text that would leave this computer before you send it.'));
  });

  async function recentProposals(result) {
    const list = h('div', { class: 'stack small' });
    try {
      const r = await api.get('/api/proposals?paper=' + encodeURIComponent(S.paper));
      if (!r.proposals.length) list.append(h('span', { class: 'muted', text: 'None yet.' }));
      for (const p of r.proposals.slice(0, 8)) {
        list.append(h('div', null, h('button', { type: 'button', class: 'ghost small', text: `${p.id} · ${p.status}${p.handoff ? ' (handed to Claude)' : ''} · ${p.hunks} hunks`, onclick: async () => {
          const full = await api.get(`/api/proposal?paper=${encodeURIComponent(S.paper)}&id=${p.id}`);
          W.proposal = full; W.selected = new Set(full.units.flatMap((u) => u.hunks.map((x) => x.uid)));
          renderProposal(result, full);
        } }), h('div', { class: 'muted', text: (p.instruction || '').slice(0, 80) })));
      }
    } catch (e) { list.append(h('span', { class: 'muted', text: e.message })); }
    return list;
  }

  async function showPreview(previewBox, result) {
    const pv = W.preview;
    const rules = [];
    for (const id of pv.rules.slice(0, 8)) {
      const bookId = id.split(':')[1];
      try { const c = await api.get(`/api/book/${encodeURIComponent(bookId)}/chunk?id=${encodeURIComponent(id)}`); rules.push(h('li', { text: c.label })); }
      catch (e) { rules.push(h('li', { text: id })); }
    }
    const sendBtn = guarded(h('button', { type: 'button', class: 'primary', text: 'Send to ChatGPT' }), async () => {
      if (W.previewBody !== JSON.stringify({ paper: S.paper, instruction: document.getElementById('w-instr').value, part: document.getElementById('w-part').value || null, book_id: document.getElementById('w-book').value || null, use_book: !!document.getElementById('w-book').value })) {
        throw new Error('You changed the request after the preview. Preview it again first.');
      }
      clear(result).append(h('section', { class: 'card' }, busy('Waiting for ChatGPT...')));
      const r = await api.post('/api/revise/run', Object.assign(JSON.parse(W.previewBody), { confirm: pv.payload_hash }));
      let res;
      try { res = await waitJob(r.job); } catch (e) { clear(result).append(notice('bad', e.message)); throw e; }
      const prop = await api.get(`/api/proposal?paper=${encodeURIComponent(S.paper)}&id=${res.proposal}`);
      W.proposal = prop; W.selected = new Set(prop.units.flatMap((u) => u.hunks.map((x) => x.uid)));
      renderProposal(result, prop);
    });
    clear(previewBox).append(h('section', { class: 'card', 'aria-label': 'Preview' },
      h('h2', { text: 'Preview: nothing has been sent' }),
      h('p', { class: 'small secondary', text: `Model ${pv.model} at ${pv.endpoint}. ${pv.payloads.length} request(s), about ${fmtTok(pv.est_tokens_in)} tokens in (instructions, book rules and the passage); the whole manuscript is ${fmtTok(pv.manuscript_tokens)} tokens. LaTeX such as citations, formulas and headings is replaced by placeholders and never leaves.` }),
      pv.warnings.length ? notice('warn', pv.warnings.join(' ')) : null,
      h('h3', { text: 'Book rules included' }), rules.length ? h('ul', { class: 'small' }, rules) : h('p', { class: 'small muted', text: 'None.' }),
      h('details', null, h('summary', { text: 'The exact text that would be sent' }),
        pv.payloads.map((p) => h('div', { class: 'payload' }, h('div', { class: 'small muted', text: `${p.file}, lines ${p.lines[0]}-${p.lines[1]}` }), h('pre', { text: (p.request.instructions || p.request.messages[0].content) + '\n\n----\n\n' + (p.request.input || p.request.messages[1].content) })))),
      h('div', { class: 'row' }, sendBtn)));
  }

  function renderProposal(result, p) {
    clear(result);
    const sel = W.selected;
    const nHunks = p.units.reduce((n, u) => n + u.hunks.length, 0);
    const usage = p.usage || {};
    const head = h('section', { class: 'card', 'aria-label': 'Proposal' },
      h('div', { class: 'row' }, h('h2', { text: 'Proposal ' + p.id }), h('span', { class: 'badge ' + (p.status === 'applied' ? 'ok' : p.status === 'failed' ? 'bad' : ''), text: p.status })),
      h('p', { class: 'small secondary', text: `${p.model} · ${fmtTok(usage.input_tokens)} in / ${fmtTok(usage.output_tokens)} out${usage.cost != null ? ' · cost ' + usage.cost : ''} · book rules: ${p.rules.length}` }),
      p.instruction ? h('p', { class: 'small', text: 'Instruction: ' + p.instruction }) : null,
      (p.warnings || []).map((w) => notice('warn', w)));
    result.append(head);
    if (p.status === 'failed') head.append(notice('bad', 'ChatGPT did not return an acceptable answer, so nothing is offered. ', p.units.map((u) => u.error).filter(Boolean).join(' ')));
    if (p.status === 'no_changes') head.append(notice('ok', 'ChatGPT found nothing it would change.'));

    const count = h('span', { class: 'muted small' });
    const applyBtn = guarded(h('button', { type: 'button', class: 'primary' }), async () => {
      const accept = [...sel];
      if (!accept.length) throw new Error('Select at least one change.');
      let r;
      try { r = await api.post('/api/proposal/apply', { paper: p.paper, id: p.id, accept }); }
      catch (e) {
        head.append(notice('bad', h('strong', { text: 'Not applied: ' }), e.message, e.data && e.data.handoff_available ? ' Nothing was written.' : ''));
        throw e;
      }
      toast('Applied ' + r.accepted.length + ' change(s)');
      p.status = 'applied';
      await loadState();
      renderApplied(result, p, r);
    });
    const updateCount = () => { count.textContent = `${sel.size} of ${nHunks} selected`; applyBtn.textContent = `Apply ${sel.size} selected change(s)`; applyBtn.disabled = !sel.size || p.status === 'applied'; };
    const handBtn = guarded(h('button', { type: 'button', text: 'Hand to Claude' }), async () => {
      const accept = [...sel];
      if (!accept.length) throw new Error('Select at least one change.');
      await api.post('/api/proposal/handoff', { paper: p.paper, id: p.id, accept });
      p.handoff = { accepted: accept };
      head.append(notice('ok', h('strong', { text: 'Handed to Claude. ' }), 'In Claude Code say: "apply the proposal I handed you". Claude applies exactly the changes you ticked, cleans them per step 6b, and records it here.'));
    });
    handBtn.title = 'Claude applies the ticked changes itself (it runs the watermark clean). Use this when the cleaner is not set up here.';
    const rejectBtn = guarded(h('button', { type: 'button', text: 'Reject all' }), async () => {
      await api.post('/api/proposal/record', { paper: p.paper, id: p.id, accepted: [], rejected: 'all' });
      toast('Rejected; nothing was changed'); p.status = 'rejected'; renderProposal(result, p);
    });
    if (nHunks && p.status !== 'applied' && p.status !== 'rejected') {
      head.append(h('div', { class: 'row' },
        h('button', { type: 'button', text: 'Select all', onclick: () => { p.units.forEach((u) => u.hunks.forEach((x) => sel.add(x.uid))); result.querySelectorAll('.hunk input[type=checkbox]').forEach((c) => { c.checked = true; }); updateCount(); } }),
        h('button', { type: 'button', text: 'Select none', onclick: () => { sel.clear(); result.querySelectorAll('.hunk input[type=checkbox]').forEach((c) => { c.checked = false; }); updateCount(); } }),
        count, h('span', { class: 'grow' }), rejectBtn, handBtn, applyBtn));
    }
    for (const u of p.units) {
      const bad = (u.validators || []).filter((v) => !v.ok);
      const unit = h('section', { class: 'card', 'aria-label': u.unit_id },
        h('h3', { text: `${u.file}, lines ${u.line_start}-${u.line_end}` }), h('p', { class: 'small muted', text: u.section_path + (u.retries ? ` · ChatGPT was asked to redo it ${u.retries}x` : '') }),
        u.status === 'failed' ? notice('bad', u.error || 'failed') : null,
        bad.length && u.status !== 'failed' ? notice('warn', 'Heads up: ', bad.map((v) => `${v.name}: ${v.detail}`).join('; ')) : null,
        u.questions.map((q) => notice('warn', h('strong', { text: 'Question for you: ' }), q)));
      for (const hk of u.hunks) unit.append(hunkCard(hk, sel, updateCount, p.status === 'applied' || p.status === 'rejected'));
      result.append(unit);
    }
    updateCount();
  }

  function diffSpans(ops, side) {
    return ops.filter(([op]) => op === '=' || (side === 'old' ? op === '-' : op === '+')).map(([op, t]) => (op === '=' ? document.createTextNode(t) : h(op === '-' ? 'del' : 'ins', { class: 'd', text: t })));
  }
  function hunkCard(hk, sel, onChange, locked) {
    const cb = h('input', { type: 'checkbox', checked: sel.has(hk.uid) || (locked && hk.status === 'accepted'), disabled: locked, 'aria-label': 'Accept change ' + hk.uid });
    cb.addEventListener('change', () => { if (cb.checked) sel.add(hk.uid); else sel.delete(hk.uid); onChange(); });
    return h('div', { class: 'hunk', 'aria-label': 'Change ' + hk.uid },
      h('header', null, cb, h('div', { class: 'grow' },
        h('div', { class: 'small', text: hk.reasons.length ? hk.reasons.join('; ') : '(no reason given)' }),
        hk.rules.length ? h('div', { class: 'rule', text: 'Follows: ' + hk.rules.join(', ') }) : null),
        locked ? h('span', { class: 'badge', text: hk.status }) : null),
      h('div', { class: 'sides' },
        h('div', { class: 'side' }, h('h4', { text: 'Now' }), diffSpans(hk.diff, 'old')),
        h('div', { class: 'side' }, h('h4', { text: 'Proposed' }), diffSpans(hk.diff, 'new'))));
  }

  function renderApplied(result, p, r) {
    clear(result);
    const msg = h('input', { type: 'text', class: 'grow', value: 'Improve writing with the book rules (' + p.id + ')', 'aria-label': 'Commit message' });
    result.append(h('section', { class: 'card' },
      h('h2', { text: 'Applied' }),
      notice('ok', `Wrote ${r.written.map((w) => w.file).join(', ')}. The index followed: ${r.index.changed} changed, ${r.index.unchanged} unchanged.`),
      h('p', { class: 'small secondary', text: 'The text is only on this computer until you send it to Overleaf.' }),
      h('div', { class: 'row' }, msg, guarded(h('button', { type: 'button', class: 'primary', text: 'Commit and push to Overleaf' }), async () => {
        const out = await api.post('/api/papers/sync', { name: p.paper, message: msg.value });
        await api.post('/api/proposal/record', { paper: p.paper, id: p.id, accepted: [], rejected: [], applied: false, synced: true, note: 'synced from the web app' }).catch(() => {});
        toast(out.output.split('\n')[0] || 'Done');
        result.append(h('pre', { class: 'small payload', text: out.output }));
      }))));
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

  // ------------------------------------------------------------------ Settings
  registerPage('settings', 'Settings', async (box) => {
    await loadState();
    const st = S.st;
    const health = await api.get('/api/health');
    box.append(h('div', { class: 'page-head' }, h('h1', { text: 'Settings and health' })));
    box.append(h('section', { class: 'card', 'aria-label': 'Health' }, h('h2', { text: 'Health' }),
      health.issues.length ? h('ul', null, health.issues.map((i) => h('li', null, h('strong', { text: i.scope + ': ' }), i.text))) : notice('ok', 'Everything is set up.')));
    box.append(h('section', { class: 'card', 'aria-label': 'Secrets' }, h('h2', { text: 'Keys and tokens' }),
      h('p', { class: 'small secondary', text: 'These live in files you create yourself (never pasted in a chat). The app only ever reads them; it never shows them.' }),
      h('table', null, h('tbody', null,
        h('tr', null, h('td', { text: 'ChatGPT (OpenAI)' }), h('td', null, h('span', { class: 'badge ' + (st.settings.openai.state === 'ok' ? 'ok' : 'warn'), text: st.settings.openai.state }), ' ', h('span', { class: 'small muted', text: st.settings.openai.detail })),
          h('td', null, h('code', { class: 'small', text: '~/.config/overleaf-skill/openai.json  {"api_key": "...", "model": "..."}' }))),
        h('tr', null, h('td', { text: 'Overleaf Git token' }), h('td', null, h('span', { class: 'badge ' + (st.settings.git_token ? 'ok' : 'warn'), text: st.settings.git_token ? 'present' : 'missing' })),
          h('td', null, h('code', { class: 'small', text: '~/.config/overleaf-skill/overleaf_git.json  {"token": "..."}' })))))));

    const wm = h('input', { type: 'text', class: 'grow', value: st.settings.ui.watermark_url || '', placeholder: 'http://host.docker.internal:PORT', 'aria-label': 'Watermark service URL' });
    const idle = h('input', { type: 'number', min: '5', max: '1440', value: String(st.settings.ui.idle_minutes), 'aria-label': 'Idle minutes' });
    const save = guarded(h('button', { type: 'button', class: 'primary', text: 'Save' }), async () => {
      await api.post('/api/settings', { ui: { watermark_url: wm.value.trim(), idle_minutes: Number(idle.value) } });
      toast('Saved'); route();
    });
    box.append(h('section', { class: 'card', 'aria-label': 'App settings' }, h('h2', { text: 'This app' }),
      h('div', { class: 'stack' },
        h('label', { class: 'field' }, h('span', { text: 'Step 6b: watermark-clean service URL (needed before this app can apply text; otherwise use Hand to Claude)' }), h('div', { class: 'row' }, wm)),
        h('p', { class: 'small secondary' }, 'Status: ', h('span', { class: 'badge ' + (health.watermark.state === 'ok' ? 'ok' : 'warn'), text: health.watermark.state }), ' ', health.watermark.detail),
        h('label', { class: 'field' }, h('span', { text: 'Close the app after this many idle minutes' }), idle),
        h('div', { class: 'row' }, save))));

    const texture = h('input', { type: 'checkbox', checked: document.documentElement.dataset.texture === 'on' });
    texture.addEventListener('change', () => { document.documentElement.dataset.texture = texture.checked ? 'on' : 'off'; store.set('ols-texture', texture.checked ? 'on' : 'off'); });
    box.append(h('section', { class: 'card', 'aria-label': 'Display' }, h('h2', { text: 'Display' }),
      h('label', { class: 'check' }, texture, 'Pattern fills on charts (for colour-blind readers and printing)')));
    if (st.settings.in_docker) box.append(notice('ok', 'Running in Docker: only the registered paper folders, the data folder and the inbox are visible to this app. A paper registered after it started needs ui.sh down and ui.sh up.'));
  });

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
    document.getElementById('active-paper').addEventListener('change', async (e) => {
      try { await api.post('/api/papers/active', { name: e.target.value }); await loadState(); route(); } catch (err) { toast(err.message); }
    });
    window.addEventListener('hashchange', route);
    setInterval(() => { if (S.st) loadState().catch(() => {}); }, 60000);
    route();
  }

  window.OLS = { h, clear, api, waitJob, toast, notice, busy, guarded, tile, fmtTok, ago, registerPage, S, route, loadState, paperObj, store };
  window.addEventListener('load', init);
}());
