/* The chat page: say what you want, watch what is being done, read the explanation and the corrected text, adjust the tone, approve.
   Every piece of manuscript or model text goes in as text (textContent / text nodes), never as HTML. */
(function () {
  'use strict';
  const { h, clear, api, notice, busy, guarded, registerPage, toast, fmtTok, ago, S, waitJob } = window.OLS;
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
  function readonlyBox(text) { const t = h('textarea', { rows: '8', readonly: true, class: 'mono', 'aria-label': 'Suggestion' }); t.value = text; return t; }
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + 's'}`;
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
    const st = { data, paper, cid: data.id, messages: data.thread.messages, local: [], working: null, busy: false };
    const threadEl = h('div', { class: 'thread', role: 'log', 'aria-label': 'Conversation' });
    const sub = h('p', { class: 'muted small' });
    const alive = () => document.body.contains(threadEl) && S.paper === paper && S.chatId === st.cid;

    // ---- header
    const gitNotice = (r) => (r.friendly ? notice('warn', r.friendly, ' ', h('a', { href: '#/settings', text: 'Open Settings' })) :
      h('div', null, notice(r.level === 'bad' ? 'bad' : r.level === 'warn' ? 'warn' : 'ok', r.summary || r.output), r.output && r.output !== r.summary ? h('details', null, h('summary', { text: 'Details' }), h('pre', { class: 'small payload', text: r.output })) : null));
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
      if (action === 'sync' && window.OLS.panes && window.OLS.panes.afterSend) window.OLS.panes.afterSend();
    });
    loadOverview();
    const head = h('header', { class: 'chat-head' }, h('div', { class: 'grow' }, h('h1', { text: paper }), sub),
      h('div', { class: 'row' }, window.OLS.panes.bar(), gitBtn('Get latest from Overleaf', 'pull'), gitBtn('Send to Overleaf', 'sync', { message: 'Writing assistant: update' }),
        h('button', { type: 'button', class: 'ghost', text: '?', title: 'How this works', 'aria-label': 'How this works', onclick: () => { st.showHelp = !st.showHelp; renderThread(); } })));

    // ---- composer
    const ta = h('textarea', { id: 'composer', rows: '2', 'aria-label': 'Ask for a change', placeholder: 'What should I do? For example: improve the flow of the text before Figure 1 in the methods' });
    const sendBtn = h('button', { type: 'button', class: 'primary', text: 'Send' });
    const chips = h('div', { class: 'row chips' }, data.chips.map((c) => h('button', { type: 'button', class: 'chip', text: c.label, onclick: () => { ta.value = (ta.value.trim() ? ta.value.trim() + ' ' : '') + c.text; ta.focus(); } })));
    const composer = h('form', { class: 'composer' }, chips, h('div', { class: 'row top' }, ta, sendBtn));
    const setBusy = (b) => { st.busy = b; sendBtn.disabled = b; sendBtn.textContent = b ? 'Working...' : 'Send'; };

    async function reload() {
      const d = await api.get(`/api/chat?paper=${enc(paper)}&id=${enc(st.cid)}`);
      st.messages = d.thread.messages; st.data = d;
      if (alive()) renderThread();
      return d;
    }

    async function send(text, extra) {
      if (st.busy) return;
      setBusy(true);
      try {
        const r = await api.post('/api/chat/send', Object.assign({ paper, id: st.cid, text }, extra || {}));
        await reload();
        await window.OLS.loadThreads();
        if (r.job) await watch(r.job);
        else if (window.OLS.panes && window.OLS.panes.setTargetFromThread) window.OLS.panes.setTargetFromThread(st.messages);
      } catch (e) { toast(e.message); }
      finally { setBusy(false); }
    }
    const submit = () => { const t = ta.value.trim(); if (!t) return; ta.value = ''; send(t); };
    sendBtn.addEventListener('click', submit);
    composer.addEventListener('submit', (e) => { e.preventDefault(); submit(); });
    ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } });

    // ---- the pass in progress
    async function watch(jobId) {
      st.working = { steps: [{ key: 'start', label: 'Starting', state: 'running', detail: '' }] };
      renderThread();
      setBusy(true);
      try {
        await waitJob(jobId, (j) => { if (j.steps) { st.working.steps = j.steps; if (alive()) renderThread(); } });
      } catch (e) { /* the failure is already in the chat as a message */ }
      finally {
        st.working = null; setBusy(false);
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
      (pl.warnings || []).forEach((x) => lines.push(notice('warn', x)));
      const exact = h('div');
      const details = h('details', null, h('summary', { text: 'What goes out' }),
        h('ul', { class: 'small' }, pl.passages.map((x) => h('li', { text: `${x.file}, lines ${x.lines[0]} to ${x.lines[1]} · ${x.section} · about ${x.words} words` }))),
        h('button', { type: 'button', class: 'small', text: 'Show the exact text', onclick: async (ev) => {
          ev.target.disabled = true;
          const r = await api.post('/api/revise/preview', { paper, instruction: pl.instruction, chunk_ids: pl.chunk_ids, book_id: pl.book });
          exact.append(...r.payloads.map((x) => h('pre', { class: 'payload small', text: (x.request.input || x.request.messages[1].content) })));
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

    function reviewBubble(m) {
      const d = m.data;
      const sev = { major: 'bad', minor: 'warn', suggestion: '' };
      const issues = (d.issues || []).map((it) => h('li', { class: 'issue' },
        h('div', { class: 'row tight' }, h('span', { class: 'badge ' + (sev[it.severity] || ''), text: it.severity }), it.where ? h('strong', { class: 'small', text: it.where }) : null),
        it.quote ? h('div', { class: 'small muted', text: '“' + it.quote + '”' }) : null, h('div', { text: it.problem }), it.suggestion ? h('div', { class: 'small secondary', text: it.suggestion }) : null,
        it.chunk_ids && it.chunk_ids.length ? h('div', null, h('button', { type: 'button', class: 'small', text: 'Fix this', onclick: () =>
          send(`Address this reviewer comment${it.where ? ' about ' + it.where : ''}: ${it.problem} ${it.suggestion || ''}`.trim(), { chunk_ids: it.chunk_ids }) })) : null));
      const scores = Object.entries(d.scores || {});
      return assistant(
        h('div', { class: 'row' }, h('strong', { text: 'Peer review of ' + d.scope }), d.verdict ? h('span', { class: 'badge', text: d.verdict }) : null,
          h('span', { class: 'small secondary', text: `${d.model} · ${fmtTok(d.usage.input_tokens)} in / ${fmtTok(d.usage.output_tokens)} out${d.usage.cost != null ? ' · cost ' + d.usage.cost : ''}` })),
        scores.length ? h('div', { class: 'row chips' }, scores.map(([k, v]) => h('span', { class: 'chip static', text: `${k.replace(/_/g, ' ')}: ${v}` }))) : null,
        issues.length ? h('div', null, h('h4', { text: `Issues to look at (${issues.length})` }), h('ul', { class: 'issues' }, ...issues)) : null,
        h('details', { open: !issues.length }, h('summary', { text: 'Full report' }), mdLite(d.report)),
        h('div', { class: 'row' }, h('button', { type: 'button', class: 'small', text: 'Copy the report', onclick: () => copyText(d.report, 'Report') }), h('span', { class: 'small muted', text: 'Saved in ' + d.path })),
        h('p', { class: 'small muted', text: 'Nothing was changed in your paper. "Fix this" asks for an ordinary suggestion that you approve or set aside.' }));
    }

    // ---- references
    function refsBubble(m) {
      const d = m.data, added = d.results.filter((r) => r.status === 'added').length;
      const kids = d.results.map((r) => window.OLS.refCard(r, d.push));
      if (d.push && d.push.friendly) kids.push(notice('warn', d.push.friendly, ' ', h('a', { href: '#/settings', text: 'Open Settings' })));
      if (d.pending) kids.push(notice('warn', `${plural(d.pending, 'reference')} still waiting to reach Mendeley. `, h('a', { href: '#/references', text: 'Try again on the References page' })));
      return assistant(h('p', { class: 'small secondary', text: added ? `Added ${plural(added, 'reference')}.` : 'Nothing new was added.' }), ...kids);
    }

    // ---- the suggestion
    function resultBubble(m) {
      const holder = h('div', null, busy('Loading the suggestion...'));
      api.get(`/api/proposal?paper=${enc(paper)}&id=${enc(m.data.proposal)}`).then((prop) => { if (alive()) renderSuggestion(holder, prop, m); })
        .catch((e) => clear(holder).append(notice('bad', e.message)));
      return assistant(holder);
    }

    function renderSuggestion(holder, prop, m) {
      clear(holder);
      let p = prop;
      const locked = p.status === 'applied' || p.status === 'rejected';
      const sel = new Set(p.units.flatMap((u) => u.hunks.map((x) => x.uid)));
      const nHunks = sel.size;
      const usage = p.usage || {};
      const notices = h('div', { class: 'stack' });
      const redraw = async () => { const fresh = await api.get(`/api/proposal?paper=${enc(paper)}&id=${enc(p.id)}`); renderSuggestion(clear(holder), fresh, m); };

      holder.append(h('div', { class: 'row small secondary' }, h('span', { text: `${p.model} · ${fmtTok(usage.input_tokens)} in / ${fmtTok(usage.output_tokens)} out${usage.cost != null ? ' · cost ' + usage.cost : ''}` }),
        p.status === 'applied' ? h('span', { class: 'badge ok', text: 'saved in your files' }) : p.status === 'rejected' ? h('span', { class: 'badge', text: 'set aside' }) : null,
        p.edited_by_author ? h('span', { class: 'badge', text: 'your own version' }) : null));
      if (m.data.steps && m.data.steps.length) holder.append(h('details', null, h('summary', { text: 'What I did' }), stepList(m.data.steps)));
      (p.warnings || []).forEach((w) => holder.append(notice('warn', w)));
      if (p.status === 'failed') {
        holder.append(notice('bad', h('strong', { text: 'I could not use OpenAI\'s answer. ' }), p.units.map((u) => u.error_plain || u.error).filter(Boolean).join(' '), h('div', { class: 'small' }, h('a', { href: '#/settings', text: 'Change the model in Settings' }), ' or just ask again.')));
        return;
      }
      if (p.status === 'no_changes') holder.append(notice('ok', 'OpenAI found nothing it would change in this text.'));

      // each passage: the explanation, then the changes
      p.units.forEach((u) => {
        if (u.status === 'failed') { holder.append(notice('bad', `${u.section_path}: ${u.error_plain || u.error || 'failed'}`)); return; }
        const box = h('section', { class: 'unit' }, h('h3', { text: `${u.section_path.split(' > ').slice(-1)[0]} · lines ${u.line_start} to ${u.line_end}` }));
        if (u.approach) box.append(h('p', { class: 'approach', text: u.approach }));
        const back = (u.restored || []).filter((x) => !x.own_line);
        if (back.length) box.append(notice('warn', `OpenAI left out ${plural(back.length, 'citation or formula', 'citations or formulas')} (${back.map((x) => x.latex).join(', ')}). I put ${back.length === 1 ? 'it' : 'them'} back where ${back.length === 1 ? 'it' : 'they'} stood; check ${back.length === 1 ? 'it still sits' : 'they still sit'} next to the claim ${back.length === 1 ? 'it supports' : 'they support'}.`));
        (u.questions || []).forEach((q) => box.append(notice('warn', h('strong', { text: 'A question for you: ' }), q)));
        const bad = (u.validators || []).filter((v) => !v.ok);
        if (bad.length) box.append(notice('warn', 'Heads up: ', bad.map((v) => v.detail || v.name).join('; ')));
        u.hunks.forEach((hk) => box.append(hunkCard(hk, sel, () => { count.textContent = `${sel.size} of ${nHunks} changes selected`; }, locked, p.rule_labels)));
        if (!u.hunks.length && u.status !== 'failed') box.append(h('p', { class: 'muted small', text: 'No changes in this passage.' }));
        holder.append(box);
      });

      holder.append(notices);
      const count = h('span', { class: 'muted small', text: `${sel.size} of ${nHunks} changes selected` });
      if (locked) {
        if (p.status === 'applied') holder.append(h('div', { class: 'row' }, guarded(h('button', { type: 'button', class: 'primary', text: 'Send to Overleaf' }), async () => {
          const r = await api.post('/api/papers/sync', { name: paper, message: 'Writing assistant: ' + (p.instruction || '').slice(0, 60) });
          showGit(notices, r);
        })));
        return;
      }
      if (!nHunks) return;
      const tone = h('div', { hidden: true, class: 'tone' });
      const canApply = st.data.can_apply;
      const approve = (push) => async () => {
        if (!sel.size) throw new Error('Tick at least one change.');
        await doApprove(notices, p, { accept: [...sel], push }, redraw);
      };
      const handoff = guarded(h('button', { type: 'button', class: canApply ? '' : 'primary', text: 'Let Claude apply it' }), async () => {
        await api.post('/api/proposal/approve', { paper, id: p.id, accept: [...sel], mode: 'handoff' });
        notices.append(notice('ok', h('strong', { text: 'Handed to Claude. ' }), 'In Claude Code say "apply the suggestion I handed you". It saves exactly the changes you ticked and cleans the text first.'));
      });
      handoff.title = 'Claude saves the ticked changes itself, including the text-cleaning step';
      const actions = h('div', { class: 'row actions' },
        canApply ? guarded(h('button', { type: 'button', class: 'primary', text: 'Approve and save' }), approve(false)) : null,
        canApply ? guarded(h('button', { type: 'button', text: 'Approve and push to Overleaf' }), approve(true)) : null,
        canApply ? null : handoff,
        h('button', { type: 'button', text: 'Adjust the tone myself', onclick: () => { tone.hidden = !tone.hidden; if (!tone.childNodes.length) buildTone(tone, p, redraw); } }),
        guarded(h('button', { type: 'button', class: 'ghost', text: 'Not now' }), async () => { await api.post('/api/proposal/record', { paper, id: p.id, accepted: [], rejected: 'all' }); await redraw(); }),
        h('span', { class: 'grow' }), count);
      holder.append(actions);
      if (!canApply) holder.append(h('p', { class: 'small muted', text: 'The text-cleaning service is not set up, so Claude saves it for you. Add its address in Settings to save directly from here.' }));
      holder.append(tone);
    }

    function hunkCard(hk, sel, onChange, locked, labels) {
      const cb = h('input', { type: 'checkbox', checked: locked ? hk.status === 'accepted' : sel.has(hk.uid), disabled: locked, 'aria-label': 'Keep this change' });
      cb.addEventListener('change', () => { if (cb.checked) sel.add(hk.uid); else sel.delete(hk.uid); onChange(); });
      return h('div', { class: 'hunk' },
        h('header', null, cb, h('div', { class: 'grow' }, h('div', { class: 'small', text: hk.reasons.length ? hk.reasons.join('; ') : 'A small improvement' }),
          hk.rules.length ? h('div', { class: 'rule', text: 'From your writing book: ' + hk.rules.map((r) => (labels && labels[r]) || 'a tip from the book').join('; ') }) : null),
          locked ? h('span', { class: 'badge', text: hk.status }) : null),
        h('div', { class: 'sides' }, h('div', { class: 'side' }, h('h4', { text: 'Now' }), diffSpans(hk.diff, 'old')), h('div', { class: 'side' }, h('h4', { text: 'Suggested' }), diffSpans(hk.diff, 'new'))));
    }

    function showGit(into, r) {
      into.append(gitNotice(r.push ? Object.assign({}, r.push) : r));
      loadOverview();
    }

    async function doApprove(into, p, body, redraw) {
      try {
        const r = await api.post('/api/proposal/approve', Object.assign({ paper, id: p.id, chat: st.cid }, body));
        if (r.ok === false) return r;
        toast('Saved in ' + r.files.join(', '));
        if (r.push) showGit(into, r);
        if (window.OLS.panes && window.OLS.panes.refreshText) window.OLS.panes.refreshText(true);
        if (r.push && window.OLS.panes && window.OLS.panes.afterSend) window.OLS.panes.afterSend();
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
      mine.value = t; check(); toast('Took ' + t.trim().split(/\s+/).length + ' words from QuillBot. Check them below.');
    });

    function buildTone(el, p, redraw) {
      const inQuillBot = !!(window.OLS.panes && window.OLS.panes.has && window.OLS.panes.has('quillbot'));
      const units = p.units.filter((u) => u.status !== 'failed' && u.views);
      const boxes = new Map();
      const checkEls = new Map();
      const approveBtns = [];
      const ack = h('input', { type: 'checkbox' });
      const ackRow = h('label', { class: 'check small', hidden: true }, ack, 'I checked the warnings above and want to go ahead');
      let timer = null, last = {};
      const texts = () => Object.fromEntries([...boxes.entries()].map(([id, b]) => [id, b.value]));
      const paintChecks = (checks) => {
        last = checks;
        let blocked = false, warn = false;
        for (const [uid, c] of Object.entries(checks)) {
          const e = checkEls.get(uid); if (!e) continue;
          clear(e.list).append(...c.messages.map((x) => h('li', { class: 'chk ' + x.level }, h('span', { class: 'mark', 'aria-hidden': 'true', text: icon(x.level) }), h('span', { text: x.text }))));
          clear(e.diff).append(h('div', { class: 'sides one' }, h('div', { class: 'side' }, diffSpans(e.which.value === 'model' ? c.diff_model : c.diff_original, 'new'))));
          blocked = blocked || !c.ok; warn = warn || c.has_warnings;
        }
        ackRow.hidden = !warn || blocked;
        approveBtns.forEach((b) => { b.disabled = blocked; });
      };
      const check = () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          try { paintChecks((await api.post('/api/proposal/check', { paper, id: p.id, texts: texts() })).checks); }
          catch (e) { toast(e.message); }
        }, 350);
      };
      const out = h('div', { class: 'stack' });
      el.append(h('p', { class: 'small secondary', text: (inQuillBot ?
        'Press "Put it in QuillBot", adjust the tone in the QuillBot pane, then press "Take QuillBot\'s result" (or select the result there and press "Take the text I selected"). You can also copy and paste by hand, or just edit here. ' :
        'Copy the suggestion to QuillBot (the pane on the right, or any window), adjust the tone, copy the result, and paste it into "Your final version". You can also just edit it here. ') +
        'Tags like [[LX001]] stand for your citations and formulas: leave them as they are.' }));
      units.forEach((u) => {
        const v = u.views;
        const mine = h('textarea', { rows: '8', 'aria-label': 'Your final version', class: 'mono' });
        mine.value = v.suggested_masked;
        mine.addEventListener('input', check);
        boxes.set(u.unit_id, mine);
        const which = h('select', { 'aria-label': 'Compare with' }, h('option', { value: 'original', text: 'Show my changes against my original text' }), h('option', { value: 'model', text: 'Show my changes against OpenAI\'s suggestion' }));
        which.addEventListener('change', () => paintChecks(last));
        const list = h('ul', { class: 'checks' });
        const diff = h('div');
        checkEls.set(u.unit_id, { list, diff, which });
        const legend = Object.entries(v.legend || {});
        out.append(h('section', { class: 'unit' }, h('h4', { text: u.section_path.split(' > ').slice(-1)[0] + ` · lines ${u.line_start} to ${u.line_end}` }),
          h('div', { class: 'two' },
            h('div', null, h('label', { class: 'small secondary', text: 'OpenAI\'s suggestion' }), readonlyBox(v.suggested_masked),
              h('div', { class: 'row' }, h('button', { type: 'button', text: 'Copy for QuillBot', onclick: () => copyText(v.suggested_masked, 'Text for QuillBot') }),
                inQuillBot ? guarded(h('button', { type: 'button', class: 'primary', text: 'Put it in QuillBot', title: 'Types the suggestion into the QuillBot pane' }), async () => {
                  await window.OLS.panes.sendText('quillbot', v.suggested_masked);
                  toast('It is in QuillBot. Adjust the tone there, then press "Take QuillBot\'s result" here.');
                }) : null,
                h('button', { type: 'button', text: 'Copy as LaTeX', onclick: () => copyText(v.suggested_latex, 'LaTeX') }))),
            h('div', null, h('label', { class: 'small secondary', text: 'Your final version' }), mine,
              h('div', { class: 'row' }, inQuillBot ? [
                take(mine, check, 'clipboard', 'Take QuillBot\'s result', 'The text QuillBot put on its clipboard with its own Copy button'),
                take(mine, check, 'selection', 'Take the text I selected', 'The text you selected in the QuillBot pane')] : null,
              h('button', { type: 'button', text: 'Paste what I copied', onclick: async () => {
                try { mine.value = await navigator.clipboard.readText(); check(); } catch (e) { toast('Your browser did not allow reading the clipboard. Paste with Cmd+V or Ctrl+V into the box instead.'); }
              } }), h('button', { type: 'button', class: 'ghost', text: 'Start over', onclick: () => { mine.value = v.suggested_masked; check(); } })))),
          legend.length ? h('details', null, h('summary', { text: 'What each tag stands for' }), h('ul', { class: 'small mono' }, legend.map(([k, x]) => h('li', { text: `[[${k}]]  ${x}` })))) : null,
          list, h('div', { class: 'row small' }, which), diff));
      });
      const approve = (push) => async () => {
        const r = await doApprove(out, p, { texts: texts(), acknowledge: ack.checked, push });
        if (r && r.ok === false) { paintChecks(r.checks); if (r.needs_ack) { ackRow.hidden = false; toast('Tick "I checked the warnings" to go ahead.'); } else toast('Fix the red items first.'); }
      };
      const b1 = guarded(h('button', { type: 'button', class: 'primary', text: 'Approve and save' }), approve(false));
      const b2 = guarded(h('button', { type: 'button', text: 'Approve and push to Overleaf' }), approve(true));
      const b3 = guarded(h('button', { type: 'button', text: 'Let Claude apply my version' }), async () => {
        const r = await api.post('/api/proposal/approve', { paper, id: p.id, texts: texts(), acknowledge: ack.checked, mode: 'handoff' });
        if (r.ok === false) { paintChecks(r.checks); return; }
        out.append(notice('ok', 'Handed to Claude with your version.'));
      });
      approveBtns.push(...(st.data.can_apply ? [b1, b2] : [b3]));
      el.append(out, ackRow, h('div', { class: 'row' }, st.data.can_apply ? [b1, b2] : [b3]));
      check();
    }

    // ---- the thread
    function renderThread() {
      if (!alive()) return;
      clear(threadEl);
      if (!st.messages.length || st.showHelp) threadEl.append(welcome());
      for (const m of st.messages) {
        if (m.role === 'user') threadEl.append(h('div', { class: 'msg user' }, h('div', { class: 'bubble', text: m.text })));
        else if (m.kind === 'plan') threadEl.append(planBubble(m));
        else if (m.kind === 'ask') threadEl.append(askBubble(m));
        else if (m.kind === 'result') threadEl.append(resultBubble(m));
        else if (m.kind === 'refs') threadEl.append(refsBubble(m));
        else if (m.kind === 'review_plan') threadEl.append(reviewPlanBubble(m));
        else if (m.kind === 'review') threadEl.append(reviewBubble(m));
        else if (m.kind === 'error') threadEl.append(assistant(notice('bad', m.text)));
        else threadEl.append(assistant(textBlock(m.text)));
      }
      st.local.forEach((l) => threadEl.append(assistant(l.node)));
      if (st.working) threadEl.append(assistant(h('p', { class: 'small secondary', text: 'Working on it...' }), stepList(st.working.steps, true)));
      threadEl.scrollTop = threadEl.scrollHeight;
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
        h('p', { class: 'small muted', text: 'Your citations, formulas, numbers and figures never leave your computer as text; OpenAI only sees the passage with tags in their place, and the answer is rejected if any of them changed. Each paper keeps its own private notes.' }));
    }

    box.append(h('div', { class: 'chat-page' }, head, threadEl, composer));
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
