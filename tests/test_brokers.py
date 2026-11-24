"""Unit tests for brokers/ (PR 2). Run: python -m unittest discover -s tests -v   (nothing is sent anywhere)."""
import os
import sys
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brokers                              # noqa: E402
from brokers import base as bk              # noqa: E402
from brokers.upstox import UpstoxBroker, map_status   # noqa: E402

KEY = "NSE_FO|43919"


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


class FakeSession:
    """Records requests; answers from a queue (a FakeResp, or an exception to raise)."""
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json, "params": params})
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def ok(data):
    return FakeResp(200, {"status": "success", "data": data})


class ValidateTests(unittest.TestCase):
    def test_complete_requests(self):
        self.assertEqual(bk.validate(bk.OrderRequest(KEY, bk.BUY, 65)), "")
        self.assertEqual(bk.validate(bk.OrderRequest(KEY, bk.BUY, 65, bk.SL, price=141, trigger_price=140)), "")
        self.assertEqual(bk.validate(bk.OrderRequest(KEY, bk.SELL, 65, bk.SLM, trigger_price=135)), "")

    def test_bad_requests(self):
        self.assertIn("price", bk.validate(bk.OrderRequest(KEY, bk.BUY, 65, bk.LIMIT)))
        self.assertIn("trigger", bk.validate(bk.OrderRequest(KEY, bk.SELL, 65, bk.SLM)))
        self.assertIn("quantity", bk.validate(bk.OrderRequest(KEY, bk.BUY, 0)))
        self.assertIn("at or below", bk.validate(bk.OrderRequest(KEY, bk.BUY, 65, bk.SL, price=139,
                                                                 trigger_price=140)))
        self.assertIn("at or above", bk.validate(bk.OrderRequest(KEY, bk.SELL, 65, bk.SL, price=136,
                                                                 trigger_price=135)))
        self.assertIn("40", bk.validate(bk.OrderRequest(KEY, bk.BUY, 65, tag="X" * 41)))

    def test_round_tick(self):
        self.assertEqual(bk.round_tick(140.03), 140.05)
        self.assertEqual(bk.round_tick(140.02), 140.0)


class FactoryTests(unittest.TestCase):
    def test_modes(self):
        self.assertIsInstance(brokers.create("dry"), brokers.PaperBroker)
        sb = brokers.create("sandbox", sandbox_token_fn=lambda: "s")
        lv = brokers.create("live", live_token_fn=lambda: "l")
        self.assertEqual((sb.mode, lv.mode), ("sandbox", "live"))
        for b in (brokers.create("dry"), sb, lv):
            self.assertIsInstance(b, bk.Broker)       # matches the interface (Protocol)
        self.assertNotIn(bk.CAP_STATUS, sb.capabilities)
        self.assertIn(bk.CAP_EXIT_BY_TAG, lv.capabilities)
        with self.assertRaises(ValueError):
            brokers.create("live", broker="other")


