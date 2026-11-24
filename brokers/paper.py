"""
brokers/paper.py - the dry-run broker (BEP Upstox PR 2): same interface as a real broker, nothing is sent.

Orders live in memory:
  MARKET  -> COMPLETE at once at `ref_price` (the LTP when sent; the caller passes it).
  LIMIT   -> OPEN; fills on `on_ltp`: BUY once LTP <= price (at min(price, LTP)), SELL once LTP >= price.
  SL      -> TRIGGER_PENDING; triggers when a BUY sees LTP >= trigger (SELL: LTP <= trigger), then it is a limit.
  SL-M    -> TRIGGER_PENDING; triggers the same way, then fills at the LTP.
`on_ltp(instrument_key, ltp)` moves them on; the engine will call it with the 2 s LTP batch (PR 3).
`exit_by_tag` sells (or buys back) the net filled quantity of that tag's orders at the last LTP.
"""
import itertools
import time
import uuid

from . import base


class PaperBroker:
    name = "Paper"
    mode = "dry"
    capabilities = frozenset({base.CAP_MODIFY, base.CAP_CANCEL_BY_ID, base.CAP_STATUS, base.CAP_EXIT_BY_TAG,
                              base.CAP_STOP_MARKET, base.CAP_ORDER_BOOK})

    def __init__(self):
        self.orders = {}            # order_id -> dict
        self.last_ltp = {}          # instrument_key -> last LTP seen
        self._ids = itertools.count(1)
        # IDs stay unique after an app restart (the orders file keeps the old ones)
        self._run = time.strftime("%H%M%S") + uuid.uuid4().hex[:3]

    def _new_id(self):
        return f"PAPER-{self._run}-{next(self._ids)}"

    @staticmethod
    def _fill(o, price):
        o.update(status=base.COMPLETE, filled_qty=o["quantity"], pending_qty=0, avg_price=round(float(price), 2))

    # ------------------------------------------------------------------ Broker interface
    def place(self, req: base.OrderRequest) -> base.OrderResult:
        bad = base.validate(req)
        if bad:
            return base.OrderResult(False, message=f"not sent: {bad}", sent=req.as_dict())
        oid = self._new_id()
        o = {"order_id": oid, "instrument_key": req.instrument_key, "side": req.side, "quantity": req.quantity,
             "order_type": req.order_type, "price": base.round_tick(req.price) if req.price else 0.0,
             "trigger_price": base.round_tick(req.trigger_price) if req.trigger_price else 0.0, "tag": req.tag,
             "status": base.OPEN, "filled_qty": 0, "pending_qty": req.quantity, "avg_price": 0.0,
             "triggered": False}
        ltp = req.ref_price if req.ref_price is not None else self.last_ltp.get(req.instrument_key)
        if req.order_type == base.MARKET:
            if ltp is None:
                return base.OrderResult(False, message="dry run: no LTP to fill the market order at",
                                        sent=req.as_dict())
            self._fill(o, ltp)
        elif req.order_type in (base.SL, base.SLM):
            o["status"] = base.TRIGGER_PENDING
        self.orders[oid] = o
        if ltp is not None and o["status"] != base.COMPLETE:
            self.on_ltp(req.instrument_key, ltp)
        return base.OrderResult(True, [oid], "dry run: " + o["status"].lower().replace("_", " "),
                                sent=req.as_dict())

    def modify(self, order_id, *, order_type=None, price=None, trigger_price=None, quantity=None):
        o = self.orders.get(order_id)
        if not o:
            return base.OrderResult(False, [order_id], "dry run: order not found")
        if o["status"] in base.FINAL:
            return base.OrderResult(False, [order_id], f"dry run: order already {o['status'].lower()}")
        if order_type:
            o["order_type"] = order_type
        if price is not None:
            o["price"] = base.round_tick(price)
        if trigger_price is not None:
            o["trigger_price"] = base.round_tick(trigger_price)
        if quantity:
            o["quantity"] = o["pending_qty"] = int(quantity)
        if o["order_type"] in (base.MARKET, base.LIMIT):
            o["status"] = base.OPEN
        elif not o["triggered"]:
            o["status"] = base.TRIGGER_PENDING
        ltp = self.last_ltp.get(o["instrument_key"])
        if o["order_type"] == base.MARKET:
            if ltp is None:
                return base.OrderResult(False, [order_id], "dry run: no LTP to fill the market order at")
            self._fill(o, ltp)
        elif ltp is not None:
            self.on_ltp(o["instrument_key"], ltp)
        return base.OrderResult(True, [order_id], "dry run: modified")

    def cancel(self, order_id):
        o = self.orders.get(order_id)
        if not o:
            return base.OrderResult(False, [order_id], "dry run: order not found")
        if o["status"] in base.FINAL:
            return base.OrderResult(False, [order_id], f"dry run: order already {o['status'].lower()}")
        o["status"] = base.CANCELLED
        return base.OrderResult(True, [order_id], "dry run: cancelled")

    def status(self, order_id):
        o = self.orders.get(order_id)
        if not o:
            return base.OrderStatus(order_id, base.UNKNOWN, message="dry run: order not found")
        return base.OrderStatus(order_id, o["status"], o["order_type"], o["side"], o["quantity"], o["filled_qty"],
                                o["pending_qty"], o["avg_price"], o["price"], o["trigger_price"], o["tag"],
                                "dry run")

    def order_book(self):
        return [self.status(oid) for oid in self.orders]

    def exit_by_tag(self, tag):
        if not tag:
            return base.OrderResult(False, message="not sent: no tag")
        net = {}
        for o in self.orders.values():
            if o["tag"] == tag and o["filled_qty"]:
                sign = 1 if o["side"] == base.BUY else -1
                net[o["instrument_key"]] = net.get(o["instrument_key"], 0) + sign * o["filled_qty"]
        ids = []
        for key, qty in net.items():
            if not qty:
                continue
            ltp = self.last_ltp.get(key)
            if ltp is None:
                return base.OrderResult(False, ids, f"dry run: no LTP for {key} to exit at")
            res = self.place(base.OrderRequest(key, base.SELL if qty > 0 else base.BUY, abs(qty), base.MARKET,
                                               tag=tag, ref_price=ltp))
            ids += res.order_ids
        if not ids:
            return base.OrderResult(False, message=f"dry run: no open position with tag {tag}")
        return base.OrderResult(True, ids, "dry run: exit filled")

    # ------------------------------------------------------------------ price updates
    def on_ltp(self, instrument_key, ltp):
        """Moves this option's open paper orders on with a new LTP. Returns the IDs that filled."""
        if ltp is None:
            return []
        ltp = float(ltp)
        self.last_ltp[instrument_key] = ltp
        filled = []
        for o in self.orders.values():
            if o["instrument_key"] != instrument_key or o["status"] in base.FINAL:
                continue
            buy = o["side"] == base.BUY
            if o["status"] == base.TRIGGER_PENDING:
                hit = ltp >= o["trigger_price"] if buy else ltp <= o["trigger_price"]
                if not hit:
                    continue
                o["triggered"] = True
                if o["order_type"] == base.SLM:
                    self._fill(o, ltp)
                    filled.append(o["order_id"])
                    continue
                o["status"] = base.OPEN                     # SL: now a limit at its price
            if o["status"] == base.OPEN and o["order_type"] in (base.LIMIT, base.SL):
                if buy and ltp <= o["price"]:
                    self._fill(o, min(o["price"], ltp))
                    filled.append(o["order_id"])
                elif not buy and ltp >= o["price"]:
                    self._fill(o, max(o["price"], ltp))
                    filled.append(o["order_id"])
        return filled
