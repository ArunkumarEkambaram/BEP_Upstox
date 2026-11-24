"""Max loss per day (Dry run only for now; the 09:15 order is exempt)."""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import engine as engine_mod  # noqa: E402


def _engine(rows):
    e = engine_mod.Engine.__new__(engine_mod.Engine)
    e.order_log = rows
    e.option_ltp = {}
    return e


class MaxLossTest(unittest.TestCase):
    def setUp(self):
        self.name = next(iter(config.AUTO_ORDER))
        self.saved = (config.AUTO_ORDER_DRY_RUN, config.AUTO_ORDER[self.name].get("max_loss_day"))
        config.AUTO_ORDER_DRY_RUN = True
        config.AUTO_ORDER[self.name]["max_loss_day"] = 2000

    def tearDown(self):
        config.AUTO_ORDER_DRY_RUN, config.AUTO_ORDER[self.name]["max_loss_day"] = self.saved

    def test_default_is_2000(self):
        self.assertEqual(config._default_auto_order()["max_loss_day"], 2000)

    def test_closed_loss_counts_dry_run_only(self):
        e = _engine([{"status": "DRY RUN", "pnl": -1500.0}, {"status": "DRY RUN", "pnl": -600.0},
                     {"status": "SENT", "pnl": -5000.0}, {"status": "BLOCKED"}])
        e.open_pnl = lambda r: None
        m = e.max_loss_state(self.name)
        self.assertEqual(m["loss"], 2100.0)
        self.assertTrue(m["hit"])

    def test_open_pnl_counts(self):
        e = _engine([{"status": "DRY RUN", "pnl": -1500.0}, {"status": "DRY RUN", "id": 2}])
        e.open_pnl = lambda r: (-10.0, -650.0) if r.get("id") == 2 else None
        self.assertTrue(e.max_loss_state(self.name)["hit"])
        e.open_pnl = lambda r: (5.0, 325.0) if r.get("id") == 2 else None
        m = e.max_loss_state(self.name)
        self.assertEqual(m["loss"], 1175.0)
        self.assertFalse(m["hit"])

    def test_off_and_not_dry(self):
        e = _engine([{"status": "DRY RUN", "pnl": -3000.0}])
        e.open_pnl = lambda r: None
        config.AUTO_ORDER[self.name]["max_loss_day"] = 0
        self.assertFalse(e.max_loss_state(self.name)["hit"])
        config.AUTO_ORDER[self.name]["max_loss_day"] = 2000
        config.AUTO_ORDER_DRY_RUN = False
        self.assertFalse(e.max_loss_state(self.name)["hit"])

    def test_place_entry_blocked_but_opening_exempt(self):
        e = _engine([{"status": "DRY RUN", "pnl": -2500.0}])
        e.open_pnl = lambda r: None
        sig = {"index": self.name, "opt": "CE", "strike": 26000, "ltp": 150.0, "kind": "star", "params": {},
               "kind_label": "Manual — ⭐ support"}
        e._order_plan = lambda s: {}
        e._payload = lambda *a: {}
        e._tag_seq = 0
        e.broker = type("B", (), {"name": "Paper"})()
        res = asyncio.run(e.place_entry(dict(sig)))
        self.assertEqual(res["status"], "blocked")
        self.assertIn("Max loss per day", res["detail"])

        async def no_key(*a):
            return None, "stop here"
        e.option_key = no_key
        res = asyncio.run(e.place_entry(dict(sig, kind="opening")))      # 09:15: not stopped by the max loss
        self.assertEqual(res["status"], "failed")
        res = asyncio.run(e.place_entry(dict(sig, loss_ok=True)))        # 'Place anyway' at the click
        self.assertEqual(res["status"], "failed")


if __name__ == "__main__":
    unittest.main()
