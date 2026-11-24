"""
brokers/upstox.py - Upstox adapter for the Broker interface (BEP Upstox PR 2).

Upstox v3 order API (https://upstox.com/developer/api-documentation/v3/place-order):
  place   POST   {order host}/v3/order/place          -> data.order_ids (more than one when auto-sliced)
  modify  PUT    {order host}/v3/order/modify          (validity, price, order_type, trigger_price required)
  cancel  DELETE {order host}/v3/order/cancel?order_id=
  status  GET    https://api.upstox.com/v2/order/details?order_id=   (live only; not in the sandbox)
  book    GET    https://api.upstox.com/v2/order/retrieve-all         (live only; all of today's orders)
  exit    POST   https://api.upstox.com/v2/order/positions/exit?tag=  (intraday positions of that tag; live only)

Live: order host https://api-hft.upstox.com, the daily access token, and the static IP registered in the user's
Upstox app (else error UDAPI1154). Sandbox: https://api-sandbox.upstox.com with the 30-day sandbox token; only
place / modify / cancel are offered there.

Market and SL-M orders carry `market_protection` (-1 = automatic, 1-25 %): exchange rules reject plain market
orders sent through an API (UDAPI1158).

Retries: a place is NEVER retried (a retry could double the order). Modify and cancel are retried once, only on a
network error (no reply at all).
"""
import requests

from . import base

LIVE_ORDER_HOST = "https://api-hft.upstox.com"
LIVE_HOST = "https://api.upstox.com"
SANDBOX_HOST = "https://api-sandbox.upstox.com"

# Upstox order status names -> normalised statuses (Order Status Appendix of the Upstox docs).
_STATUS = {
    "complete": base.COMPLETE,
    "cancelled": base.CANCELLED,
    "rejected": base.REJECTED,
    "trigger pending": base.TRIGGER_PENDING,
    "open": base.OPEN,
    "open pending": base.OPEN,
    "validation pending": base.SENT,
    "put order req received": base.SENT,
    "modify validation pending": base.OPEN,
    "modify pending": base.OPEN,
    "modified": base.OPEN,
    "not modified": base.OPEN,
    "after market order req received": base.OPEN,
    "cancel pending": base.OPEN,
    "not cancelled": base.OPEN,
}


def map_status(text: str) -> str:
    return _STATUS.get((text or "").strip().lower(), base.UNKNOWN)


