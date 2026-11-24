import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import store  # noqa: E402
import watch  # noqa: E402


class PruneKeepsMonthlyTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self._db = store.DB
        store.DB = os.path.join(self.tmp.name, "t.db")
        store.connect().close()

    def tearDown(self):
        store.DB = self._db
        self.tmp.cleanup()

    def test_monthly_older_than_180d_survives(self):
        rows = ([{"series": "PCEPILFE", "date": "2025-09-01", "value": 126.9},
                 {"series": "PCEPILFE", "date": "2026-09-01", "value": 130.9},
                 {"series": "DFF", "date": "2025-09-01", "value": 4.0},
                 {"series": "DFF", "date": "2026-11-20", "value": 3.9}])
        store.upsert_market(rows)
        store.prune_market(days=180)
        con = store.connect()
        left = sorted(con.execute("select series, date from market_series").fetchall())
        con.close()
        self.assertEqual(left, [("DFF", "2026-11-20"), ("PCEPILFE", "2025-09-01"), ("PCEPILFE", "2026-09-01")])

    def test_monthly_really_old_pruned(self):
        store.upsert_market([{"series": "CPILFESL", "date": "2023-01-01", "value": 1.0},
                             {"series": "CPILFESL", "date": "2026-10-01", "value": 2.0}])
        store.prune_market(days=180)
        con = store.connect()
        left = [r[0] for r in con.execute("select date from market_series order by date").fetchall()]
        con.close()
        self.assertEqual(left, ["2026-10-01"])


class YoyEndToEndTest(unittest.TestCase):
    def test_yoy_from_13_rows(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        old = store.DB
        store.DB = os.path.join(tmp.name, "t.db")
        store.connect().close()
        try:
            rows = []
            for i in range(13):
                y, m = 2025 + (9 + i) // 12, (9 + i) % 12 + 1
                rows.append({"series": "PCEPILFE", "date": f"{y}-{m:02d}-01",
                             "value": round(100 * (1.03 ** (i / 12)), 3)})
            store.upsert_market(rows)
            self.assertAlmostEqual(watch.monthly_yoy("PCEPILFE"), 3.0, places=1)
        finally:
            store.DB = old
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
