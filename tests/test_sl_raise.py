"""SL raised to / above the LTP after placing (user, 26 Nov): exit at once instead of moving the exchange SL above
the LTP (the exchange refuses a SELL stop whose trigger is above the LTP); repeated SL modify refusals raise an alert."""
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


def start(plan, ltp=219.35, broker=None):
    b = broker or PaperBroker()
    clock = Clock()
    pm = P.PositionManager(b, sl_limit_gap=5, clock=clock)
    st = P.new_state(b.mode, "BEP-1", IK, LOT, plan, ltp)
    pm.place_entry(st, bk.OrderRequest(IK, bk.BUY, LOT, tag="BEP-1", ref_price=ltp), "09:33:18")
    return b, pm, st, clock


def tick(pm, st, ltp, t="09:40:00"):
    return pm.tick([(1, st, IK, ltp)], t, False)


class RefuseModify(PaperBroker):
    """An exchange that refuses every SL modify while `refuse` is set (e.g. RMS / trigger checks)."""
    refuse = False

    def modify(self, order_id, **kw):
        if self.refuse and kw.get("order_type") != bk.MARKET:
            return bk.OrderResult(False, message="trigger price must be below the LTP")
        return super().modify(order_id, **kw)


class SlRaise(unittest.TestCase):
    def test_sl_raised_above_ltp_exits_now(self):
        b, pm, st, _ = start({"sl": {"type": "POINT", "value": 17}})
        tick(pm, st, 219.35, "09:33:20")
        tick(pm, st, 260, "09:35:00")
        self.assertEqual(st["sl_trg"], 202.35)
        st["plan"]["sl"] = {"type": "LTP", "value": 250}          # the user's change after placing
        P.PositionManager.reset_sl(st)
        tick(pm, st, 244.85, "09:40:00")                         # already through 250: exit, no SL move to 250
        self.assertEqual(st["exit_req"], "SL")
        tick(pm, st, 244.85, "09:40:02")
        self.assertEqual((st["exit"], st["exit_reason"]), (244.85, "SL"))

    def test_sl_raised_below_ltp_moves_the_exchange_sl(self):
        b, pm, st, _ = start({"sl": {"type": "POINT", "value": 17}})
        tick(pm, st, 219.35, "09:33:20")
        st["plan"]["sl"] = {"type": "LTP", "value": 250}
        P.PositionManager.reset_sl(st)
        tick(pm, st, 260, "09:40:00")                            # LTP above 250: the exchange SL moves
        self.assertEqual((st["exit_req"], st["sl_trg"]), ("", 250.0))
        tick(pm, st, 249, "09:41:00")                            # the exchange SL fills
        self.assertEqual(st["exit_reason"], "Trailing SL")       # (an SL above the first one reads as trailed)

    def test_lowered_sl_does_not_exit(self):
        b, pm, st, _ = start({"sl": {"type": "LTP", "value": 210}})
        tick(pm, st, 219.35, "09:33:20")
        st["plan"]["sl"] = {"type": "LTP", "value": 200}
        P.PositionManager.reset_sl(st)
        tick(pm, st, 215, "09:40:00")
        self.assertEqual((st["exit_req"], st["sl_trg"]), ("", 200.0))

    def test_refused_modify_alerts_after_three_tries(self):
        b = RefuseModify()
        b, pm, st, clock = start({"sl": {"type": "POINT", "value": 17},
                                  "trail": {"trigger": 10, "gap": 5, "step": 1}}, broker=b)
        tick(pm, st, 219.35, "09:33:20")
        b.refuse = True                                          # every modify refused from now on
        events = []
        for i in range(3):
            clock.t += 3
            events += tick(pm, st, 240 + i, f"09:40:0{i}")      # the trail wants to move the SL up
        self.assertEqual(st["alert"], P.SL_MOD_ALERT)
        self.assertEqual(sum(e["kind"] == "alert" for e in events), 1)
        b.refuse = False                                         # modifies accepted again
        clock.t += 3
        tick(pm, st, 245, "09:40:09")
        self.assertEqual((st["alert"], st["sl_mod_fails"]), ("", 0))


if __name__ == "__main__":
    unittest.main()
