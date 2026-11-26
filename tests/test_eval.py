import json
import sqlite3
import unittest
from pathlib import Path

from evals.run_eval import is_ordered, results_match
from scripts.seed_demo_db import build
import tempfile

DB = build(Path(tempfile.mkdtemp()) / "sales.db")
QUESTIONS = [json.loads(l) for l in (Path(__file__).parents[1] / "evals" / "questions.jsonl").read_text().splitlines()]


class ComparatorTests(unittest.TestCase):
    def test_extra_columns_and_aliases_allowed(self):
        self.assertTrue(results_match([("Delhi", 10)], [(10, "delhi", "extra")], ordered=False))

    def test_row_order_only_matters_when_ordered(self):
        gold, pred = [("a", 2), ("b", 1)], [("b", 1), ("a", 2)]
        self.assertTrue(results_match(gold, pred, ordered=False))
        self.assertFalse(results_match(gold, pred, ordered=True))

    def test_numbers_compared_after_rounding(self):
        self.assertTrue(results_match([(1234.5678,)], [(1234.57,)], ordered=False))
        self.assertFalse(results_match([(10,)], [(11,)], ordered=False))

    def test_row_count_must_match(self):
        self.assertFalse(results_match([(1,), (2,)], [(1,)], ordered=False))

    def test_is_ordered(self):
        self.assertTrue(is_ordered("SELECT a FROM t ORDER BY b DESC LIMIT 5"))
        self.assertFalse(is_ordered("SELECT a, COUNT(*) FROM t GROUP BY a"))


class GoldSetTests(unittest.TestCase):
    def test_every_gold_query_runs_and_matches_itself(self):
        self.assertEqual(len(QUESTIONS), 40)
        conn = sqlite3.connect(DB)
        for q in QUESTIONS:
            with self.subTest(q=q["id"]):
                rows = conn.execute(q["gold_sql"]).fetchall()
                self.assertTrue(rows)
                self.assertTrue(results_match(rows, rows, is_ordered(q["gold_sql"])))


if __name__ == "__main__":
    unittest.main()