class UpstoxTests(unittest.TestCase):
    def broker(self, *answers, sandbox=False, algo=""):
        s = FakeSession(*answers)
        return UpstoxBroker(lambda: "TOKEN", sandbox=sandbox, algo_name=algo, market_protection=-1, session=s), s

    def test_place_limit_body_and_hosts(self):
        b, s = self.broker(ok({"order_ids": ["111"]}))
        r = b.place(bk.OrderRequest(KEY, bk.BUY, 65, bk.LIMIT, price=120.53, tag="BEP-1"))
        self.assertTrue(r.ok)
        self.assertEqual(r.order_id, "111")
        c = s.calls[0]
        self.assertEqual((c["method"], c["url"]), ("POST", "https://api-hft.upstox.com/v3/order/place"))
        body = c["json"]
        self.assertEqual((body["order_type"], body["price"], body["trigger_price"], body["quantity"], body["tag"],
                          body["product"], body["validity"], body["market_protection"]),
                         ("LIMIT", 120.55, 0, 65, "BEP-1", "I", "DAY", -1))
        self.assertEqual(c["headers"]["Authorization"], "Bearer TOKEN")
        self.assertNotIn("X-Algo-Name", c["headers"])

    def test_sandbox_host_and_algo_header(self):
        b, s = self.broker(ok({"order_ids": ["1"]}), sandbox=True, algo="BEP")
        b.place(bk.OrderRequest(KEY, bk.BUY, 65, bk.SL, price=141, trigger_price=140))
        self.assertEqual(s.calls[0]["url"], "https://api-sandbox.upstox.com/v3/order/place")
        self.assertEqual(s.calls[0]["headers"]["X-Algo-Name"], "BEP")
        self.assertEqual(s.calls[0]["json"]["trigger_price"], 140.0)

    def test_place_error_code(self):
        b, _ = self.broker(FakeResp(403, {"status": "error", "errors": [
            {"errorCode": "UDAPI1154", "message": "Access to this API is blocked due to static IP restrictions."}]}))
        r = b.place(bk.OrderRequest(KEY, bk.BUY, 65))
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "UDAPI1154")
        self.assertIn("static IP", r.message)

    def test_place_is_never_retried(self):
        b, s = self.broker(requests.Timeout("slow"), ok({"order_ids": ["2"]}))
        r = b.place(bk.OrderRequest(KEY, bk.BUY, 65))
        self.assertFalse(r.ok)
        self.assertTrue(r.uncertain)
        self.assertIn("MAY have been placed", r.message)
        self.assertEqual(len(s.calls), 1)

    def test_invalid_request_not_sent(self):
        b, s = self.broker()
        r = b.place(bk.OrderRequest(KEY, bk.BUY, 65, bk.LIMIT))
        self.assertFalse(r.ok)
        self.assertEqual(s.calls, [])

    def test_modify_uses_known_fields_and_retries_once(self):
        b, s = self.broker(ok({"order_ids": ["9"]}), requests.ConnectionError("x"), ok({"order_id": "9"}))
        b.place(bk.OrderRequest(KEY, bk.SELL, 65, bk.SLM, trigger_price=135))
        r = b.modify("9", trigger_price=148)              # move the SL up: type kept from the place
        self.assertTrue(r.ok)
        self.assertEqual(len(s.calls), 3)
        body = s.calls[2]["json"]
        self.assertEqual((s.calls[2]["method"], body["order_type"], body["trigger_price"], body["price"]),
                         ("PUT", "SL-M", 148.0, 0))

    def test_modify_to_market(self):
        b, s = self.broker(ok({"order_ids": ["9"]}), ok({"order_id": "9"}))
        b.place(bk.OrderRequest(KEY, bk.SELL, 65, bk.SLM, trigger_price=135))
        self.assertTrue(b.modify("9", order_type=bk.MARKET).ok)
        body = s.calls[1]["json"]
        self.assertEqual((body["order_type"], body["price"], body["trigger_price"]), ("MARKET", 0, 0))

    def test_cancel_and_status(self):
        b, s = self.broker(ok({"order_id": "5"}), ok({
            "order_id": "5", "status": "trigger pending", "order_type": "SL", "transaction_type": "BUY",
            "quantity": 65, "filled_quantity": 0, "pending_quantity": 65, "average_price": 0, "price": 141,
            "trigger_price": 140, "tag": "BEP-1", "status_message": None}))
        self.assertTrue(b.cancel("5").ok)
        self.assertEqual((s.calls[0]["method"], s.calls[0]["params"]), ("DELETE", {"order_id": "5"}))
        st = b.status("5")
        self.assertEqual((st.status, st.trigger_price, st.tag), (bk.TRIGGER_PENDING, 140.0, "BEP-1"))
        self.assertEqual(s.calls[1]["url"], "https://api.upstox.com/v2/order/details")

    def test_sandbox_has_no_status_or_exit(self):
        b, s = self.broker(sandbox=True)
        self.assertEqual(b.status("1").status, bk.UNKNOWN)
        self.assertFalse(b.exit_by_tag("BEP-1").ok)
        self.assertEqual(s.calls, [])

    def test_exit_by_tag(self):
        b, s = self.broker(ok({"order_ids": ["77"]}))
        r = b.exit_by_tag("BEP-1")
        self.assertTrue(r.ok)
        self.assertEqual((s.calls[0]["url"], s.calls[0]["params"]),
                         ("https://api.upstox.com/v2/order/positions/exit", {"tag": "BEP-1"}))

    def test_no_token(self):
        b = UpstoxBroker(lambda: "", session=FakeSession())
        r = b.place(bk.OrderRequest(KEY, bk.BUY, 65))
        self.assertFalse(r.ok)
        self.assertIn("no token", r.message)

    def test_status_names(self):
        self.assertEqual(map_status("complete"), bk.COMPLETE)
        self.assertEqual(map_status("Trigger Pending"), bk.TRIGGER_PENDING)
        self.assertEqual(map_status("something new"), bk.UNKNOWN)


