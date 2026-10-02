/* Paper index + card, and References. Same rules as app.js: text only, never innerHTML. */
(function () {
  'use strict';
  const { h, clear, api, notice, busy, guarded, registerPage, toast, fmtTok, ago, tile, S } = window.OLS;
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
    const kindSel = h('select', { 'aria-label': 'Kind' }, [['', 'All kinds'], ['tex', 'Manuscript'], ['memory', 'Session notes'], ['review', 'Reviews'], ['bib', 'Bibliography'], ['source', 'Added files'], ['project', 'Project files']].map(([v, t]) => h('option', { value: v, selected: v === P.kind, text: t })));
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

  // ------------------------------------------------------------------ References: paste a DOI, it is added everywhere
  async function copyText(t, what) {
    try { await navigator.clipboard.writeText(t); toast((what || 'Text') + ' copied'); } catch (e) { toast('Select it and copy by hand'); }
  }

  /* One finished DOI: what to cite, and where it went. Used by the chat and by this page. */
  function refCard(res, push) {
    if (res.status === 'invalid') return notice('warn', res.message);
    if (res.status === 'error') return notice('bad', h('strong', { text: res.doi + ': ' }), 'I could not find it. Check the DOI and try again. ', h('span', { class: 'muted small', text: res.message }));
    const head = res.status === 'duplicate'
      ? h('p', { class: 'small secondary', text: 'This one was already in your bibliography. Cite it with:' })
      : h('div', null, h('div', { class: 'strong', text: res.title }), h('div', { class: 'small secondary', text: [res.authors, res.year, res.journal].filter(Boolean).join(' · ') }));
    const m = res.mendeley || {};
    const tick = (ok, text) => h('li', { class: 'chk ' + (ok === true ? 'ok' : ok === false ? 'warn' : '') }, h('span', { class: 'mark', 'aria-hidden': 'true', text: ok === true ? '✓' : ok === false ? '!' : '–' }), h('span', { text }));
    const where = res.status === 'duplicate' ? null : h('ul', { class: 'checks' },
      tick(true, 'In your bibliography (references.bib)'),
      m.state === 'added' ? tick(true, `In Mendeley, group “${m.group}”`) : m.state === 'failed' ? tick(false, `Not in Mendeley yet (group “${m.group}”). It is waiting and will be tried again.`) :
        m.state === 'not_linked' ? tick(null, 'Not linked to a Mendeley group, so not added there') : tick(null, 'Adding to Mendeley is switched off'),
      push ? (push.ok ? tick(true, 'Sent to Overleaf') : tick(false, push.friendly || 'Not sent to Overleaf yet: ' + push.text.slice(0, 120))) : tick(null, 'Not sent to Overleaf yet (the bibliography changed on this computer)'));
    return h('div', { class: 'refcard' }, head,
      res.cite ? h('div', { class: 'cite-row' }, h('span', { class: 'small secondary', text: 'Cite it in Overleaf with' }), h('code', { class: 'cite', text: res.cite }),
        h('button', { type: 'button', class: 'primary small', text: 'Copy', onclick: () => copyText(res.cite, 'Citation') })) : null,
      where, res.bibtex ? h('details', null, h('summary', { text: 'BibTeX entry' }), h('pre', { class: 'payload mono small', text: res.bibtex })) : null);
  }
  window.OLS.refCard = refCard;

  // ------------------------------------------------------------------ the author's files: articles, tables, notes and code the requests can read
  const readB64 = (file) => new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result).split(',')[1] || '');
    fr.onerror = () => reject(new Error('I could not read ' + file.name + '.'));
    fr.readAsDataURL(file);
  });
  const MAX_UPLOAD_MB = 25;
  async function uploadFile(paper, file, opts) {
    if (file.size > MAX_UPLOAD_MB * 1048576) throw new Error(`${file.name} is over the ${MAX_UPLOAD_MB} MB limit. Overleaf projects have a size limit too, so add only the pages you need.`);
    const data = await readB64(file);
    return api.post('/api/files/upload', Object.assign({ paper, name: file.name, data }, opts || {}));
  }
  window.OLS.files = { upload: uploadFile };
  const bytes = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB');
  const FILE_TYPES = '.pdf,.docx,.txt,.md,.markdown,.csv,.tsv,.json,.ipynb,.py,.r,.rmd,.m,.jl,.sql,.sh,.yaml,.yml,.toml,.tex,.bib,.rst,.png,.jpg,.jpeg,.gif,.svg,.webp';

  function filesCard(name) {
    const list = h('div');
    const out = h('div', { class: 'stack' });
    const picker = h('input', { type: 'file', multiple: true, hidden: true, 'aria-hidden': 'true', tabindex: '-1', accept: FILE_TYPES });
    const folderBox = h('textarea', { rows: '3', 'aria-label': 'Folders with your code and results', placeholder: '/Users/you/Documents/analysis' });
    const folderOut = h('div', { class: 'stack' });
    const modeSel = h('select', { 'aria-label': 'When requests use my files' }, [['auto', 'When I mention them (recommended)'], ['always', 'In every request'], ['never', 'Never']].map(([v, t]) => h('option', { value: v, text: t })));
    const autoSend = h('input', { type: 'checkbox', 'aria-label': 'Send added files to Overleaf automatically' });
    const parts = new Map();

    const showFolders = (folders) => {
      clear(folderOut).append(...folders.map((f) => notice(f.ok ? 'ok' : 'warn', h('span', { class: 'mono small', text: f.path }), f.ok ? ' can be read.' : ' ' + f.note)));
    };
    async function show() {
      const r = await api.get(`/api/files?paper=${enc(name)}`);
      modeSel.value = r.use_files; autoSend.checked = r.send_uploads;
      if (document.activeElement !== folderBox) folderBox.value = r.folders.map((f) => f.path).join('\n');
      showFolders(r.folders);
      clear(list);
      if (!r.files.length) { list.append(h('p', { class: 'muted small', text: `No files yet. Add an article, a table or your notes, or drop them on the chat. They are saved in the ${r.folder} folder of your project.` })); return; }
      const rows = [];
      r.files.forEach((f) => {
        const where = f.upload ? `Added here · ${r.folder}/` : f.file.startsWith('/') ? 'Other folder · ' + f.file : 'In your project · ' + f.file;
        const open = h('tr', { class: 'parts-row', hidden: true }, h('td', { colspan: '5' }));
        const see = f.parts ? h('button', { type: 'button', class: 'small', text: 'See the parts', onclick: async (ev) => {
          const btn = ev.currentTarget;
          if (!open.hidden) { open.hidden = true; btn.textContent = 'See the parts'; return; }
          btn.disabled = true;
          try {
            const p = await api.get(`/api/files/parts?paper=${enc(name)}&file=${enc(f.file)}`);
            clear(open.firstChild).append(h('ul', { class: 'small parts' }, p.parts.map((x) => h('li', null, h('strong', { text: x.where }), h('span', { class: 'muted', text: ` · about ${Math.round(x.tokens * 0.75)} words` }), h('div', { class: 'muted', text: x.preview })))));
            open.hidden = false; btn.textContent = 'Hide the parts';
          } catch (e) { toast(e.message); }
          btn.disabled = false;
        } }) : null;
        const del = f.upload ? guarded(h('button', { type: 'button', class: 'small', text: 'Remove' }), async () => {
          if (!window.confirm(`Remove ${f.name} from this paper? It leaves Overleaf too the next time you send.`)) return;
          const rm = await api.post('/api/files/remove', { paper: name, file: f.file });
          toast(rm.note); await show(); await window.OLS.loadState();
        }) : null;
        rows.push(h('tr', null, h('td', null, h('div', { class: 'mono small', text: f.name }), h('div', { class: 'muted small', text: where })),
          h('td', { class: 'num', text: f.reads ? String(f.parts) : '-' }), h('td', { class: 'num', text: f.reads ? f.words.toLocaleString() : bytes(f.size) }),
          h('td', { class: 'small', text: f.note }), h('td', null, h('div', { class: 'row tight' }, see, del))), open);
      });
      list.append(h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, ['File', 'Parts', 'Words', 'Note', ''].map((t) => h('th', { text: t })))), h('tbody', null, rows))));
    }
    const add = async (fileList) => {
      for (const f of Array.from(fileList || [])) {
        const line = h('div', null, busy(`Adding ${f.name}...`));
        out.append(line);
        try {
          const r = await uploadFile(name, f, {});
          clear(line).append(notice(r.push && !r.push.sent && r.push.level !== 'ok' ? 'warn' : 'ok', h('strong', { text: f.name + ': ' }),
            r.file && r.file.parts ? `${r.file.parts} parts, about ${r.file.words.toLocaleString()} words. ` : (r.file && r.file.note ? r.file.note + ' ' : 'saved. '),
            r.push ? (r.push.friendly || r.push.summary || '') : 'It goes to Overleaf with your next send.'));
        } catch (e) { clear(line).append(notice('bad', h('strong', { text: f.name + ' was not added. ' }), e.message)); }
      }
      await show(); await window.OLS.loadState();
    };
    picker.addEventListener('change', () => { add(picker.files); picker.value = ''; });
    modeSel.addEventListener('change', async () => { try { await api.post('/api/settings', { paper: name, use_files: modeSel.value }); toast('Saved'); } catch (e) { toast(e.message); } });
    autoSend.addEventListener('change', async () => { try { await api.post('/api/settings', { paper: name, send_uploads: autoSend.checked }); toast(autoSend.checked ? 'Added files will go to Overleaf at once' : 'Added files wait for your next send'); } catch (e) { toast(e.message); autoSend.checked = !autoSend.checked; } });
    const card = h('section', { class: 'card', 'aria-label': 'Your files' }, h('h2', { text: 'Your files' }),
      h('p', { class: 'small secondary', text: 'Add articles you cite, tables of results, notes or code. Each file is saved in the references folder of your project, sent to Overleaf, and split into small labelled parts, so a request can read just the part it needs ("the methods of that article", "rows 1 to 25 of results.csv").' }),
      h('div', { class: 'row' }, h('button', { type: 'button', class: 'primary', text: 'Add files', onclick: () => picker.click() }), picker,
        h('label', { class: 'row tight small' }, autoSend, ' Send added files to Overleaf at once')),
      out, list,
      h('h3', { text: 'When a request may use them' }),
      h('div', { class: 'row' }, modeSel, h('span', { class: 'small secondary', text: 'Nothing goes to OpenAI unless a request uses it, and the request shows which parts.' })),
      h('h3', { text: 'Code and results in other folders' }),
      h('p', { class: 'small secondary', text: 'If your analysis lives outside the Overleaf project, list its folders here (one full path per line). Inside Docker, the folder must be shared first: ./ui.sh folders add <folder>. Passwords, keys and hidden folders are never read.' }),
      folderBox, folderOut,
      guarded(h('button', { type: 'button', text: 'Save the folders' }), async () => {
        const r = await api.post('/api/files/folders', { paper: name, folders: folderBox.value.split('\n').map((x) => x.trim()).filter(Boolean) });
        showFolders(r.folders); toast('Saved'); await show();
      }));
    show().catch((e) => clear(list).append(notice('bad', e.message)));
    return card;
  }

  registerPage('references', 'References', async (box) => {
    await window.OLS.loadState();
    if (!S.paper) { box.append(notice('warn', 'Add a paper first (Settings).')); return; }
    const name = S.paper;
    const proj = window.OLS.paperObj(name);
    box.append(h('div', { class: 'page-head' }, h('h1', { text: 'References: ' + name }),
      h('span', { class: 'muted small', text: 'Paste a DOI and it goes into your bibliography, your Mendeley group and Overleaf. You get the \\cite{...} to paste into your text.' })));
    const ta = h('textarea', { 'aria-label': 'DOIs', placeholder: 'Paste one or more DOIs or doi.org links', rows: '3' });
    const results = h('div', { class: 'stack' });
    const waiting = h('div');
    const recent = h('div');
    const group = h('div');

    const showWaiting = async () => {
      const r = await api.get(`/api/refs/pending?paper=${enc(name)}`);
      clear(waiting);
      if (!r.pending.length) return;
      waiting.append(notice('warn', h('strong', { text: `${r.pending.length} reference${r.pending.length === 1 ? ' is' : 's are'} in your bibliography but not in Mendeley yet. ` }),
        r.pending.map((x) => x.key || x.doi).join(', ') + '. ',
        guarded(h('button', { type: 'button', text: 'Try again now' }), async () => { const t = await api.post('/api/refs/retry', { paper: name }); toast(`${t.done} sent to Mendeley, ${t.left} still waiting`); await showWaiting(); })));
    };
    const showGroup = () => {
      clear(group).append(proj.group ? h('p', { class: 'small secondary' }, `New references are also added to the Mendeley group “${proj.group}”. `, h('button', { type: 'button', class: 'ghost small', text: 'Change', onclick: () => pickGroup() })) :
        notice('warn', 'This paper is not linked to a Mendeley group, so references only go into the bibliography. ', h('button', { type: 'button', class: 'small', text: 'Link a group', onclick: () => pickGroup() })));
    };
    const pickGroup = async () => {
      try {
        const g = await api.get('/api/refs/groups');
        const sel = h('select', { 'aria-label': 'Mendeley group' }, g.groups.map((x) => h('option', { value: x.id, text: x.name })));
        clear(group).append(h('div', { class: 'row' }, sel, guarded(h('button', { type: 'button', class: 'primary', text: 'Use this group' }), async () => {
          const opt = sel.options[sel.selectedIndex];
          await api.post('/api/refs/link', { paper: name, group_id: sel.value, group_name: opt.text }); toast('Linked'); await window.OLS.loadState(); window.OLS.route();
        })));
      } catch (e) { toast(e.message); }
    };

    const refresh = async () => {
      const r = await api.get(`/api/refs/recent?paper=${enc(name)}`);
      clear(recent).append(r.recent.length ? h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, ['When', 'Added from', 'Cite key', 'DOI'].map((t) => h('th', { text: t })))),
        h('tbody', null, r.recent.map((e) => h('tr', null, h('td', { text: hhmm(e.ts) }), h('td', null, h('span', { class: 'badge', text: { telegram: 'your phone', web: 'this app' }[e.source] || e.source }), e.mendeley ? ' + Mendeley' : ''), h('td', { class: 'mono small', text: e.key }), h('td', { class: 'mono small', text: e.doi })))))) :
        h('p', { class: 'muted small', text: `Nothing added through the tools yet. The bibliography has ${r.bib_entries} entries.` }));
    };
    const add = guarded(h('button', { type: 'button', class: 'primary', text: 'Add' }), async () => {
      const dois = [...new Set((ta.value.match(/10\.\d{4,9}\/[^\s"<>]+/g) || []).map((d) => d.replace(/[.,;)]+$/, '')))];
      if (!dois.length) throw new Error('I did not find a DOI in that text.');
      clear(results).append(busy('Looking up and adding...'));
      try {
        const r = await api.post('/api/refs/add', { paper: name, dois });
        clear(results).append(...[...r.results.map((x) => refCard(x, r.push)), r.push && r.push.friendly ? notice('warn', r.push.friendly, ' ', h('a', { href: '#/settings', text: 'Open Settings' })) : null].filter(Boolean));
        ta.value = '';
        await Promise.all([refresh(), showWaiting(), window.OLS.loadState()]);
      } catch (e) { clear(results).append(notice('bad', e.message)); throw e; }
    });
    box.append(h('section', { class: 'card', 'aria-label': 'Add by DOI' }, h('h2', { text: 'Add a reference' }), ta, h('div', { class: 'row' }, add)), results, waiting, group);
    box.append(filesCard(name));
    box.append(h('section', { class: 'card', 'aria-label': 'Mendeley' }, h('h2', { text: 'From Mendeley' }),
      h('p', { class: 'small secondary', text: proj.group ? `Pull references that are already in the Mendeley group “${proj.group}” into the bibliography (only new ones are added).` : 'Link a Mendeley group to pull references from it.' }),
      proj.group ? guarded(h('button', { type: 'button', text: 'Pull new references from Mendeley' }), async () => {
        const o = await api.post('/api/refs/mendeley', { paper: name });
        clear(mendOut).append(h('pre', { class: 'small payload', text: o.output })); await refresh();
      }) : null));
    const mendOut = h('div');
    box.lastChild.append(mendOut);
    box.append(h('section', { class: 'card', 'aria-label': 'Recent additions' }, h('h2', { text: 'Recent additions' }), recent));
    showGroup();
    await Promise.all([refresh(), showWaiting()]);
  });

  // ------------------------------------------------------------------ Activity: what happened, in sentences
  registerPage('activity', 'Activity', async (box) => {
    await window.OLS.loadState();
    const paper = S.activityAll ? '' : (S.paper || '');
    const days = 30;
    const a = await api.get(`/api/activity?days=${days}` + (paper ? `&paper=${enc(paper)}` : ''));
    const scope = h('select', { 'aria-label': 'Which papers' }, h('option', { value: 'one', text: S.paper ? `Only ${S.paper}` : 'This paper' }), h('option', { value: 'all', text: 'All my papers' }));
    scope.value = S.activityAll ? 'all' : 'one';
    scope.addEventListener('change', () => { S.activityAll = scope.value === 'all'; window.OLS.route(); });
    box.append(h('div', { class: 'page-head' }, h('h1', { text: 'Activity' }), h('div', { class: 'row' }, S.paper ? scope : null, h('a', { class: 'small', href: '#/monitor', text: 'Charts and diagnostics' }))));
    const s = a.summary;
    box.append(h('div', { class: 'grid tiles' },
      tile('Requests to OpenAI', String(s.requests), `last ${days} days`), tile('Words sent', fmtTok(s.tokens_in * 0.75), `${fmtTok(s.tokens_out * 0.75)} words came back`),
      tile('Changes kept', String(s.accepted), `${s.rejected} set aside`), tile('References added', String(s.references)),
      s.cost != null ? tile('Cost so far', String(s.cost), 'in your price units') : null));
    if (a.waiting && (a.waiting.suggestions || a.waiting.handed_to_claude || a.waiting.mendeley)) {
      box.append(notice('warn', h('strong', { text: 'Waiting for you: ' }), [a.waiting.suggestions ? `${a.waiting.suggestions} suggestion${a.waiting.suggestions === 1 ? '' : 's'} not decided yet` : null,
        a.waiting.handed_to_claude ? `${a.waiting.handed_to_claude} handed to Claude` : null, a.waiting.mendeley ? `${a.waiting.mendeley} reference${a.waiting.mendeley === 1 ? '' : 's'} not in Mendeley yet` : null].filter(Boolean).join(' · ') + '.'));
    }
    if (!a.items.length) { box.append(notice('ok', 'Nothing has happened yet. Ask for a change in the chat and it shows up here.')); return; }
    let day = null, card = null;
    for (const it of a.items) {
      if (it.day !== day) { day = it.day; box.append(h('h2', { class: 'section-h', text: day })); card = h('section', { class: 'card feed-card' }); box.append(card); }
      card.append(h('div', { class: 'feed-row' }, h('span', { class: 'mono small muted', text: it.time }), S.activityAll ? h('span', { class: 'badge', text: it.paper }) : null, h('span', { class: 'grow', text: it.text })));
    }
  });
}());