class UpstoxBroker:
    name = "Upstox"

    def __init__(self, token_fn, sandbox: bool = False, algo_name: str = "", market_protection: int = -1,
                 timeout: float = 10.0, session=None):
        """`token_fn()` returns the current access token (live: today's token; sandbox: the sandbox token), so a
        new token entered on Setup is used without rebuilding the broker."""
        self._token_fn = token_fn
        self.sandbox = bool(sandbox)
        self.mode = "sandbox" if self.sandbox else "live"
        self.algo_name = (algo_name or "").strip()
        self.market_protection = int(market_protection)
        self.timeout = timeout
        self._http = session or requests
        self._known = {}            # order_id -> last known fields (modify needs them; the sandbox has no status)
        caps = {base.CAP_MODIFY, base.CAP_CANCEL_BY_ID, base.CAP_STOP_MARKET}
        if not self.sandbox:
            caps |= {base.CAP_STATUS, base.CAP_EXIT_BY_TAG, base.CAP_ORDER_BOOK}
        self.capabilities = frozenset(caps)

    # ------------------------------------------------------------------ http
    @property
    def order_host(self):
        return SANDBOX_HOST if self.sandbox else LIVE_ORDER_HOST

    def _headers(self):
        h = {"Authorization": f"Bearer {self._token_fn() or ''}", "Accept": "application/json",
             "Content-Type": "application/json"}
        if self.algo_name:
            h["X-Algo-Name"] = self.algo_name
        return h

    def _call(self, method, url, *, body=None, params=None, retry=False):
        """(ok, data, code, message, raw, uncertain). Network errors give ok False; `uncertain` True when the
        request may have reached Upstox (timeout) - for a place that means: check the order book first."""
        if not self._token_fn():
            return False, None, "", "no token (" + ("sandbox token on Setup" if self.sandbox else
                                                     "Upstox token on Setup") + ")", None, False
        attempts = 2 if retry else 1
        for i in range(attempts):
            try:
                resp = self._http.request(method, url, headers=self._headers(), json=body, params=params,
                                          timeout=self.timeout)
            except requests.Timeout as ex:
                if i + 1 < attempts:
                    continue
                return False, None, "", f"no reply from Upstox ({ex.__class__.__name__})", None, True
            except requests.RequestException as ex:
                if i + 1 < attempts:
                    continue
                return False, None, "", f"network error: {ex}", None, False
            try:
                raw = resp.json()
            except ValueError:
                raw = {"text": resp.text[:300]}
            if 200 <= resp.status_code < 300 and raw.get("status", "success") != "error":
                return True, raw.get("data"), "", "", raw, False
            code, msg = self._error(raw, resp.status_code)
            return False, None, code, msg, raw, False
        return False, None, "", "not sent", None, False

    @staticmethod
    def _error(raw, http_status):
        errs = (raw or {}).get("errors") or []
        if errs and isinstance(errs, list) and isinstance(errs[0], dict):
            e = errs[0]
            code = e.get("errorCode") or e.get("error_code") or ""
            msg = e.get("message") or str(e)
            if code == "UDAPI1154":
                msg += " - register this computer's / server's static IP in your Upstox app"
            return code, msg
        return "", f"HTTP {http_status}: {str(raw)[:200]}"

    def _mp(self, req_mp):
        return self.market_protection if req_mp is None else int(req_mp)

    # ------------------------------------------------------------------ Broker interface
    def place(self, req: base.OrderRequest) -> base.OrderResult:
        bad = base.validate(req)
        if bad:
            return base.OrderResult(False, message=f"not sent: {bad}", sent=req.as_dict())
        body = {
            "quantity": int(req.quantity),
            "product": req.product,
            "validity": "DAY",
            "price": base.round_tick(req.price) if req.order_type in (base.LIMIT, base.SL) else 0,
            "tag": req.tag or None,
            "instrument_token": req.instrument_key,
            "order_type": req.order_type,
            "transaction_type": req.side,
            "disclosed_quantity": 0,
            "trigger_price": base.round_tick(req.trigger_price) if req.order_type in (base.SL, base.SLM) else 0,
            "is_amo": False,
            "slice": True,
            "market_protection": self._mp(req.market_protection),
        }
        if not body["tag"]:
            del body["tag"]
        ok, data, code, msg, raw, uncertain = self._call("POST", f"{self.order_host}/v3/order/place", body=body)
        if not ok:
            if uncertain:
                msg += " - the order MAY have been placed: check the Upstox order book before sending it again"
            return base.OrderResult(False, message=msg, code=code, raw=raw, sent=body, uncertain=uncertain)
        ids = [str(i) for i in (data or {}).get("order_ids") or []]
        for oid in ids:
            self._known[oid] = {"order_type": body["order_type"], "price": body["price"],
                                "trigger_price": body["trigger_price"], "quantity": body["quantity"],
                                "side": req.side, "tag": req.tag}
        return base.OrderResult(True, ids, "placed", raw=raw, sent=body)

    def modify(self, order_id, *, order_type=None, price=None, trigger_price=None, quantity=None):
        known = dict(self._known.get(order_id) or {})
        if not known and base.CAP_STATUS in self.capabilities:
            st = self.status(order_id)
            if st.status != base.UNKNOWN:
                known = {"order_type": st.order_type, "price": st.price, "trigger_price": st.trigger_price,
                         "quantity": st.quantity}
        otype = order_type or known.get("order_type")
        if otype not in base.ORDER_TYPES:
            return base.OrderResult(False, message="not sent: the order type is not known - give it")
        px = price if price is not None else known.get("price", 0)
        trg = trigger_price if trigger_price is not None else known.get("trigger_price", 0)
        if otype in (base.LIMIT, base.SL) and not (px and px > 0):
            return base.OrderResult(False, message=f"not sent: {otype} needs a price above 0")
        if otype in (base.SL, base.SLM) and not (trg and trg > 0):
            return base.OrderResult(False, message=f"not sent: {otype} needs a trigger price above 0")
        body = {
            "validity": "DAY",
            "price": base.round_tick(px) if otype in (base.LIMIT, base.SL) else 0,
            "order_id": order_id,
            "order_type": otype,
            "trigger_price": base.round_tick(trg) if otype in (base.SL, base.SLM) else 0,
            "market_protection": self.market_protection,
        }
        if quantity:
            body["quantity"] = int(quantity)
        ok, data, code, msg, raw, _unc = self._call("PUT", f"{self.order_host}/v3/order/modify", body=body,
                                                    retry=True)
        if not ok:
            return base.OrderResult(False, [order_id], msg, code, raw, body)
        known.update(order_type=otype, price=body["price"], trigger_price=body["trigger_price"])
        if quantity:
            known["quantity"] = int(quantity)
        self._known[order_id] = known
        return base.OrderResult(True, [str((data or {}).get("order_id") or order_id)], "modified", raw=raw, sent=body)

    def cancel(self, order_id):
        ok, data, code, msg, raw, _unc = self._call("DELETE", f"{self.order_host}/v3/order/cancel",
                                                    params={"order_id": order_id}, retry=True)
        if not ok:
            return base.OrderResult(False, [order_id], msg, code, raw, {"order_id": order_id})
        return base.OrderResult(True, [str((data or {}).get("order_id") or order_id)], "cancel sent", raw=raw,
                                sent={"order_id": order_id})

    def status(self, order_id):
        if base.CAP_STATUS not in self.capabilities:
            return base.OrderStatus(order_id, base.UNKNOWN, message="the Upstox sandbox has no order status")
        ok, d, code, msg, raw, _unc = self._call("GET", f"{LIVE_HOST}/v2/order/details",
                                                 params={"order_id": order_id}, retry=True)
        if not ok or not isinstance(d, dict):
            return base.OrderStatus(order_id, base.UNKNOWN, message=msg or "no data", raw=raw)
        return self._to_status(d, order_id)

    def order_book(self):
        """[OrderStatus] of today's orders (one call), or None when not offered / not readable."""
        if base.CAP_ORDER_BOOK not in self.capabilities:
            return None
        ok, data, code, msg, raw, _unc = self._call("GET", f"{LIVE_HOST}/v2/order/retrieve-all", retry=True)
        if not ok or not isinstance(data, list):
            return None
        return [self._to_status(d) for d in data if isinstance(d, dict) and d.get("order_id")]

    @staticmethod
    def _to_status(d, order_id=""):
        return base.OrderStatus(
            order_id=str(d.get("order_id") or order_id), status=map_status(d.get("status")),
            order_type=d.get("order_type") or "", side=d.get("transaction_type") or "",
            quantity=int(d.get("quantity") or 0), filled_qty=int(d.get("filled_quantity") or 0),
            pending_qty=int(d.get("pending_quantity") or 0), avg_price=float(d.get("average_price") or 0),
            price=float(d.get("price") or 0), trigger_price=float(d.get("trigger_price") or 0),
            tag=d.get("tag") or "", message=d.get("status_message") or d.get("status") or "", raw=d)

    def exit_by_tag(self, tag):
        if base.CAP_EXIT_BY_TAG not in self.capabilities:
            return base.OrderResult(False, message="exit by tag is not offered in the Upstox sandbox")
        if not tag:
            return base.OrderResult(False, message="not sent: no tag")
        ok, data, code, msg, raw, uncertain = self._call("POST", f"{LIVE_HOST}/v2/order/positions/exit",
                                                         params={"tag": tag})
        if not ok:
            if uncertain:
                msg += " - the exit MAY have been sent: check the Upstox positions"
            return base.OrderResult(False, message=msg, code=code, raw=raw, sent={"tag": tag}, uncertain=uncertain)
        return base.OrderResult(True, [str(i) for i in (data or {}).get("order_ids") or []], "exit sent", raw=raw,
                                sent={"tag": tag})
