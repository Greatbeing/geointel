#!/usr/bin/env python3
"""Unit tests for Week 2: market data parsing/storage, value triggers, dashboard strip.
Stdlib only, no network — all fixtures are inline.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import market_collect  # noqa: E402
import store  # noqa: E402
import watch  # noqa: E402

FRED_CSV = """observation_date,DFF
2026-11-18,3.88
2026-11-19,.
2026-11-20,3.88
"""

FRED_CSV_OLD_HEADER = """DATE,VALUE
2026-11-18,3.88
2026-11-20,3.88
"""

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
  xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube><Cube time="2026-11-20">
    <Cube currency="USD" rate="1.0990"/>
    <Cube currency="JPY" rate="174.20"/>
    <Cube currency="CNY" rate="7.3431"/>
  </Cube></Cube>
</gesmes:Envelope>
"""

FRANKFURTER_JSON = json.dumps({
    "amount": 1.0, "base": "USD", "date": "2026-11-20",
    "rates": {"CNY": 6.6777, "EUR": 0.90992, "JPY": 158.64},
})


class TestFredParsing(unittest.TestCase):
    def test_new_header_and_missing_values(self):
        rows = market_collect.parse_fred_csv(FRED_CSV)
        self.assertEqual(rows, [("2026-11-18", 3.88), ("2026-11-20", 3.88)])

    def test_old_header(self):
        rows = market_collect.parse_fred_csv(FRED_CSV_OLD_HEADER)
        self.assertEqual(rows, [("2026-11-18", 3.88), ("2026-11-20", 3.88)])

    def test_empty(self):
        self.assertEqual(market_collect.parse_fred_csv("observation_date,DFF\n"), [])


class TestEcbAndFrankfurter(unittest.TestCase):
    def test_ecb_xml(self):
        date_s, rates = market_collect.parse_ecb_xml(ECB_XML)
        self.assertEqual(date_s, "2026-11-20")
        self.assertAlmostEqual(rates["USD"], 1.0990)
        self.assertAlmostEqual(rates["CNY"], 7.3431)

    def test_ecb_cross_usdcny(self):
        _d, rates = market_collect.parse_ecb_xml(ECB_XML)
        usdcny = rates["CNY"] / rates["USD"]
        self.assertAlmostEqual(usdcny, 6.681, places=2)

    def test_frankfurter(self):
        date_s, rates = market_collect.parse_frankfurter(FRANKFURTER_JSON)
        self.assertEqual(date_s, "2026-11-20")
        self.assertAlmostEqual(rates["CNY"], 6.6777)
        self.assertAlmostEqual(1.0 / rates["EUR"], 1.0990, places=3)


class TestSpread(unittest.TestCase):
    def test_derive_spread(self):
        rows = [
            {"series": "DGS2", "date": "2026-11-20", "value": 3.90, "source": "FRED"},
            {"series": "DGS10", "date": "2026-11-20", "value": 4.12, "source": "FRED"},
            {"series": "DGS2", "date": "2026-11-19", "value": 3.91, "source": "FRED"},
        ]
        out = market_collect.derive_spread(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["series"], "US210")
        self.assertAlmostEqual(out[0]["value"], 0.22)


