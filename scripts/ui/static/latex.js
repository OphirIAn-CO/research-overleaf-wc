/* The LaTeX pane: the paper's source (.tex, .bib) next to the PDF made from it, on this computer, like TeXShop. Edit on the left, the PDF on the right; the assistant's saved
   changes show up in the source (marked for a moment), the PDF is made again, and "Send to Overleaf" works as always. Nothing here talks to Overleaf.
   The editor is a plain text box with a coloured copy of the same text behind it, so there is nothing to download or trust from a third party. */
(function () {
  'use strict';
  const { h, clear, api, toast, S, waitJob, notice, store } = window.OLS;
  const enc = encodeURIComponent;
  const KEY = 'ols-lx-';
  const setting = (k, d) => { const v = store.get(KEY + k); return v == null ? d : v; };
  const BIG = 6000;                                         // lines: past this the text is shown without colours, so typing stays quick

  // ------------------------------------------------------------------ colours for LaTeX, one line at a time
  const TOKEN = new RegExp([
    '(%.*$)',                                                                                  // 1 comment
    '(\\\\(?:chapter|section|subsection|subsubsection|paragraph|part)\\*?(?:\\[[^\\]]*\\])?\\{[^}]*\\})',   // 2 headings
    '(\\\\(?:begin|end)\\{[^}]*\\})',                                                          // 3 environments
    '(\\\\(?:cite[a-zA-Z]*|ref|eqref|autoref|cref|Cref|pageref|label|input|include|includegraphics|bibliography|usepackage|documentclass)\\*?(?:\\[[^\\]]*\\])*\\{[^}]*\\})',   // 4 keys and files
    '(\\\\[a-zA-Z@]+\\*?|\\\\.)',                                                              // 5 other commands (and \\% \\$ ...)
    '(\\$\\$[^$]*\\$\\$|\\$[^$]*\\$)',                                                         // 6 maths on one line
  ].join('|'), 'g');
  const CLASS = [null, 'c', 'h', 'e', 'k', 'x', 'm'];       // comment, heading, environment, key or file, other command, maths
  function colour(line) {
    if (!line) return ['​'];                                                              // an empty line still needs a height
    const out = [];
    let at = 0, m;
    TOKEN.lastIndex = 0;
    while ((m = TOKEN.exec(line))) {
      if (m.index > at) out.push(line.slice(at, m.index));
      let g = 1;
      while (g < 7 && m[g] === undefined) g++;
      out.push(h('span', { class: 't-' + CLASS[g], text: m[0] }));
      at = m.index + m[0].length;
      if (m[0].length === 0) TOKEN.lastIndex++;
    }
    if (at < line.length) out.push(line.slice(at));
    return out;
  }

  /* The line numbers of `b` that are not in `a`, i.e. what a change touched. Common start and end are skipped; the middle is compared by longest common run. */
  function changedLines(a, b) {
    let s = 0;
    while (s < a.length && s < b.length && a[s] === b[s]) s++;
    let ea = a.length, eb = b.length;
    while (ea > s && eb > s && a[ea - 1] === b[eb - 1]) { ea--; eb--; }
    const A = a.slice(s, ea), B = b.slice(s, eb), out = new Set();
    if (!B.length) {                                               // only lines were taken out: mark the two lines that now meet there
      if (A.length) [s, s + 1].forEach((n) => { if (n >= 1 && n <= b.length) out.add(n); });
      return out;
    }
    if (A.length * B.length > 4e6) { B.forEach((_, i) => out.add(s + i + 1)); return out; }
    const w = B.length + 1, t = new Uint32Array((A.length + 1) * w);
    for (let i = A.length - 1; i >= 0; i--) for (let j = B.length - 1; j >= 0; j--) t[i * w + j] = A[i] === B[j] ? t[(i + 1) * w + j + 1] + 1 : Math.max(t[(i + 1) * w + j], t[i * w + j + 1]);
    const same = new Set();
    for (let i = 0, j = 0; i < A.length && j < B.length;) {
      if (A[i] === B[j]) { same.add(j); i++; j++; } else if (t[(i + 1) * w + j] >= t[i * w + j + 1]) i++; else j++;
    }
    B.forEach((_, j) => { if (!same.has(j)) out.add(s + j + 1); });
    return out;
  }
  const inRanges = (ranges) => { const s = new Set(); (ranges || []).forEach(([a, b]) => { for (let i = a; i <= b; i++) s.add(i); }); return s; };

  // ------------------------------------------------------------------ the editor
  function editor(on) {
    const over = h('div', { class: 'lx-over', 'aria-hidden': 'true' });
    const ta = h('textarea', { class: 'lx-ta', spellcheck: 'false', autocapitalize: 'off', autocomplete: 'off', autocorrect: 'off', wrap: 'soft', rows: '1', 'aria-label': 'LaTeX source' });
    const box = h('div', { class: 'lx-ed' }, h('div', { class: 'lx-stack' }, over, ta));
    let lines = [''], plain = false, cur = 0;
    const marks = { chg: new Set(), err: new Map(), flash: new Set() };
    const cls = (n) => 'l' + (marks.chg.has(n) ? ' chg' : '') + (marks.err.has(n) ? ' err' : '') + (marks.flash.has(n) ? ' flash' : '') + (n === cur ? ' cur' : '');
    const makeLine = (text, n) => h('div', { class: cls(n), title: marks.err.get(n) || null }, plain ? [text || '​'] : colour(text));
    function paint() {
      const kids = over.children;
      for (let i = 0; i < kids.length; i++) {
        const c = cls(i + 1);
        if (kids[i].className !== c) kids[i].className = c;
        const t = marks.err.get(i + 1) || null;
        if (t) kids[i].title = t; else kids[i].removeAttribute('title');
      }
    }
    function setText(text) {
      lines = text.split('\n');
      plain = lines.length > BIG;
      const frag = document.createDocumentFragment();
      lines.forEach((l, i) => frag.append(makeLine(l, i + 1)));
      clear(over).append(frag);
      ta.value = text;
    }
    function sync() {                                              // after typing: only the lines that differ are drawn again
      const next = ta.value.split('\n');
      let s = 0;
      while (s < lines.length && s < next.length && lines[s] === next[s]) s++;
      let ea = lines.length, en = next.length;
      while (ea > s && en > s && lines[ea - 1] === next[en - 1]) { ea--; en--; }
      const kids = over.children;
      for (let i = ea - 1; i >= s; i--) kids[i].remove();
      const before = kids[s] || null;
      for (let i = s; i < en; i++) over.insertBefore(makeLine(next[i], i + 1), before);
      if (next.length !== lines.length) {                           // later lines moved: marks follow them
        const d = next.length - lines.length;
        const shift = (set) => new Set([...set].filter((n) => n <= s || n > ea).map((n) => (n > ea ? n + d : n)));
        marks.chg = shift(marks.chg); marks.flash = shift(marks.flash);
        marks.err = new Map([...marks.err].filter(([n]) => n <= s || n > ea).map(([n, v]) => [n > ea ? n + d : n, v]));
      }
      lines = next;
      plain = lines.length > BIG;
      paint();
    }
    const lineAt = () => ta.value.slice(0, ta.selectionStart).split('\n').length;
    const offsetOf = (n) => { let o = 0; for (let i = 0; i < n - 1 && i < lines.length; i++) o += lines[i].length + 1; return o; };
    function reveal(n, opts) {                                      // bring line n into the middle third of the view and put the caret there
      n = Math.max(1, Math.min(n, lines.length));
      const el = over.children[n - 1];
      if (el) box.scrollTop = Math.max(0, el.offsetTop - box.clientHeight / 3);
      if (!opts || opts.caret !== false) { try { ta.focus({ preventScroll: true }); ta.setSelectionRange(offsetOf(n), offsetOf(n)); } catch (e) { /* not focusable yet */ } }
      cur = n; paint();
    }
    ta.addEventListener('input', () => { sync(); on.change(ta.value); });
    ta.addEventListener('keyup', () => { const n = lineAt(); if (n !== cur) { cur = n; paint(); } });
    ta.addEventListener('click', (e) => { const n = lineAt(); if (n !== cur) { cur = n; paint(); } if (e.metaKey || e.ctrlKey) on.cmdClick(n); });
    ta.addEventListener('keydown', (e) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key === 's') { e.preventDefault(); on.save(); }
      else if (mod && e.key === 'Enter') { e.preventDefault(); on.compile(); }
    });
    return {
      box, ta, value: () => ta.value, setText, reveal, lineAt, lines: () => lines.slice(), focus: () => ta.focus({ preventScroll: true }),
      setMarks(m) { if (m.chg) marks.chg = m.chg; if (m.err) marks.err = m.err; if (m.flash) marks.flash = m.flash; paint(); },
      clearFlash() { if (marks.flash.size) { marks.flash = new Set(); paint(); } },
    };
  }

  // ------------------------------------------------------------------ the PDF, a picture per page, drawn when it comes into view
  function viewer(paper, on) {
    const box = h('div', { class: 'lx-pages', tabindex: '0', 'aria-label': 'Compiled PDF' });
    let info = null, zoom = 1, io = null, bucket = 0, pageNow = 1, raf = 0;
    const pgs = () => Array.from(box.querySelectorAll('.pg'));
    const fit = () => Math.max(220, box.clientWidth - 28);
    const wide = () => Math.round(fit() * zoom);
    const dpiFor = () => Math.max(48, Math.min(300, Math.round(wide() / (info.width / 72) * (window.devicePixelRatio || 1))));
    const url = (n) => `/latex/page?paper=${enc(paper)}&n=${n}&dpi=${dpiFor()}&id=${enc(info.id)}`;
    function load(pg) {
      if (!info || !pg.isConnected) return;
      const u = url(pg.dataset.n);
      if (pg.dataset.src === u) return;
      const pre = new Image();                                       // the old picture stays until the new one is ready: no flash on a new compile
      pre.onload = () => { if (!pg.isConnected || pg.dataset.want !== u) return; const img = pg.querySelector('img'); img.src = u; pg.dataset.src = u; pg.classList.add('ready'); };
      pre.onerror = () => { pg.classList.add('failed'); };
      pg.dataset.want = u;
      pre.src = u;
    }
    function size() {
      const w = wide(), hgt = Math.round(w * info.height / info.width);
      pgs().forEach((pg) => { pg.style.width = w + 'px'; pg.style.height = hgt + 'px'; });
    }
    function where() { const ps = pgs(); const top = box.scrollTop + box.clientHeight * 0.3; let n = 1; ps.forEach((pg, i) => { if (pg.offsetTop <= top) n = i + 1; }); return n; }
    function anchor() { const ps = pgs(); if (!ps.length) return null; const n = where(), pg = ps[n - 1]; return { n, f: pg.offsetHeight ? (box.scrollTop - pg.offsetTop) / pg.offsetHeight : 0 }; }
    function restore(a) { const pg = a && pgs()[a.n - 1]; if (pg) box.scrollTop = pg.offsetTop + a.f * pg.offsetHeight; }
    function note() { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => { const n = where(); if (n !== pageNow) { pageNow = n; on.page(n, info ? info.pages : 0); } }); }
    box.addEventListener('scroll', note, { passive: true });
    box.addEventListener('dblclick', (e) => {
      const pg = e.target.closest && e.target.closest('.pg');
      if (!pg || !info) return;
      const r = pg.getBoundingClientRect();
      on.inverse(Number(pg.dataset.n), (e.clientX - r.left) / r.width * info.width, (e.clientY - r.top) / r.height * info.height);
    });
    function observe() {
      if (io) io.disconnect();
      if (typeof IntersectionObserver !== 'function') { pgs().forEach(load); return; }
      io = new IntersectionObserver((es) => es.forEach((en) => { if (en.isIntersecting) load(en.target); }), { root: box, rootMargin: '700px 0px' });
      pgs().forEach((pg) => io.observe(pg));
    }
    const api_ = {
      box,
      get info() { return info; },
      get zoom() { return zoom; },
      shownWidth: () => wide(),
      set(next) {                                                      // a new compile (or none)
        if (!next || !next.pdf) { info = null; clear(box).append(h('p', { class: 'muted small lx-empty', text: 'The PDF appears here once the paper is compiled.' })); on.page(0, 0); return; }
        const a = info && info.id !== next.id ? anchor() : null;
        const same = info && info.id === next.id && info.pages === next.pages;
        info = next;
        if (!same) {
          if (!box.querySelector('.pg')) clear(box);
          const have = pgs();
          for (let n = have.length; n < info.pages; n++) box.append(h('div', { class: 'pg', 'data-n': String(n + 1) }, h('img', { alt: `Page ${n + 1}`, draggable: 'false' })));
          for (let n = have.length; n > info.pages; n--) have[n - 1].remove();
          size(); bucket = dpiFor(); observe(); restore(a); on.page(where(), info.pages);
        }
      },
      relayout() { if (!info) return; const a = anchor(); size(); const b = dpiFor(); if (b !== bucket) { bucket = b; observe(); } restore(a); },
      setZoom(z) { zoom = Math.max(0.5, Math.min(3, z)); api_.relayout(); },
      page(n) { const pg = pgs()[Math.max(1, Math.min(n, pgs().length)) - 1]; if (pg) box.scrollTo({ top: pg.offsetTop - 6, behavior: 'smooth' }); },
      hit(r) {                                                         // where a source line lands: a box around it that fades
        const pg = pgs()[r.page - 1];
        if (!pg || !info) return;
        box.querySelectorAll('.lx-hit').forEach((e) => e.remove());
        const el = h('div', { class: 'lx-hit' });
        el.style.left = (100 * (r.x - 2) / info.width) + '%'; el.style.top = (100 * (r.y - r.h - 2) / info.height) + '%';
        el.style.width = (100 * (r.w + 4) / info.width) + '%'; el.style.height = (100 * (r.h + 5) / info.height) + '%';
        el.addEventListener('animationend', () => el.remove());
        pg.append(el);
        box.scrollTo({ top: pg.offsetTop + pg.offsetHeight * (r.y - r.h) / info.height - box.clientHeight / 3, behavior: 'smooth' });
      },
      destroy() { if (io) io.disconnect(); cancelAnimationFrame(raf); },
    };
    return api_;
  }

  // ------------------------------------------------------------------ the pane
  function mount(host, paper) {
    const L = { paper, status: null, file: null, base: null, dirty: false, view: setting('view', 'both'), auto: setting('auto', '1') !== '0', compiling: false, queued: false,
                last: null, killed: false, saving: null, timer: null, ranges: [], errs: [] };
    const el = {};
    const clock = (fn, ms) => { clearTimeout(L.timer); L.timer = setTimeout(fn, ms); };

    // -- the pieces
    const safe = (fn) => (...a) => { try { const r = fn(...a); if (r && r.catch) r.catch((e) => toast(e.message)); } catch (e) { toast(e.message); } };
    const ed = editor({ change: () => changed(), save: safe(() => saveNow(true)), compile: safe(() => saveNow(true)), cmdClick: (n) => showInPdf(n) });
    const pdf = viewer(paper, { page: (n, total) => pageLabel(n, total), inverse: (page, x, y) => fromPdf(page, x, y) });
    el.file = h('select', { class: 'lx-file', 'aria-label': 'File' });
    el.file.addEventListener('change', () => openFile(el.file.value).catch((e) => toast(e.message)));
    el.compile = h('button', { type: 'button', class: 'small primary', text: 'Recompile', title: 'Save and make the PDF again (Cmd or Ctrl + Enter)', onclick: safe(() => saveNow(true)) });
    el.auto = h('button', { type: 'button', class: 'small', 'aria-pressed': String(L.auto), title: 'Save and recompile by themselves a moment after you stop typing, and whenever the assistant changes the text',
      onclick: () => { L.auto = !L.auto; store.set(KEY + 'auto', L.auto ? '1' : '0'); el.auto.setAttribute('aria-pressed', String(L.auto)); if (L.auto && L.dirty) changed(); } }, 'Auto');
    el.state = h('span', { class: 'small lx-state', role: 'status', 'aria-live': 'polite' });
    el.views = h('div', { class: 'row tight lx-views', role: 'group', 'aria-label': 'What to show' }, [['source', 'Source'], ['both', 'Both'], ['pdf', 'PDF']].map(([k, label]) =>
      h('button', { type: 'button', class: 'small', 'data-view': k, 'aria-pressed': String(L.view === k), text: label, onclick: () => setView(k) })));
    el.notice = h('div', { class: 'stack lx-notice' });
    el.problems = h('div', { class: 'lx-problems', hidden: true });
    el.pageLabel = h('span', { class: 'small muted lx-pagelabel' });
    el.zoom = h('span', { class: 'small lx-zoom' });
    const zbtn = (t, title, fn) => h('button', { type: 'button', class: 'small tool', text: t, title, 'aria-label': title, onclick: fn });
    el.pdfbar = h('div', { class: 'row tight lx-pdfbar' }, zbtn('−', 'Zoom out', () => zoomBy(-0.15)), el.zoom, zbtn('+', 'Zoom in', () => zoomBy(0.15)), zbtn('Fit', 'Fit the page to the width', () => zoomTo(1)),
      h('span', { class: 'grow' }), zbtn('‹', 'Previous page', () => pdf.page((pageNow() - 1))), el.pageLabel, zbtn('›', 'Next page', () => pdf.page(pageNow() + 1)),
      h('a', { class: 'small button-like', href: `/latex/pdf?paper=${enc(paper)}`, download: '', title: 'Keep a copy of the PDF', text: 'Download' }));
    el.show = h('button', { type: 'button', class: 'small', text: 'Show in PDF', title: 'Find the line you are on in the PDF (Cmd or Ctrl + click a line does the same). Double-click the PDF to come back to the source.', onclick: () => showInPdf(ed.lineAt()) });
    const srcBar = h('div', { class: 'row tight lx-srcbar' }, el.show, h('span', { class: 'small muted grow lx-hint' }));
    el.hint = srcBar.querySelector('.lx-hint');
    el.src = h('div', { class: 'lx-src' }, el.notice, srcBar, ed.box, el.problems);
    el.pdf = h('div', { class: 'lx-pdf' }, el.pdfbar, pdf.box);
    el.body = h('div', { class: 'lx-body', 'data-view': L.view }, el.src, el.pdf);
    const bar = h('div', { class: 'row tight lx-bar' }, h('h2', { text: 'LaTeX' }), el.file, el.compile, el.auto, el.state, h('span', { class: 'grow' }), el.views);
    host.append(bar, el.body);

    const pageNow = () => Number((el.pageLabel.dataset.n) || 1);
    function pageLabel(n, total) { el.pageLabel.dataset.n = String(n || 1); el.pageLabel.textContent = total ? `${n} / ${total}` : ''; }
    function zoomLabel() { el.zoom.textContent = pdf.info ? Math.round(100 * pdf.shownWidth() / (pdf.info.width * 96 / 72)) + '%' : ''; el.zoom.title = 'How large the page is drawn compared with its printed size'; }
    function zoomTo(z) { pdf.setZoom(z); zoomLabel(); }
    function zoomBy(d) { zoomTo(Math.round((pdf.zoom + d) * 100) / 100); }
    function setView(v) {
      L.view = v; store.set(KEY + 'view', v);
      el.body.dataset.view = v;
      el.views.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.view === v)));
      requestAnimationFrame(() => pdf.relayout());
    }
    // the pane is a column when it is wide and stacks when it is narrow; the pictures are drawn again at the size they are shown
    let ro = null, lastW = 0;
    if (typeof ResizeObserver === 'function') {
      ro = new ResizeObserver(() => {
        el.body.dataset.narrow = host.clientWidth < 760 ? '1' : '0';
        const w = pdf.box.clientWidth;
        if (Math.abs(w - lastW) > 8) { lastW = w; clearTimeout(L.rz); L.rz = setTimeout(() => { pdf.relayout(); zoomLabel(); }, 180); }
      });
      ro.observe(host);
    }

    // -- the state line
    const ago = (t) => { const s = Math.max(0, Date.now() / 1000 - t); return s < 90 ? 'just now' : s < 5400 ? Math.round(s / 60) + ' min ago' : Math.round(s / 3600) + ' h ago'; };
    function paintState() {
      const st = L.status, last = L.last, s = el.state;
      s.className = 'small lx-state';
      clear(s);
      if (L.compiling) { s.append(h('span', { class: 'spinner', 'aria-hidden': 'true' }), ' Compiling...'); return; }
      if (st && !st.available) { s.classList.add('warn'); s.textContent = 'No LaTeX on this computer: the source can be read and edited, not compiled'; return; }
      if (L.saving) { s.textContent = 'Saving...'; return; }
      if (L.dirty) { s.textContent = L.auto ? 'Unsaved changes: saving in a moment' : 'Unsaved changes (Cmd/Ctrl + S saves)'; return; }
      if (!last) { s.textContent = 'Not compiled yet'; return; }
      const errs = (last.errors || []).length, warn = (last.warnings || []).length + (last.undefined ? last.undefined.citations.length + last.undefined.references.length : 0);
      if (errs) { s.classList.add('bad'); s.textContent = `${errs === 1 ? '1 error' : errs + ' errors'}${last.pdf ? ' · the PDF may be out of date or incomplete' : ''}`; return; }
      const stale = st && last.started && (st.files || []).some((f) => f.mtime > last.started + 1);
      s.textContent = `Compiled ${ago(last.at)} · ${last.pages} page${last.pages === 1 ? '' : 's'} · ${last.seconds} s${warn ? ` · ${warn} warning${warn === 1 ? '' : 's'}` : ''}${stale ? ' · the source changed since' : ''}`;
      if (stale) s.classList.add('warn');
    }
    function paintFiles() {
      const st = L.status;
      if (!st) return;
      clear(el.file);
      (st.files || []).forEach((f) => el.file.append(h('option', { value: f.file, text: `${st.changed[f.file] ? '● ' : ''}${f.file}${f.main ? ' (main)' : ''}` })));
      if (L.file && !(st.files || []).some((f) => f.file === L.file)) el.file.append(h('option', { value: L.file, text: L.file + ' (not found)' }));
      el.file.value = L.file || '';
      el.file.title = 'A dot marks a file with changes that are not in Overleaf yet';
    }

    // -- problems found by the last compile
    function paintProblems() {
      const last = L.last, p = el.problems;
      clear(p);
      const errs = (last && last.errors) || [], und = (last && last.undefined) || { citations: [], references: [] }, miss = (last && last.missing) || [];
      const warns = (last && last.warnings) || [];
      const here = new Map();
      errs.filter((e) => e.file === L.file && e.line).forEach((e) => { if (!here.has(e.line)) here.set(e.line, e.message); });
      L.errs = here;
      ed.setMarks({ err: here });
      if (!errs.length && !und.citations.length && !und.references.length && !miss.length && !warns.length) { p.hidden = true; return; }
      p.hidden = false;
      const open = errs.length > 0 || window.OLS.store.get(KEY + 'problems') === '1';
      const rows = h('ul', { class: 'lx-rows' });
      errs.forEach((e) => rows.append(h('li', { class: 'bad' }, h('button', { type: 'button', class: 'link', title: 'Go to this line', onclick: () => goTo(e.file, e.line), text: `${e.file || 'LaTeX'}${e.line ? ':' + e.line : ''}` }),
        h('span', { text: ' ' + e.message }), e.context ? h('pre', { class: 'small', text: e.context }) : null)));
      miss.forEach((m) => rows.append(h('li', { class: 'bad' }, h('span', { text: `LaTeX could not find ${m}. Is the package or file in the paper, or in the TeX installation?` }))));
      if (und.citations.length) rows.append(h('li', { class: 'warn' }, h('span', { text: 'Citations without an entry in the bibliography: ' + und.citations.join(', ') })));
      if (und.references.length) rows.append(h('li', { class: 'warn' }, h('span', { text: 'References to labels that do not exist: ' + und.references.join(', ') })));
      warns.filter((w) => !/undefined/.test(w)).slice(0, 8).forEach((w) => rows.append(h('li', { class: 'warn' }, h('span', { text: w }))));
      const head = errs.length ? `${errs.length === 1 ? '1 error' : errs.length + ' errors'}` : 'Notes from the last compile';
      p.append(h('details', { open }, h('summary', { text: head + (und.citations.length + und.references.length ? ` · ${und.citations.length + und.references.length} undefined` : '') }), rows,
        last && last.log_tail ? h('details', { class: 'small' }, h('summary', { text: 'End of the compiler\'s log' }), h('pre', { class: 'small', text: last.log_tail })) : null));
      p.querySelector('details').addEventListener('toggle', (ev) => { if (!errs.length) store.set(KEY + 'problems', ev.target.open ? '1' : '0'); });
    }

    // -- talking to the server
    async function loadStatus() {
      const s = await api.get('/api/latex/status?paper=' + enc(paper));
      if (L.killed) return s;
      L.status = s;
      if (s.last) L.last = s.last;
      el.notice.querySelectorAll('.lx-status-notice').forEach((n) => n.remove());
      if (!s.available) el.notice.append(h('div', { class: 'lx-status-notice' }, notice('warn', s.why)));
      if (s.no_main) el.notice.append(h('div', { class: 'lx-status-notice' }, notice('warn', s.no_main)));
      el.compile.disabled = !s.available;
      el.pdfbar.hidden = !s.available;
      paintFiles(); paintState();
      return s;
    }

    async function openFile(rel, opts) {
      if (L.dirty) { const ok = await save(); if (ok === false) { el.file.value = L.file; return; } }          // typing is saved first, never dropped
      const f = await api.get(`/api/latex/file?paper=${enc(paper)}&file=${enc(rel)}`);
      if (L.killed) return;
      L.file = f.file; L.base = f.hash; L.dirty = false; L.ranges = f.ranges;
      store.set(KEY + 'file-' + paper, L.file);
      ed.setText(f.text);
      ed.setMarks({ chg: inRanges(f.ranges), flash: new Set(), err: new Map() });
      paintFiles(); paintState(); paintProblems(); stale(null);
      el.hint.textContent = f.text.length > 400000 ? 'A large file: shown without colours.' : '';
      if (opts && opts.line) ed.reveal(opts.line, { caret: opts.caret });
      if (opts && opts.flash && opts.flash.size) flash(opts.flash);
    }
    function flash(lines) {
      ed.setMarks({ flash: lines });
      const first = Math.min(...lines);
      if (Number.isFinite(first)) ed.reveal(first, { caret: false });
      clearTimeout(L.fl);
      L.fl = setTimeout(() => ed.clearFlash(), 4500);
    }
    function goTo(file, line) {
      if (!file) return;
      const go = () => ed.reveal(line || 1);
      if (file === L.file) go(); else openFile(file, { line, caret: true }).catch((e) => toast(e.message));
    }

    function changed() {                                             // the author typed
      L.dirty = true;
      paintState();
      ed.clearFlash();
      clearTimeout(L.timer);
      if (L.auto) L.timer = setTimeout(() => saveNow(true).catch((e) => toast(e.message)), 1400);
    }
    function stale(on) {                                             // the file changed on disk while there were unsaved edits
      el.notice.querySelectorAll('.lx-stale').forEach((n) => n.remove());
      if (!on) return;
      el.notice.append(h('div', { class: 'notice warn lx-stale' }, h('strong', { text: `${L.file} changed on disk while you were editing it. ` }),
        'The assistant saved a suggestion, or the latest from Overleaf arrived. ', h('div', { class: 'row tight' },
          h('button', { type: 'button', class: 'small', text: 'Load the new version (drops my edits)', onclick: () => { L.dirty = false; openFile(L.file).catch((e) => toast(e.message)); } }),
          h('button', { type: 'button', class: 'small', text: 'Keep mine (replaces the new version)', onclick: () => keepMine().catch((e) => toast(e.message)) }))));
    }
    async function keepMine() {
      const f = await api.get(`/api/latex/file?paper=${enc(paper)}&file=${enc(L.file)}`);
      L.base = f.hash;
      stale(null);
      await saveNow(true);
    }

    async function save() {
      if (!L.dirty || L.saving) return L.saving || true;
      const text = ed.value(), file = L.file;
      L.saving = (async () => {
        paintState();
        try {
          const r = await api.post('/api/latex/save', { paper, file, text, base: L.base });
          L.base = r.hash; L.ranges = r.ranges;
          if (ed.value() === text) L.dirty = false;                  // typing went on while saving: stay unsaved
          ed.setMarks({ chg: inRanges(r.ranges) });
          stale(null);
          return true;
        } catch (e) {
          if (e.data && e.data.code === 'stale') { stale(true); return false; }
          throw e;
        } finally { L.saving = null; paintState(); }
      })();
      return L.saving;
    }
    async function saveNow(andCompile) {
      clearTimeout(L.timer);
      const ok = await save();
      if (ok === false) return;
      loadStatus().catch(() => {});
      if (andCompile && L.status && L.status.available) await compile();
    }

    async function compile() {
      if (L.compiling) { L.queued = true; return; }
      L.compiling = true; paintState();
      try {
        const r = await api.post('/api/latex/compile', { paper });
        const res = await waitJob(r.job);
        L.last = res;
        pdf.set(res.pdf ? res : null);
        zoomLabel();
        loadStatus().catch(() => {});
      } catch (e) {
        if (e.data && e.data.code === 'unavailable') loadStatus().catch(() => {}); else toast(e.message);
      } finally {
        L.compiling = false; paintState(); paintProblems();
        if (L.queued) { L.queued = false; compile(); }
      }
    }
    async function showInPdf(line) {
      if (!pdf.info) { toast('Compile first, then the line can be found in the PDF.'); return; }
      try { const r = await api.post('/api/latex/forward', { paper, file: L.file, line }); pdf.hit(r); }
      catch (e) { toast(e.message); }
    }
    async function fromPdf(page, x, y) {
      try { const r = await api.post('/api/latex/inverse', { paper, page, x, y }); goTo(r.file, r.line); }
      catch (e) { toast(e.message); }
    }

    // -- the assistant (or a pull) changed files: show what changed, then make the PDF again
    async function refresh() {
      if (L.killed || !L.file) return;
      const before = L.status ? L.status.changed : {};
      const oldLines = ed.lines();
      const s = await loadStatus();
      const after = s.changed || {};
      const gains = (f) => { const was = inRanges(before[f]); return [...inRanges(after[f])].filter((n) => !was.has(n)); };
      const touched = Object.keys(after).filter((f) => gains(f).length);                // files that gained changed lines (a send only removes marks)
      const target = touched.includes(L.file) ? L.file : (touched[0] || L.file);
      if (!L.dirty) ed.setMarks({ chg: inRanges(after[L.file]) });
      if (target === L.file) {
        const f = await api.get(`/api/latex/file?paper=${enc(paper)}&file=${enc(L.file)}`);
        if (f.hash === L.base) { if (touched.length === 0 && !needsCompile()) return; }
        else if (L.dirty) { stale(true); return; }
        else {
          const text = f.text, lines = text.split('\n');
          const diff = changedLines(oldLines, lines);
          L.base = f.hash; L.ranges = f.ranges;
          const keep = ed.box.scrollTop;
          ed.setText(text);
          ed.setMarks({ chg: inRanges(f.ranges), err: new Map() });
          ed.box.scrollTop = keep;
          if (diff.size) flash(diff);
          paintProblems();
        }
      } else {
        await openFile(target, { flash: new Set(gains(target)) });
        toast(`The change is in ${target}.`);
      }
      if (L.auto && s.available) compile();
    }
    const needsCompile = () => !!(L.status && L.status.available && (!L.last || (L.status.files || []).some((f) => L.last.started && f.mtime > L.last.started + 1)));

    // -- start
    (async () => {
      try {
        const s = await loadStatus();
        const wanted = setting('file-' + paper, null);
        const first = (s.files || []).find((f) => f.file === wanted) ? wanted : (s.main || (s.files[0] && s.files[0].file));
        if (first) await openFile(first); else el.notice.append(notice('warn', 'This paper has no .tex file to show.'));
        if (s.last) pdf.set(s.last.pdf ? s.last : null); else pdf.set(null);
        zoomLabel();
        el.body.dataset.narrow = host.clientWidth < 760 ? '1' : '0';
        paintProblems();
        if (L.auto && needsCompile()) compile();
      } catch (e) { clear(el.notice).append(notice('bad', e.message)); }
    })();

    return {
      refresh: () => refresh().catch((e) => toast(e.message)),
      destroy() {
        if (L.dirty) save().then((ok) => { if (ok) toast(`Saved your edits to ${L.file}.`); }).catch(() => {});     // switching away never loses typing
        L.killed = true; clearTimeout(L.timer); clearTimeout(L.fl); clearTimeout(L.rz);
        if (ro) ro.disconnect();
        pdf.destroy();
      },
      get dirty() { return L.dirty; },
      flushSave: () => (L.dirty ? save() : Promise.resolve(true)),
    };
  }

  window.OLS.latex = { mount };
}());