class PaperTests(unittest.TestCase):
    def test_market_fills_at_ref_price(self):
        p = brokers.PaperBroker()
        r = p.place(bk.OrderRequest(KEY, bk.BUY, 65, ref_price=152.0))
        st = p.status(r.order_id)
        self.assertEqual((st.status, st.avg_price, st.filled_qty), (bk.COMPLETE, 152.0, 65))

    def test_stop_limit_buy(self):
        p = brokers.PaperBroker()
        r = p.place(bk.OrderRequest(KEY, bk.BUY, 65, bk.SL, price=141, trigger_price=140, ref_price=135))
        self.assertEqual(p.status(r.order_id).status, bk.TRIGGER_PENDING)
        p.on_ltp(KEY, 145)                                 # triggers, but 145 is above the limit: stays open
        self.assertEqual(p.status(r.order_id).status, bk.OPEN)
        self.assertEqual(p.on_ltp(KEY, 140.5), [r.order_id])
        self.assertEqual(p.status(r.order_id).avg_price, 140.5)

    def test_limit_buy_fills_at_min(self):
        p = brokers.PaperBroker()
        r = p.place(bk.OrderRequest(KEY, bk.BUY, 65, bk.LIMIT, price=120, ref_price=125))
        self.assertEqual(p.status(r.order_id).status, bk.OPEN)
        p.on_ltp(KEY, 118)
        self.assertEqual(p.status(r.order_id).avg_price, 118.0)

    def test_sl_sell_trail_and_modify_to_market(self):
        p = brokers.PaperBroker()
        p.place(bk.OrderRequest(KEY, bk.BUY, 65, ref_price=152.0, tag="BEP-1"))
        sl = p.place(bk.OrderRequest(KEY, bk.SELL, 65, bk.SLM, trigger_price=135, tag="BEP-1", ref_price=152))
        p.on_ltp(KEY, 170)
        self.assertTrue(p.modify(sl.order_id, trigger_price=150).ok)       # trail up
        p.on_ltp(KEY, 151)
        self.assertEqual(p.status(sl.order_id).status, bk.TRIGGER_PENDING)
        p.on_ltp(KEY, 149.5)
        st = p.status(sl.order_id)
        self.assertEqual((st.status, st.avg_price), (bk.COMPLETE, 149.5))
        self.assertFalse(p.cancel(sl.order_id).ok)                          # already filled

    def test_target_by_modify_to_market(self):
        p = brokers.PaperBroker()
        p.place(bk.OrderRequest(KEY, bk.BUY, 65, ref_price=152.0))
        sl = p.place(bk.OrderRequest(KEY, bk.SELL, 65, bk.SLM, trigger_price=135, ref_price=152))
        p.on_ltp(KEY, 180)
        self.assertTrue(p.modify(sl.order_id, order_type=bk.MARKET).ok)
        self.assertEqual(p.status(sl.order_id).avg_price, 180.0)

    def test_cancel_and_exit_by_tag(self):
        p = brokers.PaperBroker()
        lim = p.place(bk.OrderRequest(KEY, bk.BUY, 65, bk.LIMIT, price=100, ref_price=110, tag="BEP-2"))
        self.assertTrue(p.cancel(lim.order_id).ok)
        self.assertEqual(p.status(lim.order_id).status, bk.CANCELLED)
        self.assertFalse(p.exit_by_tag("BEP-2").ok)                         # nothing filled
        p.place(bk.OrderRequest(KEY, bk.BUY, 65, ref_price=110, tag="BEP-3"))
        p.on_ltp(KEY, 115)
        r = p.exit_by_tag("BEP-3")
        self.assertTrue(r.ok)
        st = p.status(r.order_id)
        self.assertEqual((st.side, st.avg_price, st.filled_qty), (bk.SELL, 115.0, 65))
        self.assertFalse(p.exit_by_tag("BEP-3").ok)                         # already flat


if __name__ == "__main__":
    unittest.main()
