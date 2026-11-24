import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import health  # noqa: E402
import market_collect as mc  # noqa: E402

NOW = datetime(2026, 11, 24, 2, 0, tzinfo=timezone.utc)


class JobHealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "health")

    def tearDown(self):
        self.tmp.cleanup()

    def test_ok_job_no_problem(self):
        health.record("digest", 0, health_dir=self.dir, now=NOW)
        self.assertEqual(health.check_jobs(self.dir, now=NOW + timedelta(hours=1)), {})

    def test_failed_job_reported_with_note(self):
        health.record("digest", 0, health_dir=self.dir, now=NOW)
        health.record("digest", 2, note="x\nTimeoutError: gateway", health_dir=self.dir, now=NOW)
        p = health.check_jobs(self.dir, now=NOW)
        self.assertIn("job:digest:fail", p)
        self.assertIn("TimeoutError", p["job:digest:fail"])
        with open(os.path.join(self.dir, "digest.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["last_ok"], NOW.isoformat(timespec="seconds"))

    def test_stale_job_reported(self):
        health.record("collect", 0, health_dir=self.dir, now=NOW)
        p = health.check_jobs(self.dir, now=NOW + timedelta(hours=9))
        self.assertIn("job:collect:stale", p)

    def test_missing_dir_is_empty(self):
        self.assertEqual(health.check_jobs(os.path.join(self.tmp.name, "nope"), now=NOW), {})


class MarketHealthTest(unittest.TestCase):
    def _latest(self, d):
        return {n: {"date": d, "value": 1.0} for n in mc.SERIES}

    def test_fresh_on_monday_after_thursday(self):
        self.assertEqual(health.check_market(date(2026, 11, 23), self._latest("2026-11-19")), {})

    def test_stale_series_reported(self):
        latest = self._latest("2026-11-20")
        latest["USDCNY"] = {"date": "2026-11-10", "value": 7.1}
        p = health.check_market(date(2026, 11, 24), latest)
        self.assertEqual(list(p), ["market:USDCNY"])

    def test_broad_dollar_has_longer_limit(self):
        latest = self._latest("2026-11-20")
        latest["DTWEXBGS"] = {"date": "2026-11-13", "value": 120.0}  # 7 个工作日
        self.assertEqual(health.check_market(date(2026, 11, 24), latest), {})

    def test_missing_series_reported(self):
        latest = self._latest("2026-11-20")
        del latest["DGS10"]
        self.assertIn("market:DGS10", health.check_market(date(2026, 11, 24), latest))


class StateDiffTest(unittest.TestCase):
    def test_only_changes_are_new(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "s.json")
            new, fixed = health.diff_state({"a": "A"}, sp)
            self.assertEqual((new, fixed), ({"a": "A"}, {}))
            new, fixed = health.diff_state({"a": "A"}, sp)
            self.assertEqual((new, fixed), ({}, {}))
            new, fixed = health.diff_state({}, sp)
            self.assertEqual((new, fixed), ({}, {"a": "A"}))

    def test_message_sections(self):
        msg = health.format_message({"b": "B 坏了"}, {"c": "C 好了"}, {"a": "A 还坏", "b": "B 坏了"})
        self.assertIn("新问题", msg)
        self.assertIn("已恢复", msg)
        self.assertIn("仍未解决：\n- A 还坏", msg)


class MainSendTest(unittest.TestCase):
    def test_send_only_on_change(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "s.json")
            with mock.patch.object(health, "STATE_PATH", sp), \
                 mock.patch.object(health, "HEALTH_DIR", os.path.join(d, "h")), \
                 mock.patch.object(health, "check_market", return_value={"market:X": "X 断更"}), \
                 mock.patch.dict(os.environ, {"GEOINTEL_HEALTH_WEBHOOK": "https://example/hook"}), \
                 mock.patch("weekly_summary.send_webhook") as send:
                self.assertEqual(health.main(["check", "--send"]), 0)
                self.assertEqual(health.main(["check", "--send"]), 0)
            self.assertEqual(send.call_count, 1)
            self.assertIn("X 断更", send.call_args[0][1])


if __name__ == "__main__":
    unittest.main()
