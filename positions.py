"""
positions.py - broker-held positions (BEP Upstox PR 3).

One position = one entry order of the app (09:15 order, Same strike / Pair / BEP Support / Manual cards). Its
state is a plain dict ("bk", saved as JSON in the orders file), so it survives a restart. Every 2 s the engine
calls PositionManager.tick, which reads the broker's order book once and moves each position on:

  entry  MARKET BUY (tag BEP-...) -> filled (avg price, quantity)
  SL     right after the fill: an SL-M SELL at the exchange (CLAUDE.md BEP Upstox decision 1). Its trigger:
         LTP rule = the value, POINT rule = fill - points, CANDLE-only SL = fill - the "max SL" (Setup).
         If placing it fails it is retried (SL_TRIES); after the first SL-M failure the next try is an SL limit
         (trigger, limit = trigger - gap). After SL_TRIES failures: alert "NO SL" (the user decides).
  trail  decision 4's rule (high >= entry + trigger -> SL = high - gap in steps of point, never down): the app
         MODIFIES the SL order's trigger, at most once per MODIFY_EVERY seconds.
  exit   target (LTP / POINT on the live LTP), a CANDLE rule (engine), the Pair opponent, a manual square-off or
         15:15: the SL order is MODIFIED to MARKET (one sell order, no double sell / short). No SL order: a
         MARKET SELL. The SL order filling on its own = exit "SL" / "Trailing SL".

Brokers without an order book or status call (the Upstox sandbox) are "assumed": the entry counts as filled at
the LTP when sent, an exit as filled at the LTP once its modify / sell was accepted, and the SL as hit when the
LTP falls to its trigger. The paper broker (Dry run) is moved on with the LTP and loses its orders on a restart;
a lost entry counts as filled at its saved price and a lost SL order is placed again.

No NiceGUI here: the engine runs tick() on a worker thread and applies the returned events.
"""
import time

from brokers import base as bk

SL_TRIES = 3              # attempts to place the exchange SL after a fill, then "NO SL"
EXIT_TRIES = 3            # attempts to get an exit order accepted, then an alert
MODIFY_EVERY = 2.0        # seconds between two trailing modifies of one SL order


def new_state(mode: str, tag: str, ik: str, units: int, plan: dict, ref_price, kind: str = "",
              etype: str = bk.MARKET, eprice: float = 0.0, etrig: float = 0.0) -> dict:
    """plan = {"sl": {"type": "LTP"|"POINT", "value"} | None, "max_sl": points | None,
               "tgt": {"type": "LTP"|"POINT", "value"} | None, "trail": {"trigger", "gap", "step"} | None}
    etype = the entry order: MARKET, LIMIT (eprice) or SL = stop-limit BUY (etrig -> limit eprice), PR 4."""
    return {"mode": mode, "tag": tag, "ik": ik, "units": int(units), "plan": plan, "ref": ref_price, "kind": kind,
            "etype": etype, "eprice": float(eprice or 0), "etrig": float(etrig or 0), "triggered": False,
            "cancel_req": "", "cancel_sent": 0, "sl_qty": 0,
            "entry_id": "", "entry_st": "", "fill": None, "fill_qty": 0, "fill_time": "",
            "sl_id": "", "sl_st": "", "sl_trg": None, "sl_first": None, "sl_kind": bk.SLM, "sl_tries": 0,
            "sl_reset": False, "high": None, "trail_at": 0.0,
            "exit_req": "", "exit_req_time": "", "exit_id": "", "exit_how": "", "exit_tries": 0,
            "exit": None, "exit_time": "", "exit_reason": "", "closed": "", "alert": ""}


def sl_trigger(plan: dict, fill):
    """The exchange SL trigger for a fill (None = no SL order)."""
    if fill is None:
        return None
    sl = plan.get("sl")
    if sl and sl.get("value") is not None:
        v = float(sl["value"])
        trg = v if sl.get("type") == "LTP" else fill - v
    elif plan.get("max_sl"):
        trg = fill - float(plan["max_sl"])
    else:
        return None
    return bk.round_tick(trg) if trg > 0 else None


