import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.photo_acquisition import _enforce_ladder_coherence, classify_relevance, _is_relevant  # noqa: E402


class TestRelevanceClassification(unittest.TestCase):
    def test_exact_subject_match(self):
        c = {"title": "Fertility clinic exterior sign"}
        self.assertEqual(classify_relevance(c, "fertility clinic", "fertility clinic room"), "EXACT_SUBJECT")

    def test_contextually_relevant_via_query_vocabulary(self):
        # Title doesn't restate the subject noun phrase but does engage
        # with the query's own non-generic environment vocabulary.
        c = {"title": "Modern treatment room interior with equipment"}
        verdict = classify_relevance(c, "fertility clinic", "fertility clinic treatment room interior")
        self.assertEqual(verdict, "CONTEXTUALLY_RELEVANT")
        self.assertTrue(_is_relevant(c, "fertility clinic", "fertility clinic treatment room interior"))

    def test_generic_only_words_rejected(self):
        c = {"title": "Person using smartphone at office desk"}
        verdict = classify_relevance(c, "fertility clinic", "fertility clinic treatment room interior")
        self.assertIn(verdict, ("GENERIC", "IRRELEVANT"))
        self.assertFalse(_is_relevant(c, "fertility clinic", "fertility clinic treatment room interior"))

    def test_irrelevant_archival_result_rejected(self):
        # Real candidate observed in a live Golden Test trace -- confirms
        # widening relevance to CONTEXTUALLY_RELEVANT did not also open the
        # door to genuinely unrelated archival content.
        c = {"title": "jubilee of the translation of St. Demetrius relics to Bucharest"}
        self.assertFalse(_is_relevant(c, "fertility treatment clinic", "calendar with days marked for leave"))

    def test_empty_subject_never_passes(self):
        self.assertFalse(_is_relevant({"title": "anything at all here"}, "", "some query"))


class TestQueryLadderCoherence(unittest.TestCase):
    def test_drops_concept_that_shares_no_word_with_anchor(self):
        # Real 3-concept ladder observed from a live Tier-2 CLI call: the
        # first two concepts stay anchored to the same real subject, the
        # third individually passes the generic-word filter but shares
        # nothing with the page's actual distinctive subject.
        concepts = [
            ("fertility treatment clinic", "fertility treatment clinic consultation room"),
            ("fertility clinic", "woman undergoing IVF fertility treatment"),
            ("calendar", "calendar with days marked for leave"),
        ]
        kept = _enforce_ladder_coherence(concepts)
        self.assertEqual(len(kept), 2)
        self.assertNotIn(("calendar", "calendar with days marked for leave"), kept)

    def test_keeps_all_when_coherent(self):
        concepts = [
            ("small shop owner", "small shop owner business"),
            ("small shop owner", "shop owner counting receipts"),
        ]
        self.assertEqual(_enforce_ladder_coherence(concepts), concepts)

    def test_single_concept_untouched(self):
        concepts = [("housing documents", "housing rental documents keys")]
        self.assertEqual(_enforce_ladder_coherence(concepts), concepts)

    def test_empty_list_untouched(self):
        self.assertEqual(_enforce_ladder_coherence([]), [])


if __name__ == "__main__":
    unittest.main()
