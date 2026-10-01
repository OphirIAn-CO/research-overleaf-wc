/* Paper index + card, and References. Same rules as app.js: text only, never innerHTML. */
(function () {
  'use strict';
  const { h, clear, api, notice, busy, guarded, registerPage, toast, fmtTok, ago, S } = window.OLS;
  const hhmm = (ts) => { const d = new Date(ts * 1000); return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
  const enc = encodeURIComponent;

  // ------------------------------------------------------------------ Paper index and card
  const P = { kind: '' };
  registerPage('paper_index', 'Paper index', async (box) => {
    await window.OLS.loadState();
    if (!S.paper) { box.append(notice('warn', 'Register a paper first (Papers page).')); return; }
    const name = S.paper;
    box.append(h('div', { class: 'page-head' }, h('h1', { text: 'Paper index and card: ' + name }),
      h('span', { class: 'muted small', text: 'What the system knows about this paper, and nothing about any other.' })));

    const viewer = h('section', { class: 'card', 'aria-label': 'Chunk' }, h('p', { class: 'muted', text: 'Click a row to read that chunk.' }));
    const tableBox = h('div');
    const info = h('p', { class: 'small secondary' });
    const kindSel = h('select', { 'aria-label': 'Kind' }, [['', 'All kinds'], ['tex', 'Manuscript'], ['memory', 'Session notes'], ['review', 'Reviews'], ['bib', 'Bibliography']].map(([v, t]) => h('option', { value: v, selected: v === P.kind, text: t })));
    const load = async () => {
      clear(tableBox).append(busy('Refreshing the index...'));
      const r = await api.get(`/api/paper/${enc(name)}/index`);
      const rows = r.chunks.filter((c) => !P.kind || c.kind === P.kind);
      info.textContent = r.last_change ? `Last time anything changed: ${ago(r.last_change)} (${r.last_change_trigger}). Chunks marked "updated" were rewritten then; the rest were left alone.` : 'No change recorded yet.';
      clear(tableBox).append(rows.length ? h('div', { class: 'table-wrap' }, h('table', null,
        h('thead', null, h('tr', null, ['Section', 'Part', 'Where', 'Tokens', 'Hash', ''].map((t, i) => h('th', { class: i === 3 ? 'num' : '', text: t })))),
        h('tbody', null, rows.map((c) => h('tr', null,
          h('td', null, h('button', { type: 'button', class: 'ghost small', text: `${c.kind === 'tex' ? '' : c.kind + ': '}${c.section_path}`, onclick: () => openChunk(viewer, name, c.id) })),
          h('td', { text: c.part || '-' }), h('td', { class: 'mono small', text: `${c.file}:${c.line_start}-${c.line_end}` }), h('td', { class: 'num', text: String(c.tokens) }),
          h('td', { class: 'mono small muted', text: c.hash }), h('td', null, c.recent ? h('span', { class: 'badge ok', text: 'updated' }) : null)))))) : h('p', { class: 'muted small', text: 'Nothing indexed for this kind yet.' }));
    };
    kindSel.addEventListener('change', () => { P.kind = kindSel.value; load().catch((e) => toast(e.message)); });
    box.append(h('section', { class: 'card', 'aria-label': 'Index' },
      h('div', { class: 'row' }, h('h2', { text: 'Index' }), h('span', { class: 'grow' }), kindSel,
        guarded(h('button', { type: 'button', text: 'Re-read the files now' }), async () => { const r = await api.post(`/api/paper/${enc(name)}/refresh`, { force: true }); toast(`${r.added} added, ${r.changed} changed, ${r.removed} removed`); await load(); })),
      info, tableBox));
    box.append(viewer);
    await load().catch((e) => clear(tableBox).append(notice('bad', e.message)));

    // ---- paper card
    const card = await api.get(`/api/paper/${enc(name)}/card`);
    const ta = h('textarea', { class: 'mono', 'aria-label': 'Paper card', rows: '16' });
    const noCard = card.exists ? null : notice('warn', 'No card yet. Start from the template, fill in what you know, and save.');
    ta.value = card.exists ? card.text : '';
    box.append(h('section', { class: 'card', 'aria-label': 'Paper card' }, h('h2', { text: 'Paper card' }),
      h('p', { class: 'small secondary', text: 'Short facts every ChatGPT request for this paper carries: key terms with one spelling, tense and voice, notation, decisions. Claude can draft it from the manuscript; you decide what stays. It never leaves this paper.' }),
      noCard,
      ta, h('div', { class: 'row' },
        h('button', { type: 'button', text: 'Insert the template', onclick: () => { if (!ta.value.trim() || window.confirm('Replace the text with the template?')) ta.value = card.template; } }),
        guarded(h('button', { type: 'button', class: 'primary', text: 'Save the card' }), async () => { await api.post(`/api/paper/${enc(name)}/card`, { text: ta.value }); if (noCard) noCard.remove(); toast('Saved'); }))));

    // ---- what the system learned from the decisions
    const pref = await api.get(`/api/paper/${enc(name)}/preferences`);
    box.append(h('section', { class: 'card', 'aria-label': 'Learned preferences' }, h('h2', { text: 'What the system has learned from your decisions' }),
      h('p', { class: 'small secondary', text: 'This is exactly the note that rides along with the next ChatGPT request for this paper, built only from this paper\'s own accepted and rejected changes. It nudges the model; it never overrides the book or you.' }),
      pref.digest ? h('pre', { class: 'payload mono', text: pref.digest }) : h('p', { class: 'muted small', text: `Nothing yet (${pref.accepted} accepted, ${pref.rejected} rejected so far). A rule you reject twice more than you accept starts appearing here.` }),
      pref.accepted + pref.rejected ? guarded(h('button', { type: 'button', class: 'danger', text: 'Forget these decisions' }), async () => {
        if (!window.confirm('Erase this paper\'s decision history? The manuscript is not touched.')) return;
        const r = await api.post(`/api/paper/${enc(name)}/forget`, {});
        toast(`Forgot ${r.forgotten} decisions`); window.OLS.route();
      }) : null));

    // ---- decision log
    const dec = await api.get(`/api/paper/${enc(name)}/decisions`);
    box.append(h('section', { class: 'card', 'aria-label': 'Decisions' }, h('h2', { text: 'What you accepted and rejected' }),
      h('p', { class: 'small secondary', text: 'Recorded every time you apply or reject a change. Rules you keep rejecting show up on the Monitor page.' }),
      dec.decisions.length ? h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, ['When', 'Decision', 'Why it was suggested', 'Rule', 'Change'].map((t) => h('th', { text: t })))),
        h('tbody', null, dec.decisions.slice(0, 30).map((d) => h('tr', null, h('td', { text: hhmm(d.ts) }), h('td', null, h('span', { class: 'badge ' + (d.status === 'accepted' ? 'ok' : 'bad'), text: d.status })),
          h('td', { text: (d.reasons || []).join('; ') || '-' }), h('td', { class: 'small', text: (d.rules || []).join(', ') || '-' }),
          h('td', { class: 'mono small', text: `${(d.old || '').slice(0, 70).replace(/\s+/g, ' ')}  →  ${(d.new || '').slice(0, 70).replace(/\s+/g, ' ')}` })))))) : h('p', { class: 'muted small', text: 'No decisions yet.' })));
  });

  async function openChunk(viewer, name, id) {
    const c = await api.get(`/api/paper/${enc(name)}/chunk?id=${enc(id)}`);
    clear(viewer).append(h('h2', { text: c.section_path }), h('p', { class: 'small secondary', text: `${c.id} · ${c.file}:${c.line_start}-${c.line_end} · part ${c.part || '-'} · ~${c.tokens} tokens` }),
      h('pre', { class: 'reader mono', tabindex: '0', text: c.text }));
    viewer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // ------------------------------------------------------------------ References
  registerPage('references', 'References', async (box) => {
    await window.OLS.loadState();
    if (!S.paper) { box.append(notice('warn', 'Register a paper first (Papers page).')); return; }
    const name = S.paper;
    const proj = window.OLS.paperObj(name);
    box.append(h('div', { class: 'page-head' }, h('h1', { text: 'References: ' + name }),
      h('span', { class: 'muted small', text: 'Add by DOI here or from your phone with the Telegram bot; both end up in the same bibliography.' })));
    const ta = h('textarea', { 'aria-label': 'DOIs', placeholder: 'Paste one or more DOIs or doi.org links', rows: '3' });
    const results = h('div');
    const recent = h('div');
    const mend = h('input', { type: 'checkbox', checked: !!proj.group });
    const push = h('input', { type: 'checkbox' });
    let found = [];

    const refresh = async () => {
      const r = await api.get(`/api/refs/recent?paper=${enc(name)}`);
      clear(recent).append(r.recent.length ? h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, ['When', 'Added from', 'Cite key', 'DOI'].map((t) => h('th', { text: t })))),
        h('tbody', null, r.recent.map((e) => h('tr', null, h('td', { text: hhmm(e.ts) }), h('td', null, h('span', { class: 'badge', text: e.source }), e.mendeley ? ' + Mendeley' : ''), h('td', { class: 'mono small', text: e.key }), h('td', { class: 'mono small', text: e.doi })))))) :
        h('p', { class: 'muted small', text: `Nothing added through the tools yet. The bibliography has ${r.bib_entries} entries.` }));
    };
    const look = guarded(h('button', { type: 'button', class: 'primary', text: 'Look up' }), async () => {
      clear(results).append(busy('Asking doi.org...'));
      try {
        const r = await api.post('/api/refs/preview', { paper: name, text: ta.value });
        found = r.refs;
        const addable = r.refs.filter((x) => x.status === 'new');
        clear(results).append(h('section', { class: 'card', 'aria-label': 'Found' },
          r.refs.map((x) => h('div', { class: 'stack' },
            h('div', { class: 'row' }, h('span', { class: 'badge ' + (x.status === 'new' ? 'ok' : x.status === 'duplicate' ? 'warn' : 'bad'), text: x.status === 'new' ? 'new' : x.status === 'duplicate' ? 'already in your bibliography' : 'not found' }),
              h('span', { class: 'mono small', text: x.doi }), x.key ? h('strong', { text: '\\cite{' + x.key + '}' }) : null),
            x.title ? h('div', { class: 'small', text: x.title }) : null, x.error ? h('div', { class: 'small muted', text: x.error }) : null,
            x.bibtex ? h('details', null, h('summary', { text: 'BibTeX' }), h('pre', { class: 'payload mono', text: x.bibtex })) : null)),
          addable.length ? h('div', { class: 'stack' },
            r.group ? h('label', { class: 'check small' }, mend, `Also add to the Mendeley group "${r.group}"`) : null,
            h('label', { class: 'check small' }, push, 'Commit and push the bibliography to Overleaf now'),
            h('div', { class: 'row' }, guarded(h('button', { type: 'button', class: 'primary', text: `Add ${addable.length} reference(s)` }), async () => {
              const a = await api.post('/api/refs/add', { paper: name, dois: addable.map((x) => x.doi), mendeley: mend.checked, push: push.checked });
              clear(results).append(h('section', { class: 'card' }, h('h2', { text: 'Done' }), a.messages.map((m) => h('pre', { class: 'small', text: m })), a.push ? notice(a.push.ok ? 'ok' : 'bad', a.push.text) : null));
              ta.value = ''; await refresh();
            }))) : null));
      } catch (e) { clear(results).append(notice('bad', e.message)); throw e; }
    });
    box.append(h('section', { class: 'card', 'aria-label': 'Add by DOI' }, h('h2', { text: 'Add by DOI' }), ta, h('div', { class: 'row' }, look)), results);
    box.append(h('section', { class: 'card', 'aria-label': 'Mendeley' }, h('h2', { text: 'From Mendeley' }),
      h('p', { class: 'small secondary', text: proj.group ? `Pull references already in the Mendeley group "${proj.group}" into the bibliography (only new ones are appended).` : 'No Mendeley group is linked to this paper. Register it with: python3 scripts/common.py add <path> --group "Group name".' }),
      proj.group ? guarded(h('button', { type: 'button', text: 'Pull new references from Mendeley' }), async () => {
        const o = await api.post('/api/refs/mendeley', { paper: name });
        clear(mendOut).append(h('pre', { class: 'small payload', text: o.output })); await refresh();
      }) : null));
    const mendOut = h('div');
    box.lastChild.append(mendOut);
    box.append(h('section', { class: 'card', 'aria-label': 'Recent additions' }, h('h2', { text: 'Recent additions' }), recent));
    await refresh();
  });
}());
