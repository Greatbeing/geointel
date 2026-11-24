import json
import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import review  # noqa: E402
import watch  # noqa: E402


def T(desc, thr, **kw):
    d = {"kind": "value", "description": desc, "threshold": thr}
    d.update(kw)
    return watch.infer_series(d)


class InferSeriesTest(unittest.TestCase):
    def test_ten_year_up(self):
        t = T("10 年期美债收益率站上 5.25% 并日收线企稳", 5.25)
        self.assertEqual((t["series"], t["op"]), ("DGS10", ">="))
        self.assertTrue(t["series_inferred"])

    def test_cny_down(self):
        t = T("美元/人民币即期汇率跌破 6.60", 6.6)
        self.assertEqual((t["series"], t["op"]), ("USDCNY", "<="))

    def test_unknown_metric_stays_manual(self):
        self.assertNotIn("series", T("GDP 环比年化 ≥ 0.35%", 0.35))
        self.assertNotIn("series", T("BIS 许可证拒签率连续两月超过 50%", 50))

    def test_two_series_takes_first_mentioned(self):
        t = T("联邦基金利率升至 4.5% 且 10 年期美债突破 5%", 4.5)
        self.assertEqual(t["series"], "DFF")

    def test_no_series_in_description_stays_manual(self):
        self.assertNotIn("series", T("亚洲LNG现货价格指数下跌 10%", 10))

    def test_no_direction(self):
        self.assertNotIn("series", T("10 年期美债收益率在 5% 附近", 5))

    def test_explicit_series_kept(self):
        t = T("10 年期美债站上 5.25%", 5.25, series="DGS2", op="<=")
        self.assertEqual((t["series"], t["op"]), ("DGS2", "<="))
        self.assertNotIn("series_inferred", t)

    def test_non_numeric_threshold(self):
        self.assertNotIn("series", T("10 年期美债站上高位", "高位"))

    def test_count_trigger_untouched(self):
        t = watch.infer_series({"kind": "count", "description": "10 年期美债站上", "threshold": 3})
        self.assertNotIn("series", t)


class MonthlyYoyTest(unittest.TestCase):
    def test_yoy(self):
        # hist[0] 是最新；13 个月，同比恰为 3%
        hist = []
        for i in range(13):
            year, month = 2025 + (9 + i) // 12, (9 + i) % 12 + 1
            hist.append({"date": f"{year}-{month:02d}-01", "value": 100 * (1.03 ** (i / 12))})
        hist.reverse()
        hist = [(h["date"], h["value"]) for h in hist]  # 真实返回是 (date, value) 元组
        with unittest.mock.patch.object(watch.store, "market_history", side_effect=lambda s, limit: hist):
            y = watch.monthly_yoy("X")
        self.assertAlmostEqual(y, 3.0, places=1)

    def test_insufficient(self):
        with unittest.mock.patch.object(watch.store, "market_history", return_value=[{"date": "2026-01-01", "value": 1}]):
            self.assertIsNone(watch.monthly_yoy("X"))


class InferMonthlyTest(unittest.TestCase):
    def test_mom_not_bound(self):
        # 环比年化 ≠ 同比：描述没说“同比”就不自动判定，避免 3% 去比 0.35%
        t = watch.infer_series({"kind": "value", "threshold": 0.35,
                                "description": "核心 PCE 环比年化 ≥ 0.35%"})
        self.assertNotIn("series", t)

    def test_yoy_word_binds(self):
        t = watch.infer_series({"kind": "value", "threshold": 3.5,
                                "description": "CPI 同比重回 ≥ 3.5%"})
        self.assertEqual((t["series"], t["measure"], t["op"]), ("CPILFESL", "yoy", ">="))

    def test_mom_with_yoy_in_other_clause_not_bound(self):
        # 主句是“环比年化”，括号里另一个口径才提“同比”：不能绑
        t = watch.infer_series({"kind": "value", "threshold": 0.35,
                                "description": "核心 PCE 环比年化 ≥ 0.35%（或 CPI 同比重回 ≥ 3.5%）"})
        self.assertNotIn("series", t)

    def test_yoy_falls_back_when_month_missing(self):
        # 2025-10 FRED 留空：退到 2025-09 做同比
        hist = [("2026-10-01", 104.0)] + [("2026-%02d-01" % m, 100.0) for m in range(1, 11)] \
            + [("2025-11-01", 100.0), ("2025-09-01", 100.0)]
        hist = sorted(hist, key=lambda x: x[0], reverse=True)
        with unittest.mock.patch.object(watch.store, "market_history", side_effect=lambda s, limit: hist):
            self.assertEqual(watch.monthly_yoy("CPILFESL"), 4.0)

    def test_yoy_with_extra_months(self):
        # 最新 2026-10，库里从 2025-09 开始共 13 个月：取 2025-10 做同比
        hist = []
        for i in range(13):
            y, m = 2025 + (8 + i) // 12, (8 + i) % 12 + 1
            hist.append((f"{y}-{m:02d}-01", 100 * (1.04 ** (i / 12))))
        hist.reverse()
        with unittest.mock.patch.object(watch.store, "market_history", side_effect=lambda s, limit: hist):
            self.assertAlmostEqual(watch.monthly_yoy("CPILFESL"), 4.0, places=1)


class EvalYoyTest(unittest.TestCase):
    def _t(self):
        return {"kind": "value", "series": "CPILFESL", "measure": "yoy", "op": ">=", "threshold": 3.5}

    def test_fired(self):
        with unittest.mock.patch.object(watch, "monthly_yoy", return_value=3.8):
            st, d = watch.eval_value_trigger(self._t())
        self.assertEqual(st, "fired")
        self.assertIn("同比 3.80%", d)

    def test_no_data(self):
        with unittest.mock.patch.object(watch, "monthly_yoy", return_value=None):
            st, _ = watch.eval_value_trigger(self._t())
        self.assertEqual(st, "no-data")


class BucketTest(unittest.TestCase):
    def test_buckets(self):
        with tempfile.TemporaryDirectory() as d:
            def memo(stem, probs):
                with open(os.path.join(d, stem + ".json"), "w", encoding="utf-8") as f:
                    json.dump({"scenarios": [{"name": n, "prob": p} for n, p in probs]}, f)
            memo("m1", [("A", 70), ("B", 30)])
            memo("m2", [("A", 65), ("B", 35)])
            memo("m3", [("A", 10), ("B", 90)])
            rows = [{"memo": "m1", "outcome": {"occurred": "A"}},
                    {"memo": "m2", "outcome": {"occurred": "B"}},
                    {"memo": "m3", "outcome": {"occurred": "none"}},
                    {"memo": "m4", "outcome": None}]
            b = {x["lo"]: x for x in review.calibration_buckets(rows, decisions_dir=d)}
            self.assertEqual(b[60]["n"], 2)
            self.assertEqual(b[60]["hit_rate"], 50.0)
            self.assertEqual(b[60]["mean_p"], 67.5)
            self.assertEqual(b[20]["n"], 2)
            self.assertEqual(b[20]["hit_rate"], 50.0)
            self.assertEqual((b[0]["n"], b[0]["hit_rate"]), (1, 0.0))
            self.assertEqual((b[80]["n"], b[80]["hit_rate"]), (1, 0.0))
            self.assertEqual(b[40]["n"], 0)

    def test_empty(self):
        self.assertFalse(any(x["n"] for x in review.calibration_buckets([], decisions_dir="nope")))


if __name__ == "__main__":
    unittest.main()
