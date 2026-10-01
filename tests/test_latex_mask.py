import unittest

from writing_core import latex_mask as lm

SRC = r"""\subsection{Participants}
We sampled 40 farms~\cite[p.~3]{smith2020,lee2019} (see Table~\ref{tab:a}) with $d=0.8$ and 12\% attrition. % TODO fix this
\begin{equation}
y = x + 1 \label{eq:1}
\end{equation}
See \href{https://example.org}{the site} and \url{https://x.y}. Costs were \$5 and \$7.
Informed consent was obtained."""


def names(results, ok=False):
    return sorted(r["name"] for r in results if r["ok"] == ok)


def fails(original, revised):
    return {r["name"] for r in lm.validate(original, revised) if not r["ok"]}


class Masking(unittest.TestCase):
    def test_roundtrip_and_what_is_protected(self):
        m, mp = lm.mask(SRC)
        self.assertEqual(lm.unmask(m, mp), SRC)
        for bad in ("\\cite", "\\ref", "$d=0.8$", "\\begin{equation}", "\\href", "\\url", "TODO", "\\subsection"):
            self.assertNotIn(bad, m)
        self.assertIn("12\\% attrition", m)  # escaped percent is prose, not a comment
        self.assertIn("\\$5 and \\$7", m)    # escaped dollars are not math
        self.assertIn("Informed consent was obtained.", m)
        self.assertEqual(len(mp), len(lm.placeholders(m)))

    def test_placeholders_are_numbered_in_reading_order_and_unique(self):
        m, mp = lm.mask(SRC)
        phs = lm.placeholders(m)
        self.assertEqual(phs, sorted(phs))
        self.assertEqual(len(phs), len(set(phs)))

    def test_adjacent_and_nested_protection(self):
        m, mp = lm.mask(r"\begin{figure}\includegraphics{a}\caption{Cap with $x$}\end{figure} text \cite{a}\cite{b}")
        self.assertEqual(len(mp), 3)  # the whole figure is ONE placeholder (math inside is covered by it)
        self.assertTrue(m.strip().endswith("]]"))

    def test_unbalanced_or_odd_input_does_not_crash(self):
        for s in ("", "plain words only", r"\cite{unclosed", "stray $ dollar", r"\section{", "50% of it\n% only a comment"):
            m, mp = lm.mask(s)
            self.assertEqual(lm.unmask(m, mp), s)

    def test_existing_placeholder_lookalikes_survive(self):
        s = "literal [[LX001]] in the source"
        m, mp = lm.mask(s)
        self.assertEqual(lm.unmask(m, mp), s)


class Validators(unittest.TestCase):
    def setUp(self):
        self.o, _ = lm.mask(SRC)

    def rev(self, a, b):
        return self.o.replace(a, b)

    def test_clean_edit_passes_everything(self):
        r = self.rev("We sampled", "We recruited")
        self.assertEqual(fails(self.o, r), set())

    def test_placeholders_dropped_or_duplicated(self):
        self.assertIn("placeholders_intact", fails(self.o, self.o.replace("[[LX002]]", "")))
        self.assertIn("placeholders_intact", fails(self.o, self.o + " [[LX002]]"))
        self.assertIn("placeholders_intact", fails(self.o, self.o + " [[LX099]]"))

    def test_placeholder_reorder_is_only_a_warning(self):
        r = self.o.replace("[[LX002]]", "[[TMP]]").replace("[[LX003]]", "[[LX002]]").replace("[[TMP]]", "[[LX003]]")
        res = lm.validate(self.o, r)
        self.assertEqual(lm.errors(res), [])
        self.assertIn("placeholder_order", names(res))

    def test_numbers(self):
        self.assertIn("no_new_numbers", fails(self.o, self.rev("40 farms", "45 farms")))
        self.assertIn("no_new_numbers", fails(self.o, self.rev("40 farms", "40 farms and 9 fields")))
        self.assertNotIn("no_new_numbers", fails(self.o, self.rev("40 farms", "forty farms")))   # words <-> digits are the same number
        self.assertNotIn("no_new_numbers", fails("We saw 1,200 cases.", "We saw 1200 cases overall."))
        self.assertIn("no_new_numbers", fails("We saw 12 cases.", "We saw twelve of 13 cases."))
        self.assertNotIn("no_new_numbers", fails("We tested twenty-one sites.", "We tested 21 sites."))
        self.assertNotIn("no_new_numbers", fails("One of them left.", "Only one of them left."))  # "one" is not a quantity here

    def test_dashes_new_ones_fail_old_ones_are_tolerated(self):
        for d in ("—", "–", "---", "--"):
            self.assertIn("no_new_dashes", fails(self.o, self.rev("farms", f"farms {d} all of them")), d)
        had = "We saw it — twice."
        self.assertNotIn("no_new_dashes", fails(had, "We saw it — two times."))
        self.assertIn("no_new_dashes", fails(had, "We saw it — two times — clearly."))

    def test_braces_environments_commands_and_specials(self):
        self.assertIn("braces_and_environments_balanced", fails(self.o, self.rev("farms", "farms {")))
        self.assertIn("braces_and_environments_balanced", fails("Text \\begin{itemize} x \\end{itemize}", "Text \\begin{itemize} x"))
        self.assertIn("no_new_commands", fails(self.o, self.rev("farms", "\\textbf{farms}")))
        self.assertNotIn("no_new_commands", fails("A \\emph{b} c", "A c"))  # dropping a command is allowed
        self.assertIn("no_new_special_characters", fails(self.o, self.rev("farms", "farms & fields")))
        self.assertIn("no_new_special_characters", fails(self.o, self.rev("farms", "farms 50% of")))
        self.assertNotIn("no_new_special_characters", fails(self.o, self.rev("farms", "farms 5\\% of")))

    def test_length(self):
        long = " ".join(["word%d" % i for i in range(60)])
        self.assertIn("length_within_bounds", fails(long, "word1 word2 word3"))
        self.assertIn("length_within_bounds", fails(long, long + " " + long))
        self.assertEqual(fails(long, long.replace("word1", "term")), set())

    def test_acronyms_error_names_warn(self):
        self.assertIn("no_new_acronyms", fails(self.o, self.rev("farms", "farms using ANOVA")))
        self.assertNotIn("no_new_acronyms", fails("We used ANOVA here.", "Here we used ANOVA."))
        res = lm.validate(self.o, self.rev("farms", "farms near Cordoba"))
        self.assertEqual(lm.errors(res), [])
        self.assertIn("no_new_named_entities", names(res))
        self.assertNotIn("no_new_named_entities", fails("See it.", "See it in Figure shown."))  # Figure/Table/Section are whitelisted

    def test_summary_text_names_the_problem(self):
        res = lm.validate(self.o, self.rev("40 farms", "45 farms"))
        self.assertIn("45", lm.summarize(res))


