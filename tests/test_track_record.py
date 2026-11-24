#!/usr/bin/env python3
"""paid-ready 第 2 步测试：预测战绩页 docs/track-record.html。

覆盖：
- load_score_rows：排序、坏行跳过、文件缺失返回空
- build_track_record：累计 Brier / 已复盘 / 待复盘数字、每条备忘录
  预测主情景 vs 实际走向、Brier 与命中标记、待复盘的复核日
- render.build：track-record.html 落盘、导航含入口、首页出现战绩卡
- 无 scores.jsonl 时页面仍能生成（空态不崩）
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import render  # noqa: E402


def _row(memo, memo_date, outcome=None, question=None, scenarios=None, review_date=None):
    return {
        "memo": memo,
        "slug": memo.split("-", 3)[-1],
        "memo_date": memo_date,
        "question": question or ("问题 " + memo),
        "generated_at": memo_date + "T01:00:00Z",
        "review_date": review_date or "2027-02-01",
        "scenarios": scenarios or [{"name": "A", "prob": 60}, {"name": "B", "prob": 40}],
        "structural": {"score": 90, "deductions": []},
        "outcome": outcome,
        "scored_at": "2026-11-23T00:00:00",
    }


OUTCOME_HIT = {
    "occurred": "A", "brier": 0.32, "calibration": 84.0,
    "top_hit": True, "coverage_failure": False, "scored_at": "2027-02-01T00:00:00",
}
OUTCOME_MISS = {
    "occurred": "B", "brier": 0.68, "calibration": 66.0,
    "top_hit": False, "coverage_failure": False, "scored_at": "2027-02-01T00:00:00",
}


class TestLoadScoreRows(unittest.TestCase):
    def test_sorted_and_bad_lines_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "scores.jsonl")
            rows = [
                _row("2026-11-11-old", "2026-11-11", OUTCOME_HIT),
                _row("2026-11-23-new", "2026-11-23"),
                _row("2026-11-15-mid", "2026-11-15", OUTCOME_MISS),
            ]
            with open(path, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.write("{not json\n\n")  # 坏行 + 空行
            got = render.load_score_rows(path)
        self.assertEqual([r["memo"] for r in got],
                         ["2026-11-23-new", "2026-11-15-mid", "2026-11-11-old"])

    def test_missing_file_returns_empty(self):
        self.assertEqual(render.load_score_rows("Z:/no/such/file.jsonl"), [])


class TestBuildTrackRecord(unittest.TestCase):
    def test_stats_and_rows(self):
        rows = [
            _row("2026-11-11-a", "2026-11-11", OUTCOME_HIT),
            _row("2026-11-15-b", "2026-11-15", OUTCOME_MISS),
            _row("2026-11-23-c", "2026-11-23"),
        ]
        html_out = render.build_track_record(rows, {})
        self.assertIn("已复盘 <strong>2</strong> 份", html_out)
        self.assertIn("待复盘 <strong>1</strong> 份", html_out)
        # 累计 Brier = (0.32 + 0.68) / 2 = 0.500
        self.assertIn("累计 Brier <strong>0.500</strong>", html_out)
        self.assertIn("最高概率情景命中 <strong>1/2</strong>", html_out)
        # 预测 vs 结果逐条
        self.assertIn("预测主情景：A 60%", html_out)
        self.assertIn("实际走向：B", html_out)
        self.assertIn("Brier 0.680", html_out)
        self.assertIn("命中", html_out)
        self.assertIn("未命中", html_out)
        self.assertIn("待复盘（2027-02-01）", html_out)
        # 每条都有到 memo 页的链接
        self.assertIn(render.href("/decisions/2026-11-11-a.html"), html_out)
        self.assertIn(render.href("/decisions/2026-11-23-c.html"), html_out)

    def test_empty_rows_still_renders(self):
        html_out = render.build_track_record([], {})
        self.assertIn("已复盘 <strong>0</strong> 份", html_out)
        self.assertIn("还没有备忘录进入战绩表", html_out)

    def test_question_map_preferred(self):
        rows = [_row("2026-11-11-a", "2026-11-11", OUTCOME_HIT)]
        html_out = render.build_track_record(rows, {"2026-11-11-a": "映射的问题文本"})
        self.assertIn("映射的问题文本", html_out)
        self.assertNotIn("问题 2026-11-11-a", html_out)

    def test_none_brier_shown_as_dash(self):
        o = dict(OUTCOME_HIT)
        o["brier"] = None  # 情景为空等极端情况下 review 允许 brier=None
        rows = [_row("2026-11-11-a", "2026-11-11", o)]
        html_out = render.build_track_record(rows, {})
        self.assertIn("Brier —", html_out)


class TestRenderBuildTrackPage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_out = render.OUT
        self._old_site = render.SITE
        self._old_here = render.HERE
        render.OUT = os.path.join(self.tmp.name, "out")
        render.SITE = os.path.join(self.tmp.name, "docs")
        # 复制仓库里的真实 scores.jsonl？不：这里造小样本，测试自包含
        render.HERE = self.tmp.name
        # digest 供首页生成
        os.makedirs(os.path.join(render.OUT, "digest"))
        with open(os.path.join(render.OUT, "digest", "2026-11-23.md"),
                  "w", encoding="utf-8") as f:
            f.write("---\ndate: 2026-11-23\n---\n\n# 标题\n\n正文一段。\n")
        rows = [
            _row("2026-11-11-a", "2026-11-11", OUTCOME_HIT),
            _row("2026-11-23-c", "2026-11-23"),
        ]
        with open(os.path.join(render.HERE, "scores.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def tearDown(self):
        render.OUT = self._old_out
        render.SITE = self._old_site
        render.HERE = self._old_here
        self.tmp.cleanup()

    def test_build_writes_track_record_and_home_card(self):
        result = render.build()
        self.assertEqual(result["track_records"], 2)
        with open(os.path.join(render.SITE, "track-record.html"), encoding="utf-8") as _f:
            track = _f.read()
        self.assertIn("预测战绩", track)
        self.assertIn("累计 Brier", track)
        self.assertIn("track-record", track)  # nav aria-current 值
        # 导航含入口
        self.assertIn(render.href("/track-record.html"), track)
        # 首页战绩卡（显著入口，在简报正文之前）
        with open(os.path.join(render.SITE, "index.html"), encoding="utf-8") as _f:
            home = _f.read()
        self.assertIn("track-card", home)
        self.assertIn("已复盘 1 份 · 待复盘 1 份", home)
        self.assertIn("累计 Brier 0.320", home)
        self.assertLess(home.index("track-card"), home.index("正文一段"))


if __name__ == "__main__":
    unittest.main()
