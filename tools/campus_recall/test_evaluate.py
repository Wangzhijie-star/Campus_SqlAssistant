import unittest

from evaluate import HERE, load_fixture, lexical_preview, score, summarize


class MetricsTest(unittest.TestCase):
    def test_partial_recall_and_extra_term(self):
        result = score(["曾不及格", "尚不及格"], ["曾不及格", "GPA", "GPA"])
        self.assertEqual(result["recall"], 0.5)
        self.assertEqual(result["precision"], 0.5)
        self.assertFalse(result["all_hit"])
        self.assertEqual(result["missing"], ["尚不及格"])
        self.assertEqual(result["extra"], ["GPA"])

    def test_negatives_do_not_inflate_positive_recall(self):
        rows = [score(["GPA"], []), score([], []), score([], ["GPA"])]
        result = summarize(rows)
        self.assertEqual(result["recall"], 0)
        self.assertEqual(result["false_positive"], 0.5)
        self.assertIsNone(summarize([rows[0]])["false_positive"])

    def test_fixture_contains_real_semantic_gaps(self):
        data = load_fixture(HERE / "fixture.json")
        rows = lexical_preview(data)
        semantic = [r for r in rows if r["category"] == "semantic"]
        self.assertTrue(semantic)
        self.assertTrue(all(not r["actual"] for r in semantic))
        self.assertTrue(all(r["exact_match"] for r in rows if r["category"] in {"direct", "alias", "multi"}))


if __name__ == "__main__":
    unittest.main()