class Hunks(unittest.TestCase):
    A = "First sentence is here. Second sentence is also here. Third stays the same."

    def test_identical_text_has_no_hunks(self):
        segs = lm.segments(self.A, self.A)
        self.assertEqual([s["type"] for s in segs], ["eq"])

    def test_sentence_rewrites_are_separate_hunks_and_subsets_work(self):
        b = "First sentence is now there. Second sentence is also elsewhere. Third stays the same."
        segs = lm.segments(self.A, b)
        ids = [s["id"] for s in segs if s["type"] == "hunk"]
        self.assertEqual(ids, ["h1", "h2"])
        self.assertEqual(lm.assemble(segs, set()), self.A)
        self.assertEqual(lm.assemble(segs, set(ids)), b)
        self.assertEqual(lm.assemble(segs, {"h1"}), "First sentence is now there. Second sentence is also here. Third stays the same.")

    def test_pure_insert_and_delete(self):
        segs = lm.segments(self.A, self.A + " Fourth is new.")
        self.assertEqual(lm.assemble(segs, {"h1"}), self.A + " Fourth is new.")
        segs = lm.segments(self.A, "First sentence is here. Third stays the same.")
        self.assertEqual(lm.assemble(segs, {"h1"}), "First sentence is here. Third stays the same.")

    def test_a_citation_that_moves_between_sentences_links_the_hunks(self):
        a = "Water matters [[LX001]]. Yield rises. Nothing else changes."
        b = "Water matters. Yield rises clearly [[LX001]]. Nothing else changes."
        segs = lm.segments(a, b)
        hunks = [s for s in segs if s["type"] == "hunk"]
        self.assertEqual(len(hunks), 1)  # merged: accepting only half would lose or duplicate the citation
        for subset in (set(), {"h1"}):
            self.assertEqual(lm.errors(lm.validate(a, lm.assemble(segs, subset))), [])

    def test_every_subset_of_hunks_keeps_placeholders_intact(self):
        a = "One [[LX001]]. Two [[LX002]]. Three [[LX003]]. Four."
        b = "One changed [[LX001]]. Two [[LX002]] and more. Three. Four [[LX003]]."
        segs = lm.segments(a, b)
        ids = [s["id"] for s in segs if s["type"] == "hunk"]
        import itertools
        for r in range(len(ids) + 1):
            for sub in itertools.combinations(ids, r):
                self.assertEqual(lm.errors(lm.validate(a, lm.assemble(segs, set(sub)))), [], sub)

    def test_word_diff(self):
        d = lm.word_diff("the cat sat", "the dog sat")
        self.assertIn(["-", "cat"], d)
        self.assertIn(["+", "dog"], d)
        self.assertEqual("".join(t for op, t in d if op != "+"), "the cat sat")
        self.assertEqual("".join(t for op, t in d if op != "-"), "the dog sat")

    def test_reasons_attach_to_the_closest_hunk(self):
        hunks = [{"id": "h1", "old": "The farms were sampled randomly.", "new": "We sampled farms at random."},
                 {"id": "h2", "old": "Consent was obtained.", "new": "All farmers gave consent."}]
        lm.attach_reasons(hunks, [{"before": "Consent was obtained", "after": "All farmers gave consent", "reason": "active voice", "rule": "B:x:c1"},
                                  {"before": "zzz", "after": "qqq", "reason": "unrelated", "rule": ""}])
        self.assertEqual(hunks[1]["reasons"], ["active voice"])
        self.assertEqual(hunks[1]["rules"], ["B:x:c1"])
        self.assertEqual(hunks[0]["reasons"], [])


if __name__ == "__main__":
    unittest.main()
