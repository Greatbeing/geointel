#!/usr/bin/env python3
"""Unit tests for memo parsing, deliver formatting, and scoring (stdlib only)."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import deliver  # noqa: E402
import memo  # noqa: E402
import score  # noqa: E402

GOOD_JSON = {
    "scenarios": [
        {"name": "加速收紧", "prob": 45},
        {"name": "管控常态化", "prob": 35},
        {"name": "碎片化升级", "prob": 20},
    ],
    "triggers": [
        {
            "id": "t1",
            "description": "两项以上跨党派出口管制法案通过众院委员会",
            "keywords": ["export control bill", "House committee"],
            "threshold": 2,
            "type": "count",
            "action": "启动供应链去风险预案",
        },
        {
            "id": "t2",
            "description": "BIS 对华 AI 芯片许可证拒签率连续两月超过 50%",
            "keywords": [],
            "threshold": 50,
            "type": "value",
            "action": "冻结先进制程流片计划",
        },
        {
            "id": "t3",
            "description": "至少两个关键盟友正式采纳与美国等效的管制措施",
            "keywords": ["Netherlands", "Japan", "adopts", "aligned with US"],
            "threshold": 2,
            "type": "count",
            "action": "加速非美供应链合规审计",
        },
        {
            "id": "t4",
            "description": "中国正式发布针对美防务科技领域的稀土出口许可限制",
            "keywords": ["rare earth", "export restrictions", "China"],
            "threshold": 1,
            "type": "count",
            "action": "触发备选采购合同与库存调拨预案",
        },
    ],
    "confidence": "medium",
    "review_date": "2027-02-18",
    "question": "未来 90 天半导体出口管制升级的概率与应对",
    "generated_at": "2026-11-21T04:11:02Z",
    "horizon_days": 90,
    "items_used": 40,
}

MEMO_MD_TEMPLATE = """---
question: {question}
generated_at: {generated_at}
horizon_days: {horizon_days}
items_used: {items_used}
---

```json
{json_block}
```

# {question}

## 1. 问题与边界
**决策核心**：在未来 90 天内调整对华半导体供应链配置。
**最晚决策点**：2027-01-15（留出 30 天执行窗口）。

