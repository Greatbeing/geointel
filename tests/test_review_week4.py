#!/usr/bin/env python3
"""Unit tests for Week 4: review.py auto-review + calibration page,
weekly_summary.py weekly digest + Feishu webhook push.
Stdlib only, no network — fixtures are inline, webhook is mocked.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import review  # noqa: E402
import weekly_summary  # noqa: E402

MEMO_JSON = {
    "question": "未来 90 天测试问题的走向",
    "generated_at": "2026-11-21T09:00:00+08:00",
    "horizon_days": 90,
    "items_used": 12,
    "scenarios": [
        {"name": "情景甲", "prob": 45},
        {"name": "情景乙", "prob": 35},
        {"name": "情景丙", "prob": 20},
    ],
    "triggers": [
        {"id": "t1", "description": "测试触发条件，数值超过 5 就触发",
         "keywords": ["测试"], "threshold": 5, "type": "value", "action": "复核"},
    ],
    "confidence": "medium",
    "review_date": "2027-01-15",
}


def write_memo(directory, stem, data=None, body_extra=""):
    data = data or MEMO_JSON
    md = (
        "---\n"
        f"question: {data['question']}\n"
        f"generated_at: {data['generated_at']}\n"
        f"horizon_days: {data['horizon_days']}\n"
        f"items_used: {data['items_used']}\n"
        "---\n\n"
        "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n\n"
        "## 1. 问题与边界\n\n最晚决策点：2027-01-10。\n" + body_extra
    )
    md_path = os.path.join(directory, stem + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(directory, stem + ".json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return md_path


def row(memo, review_date, structural=90, outcome=None):
    return {
        "memo": memo,
        "slug": memo[11:],
        "memo_date": memo[:10],
        "question": "问题 " + memo,
        "generated_at": "2026-11-21T09:00:00+08:00",
        "review_date": review_date,
        "structural": {"score": structural, "deductions": []},
        "outcome": outcome,
        "scored_at": "2026-11-21T10:00:00",
    }


class TestDueAndUpcoming(unittest.TestCase):
    def setUp(self):
        self.rows = [
            row("2026-11-21-aaa", "2027-01-15"),
            row("2026-11-21-bbb", "2027-02-18"),
            row("2026-11-20-ccc", "2027-01-15",
                outcome={"occurred": "情景甲", "brier": 0.1,
                         "calibration": 95.0, "top_hit": True,
                         "coverage_failure": False}),
        ]
        self.today = date(2027, 1, 20)

    def test_due_only_unreviewed_and_past(self):
        due = review.due_reviews(self.rows, self.today)
        self.assertEqual([r["memo"] for r in due], ["2026-11-21-aaa"])

    def test_upcoming_within_window(self):
        up = review.upcoming_reviews(self.rows, date(2027, 2, 10), days=14)
        self.assertEqual([r["memo"] for r in up], ["2026-11-21-bbb"])

    def test_due_empty_before_any_review_date(self):
        self.assertEqual(review.due_reviews(self.rows, date(2026, 12, 1)), [])


class TestCalibrationStats(unittest.TestCase):
    def test_no_reviewed(self):
        s = review.calibration_stats([row("2026-11-21-aaa", "2027-01-15")])
        self.assertEqual(s["n_reviewed"], 0)
        self.assertIsNone(s["mean_brier"])
        self.assertEqual(s["mean_structural"], 90)

    def test_reviewed_means(self):
        rows = [
            row("2026-11-21-aaa", "2027-01-15", structural=90,
                outcome={"occurred": "情景乙", "brier": 0.2217,
                         "calibration": 88.9, "top_hit": False,
                         "coverage_failure": False}),
            row("2026-11-21-bbb", "2027-01-15", structural=80,
                outcome={"occurred": "none", "brier": 0.1217,
                         "calibration": 93.9, "top_hit": False,
                         "coverage_failure": True}),
        ]
        s = review.calibration_stats(rows)
        self.assertEqual(s["n_reviewed"], 2)
        self.assertAlmostEqual(s["mean_brier"], 0.1717)
        self.assertAlmostEqual(s["mean_calibration"], 91.4)
        self.assertEqual(s["top_hit_rate"], 0.0)
        self.assertEqual(s["coverage_failures"], 1)
        self.assertEqual(s["mean_structural"], 85.0)


class TestReviewMemo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.decisions = self.tmp.name
        self.scores = os.path.join(self.tmp.name, "scores.jsonl")
        self.memo_path = write_memo(self.decisions, "2026-11-21-test-q")

    def tearDown(self):
        self.tmp.cleanup()

    def test_outcome_second_scenario(self):
        # Brier = ((0.45)^2 + (0.35-1)^2 + (0.20)^2)/3 = 0.2217
        m, outcome = review.review_memo(
            self.memo_path, "情景乙", self.scores, today=date(2027, 1, 16))
        self.assertEqual(outcome["occurred"], "情景乙")
        self.assertAlmostEqual(outcome["brier"], 0.2217)
        self.assertAlmostEqual(outcome["calibration"], 88.9)
        self.assertFalse(outcome["top_hit"])
        self.assertFalse(outcome["coverage_failure"])
        rows = review.load_rows(self.scores)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["memo"], "2026-11-21-test-q")
        self.assertEqual(rows[0]["slug"], "test-q")
        self.assertIsNotNone(rows[0]["structural"])

    def test_outcome_none_is_coverage_failure(self):
        _m, outcome = review.review_memo(
            self.memo_path, "none", self.scores, today=date(2027, 1, 16))
        self.assertTrue(outcome["coverage_failure"])
        self.assertFalse(outcome["top_hit"])
        self.assertAlmostEqual(outcome["brier"], 0.1217)

    def test_unknown_scenario_raises(self):
        with self.assertRaises(ValueError):
            review.review_memo(self.memo_path, "不存在的情景", self.scores,
                               today=date(2027, 1, 16))

    def test_early_review_warns_but_records(self):
        with mock.patch("builtins.print") as fake_print:
            _m, outcome = review.review_memo(
                self.memo_path, "情景甲", self.scores, today=date(2026, 12, 1))
        self.assertEqual(outcome["occurred"], "情景甲")
        printed = " ".join(str(c.args[0]) for c in fake_print.call_args_list)
        self.assertIn("未到复核日", printed)


class TestBuildPage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.scores = os.path.join(self.tmp.name, "scores.jsonl")
        self.out = os.path.join(self.tmp.name, "calibration.html")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_rows(self, rows):
        with open(self.scores, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def test_page_with_no_reviewed(self):
        self._write_rows([row("2026-11-21-aaa", "2027-01-15")])
        out = review.build_page(self.scores, self.out, today=date(2026, 11, 22))
        text = open(out, encoding="utf-8").read()
        self.assertIn("暂无已复盘 memo", text)
        self.assertIn("2027-01-15", text)
        self.assertIn("结构分一览", text)

    def test_page_with_reviewed_and_due(self):
        self._write_rows([
            row("2026-11-21-aaa", "2027-01-15", structural=90,
                outcome={"occurred": "情景乙", "brier": 0.2217,
                         "calibration": 88.9, "top_hit": False,
                         "coverage_failure": False}),
            row("2026-11-21-bbb", "2027-01-10", structural=85),
        ])
        out = review.build_page(self.scores, self.out, today=date(2027, 1, 20))
        text = open(out, encoding="utf-8").read()
        self.assertIn("88.9", text)
        self.assertIn("已到期未复盘", text)
        self.assertIn("2026-11-21-bbb", text)


class TestWeeklySummary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.decisions = os.path.join(self.tmp.name, "decisions")
        os.makedirs(self.decisions)
        self.scores = os.path.join(self.tmp.name, "scores.jsonl")
        write_memo(self.decisions, "2026-11-21-test-q")
        old = dict(MEMO_JSON)
        old["question"] = "旧问题不应出现在汇总里"
        write_memo(self.decisions, "2026-11-10-old-q", data=old)
        with open(self.scores, "w", encoding="utf-8") as f:
            f.write(json.dumps(
                row("2026-11-21-test-q", "2027-01-15", structural=95),
                ensure_ascii=False) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_summary_covers_window_only(self):
        text = weekly_summary.build_summary(
            self.decisions, self.scores, today=date(2026, 11, 22), days=7)
        self.assertIn("未来 90 天测试问题的走向", text)
        self.assertNotIn("旧问题不应出现在汇总里", text)
        self.assertIn("95/100", text)
        self.assertIn("主情景 情景甲 45%", text)
        self.assertIn("暂无已复盘 memo", text)

    def test_send_without_url_exits_2(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEOINTEL_WEEKLY_WEBHOOK", None)
            code = weekly_summary.main(
                ["--send", "--decisions", self.decisions,
                 "--scores", self.scores, "--date", "2026-11-22"])
        self.assertEqual(code, 2)

    def test_send_posts_feishu_payload(self):
        seen = {}

        class FakeResp:
            status = 200

            def read(self):
                return b'{"code": 0, "msg": "success"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=15):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResp()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            code = weekly_summary.main(
                ["--send", "--webhook", "https://example.invalid/hook",
                 "--decisions", self.decisions, "--scores", self.scores,
                 "--date", "2026-11-22"])
        self.assertEqual(code, 0)
        self.assertEqual(seen["url"], "https://example.invalid/hook")
        # 飞书自定义机器人标准格式：msg_type text + content.text
        self.assertEqual(seen["body"]["msg_type"], "text")
        text = seen["body"]["content"]["text"]
        self.assertIn("每周汇总", text)
        self.assertIn("未来 90 天测试问题的走向", text)
        self.assertNotIn("# geointel", text)  # markdown 标题号已去掉

    def test_feishu_payload_strips_markdown_headings(self):
        payload = weekly_summary.build_feishu_payload(
            "# 标题\n\n## 小节\n- 一条\n")
        self.assertEqual(payload["msg_type"], "text")
        text = payload["content"]["text"]
        self.assertIn("标题", text)
        self.assertIn("小节", text)
        self.assertIn("- 一条", text)
        self.assertNotIn("#", text)

    def test_feishu_payload_truncates_long_text(self):
        payload = weekly_summary.build_feishu_payload("x" * 20000)
        text = payload["content"]["text"]
        self.assertLessEqual(len(text), weekly_summary.FEISHU_MAX_TEXT)
        self.assertTrue(text.endswith("……（内容过长，已截断）"))

    def test_send_feishu_error_code_fails(self):
        class FakeResp:
            status = 200

            def read(self):
                return b'{"code": 1, "msg": "boom"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen",
                        lambda req, timeout=15: FakeResp()):
            code = weekly_summary.main(
                ["--send", "--webhook", "https://example.invalid/hook",
                 "--decisions", self.decisions, "--scores", self.scores,
                 "--date", "2026-11-22"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