class TestStoreMarket(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        self._old = store.DB
        store.DB = self.db

    def tearDown(self):
        store.DB = self._old
        self.tmp.cleanup()

    def test_upsert_latest_history(self):
        store.upsert_market([
            {"series": "DFF", "date": "2026-11-19", "value": 3.88, "source": "FRED"},
            {"series": "DFF", "date": "2026-11-20", "value": 3.88, "source": "FRED"},
            {"series": "DFF", "date": "2026-11-20", "value": 3.89, "source": "FRED"},
        ])
        latest = store.latest_market(["DFF"])
        self.assertEqual(latest["DFF"]["date"], "2026-11-20")
        self.assertAlmostEqual(latest["DFF"]["value"], 3.89)  # 同日后写覆盖
        hist = store.market_history("DFF", limit=10)
        self.assertEqual([d for d, _ in hist], ["2026-11-20", "2026-11-19"])

    def test_latest_all_series(self):
        store.upsert_market([
            {"series": "DFF", "date": "2026-11-20", "value": 3.88, "source": "FRED"},
            {"series": "USDCNY", "date": "2026-11-20", "value": 6.68, "source": "FRED"},
        ])
        latest = store.latest_market()
        self.assertEqual(set(latest), {"DFF", "USDCNY"})


class TestValueTriggerEval(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        self._old = store.DB
        store.DB = self.db

    def tearDown(self):
        store.DB = self._old
        self.tmp.cleanup()

    def _seed(self, series, values):
        # values: [(date, value)] 从新到旧
        store.upsert_market([
            {"series": series, "date": d, "value": v, "source": "TEST"}
            for d, v in values
        ])

    def test_fired(self):
        from datetime import date as _date
        today = _date.today().isoformat()
        self._seed("DFF", [(today, 4.10), ("2026-11-20", 4.05)])
        status, detail = watch.eval_value_trigger(
            {"series": "DFF", "op": ">=", "threshold": 4.0, "window": 2})
        self.assertEqual(status, "fired")
        self.assertIn("4.1", detail)

    def test_not_fired(self):
        from datetime import date as _date
        today = _date.today().isoformat()
        self._seed("DFF", [(today, 3.88)])
        status, _ = watch.eval_value_trigger(
            {"series": "DFF", "op": ">=", "threshold": 4.0})
        self.assertEqual(status, "not-fired")

    def test_below_operator(self):
        from datetime import date as _date
        today = _date.today().isoformat()
        self._seed("USDCNY", [(today, 6.68)])
        status, _ = watch.eval_value_trigger(
            {"series": "USDCNY", "op": "<=", "threshold": 7.0})
        self.assertEqual(status, "fired")

    def test_no_data(self):
        status, _ = watch.eval_value_trigger(
            {"series": "DFF", "op": ">=", "threshold": 4.0})
        self.assertEqual(status, "no-data")

    def test_stale_data(self):
        self._seed("DFF", [("2026-01-05", 5.0)])
        status, detail = watch.eval_value_trigger(
            {"series": "DFF", "op": ">=", "threshold": 4.0})
        self.assertEqual(status, "stale")
        self.assertIn("已过期", detail)

    def test_manual_without_series(self):
        status, _ = watch.eval_value_trigger({"threshold": 50})
        self.assertEqual(status, "manual")

    def test_window_needs_enough_observations(self):
        from datetime import date as _date
        today = _date.today().isoformat()
        self._seed("DFF", [(today, 4.10)])
        status, _ = watch.eval_value_trigger(
            {"series": "DFF", "op": ">=", "threshold": 4.0, "window": 2})
        self.assertEqual(status, "no-data")


class TestWatchMainWithSeriesTrigger(unittest.TestCase):
    def test_fired_alert_written(self):
        from datetime import date as _date
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "test.db")
            old = store.DB
            store.DB = db
            try:
                today = _date.today().isoformat()
                store.upsert_market([
                    {"series": "DFF", "date": today, "value": 4.10, "source": "TEST"}])
                dec_dir = os.path.join(d, "decisions")
                out_dir = os.path.join(d, "alerts")
                os.makedirs(dec_dir)
                memo = {
                    "question": "演示问题",
                    "scenarios": [{"name": "A", "prob": 60}, {"name": "B", "prob": 40}],
                    "triggers": [{
                        "id": "t-demo", "description": "演示：联邦基金利率高于 4.0%",
                        "keywords": [], "threshold": 4.0, "type": "value",
                        "series": "DFF", "op": ">=", "window": 1,
                        "action": "演示动作",
                    }],
                    "confidence": "medium", "review_date": "2027-02-18",
                }
                with open(os.path.join(dec_dir, "2026-11-22-demo.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(memo, f, ensure_ascii=False)
                with mock.patch.object(watch.render, "build", return_value={}):
                    rc = watch.main(["--decisions-dir", dec_dir, "--out-dir", out_dir])
                self.assertEqual(rc, 0)
                files = os.listdir(out_dir)
                self.assertEqual(len(files), 1)
                text = open(os.path.join(out_dir, files[0]), encoding="utf-8").read()
                self.assertIn("联邦基金利率高于 4.0%", text)
                self.assertIn("演示动作", text)
            finally:
                store.DB = old


class TestDashboardStrip(unittest.TestCase):
    def test_strip_with_data(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "test.db")
            old = store.DB
            store.DB = db
            try:
                from datetime import date as _date
                today = _date.today().isoformat()
                store.upsert_market([
                    {"series": "DFF", "date": today, "value": 3.88, "source": "FRED"},
                    {"series": "DGS2", "date": today, "value": 3.90, "source": "FRED"},
                    {"series": "DGS10", "date": today, "value": 4.12, "source": "FRED"},
                    {"series": "US210", "date": today, "value": 0.22, "source": "FRED-derived"},
                    {"series": "USDCNY", "date": today, "value": 6.68, "source": "FRED"},
                ])
                strip = market_collect.dashboard_strip()
                self.assertIn("核心利率 / 汇率", strip)
                self.assertIn("美联邦基金利率 3.88%", strip)
                self.assertIn("美债 2–10 年利差 +0.22pp", strip)
                self.assertIn("美元/人民币 6.68", strip)
            finally:
                store.DB = old

    def test_stale_series_gets_its_own_date(self):
        # 某序列比最新日期旧：必须标出它的实际日期，不能冒充最新值
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "test.db")
            old = store.DB
            store.DB = db
            try:
                from datetime import date as _date
                today = _date.today().isoformat()
                store.upsert_market([
                    {"series": "DFF", "date": today, "value": 3.88, "source": "FRED"},
                    {"series": "USDCNY", "date": "2026-11-13", "value": 6.67, "source": "FRED"},
                ])
                strip = market_collect.dashboard_strip()
                self.assertIn("美元/人民币 6.67（11-13）", strip)
                self.assertIn("美联邦基金利率 3.88%", strip)
            finally:
                store.DB = old

    def test_strip_without_data(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "test.db")
            old = store.DB
            store.DB = db
            try:
                strip = market_collect.dashboard_strip()
                self.assertIn("暂无更新", strip)
                self.assertNotIn("3.88", strip)
            finally:
                store.DB = old


if __name__ == "__main__":
    unittest.main()