## 6. 建议与触发条件
**推荐：选项 2（选择性接触与游说），并设定触发条件自动升级至选项 1。**
"""


def make_memo(tmpdir, data=None, name="2026-11-21-chips-export-control.md"):
    data = data if data is not None else GOOD_JSON
    text = MEMO_MD_TEMPLATE.format(
        question=data["question"],
        generated_at=data["generated_at"],
        horizon_days=data["horizon_days"],
        items_used=data["items_used"],
        json_block=json.dumps(data, ensure_ascii=False, indent=2),
    )
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class TestMemoParsing(unittest.TestCase):
    def test_load_memo_from_md_json_block(self):
        with tempfile.TemporaryDirectory() as d:
            path = make_memo(d)
            m = memo.load_memo(path)
            self.assertEqual(m["memo_date"], "2026-11-21")
            self.assertEqual(m["frontmatter"]["question"], GOOD_JSON["question"])
            self.assertEqual(len(m["data"]["scenarios"]), 3)
            self.assertEqual(m["data"]["review_date"], "2027-02-18")

    def test_sidecar_json_preferred(self):
        with tempfile.TemporaryDirectory() as d:
            path = make_memo(d)
            sidecar = os.path.splitext(path)[0] + ".json"
            tweaked = dict(GOOD_JSON)
            tweaked["confidence"] = "high"
            with open(sidecar, "w", encoding="utf-8") as f:
                json.dump(tweaked, f, ensure_ascii=False)
            m = memo.load_memo(path)
            self.assertEqual(m["data"]["confidence"], "high")

    def test_latest_memo_picks_newest_date(self):
        with tempfile.TemporaryDirectory() as d:
            make_memo(d, name="2026-11-20-fed-hike-90d.md")
            make_memo(d, name="2026-11-21-chips-export-control.md")
            latest = memo.latest_memo(d)
            self.assertTrue(latest.endswith("2026-11-21-chips-export-control.md"))

    def test_latest_memo_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(memo.latest_memo(d))


class TestDeliverFormatting(unittest.TestCase):
    def test_format_contains_key_parts(self):
        with tempfile.TemporaryDirectory() as d:
            m = memo.load_memo(make_memo(d))
            out = deliver.format_memo(m)
            self.assertIn("未来 90 天半导体出口管制升级", out)
            self.assertIn("加速收紧 45%", out)
            self.assertIn("管控常态化 35%", out)
            self.assertIn("碎片化升级 20%", out)
            self.assertIn("T1", out)
            self.assertIn("启动供应链去风险预案", out)
            self.assertIn("2027-02-18", out)
            self.assertIn("选项 2", out)
            self.assertIn("最晚决策点：2027-01-15", out)

    def test_fresh_memo_printed(self):
        with tempfile.TemporaryDirectory() as d:
            path = make_memo(d)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = deliver.main(["--memo", path, "--date", "2026-11-21"])
            self.assertEqual(rc, 0)
            self.assertIn("加速收紧 45%", buf.getvalue())

    def test_stale_memo_short_notice(self):
        with tempfile.TemporaryDirectory() as d:
            path = make_memo(d)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = deliver.main(["--memo", path, "--date", "2026-11-22"])
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("今日暂无新决策 memo", out)
            self.assertNotIn("加速收紧", out)


class TestStructuralScore(unittest.TestCase):
    def _memo(self, data):
        with tempfile.TemporaryDirectory() as d:
            return memo.load_memo(make_memo(d, data=data))

    def test_perfect_memo_scores_100(self):
        m = self._memo(GOOD_JSON)
        total, deductions = score.structural_score(m)
        self.assertEqual(total, 100)
        self.assertEqual(deductions, [])

    def test_probs_not_summing_to_100(self):
        bad = dict(GOOD_JSON)
        bad["scenarios"] = [
            {"name": "A", "prob": 40},
            {"name": "B", "prob": 30},
            {"name": "C", "prob": 20},
        ]
        m = self._memo(bad)
        total, deductions = score.structural_score(m)
        self.assertEqual(total, 90)
        self.assertTrue(any("不是 100" in r for _, r in deductions))

    def test_trigger_missing_action(self):
        bad = json.loads(json.dumps(GOOD_JSON))
        bad["triggers"][0]["action"] = ""
        m = self._memo(bad)
        total, deductions = score.structural_score(m)
        self.assertEqual(total, 98)
        self.assertTrue(any("没有对应动作" in r for _, r in deductions))

    def test_count_trigger_without_keywords(self):
        bad = json.loads(json.dumps(GOOD_JSON))
        bad["triggers"][0]["keywords"] = []
        m = self._memo(bad)
        total, deductions = score.structural_score(m)
        self.assertEqual(total, 98)
        self.assertTrue(any("没有关键词" in r for _, r in deductions))

    def test_no_triggers_scores_60(self):
        bad = json.loads(json.dumps(GOOD_JSON))
        bad["triggers"] = []
        m = self._memo(bad)
        total, _ = score.structural_score(m)
        self.assertEqual(total, 60)

    def test_few_triggers_ceiling_recorded(self):
        bad = json.loads(json.dumps(GOOD_JSON))
        bad["triggers"] = bad["triggers"][:2]
        m = self._memo(bad)
        total, deductions = score.structural_score(m)
        self.assertEqual(total, 80)
        self.assertTrue(any("触发器只有 2 条" in r for _, r in deductions))

    def test_missing_review_date(self):
        bad = json.loads(json.dumps(GOOD_JSON))
        del bad["review_date"]
        m = self._memo(bad)
        total, deductions = score.structural_score(m)
        self.assertEqual(total, 85)
        self.assertTrue(any("复核日期" in r for _, r in deductions))


class TestBrierScore(unittest.TestCase):
    def test_top_scenario_hits(self):
        brier, calib = score.brier_score([0.45, 0.35, 0.20], 0)
        self.assertAlmostEqual(brier, 0.155, places=3)
        self.assertAlmostEqual(calib, 92.25, places=1)

    def test_second_scenario_hits(self):
        brier, calib = score.brier_score([0.45, 0.35, 0.20], 1)
        self.assertAlmostEqual(brier, 0.222, places=3)
        self.assertAlmostEqual(calib, 88.92, places=1)

    def test_outcome_outside_scenarios(self):
        # Brier 看着更好，但这是覆盖失效，见 rubric。
        brier, _ = score.brier_score([0.45, 0.35, 0.20], None)
        self.assertAlmostEqual(brier, 0.122, places=3)


class TestScoreRecording(unittest.TestCase):
    def test_upsert_no_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            path = make_memo(d)
            m = memo.load_memo(path)
            scores_path = os.path.join(d, "scores.jsonl")
            total, deductions = score.structural_score(m)
            score.record_structural(m, total, deductions, path=scores_path)
            score.record_structural(m, total, deductions, path=scores_path)
            with open(scores_path, encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["structural"]["score"], 100)
            self.assertEqual(rows[0]["slug"], "chips-export-control")
            self.assertIsNone(rows[0]["outcome"])

    def test_record_outcome(self):
        with tempfile.TemporaryDirectory() as d:
            path = make_memo(d)
            m = memo.load_memo(path)
            scores_path = os.path.join(d, "scores.jsonl")
            outcome = score.record_outcome(m, "加速收紧", path=scores_path)
            self.assertAlmostEqual(outcome["brier"], 0.155, places=3)
            self.assertTrue(outcome["top_hit"])
            self.assertFalse(outcome["coverage_failure"])
            outcome_none = score.record_outcome(m, "none", path=scores_path)
            self.assertTrue(outcome_none["coverage_failure"])
            with open(scores_path, encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(rows), 1)
            self.assertIsNotNone(rows[0]["structural"])

    def test_record_outcome_unknown_scenario(self):
        with tempfile.TemporaryDirectory() as d:
            m = memo.load_memo(make_memo(d))
            with self.assertRaises(ValueError):
                score.record_outcome(m, "不存在的情景", path=os.path.join(d, "s.jsonl"))


if __name__ == "__main__":
    unittest.main()
