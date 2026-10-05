/* The chat page: say what you want, watch what is being done, read the explanation and the corrected text, adjust the tone, approve.
   Every piece of manuscript or model text goes in as text (textContent / text nodes), never as HTML. */
(function () {
  'use strict';
  const { h, clear, api, notice, busy, guarded, registerPage, toast, fmtTok, ago, S, waitJob, drafts } = window.OLS;
  const enc = encodeURIComponent;
  const words = (n) => Math.round(n);

  // ------------------------------------------------------------------ small helpers
  async function copyText(text, what) {
    try { await navigator.clipboard.writeText(text); toast((what || 'Text') + ' copied'); }
    catch (e) { toast('Your browser did not allow copying. Select the text and copy it by hand.'); }
  }
  function diffSpans(ops, side) {
    return ops.filter(([op]) => op === '=' || (side === 'old' ? op === '-' : op === '+')).map(([op, t]) => (op === '=' ? document.createTextNode(t) : h(op === '-' ? 'del' : 'ins', { class: 'd', text: t })));
  }
  function readonlyBox(text) { const t = h('textarea', { rows: '4', readonly: true, class: 'mono', 'aria-label': 'Suggestion' }); t.value = text; return t; }
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + 's'}`;
  // the user message of a request as text; a picture shows as a line (the server never sends the picture itself to the page)
  function requestText(rq) {
    const u = rq.input !== undefined ? rq.input : rq.messages[1].content;
    if (typeof u === 'string') return u;
    const parts = Array.isArray(u) && u[0] && u[0].content ? u[0].content : u;
    return parts.map((x) => (x.text !== undefined ? x.text : '[a picture goes here]')).join('\n');
  }
  // the heading of one passage of a suggestion: where it is, or where the new text goes
  const unitTitle = (u) => (u.insert ? `${u.section_path.replace(/ \(new text\)$/, '')}: new text after line ${u.line_start - 1} of ${u.file}` : `${u.section_path.split(' > ').slice(-1)[0]} · lines ${u.line_start} to ${u.line_end}`);
  const icon = (level) => ({ ok: '✓', warn: '!', error: '✗' }[level] || '•');

  /* A small, safe Markdown renderer for the review report: headings, lists, tables, bold, italics, code. Text nodes only, never HTML. */
  function inline(text) {
    const out = [];
    const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(document.createTextNode(text.slice(last, m.index)));
      const t = m[0];
      out.push(t.startsWith('**') ? h('strong', { text: t.slice(2, -2) }) : t.startsWith('`') ? h('code', { text: t.slice(1, -1) }) : h('em', { text: t.slice(1, -1) }));
      last = m.index + t.length;
    }
    if (last < text.length) out.push(document.createTextNode(text.slice(last)));
    return out;
  }
  function mdLite(md) {
    const box = h('div', { class: 'md' });
    const lines = String(md).split('\n');
    let i = 0;
    while (i < lines.length) {
      const ln = lines[i];
      let m;
      if (!ln.trim()) { i++; continue; }
      if ((m = ln.match(/^(#{1,4})\s+(.*)$/))) { box.append(h('h' + Math.min(m[1].length + 2, 5), null, ...inline(m[2]))); i++; continue; }
      if (/^\s*[-*]\s+/.test(ln)) {
        const ul = h('ul');
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { ul.append(h('li', null, ...inline(lines[i].replace(/^\s*[-*]\s+/, '')))); i++; }
        box.append(ul); continue;
      }
      if (/^\s*\d+[.)]\s+/.test(ln)) {
        const ol = h('ol');
        while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) { ol.append(h('li', null, ...inline(lines[i].replace(/^\s*\d+[.)]\s+/, '')))); i++; }
        box.append(ol); continue;
      }
      if (/^\s*\|/.test(ln)) {
        const rows = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) { if (!/^\s*\|[\s:|-]+\|?\s*$/.test(lines[i])) rows.push(lines[i].trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())); i++; }
        if (rows.length) box.append(h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, rows[0].map((c) => h('th', null, ...inline(c))))),
          h('tbody', null, rows.slice(1).map((r) => h('tr', null, r.map((c) => h('td', null, ...inline(c)))))))));
        continue;
      }
      if (/^\s*(---+|\*\*\*+)\s*$/.test(ln)) { box.append(h('hr')); i++; continue; }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*[-*]\s|\s*\d+[.)]\s|\s*\|)/.test(lines[i])) { para.push(lines[i].trim()); i++; }
      box.append(h('p', null, ...inline(para.join(' '))));
    }
    return box;
  }

  function stepList(steps, running) {
    return h('ul', { class: 'steps', 'aria-live': running ? 'polite' : null }, (steps || []).map((s) =>
      h('li', { class: 'step ' + s.state }, h('span', { class: 'mark', 'aria-hidden': 'true', text: s.state === 'done' ? '✓' : s.state === 'failed' ? '✗' : s.state === 'skipped' ? '–' : '' }),
        s.state === 'running' ? h('span', { class: 'spinner', 'aria-hidden': 'true' }) : null,
        h('span', { class: 'grow' }, h('span', { text: s.label }), s.detail ? h('span', { class: 'muted small', text: ' · ' + s.detail }) : null))));
  }

  // ------------------------------------------------------------------ the page
  registerPage('chat', 'Chat', async (box) => {
    if (!S.paper) {
      box.append(h('div', { class: 'welcome' }, h('h1', { text: 'Add your first paper' }),
        h('p', { text: 'No Overleaf papers are registered yet. In Settings you can see how to add one.' }), h('a', { href: '#/settings', text: 'Open Settings' })));
      return;
    }
    if (!S.threads.length) await window.OLS.loadThreads();
    const paper = S.paper;
    const data = await api.get(`/api/chat?paper=${enc(paper)}&id=${enc(S.chatId || '')}`);
    S.chatId = data.id;
    const st = { data, paper, cid: data.id, messages: data.thread.messages, local: [], working: null, busy: false, cache: new Map(), seen: new Set(), workEl: null, work: null, welcomeEl: null, welcomeSig: '', stuck: true };
    const threadEl = h('div', { class: 'thread', role: 'log', 'aria-label': 'Conversation' });
    const sub = h('p', { class: 'muted small' });
    const alive = () => document.body.contains(threadEl) && S.paper === paper && S.chatId === st.cid;
    // The conversation follows new content only while the reader is at the bottom; scroll up to read and it stays where you left it.
    const nearBottom = () => threadEl.scrollHeight - threadEl.scrollTop - threadEl.clientHeight < 90;
    threadEl.addEventListener('scroll', () => { st.stuck = nearBottom(); }, { passive: true });
    const follow = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { if (st.stuck && alive()) threadEl.scrollTop = threadEl.scrollHeight; }) : null;   // a bubble that grows after it was drawn
    if (follow) follow.observe(threadEl);

    // ---- header
    const catchUp = (into) => guarded(h('button', { type: 'button', class: 'primary', text: 'Get the latest from Overleaf, then send again' }), async () => {
      const r = await api.post('/api/papers/catchup', { name: paper, message: 'Writing assistant: update' });
      let out = into.querySelector(':scope > .catch-out');                 // one result at a time: pressing the button again replaces the last answer
      if (!out) { out = h('div', { class: 'stack catch-out' }); into.append(out); }
      clear(out);
      out.append(gitNotice(r.pull));
      if (r.conflict) out.append(conflictPanel(r.conflict));
      if (r.conflict_error) out.append(notice('warn', r.conflict_error));
      if (r.push) out.append(gitNotice(r.push));
      loadOverview();
      await window.OLS.loadState();
      if (window.OLS.panes && window.OLS.panes.refreshText) window.OLS.panes.refreshText();
      if (r.push && r.push.sent && window.OLS.panes && window.OLS.panes.afterSend) window.OLS.panes.afterSend(r.push);
    });
    /* Overleaf and this computer changed the same lines: every colliding place with both versions side by side, a choice for each, and a way to hand it to Claude Code.
       Nothing is changed until "Join" is pressed with every place chosen; what was not chosen stays in Overleaf's history and in this computer's. */
    const conflictPanel = (c) => {
      const picks = new Map();
      const groups = [];                                                   // [{id, inputs: {value: input}}]
      const total = c.files.reduce((n, f) => n + f.blocks.length, 0) + c.whole_files.length;
      const left = h('span', { class: 'muted small' });
      const status = () => { left.textContent = picks.size >= total ? `All ${plural(total, 'place')} chosen.` : `${picks.size} of ${plural(total, 'place')} chosen.`; };
      const setAll = (value) => groups.forEach((g) => { if (g.inputs[value]) { g.inputs[value].checked = true; picks.set(g.id, value); } else if (g.inputs.mine) { g.inputs.mine.checked = true; picks.set(g.id, 'mine'); } status(); });
      const card = (id, title, body, whole) => {
        const inputs = {};
        const opt = (value, label) => {
          const input = h('input', { type: 'radio', name: 'pick-' + id, value });
          input.addEventListener('change', () => { picks.set(id, value); status(); });
          inputs[value] = input;
          return h('label', { class: 'row tight pick-opt' }, input, ' ' + label);
        };
        groups.push({ id, inputs });
        return h('div', { class: 'hunk place' }, h('header', null, h('div', { class: 'grow small', text: title })), body,
          h('div', { class: 'choices row' }, opt('overleaf', 'Use Overleaf\'s'), opt('mine', 'Use mine'), whole ? null : opt('both', 'Keep both (Overleaf\'s, then mine)')));
      };
      const ctx = (t) => (t ? h('div', { class: 'ctx', text: t }) : null);
      const cards = [];
      c.files.forEach((f) => f.blocks.forEach((b, i) => cards.push(card(b.id, `${f.file} · place ${i + 1} of ${f.blocks.length}`, h('div', null, ctx(b.before),
        h('div', { class: 'sides' }, h('div', { class: 'side' }, h('h4', { text: 'Overleaf has' }), diffSpans(b.diff, 'old')), h('div', { class: 'side' }, h('h4', { text: 'You have (saved here)' }), diffSpans(b.diff, 'new'))), ctx(b.after))))));
      c.whole_files.forEach((w) => cards.push(card(w.id, w.file, h('p', { class: 'small secondary ctx', text: w.why }), true)));
      const panel = h('div', { class: 'conflict stack' });
      const answer = h('div', { class: 'stack' });
      const joined = guarded(h('button', { type: 'button', class: 'primary', text: 'Join with my choices and send' }), async () => {
        if (picks.size < total) throw new Error(`Choose for every place first (${total - picks.size} left).`);
        const r = await api.post('/api/papers/resolve', { name: paper, choices: Object.fromEntries(picks), message: 'Writing assistant: update' });
        clear(answer).append(gitNotice(r.push));
        loadOverview();
        await window.OLS.loadState();
        if (window.OLS.panes && window.OLS.panes.refreshText) window.OLS.panes.refreshText();
        if (r.push && r.push.sent) { cards.forEach((x) => x.remove()); if (window.OLS.panes && window.OLS.panes.afterSend) window.OLS.panes.afterSend(r.push); }
      });
      const claude = guarded(h('button', { type: 'button', text: 'Ask Claude Code to join them' }), async () => {
        const r = await api.post('/api/papers/conflict/handoff', { name: paper });
        clear(answer).append(notice('ok', h('strong', { text: 'Handed to Claude Code. ' }), 'In your Claude Code terminal say: ', h('em', { text: `"${r.say}"` }), ' It joins the two versions, shows you what it kept, and sends. ',
          h('button', { type: 'button', class: 'small', text: 'Copy that sentence', onclick: () => copyText(r.say, 'The sentence') })));
      });
      claude.title = 'Claude Code runs in your terminal, not inside this app, so the request waits for you to say the sentence there. It finds the details itself.';
      status();
      panel.append(h('p', { class: 'small secondary', text: 'These places were changed in Overleaf and here. For each one, choose what the paper should say. Everything else is joined automatically. What you do not choose stays in Overleaf\'s history and in this computer\'s, so nothing is lost for good.' }),
        h('div', { class: 'row' }, h('button', { type: 'button', class: 'small', text: 'Keep mine everywhere', onclick: () => setAll('mine') }), h('button', { type: 'button', class: 'small', text: 'Keep Overleaf\'s everywhere', onclick: () => setAll('overleaf') }), left),
        ...cards, h('div', { class: 'row' }, joined, claude), answer);
      return panel;
    };
    // Overleaf's editor does not follow a send by itself: the page that is open keeps showing the old text until it is loaded again, and so does the PDF until Recompile
    const sentNote = (r) => {
      const panes = window.OLS.panes;
      if (r.pane && r.pane.reloading) return h('p', { class: 'small muted', text: 'The Overleaf pane is reloading and pressing Recompile. In a few seconds it shows your new text and the PDF.' });
      if (panes && panes.has && panes.has('overleaf')) {
        return h('div', { class: 'row tight' }, h('span', { class: 'small muted', text: 'Overleaf shows a sent change once its page is loaded again.' }),
          h('button', { type: 'button', class: 'small', text: 'Show Overleaf', onclick: () => panes.show('overleaf') }));
      }
      return h('p', { class: 'small muted', text: 'Overleaf shows a sent change once its page is loaded again: reload the Overleaf window.' });
    };
    const gitNotice = (r) => {
      if (r.friendly) return notice('warn', r.friendly, ' ', h('a', { href: '#/settings', text: 'Open Settings' }));
      const box = h('div', { class: 'stack' }, notice(r.level === 'bad' ? 'bad' : r.level === 'warn' ? 'warn' : 'ok', r.summary || r.output));
      if (r.sent && r.level === 'ok') box.append(sentNote(r));
      if (r.needs === 'latest') box.append(h('div', { class: 'row' }, catchUp(box)));
      if (r.output && r.output !== r.summary) box.append(h('details', null, h('summary', { text: 'Details' }), h('pre', { class: 'small payload', text: r.output })));
      return box;
    };
    async function loadOverview() {
      try {
        const o = await api.get('/api/overview?paper=' + enc(paper), { poll: true });
        if (!alive()) return;
        const parts = [o.sentence];
        if (o.last_checked) parts.push('asked Overleaf ' + ago(o.last_checked));
        sub.textContent = parts.join(' · ');
        sub.title = o.pending_count ? o.pending.map((x) => `${x.file} (${x.what})`).join('\n') : '';
      } catch (e) { sub.textContent = ''; }
    }
    const gitBtn = (label, action, body) => guarded(h('button', { type: 'button', text: label }), async () => {
      const r = await api.post('/api/papers/' + action, Object.assign({ name: paper }, body || {}));
      st.local.push({ node: gitNotice(r) });
      renderThread();
      loadOverview();
      await window.OLS.loadState();
      if (window.OLS.panes && window.OLS.panes.refreshText) window.OLS.panes.refreshText();
      if (action === 'sync' && r.sent && window.OLS.panes && window.OLS.panes.afterSend) window.OLS.panes.afterSend(r);
      if (action === 'sync') await reload().catch(() => {});                 // what was saved is now sent: the review comments say so
    });
    loadOverview();
    const head = h('header', { class: 'chat-head' }, h('div', { class: 'grow' }, h('h1', { text: paper }), sub),
      h('div', { class: 'row' }, window.OLS.panes.bar(), gitBtn('Get latest from Overleaf', 'pull'), gitBtn('Send to Overleaf', 'sync', { message: 'Writing assistant: update' }),
        guarded(h('button', { type: 'button', class: 'ghost', text: 'Start over', title: 'Clear this conversation and begin again in the same chat' }), () => startOver()),
        h('button', { type: 'button', class: 'ghost', text: '?', title: 'How this works and what I remember', 'aria-label': 'How this works and what I remember', onclick: () => { st.showHelp = !st.showHelp; st.welcomeEl = null; renderThread(); } })));

    // ---- composer
    const ta = h('textarea', { id: 'composer', rows: '2', 'aria-label': 'Ask for a change', placeholder: 'What should I do? For example: improve the flow of the text before Figure 1 in the methods' });
    drafts.bind(ta, `composer:${paper}:${st.cid}`);                  // what was typed and not sent is still there after a visit to Settings or another page
    const sendBtn = h('button', { type: 'button', class: 'primary', text: 'Send' });
    const chips = h('div', { class: 'row chips' }, data.chips.map((c) => h('button', { type: 'button', class: 'chip', text: c.label, onclick: () => { ta.value = (ta.value.trim() ? ta.value.trim() + ' ' : '') + c.text; ta.dispatchEvent(new Event('input')); ta.focus(); } })));
    const fileInput = h('input', { type: 'file', multiple: true, hidden: true, 'aria-hidden': 'true', tabindex: '-1', accept: '.pdf,.docx,.txt,.md,.markdown,.csv,.tsv,.json,.ipynb,.py,.r,.rmd,.m,.jl,.sql,.sh,.yaml,.yml,.toml,.tex,.bib,.rst,.png,.jpg,.jpeg,.gif,.svg,.webp' });
    const attachBtn = h('button', { type: 'button', class: 'attach', text: 'Add evidence', title: 'Add an article, data, notes, or code as evidence. It may be used to support a draft and is saved with the project.' });
    const outlineBtn = h('button', { type: 'button', class: 'attach', text: 'Add outline', title: 'Add a writing brief or outline. It stays local, appears in Your files as an author outline, is followed only when named in a request, and is never cited or sent to Overleaf.' });
    const adding = h('div', { class: 'row chips adding', role: 'status', 'aria-live': 'polite', hidden: true });
    const REQUEST_KINDS = [['edit', 'Improve or correct text that is already written'], ['draft', 'Write new text'], ['cite', 'Add in-text citations to text that is already written'],
      ['refsearch', 'Find references on the internet'], ['layout', 'Change the LaTeX itself (tables, figures, graphs, the bibliography command, packages, appendix, page layout)'], ['library', 'Answer from my reading (my literature library)'],
      ['inspect', 'Count or list what is in my paper\'s files (references, figures, tables)'], ['bibliography', 'Bring my library into the bibliography (reference entries with keys)'], ['review', 'Peer review']];
    const kindSel = h('select', { id: 'request-kind', class: 'kind-flag', 'aria-label': 'What kind of request is this?', title: 'Leave on Automatic and I read your message and tell you what I understood. Choose a kind to tell me yourself.' },
      h('option', { value: '', text: 'Automatic: you read it' }), REQUEST_KINDS.map(([v, l]) => h('option', { value: v, text: l })));
    const composer = h('form', { class: 'composer' }, chips, adding, h('div', { class: 'row top' }, attachBtn, outlineBtn, ta, sendBtn), h('div', { class: 'row small kind-row' }, h('label', { for: 'request-kind', class: 'secondary', text: 'This is a request to:' }), kindSel), fileInput);
    const setBusy = (b) => { st.busy = b; sendBtn.disabled = b; sendBtn.textContent = b ? 'Working...' : 'Send'; renderDock(); };

    async function reload() {
      const d = await api.get(`/api/chat?paper=${enc(paper)}&id=${enc(st.cid)}`);
      st.messages = d.thread.messages; st.data = d;
      if (alive()) { renderThread(); await refreshCards(); }
      return d;
    }
    // A suggestion's card is drawn once; when its suggestion is saved, set aside or sent (here, from the QuillBot panel, or in Claude Code) the card is drawn again from the
    // saved state, but only when that state changed, so ticks the author is making in another card are never lost.
    st.cards = new Map();
    async function refreshCards() {
      for (const [id, c] of Array.from(st.cards)) {
        if (!c.holder.isConnected || c.status === 'rejected' || c.status === 'failed' || c.status === 'no_changes' || (c.status === 'applied' && c.sent)) continue;
        try {
          const fresh = await api.get(`/api/proposal?paper=${enc(paper)}&id=${enc(id)}`);
          if (fresh.status !== c.status || !!fresh.sent_at !== c.sent) renderSuggestion(clear(c.holder), fresh, c.m);
        } catch (e) { /* the card keeps what it shows */ }
      }
    }

    async function send(text, extra) {                          // -> true when the request went, false when it did not (the caller can then give the text back)
      if (st.busy) return false;
      st.stuck = true;
      setBusy(true);
      let went = false;
      try {
        const r = await api.post('/api/chat/send', Object.assign({ paper, id: st.cid, text }, extra || {}));
        went = true;
        await reload();
        await window.OLS.loadThreads();
        if (r.job) await watch(r.job);
        else if (window.OLS.panes && window.OLS.panes.setTargetFromThread) window.OLS.panes.setTargetFromThread(st.messages);
      } catch (e) { toast(e.message); }
      finally { setBusy(false); }
      return went;
    }
    // ---- adding files: from the button or by dropping them on the chat. Each one is saved, read into parts and (unless switched off) sent to Overleaf on its own.
    let uploadPurpose = 'evidence';
    async function addFiles(list, purpose) {
      const picked = Array.from(list || []);
      if (!picked.length) return;
      for (const f of picked) {
        const chip = h('span', { class: 'chip static', text: `Adding ${f.name}...` });
        adding.hidden = false; adding.append(chip);
        try {
          const r = await window.OLS.files.upload(paper, f, { chat: st.cid, purpose });
          chip.textContent = `${f.name}: ${r.saved.outline ? 'author outline' : (r.file && r.file.parts ? plural(r.file.parts, 'part') : 'saved')}`;
          if (r.push && !r.push.sent) st.local.push({ node: gitNotice(r.push) });          // a refusal by Overleaf comes with its own button
          if (r.saved.warning) toast(r.saved.warning);
        } catch (e) {
          chip.textContent = `${f.name}: not added`; chip.classList.add('bad');
          st.local.push({ node: notice('bad', h('strong', { text: `${f.name} was not added. ` }), e.message) });
        }
        setTimeout(() => { chip.remove(); if (!adding.childNodes.length) adding.hidden = true; }, 6000);
        await reload().catch(() => {});
        loadOverview();
      }
    }
    attachBtn.addEventListener('click', () => { uploadPurpose = 'evidence'; fileInput.click(); });
    outlineBtn.addEventListener('click', () => { uploadPurpose = 'outline'; fileInput.click(); });
    fileInput.addEventListener('change', () => { addFiles(fileInput.files, uploadPurpose); fileInput.value = ''; });
    const submit = async () => {
      const t = ta.value.trim();
      if (!t) return;
      if (st.busy) { toast('A request is still running. Your text is kept: send it when the answer is in.'); return; }
      ta.value = ''; drafts.clear(`composer:${paper}:${st.cid}`);
      const flag = kindSel.value;
      kindSel.value = '';                                         // the kind chosen is for this request only
      if (!(await send(t, flag ? { flag } : undefined))) {
        kindSel.value = flag;
        if (!ta.value.trim()) { ta.value = t; ta.dispatchEvent(new Event('input')); }          // it did not go (the connection, a refusal): the text is not lost
      }
    };
    sendBtn.addEventListener('click', submit);
    composer.addEventListener('submit', (e) => { e.preventDefault(); submit(); });
    ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } });

    // ---- the pass in progress: one calm line, and the steps behind a toggle. Nothing is rebuilt while it works; the line and the rows change in place.
    const stepsKey = (steps) => (steps || []).map((x) => `${x.key}|${x.state}|${x.label}|${x.detail || ''}`).join('\n');
    function workingBubble() {
      if (st.workEl) return st.workEl;
      const line = h('span', { class: 'grow work-text', text: 'Working on it...' });
      const list = h('ul', { class: 'steps' });
      const det = h('details', { class: 'work-steps' }, h('summary', { text: 'What I am doing' }), list);
      det.open = window.OLS.store.get('ols-show-steps') === '1';          // remembered: open it once and it stays open for the next requests
      det.addEventListener('toggle', () => { window.OLS.store.set('ols-show-steps', det.open ? '1' : '0'); if (st.stuck) threadEl.scrollTop = threadEl.scrollHeight; });
      st.workEl = assistant(h('div', { class: 'row tight work-line', role: 'status', 'aria-live': 'polite' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), line), det);
      st.work = { line, list, rows: new Map(), key: '' };
      return st.workEl;
    }
    function updateWorking(steps) {
      const w = st.work;
      if (!w || !steps) return;
      const key = stepsKey(steps);
      if (key === w.key) return;                                          // nothing new: touch nothing
      w.key = key;
      const cur = steps.find((x) => x.state === 'running') || steps[steps.length - 1];
      const text = cur ? cur.label + (cur.detail ? ' · ' + cur.detail : '') : 'Working on it...';
      if (w.line.textContent !== text) w.line.textContent = text;
      const ids = new Set(steps.map((x, i) => x.key || String(i)));
      Array.from(w.rows.keys()).forEach((id) => { if (!ids.has(id)) { w.rows.get(id).li.remove(); w.rows.delete(id); } });    // the first 'Starting' row goes once the real steps arrive
      steps.forEach((x, i) => {
        const id = x.key || String(i);
        let row = w.rows.get(id);
        if (!row) {
          row = { li: h('li', { class: 'step' }), mark: h('span', { class: 'mark', 'aria-hidden': 'true' }), spin: h('span', { class: 'spinner', 'aria-hidden': 'true' }), txt: h('span', { class: 'grow' }), state: '', text: '' };
          row.li.append(row.mark, row.spin, row.txt);
          w.rows.set(id, row);
          w.list.append(row.li);
        }
        if (row.state !== x.state) {
          row.state = x.state;
          row.li.className = 'step ' + x.state;
          row.mark.textContent = x.state === 'done' ? '✓' : x.state === 'failed' ? '✗' : x.state === 'skipped' ? '–' : '';
        }
        const t = x.label + (x.detail ? ' · ' + x.detail : '');
        if (row.text !== t) { row.text = t; row.txt.textContent = t; }
      });
      if (st.stuck) threadEl.scrollTop = threadEl.scrollHeight;
    }
    async function watch(jobId) {
      st.working = { steps: [{ key: 'start', label: 'Starting', state: 'running', detail: '' }] };
      st.workEl = null; st.work = null;
      renderThread();
      updateWorking(st.working.steps);
      setBusy(true);
      try {
        await waitJob(jobId, (j) => { if (j.steps && alive()) { st.working.steps = j.steps; updateWorking(j.steps); } });
      } catch (e) { /* the failure is already in the chat as a message */ }
      finally {
        st.working = null; st.workEl = null; st.work = null; setBusy(false);
        await reload().catch(() => {});
        if (window.OLS.panes && window.OLS.panes.setTargetFromThread) window.OLS.panes.setTargetFromThread(st.messages);
      }
    }

    // ---- bubbles
    const assistant = (...kids) => h('div', { class: 'msg assistant' }, h('div', { class: 'who', 'aria-hidden': 'true', text: 'A' }), h('div', { class: 'bubble' }, ...kids));
    const textBlock = (t) => h('div', { class: 'prose', text: t });

    function planBubble(m) {
      const d = m.data, pl = d.plan;
      const w = pl.passages.reduce((n, x) => n + (x.words || 0), 0);
      const lines = [h('p', null, 'I will work on ', h('strong', { text: d.title }), ` (${plural(pl.units, 'passage')}, about ${words(w)} words).`)];
      if (d.notes && d.notes.length) lines.push(h('p', { class: 'small secondary', text: d.notes.join(' ') }));
      lines.push(h('p', { class: 'small secondary', text: (pl.tips ? `I will add ${plural(pl.tips, 'tip')} from your writing book. ` : 'No book advice this time. ') +
        `About ${fmtTok(pl.est_tokens_in)} tokens go to ${pl.model}${pl.est_cost != null ? ', roughly ' + pl.est_cost.toFixed(4) + ' in your price units' : ''}. Your citations, formulas and numbers stay behind as tags.` }));
      const pics = pl.pictures || [];
      const src = (pl.sources || []).filter((x) => !x.picture), outline = pl.outline || [];
      if (src.length) {
        const names = [...new Set(src.map((x) => x.file.split('/').pop()))];
        lines.push(h('p', { class: 'small secondary', text: `${plural(src.length, 'part')} of your files go with it (${names.slice(0, 3).join(', ')}${names.length > 3 ? '...' : ''}), labelled so the answer can say where a number came from. Numbers taken from them are pointed out for you to check.` }));
      }
      if (pics.length) {
        lines.push(h('p', { class: 'small secondary', text: `${plural(pics.length, 'picture')} from your figures go to ${pl.model} with it (${pics.map((x) => x.name).join(', ')}), shrunk first. They are used to describe what a plot shows; numbers must come from your tables and code output.` }));
      }
      const lib = pl.library || [];
      if (lib.length) {
        const cites = lib.filter((x) => x.tag).length;
        lines.push(h('p', { class: 'small secondary', text: `${plural(lib.length, 'part')} of your library go with it (${[...new Set(lib.map((x) => x.label))].slice(0, 3).join(', ')}${new Set(lib.map((x) => x.label)).size > 3 ? '...' : ''}). ` +
          (cites ? `${plural(cites, 'source')} can be cited when an excerpt directly supports the claim, because ${cites === 1 ? 'it is' : 'they are'} in your bibliography.` : 'None of them is in your bibliography yet, so none will be cited.') }));
      }
      if (outline.length) lines.push(h('p', { class: 'small secondary', text: `Following your outline: ${outline.map((x) => x.file.split('/').pop() + (x.where && x.where !== 'all of it' ? ' (' + x.where + ')' : '')).join(', ')}. It is an instruction, not a source and will not be cited.` }));
      (pl.warnings || []).forEach((x) => lines.push(notice('warn', x)));
      const exact = h('div');
      const details = h('details', null, h('summary', { text: 'What goes out' }),
        h('ul', { class: 'small' }, pl.passages.map((x) => h('li', { text: `${x.file}, lines ${x.lines[0]} to ${x.lines[1]} · ${x.section} · about ${x.words} words` })),
          src.map((x) => h('li', { text: `From your files: ${x.where} · about ${fmtTok(x.tokens * 0.75)} words` })),
          pics.map((x) => h('li', { text: `Picture: ${x.name} · ${x.kb} KB` })),
          lib.map((x) => h('li', { text: `From your library: ${x.label}, ${x.where} · about ${fmtTok(x.tokens * 0.75)} words` }))),
        h('button', { type: 'button', class: 'small', text: 'Show the exact text', onclick: async (ev) => {
          ev.target.disabled = true;
          const r = await api.post('/api/revise/preview', { paper, instruction: pl.instruction, chunk_ids: pl.chunk_ids, book_id: pl.book });
          exact.append(...r.payloads.map((x) => h('pre', { class: 'payload small', text: requestText(x.request) })));
        } }), exact);
      const foot = h('div');
      if (d.state === 'waiting') {
        foot.append(notice('warn', d.confirm === 'big' ? 'This is bigger than a usual request, so I am checking with you first.' : 'You asked me to check with you before anything is sent.'),
          h('div', { class: 'row' }, guarded(h('button', { type: 'button', class: 'primary', text: 'Send to OpenAI' }), async () => {
            const r = await api.post('/api/chat/run', { paper, id: st.cid, message: m.id });
            await reload(); await watch(r.job);
          }), guarded(h('button', { type: 'button', text: 'Cancel' }), async () => { await api.post('/api/chat/cancel', { paper, id: st.cid, message: m.id }); await reload(); })));
      } else {
        foot.append(h('p', { class: 'muted small', text: { sent: 'Sent to OpenAI.', failed: 'It could not be completed.', cancelled: 'Cancelled, nothing was sent.' }[d.state] || '' }));
      }
      return assistant(...lines, details, foot);
    }

    // ---- new text written from the author's own sources (analysis, notes, library): what it will rest on, and where it will go
    function draftPlanBubble(m) {
      const d = m.data, pl = d.plan;
      const src = (pl.sources || []).filter((x) => !x.picture), outline = pl.outline || [];
      const pics = pl.pictures || [], lib = pl.library || [];
      const lines = [h('p', null, 'I will write new text for the ', h('strong', { text: pl.label.toLowerCase() }), `, ${d.where}, about ${pl.target_words} words or fewer.`)];
      (d.notes || []).forEach((x) => lines.push(h('p', { class: 'small secondary', text: x })));
      const bits = [];
      if (outline.length) bits.push(`your outline (${outline.map((x) => x.file.split('/').pop() + (x.where && x.where !== 'all of it' ? ', ' + x.where : '')).join('; ')})`);
      if (src.length) bits.push(`${plural(src.length, 'part')} of your files (${[...new Set(src.map((x) => x.file.split('/').pop()))].slice(0, 4).join(', ')}${new Set(src.map((x) => x.file)).size > 4 ? '...' : ''})`);
      if (pics.length) bits.push(`${plural(pics.length, 'picture')} (${pics.map((x) => x.name).join(', ')})`);
      if (lib.length) bits.push(`${plural(lib.length, 'part')} of your library`);
      if (pl.tips) bits.push(`${plural(pl.tips, 'tip')} from your writing book`);
      lines.push(h('p', { class: 'small secondary', text: (bits.length ? 'It rests on ' + bits.join(', ') + '. ' : 'You have not linked any code, results or notes, so there is nothing for it to rest on yet. ') +
        `Every statement says which source it comes from, and a number that is in none of them is refused. About ${fmtTok(pl.est_tokens_in)} tokens go to ${pl.model}${pl.est_cost != null ? ', roughly ' + pl.est_cost.toFixed(4) + ' in your price units' : ''}.` }));
      if (!src.length && !lib.length) lines.push(notice('warn', 'Link the folder with your code and results (or your notes) in ', h('a', { href: '#/settings', text: 'Settings' }), ' so the text can rest on them. Without sources, it can only reorganise what you tell me.'));
      if (lib.length) {
        const cites = lib.filter((x) => x.tag).length;
        lines.push(h('p', { class: 'small secondary', text: cites ? `${plural(cites, 'library source')} can be cited where its retrieved excerpt supports the statement.` : 'The retrieved library excerpts can inform the wording, but none can be cited until it is available in this paper’s bibliography.' }));
      }
      (pl.warnings || []).forEach((x) => lines.push(notice('warn', x)));
      const exact = h('div');
      const details = h('details', null, h('summary', { text: 'What goes out' }),
        h('ul', { class: 'small' }, outline.map((x) => h('li', { text: `Author outline: ${x.file.split('/').pop()} · ${x.where}` })), src.map((x) => h('li', { text: `From your files: ${x.file.split('/').pop()} · ${(x.where || '').split(' · ').slice(1).join(' · ')} · about ${fmtTok(x.tokens * 0.75)} words` })),
          pics.map((x) => h('li', { text: `Picture: ${x.name} · ${x.kb} KB` })), lib.map((x) => h('li', { text: `From your library: ${x.label}, ${x.where}` }))),
        h('button', { type: 'button', class: 'small', text: 'Show the exact text', onclick: (ev) => { ev.target.disabled = true; exact.append(h('pre', { class: 'payload small', text: requestText(pl.payload) })); } }), exact);
      const foot = h('div');
      if (d.state === 'waiting') {
        foot.append(notice('warn', d.confirm === 'citations' ? 'You required in-text citations. Review the proposed literature below before this is sent.' : 'You asked me to check with you before anything is sent.'),
          h('div', { class: 'row' }, guarded(h('button', { type: 'button', class: 'primary', text: 'Send to OpenAI' }), async () => {
            const r = await api.post('/api/chat/run', { paper, id: st.cid, message: m.id });
            await reload(); await watch(r.job);
          }), guarded(h('button', { type: 'button', text: 'Cancel' }), async () => { await api.post('/api/chat/cancel', { paper, id: st.cid, message: m.id }); await reload(); })));
      } else {
        foot.append(h('p', { class: 'muted small', text: { sent: 'Sent to OpenAI.', failed: 'It could not be completed.', cancelled: 'Cancelled, nothing was sent.' }[d.state] || '' }));
      }
      return assistant(...lines, details, foot);
    }

    function layoutPlanBubble(m) {
      const d = m.data, pl = d.plan, ts = pl.targets || [];
      const what = ts.length ? ts.map((t) => `${t.kind} ${t.n}${t.caption ? ' (' + t.caption.slice(0, 50) + (t.caption.length > 50 ? '...' : '') + ')' : ''}`).join(', ') : 'the settings of the document';
      const lines = [h('p', null, 'I will change the form of ', h('strong', { text: what }), '. I do not change what the paper says.'),
        h('p', { class: 'small secondary', text: `The LaTeX of ${ts.length ? 'that part' : 'the preamble'}, the lines that mention it and the outline of your document go to ${pl.model} (about ${fmtTok(pl.est_tokens_in)} tokens${pl.est_cost != null ? ', roughly ' + pl.est_cost.toFixed(4) + ' in your price units' : ''}). `
          + 'Before anything can be saved I check, on this computer, that labels and references still match, citations and the words of tables and figures are unchanged, nothing unsafe is added, and the paper still compiles.' })];
      (pl.notes || []).forEach((x) => lines.push(notice('warn', x)));
      const exact = h('div');
      const details = h('details', null, h('summary', { text: 'What goes out' }),
        h('ul', { class: 'small' }, ts.map((t) => h('li', { text: `${t.kind} ${t.n}: ${t.file}, lines ${t.lines[0]} to ${t.lines[1]}${t.cut ? ' (only its start)' : ''}` })), h('li', { text: 'The outline of the document, its preamble and the end of the document' })),
        h('button', { type: 'button', class: 'small', text: 'Show the exact text', onclick: (ev) => { ev.target.disabled = true; exact.append(h('pre', { class: 'payload small', text: requestText(pl.payload) })); } }), exact);
      const foot = h('div');
      if (d.state === 'waiting') {
        foot.append(notice('warn', 'You asked me to check with you before anything is sent.'),
          h('div', { class: 'row' }, guarded(h('button', { type: 'button', class: 'primary', text: 'Send to OpenAI' }), async () => {
            const r = await api.post('/api/chat/run', { paper, id: st.cid, message: m.id });
            await reload(); await watch(r.job);
          }), guarded(h('button', { type: 'button', text: 'Cancel' }), async () => { await api.post('/api/chat/cancel', { paper, id: st.cid, message: m.id }); await reload(); })));
      } else {
        foot.append(h('p', { class: 'muted small', text: { sent: 'Sent to OpenAI.', failed: 'It could not be completed.', cancelled: 'Cancelled, nothing was sent.' }[d.state] || '' }));
      }
      return assistant(...lines, details, foot);
    }

    function renderLayout(holder, p, m) {
      clear(holder);
      const locked = p.status === 'applied' || p.status === 'rejected' || p.status === 'superseded';
      const usage = p.usage || {};
      const notices = h('div', { class: 'stack' });
      const redraw = async () => { const fresh = await api.get(`/api/proposal?paper=${enc(paper)}&id=${enc(p.id)}`); renderLayout(clear(holder), fresh, m); };
      st.cards.set(p.id, { holder, m, status: p.status, sent: !!p.sent_at });
      holder.append(h('div', { class: 'row small secondary' }, h('span', { text: `${p.model} · ${fmtTok(usage.input_tokens)} in / ${fmtTok(usage.output_tokens)} out${usage.cost != null ? ' · cost ' + usage.cost : ''}` }),
        p.status === 'applied' ? h('span', { class: 'badge ok', text: 'saved in your files' }) : p.status === 'rejected' ? h('span', { class: 'badge', text: 'set aside' }) : null));
      if (m.data.steps && m.data.steps.length) holder.append(h('details', null, h('summary', { text: 'What I did' }), stepList(m.data.steps)));
      if (p.status === 'failed') {
        holder.append(notice('bad', h('strong', { text: 'I did not use the change OpenAI proposed. ' }), p.error_plain || p.error || ''));
        return;
      }
      if (p.approach) holder.append(h('div', { class: 'approach' }, h('strong', { text: 'What changes and why' }), h('p', { text: p.approach })));
      (p.questions || []).forEach((q) => holder.append(notice('warn', h('strong', { text: 'OpenAI asks: ' }), q)));
      if (p.status === 'no_changes') {
        holder.append(notice('ok', 'OpenAI proposed no change.'));
        return;
      }
      const bad = (p.validators || []).filter((v) => !v.ok && v.detail);
      if (bad.length) holder.append(notice('warn', 'Heads up: ', bad.map((v) => v.detail).join('; ')));
      else if ((p.validators || []).some((v) => v.name === 'compiles' && v.ok)) holder.append(notice('ok', 'Checked: labels, references, citations and the words of tables and figures are unchanged, and the paper still compiles with the change.'));
      else holder.append(notice('ok', 'Checked: labels, references, citations and the words of tables and figures are unchanged.'));
      const files = [...new Set((p.edits || []).map((e) => e.file))];
      holder.append(h('p', { class: 'small secondary', text: `${plural((p.edits || []).length, 'change')} in ${files.join(', ')}. They are saved together or not at all: a move is a removal and an insertion, and one without the other would lose the table.` }));
      (p.edits || []).forEach((e, i) => holder.append(h('div', { class: 'hunk layout-edit' },
        h('div', { class: 'small' }, h('strong', { text: `${i + 1}. ` }), e.reason || 'A change', h('span', { class: 'secondary', text: `  ${e.file}${e.line_start ? ', line ' + e.line_start : ''}` })),
        h('div', { class: 'small secondary', text: 'Now' }), h('pre', { class: 'payload small', text: e.find || '(nothing: new text is added here)' }),
        h('div', { class: 'small secondary', text: 'After' }), h('pre', { class: 'payload small', text: e.replace || '(removed)' }))));
      holder.append(notices);
      if (!locked) {
        holder.append(h('div', { class: 'row actions' },
          guarded(h('button', { type: 'button', class: 'primary', text: 'Apply and save in my files' }), async () => { await doApprove(notices, p, { push: false }, redraw); }),
          guarded(h('button', { type: 'button', text: 'Apply, save and send to Overleaf' }), async () => { await doApprove(notices, p, { push: true }, redraw); }),
          guarded(h('button', { type: 'button', class: 'ghost', text: 'Not now' }), async () => { await api.post('/api/proposal/record', { paper, id: p.id, accepted: [], rejected: 'all', chat: st.cid }); await redraw(); await reload(); }),
          h('span', { class: 'grow' })));
        holder.append(h('p', { class: 'small muted', text: 'After saving, look at the PDF (the LaTeX pane) to see how it looks; if you do not like it, ask again in your own words, or restore the file from Overleaf\'s history.' }));
      }
    }

    function askBubble(m) {
      const d = m.data;
      const sections = h('div', { class: 'row chips' }, (d.sections || []).map((s) => h('button', { type: 'button', class: 'chip', text: s.label,
        onclick: () => send(d.original || 'Improve this.', { chunk_ids: s.chunk_ids }) })));
      const picker = h('div', { hidden: true, class: 'picker' });
      const toggle = h('button', { type: 'button', class: 'small', text: 'Pick paragraphs myself', onclick: () => { picker.hidden = !picker.hidden; if (!picker.childNodes.length) buildPicker(picker, d); } });
      return assistant(textBlock(m.text), (d.notes || []).map((x) => h('p', { class: 'small secondary', text: x })), sections, h('div', { class: 'row' }, toggle), picker);
    }

    function buildPicker(el, d) {
      const checks = new Map();
      const status = h('p', { class: 'small' });
      const useBtn = h('button', { type: 'button', class: 'primary', text: 'Use these' });
      const filter = h('input', { type: 'search', 'aria-label': 'Filter', placeholder: 'Filter by words', class: 'grow' });
      const list = h('div', { class: 'pick-list' });
      let timer = null;
      const ticked = () => [...checks.entries()].filter(([, c]) => c.checked).map(([id]) => id);
      const refresh = () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          const ids = ticked();
          if (!ids.length) { status.textContent = 'Tick the pieces you want improved.'; useBtn.disabled = true; return; }
          try {
            const c = await api.post('/api/chat/count', { paper, chunk_ids: ids });
            status.textContent = `${plural(c.pieces, 'piece')} · ${plural(c.units, 'separate passage')} · about ${words(c.tokens * 0.75)} words` + (c.ok ? '' : ` · too many for one request (at most ${c.limit})`);
            useBtn.disabled = !c.ok;
          } catch (e) { status.textContent = e.message; }
        }, 150);
      };
      const inPart = new Set(d.in_part && d.in_part.length ? d.in_part : []);
      let lastSection = null;
      for (const o of d.options) {
        if (inPart.size && !inPart.has(o.id) && !(d.preselect || []).includes(o.id)) continue;
        const top = o.section.split(' > ')[0];
        if (top !== lastSection) { list.append(h('h4', { text: o.section.split(' > ').slice(0, 2).join(' › ') })); lastSection = top; }
        const cb = h('input', { type: 'checkbox', checked: (d.preselect || []).includes(o.id), 'aria-label': o.preview.slice(0, 60) });
        cb.addEventListener('change', refresh);
        checks.set(o.id, cb);
        list.append(h('label', { class: 'pick', 'data-text': (o.section + ' ' + o.preview).toLowerCase() }, cb, h('span', { class: 'grow' }, h('span', { text: o.preview + (o.preview.length >= 150 ? '…' : '') }),
          h('span', { class: 'muted small', text: `  lines ${o.lines[0]} to ${o.lines[1]}, ~${words(o.tokens * 0.75)} words` }))));
      }
      filter.addEventListener('input', () => { const q = filter.value.toLowerCase(); list.querySelectorAll('.pick').forEach((l) => { l.hidden = !!q && !l.dataset.text.includes(q); }); });
      useBtn.addEventListener('click', () => send(d.original || 'Improve this.', { chunk_ids: ticked() }));
      el.append(h('div', { class: 'row' }, filter), list, status, h('div', { class: 'row' }, useBtn));
      refresh();
    }

    // ---- peer review
    function reviewPlanBubble(m) {
      const d = m.data, pl = d.plan;
      const foot = h('div');
      if (d.state === 'waiting') {
        foot.append(h('div', { class: 'row' }, guarded(h('button', { type: 'button', class: 'primary', text: 'Run the review' }), async () => {
          const r = await api.post('/api/chat/run', { paper, id: st.cid, message: m.id });
          await reload(); await watch(r.job);
        }), guarded(h('button', { type: 'button', text: 'Cancel' }), async () => { await api.post('/api/chat/cancel', { paper, id: st.cid, message: m.id }); await reload(); })));
      } else foot.append(h('p', { class: 'muted small', text: { sent: 'Review started.', failed: 'The review could not be completed.', cancelled: 'Cancelled, nothing was sent.' }[d.state] || '' }));
      return assistant(h('p', null, 'I can peer review ', h('strong', { text: pl.scope }), '.'),
        h('p', { class: 'small secondary', text: `A review reads far more of your paper at once than an edit does (about ${fmtTok(pl.tokens * 0.75)} words go to ${pl.model}${pl.est_cost != null ? ', roughly ' + pl.est_cost.toFixed(4) + ' in your price units' : ''}), so I always check with you first. Nothing in your paper is changed; the report is saved in the paper's notes folder.` }), foot);
    }

    // The comments of a peer review: where each one stands (not tried, working, waiting for the author, completed, set aside...), in the review itself and in a
    // panel above the composer, so after every decision the author sees what is left without scrolling back up.
    const HANDLED = new Set(['saved', 'sent', 'rejected', 'nothing', 'skipped']);
    const STATE = {
      asking: ['warn', '●', 'Waiting for your go-ahead'], working: ['work', '', 'Working on it...'], decide: ['warn', '●', 'Suggestion ready: your decision'],
      saved: ['ok', '✓', 'Completed · saved here, not in Overleaf yet'], sent: ['ok', '✓', 'Completed · sent to Overleaf'],
      rejected: ['off', '✗', 'Set aside'], nothing: ['off', '–', 'OpenAI would change nothing here'], skipped: ['off', '–', 'Skipped'], failed: ['bad', '!', 'Could not finish'],
    };
    const slotsOf = (mid) => (st.data.issues || {})[mid] || [];
    const tally = (slots) => {
      const by = {};
      slots.forEach((x) => { by[x.state] = (by[x.state] || 0) + 1; });
      return { total: slots.length, handled: slots.filter((x) => HANDLED.has(x.state)).length, done: (by.saved || 0) + (by.sent || 0), aside: (by.rejected || 0) + (by.skipped || 0) + (by.nothing || 0), unsent: by.saved || 0 };
    };
    function showMessage(id) {                                          // bring the suggestion for a comment into view and mark it for a moment
      const hit = st.cache.get(id);
      if (!hit) return;
      st.stuck = false;
      // only the conversation moves: scrollIntoView would also slide the whole page frame up, and the buttons at the top with it
      threadEl.scrollTo({ top: Math.max(0, threadEl.scrollTop + hit.node.getBoundingClientRect().top - threadEl.getBoundingClientRect().top - 8), behavior: 'smooth' });
      hit.node.classList.add('spot');
      setTimeout(() => hit.node.classList.remove('spot'), 2400);
    }
    function fixIssue(m, i) {
      if (st.busy) { toast('A request is still running. Wait for it to finish, then press the button again.'); return; }
      const it = m.data.issues[i];
      return send(`Address this reviewer comment${it.where ? ' about ' + it.where : ''}: ${it.problem} ${it.suggestion || ''}`.trim(), { chunk_ids: it.chunk_ids, issue: { review: m.id, index: i } });
    }
    async function issueAction(m, i, action) {
      try { await api.post('/api/chat/issue', { paper, id: st.cid, message: m.id, index: i, action }); await reload(); } catch (e) { toast(e.message); }
    }
    function issueRow(m, i, compact) {
      const it = m.data.issues[i], s = slotsOf(m.id)[i] || { state: 'open' };
      const sev = { major: 'bad', minor: 'warn', suggestion: '' };
      const info = STATE[s.state];
      const chip = info ? h('span', { class: 'state ' + info[0], role: 'status' }, info[0] === 'work' ? h('span', { class: 'spinner', 'aria-hidden': 'true' }) : h('span', { class: 'mark', 'aria-hidden': 'true', text: info[1] }), h('span', { text: info[2] })) : null;
      const can = !!(it.chunk_ids && it.chunk_ids.length);
      const acts = [];
      if (s.state === 'open' || s.state === 'failed') {
        if (it.chunk_ids && it.chunk_ids.length) acts.push(h('button', { type: 'button', class: 'small' + (s.state === 'failed' ? '' : ' primary'), disabled: !can, text: s.state === 'failed' ? 'Try again' : 'Fix this', onclick: () => fixIssue(m, i) }));
        acts.push(h('button', { type: 'button', class: 'small ghost', title: 'This comment needs no change', text: 'Skip', onclick: () => issueAction(m, i, 'skip') }));
      } else if (s.state === 'rejected' || s.state === 'nothing') {
        if (it.chunk_ids && it.chunk_ids.length) acts.push(h('button', { type: 'button', class: 'small', disabled: !can, text: 'Try again', onclick: () => fixIssue(m, i) }));
      } else if ((s.state === 'decide' || s.state === 'asking') && s.message) {
        acts.push(h('button', { type: 'button', class: 'small primary', text: 'Show it', onclick: () => showMessage(s.message) }));
      } else if (s.state === 'skipped') {
        acts.push(h('button', { type: 'button', class: 'small ghost', text: 'Bring it back', onclick: () => issueAction(m, i, 'reopen') }));
      }
      return h('li', { class: 'issue st-' + s.state + (compact ? ' compact' : '') },
        h('div', { class: 'row tight' }, h('span', { class: 'badge ' + (sev[it.severity] || ''), text: it.severity }), it.where ? h('strong', { class: 'small', text: it.where }) : null, h('span', { class: 'grow' }), chip),
        compact ? h('div', { class: 'small clamp', title: it.problem, text: it.problem }) : [
          it.quote ? h('div', { class: 'small muted', text: '“' + it.quote + '”' }) : null, h('div', { text: it.problem }), it.suggestion ? h('div', { class: 'small secondary', text: it.suggestion }) : null],
        acts.length ? h('div', { class: 'row tight' }, acts) : null);
    }
    const progressBar = (t) => {
      const fill = h('span');
      fill.style.width = `${t.total ? Math.round(100 * t.handled / t.total) : 0}%`;          // set from script: the page's policy does not allow style attributes
      return h('div', { class: 'progress', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': String(t.total), 'aria-valuenow': String(t.handled), 'aria-label': 'Comments handled' }, fill);
    };

    function reviewBubble(m) {
      const d = m.data;
      const slots = slotsOf(m.id), t = tally(slots);
      const many = (d.issues || []).length >= 2;
      const finished = many && t.total && t.handled === t.total;
      const list = h('ul', { class: 'issues' }, (d.issues || []).map((it, i) => issueRow(m, i, false)));
      const scores = Object.entries(d.scores || {});
      let issuesBlock = null;
      if ((d.issues || []).length) {
        issuesBlock = finished
          ? h('details', null, h('summary', { text: `${plural(t.total, 'comment')} · ${t.done} completed${t.aside ? `, ${t.aside} set aside` : ''}` }), list)
          : h('div', null, h('h4', { text: `Issues to look at (${t.total || d.issues.length})` }),
            many ? h('div', { class: 'row tight' }, progressBar(t), h('span', { class: 'small muted', text: `${t.handled} of ${t.total} handled` })) : null, list);
      }
      return assistant(
        h('div', { class: 'row' }, h('strong', { text: 'Peer review of ' + d.scope }), d.verdict ? h('span', { class: 'badge', text: d.verdict }) : null,
          h('span', { class: 'small secondary', text: `${d.model} · ${fmtTok(d.usage.input_tokens)} in / ${fmtTok(d.usage.output_tokens)} out${d.usage.cost != null ? ' · cost ' + d.usage.cost : ''}` })),
        scores.length ? h('div', { class: 'row chips' }, scores.map(([k, v]) => h('span', { class: 'chip static', text: `${k.replace(/_/g, ' ')}: ${v}` }))) : null,
        issuesBlock,
        h('details', { open: !(d.issues || []).length }, h('summary', { text: 'Full report' }), mdLite(d.report)),
        h('div', { class: 'row' }, h('button', { type: 'button', class: 'small', text: 'Copy the report', onclick: () => copyText(d.report, 'Report') }), h('span', { class: 'small muted', text: 'Saved in ' + d.path })),
        h('p', { class: 'small muted', text: 'Nothing was changed in your paper. "Fix this" asks for an ordinary suggestion that you approve or set aside.' }));
    }

    // When the last comment of a review is handled: the list is done, and the chat asks what comes next.
    function reviewDoneBubble(m) {
      const d = m.data, t = tally(slotsOf(d.review));
      const unsent = slotsOf(d.review).length ? t.unsent : d.unsent;
      const sendBtn = unsent ? guarded(h('button', { type: 'button', class: 'primary', text: `Send ${plural(unsent, 'saved change')} to Overleaf` }), async () => {
        const r = await api.post('/api/papers/sync', { name: paper, message: 'Writing assistant: reviewer comments' });
        st.local.push({ node: gitNotice(r) });
        await reload();
        loadOverview();
        if (r.sent && window.OLS.panes && window.OLS.panes.afterSend) window.OLS.panes.afterSend(r);
      }) : null;
      return assistant(h('div', { class: 'review-done' },
        h('div', { class: 'row tight' }, h('span', { class: 'tick', 'aria-hidden': 'true', text: '✓' }), h('strong', { text: `All ${d.total} reviewer comments are handled` })),
        h('p', { class: 'small secondary', text: `${d.done} completed${d.aside ? `, ${d.aside} set aside or skipped` : ''}.` + (d.done && !unsent ? ' Everything that was saved is in Overleaf.' : '') }),
        sendBtn ? h('div', { class: 'row' }, sendBtn) : null,
        h('p', { text: 'Let\'s continue. What would you like to work on next? Tell me if you need anything else.' })));
    }

    // The panel above the composer while a review with several comments still has some open.
    const dock = h('section', { class: 'review-dock', hidden: true, 'aria-label': 'Reviewer comments still to go through' });
    st.dockOpen = window.OLS.store.get('ols-dock-open') !== '0';
    let dockSig = '';
    function activeReview() {
      for (let k = st.messages.length - 1; k >= 0; k--) {
        const m = st.messages[k];
        if (m.kind !== 'review' || (m.data.issues || []).length < 2) continue;
        const slots = slotsOf(m.id);
        if (!slots.length || slots.every((x) => HANDLED.has(x.state))) continue;
        return m;
      }
      return null;
    }
    function renderDock() {
      const m = activeReview();
      const slots = m ? slotsOf(m.id) : [];
      const showList = st.dockOpen && !st.banner;
      const sig = m ? JSON.stringify([m.id, slots, showList, st.busy]) : '';
      if (sig === dockSig) return;
      dockSig = sig;
      clear(dock);
      dock.hidden = !m;
      if (!m) return;
      const t = tally(slots);
      const nextAt = slots.findIndex((x, i) => (x.state === 'open' || x.state === 'failed') && (m.data.issues[i].chunk_ids || []).length);
      const toggle = h('button', { type: 'button', class: 'small ghost', 'aria-expanded': String(showList), text: showList ? 'Hide the list' : 'Show the list', onclick: () => {
        st.dockOpen = !st.dockOpen; window.OLS.store.set('ols-dock-open', st.dockOpen ? '1' : '0'); renderDock();
      } });
      dock.append(h('div', { class: 'row dock-head' }, h('strong', { text: 'Reviewer comments' }), h('span', { class: 'small muted grow', text: `${t.handled} of ${t.total} handled · ${t.total - t.handled} to go` }),
        nextAt >= 0 ? h('button', { type: 'button', class: 'small primary', disabled: st.busy, text: 'Fix the next one', onclick: () => fixIssue(m, nextAt) }) : null, st.banner ? null : toggle),
      progressBar(t));
      if (showList) dock.append(h('ul', { class: 'issues dock-list' }, m.data.issues.map((it, i) => issueRow(m, i, true))));
    }

    // ---- references
    function refsBubble(m) {
      const d = m.data, added = d.results.filter((r) => r.status === 'added').length;
      const kids = d.results.map((r) => window.OLS.refCard(r, d.push));
      if (d.push && d.push.friendly) kids.push(notice('warn', d.push.friendly, ' ', h('a', { href: '#/settings', text: 'Open Settings' })));
      if (d.pending) kids.push(notice('warn', `${plural(d.pending, 'reference')} still waiting to reach Mendeley. `, h('a', { href: '#/references', text: 'Try again on the References page' })));
      return assistant(h('p', { class: 'small secondary', text: added ? `Added ${plural(added, 'reference')}.` : 'Nothing new was added.' }), ...kids);
    }

    // ---- a look into the library: the best parts, where they are from, and how to cite them
    function libraryBubble(m) {
      const d = m.data, cmd = d.command || '\\cite';
      if (!d.hits.length) return assistant(h('p', { text: `Nothing in your library (${plural(d.total, 'document')}) matches "${d.topic}". Try other words, or check that the folder with that reading is added in Settings.` }));
      const rows = d.hits.map((c) => {
        const out = h('div', { class: 'small' });
        const cite = (key) => `${cmd}{${key}}`;
        const copy = (text) => h('button', { type: 'button', class: 'small', text: 'Copy ' + text, onclick: async () => { try { await navigator.clipboard.writeText(text); toast('Copied'); } catch (e) { toast('Select it and copy by hand'); } } });
        const actions = h('div', { class: 'row tight' });
        if (c.in_bib) actions.append(h('code', { class: 'cite', text: cite(c.cite_key) }), copy(cite(c.cite_key)), h('span', { class: 'small secondary', text: 'already in your bibliography' }));
        else if (c.can_add || c.has_entry) {
          actions.append(guarded(h('button', { type: 'button', class: 'small', text: 'Add to my bibliography' }), async () => {
            const r = await api.post('/api/library/cite', { paper, doc: c.doc });
            clear(actions).append(h('code', { class: 'cite', text: r.cite }), copy(r.cite), h('span', { class: 'small secondary', text: r.message }));
            await window.OLS.loadState();
          }));
        } else actions.append(h('span', { class: 'small secondary', text: 'No DOI and no entry in a .bib file: paste its DOI here to add it to your bibliography.' }));
        return h('div', { class: 'hit' }, h('div', { class: 'row tight' }, h('strong', { text: c.label }), h('span', { class: 'small secondary', text: c.where })),
          c.title && c.title !== c.label ? h('div', { class: 'small secondary', text: c.title }) : null, h('div', { class: 'small', text: c.text }), actions, out);
      });
      return assistant(h('p', { class: 'small secondary', text: `Here is what your library says about "${d.topic}" (${plural(d.hits.length, 'part')} of ${plural(d.docs, 'document')}, searched on this computer; nothing was sent to OpenAI).` }), ...rows,
        h('p', { class: 'small secondary', text: 'To use these in your text, ask for it: for example "add support from my library to the introduction".' }));
    }

    // ---- references found on the internet: tick the ones to add to the bibliography; when they were asked for a text, have that text written again with them
    const SOURCE_NAMES = { web: 'The web search', openalex: 'OpenAlex', crossref: 'Crossref' };
    async function pickRefs(m, dois) {
      if (!dois.length) throw new Error('Tick at least one reference first.');
      if (st.busy) throw new Error('A request is still running. Wait for it to finish.');
      const r = await api.post('/api/chat/refs/pick', { paper, id: st.cid, message: m.id, dois });
      await watch(r.job);
    }
    async function useRefs(ctx, dois) {                                   // the same request as before, now with the abstracts of these references to cite
      const extra = { online: dois, facts: ctx.facts || undefined, issue: ctx.issue || undefined };
      Object.assign(extra, ctx.draft && ctx.revises ? { redraft: ctx.revises } : ctx.draft ? {} : { chunk_ids: ctx.chunk_ids, revises: ctx.revises || undefined });
      await send(ctx.request || 'Support the claims of this text with citations from the sources provided.', extra);
    }
    const ORIGINS = { record: 'from the bibliography record in your library', doi: 'from its DOI', title: 'found at Crossref by its title', file: 'made from the title, authors and year the file gives: check it' };
    function bringBubble(m) {
      const d = m.data, rows = d.rows || [], cmd = d.command || '\\cite', counts = d.counts || {};
      const fresh = rows.filter((r) => r.status === 'new'), have = rows.filter((r) => r.status === 'have'), cannot = rows.filter((r) => r.status === 'cannot');
      const kids = [h('p', { text: `Your library has ${plural(rows.length, 'document')}: ${counts.new || 0} can become reference entries, ${counts.have || 0} are in your bibliography already and ${counts.cannot || 0} cannot be added. ` +
        'Each entry gets a citation key, so a request to write or to cite can use it. Nothing is written until you press the button.' })];
      if (d.state === 'done' && d.result) {
        const r = d.result;
        kids.push(notice('ok', `Added ${plural((r.added || []).length, 'entry', 'entries')} to your bibliography${d.pushed ? ' and sent them to Overleaf' : ''}. Cite one with ${cmd}{key}.`));
        if ((r.added || []).length) kids.push(h('details', { open: true }, h('summary', { class: 'small', text: 'The keys' }),
          h('ul', { class: 'small' }, r.added.map((a) => h('li', null, h('code', { class: 'cite', text: `${cmd}{${a.key}}` }), ' ', a.label)))));
        if ((r.failed || []).length) kids.push(h('p', { class: 'small secondary', text: plural(r.failed.length, 'document') + ' could not be added.' }));
      } else if (d.state === 'working') {
        kids.push(busy('Adding the entries to your bibliography...'));
      } else if (fresh.length) {
        const picks = new Set(fresh.map((r) => r.doc));
        const count = h('span', { class: 'small muted' });
        const sync = () => { count.textContent = `${picks.size} of ${fresh.length} ticked`; };
        const boxes = fresh.map((r) => {
          const box = h('input', { type: 'checkbox', checked: true, 'aria-label': 'Add ' + (r.label || r.title) });
          box.addEventListener('change', () => { if (box.checked) picks.add(r.doc); else picks.delete(r.doc); sync(); });
          return [box, r];
        });
        sync();
        kids.push(h('div', { class: 'stack tight' }, boxes.map(([box, r]) => h('label', { class: 'hit row tight' }, box, h('div', { class: 'stack tight grow' },
          h('div', null, h('strong', { text: r.label || r.title }), ' ', h('code', { class: 'cite', text: `${cmd}{${r.key}}` })),
          h('div', { class: 'small secondary', text: (r.title && r.title !== r.label ? r.title + ' · ' : '') + (ORIGINS[r.origin] || '') }))))));
        kids.push(h('div', { class: 'row actions' },
          guarded(h('button', { type: 'button', class: 'primary', text: 'Add the ticked to my bibliography' }), async () => {
            if (!picks.size) throw new Error('Tick at least one document.');
            if (st.busy) throw new Error('A request is still running. Wait for it to finish.');
            const r = await api.post('/api/library/bring', { paper, id: st.cid, message: m.id, doc_ids: [...picks] });
            await watch(r.job);
          }),
          h('button', { type: 'button', class: 'small', text: 'Tick none', onclick: () => { picks.clear(); boxes.forEach(([b]) => { b.checked = false; }); sync(); } }),
          h('button', { type: 'button', class: 'small', text: 'Tick all', onclick: () => { fresh.forEach((r) => picks.add(r.doc)); boxes.forEach(([b]) => { b.checked = true; }); sync(); } }),
          h('span', { class: 'grow' }), count));
      } else kids.push(notice('ok', 'Nothing to add: every document that can be cited is in your bibliography already.'));
      if (have.length) kids.push(h('details', null, h('summary', { class: 'small', text: `Already in your bibliography: ${have.length}` }),
        h('ul', { class: 'small' }, have.map((r) => h('li', null, h('code', { class: 'cite', text: `${cmd}{${r.key}}` }), ' ', r.label || r.title)))));
      if (cannot.length) kids.push(h('details', null, h('summary', { class: 'small', text: `Cannot be added: ${cannot.length}` }),
        h('ul', { class: 'small' }, cannot.map((r) => h('li', null, h('strong', { text: r.label || r.title }), ': ' + r.why)))));
      return assistant(...kids);
    }
    function refsearchBubble(m) {
      const d = m.data, ctx = d.ctx || {}, added = d.added || {}, cmd = d.command || '\\cite';
      const picks = new Set();
      const copy = (text) => h('button', { type: 'button', class: 'small', text: 'Copy ' + text, onclick: async () => { try { await navigator.clipboard.writeText(text); toast('Copied'); } catch (e) { toast('Select it and copy by hand'); } } });
      const how = d.how === 'model' ? 'ChatGPT chose these words from your text' : d.how === 'given' ? 'These are the words you gave' : 'From your request';
      const kids = [h('p', { class: 'small secondary', text: `${how}: ${(d.queries || []).map((q) => '"' + q + '"').join(' · ')}. Searched on the internet from this computer; only those words were sent.` })];
      (d.tried || []).filter((t) => !t.ok).forEach((t) => kids.push(h('p', { class: 'small secondary', text: `${SOURCE_NAMES[t.source] || t.source} did not answer${t.note ? ' (' + t.note + ')' : ''}.` })));
      const count = h('span', { class: 'small muted', text: 'Tick the ones you want' });
      const sync = () => { count.textContent = picks.size ? plural(picks.size, 'reference') + ' ticked' : 'Tick the ones you want'; };
      const row = (c) => {
        const have = c.cite_key ? { key: c.cite_key, how: 'already in your bibliography' } : added[c.doi] ? { key: added[c.doi].key, how: 'added to your bibliography' } : null;
        const box = h('input', { type: 'checkbox', 'aria-label': 'Choose ' + c.label });
        box.addEventListener('change', () => { if (box.checked) picks.add(c.doi); else picks.delete(c.doi); sync(); });
        const who = (c.authors || []).slice(0, 3).join('; ') + ((c.authors || []).length > 3 ? ' et al.' : '');
        return h('div', { class: 'hit refcand' }, box, h('div', { class: 'stack tight grow' },
          h('div', null, h('strong', { text: c.title })),
          h('div', { class: 'small secondary', text: [who, c.year, c.venue].filter(Boolean).join(' · ') }),
          h('div', { class: 'row tight small' }, h('a', { href: 'https://doi.org/' + c.doi, target: '_blank', rel: 'noopener noreferrer', text: 'doi.org/' + c.doi }),
            c.cited_by ? h('span', { class: 'muted', text: `cited ${c.cited_by} times` }) : null),
          c.abstract ? h('details', null, h('summary', { class: 'small', text: 'Abstract' }), h('div', { class: 'small', text: c.abstract })) : h('div', { class: 'small muted', text: 'No abstract was available.' }),
          have ? h('div', { class: 'row tight' }, h('code', { class: 'cite', text: `${cmd}{${have.key}}` }), copy(`${cmd}{${have.key}}`), h('span', { class: 'small secondary', text: have.how })) : null));
      };
      if (!(d.candidates || []).length) kids.push(notice('warn', d.hidden ? `I found ${plural(d.hidden, 'record')}, but none was about your paper's subject, so I am not showing them. Try other words below, or paste the DOI of a paper you know.` : 'I found no papers with a DOI for those words. Try other words below, or paste the DOI of a paper you know.'));
      else {
        (d.candidates || []).forEach((c) => kids.push(row(c)));
        if (d.hidden) kids.push(h('p', { class: 'small muted', text: `${plural(d.hidden, 'other record')} left out: nothing in the title or abstract was about your paper's subject.` }));
        const useIt = (ctx.chunk_ids && ctx.chunk_ids.length) || ctx.draft;
        kids.push(h('div', { class: 'row actions' },
          guarded(h('button', { type: 'button', class: useIt ? '' : 'primary', text: 'Add the ticked to my bibliography' }), async () => { await pickRefs(m, [...picks]); }),
          useIt ? guarded(h('button', { type: 'button', class: 'primary', text: ctx.revises ? 'Add them and write the suggestion again with them' : 'Add them and cite them in the text' }), async () => {
            const dois = [...picks]; await pickRefs(m, dois); await useRefs(ctx, dois);
          }) : null, h('span', { class: 'grow' }), count));
        kids.push(h('p', { class: 'small muted', text: 'I only know what the search and the paper\'s own page say about each one: title, authors, abstract. I do not have the paper itself, so read it before you rely on what it says. Nothing is added to your bibliography until you tick it.' }));
        if (!useIt) kids.push(h('p', { class: 'small muted', text: 'To cite them in your text afterwards, ask for it, for example: "support the first paragraph of the introduction with these references".' }));
      }
      const words = drafts.bind(h('input', { type: 'text', value: (d.queries || [])[0] || '', 'aria-label': 'Words to search for', class: 'grow' }), `words:${paper}:${m.id}`);
      kids.push(h('div', { class: 'row' }, h('span', { class: 'small secondary', text: 'Other words:' }), words, guarded(h('button', { type: 'button', class: 'small', text: 'Search again' }), async () => {
        const w = words.value.trim();
        if (!w) throw new Error('Write what to look for.');
        await send('Find references about ' + w, Object.assign({ find_refs: true, words: w, chunk_ids: ctx.chunk_ids, facts: ctx.facts || undefined, issue: ctx.issue || undefined }, ctx.draft ? { redraft: ctx.revises } : { revises: ctx.revises || undefined }));
      })));
      if ((d.others || []).length) kids.push(h('details', null, h('summary', { class: 'small', text: `Other pages the search found (no DOI, so I cannot add them): ${d.others.length}` }),
        h('ul', { class: 'small' }, d.others.map((o) => h('li', null, h('a', { href: o.url, target: '_blank', rel: 'noopener noreferrer', text: o.title || o.url }))))));
      return assistant(...kids);
    }

    // ---- the suggestion
    function resultBubble(m) {
      const holder = h('div', null, busy('Loading the suggestion...'));
      api.get(`/api/proposal?paper=${enc(paper)}&id=${enc(m.data.proposal)}`).then((prop) => { if (alive()) renderSuggestion(holder, prop, m); })
        .catch((e) => clear(holder).append(notice('bad', e.message)));
      return assistant(holder);
    }

    function renderSuggestion(holder, prop, m) {
      if (prop.kind === 'layout') return renderLayout(holder, prop, m);
      clear(holder);
      let p = prop;
      st.cards.set(p.id, { holder, m, status: p.status, sent: !!p.sent_at });
      const locked = p.status === 'applied' || p.status === 'rejected' || p.status === 'superseded';
      const forgetDrafts = () => ['answer', 'req', 'more', 'final'].forEach((k) => drafts.clearPrefix(`${k}:${paper}:${p.id}`));       // a suggestion that is closed has nothing left to answer
      if (locked) forgetDrafts();
      const sel = new Set(p.units.flatMap((u) => u.hunks.map((x) => x.uid)));
      const nHunks = sel.size;
      const usage = p.usage || {};
      const notices = h('div', { class: 'stack' });
      const srcLabels = {};                                  // S1 -> "metrics.csv · rows 2 to 3", L1 -> "Smith and Lee 2020 · page 2"
      (p.sources || []).forEach((x) => { if (x.ref) srcLabels[x.ref] = `${x.file.split('/').pop()} · ${(x.where || '').split(' · ').slice(1).join(' · ') || x.where}`; });
      (p.library || []).forEach((x) => { if (x.ref) srcLabels[x.ref] = `${x.label} · ${x.where}`; });
      srcLabels.commentary = 'connecting comment, adds no new fact';
      const redraw = async () => { const fresh = await api.get(`/api/proposal?paper=${enc(paper)}&id=${enc(p.id)}`); renderSuggestion(clear(holder), fresh, m); };

      holder.append(h('div', { class: 'row small secondary' }, h('span', { text: `${p.model} · ${fmtTok(usage.input_tokens)} in / ${fmtTok(usage.output_tokens)} out${usage.cost != null ? ' · cost ' + usage.cost : ''}` }),
        p.status === 'applied' ? h('span', { class: 'badge ok', text: 'saved in your files' }) : p.status === 'rejected' ? h('span', { class: 'badge', text: 'set aside' }) : p.status === 'superseded' ? h('span', { class: 'badge', text: 'replaced' }) : null,
        p.edited_by_author ? h('span', { class: 'badge', text: 'your own version' }) : null));
      if ((p.sources || []).some((x) => !x.picture)) {
        const per = new Map();
        p.sources.filter((x) => !x.picture).forEach((x) => per.set(x.file.split('/').pop(), (per.get(x.file.split('/').pop()) || 0) + 1));
        holder.append(h('p', { class: 'small secondary', text: 'Used parts of your files: ' + [...per].map(([f, n]) => `${f} (${plural(n, 'part')})`).join(', ') }));
      }
      if ((p.pictures || []).length) holder.append(h('p', { class: 'small secondary', text: 'Looked at your figures: ' + p.pictures.map((x) => x.name).join(', ') + '.' }));
      if ((p.library || []).length) {
        const cited = [...new Set(p.units.flatMap((u) => u.added_cites || []))];
        const own = p.library.filter((x) => !x.online), found = p.library.filter((x) => x.online);
        const bits = [];
        if (own.length) bits.push('Used from your library: ' + [...new Set(own.map((x) => `${x.label} (${x.where})`))].join('; ') + '.');
        if (found.length) bits.push('Used from the references you found (their abstracts only): ' + [...new Set(found.map((x) => x.label))].join('; ') + '.');
        if (cited.length) bits.push(`Citations added: ${cited.join(', ')}.`);
        holder.append(h('p', { class: 'small secondary', text: bits.join(' ') }));
      }
      if (m.data.steps && m.data.steps.length) holder.append(h('details', null, h('summary', { text: 'What I did' }), stepList(m.data.steps)));
      (p.warnings || []).forEach((w) => holder.append(notice('warn', w)));
      if (p.status === 'failed') {
        holder.append(notice('bad', h('strong', { text: 'I could not use OpenAI\'s answer. ' }), p.units.map((u) => u.error_plain || u.error).filter(Boolean).join(' '), h('div', { class: 'small' }, h('a', { href: '#/settings', text: 'Change the model in Settings' }), ' or just ask again.')));
        return;
      }
      if (p.status === 'no_changes') holder.append(notice('ok', 'OpenAI found nothing it would change in this text.'));

      // each passage: the explanation, then the changes
      const isDraft = p.kind === 'draft';
      if (isDraft) {
        const complete = p.units.filter((u) => u.status !== 'failed')
          .flatMap((u) => u.hunks.map((hk) => hk.new).filter(Boolean)).join('\n\n').trim();
        if (complete) {
          holder.append(h('details', { class: 'draft-full', open: true },
            h('summary', { text: 'Complete draft (with in-text citations where supported)' }),
            h('pre', { class: 'payload draft-text', text: complete }),
            h('p', { class: 'small secondary', text: 'Source support is shown separately below each paragraph; it is not part of the manuscript text.' })));
        }
      }
      const answers = [];                                   // [{q, box}]: what the author replies to the model's questions
      let moreBox = null;                                   // the "anything to add or change" box of the discuss panel, once it has been opened
      const composeFacts = () => {
        const parts = [];
        answers.forEach(({ q, box }) => { const a = box.value.trim(); if (a) parts.push(`Question: ${q}\nAnswer: ${a}`); });
        const extra = moreBox ? moreBox.value.trim() : '';
        if (extra) parts.push('Further instructions from the author: ' + extra);
        return parts.join('\n\n');
      };
      const CITE_Q = /\b(citations?|cite|references?|sources?|literature|evidence|stud(?:y|ies)|publications?)\b/i;
      // Everything the author wants to tell the model goes in one go: the answers to ALL its questions, what to add, and (ticked) which questions are to be answered by looking for references on the
      // internet. Nothing is asked of OpenAI or of the internet until the author presses "Send my responses and re-edit".
      let wholeFind = null;                                 // "look for references online for the whole suggestion", in the panel
      const statusEls = [];
      const asking = () => answers.filter((a) => a.find && a.find.checked).map((a) => a.q);
      const updateStatus = () => {
        const done = answers.filter((a) => a.box.value.trim()).length, look = asking().length + (wholeFind && wholeFind.checked ? 1 : 0);
        const txt = `${done} of ${answers.length} answered` + (look ? ` · references will be looked for on the internet first (${look})` : '') + (done < answers.length - asking().length ? ' · an empty answer is skipped' : '');
        statusEls.forEach((e) => { e.textContent = txt; });
      };
      const submitAll = async (request) => {
        if (st.busy) throw new Error('A request is still running. Wait for it to finish, then press the button again.');
        const facts = composeFacts(), qs = asking(), whole = !!(wholeFind && wholeFind.checked);
        if (qs.length || whole) {                           // references first (the author chooses which to add), with every answer along; the re-edit follows from that card
          await send(qs.length ? 'Find references for these questions: ' + qs.join(' ') : 'Find references for: ' + (p.instruction || '').slice(0, 160),
            Object.assign({ find_refs: true, chunk_ids: m.data.chunk_ids || [], facts, issue: m.data.issue, ask: qs.length ? qs : undefined }, p.kind === 'draft' ? { redraft: p.id } : { revises: p.id }));
          return;
        }
        const req = (request || p.instruction || '').trim();
        if (!req) throw new Error('Say what the suggestion should do.');
        if (!facts && req === (p.instruction || '').trim()) throw new Error('Answer a question, or write what to add or change, first.');
        await send(req, p.kind === 'draft' ? { redraft: p.id, facts } : { chunk_ids: m.data.chunk_ids, facts, revises: p.id, issue: m.data.issue });
      };
      const sendAllButton = (cls) => guarded(h('button', { type: 'button', class: cls || 'primary', text: isDraft ? 'Send my responses and draft again' : 'Send my responses and re-edit',
        title: 'Sends all your answers together, and asks OpenAI again for this place. If you ticked "look for references", the references are searched first and you choose which to add; this suggestion is replaced only when the new one is ready.' }), () => submitAll());
      p.units.forEach((u) => {
        if (u.status === 'failed') { holder.append(notice('bad', `${u.section_path}: ${u.error_plain || u.error || 'failed'}`)); return; }
        // on a wide screen the explanation sits beside the changes instead of above them (see .unit in the stylesheet)
        const why = h('div', { class: 'unit-why' }, h('h3', { text: unitTitle(u) }));
        const changes = h('div', { class: 'unit-changes' });
        const box = h('section', { class: 'unit' }, why, changes);
        if (u.approach) why.append(h('p', { class: 'approach', text: u.approach }));
        if (u.heading) why.append(notice('ok', h('strong', { text: 'A new section is added: ' }), h('code', { text: u.heading }), ' goes above this text, ' + ((u.anchor && u.anchor.mode) || '').replace(/^as a new \w+ section /, '') + '. Your paper has no such section yet.'));
        const back = (u.restored || []).filter((x) => !x.own_line);
        if (back.length) why.append(notice('warn', `OpenAI left out ${plural(back.length, 'citation or formula', 'citations or formulas')} (${back.map((x) => x.latex).join(', ')}). I put ${back.length === 1 ? 'it' : 'them'} back where ${back.length === 1 ? 'it' : 'they'} stood; check ${back.length === 1 ? 'it still sits' : 'they still sit'} next to the claim ${back.length === 1 ? 'it supports' : 'they support'}.`));
        (u.questions || []).forEach((q, qi) => {
          const nq = notice('warn', h('strong', { text: 'A question for you: ' }), q);
          if (!locked) {
            const box = h('textarea', { rows: '2', class: 'answer', 'aria-label': 'Your answer to: ' + q, placeholder: 'Your answer (leave it empty to skip this question). Answer all the questions, then press "Send my responses and re-edit".' });
            drafts.bind(box, `answer:${paper}:${p.id}:${u.unit_id}:${qi}`);
            const entry = { q, box, find: null };
            if (CITE_Q.test(q)) {
              entry.find = h('input', { type: 'checkbox', id: `find-${p.id}-${u.unit_id}-${qi}` });
              nq.append(box, h('label', { class: 'use-box small', for: entry.find.id }, entry.find, ' I do not have the references: look for them on the internet for this question (I choose the best ones and cite them)'));
            } else nq.append(box);
            answers.push(entry);
            box.addEventListener('input', updateStatus);
            if (entry.find) entry.find.addEventListener('change', updateStatus);
          }
          why.append(nq);
        });
        if (!locked && (u.questions || []).length) {
          const st1 = h('span', { class: 'small muted', role: 'status', 'aria-live': 'polite' });
          statusEls.push(st1);
          why.append(h('div', { class: 'row' }, sendAllButton(), st1));
          updateStatus();
        }
        const bad = (u.validators || []).filter((v) => !v.ok);
        if (bad.length) why.append(notice('warn', 'Heads up: ', bad.map((v) => v.detail || v.name).join('; ')));
        if ((u.added_cites || []).length) {
          const keyOf = (c) => (/\{([^}]*)\}/.exec(c) || [])[1];
          const fresh = (p.bib_adds || []).filter((a) => u.added_cites.some((c) => keyOf(c) === a.key));
          const online = (p.library || []).some((x) => x.online && u.added_cites.some((c) => keyOf(c) === x.cite_key));
          why.append(notice('ok', h('strong', { text: online ? 'Citations from the references you found: ' : 'Citations from your library: ' }), u.added_cites.join(', '),
            fresh.length ? `. ${plural(fresh.length, 'entry', 'entries')} (${fresh.map((a) => a.key).join(', ')}) will be added to your bibliography when you save, so the citation and its reference are saved together.` : '. They stand in your bibliography already.',
            online ? ' A reference you found online is cited from its abstract only: read the paper to make sure it says what the sentence says.' : ' Check each one supports the claim it sits next to.'));
        }
        if ((u.from_files || []).length) why.append(notice('warn', h('strong', { text: 'Numbers that came from your files: ' }), u.from_files.join(', '), '. Check them against the source before you keep the change.'));
        u.hunks.forEach((hk) => changes.append(hunkCard(hk, sel, () => { count.textContent = `${sel.size} of ${nHunks} changes selected`; }, locked, p.rule_labels, srcLabels)));
        if ((u.basis || []).length) {
          why.append(h('details', null, h('summary', { text: 'What each statement rests on' }),
            h('ul', { class: 'small' }, u.basis.map((b) => h('li', null, h('strong', { text: (srcLabels[b.source] || b.source) + ': ' }), b.statement + '...')))));
        }
        if (!u.hunks.length && u.status !== 'failed') changes.append(h('p', { class: 'muted small', text: 'No changes in this passage.' }));
        holder.append(box);
      });

      holder.append(notices);
      const count = h('span', { class: 'muted small', text: `${sel.size} of ${nHunks} changes selected` });
      const copyAs = window.OLS.panes && window.OLS.panes.copyAsync;
      // the text of the ticked changes, as the server assembles it (real LaTeX, or tagged for QuillBot); nothing is written
      const textOf = () => api.post('/api/proposal/text', { paper, id: p.id, accept: sel.size ? [...sel] : 'all' });
      const copyBtn = (label, title, make, what, after) => guarded(h('button', { type: 'button', class: 'small', text: label, title }), async () => {
        if (!sel.size) throw new Error('Tick at least one change.');
        await copyAs(make(), what);
        if (after) after();
      });
      const beforeAfter = (hk) => [hk.diff.filter(([o]) => o !== '+').map(([, t]) => t).join(''), hk.diff.filter(([o]) => o !== '-').map(([, t]) => t).join('')];
      const everything = async () => {
        const t = await textOf();
        const parts = [];
        p.units.filter((u) => u.status !== 'failed').forEach((u) => {
          const mine = u.hunks.filter((hk) => sel.has(hk.uid));
          parts.push(unitTitle(u));
          if (u.approach) parts.push('', 'Why: ' + u.approach);
          if (mine.length) parts.push('', 'Changes:', ...mine.map((hk) => { const [b, a] = beforeAfter(hk); return `- "${b.trim()}" -> "${a.trim()}"${hk.reasons.length ? ' (' + hk.reasons.join('; ') + ')' : ''}`; }));
          parts.push('');
        });
        return parts.join('\n') + 'New text:\n\n' + t.latex;
      };
      const copies = h('div', { class: 'row tight copies' }, h('span', { class: 'small muted', text: 'Copy:' }),
        copyBtn('the new text', 'The improved text with your citations and formulas back in place, ready to paste into Overleaf or anywhere', async () => (await textOf()).latex, 'The new text'),
        copyBtn('for QuillBot', 'Copies the text with citations and formulas as tags like [[LX001]] (so QuillBot cannot damage them) and opens the panel where you bring QuillBot\'s result back', async () => (await textOf()).masked, 'The text for QuillBot', () => openBanner(p, 2)),
        copyBtn('everything', 'The explanation, each change with its reason, and the new text', everything, 'The whole answer'));
      if (locked) {
        if (p.status === 'superseded') holder.append(notice('ok', h('strong', { text: 'Replaced. ' }), 'You asked for a revised suggestion with your answers; it is further down the conversation, and this one can no longer be saved.'));
        if (p.status === 'applied' && p.sent_at) {
          holder.append(notice('ok', h('strong', { text: 'Saved and sent to Overleaf.' })));
        } else if (p.status === 'applied') {
          holder.append(h('div', { class: 'stack' }, notice('ok', h('strong', { text: 'Saved in your files. ' }), 'It is not in Overleaf until you send it.'),
            h('div', { class: 'row' }, guarded(h('button', { type: 'button', class: 'primary', text: 'Send to Overleaf' }), async () => {
              const r = await api.post('/api/papers/sync', { name: paper, message: 'Writing assistant: ' + (p.instruction || '').slice(0, 60) });
              showGit(notices, r);
              if (r.sent && window.OLS.panes && window.OLS.panes.afterSend) window.OLS.panes.afterSend(r);
              await reload().catch(() => {});
            }))));
        }
        return;
      }
      // Discuss and revise: answer the model's questions, change what was asked (the reviewer's comment, if this is one), say what to add. The answer is a new suggestion
      // for the same place, built with those answers; it replaces this one, and the reviewer's comment follows the new one.
      const isComment = !!(m.data.issue);
      const discuss = h('div', { class: 'discuss', hidden: true });
      const openDiscuss = () => {
        discuss.hidden = !discuss.hidden;
        if (discuss.hidden || discuss.childNodes.length) return;
        const req = h('textarea', { rows: '3', 'aria-label': isComment ? 'The reviewer\'s comment' : 'What I was asked to do' });
        req.value = p.instruction || '';
        drafts.bind(req, `req:${paper}:${p.id}`);
        const more = h('textarea', { rows: '3', 'aria-label': 'Anything to add or change', placeholder: 'For example: the criteria were wells with at least six months of production; keep it to two sentences; do not mention the vendor.' });
        drafts.bind(more, `more:${paper}:${p.id}`);
        moreBox = more;
        const compose = composeFacts;
        wholeFind = h('input', { type: 'checkbox', id: `find-all-${p.id}` });
        wholeFind.addEventListener('change', updateStatus);
        const go = guarded(h('button', { type: 'button', class: 'primary', text: 'Send and make a new suggestion' }), async () => { await submitAll(req.value); });
        discuss.append(h('p', { class: 'small secondary', text: answers.length ? 'Your answers to the questions above go with it. Everything you write here is used as given: I add nothing beyond it, and the numbers and names in it are allowed in the new text.' : 'Everything you write here is used as given: I add nothing beyond it, and the numbers and names in it are allowed in the new text.' }),
          h('label', { class: 'small secondary', text: isComment ? 'The reviewer\'s comment (change it if you read it differently)' : 'What I was asked to do (change it if you want it understood differently)' }), req,
          h('label', { class: 'small secondary', text: 'Anything to add or change?' }), more,
          h('label', { class: 'use-box small', for: wholeFind.id }, wholeFind, ' I do not have the references: look for them on the internet (I choose the best ones and cite them)'),
          h('div', { class: 'row' }, go, h('button', { type: 'button', class: 'ghost', text: 'Cancel', onclick: () => { discuss.hidden = true; } }),
            h('span', { class: 'small muted', text: 'This asks OpenAI again for this place and costs about as much as the first time.' })));
      };
      const discussBtn = h('button', { type: 'button', text: answers.length ? 'Add instructions or change the request' : (isDraft ? 'Discuss and draft again' : 'Discuss and revise'),
        title: isDraft ? 'Refine what to add, add instructions, or tick that references are looked for first. You get a new insertion suggestion for the same place, and this one is replaced.' : 'Change what was asked, say what to add, or tick that references are looked for first. You get a new suggestion for the same place that replaces this one.', onclick: openDiscuss });
      const sendAllBtn = answers.length ? sendAllButton() : null;
      if (!nHunks) {
        if (p.status === 'proposed' || p.status === 'no_changes') {
          holder.append(h('div', { class: 'row actions' }, sendAllBtn, discussBtn), discuss);
          if (drafts.has(`req:${paper}:${p.id}`) || drafts.has(`more:${paper}:${p.id}`)) openDiscuss();          // what was being written there is still there: show it
        }
        return;
      }
      const unclean = !st.data.can_apply;             // no text-cleaning service: the text is saved exactly as written, and the card says so
      const approve = (push) => async () => {
        if (!sel.size) throw new Error('Tick at least one change.');
        await doApprove(notices, p, { accept: [...sel], push, unclean_ok: unclean }, redraw);
      };
      const handoff = guarded(h('button', { type: 'button', class: 'ghost', text: 'Hand to Claude Code instead' }), async () => {
        await api.post('/api/proposal/approve', { paper, id: p.id, accept: [...sel], mode: 'handoff' });
        notices.append(notice('ok', h('strong', { text: 'Handed to Claude Code. ' }), 'In Claude Code say "apply the suggestion I handed you". It saves exactly the changes you ticked and runs your text-cleaning step first.'));
      });
      handoff.title = 'For when you work in Claude Code: it saves the ticked changes itself, after your text-cleaning step. You do not need this to save from here.';
      const actions = h('div', { class: 'row actions' },
        guarded(h('button', { type: 'button', class: 'primary', text: isDraft ? 'Accept all and insert in my files' : 'Accept all and save in my files' }), async () => {
          const all = p.units.flatMap((u) => u.hunks.map((hk) => hk.uid));
          await doApprove(notices, p, { accept: all, push: false, unclean_ok: unclean }, redraw);
        }),
        guarded(h('button', { type: 'button', text: isDraft ? 'Accept all, insert and send to Overleaf' : 'Accept all, save and send to Overleaf' }), async () => {
          const all = p.units.flatMap((u) => u.hunks.map((hk) => hk.uid));
          await doApprove(notices, p, { accept: all, push: true, unclean_ok: unclean }, redraw);
        }),
        h('button', { type: 'button', text: 'Polish in QuillBot or by hand', title: 'Opens a panel above the message box: take the text to QuillBot, bring the result back, check it and save', onclick: () => openBanner(p, 1) }),
        sendAllBtn, discussBtn,
        guarded(h('button', { type: 'button', class: 'ghost', text: 'Not now' }), async () => { await api.post('/api/proposal/record', { paper, id: p.id, accepted: [], rejected: 'all', chat: st.cid }); await redraw(); await reload(); }),
        h('span', { class: 'grow' }), count);
      holder.append(copies, actions, discuss);
      if (drafts.has(`req:${paper}:${p.id}`) || drafts.has(`more:${paper}:${p.id}`)) openDiscuss();
      if (drafts.has(`final:${paper}:${p.id}`)) openBanner(p, 3);                                                 // the author's own version was being written: bring the panel back
      holder.append(h('p', { class: 'small muted' }, unclean ? 'Saved exactly as written: no text-cleaning service is set up. ' : '', isDraft ? 'Inserting puts the ticked new paragraphs at the chosen place in your files; "send" then puts them in Overleaf. ' : 'Saving puts the ticked changes into your files here; "send" then puts them in Overleaf. ', handoff));
    }

    function hunkCard(hk, sel, onChange, locked, labels, sources) {
      const cb = h('input', { type: 'checkbox', checked: locked ? hk.status === 'accepted' : sel.has(hk.uid), disabled: locked, 'aria-label': 'Keep this change' });
      cb.addEventListener('change', () => { if (cb.checked) sel.add(hk.uid); else sel.delete(hk.uid); onChange(); });
      return h('div', { class: 'hunk' },
        h('header', null, cb, h('div', { class: 'grow' }, h('div', { class: 'small', text: hk.reasons.length ? hk.reasons.join('; ') : 'A small improvement' }),
          (hk.rests_on || []).length ? h('div', { class: 'rule', text: 'Rests on: ' + hk.rests_on.map((x) => (sources && sources[x]) || x).join('; ') }) : null,
          hk.rules.length ? h('div', { class: 'rule', text: 'From your writing book: ' + hk.rules.map((r) => (labels && labels[r]) || 'a tip from the book').join('; ') }) : null),
          locked ? h('span', { class: 'badge', text: hk.status }) : null),
        h('div', { class: 'sides' }, h('div', { class: 'side' }, h('h4', { text: 'Now' }), hk.old ? diffSpans(hk.diff, 'old') : h('span', { class: 'muted', text: 'Nothing here yet.' })), h('div', { class: 'side' }, h('h4', { text: 'Suggested' }), diffSpans(hk.diff, 'new'))));
    }

    function showGit(into, r) {
      into.append(gitNotice(r.push ? Object.assign({}, r.push) : r));
      loadOverview();
    }

    async function doApprove(into, p, body, redraw) {
      try {
        const r = await api.post('/api/proposal/approve', Object.assign({ paper, id: p.id, chat: st.cid }, body));
        if (r.ok === false) return r;
        ['answer', 'req', 'more', 'final'].forEach((k) => drafts.clearPrefix(`${k}:${paper}:${p.id}`));
        const addedRefs = ((r.applied || {}).bibliography || {}).added || [];
        toast('Saved in ' + r.files.join(', ') + (addedRefs.length ? '; added to your bibliography: ' + addedRefs.map((a) => a.key).join(', ') : ''));
        (((r.applied || {}).bibliography || {}).warnings || []).forEach((w) => into.append(notice('warn', w)));
        if (r.push) showGit(into, r);
        if (window.OLS.panes && window.OLS.panes.refreshText) window.OLS.panes.refreshText(true);
        if (r.push && r.push.sent && window.OLS.panes && window.OLS.panes.afterSend) window.OLS.panes.afterSend(r.push);
        await window.OLS.loadState();
        await reload();
        return r;
      } catch (e) {
        into.append(notice('bad', h('strong', { text: 'Not saved: ' }), e.message, e.data && e.data.handoff_available ? ' Nothing was written. You can hand it to Claude instead.' : ''));
        throw e;
      }
    }

    // ---- the tone panel: copy to QuillBot (or edit by hand), paste the final version back, check, approve
    const take = (mine, check, what, label, title) => guarded(h('button', { type: 'button', text: label, title }), async () => {
      const t = await window.OLS.panes.takeFrom('quillbot', what);
      if (!t || !t.trim()) {
        toast(what === 'clipboard' ? 'QuillBot has not copied anything yet. Press QuillBot\'s own Copy button, or select the text there and use "Take the text I selected".' : 'Nothing is selected in QuillBot. Select the result text there first.');
        return;
      }
      mine.value = t; mine.dispatchEvent(new Event('input')); if (st.banner) st.banner.setStep(Math.max(st.banner.step, 3)); toast('Took ' + t.trim().split(/\s+/).length + ' words from QuillBot. Check them below.');
    });

    /* The panel above the message box for taking a suggestion through QuillBot (or editing it by hand): the text goes out, the result comes back into a box here, the
       same checks as everywhere run on it, and one button saves it and sends it to Overleaf. It lives outside the scrolling conversation, so it stays in view. */
    const banner = h('section', { class: 'quill-banner', hidden: true, 'aria-label': 'Take the text to QuillBot and bring it back' });
    st.banner = null;
    function closeBanner() { st.banner = null; clear(banner); banner.hidden = true; renderDock(); }
    function openBanner(p, step) {
      const panes = window.OLS.panes;
      if (panes && panes.has && panes.has('quillbot')) panes.show('quillbot');                 // QuillBot opens beside the chat
      if (st.banner && st.banner.pid === p.id) {                                               // already open for this suggestion: keep what was typed, move to the step
        st.banner.setStep(Math.max(st.banner.step, step || 1)); st.banner.minimised(false);
        return;
      }
      clear(banner); banner.hidden = false;
      buildBanner(banner, p, step || 1);
      renderDock();
      if (st.stuck) threadEl.scrollTop = threadEl.scrollHeight;
    }

    function buildBanner(el, p, startStep) {
      const inQuillBot = !!(window.OLS.panes && window.OLS.panes.has && window.OLS.panes.has('quillbot'));
      const units = p.units.filter((u) => u.status !== 'failed' && u.views);
      const boxes = new Map(), checkEls = new Map(), approveBtns = [];
      const ack = h('input', { type: 'checkbox' });
      const ackRow = h('label', { class: 'check small', hidden: true }, ack, 'I checked the warnings above and want to go ahead');
      let timer = null, last = {}, blocked = false;
      const texts = () => Object.fromEntries([...boxes.entries()].map(([id, b]) => [id, b.value]));
      const changed = () => units.some((u) => boxes.get(u.unit_id).value !== u.views.suggested_masked);

      // the four steps, with the current one marked
      const labels = ['Copy the text', 'Adjust the tone in QuillBot', 'Bring the result back', 'Save and send'];
      const stepper = h('ol', { class: 'stepper' }, labels.map((t, i) => h('li', null, h('span', { class: 'n', 'aria-hidden': 'true', text: String(i + 1) }), h('span', { text: t }))));
      const handle = { pid: p.id, step: 0 };
      handle.setStep = (n) => {
        handle.step = n;
        Array.from(stepper.children).forEach((li, i) => { li.className = i + 1 < n ? 'done' : i + 1 === n ? 'now' : ''; li.querySelector('.n').textContent = i + 1 < n ? '✓' : String(i + 1); });
      };
      st.banner = handle;

      const body = h('div', { class: 'banner-body' });
      handle.minimised = (on) => { body.hidden = on; mini.textContent = on ? 'Show' : 'Hide'; mini.setAttribute('aria-expanded', String(!on)); };
      const mini = h('button', { type: 'button', class: 'small ghost', 'aria-expanded': 'true', text: 'Hide', onclick: () => handle.minimised(!body.hidden) });
      const first = units[0];
      el.append(h('div', { class: 'row banner-head' }, h('span', { class: 'qb-dot', 'aria-hidden': 'true' }), h('strong', { text: 'Taking this to QuillBot' }),
        h('span', { class: 'small muted grow clamp1', text: first ? `${first.section_path.split(' > ').slice(-1)[0]} · lines ${first.line_start} to ${first.line_end}${units.length > 1 ? ` and ${units.length - 1} more` : ''}` : '' }),
        mini, h('button', { type: 'button', class: 'small ghost', title: 'Close this panel. Nothing is saved.', text: 'Close', onclick: closeBanner })), stepper, body);

      const paintChecks = (checks) => {
        last = checks;
        blocked = false;
        let warn = false;
        for (const [uid, c] of Object.entries(checks)) {
          const e = checkEls.get(uid); if (!e) continue;
          clear(e.list).append(...c.messages.map((x) => h('li', { class: 'chk ' + x.level }, h('span', { class: 'mark', 'aria-hidden': 'true', text: icon(x.level) }), h('span', { text: x.text }))));
          clear(e.diff).append(h('div', { class: 'sides one' }, h('div', { class: 'side' }, diffSpans(e.which.value === 'model' ? c.diff_model : c.diff_original, 'new'))));
          blocked = blocked || !c.ok; warn = warn || c.has_warnings;
        }
        ackRow.hidden = !warn || blocked;
        approveBtns.forEach((b) => { b.disabled = blocked; });
        if (handle.step >= 3) handle.setStep(changed() && !blocked ? 4 : 3);
      };
      const check = () => {
        if (handle.step < 3 && changed()) handle.setStep(3);
        clearTimeout(timer);
        timer = setTimeout(async () => {
          try { paintChecks((await api.post('/api/proposal/check', { paper, id: p.id, texts: texts() })).checks); }
          catch (e) { toast(e.message); }
        }, 350);
      };
      const out = h('div', { class: 'stack' });
      body.append(h('p', { class: 'small secondary', text: inQuillBot
        ? '1. Copy or put the text in QuillBot (it is beside the chat). 2. Adjust the tone there. 3. Press "Take QuillBot\'s result" and check the notes. 4. Save and send. Or skip QuillBot and edit the right-hand box by hand.'
        : '1. Copy the text and paste it into QuillBot (open it with the QuillBot button at the top, or in any window). 2. Adjust the tone. 3. Copy the result and press "Paste what I copied". 4. Save and send.' }));
      units.forEach((u) => {
        const v = u.views;
        const mine = h('textarea', { rows: '5', 'aria-label': 'Your final version', class: 'mono' });
        mine.value = v.suggested_masked;
        drafts.bind(mine, `final:${paper}:${p.id}:${u.unit_id}`);
        mine.addEventListener('input', check);
        boxes.set(u.unit_id, mine);
        const which = h('select', { 'aria-label': 'Compare with' }, h('option', { value: 'original', text: 'Show my changes against my original text' }), h('option', { value: 'model', text: 'Show my changes against OpenAI\'s suggestion' }));
        which.addEventListener('change', () => paintChecks(last));
        const list = h('ul', { class: 'checks' });
        const diff = h('div');
        checkEls.set(u.unit_id, { list, diff, which });
        const legend = Object.entries(v.legend || {});
        body.append(h('section', { class: 'unit' }, units.length > 1 ? h('h4', { text: unitTitle(u) }) : null,
          h('div', { class: 'two' },
            h('div', null, h('label', { class: 'small secondary', text: 'Text for QuillBot' }), readonlyBox(v.suggested_masked),
              h('div', { class: 'row tight' }, h('button', { type: 'button', class: 'small', text: 'Copy for QuillBot', onclick: () => { copyText(v.suggested_masked, 'Text for QuillBot'); handle.setStep(Math.max(handle.step, 2)); } }),
                inQuillBot ? guarded(h('button', { type: 'button', class: 'small primary', text: 'Put it in QuillBot', title: 'Types the text into the QuillBot pane' }), async () => {
                  await window.OLS.panes.sendText('quillbot', v.suggested_masked);
                  handle.setStep(Math.max(handle.step, 2));
                  toast('It is in QuillBot. Adjust the tone there, then press "Take QuillBot\'s result" here.');
                }) : null,
                h('button', { type: 'button', class: 'small', text: 'Copy as LaTeX', onclick: () => copyText(v.suggested_latex, 'LaTeX') }))),
            h('div', null, h('label', { class: 'small secondary', text: 'Your final version (the result from QuillBot goes here)' }), mine,
              h('div', { class: 'row tight' }, inQuillBot ? [
                take(mine, check, 'clipboard', 'Take QuillBot\'s result', 'The text QuillBot put on its clipboard with its own Copy button'),
                take(mine, check, 'selection', 'Take the text I selected', 'The text you selected in the QuillBot pane')] : null,
              h('button', { type: 'button', class: inQuillBot ? 'small' : 'small primary', text: 'Paste what I copied', onclick: async () => {
                try { mine.value = await navigator.clipboard.readText(); mine.dispatchEvent(new Event('input')); } catch (e) { toast('Your browser did not allow reading the clipboard. Paste with Cmd+V or Ctrl+V into the box instead.'); }
              } }), h('button', { type: 'button', class: 'small ghost', text: 'Start over', onclick: () => { mine.value = v.suggested_masked; mine.dispatchEvent(new Event('input')); } }),
              guarded(h('button', { type: 'button', class: 'small ghost', text: 'Copy my version as LaTeX', title: 'Your version with the citations and formulas put back, ready to paste anywhere' }), async () => {
                await window.OLS.panes.copyAsync(api.post('/api/proposal/check', { paper, id: p.id, texts: { [u.unit_id]: mine.value } }).then((r) => r.checks[u.unit_id].latex), 'Your version');
              })))),
          legend.length ? h('details', null, h('summary', { text: 'What each tag stands for' }), h('ul', { class: 'small mono' }, legend.map(([k, x]) => h('li', { text: `[[${k}]]  ${x}` })))) : null,
          list, h('details', null, h('summary', { text: 'Show what differs' }), h('div', { class: 'row small' }, which), diff)));
      });
      const unclean = !st.data.can_apply;             // no text-cleaning service: saved exactly as written
      const approve = (push) => async () => {
        const sink = h('div', { class: 'stack' });
        let r;
        try { r = await doApprove(sink, p, { texts: texts(), acknowledge: ack.checked, push, unclean_ok: unclean }); }
        catch (e) { out.append(...Array.from(sink.childNodes)); throw e; }
        if (r && r.ok === false) { paintChecks(r.checks); if (r.needs_ack) { ackRow.hidden = false; toast('Tick "I checked the warnings" to go ahead.'); } else toast('Fix the red items first.'); return; }
        if (sink.childNodes.length) st.local.push({ node: sink });                    // what happened with Overleaf stays in the conversation
        closeBanner();
        renderThread();
      };
      const bSend = guarded(h('button', { type: 'button', class: 'primary', text: 'Save and send to Overleaf' }), approve(true));
      const bSave = guarded(h('button', { type: 'button', text: 'Save only' }), approve(false));
      const bHand = guarded(h('button', { type: 'button', class: 'ghost', text: 'Hand my version to Claude Code instead', title: 'For when you work in Claude Code. You do not need this to save from here.' }), async () => {
        const r = await api.post('/api/proposal/approve', { paper, id: p.id, texts: texts(), acknowledge: ack.checked, mode: 'handoff' });
        if (r.ok === false) { paintChecks(r.checks); return; }
        out.append(notice('ok', 'Handed to Claude Code with your version.'));
      });
      approveBtns.push(bSend, bSave);
      body.append(out, ackRow, h('div', { class: 'row' }, bSend, bSave, bHand), ...(unclean ? [h('p', { class: 'small muted', text: 'Saved exactly as written: no text-cleaning service is set up.' })] : []));
      handle.setStep(startStep);
      check();
    }

    // ---- the thread. Every message keeps its element until its own content changes, so a refresh never flashes, never loses an open toggle or text
    // being edited, and never throws the reading position away.
    function buildMessage(m) {
      if (m.role === 'user') return h('div', { class: 'msg user' }, h('div', { class: 'bubble' }, h('div', { text: m.text }),
        (m.data && m.data.facts) ? h('details', { class: 'facts' }, h('summary', { text: 'My answers that went with it' }), h('div', { class: 'small', text: m.data.facts })) : null),
        (m.data && m.data.understood) ? h('div', { class: 'read-as small secondary' }, h('span', { text: 'Read as: ' + m.data.understood.summary + ' ' }),
          h('button', { type: 'button', class: 'link', text: 'Not what I meant', onclick: () => answerUnderstanding(m, { action: 'reconsider' }) })) : null);
      if (m.kind === 'understanding') return understandingBubble(m);
      if (m.kind === 'layout_plan') return layoutPlanBubble(m);
      if (m.kind === 'plan') return planBubble(m);
      if (m.kind === 'draft_plan') return draftPlanBubble(m);
      if (m.kind === 'ask') return askBubble(m);
      if (m.kind === 'result') return resultBubble(m);
      if (m.kind === 'refs') return refsBubble(m);
      if (m.kind === 'library') return libraryBubble(m);
      if (m.kind === 'refsearch') return refsearchBubble(m);
      if (m.kind === 'bring') return bringBubble(m);
      if (m.kind === 'review_plan') return reviewPlanBubble(m);
      if (m.kind === 'review') return reviewBubble(m);
      if (m.kind === 'review_done') return reviewDoneBubble(m);
      if (m.kind === 'error') return assistant(notice('bad', m.text));
      return assistant(textBlock(m.text));
    }
    async function answerUnderstanding(m, body) {
      if (st.busy) { toast('A request is still running. Try again when the answer is in.'); return; }
      st.stuck = true;
      setBusy(true);
      try {
        const r = await api.post('/api/chat/understood', Object.assign({ paper, id: st.cid, message: m.id }, body));
        if (body.action !== 'change') drafts.clearPrefix(`understood:${paper}:${m.id}`);
        await reload();
        await window.OLS.loadThreads();
        if (r.job) await watch(r.job);
      } catch (e) { toast(e.message); }
      finally { setBusy(false); }
    }
    function understandingBubble(m) {
      const d = m.data, rd = d.reading || {}, waiting = d.state === 'waiting';
      const label = (v) => (REQUEST_KINDS.find((k) => k[0] === v) || [v, v])[1];
      const kids = [h('p', null, h('strong', { text: waiting ? 'Is this what you want? ' : d.state === 'cancelled' ? 'Set aside: ' : 'Understood: ' }), m.text || rd.summary)];
      if (rd.target && rd.target !== 'not given') kids.push(h('p', { class: 'small secondary', text: 'About: ' + rd.target }));
      if (rd.question && waiting) kids.push(notice('warn', rd.question));
      if (!waiting) {
        if (d.state === 'confirmed') kids.push(h('p', { class: 'small secondary', text: `Done as: ${label(d.chosen || rd.intent)}.` + (d.note ? ` Your note: ${d.note}` : '') }));
        if (d.correction) kids.push(h('p', { class: 'small secondary', text: 'You corrected it: ' + d.correction }));
        return assistant(...kids);
      }
      const sel = h('select', { 'aria-label': 'Kind of request' }, REQUEST_KINDS.map(([v, l]) => h('option', { value: v, text: l, selected: v === rd.intent })));
      const uses = rd.uses || {};
      const boxes = {};
      const tick = (key, text) => { boxes[key] = h('input', { type: 'checkbox', checked: !!uses[key], id: `use-${key}-${m.id}` }); return h('label', { class: 'use-box', for: `use-${key}-${m.id}` }, boxes[key], ' ' + text); };
      const note = drafts.bind(h('textarea', { rows: '2', class: 'grow', 'aria-label': 'Anything to correct or add', placeholder: 'Anything to correct or add? For example: only references from the internet, not from my library' }), `understood:${paper}:${m.id}`);
      const body = (action) => ({ action, intent: sel.value, uses: { library: boxes.library.checked, online: boxes.online.checked, outline: boxes.outline.checked }, note: note.value.trim() });
      kids.push(h('div', { class: 'row' }, h('span', { class: 'small secondary', text: 'Kind of request:' }), sel));
      kids.push(h('div', { class: 'row small' }, h('span', { class: 'secondary', text: 'Use:' }), tick('library', 'my reading'), tick('online', 'the internet'), tick('outline', 'my outline')));
      (rd.alternatives || []).forEach((a) => kids.push(h('button', { type: 'button', class: 'small', text: 'Or: ' + label(a), onclick: () => { sel.value = a; } })));
      kids.push(h('div', { class: 'row' }, note));
      kids.push(h('div', { class: 'row actions' },
        h('button', { type: 'button', class: 'primary', text: 'Yes, go ahead', onclick: () => answerUnderstanding(m, body('go')) }),
        h('button', { type: 'button', text: 'Read it again with my correction', onclick: () => { if (!note.value.trim()) { toast('Write what to correct first.'); note.focus(); return; } answerUnderstanding(m, body('change')); } }),
        h('button', { type: 'button', class: 'ghost', text: 'Not what I meant', onclick: () => answerUnderstanding(m, body('cancel')) })));
      kids.push(h('p', { class: 'small muted', text: 'Nothing has been changed or sent yet. I remember what you correct, in this paper\'s folder, to read your next requests better.' }));
      return assistant(...kids);
    }
    function syncChildren(parent, nodes) {
      const want = new Set(nodes);
      Array.from(parent.children).forEach((c) => { if (!want.has(c)) parent.removeChild(c); });
      nodes.forEach((n, i) => { if (parent.children[i] !== n) parent.insertBefore(n, parent.children[i] || null); });
    }
    function renderThread() {
      if (!alive()) return;
      const stick = st.stuck || !threadEl.firstChild;
      const keep = threadEl.scrollTop;
      const ctx = JSON.stringify([st.data.can_apply, st.data.openai, !!(window.OLS.panes && window.OLS.panes.has && window.OLS.panes.has('quillbot'))]);
      const nodes = [];
      if (!st.messages.length || st.showHelp) {
        const wsig = ctx + (S.st.books || []).length;
        if (!st.welcomeEl || st.welcomeSig !== wsig) { st.welcomeEl = welcome(); st.welcomeSig = wsig; }
        nodes.push(st.welcomeEl);
      }
      const live = new Set();
      for (const m of st.messages) {
        live.add(m.id);
        const sig = ctx + JSON.stringify([m.kind, m.role, m.text, m.data]) + (m.kind === 'review' || m.kind === 'review_done' ? JSON.stringify(slotsOf(m.kind === 'review' ? m.id : m.data.review)) : '');
        let hit = st.cache.get(m.id);
        if (!hit || hit.sig !== sig) {
          hit = { sig, node: buildMessage(m) };
          if (st.seen.size && !st.seen.has(m.id)) hit.node.classList.add('fresh');       // a new message eases in; the ones already there stay still
          st.cache.set(m.id, hit);
        }
        st.seen.add(m.id);
        nodes.push(hit.node);
      }
      Array.from(st.cache.keys()).forEach((id) => { if (!live.has(id)) st.cache.delete(id); });
      st.local.forEach((l) => { if (!l.wrapper) l.wrapper = assistant(l.node); nodes.push(l.wrapper); });
      if (st.working) nodes.push(workingBubble());
      syncChildren(threadEl, nodes);
      if (follow) nodes.forEach((n) => follow.observe(n));              // observing an element twice is harmless; a removed one is dropped by the browser
      threadEl.scrollTop = stick ? threadEl.scrollHeight : keep;
      if (stick) st.stuck = true;
      renderDock();
    }

    // ---- what the app remembers, and how to forget it
    async function startOver() {
      if (st.busy || st.working) throw new Error('A request is still running. Wait for it to finish first.');
      if (st.messages.length && !window.confirm('Clear this conversation and start again here?\n\nSuggestions you already saved stay in your files. Nothing is deleted from your paper.')) return;
      await api.post('/api/chat/clear', { paper, id: st.cid });
      st.local.length = 0; st.cache.clear(); st.seen.clear(); st.showHelp = false;
      await reload();
      await window.OLS.loadThreads();
      if (window.OLS.panes && window.OLS.panes.setTargetFromThread) window.OLS.panes.setTargetFromThread(st.messages);
      toast('Started over. This chat remembers nothing now.');
      ta.focus();
    }
    async function newChat() {
      const r = await api.post('/api/chat/new', { paper });
      S.chatId = r.thread.id;
      await window.OLS.loadThreads();
      window.OLS.route();
    }
    async function renameChat() {
      const cur = ((S.threads || []).find((t) => t.id === st.cid) || {}).title || '';
      const name = window.prompt('Name this chat', cur);
      if (name == null) return;
      await api.post('/api/chat/rename', { paper, id: st.cid, title: name });
      await window.OLS.loadThreads();
      toast('Renamed');
    }
    async function deleteChat() {
      if (!window.confirm('Delete this whole chat? Suggestions you already saved stay in your files.')) return;
      await api.post('/api/chat/delete', { paper, id: st.cid });
      S.chatId = null;
      await window.OLS.loadThreads();
      window.OLS.route();
    }
    function memoryBox() {
      const decisions = h('p', { class: 'small secondary', text: 'Looking up what I have learned from your decisions...' });
      const forget = guarded(h('button', { type: 'button', class: 'small', text: 'Forget my past decisions' }), async () => {
        if (!window.confirm('Forget which suggestions you kept and which you set aside for this paper?\n\nRequests then go out without those style hints. Your paper and your saved files are not touched.')) return;
        const r = await api.post(`/api/paper/${enc(paper)}/forget`, {});
        toast(`Forgot ${plural(r.forgotten, 'decision')}`);
        decisions.textContent = 'Nothing learned yet: I start from the writing book and the notes about your paper.';
        forget.hidden = true;
      });
      forget.hidden = true;
      api.get(`/api/paper/${enc(paper)}/preferences`).then((r) => {
        const n = (r.accepted || 0) + (r.rejected || 0);
        decisions.textContent = n ? `From your ${plural(n, 'earlier decision')} on this paper (${r.accepted} kept, ${r.rejected} set aside) I send OpenAI a short note about the style you like. It is the same in every chat of this paper.`
          : 'Nothing learned yet: I start from the writing book and the notes about your paper.';
        forget.hidden = !n;
      }).catch(() => { decisions.textContent = ''; });
      return h('section', { class: 'memory', 'aria-label': 'What I remember' }, h('h3', { text: 'What I remember' }),
        h('ul', { class: 'small' },
          h('li', null, h('strong', { text: 'In this chat: ' }), 'only where your last request worked, so "make it shorter" means the same place. I do not send the earlier messages to OpenAI; each request is sent on its own.'),
          h('li', null, h('strong', { text: 'For this paper, in every chat: ' }), 'the notes about it (key terms, tense, notation; edit them under Settings, Advanced) and what you kept or set aside.')),
        decisions,
        h('div', { class: 'row' }, guarded(h('button', { type: 'button', class: 'primary small', text: 'Start over in this chat' }), () => startOver()),
          guarded(h('button', { type: 'button', class: 'small', text: 'Start a new chat instead' }), () => newChat()),
          h('button', { type: 'button', class: 'small', text: 'Rename', onclick: () => renameChat().catch((e) => toast(e.message)) }),
          guarded(h('button', { type: 'button', class: 'small', text: 'Delete this chat' }), () => deleteChat()), forget));
    }

    function welcome() {
      const setup = [];
      if (st.data.openai !== 'ok') setup.push(notice('warn', h('strong', { text: 'One thing first: ' }), 'OpenAI is not set up yet. ', h('a', { href: '#/settings', text: 'Add your key and model in Settings' }), '.'));
      if (!(S.st.books || []).length) setup.push(notice('ok', h('strong', { text: 'Optional: ' }), 'your writing book is not prepared yet, so requests will not carry its advice. ', h('a', { href: '#/settings', text: 'Prepare it in Settings' }), '.'));
      return h('div', { class: 'welcome' }, h('h2', { text: `What would you like to do with ${paper}?` }), ...setup,
        h('ol', { class: 'how' },
          h('li', null, h('strong', { text: 'Say what you want. ' }), 'For example "improve the flow of the text before Figure 1 in the methods", "fix the grammar in the introduction", "peer review my paper", or paste a DOI.'),
          h('li', null, h('strong', { text: 'I find the right part ' }), 'of your paper and the writing advice that applies, and ask OpenAI for a better version with an explanation.'),
          h('li', null, h('strong', { text: 'You decide. ' }), 'Keep the changes you like, tweak the tone (QuillBot or by hand), then approve. It is saved in your files and you can send it to Overleaf.')),
        h('p', { class: 'small muted', text: 'Your citations, formulas, numbers and figures never leave your computer as text; OpenAI only sees the passage with tags in their place, and the answer is rejected if any of them changed. Each paper keeps its own private notes.' }),
        memoryBox());
    }

    const page = h('div', { class: 'chat-page' }, head, threadEl, dock, banner, composer, h('div', { class: 'drop-hint', 'aria-hidden': 'true', text: 'Drop the file here to add it to this paper' }));
    let depth = 0;
    const hasFiles = (e) => !!e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
    page.addEventListener('dragenter', (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth++; page.classList.add('dropping'); });
    page.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
    page.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) page.classList.remove('dropping'); });
    page.addEventListener('drop', (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth = 0; page.classList.remove('dropping'); addFiles(e.dataTransfer.files); });
    box.append(page);
    // the router attaches this page to the document right after we return, and the thread only draws itself while attached
    setTimeout(() => {
      renderThread();
      if (data.running.length) data.running.forEach((j) => watch(j.job));
      ta.focus();
    }, 0);
    if (window.OLS.panes && window.OLS.panes.setTargetFromThread) window.OLS.panes.setTargetFromThread(st.messages);
    if (window.OLS.panes && window.OLS.panes.knows) window.OLS.panes.knows(paper).catch(() => {});
  });
}());
