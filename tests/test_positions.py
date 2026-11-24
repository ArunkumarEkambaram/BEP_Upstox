"""Unit tests for positions.py (PR 3) with the paper broker and an 'assumed' (sandbox-like) broker."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import positions as P                         # noqa: E402
from brokers import base as bk                # noqa: E402
from brokers.paper import PaperBroker         # noqa: E402

IK = "NSE_FO|1"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def start(plan, ltp=152.0, broker=None, clock=None):
    b = broker or PaperBroker()
    pm = P.PositionManager(b, sl_limit_gap=5, clock=clock or Clock())
    st = P.new_state(b.mode, "BEP-1", IK, 65, plan, ltp)
    res = pm.place_entry(st, bk.OrderRequest(IK, bk.BUY, 65, tag="BEP-1", ref_price=ltp), "09:16:00")
    return b, pm, st, res


def tick(pm, st, ltp, eod=False, t="09:16:02"):
    return pm.tick([(1, st, IK, ltp)], t, eod)


def kinds(events):
    return [e["kind"] for e in events]


class Levels(unittest.TestCase):
    def test_sl_trigger(self):
        self.assertEqual(P.sl_trigger({"sl": {"type": "POINT", "value": 17}}, 152), 135.0)
        self.assertEqual(P.sl_trigger({"sl": {"type": "LTP", "value": 140}}, 152), 140.0)
        self.assertEqual(P.sl_trigger({"sl": None, "max_sl": 17}, 152), 135.0)
        self.assertIsNone(P.sl_trigger({"sl": None, "max_sl": None}, 152))

    def test_trail_level_decision_4(self):
        tr = {"trigger": 20, "gap": 20, "step": 1}
        self.assertIsNone(P.trail_level(tr, 178, 197))
        self.assertEqual(P.trail_level(tr, 178, 198), 178.0)
        self.assertEqual(P.trail_level(tr, 178, 210), 190.0)

    def test_target(self):
        self.assertEqual(P.target_level({"tgt": {"type": "POINT", "value": 50}}, 152), 202.0)
        self.assertIsNone(P.target_level({"tgt": None}, 152))


class PaperFlow(unittest.TestCase):
    def test_fill_then_exchange_sl(self):
        b, pm, st, res = start({"sl": {"type": "POINT", "value": 17}, "tgt": None, "trail": None})
        self.assertTrue(res.ok)
        ev = tick(pm, st, 152)
        self.assertEqual(kinds(ev), ["fill", "sl"])
        self.assertEqual((st["fill"], st["sl_trg"]), (152.0, 135.0))
        sl = b.status(st["sl_id"])
        self.assertEqual((sl.side, sl.order_type, sl.status), (bk.SELL, bk.SLM, bk.TRIGGER_PENDING))

    def test_sl_hit_at_exchange(self):
        b, pm, st, _ = start({"sl": {"type": "POINT", "value": 17}})
        tick(pm, st, 152)
        ev = tick(pm, st, 134)                        # paper SL-M fills at the LTP
        self.assertIn("exit", kinds(ev))
        self.assertEqual((st["exit"], st["exit_reason"]), (134.0, "SL"))

    def test_trailing_modifies_sl(self):
        clock = Clock()
        b, pm, st, _ = start({"sl": {"type": "POINT", "value": 12},
                              "trail": {"trigger": 20, "gap": 20, "step": 1}}, ltp=178, clock=clock)
        tick(pm, st, 178)
        self.assertEqual(st["sl_trg"], 166.0)
        clock.t += 3
        tick(pm, st, 198)
        self.assertEqual(st["sl_trg"], 178.0)          # to cost
        self.assertEqual(b.status(st["sl_id"]).trigger_price, 178.0)
        clock.t += 1                                   # within MODIFY_EVERY: no new modify
        tick(pm, st, 210)
        self.assertEqual(st["sl_trg"], 178.0)
        clock.t += 3
        tick(pm, st, 205)                              # high 210 -> 190, never down
        self.assertEqual(st["sl_trg"], 190.0)
        ev = tick(pm, st, 189.5)
        self.assertEqual((st["exit"], st["exit_reason"]), (189.5, "Trailing SL"))
        self.assertIn("exit", kinds(ev))

    def test_target_modifies_sl_to_market(self):
        b, pm, st, _ = start({"sl": {"type": "POINT", "value": 17}, "tgt": {"type": "POINT", "value": 30}})
        tick(pm, st, 152)
        sl_id = st["sl_id"]
        tick(pm, st, 182.5)                            # target: SL order -> MARKET
        self.assertEqual((st["exit_how"], st["exit_id"]), ("modify", sl_id))
        tick(pm, st, 182.5)
        self.assertEqual((st["exit"], st["exit_reason"]), (182.5, "Target"))
        sells = [o for o in b.orders.values() if o["side"] == bk.SELL]
        self.assertEqual(len(sells), 1)                # one sell order only

    def test_request_exit_candle_rule(self):
        b, pm, st, _ = start({"sl": None, "max_sl": 17})
        tick(pm, st, 152)
        self.assertEqual(st["sl_trg"], 135.0)          # CANDLE-only SL: max SL at the exchange
        self.assertTrue(P.PositionManager.request_exit(st, "Candle SL", "09:25:00"))
        self.assertFalse(P.PositionManager.request_exit(st, "again", "09:25:00"))
        tick(pm, st, 147)
        tick(pm, st, 147)
        self.assertEqual((st["exit"], st["exit_reason"]), (147.0, "Candle SL"))

    def test_no_sl_plan_market_exit_at_eod(self):
        b, pm, st, _ = start({"sl": None, "max_sl": None})
        tick(pm, st, 152)
        self.assertEqual(st["sl_id"], "")
        tick(pm, st, 160, eod=True)
        self.assertEqual(st["exit_how"], "sell")
        tick(pm, st, 160)
        self.assertEqual((st["exit"], st["exit_reason"]), (160.0, "15:15"))

    def test_price_already_below_sl(self):
        b, pm, st, _ = start({"sl": {"type": "LTP", "value": 150}}, ltp=152)
        tick(pm, st, 152)                              # fill at 152 (paper), SL 150 placed
        st2 = P.new_state("dry", "BEP-2", IK, 65, {"sl": {"type": "LTP", "value": 150}}, 152)
        pm.place_entry(st2, bk.OrderRequest(IK, bk.BUY, 65, tag="BEP-2", ref_price=152), "09:17:00")
        pm.tick([(2, st2, IK, 149)], "09:17:02")       # paper fill at 152, LTP already 149 <= 150
        self.assertEqual(st2["exit_req"], "SL")

    def test_restart_dry_run_replaces_lost_orders(self):
        b, pm, st, _ = start({"sl": {"type": "POINT", "value": 17}})
        tick(pm, st, 152)
        pm2 = P.PositionManager(PaperBroker(), clock=Clock())     # restart: the paper broker forgot everything
        ev = pm2.tick([(1, st, IK, 150)], "10:00:00")
        self.assertIn("sl", kinds(ev))
        self.assertEqual(st["sl_trg"], 135.0)

    def test_sl_change_by_user(self):
        b, pm, st, _ = start({"sl": {"type": "POINT", "value": 17}})
        tick(pm, st, 152)
        st["plan"]["sl"] = {"type": "LTP", "value": 130}           # lower: allowed for a user change
        pm.reset_sl(st)
        tick(pm, st, 152)
        self.assertEqual(st["sl_trg"], 130.0)
        self.assertEqual(b.status(st["sl_id"]).trigger_price, 130.0)


class FailingSL(PaperBroker):
    """Rejects every SL-M and SL sell (to test the retries and the NO SL alert)."""
    def place(self, req):
        if req.side == bk.SELL and req.order_type in (bk.SL, bk.SLM):
            return bk.OrderResult(False, message="rejected for the test")
        return super().place(req)


class Failures(unittest.TestCase):
    def test_sl_retries_fallback_then_alert(self):
        b, pm, st, _ = start({"sl": {"type": "POINT", "value": 17}}, broker=FailingSL())
        tick(pm, st, 152)
        self.assertEqual((st["sl_tries"], st["sl_kind"]), (1, bk.SL))       # next try is an SL limit
        tick(pm, st, 152)
        tick(pm, st, 152)
        ev = tick(pm, st, 152)
        self.assertEqual(st["alert"], "NO SL")
        self.assertIn("alert", kinds(ev))
        tick(pm, st, 152)                              # the alert only once
        self.assertEqual(st["sl_tries"], 3)

    def test_entry_rejected(self):
        class Reject(PaperBroker):
            def place(self, req):
                return bk.OrderResult(False, message="no margin")
        b, pm, st, res = start({"sl": None}, broker=Reject())
        self.assertFalse(res.ok)
        self.assertEqual(st["closed"], "entry not placed")


class Assumed(unittest.TestCase):
    """Like the Upstox sandbox: place / modify / cancel only, no order status."""
    def broker(self):
        class Sandbox(PaperBroker):
            mode = "sandbox"
            capabilities = frozenset({bk.CAP_MODIFY, bk.CAP_CANCEL_BY_ID, bk.CAP_STOP_MARKET})
        return Sandbox()

    def test_assumed_flow(self):
        b, pm, st, _ = start({"sl": {"type": "POINT", "value": 17}, "tgt": {"type": "POINT", "value": 20}},
                             broker=self.broker())
        self.assertEqual(st["fill"], 152.0)            # assumed filled when sent
        tick(pm, st, 152)
        self.assertEqual(st["sl_trg"], 135.0)
        b.last_ltp[IK] = 172                           # (the fake accepts a MARKET modify only with a price)
        tick(pm, st, 172)                              # target: modify to MARKET accepted
        tick(pm, st, 172)                              # -> assumed filled at the LTP
        self.assertEqual((st["exit"], st["exit_reason"]), (172.0, "Target"))

    def test_assumed_sl_hit(self):
        b, pm, st, _ = start({"sl": {"type": "POINT", "value": 17}}, broker=self.broker())
        tick(pm, st, 152)
        tick(pm, st, 134)
        self.assertEqual(st["exit_reason"], "SL")


if __name__ == "__main__":
    unittest.main()


class LimitEntries(unittest.TestCase):
    """PR 4: Limit / Stop-limit entries, partial fills, cancel."""
    def start(self, etype, price=0.0, trig=0.0, broker=None, ltp=135.0, plan=None):
        b = broker or PaperBroker()
        pm = P.PositionManager(b, sl_limit_gap=5, clock=Clock())
        st = P.new_state(b.mode, "BEP-L", IK, 65, plan or {"sl": {"type": "POINT", "value": 17}}, ltp,
                         etype=etype, eprice=price, etrig=trig)
        pm.place_entry(st, bk.OrderRequest(IK, bk.BUY, 65, etype, price=price, trigger_price=trig, tag="BEP-L",
                                           ref_price=ltp), "10:00:00")
        return b, pm, st

    def test_limit_waits_then_fills_and_gets_sl(self):
        b, pm, st = self.start(bk.LIMIT, price=130, ltp=135)
        tick(pm, st, 134)
        self.assertIsNone(st["fill"])
        self.assertEqual(P.entry_text(st), "LIMIT OPEN @ 130.00")
        tick(pm, st, 129.5)
        self.assertEqual((st["fill"], st["sl_trg"]), (129.5, 112.5))
        self.assertFalse(P.entry_open(st))

    def test_stop_limit(self):
        b, pm, st = self.start(bk.SL, price=141, trig=140, ltp=135)
        self.assertEqual(P.entry_text(st), "STOP @ 140.00 → LIMIT 141.00")
        tick(pm, st, 138)
        self.assertIsNone(st["fill"])
        tick(pm, st, 140.5)                         # triggers and fills (140.5 <= 141)
        self.assertEqual(st["fill"], 140.5)
        self.assertEqual(st["sl_trg"], 123.5)

    def test_cancel_unfilled(self):
        b, pm, st = self.start(bk.LIMIT, price=130, ltp=135)
        self.assertTrue(P.PositionManager.request_cancel(st, "Card deleted"))
        ev = tick(pm, st, 134)
        self.assertEqual(st["closed"], "entry cancelled")
        self.assertIn("cancelled", kinds(ev))
        self.assertEqual(b.status(st["entry_id"]).status, bk.CANCELLED)

    def test_eod_cancels_unfilled(self):
        b, pm, st = self.start(bk.SL, price=141, trig=140, ltp=135)
        tick(pm, st, 135, eod=True)
        self.assertEqual(st["closed"], "entry cancelled")

    def test_exit_before_fill_cancels(self):
        b, pm, st = self.start(bk.LIMIT, price=130, ltp=135)
        P.PositionManager.request_exit(st, "Opponent SL", "10:05:00")
        tick(pm, st, 134)
        self.assertEqual(st["closed"], "entry cancelled")
        self.assertEqual([o for o in b.orders.values() if o["side"] == bk.SELL], [])

    def test_partial_fills_grow_the_sl_then_exit_cancels_rest(self):
        class Partial(PaperBroker):
            """The entry fills 65 of 130 first, then the test fills the rest by hand."""
            def place(self, req):
                res = super().place(req)
                o = self.orders[res.order_id]
                if req.side == bk.BUY and req.order_type == bk.LIMIT:
                    o.update(status=bk.OPEN, filled_qty=65, pending_qty=65, avg_price=129.0)
                    o["order_type"] = "X"            # keep on_ltp away from it
                return res
        b = Partial()
        pm = P.PositionManager(b, sl_limit_gap=5, clock=Clock())
        st = P.new_state("dry", "BEP-P", IK, 130, {"sl": {"type": "POINT", "value": 17}}, 135,
                         etype=bk.LIMIT, eprice=130)
        pm.place_entry(st, bk.OrderRequest(IK, bk.BUY, 130, bk.LIMIT, price=130, tag="BEP-P", ref_price=135),
                       "10:00:00")
        tick(pm, st, 131)
        self.assertEqual((st["fill"], st["fill_qty"], st["sl_qty"]), (129.0, 65, 65))
        self.assertTrue(P.entry_open(st))
        self.assertEqual(P.entry_text(st), "FILLED 65 @ 129.00 · PARTIAL")
        e = b.orders[st["entry_id"]]
        e.update(status=bk.COMPLETE, filled_qty=130, pending_qty=0, avg_price=129.5)
        tick(pm, st, 131)
        self.assertEqual((st["fill"], st["fill_qty"], st["sl_qty"]), (129.5, 130, 130))
        self.assertEqual(b.status(st["sl_id"]).quantity, 130)

    def test_partial_then_exit_cancels_the_rest_first(self):
        class Partial(PaperBroker):
            def place(self, req):
                res = super().place(req)
                o = self.orders[res.order_id]
                if req.side == bk.BUY and req.order_type == bk.LIMIT:
                    o.update(status=bk.OPEN, filled_qty=65, pending_qty=65, avg_price=129.0, order_type="X")
                return res
        b = Partial()
        pm = P.PositionManager(b, sl_limit_gap=5, clock=Clock())
        st = P.new_state("dry", "BEP-P", IK, 130, {"sl": {"type": "POINT", "value": 17}}, 135,
                         etype=bk.LIMIT, eprice=130)
        pm.place_entry(st, bk.OrderRequest(IK, bk.BUY, 130, bk.LIMIT, price=130, tag="BEP-P", ref_price=135),
                       "10:00:00")
        tick(pm, st, 131)
        P.PositionManager.request_exit(st, "Manual square-off", "10:01:00")
        tick(pm, st, 131)                            # cancels the rest, then the SL (65) becomes MARKET
        self.assertEqual(b.orders[st["entry_id"]]["status"], bk.CANCELLED)
        tick(pm, st, 131)
        self.assertEqual((st["exit"], st["exit_reason"]), (131.0, "Manual square-off"))
        sells = [o for o in b.orders.values() if o["side"] == bk.SELL]
        self.assertEqual([o["filled_qty"] for o in sells], [65])

    def test_assumed_limit_and_cancel(self):
        class Sandbox(PaperBroker):
            mode = "sandbox"
            capabilities = frozenset({bk.CAP_MODIFY, bk.CAP_CANCEL_BY_ID, bk.CAP_STOP_MARKET})
        b, pm, st = self.start(bk.SL, price=141, trig=140, broker=Sandbox())
        self.assertIsNone(st["fill"])                # not assumed filled when sent
        tick(pm, st, 140.2)                          # assumed trigger + fill
        self.assertEqual(st["fill"], 140.2)
        b2, pm2, st2 = self.start(bk.LIMIT, price=130, broker=Sandbox())
        P.PositionManager.request_cancel(st2, "Cancel")
        tick(pm2, st2, 134)
        self.assertEqual(st2["closed"], "entry cancelled")

    def test_restart_lost_limit_is_placed_again(self):
        b, pm, st = self.start(bk.LIMIT, price=130, ltp=135)
        old = st["entry_id"]
        pm2 = P.PositionManager(PaperBroker(), clock=Clock())
        pm2.tick([(1, st, IK, 134)], "11:00:00")
        self.assertNotEqual(st["entry_id"], old)
        self.assertIsNone(st["fill"])
        pm2.tick([(1, st, IK, 129)], "11:00:02")
        self.assertEqual(st["fill"], 129.0)
