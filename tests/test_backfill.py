import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import market_collect as mc  # noqa: E402


class BackfillTest(unittest.TestCase):
    def test_gap_triggers_backfill(self):
        # 2025-09 后直接跳到 2025-11：缺 10 月
        hist = [("2026-09-01", 1), ("2025-11-01", 1), ("2025-09-01", 1)] + [("2026-%02d-01" % m, 1) for m in range(1, 10)]
        hist = sorted(hist, reverse=True)
        with mock.patch.object(mc, "SERIES", {"PCEPILFE": {"freq": "M"}}), \
             mock.patch.object(mc.store, "market_history", return_value=hist):
            self.assertTrue(mc.needs_backfill())

    def test_continuous_no_backfill(self):
        hist = [("2026-%02d-01" % m, 1) for m in range(1, 15)]
        hist = sorted(hist, reverse=True)
        with mock.patch.object(mc, "SERIES", {"PCEPILFE": {"freq": "M"}}), \
             mock.patch.object(mc.store, "market_history", return_value=hist):
            self.assertFalse(mc.needs_backfill())

    def test_too_few_triggers(self):
        with mock.patch.object(mc, "SERIES", {"PCEPILFE": {"freq": "M"}}), \
             mock.patch.object(mc.store, "market_history", return_value=[("2026-09-01", 1)]):
            self.assertTrue(mc.needs_backfill())


if __name__ == "__main__":
    unittest.main()
