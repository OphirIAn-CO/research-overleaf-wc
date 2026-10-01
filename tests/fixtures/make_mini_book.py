#!/usr/bin/env python3
"""Regenerates tests/fixtures/mini_book.pdf, a small ORIGINAL text-PDF stand-in for a writing handbook.

It has what a real book has and our chunker must cope with: front matter (title page + contents with dotted leaders),
printed page numbers offset from PDF page numbers, running headers/footers, several headings on one page,
paragraphs that continue across a page break, and an index to be skipped.

Needs a Chromium binary (Playwright's is used here). Run manually; the PDF is committed so tests do not need Chromium.
  python3 tests/fixtures/make_mini_book.py [/path/to/chrome]
"""
import html
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME = sys.argv[1] if len(sys.argv) > 1 else "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
OUT = Path(__file__).with_name("mini_book.pdf")
BOOK = "A Short Guide to Writing Research Papers"
FRONT = 2  # title page + contents; printed page 1 is PDF page 3

# Each page is a list of blocks: ("h1", "1", title) | ("h2", "1.1", title) | ("p", text)
PAGES = [
    [("h1", "1", "Writing the Introduction"),
     ("p", "The introduction has one job: to convince a reader that the question is worth answering and that this paper answers it. Everything else is detail. A good introduction is short, usually four to six paragraphs, and every paragraph moves the reader one step closer to the research question."),
     ("h2", "1.1", "Opening with the problem"),
     ("p", "Open with the problem, not with the history of the field. A reader who meets a concrete problem in the first two sentences knows why to keep reading. Avoid sweeping openers such as 'since the dawn of time' or 'in today's fast-paced world', which promise nothing and delay the point. Name the problem, say who is affected by it, and give one number or example that shows its size."),
     ("p", "Keep the first paragraph free of citations that only decorate. Cite when a claim could be doubted, and place the citation as close to the claim as the sentence allows. If the problem is well known to your readers, say so in one clause and move on to what is still unknown, because the reader of a journal article")],
    [("p", "already knows the background and is waiting for the contribution."),
     ("h2", "1.2", "Stating the gap"),
     ("p", "A gap is a specific thing that earlier work did not do. It is never simply that little research exists. Say what earlier studies established, say what they left open, and say why the open part matters. The introduction then follows a funnel: from what is known, to what is missing, to what this paper contributes."),
     ("p", "State the research question or objective in one sentence, in the same words you will later use in the methods and the conclusion. If the paper tests hypotheses, list them here in plain language. End the introduction with a roadmap sentence only if the journal expects one; otherwise let the headings carry the structure. The gap statement is the sentence most reviewers quote back to authors, so make it precise, make it honest, and make it match the results.")],
    [("p", "A final test for the introduction: remove the last paragraph and ask whether the reader still knows what the paper will do. If the answer is no, the contribution is hidden, and it should move up. Contribution statements should be concrete, for example naming the new dataset, the new method, or the new comparison, rather than saying that the paper sheds light on a topic.")],
    [("h1", "2", "Writing the Methods"),
     ("p", "The methods section exists so that another researcher could repeat the work. Write for that reader. Detail serves replication; detail that does not help someone repeat or judge the study should be moved to a supplement. Use the past tense for what you did and keep the order of the text equal to the order of the work."),
     ("h2", "2.1", "Describing participants and sampling"),
     ("p", "State who or what was studied, how many, and how they were chosen. Give the sampling frame, the sampling method (random sampling, stratified sampling, convenience sample), the inclusion and exclusion criteria, and the response rate. Report where and when the data were collected. For studies with people, state that informed consent was obtained and name the ethics committee that approved the protocol, with the approval number.")],
    [("p", "Describe the sample in numbers the reader can use: sample size, age range, and any characteristic that bears on the research question. Explain how the sample size was decided, for example through a power analysis, and say what happened to participants who dropped out. If the sample is a convenience sample, say so plainly and return to the consequences in the limitations."),
     ("h2", "2.2", "Describing procedures"),
     ("p", "Describe the procedure in the order in which it happened. Name the materials, the apparatus and the instruments with their source and version, so that the same items can be obtained. Explain pilot testing, random assignment of participants to conditions, and blinding, and say who knew what and when. A reader should be able to follow the procedure like a recipe without guessing a step.")],
    [("p", "Separate what you did from why you did it. One sentence of rationale per major choice is enough. Standard techniques need only a citation, while new or modified techniques need a full description. If a step failed or was changed during the study, report the change, because hiding it makes the work harder to trust and harder to repeat."),
     ("h2", "2.3", "Reporting the statistical analysis"),
     ("p", "Name each statistical test and the software and version used. State the significance threshold, and say that it was chosen before seeing the data. Explain how missing data were handled, which model assumptions were checked, and how outliers were treated. For regression models, list the outcome, the predictors, and the way categorical predictors were coded. Report effect sizes and confidence intervals, not only p values, because a")],
    [("p", "p value alone says nothing about how large or how important an effect is. When many tests are run, say how the multiple comparisons were handled. Distinguish the analyses planned in advance from those added after seeing the results, and label the second kind as exploratory. Give the analysis code or a link to it when the journal allows, so that the analysis can be repeated exactly.")],
    [("h1", "3", "Reporting Results"),
     ("p", "The results section reports what was found and nothing more. Interpretation belongs in the discussion. Follow the order of the research questions so that the reader can match each question with its answer."),
     ("h2", "3.1", "Presenting numbers"),
     ("p", "Report the result first and the interpretation later. Give exact values with a sensible precision, always with units, and put the uncertainty beside the estimate, for example a mean with its confidence interval. Do not repeat in the text every number that is in a table; point to the table and state the pattern that the numbers show. Use the same number of decimals for values that are compared."),
     ("h2", "3.2", "Using tables and figures"),
     ("p", "A table holds many exact numbers; a figure shows a pattern. Choose the one that serves the point. Every table and figure needs a caption that explains it without the text, labelled axes with units, and a mention in the text before it appears. Number them in the order they are cited. Avoid chart decoration, three dimensional effects, and colors that cannot be told apart in grayscale.")],
    [("p", "Check each figure at the size at which it will be printed. Lines that look fine on a screen can vanish on paper, and a legend in a tiny font defeats the purpose of the figure. Report the sample size behind every plotted value, and say what the error bars show, whether the standard deviation, the standard error, or a confidence interval.")],
    [("h1", "4", "Writing the Discussion"),
     ("p", "The discussion answers the question the introduction asked. It should read as a direct reply and not as a second results section."),
     ("h2", "4.1", "Interpreting findings"),
     ("p", "Start with the main finding in one sentence. Then explain it: what mechanism or reasoning makes sense of the result, and how does it compare with earlier work? Separate what the data show from what you believe they suggest. Use cautious verbs such as suggests or is consistent with when the design cannot prove a cause, and use plain verbs when the evidence is strong."),
     ("h2", "4.2", "Stating limitations"),
     ("p", "Name the real limitations plainly: the sample, the design, the measurement, the setting. Do not apologize and do not list trivial weaknesses to look modest. For each limitation, explain the likely direction of the bias and what future work could do about it. A limitations paragraph that is honest and specific increases the trust of the reader and gives reviewers less to find.")],
    [("p", "Close the discussion with the implications for practice or theory and a short statement of what should be done next. The conclusion then repeats the answer to the research question in a few sentences, without new data and without new citations, so that a reader who only reads the conclusion still learns what the paper found.")],
    [("h1", "5", "Clear Sentences and Paragraphs"),
     ("p", "Clear writing is the result of revision, not of talent. Most unclear sentences hide their actor, bury their verb, or carry more than one idea."),
     ("h2", "5.1", "Active and passive voice"),
     ("p", "Use the active voice when the actor matters: 'we measured the pressure' is shorter and clearer than 'the pressure was measured by us'. Use the passive voice when the actor is unknown or irrelevant, which is common in methods, as in 'samples were centrifuged for ten minutes'. Do not switch voice inside a sentence without a reason."),
     ("h2", "5.2", "Tense in scientific writing"),
     ("p", "Use the past tense for what you did and what you found. Use the present tense for established knowledge and for statements about the paper itself, such as 'Table 2 shows the results' or 'this section describes the sample'. Mixing tenses inside one paragraph confuses the reader about what is old knowledge and what is new evidence.")],
    [("p", "A useful check: highlight every verb in a paragraph and read only the highlighted words. The tense should change only when the time of the thing described changes."),
     ("h2", "5.3", "Cutting wordiness"),
     ("p", "Delete empty phrases such as 'it is important to note that' and 'in order to'. Replace nominalizations with verbs: 'make an assessment of' becomes 'assess', and 'perform an analysis of' becomes 'analyze'. Prefer the short word to the long one, the concrete word to the abstract one, and one clear sentence to two vague ones. Read the paragraph aloud; wherever you stumble, rewrite.")],
    [("h1", "6", "Abstracts and Titles"),
     ("p", "Abstracts and titles are read by many more people than the paper itself, so they deserve the most careful revision."),
     ("h2", "6.1", "Writing the abstract"),
     ("p", "An abstract is a stand-alone summary. Give the background in one sentence, then the aim, the method, the main results with numbers, and the conclusion. Most journals allow 150 to 250 words. Write it last, from the finished paper, and check that every claim in the abstract appears in the results. Do not cite references in the abstract and do not introduce abbreviations that are not repeated."),
     ("h2", "6.2", "Choosing a title"),
     ("p", "A good title names the topic and, when possible, the finding. Avoid empty openings such as 'a study of' or 'towards an understanding of'. Avoid abbreviations and jargon that the target readers may not know. Try the title on a colleague outside the field and ask what they think the paper is about.")],
    [("h1", "Index", ""),
     ("p", "abstract, 14. active voice, 12. confidence interval, 7. consent, 4. effect size, 6. gap, 2. limitations, 10. methods, 4. participants, 4. passive voice, 12. p value, 6. sampling, 4. tables, 9. tense, 12. title, 14. wordiness, 13.")],
]

