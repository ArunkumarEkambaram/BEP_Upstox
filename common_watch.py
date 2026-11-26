"""
common_watch.py - Common Price Watch (manual trades, nothing is placed automatically).

You add a strike, a leg (Both / CE / PE), a common price and a timeframe (3 or 5 min). A row added before
the open has no previous candle, so the first candle's OPEN counts as the previous price: a 09:15 candle that
opens at or below the price and closes above it triggers at 09:20 (5 min) / 09:18 (3 min). Each leg is
tracked on its own row. After every candle of that timeframe closes, the leg's close is compared with
the common price:
  - TRIGGERED: the close crosses ABOVE the common price (previous close <= common price, this close > it).
    The baseline is the last candle that closed before the row was added, so a leg that is already above
    must first close at or below, then cross. Fires once (alert + "Place order" button).
  - FAILED: after triggering, a later candle closes BELOW the common price. The row turns red, no alert.
Rows are kept for the day in common_watch_YYYY-MM-DD.json next to the app.

PAIR rows (kind "pair"): a CE strike and a PE strike that "fight" around a common price (PE may have its own
price). On a candle close, a leg TRIGGERS when it closes above its price AND, in the same candle, the opponent
closes below its price - buffer. The leg must have closed at or below its price at some point first (it is
"armed"), so a leg already above when the row is added has to come back down and cross up. Either side can trigger, once. Then:
  - not ordered yet: the opponent closing at or above (its price - buffer) marks the row FAILED (red).
  - ordered: the same rule is the OPPONENT SL -> status "exited" and an exit event for the engine, which sends
    AlgoDelta's squareoff_position (live) or records the exit at the bought leg's close (dry run).
The normal Target / SL / trailing SL still apply as well (AlgoDelta live, simulated in dry run).

ARMING / TRIGGER (PR #27, both Same strike and Pair): a leg is ARMED once it trades at or below its price after
the row was added - a candle close or a candle LOW (wick) of a candle that started after the add, or the live LTP
(every 2 s, engine.check_live_watch -> live_step). Trigger on (per row, "trig_on"):
  - "close" (default): an armed leg's candle CLOSES above its price (Pair: the opponent's close below price - buffer).
  - "ltp": an armed leg's live LTP goes above its price (Pair: the opponent's LTP below price - buffer), within ~2 s.
FAILED / the Pair opponent exit stay on candle closes. Timeframes 1 / 3 / 5 min.

\u2b50 rows (source "star") are added automatically for a LIVE first-support-of-the-day (\u2b50) on the BEP Support
board, within that candle's ATM window. They are TRIGGERED at once (Place order ready, no extra alert - the board
already alerts) and turn FAILED when the board's support row turns red. Their candles are not re-checked here.
"""
import json
import os
from datetime import datetime, timedelta

import config
import market_utils

IST = market_utils.IST
FINALIZE_DELAY = timedelta(seconds=config.TRACK_FINALIZE_DELAY_SECONDS)


def _file(day: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), f"common_watch_{day}.json")


def _ts(c) -> datetime:
    t = datetime.fromisoformat(c[0])
    return t.astimezone(IST) if t.tzinfo else IST.localize(t)


