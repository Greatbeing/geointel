import json
import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import review_draft as rd  # noqa: E402

MEMO = {"question": "Q?", "review_date": "2026-11-20",
        "scenarios": [{"name": "升级", "prob": 60}, {"name": "维持", "prob": 40}],
        "triggers": [{"id": "t1", "keywords": ["export control", "BIS"]}]}
ITEMS = [{"id": "a", "title": "BIS adds entities", "link": "https://x/a", "summary": "s",
          "published": "2026-11-18T00:00:00Z", "source": "Reuters"},
         {"id": "b", "title": "export control talk", "link": "https://x/b", "summary": "s",
          "published": "2026-11-19T00:00:00Z", "source": "FT"}]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.dec = os.path.join(d, "dec")
        os.makedirs(self.dec)
        self.stem = "2026-11-01-chips"
        with open(os.path.join(self.dec, self.stem + ".md"), "w", encoding="utf-8") as f:
            f.write("# memo\n")
        with open(os.path.join(self.dec, self.stem + ".json"), "w", encoding="utf-8") as f:
            json.dump(MEMO, f, ensure_ascii=False)
        self.scores = os.path.join(d, "scores.jsonl")
        rows = [{"memo": self.stem, "review_date": "2026-11-20", "question": "Q?", "outcome": None},
                {"memo": "2026-11-02-later", "review_date": "2027-01-01", "outcome": None},
                {"memo": "2026-10-01-done", "review_date": "2026-11-01", "outcome": {"occurred": "x"}}]
        with open(self.scores, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
        self.out = os.path.join(d, "reviews")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, chat, items=ITEMS, **kw):
        return rd.run(today=date(2026, 11, 24), out_dir=self.out, scores_path=self.scores,
                      decisions_dir=self.dec, search=lambda q, limit, days: items, chat=chat, **kw)


class DraftTest(Base):
    def test_only_due_unreviewed(self):
        res = self._run(lambda *a, **k: '{"occurred":"升级","confidence":"high","reason":"r","evidence":[1]}')
        self.assertEqual([r["memo"] for r in res], [self.stem])
        self.assertEqual(res[0]["verdict"]["occurred"], "升级")
        with open(res[0]["path"], encoding="utf-8") as f:
            text = f.read()
        self.assertIn('--occurred "升级"', text)
        self.assertIn("https://x/a", text)
        self.assertIn("未计分", text)

    def test_scores_untouched(self):
        with open(self.scores, encoding="utf-8") as f:
            before = f.read()
        self._run(lambda *a, **k: '{"occurred":"升级","confidence":"high","reason":"r","evidence":[1]}')
        with open(self.scores, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_no_evidence_becomes_unclear(self):
        res = self._run(lambda *a, **k: '{"occurred":"升级","confidence":"high","reason":"r","evidence":[]}')
        self.assertEqual(res[0]["verdict"]["occurred"], "unclear")

    def test_string_or_inline_evidence_accepted(self):
        res = self._run(lambda *a, **k: '{"occurred":"维持","confidence":"medium","reason":"r","evidence":["[2]"]}')
        self.assertEqual(res[0]["verdict"]["evidence"], [2])
        self.assertEqual(res[0]["verdict"]["occurred"], "维持")

    def test_evidence_from_reason_text(self):
        res = self._run(lambda *a, **k: '{"occurred":"升级","confidence":"medium","reason":"见[1][2]","evidence":[]}', force=True)
        self.assertEqual(res[0]["verdict"]["evidence"], [1, 2])
        self.assertEqual(res[0]["verdict"]["occurred"], "升级")

    def test_unknown_scenario_becomes_unclear(self):
        res = self._run(lambda *a, **k: '{"occurred":"别的","confidence":"low","reason":"r","evidence":[1]}')
        self.assertEqual(res[0]["verdict"]["occurred"], "unclear")

    def test_retry_once_on_unparseable(self):
        calls = []
        def flaky(prompt, **k):
            calls.append(prompt)
            return "抱歉" if len(calls) == 1 else '{"occurred":"升级","confidence":"low","reason":"r","evidence":[1]}'
        res = self._run(flaky)
        self.assertEqual(len(calls), 2)
        self.assertIn("只输出一个 JSON", calls[1])
        self.assertEqual(res[0]["verdict"]["occurred"], "升级")

    def test_gives_up_after_two(self):
        calls = []
        res = self._run(lambda p, **k: calls.append(p) or "nope")
        self.assertEqual(len(calls), 2)
        self.assertEqual(res[0]["error"], "LLM 输出无法解析")

    def test_llm_down_still_writes_draft(self):
        def boom(*a, **k):
            raise TimeoutError("gw")
        res = self._run(boom)
        self.assertIsNone(res[0]["verdict"])
        self.assertTrue(os.path.exists(res[0]["path"]))
        self.assertIn("LLM 不可用", rd.format_message(res))

    def test_no_items_no_llm_call(self):
        def never(*a, **k):
            raise AssertionError("should not call")
        res = self._run(never, items=[])
        self.assertEqual(res[0]["verdict"]["occurred"], "unclear")

    def test_existing_draft_skipped_and_not_resent(self):
        ok = lambda *a, **k: '{"occurred":"none","confidence":"medium","reason":"r","evidence":[2]}'
        first = self._run(ok)
        self.assertTrue(rd.format_message(first))
        second = self._run(ok)
        self.assertTrue(second[0]["skipped"])
        self.assertEqual(rd.format_message(second), "")

    def test_ahead_includes_upcoming(self):
        res = rd.run(today=date(2026, 12, 30), ahead=3, out_dir=self.out, scores_path=self.scores,
                     decisions_dir=self.dec, search=lambda q, limit, days: [], chat=None)
        self.assertIn(self.stem, [r["memo"] for r in res])


if __name__ == "__main__":
    unittest.main()
