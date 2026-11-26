"""Unit tests for the header's GIFT Nifty value (gift_nifty.py, user 26 Nov). No network: fake client and key loader."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gift_nifty as G   # noqa: E402

KEY = "GLOBAL_INDEX|SGX NIFTY"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeClient:
    def __init__(self):
        self.quotes, self.ltps, self.calls = {}, {}, 0

    def fetch_quote(self, keys):
        self.calls += 1
        return (self.quotes, None) if self.quotes else (None, "HTTP 400: bad key")

    def fetch_ltp(self, keys):
        return (self.ltps, None) if self.ltps else (None, "HTTP 400: bad key")


class Gift(unittest.TestCase):
    def setUp(self):
        self.clock, self.c, self.logs = Clock(), FakeClient(), []
        self.g = G.GiftNifty(clock=self.clock, key_loader=lambda: (KEY, None))

    def test_find_key(self):
        rows = [{"segment": "GLOBAL_INDEX", "instrument_key": "GLOBAL_INDEX|DAX", "trading_symbol": "DAX"},
                {"segment": "GLOBAL_INDEX", "instrument_key": KEY, "trading_symbol": "GIFT NIFTY", "name": "GIFT NIFTY"},
                {"segment": "GLOBAL_INDICATOR", "instrument_key": "GLOBAL_INDICATOR|GIFTX", "name": "GIFT NIFTY x"}]
        self.assertEqual(G.find_key(rows), KEY)
        self.assertEqual(G.find_key([{"segment": "GLOBAL_INDEX", "instrument_key": KEY}]), KEY)
        self.assertIsNone(G.find_key([{"segment": "GLOBAL_INDEX", "instrument_key": "GLOBAL_INDEX|DAX"}]))

    def test_quote_change_gap_and_throttle(self):
        self.c.quotes = {"GLOBAL_INDEX:SGX NIFTY": {"ltp": 26120.5, "net_change": 45.0}}   # other key form
        self.assertTrue(self.g.poll(self.c, "2026-11-26", self.logs.append))
        self.assertEqual(self.g.ltp, 26120.5)
        self.assertEqual(self.g.change(), (45.0, round(45 / 26075.5 * 100, 2)))
        self.assertEqual(self.g.gap(26050.5), 70.0)
        self.assertFalse(self.g.poll(self.c, "2026-11-26"))         # within 30 s: no call
        self.assertEqual(self.c.calls, 1)
        self.clock.t += 30
        self.assertTrue(self.g.poll(self.c, "2026-11-26"))
        self.assertEqual(self.c.calls, 2)

    def test_stale_after_5_min_without_change(self):
        self.c.quotes = {KEY: {"ltp": 26100.0, "net_change": None}}
        self.g.poll(self.c, "d")
        self.assertFalse(self.g.stale())
        self.assertIsNone(self.g.change())                          # no net change: LTP only
        for _ in range(11):
            self.clock.t += 30
            self.g.poll(self.c, "d")
        self.assertTrue(self.g.stale())
        self.c.quotes = {KEY: {"ltp": 26101.0, "net_change": 1.0}}
        self.clock.t += 30
        self.g.poll(self.c, "d")
        self.assertFalse(self.g.stale())

    def test_failure_shows_dashes_and_logs_once(self):
        self.g.poll(self.c, "d", self.logs.append)
        self.assertIsNone(self.g.ltp)
        self.clock.t += 30
        self.g.poll(self.c, "d", self.logs.append)
        self.assertEqual(sum("unavailable" in m for m in self.logs), 1)
        self.c.ltps = {KEY: 26000.0}                                # LTP call works when the full quote doesn't
        self.clock.t += 30
        self.g.poll(self.c, "d", self.logs.append)
        self.assertEqual(self.g.ltp, 26000.0)
        self.assertIn("GIFT NIFTY available again", self.logs)

    def test_key_reloaded_once_a_day(self):
        loads = []
        self.g.key_loader = lambda: (loads.append(1), (KEY, "offline"))[1]
        self.c.quotes = {KEY: {"ltp": 1.0}}
        self.g.poll(self.c, "2026-11-26", self.logs.append)
        self.clock.t += 30
        self.g.poll(self.c, "2026-11-26")
        self.clock.t += 30
        self.g.poll(self.c, "2026-11-27")
        self.assertEqual(len(loads), 2)
        self.assertIn("(default; offline)", self.logs[0])


if __name__ == "__main__":
    unittest.main()
