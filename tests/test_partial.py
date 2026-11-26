"""Unit tests for the partial exit (user, 26 Nov): sell some lots now, the exchange SL is lowered first."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import positions as P                         # noqa: E402
from brokers import base as bk                # noqa: E402
from brokers.paper import PaperBroker         # noqa: E402

IK = "NSE_FO|1"
LOT = 65


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def start(plan, lots=3, ltp=152.0, broker=None):
    b = broker or PaperBroker()
    pm = P.PositionManager(b, sl_limit_gap=5, clock=Clock())
    st = P.new_state(b.mode, "BEP-1", IK, lots * LOT, plan, ltp)
    pm.place_entry(st, bk.OrderRequest(IK, bk.BUY, lots * LOT, tag="BEP-1", ref_price=ltp), "09:16:00")
    return b, pm, st


def tick(pm, st, ltp, t="09:16:02", eod=False):
    return pm.tick([(1, st, IK, ltp)], t, eod)


def kinds(events):
    return [e["kind"] for e in events]


class Partial(unittest.TestCase):
    def test_validation(self):
        b, pm, st = start({"sl": {"type": "POINT", "value": 17}})
        self.assertIn("not open", P.PositionManager.request_partial(st, LOT, "09:16:01"))   # not filled yet
        tick(pm, st, 152)
        self.assertIn("fewer units", P.PositionManager.request_partial(st, 3 * LOT, "09:16:03"))
        self.assertIn("fewer units", P.PositionManager.request_partial(st, 0, "09:16:03"))
        self.assertEqual(P.PositionManager.request_partial(st, LOT, "09:16:03"), "")
        self.assertIn("already running", P.PositionManager.request_partial(st, LOT, "09:16:03"))

    def test_sl_lowered_first_then_sell_then_target_on_the_rest(self):
        b, pm, st = start({"sl": {"type": "POINT", "value": 17}, "tgt": {"type": "POINT", "value": 50}})
        tick(pm, st, 152)
        self.assertEqual((st["sl_qty"], b.status(st["sl_id"]).quantity), (3 * LOT, 3 * LOT))
        P.PositionManager.request_partial(st, LOT, "09:17:00")
        tick(pm, st, 170, "09:17:02")                  # SL quantity -> 130, then SELL 65 sent
        self.assertEqual((st["sl_qty"], b.status(st["sl_id"]).quantity), (2 * LOT, 2 * LOT))
        self.assertTrue(st["part_id"])
        ev = tick(pm, st, 170, "09:17:04")             # the paper broker fills the SELL at the LTP
        self.assertIn("partial", kinds(ev))
        e = next(x for x in ev if x["kind"] == "partial")
        self.assertEqual((e["qty"], e["price"], e["left"]), (LOT, 170.0, 2 * LOT))
        self.assertEqual((P.held(st), st["out_qty"]), (2 * LOT, LOT))
        self.assertEqual(P.parts_pnl(st), (170 - 152) * LOT)
        tick(pm, st, 202, "09:20:00")                  # target 202: the SL (130 units) becomes MARKET
        tick(pm, st, 202, "09:20:02")
        self.assertEqual((st["exit"], st["exit_reason"]), (202.0, "Target"))
        self.assertEqual(b.status(st["exit_id"]).quantity, 2 * LOT)   # only what was held
        self.assertEqual(P.avg_exit(st), round((170 * LOT + 202 * 2 * LOT) / (3 * LOT), 2))

    def test_sl_hit_after_partial_sells_only_the_rest(self):
        b, pm, st = start({"sl": {"type": "POINT", "value": 17}})
        tick(pm, st, 152)
        P.PositionManager.request_partial(st, 2 * LOT, "09:17:00")
        tick(pm, st, 150, "09:17:02")
        tick(pm, st, 150, "09:17:04")
        self.assertEqual(P.held(st), LOT)
        tick(pm, st, 134, "09:18:00")                  # SL-M 135 for 65 units fills
        self.assertEqual((st["exit_reason"], b.status(st["sl_id"]).filled_qty), ("SL", LOT))

    def test_rejected_sell_puts_the_sl_quantity_back(self):
        class RejectSell(PaperBroker):
            def place(self, req):
                if req.side == bk.SELL and req.order_type == bk.MARKET:
                    return bk.OrderResult(True, order_ids=["X-1"])       # accepted, then rejected in the book
                return super().place(req)

            def order_book(self):
                rows = super().order_book() or []
                return rows + [bk.OrderStatus("X-1", bk.REJECTED, bk.MARKET, bk.SELL, LOT, 0, 0.0, "RMS: test")]
        b, pm, st = start({"sl": {"type": "POINT", "value": 17}}, broker=RejectSell())
        tick(pm, st, 152)
        P.PositionManager.request_partial(st, LOT, "09:17:00")
        tick(pm, st, 160, "09:17:02")
        self.assertEqual(st["sl_qty"], 2 * LOT)
        ev = tick(pm, st, 160, "09:17:04")             # rejected: alert, nothing sold
        self.assertIn("alert", kinds(ev))
        self.assertEqual((st["out_qty"], st["part_id"]), (0, ""))
        tick(pm, st, 160, "09:17:06")                  # the SL goes back to the full quantity
        self.assertEqual((st["sl_qty"], b.status(st["sl_id"]).quantity), (3 * LOT, 3 * LOT))

    def test_sl_modify_failure_blocks_the_sell(self):
        class NoModify(PaperBroker):
            def modify(self, order_id, **kw):
                return bk.OrderResult(False, message="modify refused")
        b, pm, st = start({"sl": {"type": "POINT", "value": 17}}, broker=NoModify())
        tick(pm, st, 152)
        P.PositionManager.request_partial(st, LOT, "09:17:00")
        for i in range(3):
            tick(pm, st, 160, f"09:17:0{2 + i}")
            self.assertEqual(st["part_id"], "")        # never sells while the SL still covers all 3 lots
        ev = tick(pm, st, 160, "09:17:08")
        self.assertIn("alert", kinds(ev))
        self.assertIsNone(st["part_req"])

    def test_assumed_broker(self):
        class Sandbox(PaperBroker):
            mode = "sandbox"
            capabilities = frozenset({bk.CAP_MODIFY, bk.CAP_CANCEL_BY_ID, bk.CAP_STOP_MARKET})
        b, pm, st = start({"sl": {"type": "POINT", "value": 17}}, broker=Sandbox())
        tick(pm, st, 152)
        P.PositionManager.request_partial(st, LOT, "09:17:00")
        b.last_ltp[IK] = 165                           # (the fake fills a MARKET order only with a known LTP)
        ev = tick(pm, st, 165, "09:17:02")
        self.assertIn("partial", kinds(ev))
        self.assertEqual((P.held(st), st["parts"][0]["price"]), (2 * LOT, 165.0))


if __name__ == "__main__":
    unittest.main()
