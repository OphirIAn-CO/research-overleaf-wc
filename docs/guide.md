# The guide

Everything the app can do, in the order you meet it. The short version is in the [README](../README.md).

Contents: [Setting up the services](#setting-up-the-services) · [Everyday use](#everyday-use) · [Your reading: the literature library](#your-reading-the-literature-library) ·
[Your analysis: code and results](#your-analysis-code-and-results) · [Writing new text from your sources](#writing-new-text-from-your-sources) · [Your files and writing material](#your-files-and-writing-material) ·
[The LaTeX pane](#the-latex-pane-your-papers-source-and-its-pdf-on-this-computer) · [QuillBot and Overleaf inside the app](#quillbot-and-overleaf-inside-the-app-the-built-in-browser) ·
[Sending to Overleaf](#sending-to-overleaf-and-why-it-sometimes-says-overleaf-has-newer-changes) · [Where things live](#where-things-live) · [Safety](#safety) · [Troubleshooting](#troubleshooting)

## Setting up the services

Install the app first (see the README: `./install.sh ui`, then `./ui.sh up`). Everything below is done **inside the app, in Settings**, except where a terminal is named.

```bash
./install.sh ui      # builds the Docker image once (several minutes, about 1.5 GB: it includes LaTeX and the built-in browser)
./ui.sh up           # starts it and opens your browser
./ui.sh down         # stops it (it also stops itself when idle)
```

1. **OpenAI**: paste your API key and the model name you want to use; press **Test** (it is a read-only check, no cost).
2. **Overleaf**: paste your Overleaf Git token (Overleaf: Account Settings, Git Integration) so the app can get the latest and
   send your changes; press **Test**.
3. **Writing book**: press **Choose the book PDF** and pick the file (a PDF with selectable text, up to 200 MB). It is sent to the app's private folder
   on your computer (with a progress bar) and read straight away; reading is local and costs nothing. (A PDF you put in `~/.config/overleaf-skill/inbox/` by hand is still
   offered under "Or use a PDF that is already in the inbox folder".) Optionally press **See the cost**, then **Make the summaries once**: a one-time step that makes every later request
   cheaper and sharper (it sends the book text to OpenAI once, shows the estimate first, and can resume).
4. **Text-cleaning service** (optional): the address of a local service that post-processes new text before it is saved (for example one that removes
   hidden characters or fixes typography). Leave it empty to save text exactly as you approved it; the card then says so. The service must answer
   `POST /inspect` with `{"text": ...}` (any JSON object) and `POST /clean` with `{"text": ..., "layer": "A"}` (JSON with the cleaned string under `text`); see
   `scripts/writing_core/watermark.py`. When it is set, every save goes through it first and a failing service blocks saving.
5. **Papers**: your registered papers are in the sidebar. To add more from the app, share the folder that holds your Overleaf
   clones once (`ui.sh folders add ~/path/to/folder`, then `ui.sh down` and `ui.sh up`); the app lists the clones in it with an
   **Add** button. `common.py add <path> --group "Mendeley group"` still works.
6. **Mendeley**: signing in needs a browser, so run `python3 scripts/mendeley_sync.py authorize` once (Settings shows the exact
   command). Link a paper to its group on the References page.
7. **Literature library**: the folder where you keep your books and articles, read once so the app can search and cite them ([below](#your-reading-the-literature-library)).
8. **Code and results**: the folder where your analysis lives, so the Results and the Discussion can be written from what it produced ([below](#your-analysis-code-and-results)).

Then just talk to it. The pages are **Chat**, **References**, **Activity** and **Settings**; the papers and your chats are in the sidebar.
The chat uses the width of your window. In an answer the explanation, questions and warnings sit on top (side by side when there is room), and the before/after comparison below them uses the full width of the card, **Now** and **Suggested** side by side; only a card narrower than about 520 pixels stacks them. The comparison's text is sized to the card, not the window, so it stays readable with a pane open beside the chat.

### Mendeley (optional) and Telegram (optional)

**Mendeley.** Do this on every computer that should talk to Mendeley (each one signs in by itself; do not copy the token file between computers).

1. Create an app at [dev.mendeley.com](https://dev.mendeley.com) with redirect
   URI `https://localhost/callback`.
2. Save its credentials in `~/.config/overleaf-skill/mendeley_credentials.json`:
   ```json
   {"client_id": "...", "client_secret": "..."}
   ```
3. Run `python3 scripts/mendeley_sync.py authorize`
   and follow the prompts (the browser page that fails to load at the end is
   expected; copy its address back into the terminal). Paste **the address the browser ends up on** (it starts with `https://localhost/callback?code=`),
   not the first link, and do it right away: the code works once and expires within a minute or two. If Mendeley says `invalid_grant` ("Invalid access code"),
   run the command again and use the new link; never reuse an old code.

*A coauthor on another computer.* Everyone should use **their own Overleaf Git token** (Overleaf: Account Settings, Git Integration) and sign in to Mendeley on their own computer. If Overleaf's Git will not let
them in, see [Working with a coauthor](#working-with-a-coauthor-overleaf-github-and-the-token).
Best is that they use their own Mendeley account (you invite them to your Mendeley group) and their own Mendeley app, so that nobody shares a login or a client secret.
If you do share an app, the `client_id` and `client_secret` in `mendeley_credentials.json` are a secret: send them privately, never through git.

**Telegram.**

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. Create `~/.config/overleaf-skill/telegram.json`:
   ```json
   {"token": "<your token>", "allowed_chat_ids": [], "push": true}
   ```
3. Start the bot: `python3 scripts/doi_bot.py run`
4. Send your bot any message. It replies with your chat id. Put that number in
   `allowed_chat_ids`, then restart the bot. Until you do, it refuses everyone.
5. Keep it running after a reboot (macOS): `install.sh bot-service`

**Using it:** send a DOI or a `doi.org` link. The bot adds it to the paper you
last worked on in Claude Code, and every reply says where it went:

```
[my_paper @6aa33c64 -> My Mendeley group]
Added \cite{Alves2018Brazilian}
Pushed to Overleaf.
```

If you haven't touched that paper for a while, the bot asks which paper first.
`/paper` switches papers, `/status` shows the current target.

**Ready messages:** the same bot also tells you when a long request in the app is done (see [Knowing when a long request is ready](#knowing-when-a-long-request-is-ready)). The app sends them to the chats in `allowed_chat_ids`; turn them off in Settings.

## Everyday use

### Knowing when a long request is ready

A request that reads a whole section, or the whole paper, can take a few minutes. You do not have to watch it. In **Settings, When a request is ready** you choose how to hear that it is done:

- **A short sound** (two soft notes) in the browser tab.
- **A notification** when the tab is in the background, plus a tick in the tab's title (**✓ Ready · Overleaf assistant**) that goes away when you come back. The browser asks once for permission.
- **A message on Telegram**, through the same bot that adds your DOIs (it uses the bot's existing `telegram.json`, nothing new to set up). It reaches your phone even when the tab is closed. It says only the paper's name and one sentence ("Ready: 3 suggestions to review in my_paper"), never any text of your paper or of the answer.

All three are on by default (Telegram only if the bot is set up), and only for requests that took longer than 20 seconds (you can change that number). **Test** plays the sound, shows a notification and sends the Telegram message so you can see that each works. The sound and the notification come from the open tab, so keep the tab open (it can be in the background); some browsers also need you to have clicked in the page once since you opened it, which you will have done by sending the request. If Telegram does not answer, the app does not retry and nothing else is affected: the answer is in the chat as always.


### The assistant plans the work (the default)

Each message is read by the model **in the context of the whole conversation**, and the model decides what to do, in as many steps as the job needs. It has the app's abilities as tools: it reads your paper,
searches your library, searches the internet (several searches, with different words), writes new text, edits existing text, changes the LaTeX, adds references, counts what your files hold, and asks you one
specific question only when it truly cannot tell. It then tells you, in plain words, what it understood, what it did and found, and what to check.

*Example:* "now add it to the text with a passage supporting my idea, and don't use references from Amaya Gomez or Cabrales". It looks at the conversation to see what "it" and "my idea" are (a reference you picked,
a suggestion that is not saved yet), reads the part of the paper the idea belongs to, searches the library and the internet for the claim and leaves out those authors, writes the passage with the sources that fit,
and reports what it cited and what it could not support. There is no card to answer first and no stock instruction in the middle: the model writes the brief for the writing step itself, from the conversation.

- **The safeguards are where the work is saved, not in front of the work.** Every writing step is the same careful pipeline as before: citations, formulas and numbers are hidden behind tags, every answer is checked
  (numbers, names, dashes, LaTeX), a citation can only be one of the sources that were offered, and what a check refuses comes back to the model, which fixes the cause and tries once more, or tells you. **Nothing is
  saved**: you read each suggestion, change it, and accept it. A few limits apply: at most twenty steps, a ceiling on how much it reads from ChatGPT for one request, and it never repeats a step.
- **A request about the whole paper** ("a complete review for this journal: shorter, less verbose, better flow and grammar") is done section by section: it takes the sections from the map in order, makes one editing
  call per section (or a few short ones together, up to about twenty passages) with an instruction made for that section and for the journal, and leaves out what needs nothing. Each call is a suggestion card of
  its own that you accept or set aside. A web address you give (the journal's page) is read when the publisher allows it; many refuse automated readers, and then it says so and uses what it knows of the journal.
  It has up to twenty steps for a request and is told how many are left; if they run out it says what is done and what is left, and you say "continue".
- **"Check what is missing".** A request like *"revise the style, and also look for white spaces or missing information that would make the methodology more robust"* is two jobs: it improves the
  wording (a suggestion you accept or not) and it **lists the gaps in its answer**: what is missing, where it would go, and the question only you can answer (a value, a setting, a sample size, a software
  version, why a choice was made). It never writes the missing content into the text as if it were known. After a few reads with nothing written, it is told to stop reading and do the work.
- **It looks in your own files.** The methods say what you did; your code and results show it. When you ask it to check or strengthen the methods or the results, or the paper is unclear about a setting, a split, a metric, a threshold, a software version, a data source or how a number was obtained, the assistant **searches and reads the files the app can see** (your code, notebooks, results tables, logs, notes, and the articles and reports in the folders you linked; see *Code and results* and *Your files* in Settings) before it asks you. It compares what it finds with what the paper says and tells you:
  - **what the files show that the paper does not say** (so you can add it, or let it suggest the sentence, resting on those files, with every number taken from them);
  - **where the paper and the files disagree** (for example, the text says five folds and the code uses ten). It never settles that itself: it says which says what, and you decide;
  - **which files it read**, also listed under *What I did*.

  It only reads. It never runs your code and never changes a file, and anything it writes is a suggestion you accept or not. It sees the files by a short name ("analysis/results/metrics.csv"), not by the path on your computer; passwords, keys and hidden folders are never read; and **Settings, Code and results, "Never"** (or the same for *Your files*) stops it from reading that kind of file at all. What it reads goes to OpenAI as part of the request, like any other part of your files. The text it reads stays in front of it for the rest of the request, and a request can read about 48,000 characters of paper and files together (the oldest goes first).
- **How it reads your paper.** It never sends the whole manuscript in one go (a peer review is the one place that does), and it is not limited to a few pieces either. It always has a **map of the whole
  paper**: every section with the ids of its pieces, what kind of section it is (methods, results, abstract...), where it is and how long. It reads the text of what it needs, about seven thousand
  characters at a time (a long section takes a few reads, each starting where the last stopped), by piece id or range, by the kind of section (*"the methodology"* is the section called Materials and Methods, whatever its title), by title, or by a quoted sentence, and the step shows what it
  read ("Read: Materials and Methods (C9-C12, 4 pieces)"). **What it has read stays in front of it for the rest of that request** (up to about 36,000 characters, oldest first), so a section read in several turns is
  not forgotten and read again, and a part it already has is not sent twice. Citations and formulas show to it as `[ref]`. A writing step then gets only what it needs: the passages being edited, or the text around the place for
  new text, with your library and the sources it chose. If the part you named is not the one it works on, say so in your next message: it stays in the part you name.
- **"Required citations" is not guessed from a word.** It asks for a new citation to be required only when you ask to cite; a brief that says "keep the citations" is not a demand for one. If a result has no new
  citation where you wanted one, it sees that and decides.
- **What you see.** The steps appear while it works (and under *What I did* afterwards); each suggestion is the usual card; the last message says what was done. If you say "this part", it knows which suggestion you mean.
- **What still has its own card** (because you choose or confirm there): a pasted DOI, help, "what do my articles say about ...", a search for references that you want to choose from, a peer review, bringing your library into the
  bibliography, counting, the buttons under a suggestion (answer its questions, change the request, polish in QuillBot), and "Fix this" on a review comment. Choosing a kind under the message box, or switching on
  *Ask me before anything is sent*, also uses the classic way.
- **It costs more than one call.** Each step re-reads the conversation and what was found, so a request of five steps costs a few times a single edit (Activity shows it). If you prefer one fixed step per kind of
  request, set **Settings > Preferences > How I take your requests** to *Classic*.

### Checking that I understood you

*(This is how classic mode works; the assistant of the previous section needs no such card because it tells you what it understood when it is done, and asks only when it cannot tell.)*

Words alone do not say what you want ("create" inside a paragraph you pasted is the paper's verb, not a request to write; "add the citation" can mean searching the internet, using your reading, or pointing to an
article already cited). So every request is **read as a whole, in its context** by a small model call (about a thousand tokens), which sees your message with any pasted paragraph marked as material, the
recent conversation (so "it", "again" and "the second one" mean something), what you have set up (library, outlines, internet search), and your earlier corrections.

- **When I am not sure, I say what I understood and wait.** A card says, for example, *"You want references found on the internet for the paragraph you pasted, so they can be cited in it"*, with the text it is about,
  the kind of request, and which sources to use (my reading, the internet, my outline). You can press **Yes, go ahead**; change the kind of request or the sources and go ahead; write a correction or an addition and press
  **Read it again with my correction** (the card is replaced by the new reading); or **Not what I meant**, which does nothing. A note you write with your yes goes along with the request as your own words.
  Nothing is changed or sent before you answer.
- **When it is clearly right, I go ahead and show how I read it:** a line *"Read as: ..."* under your message, with **Not what I meant** next to it if I got it wrong (it opens the same card).
- **The kinds of request I distinguish:** improve or correct text that is already written; write new text; add in-text citations to text that is already written (from your reading, from references found online, or
  both); find references on the internet; change the layout or format (tables, figures, graphs, position, appendix); answer from your reading; peer review.
- **Whom or what to leave out is part of the reading.** *"Don't use references from Amaya Gomez or Cabrales"* is read as a list of authors to avoid. They are left out of the references found for you, of your library's sources
  and of the sources a suggestion may cite, and the card says how many records were left out for that reason. A name matches an author when all its words are in the author's name, whatever the order or the accents.
- **"The references just found"** means the ones you ticked earlier in this chat: when the reading says that is what you mean, they are used, and no new search is made.
- **You can say the kind yourself.** Under the message box, *This is a request to:* lets you choose the kind for the next message; then nothing is read and no tokens are used for it.
- **How often I ask** is a setting per paper (**Settings > Preferences > Check that I understood your request**): *only when I am not sure* (the default: I am not sure when I am not confident, when my reading differs from
  a quick read of the words, when I have a question, or when the request changes the layout), *every time*, or *never* (the words alone decide, as before; no model call is made to read it).
- **I learn from what you confirm, correct or refuse.** It is kept in `understanding.json` in the paper's own hidden folder (your words only, not your manuscript, and never shared with another paper), and the most
  similar earlier requests, with your corrections, are shown to the next reading as examples. **Settings > Forget what I learned from my corrections** removes it.
- If no model is set up, or the reading fails, the words decide as before.

### Saying which text you mean

You can say the place in words ("the text before Figure 1 in the methods", "the first paragraph of the introduction"), tick the passages yourself, or **paste the paragraph into the message**:
*"For this paragraph: ... there is no in-text citation here"*. The app looks for the pasted text in your paper (small differences, such as citations, formulas and line breaks, do not matter, and a single pasted sentence
finds its paragraph) and works on exactly that paragraph. The card says "I will work on the paragraph you pasted" and where it found it; only that paragraph is sent to OpenAI, whatever section the words around it
mention. If the text is not found (you changed it, or it came from somewhere else), the card says so and falls back to the part your words name; press *Cancel* if that is not what you meant.
"The ones selected" or "the references I picked" in a request means the references you ticked earlier in the same chat (see *Finding references on the internet*).

**When the words do not say where new text goes** ("add a passage that supports this part", "now add it to the text"), the app does not ask you the same question again. The model chooses the place from what you
wrote, how your request was read and the conversation: the pieces of your paper (with their sections, lines and first words) and the suggestions in this chat that are not saved yet. If "this part" is in a
suggestion you have not accepted, the new passage is written together with it, in its place, and the old suggestion is marked as replaced when the new one is ready. The card says which place it chose and why. It asks
only when it truly cannot tell, and then with a question that names what it needs ("the end of the introduction or the end of the methods?").

### Answering the model's questions and changing a suggestion

Everything about the part you are working on is gathered **in one box under the changes**, whatever the number of passages: when OpenAI has questions ("What specific criteria were used?", "Which software?"), the box lists
**every question of every passage** (each under the title of its passage), with a place for your reply (leave it empty to skip it), one box for "anything to add or change", and one button, **Send my responses and
re-edit**. Nothing is sent while you answer. A line at the top of the card tells you how many questions are waiting, and a line under each passage says its question is in the box.

- **Only the passages that need it are written again.** When the suggestion has several passages, the box lists them under **Passages to write again**. A passage you answer a question about is ticked for you; tick others
  to include them. The passages you do not tick are **carried over exactly as they are**, with their changes and the ticks you had, into the new suggestion (they say *Kept as it was*), and OpenAI is asked only
  about the others: the button says how many ("Send my responses and re-edit (1 passage of 3)"). A passage that failed is tried again. If a passage you asked for again fails, you keep the suggestion you had for it,
  with a note. Pick every passage to write the whole part again.
- **It remembers.** What you answered in earlier rounds about this part goes along with every new round, and **a question you skipped is recorded as skipped**: the model is told not to ask it again and not to guess.
  A question you answer later replaces the skip. The model is also asked to put at most three questions, only what stops it from doing the work, and none that the passage or your answers already settle. So you do not meet
  the same questions again in new boxes after each round.
- **One suggestion, not a stack.** The new suggestion replaces the old one when it is ready, and the old one shrinks to one line (*An earlier suggestion for this part, replaced by the one below*) that you can open; it can
  no longer be saved by mistake, and the reviewer's comment it belongs to follows the new one. It costs about as much as the first request, for the passages you ticked. If the new try fails or finds nothing to change, the
  first suggestion stays open.
- **The model does not ask you to choose sources.** When it would ask "Which sources should replace the placeholders?", the app answers that itself: it reads your library for every point of your outline, looks
  on the internet for the points your library does not cover, and cites what fits. If nothing supports a claim, the suggestion says so ("I looked in your library and the internet and found nothing that supports
  one claim") and writes the claim without a citation; it never hands the choice back to you.
- **If you want references looked for on the internet for one of the remaining questions,** tick **I do not have the references: look for them on the internet for this question**. It is a tick, not a button:
  nothing is searched when you tick it. When you press **Send my responses and re-edit**, the app searches (the words come from the ticked questions, your answers and the text), keeps the records that are about your
  subject, writes the suggestion again with every answer and cites them. You are not asked to choose; their entries are written to your bibliography only if the text you save cites them. (This path writes the whole part
  again.)
- **To change what was asked, or to add something not covered by a question,** use the same box: **Add instructions or change the request** (**Discuss and revise** when there was no question) opens it. It has the request
  as it was understood (for a reviewer's comment: the comment itself, which you can reword, under *Change what I was asked to do*), the box for anything to add or change ("keep it to two sentences") and the tick to look
  for references first.
- **A request for a shorter or longer text is not refused for being shorter or longer.** The app checks that an answer is not much shorter or longer than the passage (50% to 160% of its words), which protects against
  lost or invented content. When you ask to shorten, cut, condense or remove verbosity the lower limit is 20%; when you ask to expand, the upper limit is 250%. An answer that is still too far off says so in plain words
  and what to say ("cut it to half").
- **An answer that does not fit is asked for again with more room.** A long passage needs a long answer (the whole revised text, the list of changes and the explanation). The app asks for more room from the start for a long
  passage and, when an answer is cut off, asks again with twice as much (up to 16,000 tokens) before it gives up; if it still does not fit it says to ask for a smaller part.

### Going through a peer review

 The review lists its comments; **Fix this** on one turns it into an ordinary suggestion. While a review with
two or more comments still has open ones, a **Reviewer comments** panel stays above the message box with all of them, so after every decision you
see what is left without scrolling back. Each comment shows where it stands, with a colour and a mark: *Working on it*, *Suggestion ready: your
decision*, *Completed - saved here, not in Overleaf yet*, *Completed - sent to Overleaf*, *Set aside* (you pressed Not now), *OpenAI would change
nothing here*, *Skipped* (you pressed Skip: this comment needs no change; **Bring it back** undoes it) or *Could not finish* (**Try again**).
**Fix the next one** takes the next open comment. When the last comment is handled the panel goes away and the chat says so ("All 7 reviewer
comments are handled ... Let's continue. What would you like to work on next?"), with a button to send what was saved but not sent yet. The
review itself stays in the conversation with the final status of each comment, folded away.

### What the chat remembers, and starting again

 Very little. A request goes to OpenAI on its own: the earlier messages of a chat are *not* sent. Inside a chat the
only memory is the place of your last request, so "make it shorter" means the same passage. For the paper, in every chat, two things carry over: the notes about it
(key terms, tense, notation; **Settings > Advanced > Paper index**) and a short style note built from which suggestions you kept or set aside. To start again:
**Start over** (header, or in the **?** panel) empties the current chat and keeps it in the same place; **+ New chat** (sidebar) opens a second, empty one and keeps
the old; **Delete this chat** and **Rename** are in the **?** panel; **Forget my past decisions** (also there, only when something was learned) erases the style note.
**What you have typed is kept.** Text in the message box, your answers under a model's question, the "anything to add" box, the search words of a references card, the box with your own version of a suggestion and the DOI box are kept while you open Settings or another page and come back, and when you reload the tab. They are emptied when you send them (a send that fails gives the text back), and forgotten when you close the tab. Each paper and chat keeps its own. Passwords and tokens are never kept this way.
None of these touch your paper or the suggestions already saved in your files. **Which chat belongs to which paper:** each chat is a small file inside its own paper's folder (`<paper>/.overleaf-skill/chats/`, git-ignored, never sent to Overleaf), so the sidebar lists exactly the chats of the paper you selected, a chat id from another paper opens that paper's own chat and never this one, and moving or re-cloning a paper folder takes its chats along only if you copy `.overleaf-skill/` with it.

## Your reading: the literature library

![The literature library card in Settings](images/library.png)

Point the app at the folder where you keep books, articles and PDFs (**Settings > Literature library**). Subfolders are read too. Word, text, Markdown, HTML and `.bib` files are read as well.

- **Read once, on this computer.** Press **Read the folders**. Each file is cut into small labelled parts (about 350 tokens, with the page it came from and the heading above it). A first reading takes about a second
  per article; afterwards only new or changed files are read again (**Forget it all and read again** starts over). Reference lists of articles are left out. Scanned PDFs with no text are listed
  with a note. Your files are only read, never changed, and nothing is sent anywhere by reading.
- **Titles, authors and years** come from the PDF's text, then from file names like `Smith et al. - 2020 - Soil moisture.pdf` (the Zotero and Mendeley style), and best of all from a `.bib` file in the same folders:
  a document that matches one of its entries (by DOI, or by an almost identical title) takes its details, and the entry is not listed twice. The list says where each document's details came from.
- **Search it from the chat.** Ask a question about your reading and you get the best parts, with the document, the page and how to cite it, found on your computer (nothing goes to OpenAI):
  *"what do my articles say about well data curation?"*, *"search my library for stratified sampling"*. A part whose document is already in the paper's bibliography shows its `\cite{key}`.
  One that is not has **Add to my bibliography**: it copies the entry from your `.bib`, or adds the document by its DOI the usual way (bibliography, Mendeley group, Overleaf).
- **Every document is a reference.** A document of your library can be cited right away, whatever it is: it takes its entry from a `.bib` record, else from its DOI (the one in the file, or the one Crossref
  gives for its exact title), else from the title, authors and year the file itself gives. An entry made from a file is marked in its `note` field ("check the details") and the suggestion tells you which ones
  they are. Only a file that says too little about itself (no title, or neither authors nor year) cannot be cited, and the app says why for each such document.
- **Bring the whole library into the bibliography.** *"Convert the documents of my library into references"* (or *"bring my library into the bibliography"*) lists what each document would become, with its citation key
  (the "tag" you can cite it with). Tick the ones you want and press **Add the ticked to my bibliography**; they are written to your `.bib` and sent to Overleaf. Nothing is written before you press the button.
  *"How many references are in my .bib file?"* (or how many figures or tables the paper has, or which documents are not in the bibliography yet) is answered from your files, with nothing sent anywhere.
- **Use it in a request.** A request that asks for sources, citations or your reading ("support the sampling statement with my library", "add citations to the introduction") sends the best parts to the model
  (for a long outline: the best parts for every point of it, up to 24 sources)
  with it. Each source the paper's bibliography already has comes with a tag the model may write to cite it; the app turns the tag into the real `\citep{key}` (or whatever command the paper uses most).
  Any other citation is refused, and a source that is not in the bibliography can inform the wording but is never cited. Per paper you can set the library to *when I ask* (default), *every request* or *never*.
  The request card lists the parts that go out; the answer lists the citations it adds.
- **From the terminal and Claude Code.** `writing.py library --add PATH --index`, `--search "text" [--paper NAME]`, and the `library_query` tool.
- **In Docker** the app sees only the folders you share with it: `./ui.sh folders add --read-only ~/Documents/Literature`, then `./ui.sh down && ./ui.sh up`. Folders shared that way are offered as buttons under the folder box.

## Finding references on the internet, and citing them

Ask for them in the chat: *"find references about how armed conflict affects energy infrastructure in Colombia"*, *"suggest sources for the first paragraph of the introduction"*, *"search for papers on pipeline attacks"*.
The app looks on the open web the way you would (a web search, then the page of each paper that comes up, which carries its title, authors, year, journal, abstract and DOI) and asks two open scholarly
indexes, OpenAlex and Crossref, as a second source. Only a few search words leave your computer. You get a card with each paper, its abstract, a link to it by DOI and, for a paper already in your
bibliography, its `\cite`.

- **When you ask for citations while writing or editing, the app searches for you.** If the request asks for citations or sources (or your outline has places marked [REF], (Author, year) or "citation needed"), the app
  looks for papers on the internet for each point of the outline (or for the passage), keeps only the records whose title or abstract is about your paper's subject, and offers them to the model with the ones in
  your library. Nothing is shown for you to choose from, and an entry reaches your bibliography only if the text you save cites it. The suggestion lists what it cited and marks each as found online. A record
  that merely shares a word of your request with your paper ("ideas", "introduction") is never used.
- **A search you ask for yourself shows its findings.** For *"find references about X"*, the card lists the papers that are about the subject (and says how many it left out because they were not), and nothing is added by itself. Tick the papers you want and press **Add the ticked to my bibliography**: they go into your `.bib`, your Mendeley group and Overleaf like a pasted DOI, and the card gives the `\cite` to paste.
- **From a question in a suggestion.** See [Answering the model's questions](#answering-the-models-questions-and-changing-a-suggestion): ticking the question makes the app search, pick and cite without a list to choose from.
- **A reference you tick is added even when its record cannot be read.** If doi.org's record for a DOI is broken or missing (for example it lists an empty author), the title, authors and year the search found make the
  entry, and its `note` says so: check it. A record with an empty author name is cleaned (the empty name is dropped) before the key is made.
- **What the model knows about a paper found online is its abstract.** The app never has the paper itself. The suggestion says so next to the citation ("cited from its abstract only: read the paper"), the model is told to say
  no more than the abstract says, and a citation is only ever one of those you added; an invented one is refused like any other.
- **Later requests can cite them too.** Once added, a request that asks for sources or citations ("support the sampling statement with a source about armed groups attacking pipelines, and cite it") is offered the
  references of this paper that fit it. The author's own answers count as a request: if you answer a question with "add a citation of how violence affects infrastructure", the references (and your library) are used.
- **Your uploaded reading is cited and referenced the same way, even when the paper's bibliography does not have it yet.** A document of your library with a DOI (or an entry in a `.bib` file there) can be cited right away; the
  suggestion says "will be added to your bibliography when you save", and the entry is written, with the Mendeley copy, in the same click that saves the text, only if the saved text really cites it.
  If the document then has no working bibliography command, saving warns you ("the references are in your .bib file, but LaTeX will not print them yet: ...") and you can ask *"include the bibliography with the .bib file"*.
- **Switch it off** per paper: **Settings > Literature library > Search the internet for ...: Never**.
- **If the search finds nothing or cannot connect**, the card or message says so, and says which of the web search, OpenAlex and Crossref did not answer. Search pages sometimes refuse automated requests or change
  their layout; the two indexes are the backstop. Change the words with **Search again**, or paste the DOI of a paper you know. A page found on the web that names no DOI is listed apart and cannot be added.

## Your analysis: code and results

![The Code and results card in Settings](images/code-and-results.png)

Give the paper the folder where your analysis lives (**Settings > Code and results**), so that the Results and the Discussion can be written from what the analysis really produced.

- **What is read:** scripts and notebooks (Python, R, MATLAB, Julia, SQL, shell; a notebook's text output but not its embedded pictures), tables and numbers (CSV, TSV, JSON, YAML, LaTeX tables the code wrote), logs, notes, and **figures** (PNG, JPG, GIF, WEBP).
  Subfolders are read; hidden folders, build folders, secrets (`.env`, keys, names containing `password` or `token`) and links are never read. Nothing is changed. The card lists every file the app can see, by kind, with **See the parts**.
- **When a request uses it.** In the default mode, a request that is about your results, figures or code ("explain my results", "what do the figures show", "describe the results from my code") brings the right parts:
  the numbers and tables first, the code that produced them, and the figures. An ordinary edit ("improve the flow of the results") does not. Set it per paper to *every request* or *never*.
  The request card says how many parts and pictures go out, and **What goes out** lists them.
- **Figures go to OpenAI as pictures** (at most 4 per request, shrunk to 1024 pixels on the long side, only when the request needs them). They are used to describe what a plot shows: a trend, an order, groups,
  an outlier. The model is told never to read an exact value off a picture, and a number that is in no text part of your files is refused like any invented number. If the model you use takes no pictures, the app
  asks again without them and says so.
- **Numbers.** A number that stands in the parts that went with the request is accepted and pointed out in the answer ("Numbers that came from your files: 0.412. Check them against the source"); one found nowhere is refused.
- **In Docker**, share the folder read-only first: `./ui.sh folders add --read-only ~/code/analysis`, then `./ui.sh down && ./ui.sh up`. Claude Code in your terminal needs no sharing:
  `writing.py settings --paper NAME --link-analysis PATH` links a folder the same way.

## Writing new text from your sources

![A draft of the Results written from the analysis](images/draft.png)

Asking to *write* something (not to improve what is there) is understood as a request for new text:
*"write the results from my analysis"*, *"draft the discussion of these results"*, *"explain the results in the discussion"*, *"add a paragraph about the sampling to the methods"*.

1. **Where it goes.** At the end of the part you named (or the start, if you say "at the beginning of the results"), or before or after a figure or table ("after Table 1"). The plan says exactly where
   ("at the end of the results, after line 34 of main.tex"). If the paper has no such section yet (a new paper with only a title and an abstract, say), the section is made: the new text comes with its own
   heading, `\section{Introduction}`, placed where that section usually goes (after the abstract, or before the first section that normally follows it). The card says so, and nothing is saved until you approve.
   Only the abstract is not made this way. Text that is already there is never changed.
2. **What it rests on.** Whatever you wrote or pasted in the request: an outline, notes or a literature review you want the section built from count as your own material (they appear as the source *request*), so
   long messages are fine (up to 30,000 characters; the chat tells you if one is cut). The request itself is read from the first or the last lines of a long message: a phrase like "now move on to methods" in the
   middle of pasted material does not change which section is written. The code output, tables, logs and figures of your analysis, the notes and reports in the folders you chose for the paper, the parts of your library that match the paper's topic (when you
   ask for them), the few pages of your writing book about that part of a paper, and the text just before and after the place. The plan lists them before anything is sent.
3. **What the model may say.** Only what those sources state. Every number must be in them (the same check as for an edit); a figure or table of your paper can be mentioned only through a tag that the app turns into
   its `\ref{...}`; a source of your library can be cited only through its tag, and only if your bibliography has it; no LaTeX, headings or lists; no dashes. When something needed is missing, the model leaves it out and asks.
   **How it reads.** New text is meant to read like an author explaining, not like a list of facts. The model is asked to put things in its own words (not to copy your outline, your notes or an article), to join
   related facts, and to add a **connecting comment** where a reader needs one: a sentence that links one fact to the next, says what the facts mean for the question the section asks, or closes a paragraph with its
   point. A comment may only relate, compare, order or sum up facts it has already stated, and when it interprets it hedges ("suggests", "is consistent with"). It never adds a fact, number, name, cause, comparison or
   citation. It is meant to be about one comment for every two or three sentences of fact, and it counts toward the length you asked for, so it does not pad. The model marks each connecting sentence, and the card shows
   it as "connecting comment, adds no new fact" so you can tell it from a sourced statement. Checked on your computer: text copied from a library article is sent back to be rewritten; wording that claims more
   than your material ("for the first time", "proves", "unprecedented", "statistically significant", "significantly higher") is sent back unless something you gave says it; and you get a "Heads up" when a draft
   stays very close to your own wording, when connecting comments are more than about a third of it, when a comment brings in words your material does not have, when it uses stock phrases ("it is important to note",
   "plays a crucial role") or starts several sentences with "Moreover" or "Furthermore", or when it is well over the length asked for.
4. **What you get.** A suggestion with one change per paragraph (**Now: nothing here yet**, **Suggested**: the paragraph), so you can keep some and leave the rest. Each paragraph says which file and rows or pages it rests on,
   and **What each statement rests on** lists every statement with its source. Numbers that are new to the paper are pointed out so you can check them against the source.
5. **Questions.** If the model needed something the sources do not hold ("how many farms were held out?"), the question has a box for your reply; **Send my responses and re-edit** writes it again for the same place with your
   answers, which it may use as given. The new suggestion replaces the old.
6. **Saving** puts the text between the two lines it was written for, with a blank line each side, exactly like any other suggestion (**Save in my files**, **Save and send to Overleaf**, or polish it in QuillBot first).
   If either of those two lines changed since the draft was written (for example after you got the latest from Overleaf), nothing is written and you are asked to write it again.

### Writing from an outline, with your reading and your citations

*"Write the introduction of my paper based on `outlined_intro.md` sections 1-4 and the literature review in my library. Use the rules of the writing book for cohesion and flow. Cite properly in-text."*

- **The outline.** Add it with **Add outline** (it stays on this computer, is never cited and never sent to Overleaf). Name its file in the request, or say "the outline" when the paper has only one (with several, the one whose
  name shares a word with the request, such as `outlined_intro.md` for "the introduction"; if that cannot be told, the app says so and uses none, so name the file). Say which sections you mean ("sections 1-4",
  "points 2 and 3", "the first three sections") and only those go to the model, together with any general guidance written before the first numbered section. The plan says which sections went ("sections 1-4 of 5");
  if the numbers you asked for are not in the outline, all of it goes and the plan says that too.
- **What is looked up is the subject, not the way of writing.** The words used to search your library and the internet are made from the outline sections you asked for and the paper's abstract and notes. The sentences of
  your request that only say how to write (the book's rules, grammar, cohesion, flow, orthography, how to cite, which file to follow) are never searched for. **The writing book gives rules of style and nothing else**: it is
  not a source, is not searched for subject matter, and a guide to writing that sits in your library (a book on academic writing, a style manual, a copy of the book you imported) is skipped as literature. Searches that
  would be about academic writing itself are refused, and so are such papers in the results of an internet search, unless your paper is about writing.
- **Citations.** When the request asks for them ("cite", "in-text citation", `\cite`), the papers and books of your library on the subject are cited. Each document needs a bibliography entry, and the app gets it for you: from a
  `.bib` file in the library if one has it, otherwise from the document's DOI (read from its first pages, from a publisher's footer, from its copyright pages and from the PDF's own metadata; the reference list is never
  read for it), otherwise by looking its title up at Crossref. The entry (`Author2019Word`, and the Mendeley copy) is written to your bibliography only when you save a text that really cites it, and the `\cite{key}` uses that key.
  A document that cannot be cited says why in the plan ("the file names no DOI and has no bibliography record", "I could not get the reference data for DOI ... from doi.org"), so you know what to fix: add its DOI, or a `.bib` with its entry,
  to the library folder.
- **The library is read for every point of the outline**, not for the request as a whole: each numbered section is looked up in your library and the best parts of each go along (up to 24 sources, one library
  of dozens of papers being used as a literature review). Where the outline has a placeholder for a reference ([REF], [ref: topic], (Author, year), "citation needed"), the model picks the source that fits and cites it;
  it is not allowed to ask you which sources to use.
- **Where the library has nothing for a point, the app searches the internet itself** (a few words per outline section), keeps the records whose title or abstract is about your subject, and cites those that fit
  (marked as found online; the model sees only their abstract and may say no more than it says). If nothing citable on your subject exists in the library or on the internet, the app says so and does not write an
  uncited text. The same happens when you ask for an in-text citation in a pasted paragraph or a selected text: the search is made from that text.
- **Complete paragraphs.** A request to write a section from an outline produces the paragraphs themselves, with the section heading when the section does not exist yet, not edits.

## Changing the LaTeX: tables, figures, graphs, the appendix, the bibliography, the document's setup

Ask for it in your own words: *"move this table to the appendix and add the respective reference in tex"*, *"improve the visualization of Table 2"*, *"improve the graph in the following way: larger fonts and a legend"*,
*"adjust the position of the image and the labels (a), (b) of its parts"*, *"make the figures smaller and centre them"*, and the document itself: *"in the latex before the end document include the bibliography with the .bib file"*,
*"the bibliography is not rendering, fix the latex"*, *"add a table of contents"*, *"add line numbers"*, *"load the package booktabs"*. This kind of request changes the **LaTeX** of the paper, never what it says.
**Any LaTeX problem or question works, not only the bibliography:** *"my paper does not compile, fix it"*, *"why does it say Missing $ inserted?"*, *"the table sticks out of the page"*, *"the figure does not appear"*,
*"citations show as ??"*, *"what does this warning mean?"*. For a request about the document, the app **compiles your paper on this computer first** (nothing is sent for that) and sends what LaTeX itself said with the
request: each error with the lines of your source around it, citations and references without an entry, files it could not find, warnings, text or tables that stick out. The model starts from the first error (later ones are
often consequences), fixes the cause with the smallest change, or, when you asked a question, answers it in words that point at the line of your paper and changes nothing. After a fix, the compile check says how many
errors there were and how many are left ("your paper had 3 LaTeX errors; with this change it has none"); a fix that helps but does not finish is kept and says how far it got. If the cause is outside the LaTeX files
(a picture file that is not there, a package your template or Overleaf does not provide), it says so and what you can do.
The model does the work, whatever the request is about: Python only hands it the right parts of your paper and checks what comes back. A request that names no figure or table is never refused for that reason; it gets
the preamble, the structure, the end of the document, which bibliography files and system the paper uses, any bibliography problem found on your computer (for example `\printbibliography` without biblatex, which LaTeX
reports as "Undefined control sequence" and which leaves every citation without an entry although the references are in your `.bib`) and the last compile of the LaTeX pane. If it needs something it was not given,
it asks you a question instead of guessing.

1. **It is read first.** A request about layout is always shown back to you before it goes anywhere (see *Checking that I understood you*), and you can choose *LaTeX help* yourself under the message box.
2. **Which table or figure.** Name it ("Table 2", "the last figure", a word of its caption, its label such as `tab:results`), tick it in the text, paste its LaTeX, or say "this table" when there is only one. With no clue it
   lists the figures and tables of your paper and asks. Requests about the page or the document itself (margins, spacing, font size, columns, the bibliography, packages) need no figure.
3. **What goes to OpenAI.** The LaTeX of that figure or table as it is (not hidden behind tags, since it is what changes), the lines that mention it, the outline of the document, its preamble and the end of the
   document. The plan lists it and can show the exact text. Nothing is sent without that plan; with *Ask me before anything is sent* on, it waits for you.
4. **What comes back** is a list of exact edits ("find this text, put that text"), each with its reason. A move is a removal plus an insertion plus the sentence that now points to the table; they are
   **applied together or not at all**, so a table can never be lost halfway.
5. **Checks on your computer, before you see it.** Every "find" matches exactly one place; environments and braces still balance; every `\label` still exists once and every `\ref` still has its label; citations,
   picture files and the words and numbers of tables, figures and captions are unchanged (unless you asked to rename, add a column, round and so on); few words are added to the text; no unsafe command (running programs,
   reading files outside the paper); new packages are named; and, when LaTeX is installed, **a copy of the paper with the change is compiled** and must compile as well as the paper did. If a check fails the model is
   told why and asked once more; if it fails again nothing is changed and you are told which check.
6. **You decide.** Each change is shown as *Now* and *After*. **Apply and save in my files** writes them (the text cleaner, which is for prose, is not used), **Apply, save and send to Overleaf** also sends them, **Not now** sets
   the suggestion aside. Then look at the PDF in the LaTeX pane. A file that changed since the suggestion was made stops everything: ask again.
7. **What it cannot do.** It changes LaTeX, not pictures: for an image file (png, jpg, pdf) it can change size, position, caption, labels and the layout of subfigures, and it asks what you want when the picture
   itself would have to change. A graph drawn in LaTeX (pgfplots, tikz) can be changed in its code. A very long table is sent only in part, and the plan says so.
8. **In Claude Code** the same job is done directly with the editor tools; `SKILL.md` lists the same checks to make by hand.

## Your files and writing material

Everything else you want the text to draw on goes in **Settings > Your files and writing material** (or press **Add a file** next to the message box, or drop files on the chat).

- **Add a file:** an article you cite (PDF or Word), a table of results (CSV or TSV), notes (text, Markdown), JSON, a notebook, or code. Pictures are kept but not read here (pictures of *your analysis* are read in the Code and results folder). Excel files: save as CSV first.
  The file is saved in the `references/` folder of your paper, committed on its own (whatever else is waiting stays waiting) and sent to Overleaf, where it shows in the file list. Switch the sending off on the card to keep files here
  until your next send. A second file with the same name never replaces the first (it becomes `name (2).pdf`); passwords and keys are refused; 25 MB per file.
- **Add a folder of material:** a folder (with subfolders) of notes, interview transcripts, reports and articles (PDF or Word) that live outside your Overleaf project. They stay where they are and are only read.
  When you ask for new text to be written, this material is part of what it rests on. In Docker, share the folder first with `./ui.sh folders add --read-only <folder>`.
- **Split into parts.** Each file is cut into small labelled parts of about 350 tokens: a PDF by page and heading (the reference list is left out), a table into a summary (rows, columns, min/max/mean of each numeric column) plus blocks
  of 25 rows, code at its functions, Markdown and Word by heading, a notebook by cell. **See the parts** lists them with where each came from ("pages 3 to 4 · Methods", "rows 26 to 50").
- **When a request uses them.** Never silently. By default a request uses your files when it names one (`rewrite the results using results.csv`) or talks about them ("based on my data", "check it against my files", the
  **Use my files** chip). Everyday words do not count: a file called `results.csv` is not named by "improve the results". You can set it to *every request* or *never* per paper. Only the few best parts go (about 1,800 tokens).
- **Claude Code in your terminal** does not need Docker or sharing: it reads any folder you point it at, through the same parts (`paper_query` with `kind: "source"` or `"project"`).
  `writing.py settings --paper NAME --link-folder PATH` links a folder of material; `writing.py files` lists what is readable.

## The panes next to the chat

### The LaTeX pane: your paper's source and its PDF, on this computer

 Press **LaTeX** above the chat. On the left is the
paper's source (`.tex` and `.bib`, a file menu at the top), coloured, with line numbers; on the right is the PDF made from it, drawn page
by page and sharp at any zoom. Nothing is sent anywhere to make it: the paper is compiled by `latexmk` on your computer (in the Docker
image, or with your own MacTeX/TeX Live when you run the app without Docker), and the PDF and the other files LaTeX writes stay out of
what is sent to Overleaf.
- **Edit and compile.** Type in the source. A moment after you stop, it is saved and compiled (**Auto** switches that off; **Recompile**
  or Cmd/Ctrl + Enter does it at once, Cmd/Ctrl + S saves). Errors are listed in words with their file and line; click one to go there
  (the line is marked red). Undefined citations and references, and missing packages, are listed too. The bibliography runs by itself.
- **The assistant's changes show up in the source.** When it saves a suggestion (or you get the latest from Overleaf), the file that
  changed is shown, the changed lines are highlighted for a few seconds, and the PDF is made again. A green mark in the margin shows
  every line changed here since the last send, i.e. what **Send to Overleaf** will put into Overleaf. Sending itself is unchanged.
- **Source and PDF lead to each other (SyncTeX).** **Show in PDF**, or Cmd/Ctrl + click a line, finds the line in the PDF; double-click
  the PDF to go to its source line. **Source / Both / PDF** chooses what to show; the pane stacks the two when it is narrow. **-**, **+**,
  **Fit**, the page arrows and **Download** work on the PDF.
- **Safe by design.** A file you are editing is never overwritten behind your back: if the assistant saved a suggestion to the same file
  meanwhile, you are asked whether to load the new version or keep yours. Only the paper's own text files can be opened. The compiler runs
  without shell access (`-no-shell-escape`), may not write outside the paper, and is stopped after three minutes
  (`OLS_TEX_TIMEOUT` seconds) with everything it started.
- **No LaTeX on the computer?** The source still shows and can be edited; the pane says what to install (MacTeX, or rebuild the Docker
  image with `./ui.sh build`; `OLS_WITH_LATEX=0 ./ui.sh build` leaves LaTeX out to save about 1 GB). The Overleaf pane stays available.

### QuillBot and Overleaf inside the app (the built-in browser)

 Press **QuillBot**, **Overleaf** or **Paper text** above the chat and the
page opens in the side area. QuillBot and Overleaf refuse to be shown inside another page, so the app starts a real browser (Chromium) in
its own Docker container and shows you what it sees; your mouse and keyboard are passed to it, so it works like a browser tab inside the app.
You sign in once and it stays signed in (the sign-in is kept in `~/.config/overleaf-skill/browser/`; delete that folder to sign out).

- **Sign in:** type your email and password in the page as usual, or save them once in **Settings > Sign-in for QuillBot and Overleaf**
  and press **Fill my sign-in** in the pane. The app types them into the form, checks the page kept them (and tries two other ways if
  it did not), and tells you what happened; you press the site's own Sign in button, so a code or a "not a robot" check stays with you.
  Saved details are in `~/.config/overleaf-skill/sites.json` (`{"quillbot": {"email": "...", "password": "..."}, "overleaf": {...}}`),
  readable only by you and **not encrypted**; the app never shows a saved password again. Google, Apple or university sign-in cannot be
  filled (and Google refuses embedded browsers): for these two sites use an email and password (Overleaf: Account settings, set a
  password; QuillBot: "Forgot password" lets you set one).
- **"Verify you are human" checks.** QuillBot (Cloudflare) and Overleaf (reCAPTCHA) check visitors, and a browser that a program drives
  carries marks those checks can see. So the built-in browser comes in two kinds. **Overleaf** uses a scripted one (it passes). **QuillBot**
  uses an *ordinary* Chromium window on a virtual screen, started with a normal command line and no automation at all: the app takes a
  picture of that screen and passes your mouse and keyboard on as keyboard and mouse events, the way a remote-desktop viewer does. You will
  see the browser's own tab strip and address bar inside the pane. Its address bar works, but it can only open public websites. It is
  Linux Chromium on your own internet connection, not your Mac's Chrome, so a site can still decide not to trust it; nothing here tries to
  solve or get around a check. When a pane shows one, a note appears above it: click the checkbox in the picture, and if it keeps coming
  back press **Open QuillBot in a window instead**. That sends the site to a window of your own browser, which passes these checks, and
  remembers it (**Settings > QuillBot and Overleaf inside the app**, "Use the built-in browser for ..."; you can switch it back there).
- **Layout:** one pane uses the whole side area; **Both at once** (strip at the top of the area) stacks QuillBot above Overleaf; **Wider**
  gives the area more room; drag its left edge to resize. The page is drawn at least 1100 pixels wide and shrunk to fit, so the sites keep
  their desktop layout; **Aa** in the pane draws it narrower for bigger text. Back, forward, reload, stop and home are in the pane's toolbar.
- **Copy and paste:** Cmd/Ctrl+V pastes your clipboard into the page, Cmd/Ctrl+C copies the text selected there to your clipboard, and
  **Copy what I selected** does the same with a button. Tab and Esc go to the page; click outside it to leave.
- **The tone pass (QuillBot) step by step.** Under an answer, press **Copy: for QuillBot** (it copies the text with your citations and formulas as
  tags) or **Polish in QuillBot or by hand**. Either one opens a panel right above the message box, so you never scroll back up to the suggestion,
  and QuillBot opens beside the chat. The panel shows four steps and marks the one you are on: (1) **Copy the text** (**Put it in QuillBot** types
  it into QuillBot's writing box); (2) adjust the tone in QuillBot, or edit **Your final version** by hand; (3) **Bring the result back**:
  **Take QuillBot's result** brings back what QuillBot copied with its own Copy button, **Take the text I selected** brings back the text you
  selected there, **Paste what I copied** takes your clipboard; (4) the notes under the box confirm every `[[LX001]]` tag (your citations and
  formulas) is still there, then **Save and send to Overleaf** (or **Save only**). Saving closes the panel and the conversation says what was saved
  and sent. **Hide** folds the panel away without losing what you typed; **Close** drops it. **Copy my version as LaTeX** copies what is in the box
  with the citations back in place. Sending to Overleaf reloads the Overleaf pane so you see the update. (The QuillBot buttons are best effort on
  the real site; if one does not land, click in QuillBot's box and paste with Cmd/Ctrl+V.)
- **"Hand to Claude Code instead"** is only for when you work in Claude Code: it leaves the ticked changes for Claude Code to apply (after your
  text-cleaning step). You never need it to save from the app.
- **No text-cleaning service?** Saving then writes the text exactly as it was approved, and the card says so. With a service set up in Settings
  it always runs first and a failing service blocks saving.
- **Settings > QuillBot and Overleaf inside the app** shows whether the built-in browser is ready, the QuillBot address, each paper's
  Overleaf project address (normally built from its Git address), and how to show them: automatic, always the built-in browser, always a
  separate window beside the app, or inside the page as a frame (usually blank).
- **Speed, and what to do when it is slow.** The pane is a live picture of a real browser running inside Docker, so every scroll has to travel
  there, be drawn there without a graphics card, and travel back. Overleaf's editor and PDF preview are heavy pages, so how smooth it is
  depends on the CPUs Docker gives that container. What the app does about it: the browser pushes its own pictures as the page changes (instead of
  being photographed again and again, which roughly doubled the frame rate in tests), scroll notches that queue up are sent as one scroll,
  and animated scrolling is off so one notch is one picture. If it is still slow, the pane notices (a scroll or a key taking over 0.7 seconds to
  show, five times) and says so with a button, **Open Overleaf in a window beside the app**; the **↗** button in the pane header does the same any time.
  That window is your own browser: instantly smooth, already signed in, and the pane is replaced by a button that brings the window forward. You can
  switch back in **Settings > QuillBot and Overleaf inside the app**. To make the built-in one faster: give Docker more CPUs (Docker Desktop >
  Settings > Resources, 4 or more), close other heavy tabs, and keep the pane at one site at a time.
- **Safety:** the browser runs in a second container (`overleaf-browser`) on a private Docker network with nothing published to your
  network. It has no access to your OpenAI key, Overleaf token, papers or book, only its sign-in folder. It refuses to open local or
  private addresses, refuses downloads, and the app can only start it on the QuillBot and Overleaf project addresses. Chromium runs there
  without its own sandbox (a container cannot give it one), so the container itself is the sandbox: read-only, no capabilities, no new
  privileges, not root. It stops itself after hours of not being used, and `ui.sh down` stops it.
- **If it is not available** (an old image, or Docker could not start the container) the app says so in Settings and falls back to the
  old way: a frame when the site allows it, otherwise **Open QuillBot beside the app**, a window on the free half of your screen.
  After updating the app run `./ui.sh down`, then `./ui.sh up`; the first start rebuilds the image (a few minutes).

QuillBot has no public API, so the paste stays yours. **Copy for QuillBot** copies the suggestion with your citations and formulas as tags
(`[[LX001]]`) so QuillBot cannot damage them.

## Working with a coauthor: Overleaf, GitHub and the token

This is the long version of the short section in the [README](../README.md#working-with-a-coauthor-or-sharing-one-overleaf-account). It comes from a real case: a project shared through one Overleaf login, linked to the owner's GitHub
repository, and a coauthor who could only get in through GitHub.

**What was going on.** The coauthor's paper folder had been cloned from GitHub, because Overleaf's own Git address asked him for a password nobody could find. So the folder's address was `github.com`. The app only signs in
to `git.overleaf.com`, so every Test and every *Send* failed, whatever token was saved. The "password" is an Overleaf Git token that did not exist until someone made it.

**The symptoms and what each one means**

| What you see | What it means | What to do |
|---|---|---|
| Test: "This paper's folder was cloned from GitHub" | The folder's address is GitHub's. Sending goes to GitHub, where an Overleaf token is useless | Save the project's web address for the paper (Settings > Overleaf project address), press **Test**, then **Clone it from Overleaf** (share a folder for new copies once: `./ui.sh folders add ~/Documents/overleaf`, then restart). Use the new copy. |
| Test: "a password (or token) written into it" | The folder was cloned with `https://git:TOKEN@git.overleaf.com/...`; Git uses that and never looks at the token in Settings | **Remove it from the address** |
| Git: `Password for 'https://git@git.overleaf.com'` | Normal on a computer that has never signed in. The answer is an Overleaf **Git token**, not a GitHub or Overleaf password | Make a token (Overleaf > Account settings > Git Integration), paste it (nothing shows while you paste) |
| `remote: Enter your Git authentication token when prompted for a password` and `Authentication failed` | Overleaf refused what was sent as the password | Run the curl check below. Often the token was copied wrongly, typed instead of pasted, or `$TOKEN` was empty in that Terminal window |
| Your computer never asks for a password | Your Keychain remembers your token | Nothing; it is not a link to your Git account |
| The coauthor cannot link their GitHub account in Overleaf | The GitHub link belongs to the project and to whoever linked it. It is not needed for Git | Nothing to link. Use a token instead |

**The token check, and how to read it.** In Terminal on the computer that has the problem:

```bash
read -s TOKEN      # paste the token, press Enter; nothing is shown
echo "length: ${#TOKEN}"       # about 40 to 50; 0 means the variable is empty in this window
curl -s -o /dev/null -w "%{http_code}\n" -u "git:$TOKEN" "https://git.overleaf.com/<project id>/info/refs?service=git-upload-pack"
```

200 works. 401 the token is not accepted. 403 or 404 the token is fine but its account cannot open the project (invite it under *Share* by email as Editor, and have the invitation accepted). 500 Overleaf failed
while looking the project up: try again later, and try the token on a blank project that account owns; if that fails too, ask Overleaf support. These numbers are what the Git service usually answers, not something Overleaf documents.

*Account or computer?* Run the same check with the owner's token on the second computer, and the second account's token on the owner's computer. If the owner's token gives 200 on both computers, the second computer is fine and
the problem is the second account's token or access. If it fails on the second computer only, look at that computer's network (VPN, university network, security software; a phone hotspot is a quick test).

**Cloning without the credential prompt** (when typing the password keeps going wrong): send the same login that the curl check used, and leave nothing in the folder's address:

```bash
git -c credential.helper= -c http.extraHeader="Authorization: Basic $(printf 'git:%s' "$TOKEN" | base64 | tr -d '\n')" clone https://git.overleaf.com/<project id> ~/Documents/overleaf/MyPaper
```

**Sharing one Overleaf login.** It works, and it is the simplest way to get a coauthor in. Make **one token per computer** in that login (Account settings > Git Integration), so a lost laptop means deleting one token, not changing the
password. Each person still needs a fresh clone from Overleaf and the token saved in Settings > Overleaf on their own computer. Edits from the two of you meet in Overleaf: press **Get latest from Overleaf** before **Send to Overleaf**.

**Using GitHub as the hub instead.** If Overleaf's Git will not work for one of you, the GitHub copy can carry the changes. The coauthor, a collaborator on the GitHub repository, works in the GitHub copy and pushes with their own
GitHub login (in Terminal). The owner, whose GitHub account is the one linked in Overleaf, presses **Menu > Sync > Pull GitHub changes into Overleaf**, and **Push Overleaf changes to GitHub** to send theirs the other way.
The app does not sign in to GitHub, so from a GitHub folder it can suggest and save text, but **Get latest from Overleaf** and **Send to Overleaf** do not work there. A coauthor in that position can still use the app for the writing help and push with Git.

## Sending, conflicts, review

### Sending to Overleaf, and why it sometimes says "Overleaf has newer changes"

 If you (or a coauthor) edited the project in Overleaf since you last
got the latest, Overleaf refuses a send. Your change is then a *saved change* that never arrived. The header counts these ("1 saved change not
in Overleaf yet"), another **Send** tries again, and **Get the latest from Overleaf, then send again** saves what is waiting, puts Overleaf's
latest underneath your changes, and sends. Changes in different places are joined automatically.

#### When the same lines were changed on both sides

 the app cannot know which words you want, so it stops before sending, undoes its attempt (your
files are exactly as they were) and shows **every colliding place**: what Overleaf has beside what you saved here, the words that differ marked,
and a choice for each: *Use Overleaf's*, *Use mine*, or *Keep both*. **Keep mine everywhere** / **Keep Overleaf's everywhere** choose for all at once.
Nothing happens until every place has a choice; then **Join with my choices and send** makes the merge, commits it, sends it, checks it, and reloads
the Overleaf pane. This replaces only the colliding lines and only in this copy and in Overleaf's newest version: the text you did not choose stays in
Overleaf's History (Overleaf: Menu, History) and in this computer's git history, so a wrong choice can be taken back. Pictures or files that one side
deleted are chosen as a whole file. **Ask Claude Code to join them** leaves a note and gives you one sentence to say in your Claude Code terminal
("Resolve the Overleaf merge conflict I handed you."): Claude Code runs on your computer, not inside the app, so it cannot be called from the page.
It finds the note itself (`overleaf_status` ends with it), joins the versions keeping what fits together, asks you when they contradict, sends, and
the note goes away once Overleaf has the result. After a send the app asks Overleaf what it has and says so ("Checked: Overleaf now has your
change"), reloads the project's Overleaf page in the built-in browser even when its tab is not showing, and presses Recompile there so the PDF follows.
(Overleaf's editor does not follow a send by itself: a page that was open before keeps showing the old text until it is loaded again.)

### Peer review

It needs the `academic-paper` skill installed under `~/.claude/skills/` (`ui.sh` shares its rubric folder read-only).

### Terminal alternatives

`writing.py book-ingest`, `book-rules`, `revise`, `library`, `eval`, `stats`, `doctor`; see `SKILL.md`. Check how well the book is found with `writing.py eval --file examples/book_eval.example.json` (edit the questions for
your book), or use **Book check** in the book library (Settings, Advanced).

## Where things live

| What | Where |
|---|---|
| The code and docs | this repository, wherever you cloned it (for Claude Code: `~/.claude/skills/research-overleaf-wc/`) |
| Your papers list, tokens, credentials | `~/.config/overleaf-skill/` (never in git) |
| Per-paper notes | `<paper>/.overleaf-skill/` (git-ignored, never pushed to Overleaf) |
| Per-paper notes index, paper card, your chats, ChatGPT suggestions, peer reviews, activity log, references waiting for Mendeley | also inside `<paper>/.overleaf-skill/`; one paper's files are never read for another |
| The imported book, the library index, OpenAI key, Overleaf token | `~/.config/overleaf-skill/` (never in git; a book is copyrighted, keep it out of the repo) |

## Safety

- Secrets stay in `~/.config/overleaf-skill/`, outside this repo and outside your papers.
- The bot only obeys chat ids you whitelist.
- Before any action Claude checks the folder still points at the same Overleaf project, and refuses otherwise.
- LaTeX build files (`.aux`, `.log`, `.pdf`, ...) are never pushed to Overleaf by the sync tool (files you add to `references/` are not build files and are sent).
- Peer review only runs when you ask. Mendeley is written to only when you ask Claude to add a reference or send a DOI to the bot.
- The ChatGPT pass only runs when you ask. Each passage is sent with citations, formulas and headings masked, together with the paper's short card and a few book rules; never another paper, never your keys. Asking in the chat is the consent, and "What goes out" shows the exact text; turn on "Ask me before anything is sent" in Settings to confirm every time. Requests above a size you set, and every peer review (which reads the whole paper), always ask first. Answers are checked before you see them (protected LaTeX intact, no new numbers, no dashes, balanced braces, and every LaTeX command written exactly as in your paper: a model that doubles a backslash, `\\S` for `\S`, is corrected and told so, and one that adds or changes other LaTeX is refused) and nothing is written until you approve it. Parts of your own files go out only when a request uses them, and the request lists them. Linked folders cannot be system folders, hidden folders or this app's own settings folder.
- QuillBot is a third party: only what you paste or put there leaves your computer. Your own version is checked against the same rules before it can be saved.
- The built-in browser (QuillBot and Overleaf inside the app) lives in its own container with none of your keys or papers (see above). Your saved QuillBot/Overleaf email and password are in `~/.config/overleaf-skill/sites.json`, readable only by you, not encrypted.
- The web app listens on `127.0.0.1` only (your computer, not the network), needs a fresh access token every time you start it, refuses requests from other websites, loads nothing from the internet, runs as you with a read-only filesystem and no extra privileges, and can see only your registered paper folders, the data folder and the inbox. It closes itself when idle.
- The activity log holds only counts, ids, tokens and timings, never your text.

## Troubleshooting

| Problem | Fix |
|---|---|
| `refusing to act: ... origin changed` | You moved or re-cloned the paper. Run `common.py add <new path> --name <name> --relink`. |
| Push rejected | Someone edited on Overleaf. Ask Claude to pull first, or tap **Pull & push** in Telegram. |
| Bot silent | Is it running? `pgrep -fl doi_bot`. Is your chat id in `allowed_chat_ids`? |
| DOI not found | The bot only accepts DOIs that resolve at doi.org. Preprints without one need Claude's web-search route instead. |
| `ui.sh up` says a port is already in use | Nothing to do: it tells you what holds the port (another container or program) and starts on the next free one, printing the right address. Pick a start port with `OVERLEAF_UI_PORT=9000 ./ui.sh up`. |
| `ui.sh up` says Docker is not running | Start Docker Desktop, wait until it says it is running, try again. |
| The web app does not list a paper | It only sees papers registered when it started: `ui.sh down`, then `ui.sh up`. |
| "This app isn't signed in to Overleaf yet" | Paste your Overleaf Git token in **Settings > Overleaf** (the Docker app cannot use your Mac keychain), then press **Test**. |
| **Test** says the paper "was cloned from GitHub" | The paper's folder came from a GitHub repository (even if that repository is synced with Overleaf), so Pull and Send go to GitHub, where an Overleaf token cannot be used. Save the project's Overleaf web address for the paper (Settings, "Overleaf project address"), press **Test**, then **Clone it from Overleaf**: it makes a copy that comes from Overleaf, in a folder shared with the app (`./ui.sh folders add ~/Documents/overleaf` once), and adds it as a paper. Or keep GitHub and, after Send, use Overleaf's own *Menu > Sync > Pull GitHub changes into Overleaf*. |
| **Test** says the address has "a password (or token) written into it" | The paper was cloned with `https://git:TOKEN@git.overleaf.com/...`. Git uses that and ignores the token saved in Settings, so a new token cannot help. Press **Remove it from the address**. |
| **Test** says "Overleaf received the token and refused it" | The token is wrong, was copied with a character missing (Overleaf shows it once), or was made in an Overleaf account that cannot open this project. Make a new one, paste it, press **Save**, then **Test**. The message includes a Terminal check that tells the token from the app. |
| The book gives odd advice | **Settings > Advanced > Book library > Book check** shows, for each part of a paper, which pages would be used. If the wrong chapter shows up, tell Claude which part and what you expected. A scanned (image-only) PDF needs OCR first. |
| "Approve and save" is missing | The text-cleaning service is not set in Settings, so the button is **Let Claude apply it**. |
| The model was not found / the key was rejected | Use **Test** in **Settings > OpenAI**. It says which one it is, and never shows the key. |
| The pane says the built-in browser is not available | The image is older than the app: run `./ui.sh down`, then `./ui.sh up` (the first start rebuilds the image, a few minutes). **Settings > QuillBot and Overleaf inside the app** says why if it still fails. Meanwhile the sites open in a window beside the app. |
| The pane stays on "Starting the browser" or says the page did not answer | The site could not be reached from the container (internet, a VPN, or the site is down). Press **Reload**, or **Home**. Chromium keeps your sign-in, so nothing is lost. |
| Scrolling in the Overleaf pane lags | Press the **↗** button in the pane header (or the button in the "is slow here" note) to use a window of your own browser, which is always smooth. To keep the built-in one: Docker Desktop > Settings > Resources > more CPUs, and close other heavy tabs. |
| The page keeps asking "Verify you are human", or the sign-in is refused | Click the checkbox in the picture. If it comes back, use **Open QuillBot in a window instead** in the note above the pane (or switch the built-in browser off for that site in **Settings**); your own browser passes these checks. A Google/Apple/university sign-in does not work in an embedded browser: use an email and password. If Overleaf says the email or password is wrong, check them in Settings; the app never sees what you type in the page itself. |
| The picture is small or blurry | The page is drawn wide and shrunk to fit: press **Wider**, or **Aa** for bigger text. |
| The Overleaf or QuillBot picture stops short and leaves an empty strip | It follows the pane's size and proportions by itself within a couple of seconds, also when the window or the browser's zoom changes (a very large pane is drawn smaller in the same proportions, never cut). If it ever stays wrong, press **Reload** in the pane. |
| The buttons at the top of the page slid out of sight | They no longer move: only the conversation, the source and the PDF scroll. If you still see it, reload the page (Cmd/Ctrl + R) and tell me what you pressed. |
| The PDF in the LaTeX pane looks soft | It is drawn as a lossless picture with at least twice as many pixels as the screen shows, so small text stays sharp, also when the browser is zoomed out. After an update, reload the page so the new code is used. |
| "Fill my sign-in" finds no form | Open the site's sign-in page first. Some sites ask for the email, then the password: press the button again after **Continue**. |
| Typing does nothing | Click inside the page first (a blue outline shows it has the keyboard). |
| A reference is "waiting for Mendeley" | The bibliography already has it. Mendeley was unreachable or not signed in; the entry is retried after the next success, or press **Try again now** on the References page. |
| The LaTeX pane says LaTeX is not installed | In Docker the image needs LaTeX: run `./ui.sh down`, then `./ui.sh up` (the first start rebuilds the image with LaTeX, several minutes and about 1 GB more), or `./ui.sh build`. Without Docker, install MacTeX or TeX Live and restart the app. |
| A package is "not found" when compiling | The Docker image has the usual LaTeX packages (publisher classes, maths, figures, biblatex), not every package ever written. Add the `.sty` file to the paper folder, or ask for the package to be added to `ui/Dockerfile`. |
| The browser or LaTeX is slow, even though the app is idle | `./ui.sh up` and `./ui.sh status` say when Docker was given fewer than 4 CPUs or 6 GB of memory: Docker Desktop > Settings > Resources, raise both, Apply. On an Apple-silicon Mac Docker must run the arm64 image, not x86 under emulation (`./ui.sh up` tells you). |
| Mendeley sign-in says `invalid_grant` / "Invalid access code" | The code was old or already used, or the first link was pasted instead of the address the browser ended up on. Run `mendeley_sync.py authorize` again, open the NEW link, approve, and paste the `https://localhost/callback?code=...` address straight away. Also check the redirect URL of your Mendeley app is exactly `https://localhost/callback`. |
| Anything else | `common.py doctor` lists per-paper problems; `writing.py stats` prints what the system has been doing. |

Self-check for the DOI code: `python3 scripts/doi_bot.py demo` (needs internet).
