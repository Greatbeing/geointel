import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import market_collect as mc  # noqa: E402


class _FakeDate(date):
    TODAY = date(2026, 11, 23)  # 周一

    @classmethod
    def today(cls):
        return cls.TODAY


class BusinessDayTest(unittest.TestCase):
    def test_monday_after_thursday_is_two_bdays(self):
        self.assertEqual(mc.business_days_between(date(2026, 11, 19), date(2026, 11, 23)), 2)

    def test_weekend_not_counted(self):
        self.assertEqual(mc.business_days_between(date(2026, 11, 20), date(2026, 11, 23)), 1)

    def test_same_day_zero(self):
        self.assertEqual(mc.business_days_between(date(2026, 11, 23), date(2026, 11, 23)), 0)


class DashboardStripTest(unittest.TestCase):
    def _latest(self, d):
        return {n: {"date": d, "value": 1.0} for n in mc.SERIES}

    def test_monday_shows_last_thursday(self):
        with mock.patch.object(mc, "date", _FakeDate), \
             mock.patch.object(mc.store, "latest_market", return_value=self._latest("2026-11-19")):
            out = mc.dashboard_strip()
        self.assertIn("截至 2026-11-19", out)
        self.assertNotIn("暂无更新", out)

    def test_really_stale_hidden(self):
        with mock.patch.object(mc, "date", _FakeDate), \
             mock.patch.object(mc.store, "latest_market", return_value=self._latest("2026-11-10")):
            out = mc.dashboard_strip()
        self.assertIn("暂无更新", out)


class FxFallbackTest(unittest.TestCase):
    def test_stale_fred_fx_backfilled_from_ecb(self):
        def fake_fred(fid, start, end):
            if fid in ("DEXCHUS", "DEXUSEU", "DEXJPUS"):
                return [("2026-11-13", 7.1)]
            return [("2026-11-19", 4.0)]
        ecb = ("2026-11-20", {"USD": 1.05, "CNY": 7.5, "JPY": 160.0})
        with mock.patch.object(mc, "date", _FakeDate), \
             mock.patch.object(mc, "fetch_fred", side_effect=fake_fred), \
             mock.patch.object(mc, "fetch_ecb", return_value=ecb), \
             mock.patch.object(mc, "fetch_frankfurter", side_effect=RuntimeError("x")):
            rows, failed = mc.collect()
        newest = {}
        for r in rows:
            newest[r["series"]] = max(newest.get(r["series"], ""), r["date"])
        for n in ("USDCNY", "EURUSD", "USDJPY"):
            self.assertEqual(newest[n], "2026-11-20", n)

    def test_fresh_fred_fx_skips_backup(self):
        def fake_fred(fid, start, end):
            return [("2026-11-20", 7.1)]
        with mock.patch.object(mc, "date", _FakeDate), \
             mock.patch.object(mc, "fetch_fred", side_effect=fake_fred), \
             mock.patch.object(mc, "fetch_ecb") as ecb:
            mc.collect()
        ecb.assert_not_called()


if __name__ == "__main__":
    unittest.main()