def target_level(plan: dict, fill):
    tgt = plan.get("tgt")
    if fill is None or not tgt or tgt.get("value") is None:
        return None
    v = float(tgt["value"])
    return round(v if tgt.get("type") == "LTP" else fill + v, 2)


def trail_level(trail: dict, entry, high):
    """Decision 4: None until the high reaches entry + trigger; then entry + whole steps of (high - gap - entry)."""
    if not trail or entry is None or high is None or high < entry + float(trail["trigger"]):
        return None
    step = float(trail.get("step") or 1)
    return bk.round_tick(entry + int((high - float(trail["gap"]) - entry) // step) * step)


def entry_open(st) -> bool:
    """The entry order can still (partly) fill: sent, not complete / cancelled / rejected."""
    return bool(st) and bool(st.get("entry_id")) and not st.get("closed") and st.get("entry_st") not in bk.FINAL


def entry_text(st) -> str:
    """Chip text of an unfilled / partly filled Limit or Stop-limit entry ('' for a market entry)."""
    et = st.get("etype", bk.MARKET)
    if et == bk.MARKET or not entry_open(st):
        return ""
    if st.get("fill") is not None:
        return f"FILLED {st['fill_qty']} @ {st['fill']:.2f} · PARTIAL"
    if st.get("cancel_req"):
        return "CANCELLING"
    if et == bk.SL and not st.get("triggered"):
        return f"STOP @ {st['etrig']:.2f} → LIMIT {st['eprice']:.2f}"
    return f"LIMIT OPEN @ {st['eprice']:.2f}"


def is_open(st) -> bool:
    return bool(st) and st.get("fill") is not None and not st.get("closed")


def summary(st) -> str:
    """Short text for the card / Order Details, e.g. 'SL-M 135.00 at the exchange'."""
    if not st:
        return ""
    if st.get("closed"):
        return st.get("exit_reason") or st["closed"]
    if st.get("fill") is None:
        return "entry " + (st.get("entry_st") or "sending").lower().replace("_", " ")
    if st.get("exit_req"):
        return f"exiting · {st['exit_req']}"
    if st.get("alert"):
        return st["alert"]
    if st.get("sl_id") and st.get("sl_trg") is not None:
        return f"{st.get('sl_kind', bk.SLM)} {st['sl_trg']:.2f} at the exchange"
    return "no exchange SL"


class PositionManager:
    def __init__(self, broker, sl_limit_gap: float = 5.0, clock=time.time):
        self.broker = broker
        self.gap = float(sl_limit_gap)
        self.clock = clock
        caps = broker.capabilities
        self.assumed = bk.CAP_ORDER_BOOK not in caps and bk.CAP_STATUS not in caps
        self.paper = broker.mode == "dry"

    # ------------------------------------------------------------------ actions called by the engine
    def place_entry(self, st: dict, req: bk.OrderRequest, now_text: str) -> bk.OrderResult:
        res = self.broker.place(req)
        if res.ok:
            st["entry_id"], st["entry_st"] = res.order_id, bk.SENT
            if self.assumed and st.get("etype", bk.MARKET) == bk.MARKET:
                self._filled(st, req.ref_price, req.quantity, now_text, [])
        else:
            st["entry_st"], st["closed"] = bk.REJECTED, "entry not placed"
        return res

    @staticmethod
    def request_exit(st: dict, reason: str, now_text: str) -> bool:
        """Marks an exit (done on the next tick). Before a fill it cancels the entry instead."""
        if not st or st.get("closed") or st.get("exit_req"):
            return False
        st["exit_req"], st["exit_req_time"] = reason, now_text
        return True

    @staticmethod
    def request_cancel(st: dict, reason: str) -> bool:
        """Cancels the unfilled entry (or the unfilled rest of a partly filled limit); a filled part stays,
        protected by its exchange SL."""
        if not entry_open(st) or st.get("cancel_req"):
            return False
        st["cancel_req"] = reason
        return True

    @staticmethod
    def reset_sl(st: dict):
        """The SL rule was changed after placing: the next tick moves (or places) the exchange SL."""
        st["sl_reset"] = True

    # ------------------------------------------------------------------ the 2 s step
    def tick(self, items: list, now_text: str, eod: bool = False) -> list:
        """items = [(key, st, instrument_key, ltp)]. Returns events: {"key", "kind", ...} with kind
        fill / exit / failed / alert / log."""
        events = []
        if self.paper:
            for _k, _st, ik, ltp in items:
                if ik and ltp is not None:
                    self.broker.on_ltp(ik, ltp)
        book = self._book(items)
        for key, st, ik, ltp in items:
            if st.get("closed"):
                continue
            try:
                self._step(key, st, ltp, book, now_text, eod, events)
            except Exception as ex:                       # one bad position must not stop the others
                events.append({"key": key, "kind": "log", "text": f"position error: {ex}"})
        return events

    def _book(self, items):
        if self.assumed:
            return {}
        caps = self.broker.capabilities
        if bk.CAP_ORDER_BOOK in caps:
            rows = self.broker.order_book()
            if rows is not None:
                return {s.order_id: s for s in rows}
        out = {}
        if bk.CAP_STATUS in caps:
            for _k, st, _ik, _l in items:
                for oid in (st.get("entry_id"), st.get("sl_id"), st.get("exit_id")):
                    if oid and oid not in out:
                        out[oid] = self.broker.status(oid)
        return out

    def _look(self, book, oid):
        """(OrderStatus or None, lost). lost = the paper broker forgot it (restart)."""
        if not oid:
            return None, False
        s = book.get(oid)
        if s is not None and s.status != bk.UNKNOWN:
            return s, False
        return None, self.paper

    @staticmethod
    def _ev(events, key, kind, **kw):
        events.append({"key": key, "kind": kind, **kw})

    def _filled(self, st, price, qty, now_text, events, key=None):
        st.update(fill=round(float(price), 2), fill_qty=int(qty), fill_time=now_text, entry_st=bk.COMPLETE,
                  high=round(float(price), 2))
        st["sl_trg"] = None
        self._ev(events, key, "fill", price=st["fill"], qty=st["fill_qty"], time=now_text)

    def _fill_update(self, st, price, qty, now_text, events, key):
        if st.get("fill") is None:
            status = st.get("entry_st")
            self._filled(st, price, qty, now_text, events, key)
            st["entry_st"] = status                       # a partly filled limit keeps watching for the rest
            return
        st.update(fill=round(float(price), 2), fill_qty=int(qty))
        self._ev(events, key, "fill", price=st["fill"], qty=st["fill_qty"], time=now_text)

    def _entry_req(self, st):
        et = st.get("etype", bk.MARKET)
        return bk.OrderRequest(self._ik(st), bk.BUY, int(st["units"]), et,
                               price=st.get("eprice") or 0.0, trigger_price=st.get("etrig") or 0.0, tag=st["tag"],
                               ref_price=None)

    def _assume_entry(self, st, ltp, now_text, events, key):
        """Sandbox (no order status): a Limit fills once LTP <= price; a Stop-limit triggers at LTP >= trigger,
        then fills once LTP <= the limit (like the exchange)."""
        et = st.get("etype", bk.MARKET)
        if et == bk.SL and not st.get("triggered"):
            if ltp < st["etrig"]:
                return
            st["triggered"] = True
        if et in (bk.LIMIT, bk.SL) and ltp <= st["eprice"]:
            self._filled(st, min(st["eprice"], ltp), st["units"], now_text, events, key)
            st["entry_st"] = bk.COMPLETE

    def _closed(self, st, price, reason, now_text, events, key):
        st.update(exit=round(float(price), 2) if price is not None else None, exit_time=now_text,
                  exit_reason=reason, closed=reason, alert="")
        self._ev(events, key, "exit", price=st["exit"], reason=reason, time=now_text)

    def _step(self, key, st, ltp, book, now_text, eod, events):
        # 1 -- entry (a Limit / Stop-limit may fill in parts: keep watching it until it is complete)
        if entry_open(st):
            s, lost = self._look(book, st.get("entry_id"))
            if s:
                st["entry_st"] = s.status
                if s.status != bk.TRIGGER_PENDING and s.status not in (bk.SENT, bk.UNKNOWN):
                    st["triggered"] = True
                if s.filled_qty and s.filled_qty != st.get("fill_qty"):
                    self._fill_update(st, s.avg_price, s.filled_qty, now_text, events, key)
                if s.status in (bk.REJECTED, bk.CANCELLED) and st.get("fill") is None:
                    st["closed"] = f"entry {s.status.lower()}"
                    kind = "cancelled" if s.status == bk.CANCELLED else "failed"
                    self._ev(events, key, kind, text=f"entry {s.status.lower()}"
                             + (f" ({st['cancel_req']})" if st.get("cancel_req") else f": {s.message}"))
                    return
            elif lost:
                if st.get("etype", bk.MARKET) == bk.MARKET or st.get("fill") is not None:
                    if st.get("fill") is None:
                        self._filled(st, st.get("ref") or ltp, st["units"], now_text, events, key)
                        self._ev(events, key, "log", text="dry run: entry assumed filled after a restart")
                    st["entry_st"] = bk.COMPLETE
                else:                                     # dry run after a restart: wait again at the same prices
                    res = self.broker.place(self._entry_req(st))
                    if res.ok:
                        st["entry_id"] = res.order_id
                        self._ev(events, key, "log", text=f"dry run: entry order placed again after a restart ({res.order_id})")
            elif self.assumed and st.get("fill") is None and ltp is not None:
                self._assume_entry(st, float(ltp), now_text, events, key)
        # cancel the unfilled entry / the unfilled rest: Cancel, 15:15, or an exit before / during the fill
        if entry_open(st) and (st.get("cancel_req") or eod or st.get("exit_req")):
            if self.clock() - (st.get("cancel_sent") or 0) >= MODIFY_EVERY:
                st["cancel_sent"] = self.clock()
                why = st.get("cancel_req") or ("15:15" if eod else st.get("exit_req"))
                res = self.broker.cancel(st["entry_id"])
                self._ev(events, key, "log", text=f"entry {'rest ' if st.get('fill') is not None else ''}cancel "
                         f"({why}): {'sent' if res.ok else 'FAILED: ' + res.message}")
                if res.ok and (self.assumed or self.paper):
                    st["entry_st"] = bk.CANCELLED
                    if st.get("fill") is None:
                        st["closed"] = "entry cancelled"
                        self._ev(events, key, "cancelled", text=f"entry cancelled ({why})")
                        return
        if st.get("fill") is None:
            return
        if st.get("cancel_req") and not entry_open(st):
            st["cancel_req"] = ""                         # the rest is gone; the filled part stays protected

        fill = st["fill"]
        if ltp is not None:
            st["high"] = round(max(st.get("high") or fill, float(ltp)), 2)

        # 2 -- an exit already sent: wait for its fill
        if st.get("exit_id"):
            s, lost = self._look(book, st["exit_id"])
            if s and s.status == bk.COMPLETE:
                self._closed(st, s.avg_price, st.get("exit_req") or "Exit", now_text, events, key)
                return
            if s and s.status in (bk.REJECTED, bk.CANCELLED):
                self._ev(events, key, "log", text=f"exit order {s.status.lower()}: {s.message} - trying again")
                if st["exit_id"] == st.get("sl_id"):
                    st["sl_id"] = ""
                st.update(exit_id="", exit_how="")
            elif self.assumed or lost:
                self._closed(st, ltp if ltp is not None else fill, st.get("exit_req") or "Exit", now_text, events, key)
                return
            else:
                return                                    # still working at the exchange

        # 3 -- the exchange SL filled on its own
        if st.get("sl_id"):
            s, lost = self._look(book, st["sl_id"])
            if s:
                st["sl_st"] = s.status
                if s.status == bk.COMPLETE:
                    moved = st.get("sl_first") is not None and (st.get("sl_trg") or 0) > st["sl_first"] + 1e-9
                    self._closed(st, s.avg_price, "Trailing SL" if moved else "SL", now_text, events, key)
                    return
                if s.status in (bk.REJECTED, bk.CANCELLED):
                    self._ev(events, key, "log", text=f"exchange SL {s.status.lower()}: {s.message}")
                    if s.status == bk.REJECTED and st.get("sl_kind") == bk.SLM:
                        st["sl_kind"] = bk.SL                  # fall back to an SL limit
                    st.update(sl_id="", sl_st="")
            elif lost:
                st.update(sl_id="", sl_st="")                 # dry run after a restart: place it again
            elif self.assumed and ltp is not None and st.get("sl_trg") is not None and ltp <= st["sl_trg"]:
                moved = st.get("sl_first") is not None and st["sl_trg"] > st["sl_first"] + 1e-9
                self._closed(st, ltp, "Trailing SL" if moved else "SL", now_text, events, key)
                return

        plan = st.get("plan") or {}
        # 4 -- exits decided by the app
        if not st.get("exit_req"):
            reason = None
            if eod:
                reason = "15:15"
            else:
                tl = target_level(plan, fill)
                lvl = trail_level(plan.get("trail"), fill, st.get("high"))
                if tl is not None and ltp is not None and ltp >= tl:
                    reason = "Target"
                elif lvl is not None and ltp is not None and ltp <= lvl and (st.get("sl_trg") or 0) < lvl - 1e-9:
                    reason = "Trailing SL"                    # the exchange SL is lower / missing: exit now
                elif not st.get("sl_id"):
                    trg = sl_trigger(plan, fill)
                    if trg is not None and ltp is not None and ltp <= trg:
                        reason = "SL"                         # already through the SL: no point placing it
            if reason:
                self.request_exit(st, reason, now_text)

        if st.get("exit_req"):
            self._send_exit(key, st, fill, now_text, events)
            return

        # 5 -- place / move the exchange SL
        want = sl_trigger(plan, fill)
        lvl = trail_level(plan.get("trail"), fill, st.get("high"))
        if lvl is not None and (want is None or lvl > want):
            want = lvl
        if st.get("sl_id") and st.get("sl_qty") and st["sl_qty"] != st["fill_qty"]:
            res = self.broker.modify(st["sl_id"], order_type=st.get("sl_kind"), trigger_price=st["sl_trg"],
                                     price=bk.round_tick(st["sl_trg"] - self.gap) if st.get("sl_kind") == bk.SL else None,
                                     quantity=st["fill_qty"])
            if res.ok:
                self._ev(events, key, "log", text=f"exchange SL quantity {st['sl_qty']} -> {st['fill_qty']} (more filled)")
                st["sl_qty"] = st["fill_qty"]
            else:
                self._ev(events, key, "log", text=f"SL quantity modify FAILED: {res.message}")
        if st.get("sl_trg") is not None and not st.get("sl_reset") and want is not None:
            want = max(want, st["sl_trg"])                    # never down (except a user change)
        if want is None:
            return
        if not st.get("sl_id"):
            if st["sl_tries"] >= SL_TRIES:
                if not st.get("alert"):
                    st["alert"] = "NO SL"
                    self._ev(events, key, "alert", text=f"NO SL at the exchange after {SL_TRIES} tries")
                return
            self._place_sl(key, st, want, events)
        elif st.get("sl_reset") or want > (st.get("sl_trg") or 0) + 1e-9:
            if not st.get("sl_reset") and self.clock() - (st.get("trail_at") or 0) < MODIFY_EVERY:
                return
            price = bk.round_tick(want - self.gap) if st.get("sl_kind") == bk.SL else None
            res = self.broker.modify(st["sl_id"], order_type=st.get("sl_kind"), trigger_price=want, price=price)
            st["trail_at"] = self.clock()
            if res.ok:
                old = st.get("sl_trg")
                st["sl_trg"], st["sl_reset"] = want, False
                self._ev(events, key, "log", text=f"exchange SL moved {old} -> {want:.2f}")
            else:
                self._ev(events, key, "log", text=f"SL modify FAILED: {res.message}")

    def _place_sl(self, key, st, trigger, events):
        kind = st.get("sl_kind") or bk.SLM
        limit = bk.round_tick(trigger - self.gap) if kind == bk.SL else 0.0
        if kind == bk.SL and limit <= 0:
            limit = bk.TICK
        req = bk.OrderRequest(self._ik(st), bk.SELL, int(st["fill_qty"] or st["units"]), kind,
                              price=limit, trigger_price=trigger, tag=st["tag"], ref_price=None)
        res = self.broker.place(req)
        if res.ok:
            st.update(sl_id=res.order_id, sl_st=bk.SENT, sl_trg=trigger, sl_reset=False, alert="",
                      sl_qty=int(st["fill_qty"] or st["units"]))
            if st.get("sl_first") is None:
                st["sl_first"] = trigger
            self._ev(events, key, "sl", text=f"exchange SL placed: {kind} trigger {trigger:.2f}"
                     + (f" limit {limit:.2f}" if kind == bk.SL else ""), trigger=trigger)
        else:
            st["sl_tries"] += 1
            if kind == bk.SLM:
                st["sl_kind"] = bk.SL                         # next try: SL limit
            self._ev(events, key, "log", text=f"exchange SL not placed (try {st['sl_tries']}/{SL_TRIES}): "
                     f"{res.message}")

    def _send_exit(self, key, st, fill, now_text, events):
        if st.get("exit_id"):
            return
        if entry_open(st):
            return                                        # cancel the unfilled rest first (sent above)
        if st["exit_tries"] >= EXIT_TRIES:
            if st.get("alert") != "EXIT FAILED":
                st["alert"] = "EXIT FAILED"
                self._ev(events, key, "alert", text=f"exit ({st['exit_req']}) not accepted after {EXIT_TRIES} tries "
                         "- close it in the Upstox app")
            return
        if st.get("sl_id") and st.get("sl_st") not in bk.FINAL:
            res = self.broker.modify(st["sl_id"], order_type=bk.MARKET,
                                     quantity=st["fill_qty"] if st.get("sl_qty") != st["fill_qty"] else None)
            if res.ok:
                st.update(exit_id=st["sl_id"], exit_how="modify")
                self._ev(events, key, "log", text=f"exit ({st['exit_req']}): exchange SL changed to MARKET")
                return
            st["exit_tries"] += 1
            self._ev(events, key, "log", text=f"exit modify FAILED: {res.message}")
            c = self.broker.cancel(st["sl_id"])
            if not c.ok:
                return                                    # keep the SL; try again next tick
            st.update(sl_id="", sl_st="")
        req = bk.OrderRequest(self._ik(st), bk.SELL, int(st["fill_qty"] or st["units"]), bk.MARKET,
                              tag=st["tag"], ref_price=None)
        res = self.broker.place(req)
        if res.ok:
            st.update(exit_id=res.order_id, exit_how="sell")
            self._ev(events, key, "log", text=f"exit ({st['exit_req']}): market SELL sent")
        else:
            st["exit_tries"] += 1
            self._ev(events, key, "log", text=f"exit SELL FAILED: {res.message}"
                     + (" (check the Upstox order book)" if res.uncertain else ""))

    @staticmethod
    def _ik(st):
        return st.get("ik") or ""
