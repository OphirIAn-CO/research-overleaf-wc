/* Settings: keys and tokens in one place, preferences, papers (and adding one from a shared folder), and the way into the advanced pages.
   Secrets are written to private files by the server and never shown again. */
(function () {
  'use strict';
  const { h, clear, api, notice, busy, guarded, registerPage, toast, waitJob, S } = window.OLS;
  const enc = encodeURIComponent;

  const badge = (ok, yes, no) => h('span', { class: 'badge ' + (ok ? 'ok' : 'warn'), text: ok ? yes : no });
  function result(box, r) { clear(box).append(notice(r.ok ? 'ok' : 'bad', r.message)); }

  const slug = (name) => (name.replace(/\.pdf$/i, '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 30) || 'handbook');

  async function bookCard(st) {
    const card = h('section', { class: 'card', 'aria-label': 'Writing book' });
    const book = (st.books || [])[0];
    const out = h('div', { class: 'stack' });
    const prepare = (pdf, id, mode, label) => guarded(h('button', { type: 'button', class: 'primary', text: label }), async () => {
      clear(out).append(busy('Reading the book. A long one takes about a minute...'));
      try {
        const r = await api.post('/api/book/ingest', { pdf, id, mode });
        const res = await waitJob(r.job);
        toast(res.status === 'skipped' ? 'It was already prepared' : 'The book is ready');
        await window.OLS.loadState(); window.OLS.route();
      } catch (e) { clear(out).append(notice('bad', e.message)); throw e; }
    });
    if (!book) {
      card.append(h('p', { class: 'small secondary', text: 'The book is read once and cut into pieces of advice; each request then uses only the few pieces that apply. You never have to look at them.' }));
      if (!st.inbox.length) {
        card.append(notice('warn', 'No book yet. Put the PDF in this folder on your computer: ', h('code', { text: '~/.config/overleaf-skill/inbox/' }), ' (drag it in with Finder), then come back here. The PDF must have selectable text.'));
      } else {
        const sel = h('select', { 'aria-label': 'Book PDF' }, st.inbox.map((f) => h('option', { value: f, text: f })));
        card.append(h('div', { class: 'row' }, sel, guarded(h('button', { type: 'button', class: 'primary', text: 'Prepare the book' }), async () => {
          clear(out).append(busy('Reading the book. A long one takes about a minute...'));
          try {
            const r = await api.post('/api/book/ingest', { pdf: sel.value, id: slug(sel.value), mode: 'build' });
            await waitJob(r.job); toast('The book is ready'); await window.OLS.loadState(); window.OLS.route();
          } catch (e) { clear(out).append(notice('bad', e.message)); throw e; }
        })));
      }
      card.append(out);
      return card;
    }
    card.append(h('p', null, badge(true, 'ready', ''), ` ${book.source || book.id}: ${book.chunks} pieces of advice from ${book.pages} pages.`));
    if (book.exercises) card.append(h('p', { class: 'small muted', text: `${book.exercises} pages of discussion questions were set aside; they are not advice.` }));
    if (book.outdated) {
      const here = st.inbox.includes(book.source);
      card.append(notice('warn', h('strong', { text: 'The way advice is matched has improved. ' }), 'Preparing the book again takes about a minute and keeps any advice summaries already made. ',
        here ? prepare(book.source, book.id, 'rebuild', 'Prepare again') : h('span', { class: 'small', text: `Put ${book.source} back in the inbox folder first.` })));
    }
    // advice summaries: one-time, costs a little, makes every later request cheaper and sharper
    const rules = await api.get(`/api/book/${enc(book.id)}/rules`);
    const rulesBox = h('div');
    if (rules.todo === 0) {
      rulesBox.append(h('p', { class: 'small secondary', text: `Short advice summaries are ready for all ${rules.chunks} pieces. Requests carry these instead of raw book text.` }));
    } else {
      rulesBox.append(h('p', { class: 'small secondary', text: `${rules.with_cards} of ${rules.chunks} pieces have a short advice summary. Making them is a one-time step that costs a little; afterwards every request is cheaper, sends less of the book out, and finds the right advice more easily.` }),
        guarded(h('button', { type: 'button', text: 'See the cost' }), async () => {
          const pl = await api.post(`/api/book/${enc(book.id)}/rules/preview`, {});
          const bar = h('progress', { max: '1', value: '0', class: 'grow', hidden: true });
          const go = guarded(h('button', { type: 'button', class: 'primary', text: 'Make the summaries once' }), async () => {
            bar.hidden = false;
            const r = await api.post(`/api/book/${enc(book.id)}/rules/run`, { confirm: pl.payload_hash });
            try { const res = await waitJob(r.job, (j) => { if (j.progress) bar.value = j.progress[0] / j.progress[1]; }); toast(res.status === 'done' ? 'Summaries ready' : 'Stopped: ' + (res.error || res.status)); } catch (e) { toast(e.message); }
            window.OLS.route();
          });
          clear(out).append(notice('warn', h('strong', { text: 'This sends the book text to OpenAI once. ' }), `${pl.chunks} pieces in ${pl.calls} requests to ${pl.model}, about ${Math.round(pl.est_tokens_in / 1000)}k tokens in and ${Math.round(pl.est_tokens_out / 1000)}k out${pl.est_cost != null ? ', roughly ' + pl.est_cost + ' in your price units' : ''}. It can resume if interrupted.`), h('div', { class: 'row' }, go, bar));
        }));
    }
    card.append(rulesBox, out, h('p', { class: 'small' }, h('a', { href: '#/book', text: 'Open the book library (advanced)' })));
    return card;
  }

  registerPage('settings', 'Settings', async (box) => {
    await window.OLS.loadState();
    const c = await api.get('/api/connections' + (S.paper ? '?paper=' + enc(S.paper) : ''));
    const st = S.st;
    const paper = S.paper;
    box.append(h('div', { class: 'page-head' }, h('h1', { text: 'Settings' }), h('span', { class: 'muted small', text: 'Keys and tokens are saved in private files on this computer. They are never shown again, only replaced.' })));

    // ---- connections
    const oaModel = h('input', { type: 'text', value: c.openai.model || '', placeholder: 'the model name from your OpenAI account', 'aria-label': 'Model', class: 'grow' });
    const oaKey = h('input', { type: 'password', autocomplete: 'off', placeholder: c.openai.state === 'ok' ? 'a key is saved (type a new one to replace it)' : 'sk-...', 'aria-label': 'OpenAI API key', class: 'grow' });
    const oaOut = h('div');
    const openai = h('section', { class: 'card', 'aria-label': 'OpenAI' }, h('div', { class: 'row' }, h('h2', { text: 'OpenAI' }), badge(c.openai.state === 'ok', 'ready', 'not set up')),
      h('p', { class: 'small secondary', text: 'This is the model that rewrites your text. Create a key in your OpenAI account (platform.openai.com, API keys) and paste it here once.' }),
      h('label', { class: 'field' }, h('span', { text: 'Model' }), oaModel), h('label', { class: 'field' }, h('span', { text: 'API key' }), oaKey),
      h('div', { class: 'row' }, guarded(h('button', { type: 'button', class: 'primary', text: 'Save' }), async () => {
        await api.post('/api/secrets/openai', { api_key: oaKey.value, model: oaModel.value });
        oaKey.value = ''; toast('Saved'); window.OLS.route();
      }), guarded(h('button', { type: 'button', text: 'Test' }), async () => { clear(oaOut).append(busy('Asking OpenAI...')); result(oaOut, await api.post('/api/test/openai', {})); })), oaOut);

    const glKey = h('input', { type: 'password', autocomplete: 'off', placeholder: c.overleaf.token ? 'a token is saved (type a new one to replace it)' : 'paste your Overleaf Git token', 'aria-label': 'Overleaf Git token', class: 'grow' });
    const glOut = h('div');
    const overleaf = h('section', { class: 'card', 'aria-label': 'Overleaf' }, h('div', { class: 'row' }, h('h2', { text: 'Overleaf' }), badge(c.overleaf.token, 'token saved', 'no token yet')),
      h('p', { class: 'small secondary', text: 'Lets this app get the latest from Overleaf and send your changes. In Overleaf: Account Settings, then Git Integration, then create a token.' }),
      h('label', { class: 'field' }, h('span', { text: 'Overleaf Git token' }), glKey),
      h('div', { class: 'row' }, guarded(h('button', { type: 'button', class: 'primary', text: 'Save' }), async () => { await api.post('/api/secrets/git', { token: glKey.value }); glKey.value = ''; toast('Saved'); window.OLS.route(); }),
        paper ? guarded(h('button', { type: 'button', text: 'Test with ' + paper }), async () => { clear(glOut).append(busy('Asking Overleaf...')); result(glOut, await api.post('/api/test/git', { paper })); }) : null), glOut);

    const wm = h('input', { type: 'text', value: c.cleaner.url || '', placeholder: 'http://host.docker.internal:PORT', 'aria-label': 'Text-cleaning service address', class: 'grow' });
    const wmOut = h('div');
    const cleaner = h('section', { class: 'card', 'aria-label': 'Text cleaning' }, h('div', { class: 'row' }, h('h2', { text: 'Text-cleaning service' }), badge(c.cleaner.state === 'ok', 'working', c.cleaner.url ? 'not answering' : 'not set up')),
      h('p', { class: 'small secondary', text: 'Your watermark-removal step that cleans text before it is saved. With its address here, "Approve and save" works from this app. Without it, "Let Claude apply it" is used instead.' }),
      h('label', { class: 'field' }, h('span', { text: 'Address' }), wm),
      h('div', { class: 'row' }, guarded(h('button', { type: 'button', class: 'primary', text: 'Save' }), async () => { await api.post('/api/settings', { ui: { watermark_url: wm.value.trim() } }); toast('Saved'); window.OLS.route(); }),
        guarded(h('button', { type: 'button', text: 'Test' }), async () => { clear(wmOut).append(busy('Asking...')); result(wmOut, await api.post('/api/test/cleaner', {})); })), wmOut);

    const cmd = (text) => h('div', { class: 'row' }, h('code', { class: 'small', text }), h('button', { type: 'button', class: 'small', text: 'Copy', onclick: async () => { try { await navigator.clipboard.writeText(text); toast('Copied'); } catch (e) { toast('Select it and copy by hand'); } } }));
    const mend = h('section', { class: 'card', 'aria-label': 'Mendeley' }, h('div', { class: 'row' }, h('h2', { text: 'Mendeley' }),
      badge(c.mendeley.state === 'connected', 'connected', c.mendeley.state === 'needs_sign_in' ? 'needs sign-in' : 'not set up')),
      h('p', { class: 'small secondary', text: c.mendeley.state === 'connected' ? 'References you add are also added to the paper\'s Mendeley group.' :
        'Mendeley asks you to sign in in a browser, which cannot happen inside this app. Run this once in a terminal on your computer:' }),
      c.mendeley.state === 'connected' ? null : cmd(c.mendeley.command));
    const tg = h('section', { class: 'card', 'aria-label': 'Telegram' }, h('div', { class: 'row' }, h('h2', { text: 'Telegram (phone)' }), badge(c.telegram.state === 'set_up', 'set up', 'not set up')),
      h('p', { class: 'small secondary', text: c.telegram.state === 'set_up' ? `Send a DOI to your bot from your phone and it is added to the active paper (${c.telegram.allowed} chat allowed).` :
        'Optional. A Telegram bot lets you add a DOI from your phone. See the README for the one-time setup.' }));
    // ---- QuillBot and Overleaf beside the chat: their addresses, and inside the app or in a window
    const pn = c.panes;
    const qUrl = h('input', { type: 'text', value: pn.quillbot_url === pn.quillbot_default ? '' : pn.quillbot_url, placeholder: pn.quillbot_default, 'aria-label': 'QuillBot address', class: 'grow' });
    const oUrl = h('input', { type: 'text', value: pn.overleaf_override || '', placeholder: pn.overleaf_url || 'https://www.overleaf.com/project/<project id>', 'aria-label': 'Overleaf project address', class: 'grow' });
    const modeSel = h('select', { 'aria-label': 'Where to show them' },
      [['auto', 'Automatic: inside the app when the site allows it, otherwise in a window'], ['window', 'Always in a window beside the app (works everywhere)'], ['inside', 'Always try inside the app (may stay blank)']].map(([v, t]) => h('option', { value: v, text: t, selected: pn.mode === v })));
    const paneOut = h('div', { class: 'stack' });
    const sitesCard = h('section', { class: 'card', 'aria-label': 'Side panes' }, h('div', { class: 'row' }, h('h2', { text: 'QuillBot and Overleaf beside the chat' })),
      h('p', { class: 'small secondary', text: 'You do not enter your QuillBot or Overleaf password here. You sign in once, in the window; the app never sees it. Here you only say where the pages are and how to show them.' }),
      h('label', { class: 'field' }, h('span', { text: 'QuillBot address (leave empty for the usual one)' }), qUrl),
      S.paper ? h('label', { class: 'field' }, h('span', { text: `Overleaf project address for ${S.paper}` + (pn.overleaf_derived ? ' (taken from the paper\'s Git address; type another to override)' : '') }), oUrl) : null,
      h('label', { class: 'field' }, h('span', { text: 'Where to show them' }), modeSel),
      h('div', { class: 'row' }, guarded(h('button', { type: 'button', class: 'primary', text: 'Save' }), async () => {
        await api.post('/api/settings', Object.assign({ ui: { quillbot_url: qUrl.value.trim(), pane_mode: modeSel.value } }, S.paper ? { paper: S.paper, overleaf_url: oUrl.value.trim() } : {}));
        toast('Saved'); window.OLS.route();
      }), S.paper ? guarded(h('button', { type: 'button', text: 'Ask the sites if they allow it' }), async () => {
        clear(paneOut).append(busy('Asking QuillBot and Overleaf...'));
        const r = await api.post('/api/test/frames', { paper: S.paper });
        const line = (name, x) => {
          if (x.error) return notice('warn', h('strong', { text: name + ': ' }), 'the app could not ask (' + (/no project id/.test(x.error) ? 'there is no project address yet; type one above' : 'it could not reach the site from here') + '). It will open in a window beside the app, which always works.');
          return x.embeddable === true ? notice('ok', h('strong', { text: name + ': ' }), 'allows being shown inside the app.') :
            notice('warn', h('strong', { text: name + ': ' }), 'does not allow being shown inside another page (' + x.why + '). It will open in a window beside the app, which always works.');
        };
        clear(paneOut).append(line('QuillBot', r.quillbot), line('Overleaf', r.overleaf));
      }) : null,
      h('a', { class: 'small', href: '#/panes', text: 'Try them in your browser' })), paneOut);
    box.append(h('h2', { class: 'section-h', text: 'Connections' }), h('div', { class: 'grid two' }, openai, overleaf, cleaner, mend, tg, sitesCard));

    // ---- preferences
    const p = paper && window.OLS.paperObj(paper);
    const confirmBox = h('input', { type: 'checkbox', checked: !!(p && p.settings.confirm_before_send) });
    confirmBox.addEventListener('change', async () => { try { await api.post('/api/settings', { paper, confirm_before_send: confirmBox.checked }); await window.OLS.loadState(); toast(confirmBox.checked ? 'I will ask before sending' : 'I will send as soon as you ask'); } catch (e) { toast(e.message); confirmBox.checked = !confirmBox.checked; } });
    const guard = h('input', { type: 'number', min: '1', max: '100', value: String(c.limits.guard_passages), 'aria-label': 'Ask first above this many passages' });
    const maxp = h('input', { type: 'number', min: '1', max: '100', value: String(c.limits.max_passages), 'aria-label': 'Most passages in one request' });
    const idle = h('input', { type: 'number', min: '5', max: '1440', value: String(c.limits.idle_minutes), 'aria-label': 'Minutes until the app closes' });
    const texture = h('input', { type: 'checkbox', checked: document.documentElement.dataset.texture === 'on' });
    texture.addEventListener('change', () => { document.documentElement.dataset.texture = texture.checked ? 'on' : 'off'; window.OLS.store.set('ols-texture', texture.checked ? 'on' : 'off'); });
    box.append(h('h2', { class: 'section-h', text: 'Preferences' }), h('section', { class: 'card', 'aria-label': 'Preferences' }, h('div', { class: 'stack' },
      paper ? h('label', { class: 'check' }, confirmBox, `Ask me before anything is sent to OpenAI (${paper})`) : null,
      h('div', { class: 'row' }, h('label', { class: 'row tight' }, 'Ask me first when a request has more than', guard, 'passages'), h('label', { class: 'row tight' }, 'and never more than', maxp, 'in one request')),
      h('label', { class: 'row tight' }, 'Close this app after', idle, 'idle minutes'),
      h('label', { class: 'check' }, texture, 'Pattern fills on charts (for colour-blind readers and printing)'),
      h('div', { class: 'row' }, guarded(h('button', { type: 'button', class: 'primary', text: 'Save' }), async () => {
        await api.post('/api/settings', { ui: { guard_passages: Number(guard.value), max_passages: Number(maxp.value), idle_minutes: Number(idle.value) } }); toast('Saved'); window.OLS.route();
      })))));

    // ---- papers
    const found = h('div', { class: 'stack' });
    const rows = st.papers.map((x) => h('tr', null, h('td', null, h('strong', { text: x.name }), x.name === S.paper ? h('span', { class: 'badge ok', text: 'open' }) : null),
      h('td', { class: 'small mono', text: x.path }), h('td', { class: 'small', text: x.group || 'not linked' }), h('td', null, x.problem ? h('span', { class: 'badge bad', title: x.problem, text: 'problem' }) : h('span', { class: 'badge ok', text: 'healthy' })),
      h('td', null, x.name === S.paper ? null : h('button', { type: 'button', class: 'small', text: 'Open', onclick: () => window.OLS.setPaper(x.name).catch((e) => toast(e.message)) }))));
    box.append(h('h2', { class: 'section-h', text: 'Papers' }), h('section', { class: 'card', 'aria-label': 'Papers' },
      st.papers.length ? h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, ['Paper', 'Folder', 'Mendeley group', 'State', ''].map((t) => h('th', { text: t })))), h('tbody', null, rows))) : h('p', { class: 'muted', text: 'No paper yet.' }),
      h('h3', { text: 'Add a paper' }), found));
    try {
      const d = await api.get('/api/papers/discover');
      if (!d.roots.length) {
        found.append(h('p', { class: 'small secondary', text: 'To add papers from here, tell the app which folder holds your Overleaf clones (it can only see folders you share). Run once in a terminal, then restart the app:' }),
          cmd(`./ui.sh folders add ~/path/to/your/overleaf/folder`), h('p', { class: 'small muted', text: 'Or register one directly: python3 scripts/common.py add /path/to/clone --group "Mendeley group name". ' }));
      } else {
        const list = d.found.filter((x) => !x.registered);
        found.append(h('p', { class: 'small secondary', text: `Looking in: ${d.roots.join(', ')}` }));
        if (!list.length) found.append(h('p', { class: 'muted small', text: d.found.length ? 'Every Overleaf clone in those folders is already added.' : 'No Overleaf clone found in those folders (a clone is a folder whose Git address is git.overleaf.com).' }));
        list.forEach((x) => found.append(h('div', { class: 'row' }, h('span', { class: 'grow' }, h('strong', { text: x.name }), h('span', { class: 'small muted mono', text: '  ' + x.path })),
          guarded(h('button', { type: 'button', text: 'Add' }), async () => { const r = await api.post('/api/papers/add', { path: x.path }); toast('Added ' + r.name); await window.OLS.loadState(); window.OLS.route(); }))));
      }
    } catch (e) { found.append(notice('warn', e.message)); }

    // ---- writing book: prepared once, from the PDF in the inbox
    box.append(h('h2', { class: 'section-h', text: 'Writing book' }), await bookCard(st));

    const health = await api.get('/api/health').catch(() => ({ issues: [] }));
    box.append(h('h2', { class: 'section-h', text: 'Advanced' }), h('section', { class: 'card', 'aria-label': 'Advanced' },
      h('p', { class: 'small secondary', text: 'You do not need these for daily work. They show what happens behind the scenes.' }),
      h('ul', null, h('li', null, h('a', { href: '#/panes', text: 'Test the side panes' }), ' (can QuillBot and Overleaf sit beside the chat in your browser?)'),
        h('li', null, h('a', { href: '#/paper_index', text: 'What the app remembers about this paper' }), ' (notes and learned preferences)'),
        h('li', null, h('a', { href: '#/monitor', text: 'Charts and diagnostics' }))),
      h('details', null, h('summary', { text: `Health check (${health.issues.length} item${health.issues.length === 1 ? '' : 's'})` }),
        health.issues.length ? h('ul', { class: 'small' }, health.issues.map((i) => h('li', null, h('strong', { text: i.scope + ': ' }), i.text))) : h('p', { class: 'small', text: 'Everything is set up.' })),
      c.in_docker ? h('p', { class: 'small muted', text: 'Running in Docker: the app sees only the folders you share, the data folder and the inbox.' }) : null));
  });
}());