TOC_EXTRA = [("Index", "", "Index")]


def build_html():
    entries = []  # (level, number, title, pdf_page)
    body = []
    for i, blocks in enumerate(PAGES):
        pdf_page = FRONT + 1 + i
        printed = pdf_page - FRONT
        parts = [f'<div class="hdr">{html.escape(BOOK)}</div>']
        for b in blocks:
            if b[0] in ("h1", "h2"):
                num, title = b[1], b[2]
                label = f"{num}&nbsp;&nbsp;{html.escape(title)}" if title and num != "Index" else html.escape(num or title)
                parts.append(f'<{b[0]}>{label}</{b[0]}>')
                entries.append((1 if b[0] == "h1" else 2, num, title or num, printed))
            else:
                parts.append(f"<p>{html.escape(b[1])}</p>")
        parts.append(f'<div class="ftr">{printed}</div>')
        body.append('<section class="page">' + "".join(parts) + "</section>")

    toc_rows = []
    for level, num, title, printed in entries:
        text = f"{num}&nbsp;&nbsp;{html.escape(title)}" if num and num != "Index" else html.escape(title)
        toc_rows.append(f'<div class="toc l{level}"><span>{text}</span><span class="dots">{". " * 160}</span><span>{printed}</span></div>')
    front = (f'<section class="page title"><div class="big">{html.escape(BOOK)}</div><div>Test fixture, original text</div></section>'
             f'<section class="page"><h1 class="contents">Contents</h1>{"".join(toc_rows)}</section>')
    css = """
    @page { size: 8.5in 11in; margin: 0 }
    body { margin: 0; font-family: 'Times New Roman', Times, serif; font-size: 11pt; line-height: 1.45 }
    .page { width: 8.5in; height: 11in; box-sizing: border-box; padding: 1in; position: relative; page-break-after: always; overflow: hidden }
    .hdr, .ftr { position: absolute; left: 1in; right: 1in; font-size: 9pt; text-align: center; color: #333 }
    .hdr { top: .5in } .ftr { bottom: .5in }
    h1 { font-size: 20pt; margin: .3in 0 .15in } h2 { font-size: 13pt; margin: .22in 0 .08in } p { margin: 0 0 .12in; text-align: justify }
    .title { text-align: center; padding-top: 3.5in } .big { font-size: 26pt; font-weight: bold; margin-bottom: .4in }
    .toc { display: flex; margin: 3px 0; white-space: nowrap } .toc .dots { flex: 1; overflow: hidden; margin: 0 6px; letter-spacing: 1px }
    .toc.l2 { padding-left: .3in } .toc.l1 { font-weight: bold; margin-top: 8px } .contents { margin-top: 0 }
    """
    return f"<!doctype html><html lang='en'><meta charset='utf-8'><style>{css}</style><body>{front}{''.join(body)}</body></html>"


def main():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "book.html"
        src.write_text(build_html(), encoding="utf-8")
        subprocess.run([CHROME, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={OUT}", src.as_uri()], check=True, capture_output=True)
    print("wrote", OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
