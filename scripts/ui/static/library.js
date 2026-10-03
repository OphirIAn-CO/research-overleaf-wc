/* The literature library (Settings): folders of books, articles and PDFs the author keeps; the app reads them once, cuts them into labelled parts and searches them on
   this computer. Also the folder chooser that "Your files" uses for the material of one paper. Everything the files say goes in as text, never as markup. */
(function () {
  'use strict';
  const { h, clear, api, notice, busy, guarded, toast, waitJob, S } = window.OLS;
  const enc = encodeURIComponent;
  const plural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : (many || one + 's')}`;
  const words = (tokens) => Math.round((tokens || 0) * 0.75);

  // ------------------------------------------------------------------ choosing folders: type or paste a full path, or pick one that is already shared with the app
  // opts: { label, placeholder, save(paths) -> Promise<[folder]>, detail(folder) -> text for a folder that can be read, remove: text of the remove button }
  function folderPicker(opts) {
    const list = h('div', { class: 'stack' });
    const chips = h('div', { class: 'row chips' });
    const input = h('input', { type: 'text', class: 'grow', placeholder: opts.placeholder, 'aria-label': opts.label, autocomplete: 'off', spellcheck: 'false' });
    let folders = [];
    const paths = () => folders.map((f) => f.path);
    const commit = async (next) => {
      folders = await opts.save(next);
      draw();
      if (opts.onSaved) opts.onSaved(folders);
    };
    const add = guarded(h('button', { type: 'button', class: 'primary', text: 'Add this folder' }), async () => {
      const v = input.value.trim();
      if (!v) throw new Error('Type or paste the full path of the folder first.');
      await commit(paths().includes(v) ? paths() : paths().concat(v));
      input.value = '';
      toast('Folder saved');
    });
    input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); add.click(); } });
    let shared = [];
    function draw() {
      clear(list);
      folders.forEach((f) => {
        list.append(h('div', { class: 'row folder-row' },
          h('div', { class: 'grow' }, h('div', { class: 'mono small', text: f.path }),
            f.ok ? h('div', { class: 'small secondary', text: opts.detail ? opts.detail(f) : 'It can be read.' }) : h('div', { class: 'small warn-text', text: f.note || 'It cannot be read.' })),
          guarded(h('button', { type: 'button', class: 'small', text: opts.remove || 'Remove', 'aria-label': 'Remove ' + f.path }), async () => { await commit(paths().filter((x) => x !== f.path)); toast('Removed from the list. Nothing in the folder was touched.'); })));
      });
      clear(chips);
      const offer = shared.filter((x) => !paths().includes(x));
      if (offer.length) {
        chips.append(h('span', { class: 'small secondary', text: 'Shared with the app:' }), ...offer.map((x) => h('button', { type: 'button', class: 'chip small', text: x, title: 'Use this folder', onclick: () => { input.value = x; input.focus(); } })));
      }
    }
    const el = h('div', { class: 'stack' }, list, h('div', { class: 'row' }, input, add), chips);
    return { el, show(f, sharedFolders) { folders = f || []; shared = sharedFolders || []; draw(); } };
  }
  window.OLS.folderPicker = folderPicker;

  // ------------------------------------------------------------------ the card
  function libraryCard(paper) {
    const body = h('div', { class: 'stack' });
    const badgeBox = h('span');
    const state = h('div', { class: 'stack' });
    const out = h('div', { class: 'stack' });
    const docsBox = h('div');
    const searchOut = h('div', { class: 'stack' });
    const bar = h('div', { class: 'progress', hidden: true, role: 'progressbar', 'aria-label': 'Reading the library' }, h('span'));
    let st = null, polling = false;

    const picker = folderPicker({
      label: 'Folder with your books and articles', placeholder: '/Users/you/Documents/Literature',
      detail: (f) => (f.files ? `${plural(f.files, 'file')} found, ${plural(f.docs, 'document')} read (${plural(f.parts, 'part')}).` : 'No books or articles found in it yet (PDF, Word, text, HTML and .bib files are read, in all its subfolders).'),
      save: async (paths) => { const r = await api.post('/api/library/folders', { folders: paths }); st = r.status; paint(); return r.status.folders; },
    });

    function paintBadge() {
      const ready = st && st.docs > 0;
      clear(badgeBox).append(h('span', { class: 'badge ' + (ready ? 'ok' : 'warn'), text: ready ? plural(st.docs, 'document') + ' ready' : st && st.folders.length ? 'not read yet' : 'not set up' }));
    }
    function paint() {
      if (!st) return;
      paintBadge();
      picker.show(st.folders, st.shared);
      clear(state);
      const usable = st.folders.some((f) => f.ok);
      if (st.running) {
        state.append(busy(`Reading ${st.running.done} of ${st.running.total || '...'}: ${st.running.current || ''}`));
        return;
      }
      if (st.in_docker && st.folders.some((f) => !f.ok && /Docker/.test(f.note || ''))) {
        state.append(notice('warn', 'The app runs in Docker and only sees the folders you share with it. Share the folder read-only, then restart the app (the exact commands are shown under the folder).'));
      }
      if (!usable) return;
      const waiting = st.new + st.changed + st.removed;
      const sum = st.docs ? `${plural(st.docs, 'document')}, ${plural(st.parts, 'part')} of about ${words(st.tokens / Math.max(1, st.parts)).toLocaleString()} words each, ready to search.` : 'Nothing has been read yet.';
      const bits = [st.new ? plural(st.new, 'new file') : '', st.changed ? plural(st.changed, 'changed file') : '', st.removed ? plural(st.removed, 'file') + ' gone' : ''].filter(Boolean).join(', ');
      state.append(h('p', { class: 'small', text: sum + (waiting ? ' Waiting to be read: ' + bits + '.' : st.docs ? ' Everything is up to date.' : '') }));
      if (st.failed) state.append(h('p', { class: 'small secondary', text: `${plural(st.failed, 'file')} could not be read; the list below says why.` }));
      st.notes.forEach((n) => state.append(notice('warn', n)));
      if (st.folders.some((f) => f.ok) && st.docs === 0 && waiting) state.append(h('p', { class: 'small secondary', text: 'The first reading of a big library takes a few minutes (about a second per article). It only reads what is new or changed afterwards. Nothing leaves this computer.' }));
    }
    async function refresh(q) {
      const r = await api.get('/api/library' + (q ? '?q=' + enc(q) : ''), { poll: true });
      st = r.status;
      paint();
      if (st.running && !polling) poll();
      return r;
    }
    async function poll() {
      polling = true;
      try {
        while (st && st.running && card.isConnected) { await new Promise((r) => setTimeout(r, 1500)); await refresh(); }
      } finally { polling = false; }
      if (card.isConnected) { drawDocs(); }
    }

    const readBtn = guarded(h('button', { type: 'button', class: 'primary', text: 'Read the folders' }), async () => {
      clear(out);
      bar.hidden = false; bar.firstChild.style.width = '0%';
      const lead = busy('Reading...');
      out.append(lead);
      try {
        const r = await api.post('/api/library/index', {});
        const res = await waitJob(r.job, (j) => { if (j.progress) { bar.firstChild.style.width = Math.round(100 * j.progress[0] / Math.max(1, j.progress[1])) + '%'; clear(lead).append(busy(`Reading ${j.progress[0]} of ${j.progress[1]}...`)); } });
        const did = [res.new ? plural(res.new, 'new file') : '', res.changed ? plural(res.changed, 'changed file') : '', res.removed ? plural(res.removed, 'file') + ' forgotten' : ''].filter(Boolean).join(', ');
        clear(out).append(notice(res.failed ? 'warn' : 'ok', h('strong', { text: 'Done. ' }), (did || 'Nothing had changed') + `. The library has ${plural(res.docs, 'document')} and ${plural(res.parts, 'part')}.`,
          res.failed ? ` ${plural(res.failed, 'file')} could not be read (see the list).` : '', res.notes.length ? ' ' + res.notes.join(' ') : ''));
      } catch (e) { clear(out).append(notice('bad', e.message)); } finally { bar.hidden = true; }
      await refresh(); drawDocs();
    });
    const startOver = guarded(h('button', { type: 'button', class: 'small', text: 'Forget it all and read again' }), async () => {
      if (!window.confirm('Forget what was read and read every file again? Your files are not touched. A big library takes a few minutes.')) return;
      const r = await api.post('/api/library/index', { rebuild: true });
      clear(out).append(busy('Reading everything again...'));
      const res = await waitJob(r.job, (j) => { if (j.progress) bar.firstChild.style.width = Math.round(100 * j.progress[0] / Math.max(1, j.progress[1])) + '%'; });
      bar.hidden = true;
      clear(out).append(notice('ok', `Done. ${plural(res.docs, 'document')} and ${plural(res.parts, 'part')}.`));
      await refresh(); drawDocs();
    });

    // ---- the documents
    const filter = h('input', { type: 'search', placeholder: 'Find by title, author, year or file name', 'aria-label': 'Find a document', class: 'grow' });
    let timer = null;
    filter.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => drawDocs(filter.value.trim()).catch((e) => toast(e.message)), 250); });
    async function drawDocs(q) {
      const r = await api.get('/api/library' + (q ? '?q=' + enc(q) : ''), { poll: true });
      clear(docsBox);
      if (!r.docs.length) { docsBox.append(h('p', { class: 'muted small', text: q ? 'No document matches.' : 'No documents yet.' })); return; }
      const rows = [];
      r.docs.forEach((d) => {
        const open = h('tr', { class: 'parts-row', hidden: true }, h('td', { colspan: '5' }));
        const see = d.parts ? h('button', { type: 'button', class: 'small', text: 'See the parts' }) : null;
        if (see) {
          guarded(see, async () => {
            if (!open.hidden) { open.hidden = true; see.textContent = 'See the parts'; return; }
            const p = await api.get('/api/library/parts?doc=' + enc(d.id));
            clear(open.firstChild).append(h('ul', { class: 'small parts' }, p.parts.map((x) => h('li', null, h('strong', { text: x.where }), h('span', { class: 'muted', text: ` · about ${words(x.tokens)} words` }), h('div', { class: 'muted', text: x.preview })))));
            open.hidden = false; see.textContent = 'Hide the parts';
          });
        }
        const meta = [d.authors && d.authors.length < 70 ? d.authors : '', d.year, d.doi].filter(Boolean).join(' · ');
        rows.push(h('tr', null,
          h('td', null, h('div', { text: d.title || d.rel }), meta ? h('div', { class: 'small secondary', text: meta }) : null, h('div', { class: 'mono small muted', text: d.rel })),
          h('td', { class: 'num', text: d.pages ? String(d.pages) : '-' }), h('td', { class: 'num', text: d.parts ? String(d.parts) : '-' }),
          h('td', { class: 'small', text: d.note || (d.source === 'bib' ? 'Details from your .bib file' : d.source === 'name' ? 'Details from the file name' : '') }),
          h('td', null, see)), open);
      });
      docsBox.append(h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, ['Document', 'Pages', 'Parts', 'Note', ''].map((t) => h('th', { text: t })))), h('tbody', null, rows))));
      if (r.matching > r.docs.length) docsBox.append(h('p', { class: 'small muted', text: `Showing ${r.docs.length} of ${r.matching}. Type in the box to find the rest.` }));
    }

    // ---- a trial search
    const q = h('input', { type: 'text', class: 'grow', placeholder: 'What do my articles say about...', 'aria-label': 'Search the library' });
    const search = guarded(h('button', { type: 'button', text: 'Search' }), async () => {
      if (!q.value.trim()) throw new Error('Type what to look for.');
      clear(searchOut).append(busy('Searching...'));
      const r = await api.get('/api/library/search?q=' + enc(q.value.trim()) + (paper ? '&paper=' + enc(paper) : ''));
      clear(searchOut);
      if (!r.chunks.length) { searchOut.append(h('p', { class: 'muted small', text: 'Nothing in the library matches those words.' })); return; }
      r.chunks.forEach((c) => searchOut.append(h('div', { class: 'hit' },
        h('div', { class: 'row tight' }, h('strong', { text: c.label }), h('span', { class: 'small secondary', text: c.where }), c.cite_key ? h('code', { class: 'cite', text: '\\cite{' + c.cite_key + '}' }) : null),
        h('div', { class: 'small', text: c.text.length > 420 ? c.text.slice(0, 420) + '...' : c.text }))));
    });
    q.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); search.click(); } });

    // ---- when a request may use the library (a setting of the open paper)
    const mine = paper && window.OLS.paperObj(paper);
    const modeSel = h('select', { 'aria-label': 'When requests use my library' }, [['auto', 'When I ask for sources, citations or my reading (recommended)'], ['always', 'In every request'], ['never', 'Never']].map(([v, t]) => h('option', { value: v, text: t })));
    modeSel.value = (mine && mine.settings && mine.settings.use_library) || 'auto';
    modeSel.addEventListener('change', async () => { try { await api.post('/api/settings', { paper, use_library: modeSel.value }); await window.OLS.loadState(); toast('Saved'); } catch (e) { toast(e.message); } });

    // ---- looking for references on the internet (a setting of the open paper)
    const onlineSel = h('select', { 'aria-label': 'Searching the internet for references' }, [['auto', 'When I ask for references (recommended)'], ['never', 'Never']].map(([v, t]) => h('option', { value: v, text: t })));
    onlineSel.value = (mine && mine.settings && mine.settings.use_online) || 'auto';
    onlineSel.addEventListener('change', async () => { try { await api.post('/api/settings', { paper, use_online: onlineSel.value }); await window.OLS.loadState(); toast('Saved'); } catch (e) { toast(e.message); } });

    const card = h('section', { class: 'card', 'aria-label': 'Literature library' },
      h('div', { class: 'row' }, h('h2', { text: 'Literature library' }), badgeBox),
      h('p', { class: 'small secondary', text: 'Point the app at the folder where you keep books, articles and PDFs (subfolders are fine). It reads each file once on this computer, cuts it into small labelled parts with their pages, and keeps a search of them. A request can then draw on the right parts of your reading, say where they come from, and cite them. Your files are only read, never changed, and nothing is sent to OpenAI except the few parts a request uses, and the request shows which.' }),
      picker.el, state, h('div', { class: 'row' }, readBtn, bar), out,
      paper ? h('div', { class: 'row' }, h('label', { class: 'row tight small' }, 'Use the library for ' + paper + ':', modeSel), h('span', { class: 'small secondary', text: 'A document that is not in the paper\'s bibliography yet is added to it when you save a text that cites it.' })) : null,
      paper ? h('div', { class: 'row' }, h('label', { class: 'row tight small' }, 'Search the internet for ' + paper + ':', onlineSel), h('span', { class: 'small secondary', text: 'Only a few search words leave this computer, and only when you ask for references.' })) : null,
      h('details', null, h('summary', { text: 'Documents' }), h('div', { class: 'stack' }, h('div', { class: 'row' }, filter), docsBox)),
      h('details', null, h('summary', { text: 'Try a search' }), h('div', { class: 'stack' }, h('div', { class: 'row' }, q, search), searchOut)),
      h('details', null, h('summary', { text: 'More' }), h('div', { class: 'row' }, startOver, h('span', { class: 'small secondary', text: 'Use it if the results look wrong or you moved many files.' }))));
    refresh().then(() => drawDocs()).catch((e) => clear(state).append(notice('bad', e.message)));
    return card;
  }
  window.OLS.libraryCard = libraryCard;
}());
