import json
import os
import sys
import tempfile
import unittest

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
        self.assertNotIn("series", T("核心 PCE 环比年化 ≥ 0.35%", 0.35))
        self.assertNotIn("series", T("BIS 许可证拒签率连续两月超过 50%", 50))

    def test_two_series_ambiguous(self):
        self.assertNotIn("series", T("联邦基金利率升至 4.5% 且 10 年期美债突破 5%", 4.5))

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