class CommonWatch:
    def __init__(self, index: str):
        self.index = index
        self.day = None
        self.rows = []
        self._seq = 0

    # ------------------------------------------------------------------ persistence (same day only)
    def load(self, now=None):
        now = now or datetime.now(IST)
        self.day = now.strftime("%Y-%m-%d")
        self.rows, self._seq = [], 0
        try:
            with open(_file(self.day), encoding="utf-8") as f:
                data = json.load(f)
            self.rows = [r for r in data.get("rows", []) if r.get("index") == self.index]
            self._seq = max([r["id"] for r in self.rows], default=0)
        except (OSError, ValueError):
            pass

    def save(self):
        if not self.day:
            return
        try:
            others = []
            try:
                with open(_file(self.day), encoding="utf-8") as f:
                    others = [r for r in json.load(f).get("rows", []) if r.get("index") != self.index]
            except (OSError, ValueError):
                pass
            with open(_file(self.day), "w", encoding="utf-8") as f:
                json.dump({"rows": others + self.rows}, f, indent=1)
        except OSError:
            pass

    def _roll_day(self, now):
        if now.strftime("%Y-%m-%d") != self.day:
            self.load(now)

    # ------------------------------------------------------------------ rows
    def add(self, strike: int, leg: str, price: float, tf: int, now=None, mode: str = None,
            trig_on: str = "close") -> list:
        now = now or datetime.now(IST)
        self._roll_day(now)
        added = []
        for opt in (("CE", "PE") if leg == "Both" else (leg,)):
            self._seq += 1
            row = {"id": self._seq, "index": self.index, "strike": int(strike), "opt": opt, "price": float(price),
                   "tf": int(tf), "added_at": now.isoformat(), "status": "watching", "baseline": None,
                   "last_done": None, "prev_close": None, "last_close": None, "last_time": "",
                   "trig_time": "", "trig_close": None, "fail_time": "", "fail_close": None, "order_status": "",
                   "armed": False, "trig_on": "ltp" if trig_on == "ltp" else "close"}
            self._apply_mode(row, mode)
            self.rows.append(row)
            added.append(row)
        self.save()
        return added

    def add_star(self, ev: dict, now=None):
        """Adds a \u2b50 row from a live BEP Support first-support event. Returns the row, or None if already there."""
        now = now or datetime.now(IST)
        self._roll_day(now)
        if any(r.get("source") == "star" and r["strike"] == int(ev["strike"]) and r["opt"] == ev["opt"]
               and r["trig_time"] == ev["time"] for r in self.rows):
            return None
        self._seq += 1
        row = {"id": self._seq, "index": self.index, "strike": int(ev["strike"]), "opt": ev["opt"],
               "price": round(float(ev["bep"]), 2), "tf": 5, "added_at": now.isoformat(), "status": "triggered",
               "source": "star", "baseline": "star", "last_done": None, "prev_close": None,
               "last_close": float(ev["close"]), "last_time": ev["time"], "trig_time": ev["time"],
               "trig_close": float(ev["close"]), "fail_time": "", "fail_close": None, "order_status": ""}
        self.rows.append(row)
        self.save()
        return row

    def add_manual(self, strike: int, opt: str, lots: int, entry_price=None, now=None, mode: str = None,
                   trigger=None) -> dict:
        """Manual Entry card (source "manual"): no trigger condition, ready to order at once. Entry price None =
        market at the click; a price = the app waits for the LTP (same as any card's entry price).
        mode "limit" = an AlgoDelta limit_order at the price, SL / Target watched by the app (limit_orders.py)."""
        now = now or datetime.now(IST)
        self._roll_day(now)
        self._seq += 1
        row = {"id": self._seq, "index": self.index, "strike": int(strike), "opt": opt,
               "price": float(entry_price or 0), "tf": 1, "added_at": now.isoformat(), "status": "triggered",
               "source": "manual", "baseline": "manual", "last_done": None, "prev_close": None,
               "last_close": None, "last_time": "", "trig_time": now.strftime("%H:%M"), "trig_close": None,
               "fail_time": "", "fail_close": None, "order_status": "", "qty": int(lots),
               "entry_price": round(float(entry_price), 2) if entry_price else None, "entry_dir": None,
               "entry_mode": "limit" if mode == "limit" else None,
               "entry_trigger": round(float(trigger), 2) if trigger and mode == "limit" else None}  # stop-limit
        self.rows.append(row)
        self.save()
        return row

    def set_stoplimit(self, row_id, trigger, price) -> dict:
        """Turns a limit card that is not ordered yet into a stop-limit (trigger) or back to a limit (trigger None)."""
        r = self.get(row_id)
        if not r or r.get("order_status") in ("SENT", "DRY RUN", "sending", "PENDING"):
            return None
        r.update(entry_mode="limit", entry_trigger=round(float(trigger), 2) if trigger else None,
                 entry_price=round(float(price), 2), entry_dir=None)
        if r.get("source") == "manual":
            r["price"] = float(price)
        else:
            r["entry_stop"] = bool(trigger)
        self.save()
        return r

    def sync_star_failures(self, support: dict) -> bool:
        """Marks \u2b50 rows FAILED when their BEP Support board row (same strike, leg and time) has turned red."""
        changed = False
        for r in self.rows:
            if r.get("source") != "star" or r["status"] != "triggered":
                continue
            b = next((e for e in support.get(r["opt"], []) if e["strike"] == r["strike"] and e["time"] == r["trig_time"]), None)
            if b and b.get("state") == "red":
                r.update(status="failed", fail_time=b.get("fail_time") or "", fail_close=b.get("fail_close"))
                changed = True
        if changed:
            self.save()
        return changed

    def add_cross_pair(self, sig: dict, now=None):
        """Adds a TRIGGERED Pair row from a live BEP Pair cross signal (pair_cross.py): CE / PE price = each leg's
        BEP, buffer 0, 5 min, the leg that closed above is the fired leg. Later candles are checked by the normal
        Pair rules (opponent closing at or above its BEP = FAILED before ordering / opponent SL after).
        Returns (row, None) or (None, reason) when a Pair row with the same strikes is still open."""
        now = now or datetime.now(IST)
        self._roll_day(now)
        up, dn = sig["up"], sig["dn"]
        ce, pe = (up, dn) if up["opt"] == "CE" else (dn, up)
        for r in self.rows:
            if r.get("kind") == "pair" and r["ce_strike"] == ce["strike"] and r["pe_strike"] == pe["strike"] \
                    and r["status"] in ("watching", "triggered"):
                return None, f"Pair {ce['strike']}CE / {pe['strike']}PE is already open (row {r['id']})"
        row = self.add_pair(ce["strike"], pe["strike"], ce["bep"], pe["bep"], 0.0, 5, now=now)
        done = sig["start"].isoformat()
        for o, leg in (("CE", ce), ("PE", pe)):
            row["legs"][o].update(prev_close=leg["close"], last_close=leg["close"], last_done=done, armed=True)
        row.update(status="triggered", baseline=done, auto="cross", cross_id=sig["id"], opt=up["opt"],
                   strike=up["strike"], price=up["bep"], trig_time=sig["time"], trig_close=up["close"],
                   last_close=up["close"], last_time=sig["time"])
        self.save()
        return row, None

    def sync_cross_failures(self, cross_rows: list) -> bool:
        """Marks BEP-cross Pair rows that are not ordered yet FAILED when their pair-cross table row failed."""
        by_id = {c["id"]: c for c in cross_rows}
        changed = False
        for r in self.rows:
            if r.get("auto") != "cross" or r["status"] != "triggered" \
                    or r.get("order_status") in ("SENT", "DRY RUN", "sending"):
                continue
            c = by_id.get(r.get("cross_id"))
            if c and c["status"] == "failed":
                r.update(status="failed", fail_time=c["fail_time"], fail_close=c.get("fail_close"),
                         fail_why=c["fail_why"])
                changed = True
        if changed:
            self.save()
        return changed

    def add_pair(self, ce_strike: int, pe_strike: int, ce_price: float, pe_price: float, buffer: float, tf: int,
                 now=None, mode: str = None, trig_on: str = "close", auto_open: bool = False, tf_sec=None) -> dict:
        now = now or datetime.now(IST)
        self._roll_day(now)
        self._seq += 1
        leg = {"prev_close": None, "last_close": None, "last_done": None}
        row = {"id": self._seq, "index": self.index, "kind": "pair", "source": "pair",
               "ce_strike": int(ce_strike), "pe_strike": int(pe_strike), "ce_price": float(ce_price),
               "pe_price": float(pe_price), "buffer": float(buffer), "tf": int(tf), "added_at": now.isoformat(),
               "status": "watching", "baseline": None, "legs": {"CE": dict(leg), "PE": dict(leg)},
               # filled when a side triggers (so the shared Place order / alert code can use them)
               "opt": "", "strike": None, "price": None, "trig_time": "", "trig_close": None,
               "last_close": None, "last_time": "", "fail_time": "", "fail_close": None,
               "opp_close": None, "exit_close": None, "order_status": "", "order_id": None, "squareoff": "",
               "trig_on": "ltp" if trig_on == "ltp" else "close",
               "auto_open": bool(auto_open)}         # "Auto order": placed by itself (window rule, then Pair rule)
        if tf_sec in (15, 30):                    # 15 s / 30 s: app-built candles in the auto window, 1 min after
            row.update(tf=1, tf_sec=int(tf_sec))
        self._apply_mode(row, mode)
        self.rows.append(row)
        self.save()
        return row

    @staticmethod
    def auto_window(now) -> bool:
        """The "Auto at 09:15" window of a Pair row: PAIR_AUTO_FROM <= time < PAIR_AUTO_TO (09:15:00-09:30:00)."""
        t = now.astimezone(IST).strftime("%H:%M:%S")
        return config.PAIR_AUTO_FROM <= t < config.PAIR_AUTO_TO

    @staticmethod
    def auto_active(r, now) -> bool:
        return bool(r.get("auto_open")) and r.get("kind") == "pair" and r.get("auto") != "cross" \
            and r["status"] == "watching" and CommonWatch.auto_window(now)

    @staticmethod
    def tf_label(r) -> str:
        """"15 s" / "30 s" (app-built candles in the auto window) or "5 min"."""
        return f"{r['tf_sec']} s" if r.get("tf_sec") else f"{r['tf']} min"

    @staticmethod
    def _hms(now, hms):
        h, m, sec = (int(x) for x in hms.split(":"))
        return now.astimezone(IST).replace(hour=h, minute=m, second=sec, microsecond=0)

    @staticmethod
    def auto_why(r) -> str:
        """Why an auto Pair order was placed (Log / entry note)."""
        if r.get("auto_fire"):
            return (f"Pair rule: {r['opt']} {'LTP' if r.get('trig_how') == 'ltp' else 'close'} {r['trig_close']:.2f} "
                    f"above {r['price']:g}, opponent below")
        if r.get("auto_how") == "close":
            return f"{r.get('auto_tf') or CommonWatch.tf_label(r)} candle closed {r['trig_close']:.2f} above {r['price']:g}"
        return f"LTP {r['trig_close']:.2f} crossed above {r['price']:g}"

    def _sec_candles(self, r, lt, price, now, label) -> tuple:
        """15 s / 30 s candles built from the live LTP (Upstox has none) for an "Auto order" Pair row on Candle
        close, 09:15-09:30. A candle closes at its end: close = the last LTP read inside it. A leg that was at or
        below its price from 09:15 and whose candle closes above it is ordered (no opponent check; both = the one
        more above). A candle the app did not see from its start (start / restart mid-candle) is skipped.
        Returns (fired copy or None, changed)."""
        tf = int(r["tf_sec"])
        t0, end = self._hms(now, config.PAIR_AUTO_FROM), self._hms(now, config.PAIR_AUTO_TO)
        if now < t0 or now >= end + timedelta(seconds=tf + 10):
            return None, False
        idx = int((now - t0).total_seconds() // tf)
        sc, fired, changed = r.get("sc"), None, False
        full_next = None
        if sc and sc.get("i") != idx:                  # the stored candle has closed
            c_end = t0 + timedelta(seconds=(sc["i"] + 1) * tf)
            if sc.get("full") and c_end <= end and r["status"] == "watching" \
                    and (now - c_end).total_seconds() <= 10:
                closes = sc.get("c") or {}
                cands = [(closes[o] - price[o], o) for o in ("CE", "PE")
                         if r["legs"][o].get("auto_below") and closes.get(o) is not None and closes[o] > price[o]]
                if cands:
                    o = max(cands)[1]
                    opp = "PE" if o == "CE" else "CE"
                    strike = r["ce_strike"] if o == "CE" else r["pe_strike"]
                    t = c_end.strftime("%H:%M:%S")
                    r.update(status="triggered", opt=o, strike=strike, price=price[o], trig_time=t,
                             trig_close=closes[o], last_close=closes[o], last_time=t, trig_how="auto",
                             auto_how="close", auto_tf=self.tf_label(r), trig_opp=closes.get(opp))
                    fired = dict(r)
            full_next = idx == sc["i"] + 1
            sc, changed = None, True
        if sc is None:
            start = t0 + timedelta(seconds=idx * tf)
            sc = {"i": idx, "c": {}, "full": full_next if full_next is not None
                  else (now - start).total_seconds() <= 3}
            r["sc"] = sc
            changed = True
        for o in ("CE", "PE"):
            if lt[o] is not None:
                sc["c"][o] = lt[o]
                if now < end and lt[o] <= price[o] and not r["legs"][o].get("auto_below"):
                    r["legs"][o]["auto_below"] = True
                    changed = True
        return fired, changed

    def set_auto(self, row_id: int, on: bool) -> bool:
        """Turns "Auto at 09:15" on / off for a Pair row (not a BEP cross row) while it is still watching."""
        r = self.get(row_id)
        if not r or r.get("kind") != "pair" or r.get("auto") == "cross" or r["status"] != "watching":
            return False
        r["auto_open"] = bool(on)
        self.save()
        return True

    @staticmethod
    def _apply_mode(r, mode):
        """Order type of a card: "limit" = AlgoDelta limit_order at the card's Entry Price (pre-filled with the
        row's level; a Pair before it fires uses the fired leg's price), SL / Target watched by the app.
        None = strategy_order (market, or the app-side PENDING entry when an Entry Price is set)."""
        lim = mode in ("limit", "stoplimit")
        r["entry_mode"] = "limit" if lim else None
        r["entry_price"] = round(float(r["price"]), 2) if lim and r.get("price") else None
        r["entry_dir"] = None
        # "stoplimit" (PR #38) = AlgoDelta sl_order: waits at the exchange until the LTP rises to the trigger
        # (default the level; a Pair before it fires: the fired leg's price), then a limit at trigger + gap
        r["entry_stop"] = mode == "stoplimit"
        r["entry_trigger"] = r["entry_price"] if mode == "stoplimit" else None
        if mode == "stoplimit" and r["entry_price"]:
            r["entry_price"] = round(r["entry_price"] + config.STOP_LIMIT_GAP, 2)

    def set_mode(self, row_id: int, mode) -> bool:
        """Switches a card between Strategy and Limit (resets its Entry Price). Locked once ordered / pending."""
        r = self.get(row_id)
        if not r or r.get("source") == "manual" or r.get("order_status") in ("SENT", "DRY RUN", "sending", "PENDING"):
            return False
        self._apply_mode(r, mode)
        self.save()
        return True

    def pop_exits(self) -> list:
        out, self._exits = getattr(self, "_exits", []), []
        return out

    def remove(self, row_id: int):
        self.rows = [r for r in self.rows if r["id"] != row_id]
        self.save()

    def get(self, row_id: int):
        return next((r for r in self.rows if r["id"] == row_id), None)

    def set_trail(self, row_id: int, trail) -> bool:
        """Card's own trailing SL {"on", "type", "trigger", "point", "gap"}, or None for Setup. Locked once ordered."""
        r = self.get(row_id)
        if not r or r.get("order_status") in ("SENT", "DRY RUN", "sending"):
            return False
        r["c_trail"] = trail
        self.save()
        return True

    def set_custom(self, row_id: int, kind: str, rule) -> bool:
        """Row's own SL ("sl") or Target ("tgt") rule, or None to use the default. Locked once ordered."""
        r = self.get(row_id)
        if not r or r.get("order_status") in ("SENT", "DRY RUN", "sending"):
            return False
        r["c_" + kind] = rule
        self.save()
        return True

    def set_qty(self, row_id: int, lots: int) -> bool:
        """Row's own quantity in lots (default: Setup). Locked once ordered."""
        r = self.get(row_id)
        if not r or r.get("order_status") in ("SENT", "DRY RUN", "sending") or int(lots) < 1:
            return False
        r["qty"] = int(lots)
        self.save()
        return True

    def edit(self, row_id: int, tf: int, price: float = None, pe_price: float = None, buffer: float = None,
             trig_on: str = None, tf_sec=None) -> bool:
        """Changes a WAITING row's level / timeframe (pair: CE price, PE price, buffer) and restarts its check from
        the candles before it was added (same baseline rule as a new row). Not for \u2b50 rows."""
        r = self.get(row_id)
        if not r or r["status"] != "watching" or r.get("source") == "star" or r.get("order_status"):
            return False
        follow = r.get("entry_mode") == "limit" and r.get("entry_price") is not None \
            and r.get("price") is not None and abs(r["entry_price"] - r["price"]) < 1e-9
        follow_stop = r.get("entry_stop") and r.get("entry_trigger") is not None \
            and r.get("price") is not None and abs(r["entry_trigger"] - r["price"]) < 1e-9
        r.update(tf=int(tf), baseline=None, armed=False)
        if trig_on in ("close", "ltp"):
            r["trig_on"] = trig_on
        if r.get("kind") == "pair":
            r.pop("sc", None)
            if tf_sec in (15, 30):
                r.update(tf=1, tf_sec=int(tf_sec))
            else:
                r.pop("tf_sec", None)
            r.update(ce_price=float(price), pe_price=float(pe_price if pe_price is not None else price),
                     buffer=float(buffer if buffer is not None else r["buffer"]))
            r["legs"] = {o: {"prev_close": None, "last_close": None, "last_done": None} for o in ("CE", "PE")}
        else:
            r.update(price=float(price), last_done=None, prev_close=None)
            if follow_stop:                             # a stop-limit trigger that was the level follows it
                self._apply_mode(r, "stoplimit")
            elif follow:                                # a limit price that was the level follows the new level
                r["entry_price"] = round(float(price), 2)
        self.save()
        return True

    def set_order_status(self, row_id: int, status: str):
        r = self.get(row_id)
        if r:
            r["order_status"] = status
            self.save()

    # ------------------------------------------------------------------ candle checks
    def step(self, client, expiry: str, now) -> list:
        """Checks every row against the candles that have closed since the last check.
        Returns a list of new TRIGGERED rows (copies) for alerting. Safe to call every scan."""
        self._roll_day(now)
        if not self.rows or not expiry:
            return []
        mapping, _ = market_utils.option_instrument_keys(client, expiry, config.INDEXES[self.index]["underlying_key"])
        mapping = mapping or {}
        triggered = []
        changed = False
        for r in self.rows:
            if r.get("kind") == "pair":
                fired, ch = self._step_pair(client, mapping, r, now)
                triggered.extend(fired)
                changed = changed or ch
                continue
            if r["status"] == "failed" or r.get("source") in ("star", "manual"):
                continue
            tf = timedelta(minutes=r["tf"])
            ref = now - FINALIZE_DELAY
            minute = ref.minute - ref.minute % r["tf"]
            latest_closed_end = ref.replace(minute=minute, second=0, microsecond=0)   # end of the last closed candle
            if r["last_done"] and datetime.fromisoformat(r["last_done"]) + tf >= latest_closed_end:
                continue                                    # nothing new has closed yet
            ik = mapping.get(r["strike"], {}).get(r["opt"])
            if not ik:
                continue
            candles, _err = client.fetch_intraday_candles(ik, r["tf"])
            if not candles:
                continue
            added_at = datetime.fromisoformat(r["added_at"])
            closed = [c for c in candles if _ts(c) + tf <= ref]                       # completed candles only
            if r["baseline"] is None:
                before = [c for c in closed if _ts(c) + tf <= added_at]
                base = before[-1] if before else None
                r["baseline"] = base[0] if base else "none"
                r["prev_close"] = float(base[4]) if base else None
                r["last_done"] = base[0] if base else None
                changed = True
            for c in closed:
                if r["last_done"] and _ts(c) <= datetime.fromisoformat(r["last_done"]):
                    continue
                close, label = float(c[4]), (_ts(c) + tf).strftime("%H:%M")
                prev = r["prev_close"]
                if prev is None:                  # no candle before the row (added pre-open): use this candle's open
                    prev = float(c[1])
                if prev is not None and prev <= r["price"]:
                    r["armed"] = True             # at or below the price since the row was added
                if _ts(c) >= added_at and float(c[3]) <= r["price"]:
                    r["armed"] = True             # a wick to the price counts too (candle started after the add)
                if r["status"] == "watching" and r.get("armed") and close > r["price"]:
                    r.update(status="triggered", trig_time=label, trig_close=close)
                    triggered.append(dict(r))
                elif r["status"] == "triggered" and close < r["price"]:
                    r.update(status="failed", fail_time=label, fail_close=close)
                r.update(prev_close=close, last_close=close, last_time=label, last_done=c[0])
                changed = True
                if r["status"] == "failed":
                    break
        if changed:
            self.save()
        return triggered

    def _step_pair(self, client, mapping, r, now):
        if r["status"] in ("failed", "exited"):
            return [], False
        tf = timedelta(minutes=r["tf"])
        ref = now - FINALIZE_DELAY
        minute = ref.minute - ref.minute % r["tf"]
        latest_closed_end = ref.replace(minute=minute, second=0, microsecond=0)
        last = r["legs"]["CE"]["last_done"]
        if last and datetime.fromisoformat(last) + tf >= latest_closed_end:
            return [], False
        iks = {"CE": mapping.get(r["ce_strike"], {}).get("CE"), "PE": mapping.get(r["pe_strike"], {}).get("PE")}
        if not all(iks.values()):
            return [], False
        closes, opens, lows = {}, {}, {}
        for opt, ik in iks.items():
            candles, _err = client.fetch_intraday_candles(ik, r["tf"])
            closes[opt] = {c[0]: float(c[4]) for c in (candles or []) if _ts(c) + tf <= ref}
            opens[opt] = {c[0]: float(c[1]) for c in (candles or []) if _ts(c) + tf <= ref}
            lows[opt] = {c[0]: float(c[3]) for c in (candles or []) if _ts(c) + tf <= ref}
        times = sorted(set(closes["CE"]) & set(closes["PE"]), key=lambda t: datetime.fromisoformat(t))
        if not times:
            return [], False
        added_at = datetime.fromisoformat(r["added_at"])
        price = {"CE": r["ce_price"], "PE": r["pe_price"]}
        line = {o: price[o] - r["buffer"] for o in ("CE", "PE")}      # the opponent must stay BELOW this
        changed, fired = False, []
        auto_row = bool(r.get("auto_open")) and r.get("auto") != "cross"
        if r["baseline"] is None:
            before = [t for t in times if datetime.fromisoformat(t) + tf <= added_at]
            base = before[-1] if before else None
            r["baseline"] = base or "none"
            for o in ("CE", "PE"):
                r["legs"][o].update(prev_close=closes[o][base] if base else None, last_done=base)
            changed = True
        for t in times:
            done = r["legs"]["CE"]["last_done"]
            if done and datetime.fromisoformat(t) <= datetime.fromisoformat(done):
                continue
            label = (datetime.fromisoformat(t).astimezone(IST) + tf).strftime("%H:%M")
            c_start = datetime.fromisoformat(t).astimezone(IST)
            in_window = auto_row and self._hms(c_start, config.PAIR_AUTO_FROM) <= c_start \
                and c_start + tf <= self._hms(c_start, config.PAIR_AUTO_TO)
            if r["status"] == "watching" and in_window and not r.get("tf_sec") and r.get("trig_on") != "ltp":
                for o in ("CE", "PE"):                # "Auto order" on Candle close, 09:15-09:30 (1 / 3 / 5 min)
                    if closes[o][t] <= price[o] or (c_start >= added_at and lows[o].get(t, closes[o][t]) <= price[o]):
                        r["legs"][o]["auto_below"] = True
                cands = [(closes[o][t] - price[o], o) for o in ("CE", "PE")
                         if r["legs"][o].get("auto_below") and closes[o][t] > price[o]]
                if cands:
                    o = max(cands)[1]
                    opp = "PE" if o == "CE" else "CE"
                    strike = r["ce_strike"] if o == "CE" else r["pe_strike"]
                    r.update(status="triggered", opt=o, strike=strike, price=price[o], trig_time=label,
                             trig_close=closes[o][t], last_close=closes[o][t], last_time=label, trig_how="auto",
                             auto_how="close", auto_tf=self.tf_label(r), trig_opp=closes[opp][t])
                    fired.append(dict(r))
            elif r["status"] == "watching" and not in_window:
                for o, opp in (("CE", "PE"), ("PE", "CE")):
                    prev, close = r["legs"][o]["prev_close"], closes[o][t]
                    if prev is None:              # no candle before the row (added pre-open): use this candle's open
                        prev = opens[o].get(t)
                    if prev is not None and prev <= price[o]:
                        r["legs"][o]["armed"] = True          # has been at or below its price since the row was added
                    if datetime.fromisoformat(t) >= added_at and lows[o].get(t, close) <= price[o]:
                        r["legs"][o]["armed"] = True          # a wick to the price counts too
                    if r["legs"][o].get("armed") and close > price[o] and closes[opp][t] < line[opp] \
                            and not self.auto_active(r, now):
                        strike = r["ce_strike"] if o == "CE" else r["pe_strike"]
                        r.update(status="triggered", opt=o, strike=strike, price=price[o], trig_time=label,
                                 trig_close=close, last_close=close, last_time=label, auto_fire=auto_row)
                        fired.append(dict(r))
                        break
            elif r["status"] == "triggered":
                o = r["opt"]
                opp = "PE" if o == "CE" else "CE"
                r.update(last_close=closes[o][t], last_time=label)
                if closes[opp][t] >= line[opp]:
                    ordered = r["order_status"] in ("SENT", "DRY RUN")
                    r.update(status="exited" if ordered else "failed", fail_time=label, fail_close=closes[opp][t],
                             opp_close=closes[opp][t], exit_close=closes[o][t])
                    if ordered:
                        self._exits = getattr(self, "_exits", []) + [dict(r)]
            for o in ("CE", "PE"):
                r["legs"][o].update(prev_close=closes[o][t], last_close=closes[o][t], last_done=t)
            changed = True
            if r["status"] in ("failed", "exited"):
                break
        return fired, changed

    def live_step(self, ltp_of, now) -> tuple:
        """Every OPTION_LTP_SECONDS (engine): arms legs whose live LTP is at or below their price, and triggers
        rows set to Trigger on "ltp". ltp_of(strike, opt) -> live LTP or None. Returns (fired copies, changed)."""
        self._roll_day(now)
        fired, changed = [], False
        label = now.strftime("%H:%M:%S")
        for r in self.rows:
            if r["status"] != "watching" or r.get("source") in ("star", "manual"):
                continue
            if r.get("kind") == "pair":
                price = {"CE": r["ce_price"], "PE": r["pe_price"]}
                lt = {"CE": ltp_of(r["ce_strike"], "CE"), "PE": ltp_of(r["pe_strike"], "PE")}
                for o in ("CE", "PE"):
                    if lt[o] is not None and lt[o] <= price[o] and not r["legs"][o].get("armed"):
                        r["legs"][o]["armed"] = True
                        changed = True
                if r.get("auto_open") and r.get("auto") != "cross" and r.get("tf_sec") and r.get("trig_on") != "ltp":
                    f, ch = self._sec_candles(r, lt, price, now, label)   # 15 s / 30 s candle close, 09:15-09:30
                    changed = changed or ch
                    if f:
                        fired.append(f)
                        continue
                if self.auto_active(r, now):           # "Auto order", 09:15-09:30: Live LTP = the first leg to
                    for o in ("CE", "PE"):             # CROSS above its price (at or below it first, from 09:15);
                        if lt[o] is not None and lt[o] <= price[o] and not r["legs"][o].get("auto_below"):
                            r["legs"][o]["auto_below"] = True   # Candle close = the candle rule (step / _sec_candles)
                            changed = True
                    if r.get("trig_on") != "ltp":
                        continue
                    cands = [(lt[o] - price[o], o) for o in ("CE", "PE")
                             if r["legs"][o].get("auto_below") and lt[o] is not None and lt[o] > price[o]]
                    if cands:                          # both in the same check: the one more points above
                        o = max(cands)[1]
                        opp = "PE" if o == "CE" else "CE"
                        strike = r["ce_strike"] if o == "CE" else r["pe_strike"]
                        r.update(status="triggered", opt=o, strike=strike, price=price[o], trig_time=label,
                                 trig_close=lt[o], last_close=lt[o], last_time=label, trig_how="auto",
                                 auto_how="ltp", trig_opp=lt[opp])
                        fired.append(dict(r))
                        changed = True
                    continue                           # no normal trigger while the auto window is on
                if r.get("trig_on") != "ltp" or None in lt.values():
                    continue
                for o, opp in (("CE", "PE"), ("PE", "CE")):
                    if r["legs"][o].get("armed") and lt[o] > price[o] and lt[opp] < price[opp] - r["buffer"]:
                        strike = r["ce_strike"] if o == "CE" else r["pe_strike"]
                        r.update(status="triggered", opt=o, strike=strike, price=price[o], trig_time=label,
                                 trig_close=lt[o], last_close=lt[o], last_time=label, trig_how="ltp", trig_opp=lt[opp],
                                 auto_fire=bool(r.get("auto_open")) and r.get("auto") != "cross")
                        fired.append(dict(r))
                        changed = True
                        break
            else:
                v = ltp_of(r["strike"], r["opt"])
                if v is None:
                    continue
                if v <= r["price"] and not r.get("armed"):
                    r["armed"] = True
                    changed = True
                if r.get("trig_on") == "ltp" and r.get("armed") and v > r["price"]:
                    r.update(status="triggered", trig_time=label, trig_close=v, last_close=v, last_time=label,
                             trig_how="ltp")
                    fired.append(dict(r))
                    changed = True
        if changed:
            self.save()
        return fired, changed

    @staticmethod
    def armed_text(r) -> str:
        """e.g. "armed" / "CE armed" / "not armed yet" for a WAITING row."""
        if r.get("kind") == "pair":
            legs = [o for o in ("CE", "PE") if r["legs"][o].get("armed")]
            return (" + ".join(legs) + " armed") if legs else "not armed yet (no leg at or below its price)"
        return "armed" if r.get("armed") else "not armed yet (not at or below the price)"

    @staticmethod
    def pair_title(r) -> str:
        if r.get("auto") == "cross":                       # BEP Pair cross row: the prices are the legs' BEPs
            return f"{r['ce_strike']}CE / {r['pe_strike']}PE · BEP {r['ce_price']:.2f} / {r['pe_price']:.2f}"
        pe = "" if r["pe_price"] == r["ce_price"] else f" (PE @ {r['pe_price']:g})"
        return f"{r['ce_strike']}CE / {r['pe_strike']}PE @ {r['ce_price']:g}{pe}"

    @staticmethod
    def status_text(r) -> str:
        text = CommonWatch._status_text(r)
        p = r.get("pending")
        if r.get("order_status") == "PENDING" and p:
            arrow = "\u2193" if p["dir"] == "below" else "\u2191"
            text += f" \u00b7 Pending entry @ {p['price']:.2f} {arrow}"
        return text

    @staticmethod
    def _status_text(r) -> str:
        if r.get("kind") == "pair":
            opp = "PE" if r.get("opt") == "CE" else "CE"
            if r["status"] == "watching":
                if r.get("auto_open") and r.get("auto") != "cross":
                    now = datetime.now(IST)
                    t = now.strftime("%H:%M:%S")
                    if t < config.PAIR_AUTO_TO:
                        legs = [o for o in ("CE", "PE") if r["legs"][o].get("auto_below")]
                        how = ("live LTP crosses above its price" if r.get("trig_on") == "ltp" else
                               f"{CommonWatch.tf_label(r)} candle closes above its price")
                        return (f"AUTO {config.PAIR_AUTO_FROM[:5]}-{config.PAIR_AUTO_TO[:5]}: the first leg whose "
                                f"{how} is ordered \u00b7 "
                                + ((" + ".join(legs) + " at or below its price") if legs else
                                   "no leg at or below its price yet"))
                    return (f"Auto order ON: the Pair rule places the order by itself (until 15:15)"
                            + (f"; {CommonWatch.tf_label(r)} \u2192 1 min candles after {config.PAIR_AUTO_TO[:5]}"
                               if r.get("tf_sec") else "")
                            + " \u00b7 watching both legs \u00b7 " + CommonWatch.armed_text(r))
                return "Watching both legs \u00b7 " + CommonWatch.armed_text(r)
            text = (f"AUTO: {r['opt']} {r.get('auto_tf') or ''} candle closed above {r['price']:g} at {r['trig_time']}, "
                    f"close {r['trig_close']:.2f}" if r.get("trig_how") == "auto" and r.get("auto_how") == "close" else
                    f"AUTO: {r['opt']} LTP crossed above {r['price']:g} at {r['trig_time']}, LTP {r['trig_close']:.2f}"
                    if r.get("trig_how") == "auto" else
                    f"{r['opt']} LTP above at {r['trig_time']}, LTP {r['trig_close']:.2f}" if r.get("trig_how") == "ltp"
                    else f"{r['opt']} closed above at {r['trig_time']}, close {r['trig_close']:.2f}")
            if r.get("auto_fire"):
                text = "AUTO (Pair rule): " + text
            if r.get("auto") == "cross":
                text = f"BEP cross {r['trig_time']}: {r['opt']} closed {r['trig_close']:.2f} above BEP"
            if r["status"] == "failed" and r.get("fail_why"):
                text += f" \u2192 failed at {r['fail_time']}: {r['fail_why']}"
            elif r["status"] == "failed":
                text += f" \u2192 failed: {opp} closed {r['fail_close']:.2f} at {r['fail_time']}"
            elif r["status"] == "exited":
                text += (f" \u2192 opponent SL: {opp} closed {r['opp_close']:.2f} at {r['fail_time']}"
                         + (f"; square-off {r['squareoff']}" if r.get("squareoff") else ""))
            return text

        if r.get("source") == "manual":
            return ("Manual STOP-LIMIT entry" if r.get("entry_trigger") else
                    "Manual LIMIT entry" if r.get("entry_mode") == "limit" else "Manual entry") \
                + f" added {r['trig_time']}"
        if r.get("source") == "star":
            text = f"\u2b50 support at {r['trig_time']}, close {r['trig_close']:.2f}"
            if r["status"] == "failed":
                fc = r.get("fail_close")
                text += f" \u2192 failed at {r['fail_time']}" + (f", close {fc:.2f}" if fc is not None else "")
            return text
        if r["status"] == "triggered":
            return f"Triggered at {r['trig_time']}, " + ("LTP" if r.get("trig_how") == "ltp" else "close") \
                + f" {r['trig_close']:.2f}"
        if r["status"] == "failed":
            return (f"Triggered at {r['trig_time']}, close {r['trig_close']:.2f} → "
                    f"failed at {r['fail_time']}, close {r['fail_close']:.2f}")
        return "Watching \u00b7 " + CommonWatch.armed_text(r)
