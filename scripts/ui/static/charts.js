/* Charts and the Monitor page. Inline SVG, no libraries, no external requests.
   Marks follow the dataviz specs: bars <= 24px with a 4px rounded data-end and a square baseline, 2px surface gaps between
   stacked segments, hairline grid, a legend for >= 2 series, one tooltip listing every series, hit targets larger than the marks,
   a table view for every chart, text in ink tokens (never the series colour). Labels and names are inserted as text only. */
(function () {
  'use strict';
  const { h, clear, api, notice, busy, registerPage, tile, fmtTok, ago, S } = window.OLS;
  const NS = 'http://www.w3.org/2000/svg';

  function el(tag, attrs, text) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null) e.setAttribute(k, String(v));
    if (text != null) e.textContent = text;
    return e;
  }
  const nf = (n) => (n == null ? '-' : Number(n).toLocaleString());
  const hhmm = (ts) => { const d = new Date(ts * 1000); return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };

  // ---- tooltip: one at a time, same details on hover and on keyboard focus
  const tip = {
    box: null,
    show(x, y, head, rows) {
      const b = this.box || (this.box = document.getElementById('tooltip'));
      clear(b);
      if (head) b.append(h('div', { class: 't-head', text: head }));
      for (const r of rows) b.append(h('div', { class: 't-row' }, r.cls ? h('span', { class: 'key ' + r.cls }) : null, h('span', { class: 'secondary', text: r.label }), h('b', { text: r.value })));
      b.hidden = false;
      const w = b.offsetWidth, hh = b.offsetHeight;
      b.style.left = Math.max(8, Math.min(x + 14, window.innerWidth - w - 8)) + 'px';
      b.style.top = Math.max(8, Math.min(y + 14, window.innerHeight - hh - 8)) + 'px';
    },
    hide() { if (this.box) this.box.hidden = true; },
  };
  function hover(target, content) {  // content() -> {head, rows}
    target.addEventListener('pointermove', (e) => { const c = content(); tip.show(e.clientX, e.clientY, c.head, c.rows); });
    target.addEventListener('pointerleave', () => tip.hide());
    target.addEventListener('focus', () => { const r = target.getBoundingClientRect(); const c = content(); tip.show(r.left + r.width / 2, r.top, c.head, c.rows); });
    target.addEventListener('blur', () => tip.hide());
  }

  function niceMax(v) {
    if (v <= 0) return 4;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 4, 5, 10]) if (v <= m * p) return m * p;
    return 10 * p;
  }
  function topRounded(x, y, w, hgt, r) {  // data end rounded, baseline end square
    const rr = Math.min(r, w / 2, hgt);
    return `M${x},${y + hgt}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + hgt}Z`;
  }

  function card(title, sub, ...kids) {
    return h('section', { class: 'card', 'aria-label': title }, h('div', { class: 'row' }, h('h2', { text: title })), sub ? h('p', { class: 'small secondary', text: sub }) : null, ...kids);
  }
  function withTable(chartNode, tableNode) {  // every chart has a table view (also the relief for low-contrast colours)
    const wrap = h('div');
    const t = h('div', { class: 'table-wrap', hidden: true }, tableNode);
    const btn = h('button', { type: 'button', class: 'ghost small', 'aria-pressed': 'false', text: 'View as table' });
    btn.addEventListener('click', () => { const on = t.hidden; t.hidden = !on; chartNode.hidden = on; btn.setAttribute('aria-pressed', String(on)); btn.textContent = on ? 'View as chart' : 'View as table'; });
    wrap.append(h('div', { class: 'row' }, h('span', { class: 'grow' }), btn), chartNode, t);
    return wrap;
  }
  function simpleTable(head, rows) {
    return h('table', null, h('thead', null, h('tr', null, head.map((t, i) => h('th', { class: i ? 'num' : '', text: t })))),
      h('tbody', null, rows.map((r) => h('tr', null, r.map((c, i) => h('td', { class: i ? 'num' : '', text: String(c) }))))));
  }
  const legend = (series) => h('div', { class: 'legend', 'aria-label': 'Legend' }, series.map((s) => h('span', null, h('i', { class: s.cls }), s.label)));

  // ---- stacked columns (activity timeline, index health)
  function stackedColumns(o) {
    const W = 720, H = o.height || 220, m = { l: 38, r: 8, t: 10, b: 30 };
    const n = o.labels.length;
    const totals = o.labels.map((_, i) => o.series.reduce((s, x) => s + (x.values[i] || 0), 0));
    const max = niceMax(Math.max(0, ...totals));
    const iw = W - m.l - m.r, ih = H - m.t - m.b, slot = iw / Math.max(n, 1), bw = Math.min(24, slot * 0.7);
    const y = (v) => m.t + ih - (v / max) * ih;
    const s = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': o.title });
    for (const t of [0, 0.25, 0.5, 0.75, 1]) {
      const v = max * t;
      s.append(el('line', { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: t ? 'grid-line' : 'axis-line' }), el('text', { x: m.l - 6, y: y(v) + 4, 'text-anchor': 'end' }, nf(Math.round(v * 10) / 10)));
    }
    const every = Math.max(1, Math.ceil(n / 8));
    o.labels.forEach((lab, i) => { if (i % every === 0) s.append(el('text', { x: m.l + slot * (i + 0.5), y: H - 10, 'text-anchor': 'middle' }, lab)); });  // sparse; tooltips carry the rest
    o.labels.forEach((lab, i) => {
      const g = el('g', { class: 'col' });
      const x = m.l + slot * i + (slot - bw) / 2;
      let acc = 0;
      const live = o.series.filter((sr) => sr.values[i] > 0);
      live.forEach((sr, k) => {
        const v = sr.values[i], y0 = y(acc), y1 = y(acc + v);
        const gap = k < live.length ? 2 : 0;
        const hh = Math.max(1, y0 - y1 - (k > 0 ? gap : 0));
        const top = y1 + (k > 0 ? 0 : 0);
        const isTop = k === live.length - 1;
        const path = isTop ? topRounded(x, top, bw, hh, 4) : `M${x},${top}h${bw}v${hh}h-${bw}Z`;
        g.append(el('path', { d: path, class: 'mark ' + sr.cls }));
        acc += v;
      });
      const hit = el('rect', { x: m.l + slot * i, y: m.t, width: slot, height: ih, fill: 'transparent', tabindex: 0, role: 'img', 'aria-label': `${lab}: ` + o.series.map((sr) => `${sr.label} ${sr.values[i] || 0}`).join(', ') });
      hover(hit, () => ({ head: o.heads ? o.heads[i] : lab, rows: [...o.series].reverse().map((sr) => ({ cls: sr.cls, label: sr.label, value: nf(sr.values[i] || 0) })).concat(o.extra ? o.extra(i) : []) }));
      g.append(hit);
      s.append(g);
    });
    const chart = h('div', { class: 'chart' }, legend(o.series), s);
    if (!n || !max) chart.append(h('p', { class: 'muted small', text: o.empty || 'Nothing to show yet.' }));
    return withTable(chart, simpleTable(['', ...o.series.map((x) => x.label)], o.labels.map((lab, i) => [o.heads ? o.heads[i] : lab, ...o.series.map((x) => x.values[i] || 0)])));
  }

  // ---- heatmap (book usage): chapters x sections, one hue light->dark
  function heatmap(bu) {
    const max = Math.max(1, ...bu.chapters.flatMap((c) => c.sections.map((s) => s.count)));
    const q = (c) => (c <= 0 ? 0 : Math.min(7, Math.max(1, Math.ceil((c / max) * 7))));
    const rows = bu.chapters.map((c) => {
      const cs = 22, gap = 2, s = el('svg', { viewBox: `0 0 ${c.sections.length * (cs + gap)} ${cs}`, width: c.sections.length * (cs + gap), height: cs, role: 'img', 'aria-label': `Chapter ${c.chapter}` });
      c.sections.forEach((sec, i) => {
        const r = el('rect', { x: i * (cs + gap), y: 0, width: cs, height: cs, rx: 3, class: 'cell q' + q(sec.count), tabindex: 0, role: 'img', 'aria-label': `${sec.title}: retrieved ${sec.count} times` });
        const never = sec.chunks.filter((k) => !k.count).length;
        hover(r, () => ({ head: `Ch ${c.chapter}: ${c.title}`, rows: [{ label: sec.title, value: '' }, { label: 'retrieved', value: `${sec.count}x` }, { label: 'chunks never used', value: `${never} of ${sec.chunks.length}` }] }));
        s.append(r);
      });
      return h('div', { class: 'heat-row' }, h('div', { class: 'rl', title: c.title, text: `${c.chapter}. ${c.title}` }), s, h('span', { class: 'muted small', text: nf(c.count) }));
    });
    const scale = h('div', { class: 'legend', 'aria-label': 'Scale' }, h('span', { text: 'Fewer' }), [0, 1, 2, 3, 4, 5, 6, 7].map((i) => h('span', null, h('i', { class: 'seqkey q' + i }))), h('span', { text: 'More retrievals' }));
    const chart = h('div', { class: 'chart' }, scale, rows);
    const table = simpleTable(['Chapter / section', 'Retrieved'], bu.chapters.flatMap((c) => [[`${c.chapter}. ${c.title}`, c.count], ...c.sections.map((s) => [`    ${s.title}`, s.count])]));
    const lists = h('div', { class: 'grid two' },
      h('div', null, h('h3', { text: 'Most used chunks' }), bu.top.length ? h('ol', { class: 'small' }, bu.top.slice(0, 6).map((t) => h('li', { text: `${t.label} (${t.count}x)` }))) : h('p', { class: 'small muted', text: 'No lookups yet.' })),
      h('div', null, h('h3', { text: 'Never used' }), h('p', { class: 'small secondary', text: `${bu.never_used} of ${bu.total_chunks} chunks have not been retrieved yet. That is normal early on; if whole chapters stay dark, routing may not reach them.` })));
    return h('div', null, withTable(chart, table), lists);
  }

  // ---- RAG efficiency: tokens sent against the whole manuscript (emphasis: sent in accent, the rest neutral)
  function efficiency(eff) {
    const rows = eff.rows.slice(-12).reverse();
    if (!rows.length) return h('p', { class: 'muted small', text: 'No lookups yet.' });
    const avg = rows.reduce((s, r) => s + (r.manuscript ? r.sent / r.manuscript : 0), 0) / rows.length;
    const chart = h('div', { class: 'chart' }, rows.map((r) => {
      const pct = r.manuscript ? Math.min(100, (r.sent / r.manuscript) * 100) : 0;
      const fill = h('div', { class: 'fill' });
      fill.style.width = Math.max(pct, 1.5) + '%';
      const track = h('div', { class: 'track', tabindex: 0, role: 'img', 'aria-label': `${r.what} on ${r.paper}: ${r.sent} of ${r.manuscript} tokens` }, fill);
      hover(track, () => ({ head: `${r.paper} · ${r.what} · ${hhmm(r.ts)}`, rows: [{ cls: 's1', label: 'read / sent', value: `${nf(r.sent)} tok` }, { cls: 's0', label: 'whole manuscript', value: `${nf(r.manuscript)} tok` }, { label: 'whole book', value: `${nf(eff.book_tokens)} tok` }] }));
      return h('div', { class: 'hbar' }, h('span', { class: 'secondary', text: `${r.paper} · ${r.what}` }), track, h('span', { class: 'secondary', text: `${fmtTok(r.sent)} of ${fmtTok(r.manuscript)}` }));
    }));
    const table = simpleTable(['Pass', 'Sent (tok)', 'Manuscript (tok)', 'Whole book (tok)'], rows.map((r) => [`${r.paper} · ${r.what} · ${hhmm(r.ts)}`, r.sent, r.manuscript, eff.book_tokens]));
    return h('div', null, h('p', { class: 'small secondary', text: `On average a pass read ${(avg * 100).toFixed(0)}% of the manuscript. The whole book is about ${fmtTok(eff.book_tokens)} tokens and is never sent.` }), withTable(chart, table));
  }

  function validators(cg) {
    const names = Object.keys(cg.validators);
    if (!names.length) return h('p', { class: 'muted small', text: 'No ChatGPT passes yet.' });
    return h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, ['Check', 'Passed', 'Failed'].map((t, i) => h('th', { class: i ? 'num' : '', text: t })))),
      h('tbody', null, names.map((k) => h('tr', null, h('td', { text: k.replace(/_/g, ' ') }),
        h('td', { class: 'num' }, h('span', { class: 'badge ok', text: '✓ ' + cg.validators[k].pass })),
        h('td', { class: 'num' }, cg.validators[k].fail ? h('span', { class: 'badge bad', text: '✕ ' + cg.validators[k].fail }) : h('span', { class: 'muted', text: '0' })))))));
  }

  function isolation(iso) {
    const ok = iso.ok;
    return h('div', null, h('div', { class: 'status-tile' }, h('div', { class: 'icon ' + (ok ? 'ok' : 'bad'), 'aria-hidden': 'true', text: ok ? '✓' : '!' }),
      h('div', null, h('div', { class: 'big-num', text: `${iso.violations.length} violations` }),
        h('div', { class: 'small secondary', text: ok ? `${iso.checked} logged lookups checked: every chunk used belongs to its own paper or to the book.` : 'A lookup used something that is not that paper\'s own. Look at the list below.' }))),
      iso.violations.length ? h('ul', { class: 'small' }, iso.violations.slice(0, 12).map((v) => h('li', { text: `${v.scope}: ${v.problem}` }))) : null);
  }

  function activitySeries(tl) {
    const sum = (...ks) => tl.days.map((_, i) => ks.reduce((s, k) => s + ((tl.series[k] || [])[i] || 0), 0));
    return [
      { key: 'lookups', label: 'Lookups', cls: 's1', values: sum('retrieve') },
      { key: 'index', label: 'Index refreshes', cls: 's2', values: sum('index_refresh') },
      { key: 'chatgpt', label: 'ChatGPT passes and decisions', cls: 's3', values: sum('revise', 'decision', 'handoff') },
      { key: 'other', label: 'Other (imports, DOIs, errors)', cls: 's0', values: sum('book_ingest', 'doi_added', 'error') },
    ];
  }

  // ---- the Monitor page
  const M = { paper: '', days: 30, live: true, since: 0, timer: null };
  registerPage('monitor', 'Charts and diagnostics', async (box) => {
    await window.OLS.loadState();
    const st = S.st;
    const content = h('div', { class: 'stack' });
    const paperSel = h('select', { 'aria-label': 'Paper' }, h('option', { value: '', text: 'All papers' }), st.papers.map((p) => h('option', { value: p.name, selected: p.name === M.paper, text: p.name })));
    const daysSel = h('select', { 'aria-label': 'Range' }, [[7, 'Last 7 days'], [30, 'Last 30 days'], [90, 'Last 90 days']].map(([v, t]) => h('option', { value: v, selected: v === M.days, text: t })));
    const feed = h('div', { class: 'feed', 'aria-live': 'off' });
    const liveBox = h('input', { type: 'checkbox', checked: M.live });
    const load = async () => {
      content.classList.add('refetching');
      try {
        const m = await api.get(`/api/monitor?days=${M.days}${M.paper ? '&paper=' + encodeURIComponent(M.paper) : ''}`);
        render(content, m);
      } catch (e) { clear(content).append(notice('bad', e.message)); }
      content.classList.remove('refetching');
    };
    paperSel.addEventListener('change', () => { M.paper = paperSel.value; load(); });
    daysSel.addEventListener('change', () => { M.days = Number(daysSel.value); load(); });
    liveBox.addEventListener('change', () => { M.live = liveBox.checked; });
    box.append(h('div', { class: 'page-head' }, h('h1', { text: 'Charts and diagnostics' }), h('span', { class: 'muted small', text: 'For debugging: what the system did, from the local event logs. Metadata only: no manuscript or book text. (The plain story is on the Activity page.)' })),
      h('div', { class: 'row' }, h('label', { class: 'row tight small secondary' }, 'Paper', paperSel), daysSel, h('button', { type: 'button', text: 'Reload', onclick: load })), content,
      card('Live activity', 'New events appear here as the app, Claude or the Telegram bot work.', h('label', { class: 'check small' }, liveBox, 'Follow live'), feed));
    clear(content).append(busy('Loading...'));
    await load();
    M.since = Date.now() / 1000 - 600;
    clearInterval(M.timer);
    M.timer = setInterval(async () => {
      if (!document.body.contains(feed)) { clearInterval(M.timer); return; }
      if (!M.live) return;
      try {
        const r = await api.get('/api/events?since=' + M.since, { poll: true });
        for (const e of r.events) {
          if (e.ts <= M.since) continue;
          feed.prepend(h('div', null, h('span', { class: 'muted', text: hhmm(e.ts) + ' ' }), h('strong', { text: e.type }), ' ', h('span', { class: 'secondary', text: `${e.scope}${e.trigger ? ' · ' + e.trigger : ''}${e.query ? ' · ' + e.query : ''}${e.status ? ' · ' + e.status : ''}` })));
          M.since = Math.max(M.since, e.ts);
        }
        while (feed.children.length > 100) feed.lastChild.remove();
      } catch (e) { /* the page shows errors on reload; the feed just pauses */ }
    }, 4000);
  });

  function render(content, m) {
    clear(content);
    const o = m.overview;
    content.append(h('div', { class: 'grid tiles' },
      tile('Papers indexed', `${o.papers_indexed} of ${o.papers}`, `${o.books} book(s), ${nf(o.book_chunks)} chunks`),
      tile('Last index refresh', o.last_refresh ? ago(o.last_refresh) : 'never'),
      tile('ChatGPT passes', nf(o.passes), `${o.previews} previews, ${nf(o.tokens_in)} tokens in / ${nf(o.tokens_out)} out${o.cost != null ? ', cost ' + o.cost : ''}`),
      tile('Passes that validated', o.pass_rate == null ? '-' : Math.round(o.pass_rate * 100) + '%', 'ChatGPT answers that passed every check'),
      tile('Changes accepted', o.accept_rate == null ? '-' : Math.round(o.accept_rate * 100) + '%', `${o.accepted} accepted, ${o.rejected} rejected`),
      tile('References added', nf(o.doi_added), o.errors ? `${o.errors} error(s) logged` : 'no errors logged')));

    content.append(card('Activity', 'Events per day, by kind.', stackedColumns({ title: 'Activity per day', labels: m.timeline.days.map((d) => d.slice(5)), heads: m.timeline.days, series: activitySeries(m.timeline), empty: 'No activity in this range yet.' })));

    const ih = m.index_health.slice(-40);
    content.append(card('Index health: is each paper index updated on every pass?',
      'One column per refresh. Blue/green/orange = chunks that had to be rewritten; gray = chunks left alone (nothing was re-read). A pass that changed nothing is a flat gray column.',
      stackedColumns({
        title: 'Index refreshes', labels: ih.map((r) => hhmm(r.ts)), heads: ih.map((r) => `${r.paper} · ${hhmm(r.ts)}`),
        series: [{ label: 'Changed', cls: 's1', values: ih.map((r) => r.changed) }, { label: 'Added', cls: 's3', values: ih.map((r) => r.added) },
          { label: 'Removed', cls: 's2', values: ih.map((r) => r.removed) }, { label: 'Unchanged', cls: 's0', values: ih.map((r) => r.unchanged) }],
        extra: (i) => [{ label: 'trigger', value: ih[i].trigger || '-' }, { label: 'file scan', value: ih[i].skipped_scan ? 'skipped (no file changed)' : 'done' }],
        empty: 'No index refresh yet. It happens the first time a paper is queried or revised.',
      })));

    content.append(card('Book usage', 'Which parts of the book the lookups reach. Rows are chapters, cells are sections.',
      ...(m.book_usage.length ? m.book_usage.map((bu) => h('div', null, m.book_usage.length > 1 ? h('h3', { text: bu.book }) : null, heatmap(bu))) : [h('p', { class: 'muted small', text: 'No book imported yet.' })])));

    content.append(card('How much is read: RAG efficiency', null, efficiency(m.efficiency)));

    const rt = m.retrievals.slice(0, 15);
    content.append(card('Recent lookups', 'What was asked, where it was routed, and which chunks came back.',
      rt.length ? h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, ['When', 'Paper', 'Kind', 'Asked', 'Routed / mode', 'Chunks', 'Tokens'].map((t) => h('th', { text: t })))),
        h('tbody', null, rt.map((r) => h('tr', null, h('td', { text: hhmm(r.ts) }), h('td', { text: r.paper || '-' }), h('td', { text: r.kind || '-' }), h('td', { text: (r.query || ((r.parts || []).length ? '(whole part)' : '(outline)')).slice(0, 70) }),
          h('td', { text: [(r.parts || []).join(', '), r.mode].filter(Boolean).join(' · ') || '-' }),
          h('td', null, h('details', null, h('summary', { text: String((r.ids || []).length) }), h('div', { class: 'small mono' }, (r.ids || []).map((id, i) => h('div', { text: id + (r.scores && r.scores[i] != null ? '  ' + r.scores[i] : '') }))))),
          h('td', { class: 'num', text: `${nf(r.tokens)}${r.budget && r.budget < 1e6 ? ' / ' + nf(r.budget) : ''}` }))))))
        : h('p', { class: 'muted small', text: 'No lookups yet.' })));

    const cg = m.chatgpt;
    content.append(card('ChatGPT passes', 'Every answer is checked before you see it: protected LaTeX intact, no new numbers, no dashes, balanced braces, sensible length.',
      h('div', { class: 'grid two' }, h('div', null, h('h3', { text: 'Checks' }), validators(cg)),
        h('div', null, h('h3', { text: 'Your decisions' }), h('p', { class: 'small secondary', text: `${cg.accepted} accepted, ${cg.rejected} rejected.` }),
          cg.rules_rejected.length ? h('div', null, h('h3', { text: 'Book rules you reject most' }), h('ol', { class: 'small' }, cg.rules_rejected.map(([id, n]) => h('li', { text: `${id} (${n}x)` })))) : h('p', { class: 'small muted', text: 'No rejected rules yet.' }))),
      cg.passes.length ? h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, ['When', 'Paper', 'Model', 'In', 'Out', 'Latency', 'Retries', 'Hunks', 'Status'].map((t, i) => h('th', { class: i >= 3 && i <= 7 ? 'num' : '', text: t })))),
        h('tbody', null, cg.passes.slice(-12).reverse().map((p) => h('tr', null, h('td', { text: hhmm(p.ts) }), h('td', { text: p.paper }), h('td', { text: p.model || '-' }), h('td', { class: 'num', text: nf(p.tokens_in) }), h('td', { class: 'num', text: nf(p.tokens_out) }),
          h('td', { class: 'num', text: (p.latency_ms / 1000).toFixed(1) + ' s' }), h('td', { class: 'num', text: String(p.retries) }), h('td', { class: 'num', text: String(p.hunks) }), h('td', { text: p.status }))))))
        : null));

    content.append(card('Isolation audit: no mixing between papers', 'Every lookup logs the chunks it used. Each paper may only use its own chunks and the book\'s.', isolation(m.isolation)));
  }

  window.OLS.charts = { stackedColumns, heatmap, efficiency, tip, hover, withTable, simpleTable, card, el };
}());
