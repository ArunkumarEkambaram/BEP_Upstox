"""
pair_cross.py - BEP Pair cross: a CE and a PE near ATM that split around their BEPs (decision 25).

Checked on every closed 5-minute candle (the same candles as the BEP Support board), independent of the
board's support rows and of the star rows.

  PAIRS     ATM = the strike nearest NIFTY's close of that candle (step = the index's strike interval).
            Buy CE (bullish):   the leg closing ABOVE (the one to buy) is the CE at ATM or up to ITM_STRIKES
                                strikes ITM (ATM - step, ATM - 2 step), the opponent closing BELOW is the
                                PE at ATM - step, ATM or ATM + step.
            Buy PE (bearish):   the leg closing ABOVE is the PE at ATM or up to ITM_STRIKES strikes ITM
                                (ATM + step, ATM + 2 step), the opponent is the CE at ATM +/- step.
  SIGNAL    on this candle: up-leg close > its BEP AND down-leg close < its BEP, and the previous candle was
            NOT in that state (a fresh split; either leg may have crossed first, e.g. PE below at 09:40 and the
            CE crossing above at 09:45). A pair already split on the day's first candle counts.
  TOUCH     both legs must have TOUCHED their BEP (candle low <= BEP + TOUCH_TOL and high >= BEP - TOUCH_TOL)
            on this candle or one of the TOUCH_CANDLES - 1 candles before it. A full-body cross touches anyway;
            a leg may touch, then move away ("revert") before the split.
  ROWS      one row per candle and direction: the best pair (same strike first, else the smallest BEP gap,
            then nearest ATM); the other qualifying pairs go in "also". No new row for a pair while a row
            holding it is WATCHING.
  WATCH     the WATCH_CANDLES candles after the signal candle (6 = 30 min):
              FAILED: the up-leg closes below its BEP, or the down-leg closes above its BEP;
              HELD:   neither happens.
            best_up / best_dn = the best move of each leg (up-leg high above its signal close, down-leg low
            below its signal close) while watching.
"""
from datetime import timedelta

import config

CANDLE = timedelta(minutes=5)


def _cfg(name, default):
    return getattr(config, name, default)


class PairCross:
    def __init__(self, index: str, step: int):
        self.index = index
        self.step = int(step)
        self.reset()

    def reset(self):
        self.rows = []
        self._seq = 0
        self.last_start = None          # last candle start processed

    # ------------------------------------------------------------------ helpers
    def needed_legs(self, atm) -> set:
        """(strike, opt) legs whose candles the next process() call needs: ATM +/- 1 strike (both options),
        plus the legs of rows still being watched."""
        n = max(1, self._itm())
        legs = {(atm + d * self.step, o) for d in range(-n, n + 1) for o in ("CE", "PE")} if atm is not None else set()
        for r in self.rows:
            if r["status"] == "watching":
                legs.add((r["up"]["strike"], r["up"]["opt"]))
                legs.add((r["dn"]["strike"], r["dn"]["opt"]))
        return legs

    @staticmethod
    def _itm() -> int:
        return int(_cfg("BEP_CROSS_ITM_STRIKES", 2))

    def _candidates(self, atm):
        s, n = self.step, self._itm()
        below = (atm - s, atm, atm + s)
        return (("up", [(atm - i * s, "CE") for i in range(n + 1)], [(k, "PE") for k in below]),
                ("down", [(atm + i * s, "PE") for i in range(n + 1)], [(k, "CE") for k in below]))

    @staticmethod
    def _key(up, dn):
        return (up[0], up[1], dn[0], dn[1])

    def _watching_keys(self) -> set:
        keys = set()
        for r in self.rows:
            if r["status"] == "watching":
                keys.update(r["keys"])
        return keys

    # ------------------------------------------------------------------ one candle
    def process(self, start, atm, nifty, candle_of, bep_of) -> list:
        """Processes the candle starting at `start` (call in time order, once per start).
        candle_of(strike, opt, start) -> (o, h, l, c) or None; bep_of(strike) -> BEP or None.
        Returns the NEW rows (signals) of this candle."""
        self._watch(start, candle_of)
        new = self._signals(start, atm, nifty, candle_of, bep_of) if atm is not None else []
        self.last_start = start
        return new

    def _watch(self, start, candle_of) -> bool:
        changed = False
        n_max = int(_cfg("BEP_CROSS_WATCH_CANDLES", 6))
        label = start.strftime("%H:%M")
        for r in self.rows:
            if r["status"] != "watching" or start <= r["start"]:
                continue
            cu = candle_of(r["up"]["strike"], r["up"]["opt"], start)
            cd = candle_of(r["dn"]["strike"], r["dn"]["opt"], start)
            if cu is None or cd is None:
                continue                                   # a missing candle is not counted
            r["n"] += 1
            r["best_up"] = max(r["best_up"], round(cu[1] - r["up"]["close"], 2))
            r["best_dn"] = max(r["best_dn"], round(r["dn"]["close"] - cd[2], 2))
            r["up"]["last"], r["dn"]["last"] = cu[3], cd[3]
            why = []
            if cu[3] < r["up"]["bep"]:
                why.append(f"{r['up']['strike']}{r['up']['opt']} closed {cu[3]:.2f} below BEP")
            if cd[3] > r["dn"]["bep"]:
                why.append(f"{r['dn']['strike']}{r['dn']['opt']} closed {cd[3]:.2f} above BEP")
            if why:
                r.update(status="failed", fail_time=label, fail_why="; ".join(why),
                         fail_close=cu[3] if cu[3] < r["up"]["bep"] else cd[3])
            elif r["n"] >= n_max:
                r.update(status="held", held_time=label)
            changed = True
        return changed

    def _touched(self, strike, opt, start, bep, candle_of) -> bool:
        tol = float(_cfg("BEP_CROSS_TOUCH_TOL", 1.0))
        for i in range(int(_cfg("BEP_CROSS_TOUCH_CANDLES", 3))):
            c = candle_of(strike, opt, start - i * CANDLE)
            if c is not None and c[2] <= bep + tol and c[1] >= bep - tol:
                return True
        return False

    def _signals(self, start, atm, nifty, candle_of, bep_of) -> list:
        watching = self._watching_keys()
        new = []
        for direction, ups, downs in self._candidates(atm):
            hits = []
            for up in ups:
                bu = bep_of(up[0])
                cu = candle_of(up[0], up[1], start)
                if bu is None or cu is None or not cu[3] > bu:
                    continue
                pu = candle_of(up[0], up[1], start - CANDLE)
                for dn in downs:
                    bd = bep_of(dn[0])
                    cd = candle_of(dn[0], dn[1], start)
                    if bd is None or cd is None or not cd[3] < bd:
                        continue
                    pd = candle_of(dn[0], dn[1], start - CANDLE)
                    if pu is not None and pd is not None and pu[3] > bu and pd[3] < bd:
                        continue                           # already split on the previous candle: not fresh
                    if self._key(up, dn) in watching:
                        continue
                    if not (self._touched(up[0], up[1], start, bu, candle_of)
                            and self._touched(dn[0], dn[1], start, bd, candle_of)):
                        continue
                    rank = (0 if up[0] == dn[0] else 1, round(abs(bu - bd), 2), abs(up[0] - atm) + abs(dn[0] - atm))
                    hits.append((rank, up, dn, bu, bd, cu, cd))
            if not hits:
                continue
            hits.sort(key=lambda h: h[0])
            _rank, up, dn, bu, bd, cu, cd = hits[0]
            self._seq += 1
            row = {
                "id": self._seq, "index": self.index, "start": start, "time": start.strftime("%H:%M"),
                "dir": direction, "nifty": nifty, "atm": atm,
                "up": {"strike": up[0], "opt": up[1], "bep": round(bu, 2), "close": cu[3], "last": cu[3]},
                "dn": {"strike": dn[0], "opt": dn[1], "bep": round(bd, 2), "close": cd[3], "last": cd[3]},
                "also": [f"{h[1][0]}{h[1][1]}/{h[2][0]}{h[2][1]}" for h in hits[1:]],
                "keys": [self._key(h[1], h[2]) for h in hits],
                "status": "watching", "n": 0, "best_up": 0.0, "best_dn": 0.0,
                "fail_time": "", "fail_why": "", "fail_close": None, "held_time": "",
            }
            self.rows.append(row)
            new.append(row)
        return new

    # ------------------------------------------------------------------ "Why no row?" checker
    def explain(self, ce_strike: int, pe_strike: int, start, atm, nifty, candle_of, bep_of) -> list:
        """Checks one CE / PE pair on the candle starting at `start` against every rule, in order.
        Returns [(ok: bool | None, text)]; None = information only."""
        out = []
        label = start.strftime("%H:%M")
        legs = {"CE": ce_strike, "PE": pe_strike}
        bep = {o: bep_of(k) for o, k in legs.items()}
        c = {o: candle_of(k, o, start) for o, k in legs.items()}
        for o in ("CE", "PE"):
            if bep[o] is None:
                out.append((False, f"{legs[o]}{o}: no BEP (not in the uploaded CSV / could not be calculated)."))
            if c[o] is None:
                out.append((False, f"{legs[o]}{o}: no {label} candle."))
        if any(v is None for v in list(bep.values()) + list(c.values())):
            return out
        for o in ("CE", "PE"):
            side = "above" if c[o][3] > bep[o] else "below" if c[o][3] < bep[o] else "exactly at"
            out.append((None, f"{legs[o]}{o} {label}: O {c[o][0]:.2f} H {c[o][1]:.2f} L {c[o][2]:.2f} C {c[o][3]:.2f}"
                              f" → closed {side} BEP {bep[o]:.2f}"))
        if c["CE"][3] > bep["CE"] and c["PE"][3] < bep["PE"]:
            direction, up, dn = "up", ("CE", ce_strike), ("PE", pe_strike)
        elif c["PE"][3] > bep["PE"] and c["CE"][3] < bep["CE"]:
            direction, up, dn = "down", ("PE", pe_strike), ("CE", ce_strike)
        else:
            out.append((False, "Not split: one leg must close ABOVE its BEP and the other BELOW its BEP."))
            return out
        buy = f"{up[1]}{up[0]}"
        out.append((True, f"Split: buy leg {buy} above its BEP, opponent {dn[1]}{dn[0]} below its BEP."))
        if atm is None:
            out.append((False, f"No NIFTY close / ATM for the {label} candle."))
            return out
        _d, ups, downs = next(x for x in self._candidates(atm) if x[0] == direction)
        ns = f"NIFTY close {nifty:.2f}, " if nifty is not None else ""
        if (up[1], up[0]) not in ups:
            out.append((False, f"Buy leg out of range: {ns}ATM {atm}; the buy {up[0]} must be one of "
                               f"{', '.join(str(k) for k, _o in ups)} (ATM or up to {self._itm()} strikes ITM)."))
        else:
            out.append((True, f"Buy leg in range ({ns}ATM {atm})."))
        if (dn[1], dn[0]) not in downs:
            out.append((False, f"Opponent out of range: the {dn[0]} must be one of "
                               f"{', '.join(str(k) for k, _o in downs)} (ATM ± 1 strike)."))
        else:
            out.append((True, "Opponent in range."))
        pu, pd = candle_of(up[1], up[0], start - CANDLE), candle_of(dn[1], dn[0], start - CANDLE)
        bu, bd = bep[up[0]], bep[dn[0]]
        if pu is not None and pd is not None and pu[3] > bu and pd[3] < bd:
            out.append((False, "Not fresh: the pair was already split on the previous candle "
                               f"({buy} {pu[3]:.2f}, {dn[1]}{dn[0]} {pd[3]:.2f})."))
        else:
            out.append((True, "Fresh: the previous candle was not split."))
        tol, n_touch = float(_cfg("BEP_CROSS_TOUCH_TOL", 1.0)), int(_cfg("BEP_CROSS_TOUCH_CANDLES", 3))
        for o, k, b in ((up[0], up[1], bu), (dn[0], dn[1], bd)):
            hits = [(start - i * CANDLE).strftime("%H:%M") for i in range(n_touch)
                    if (x := candle_of(k, o, start - i * CANDLE)) is not None and x[2] <= b + tol and x[1] >= b - tol]
            out.append((bool(hits), f"{k}{o} touched its BEP (±{tol:g}) at {', '.join(hits)}." if hits else
                        f"{k}{o} did NOT touch its BEP (±{tol:g}) on this candle or the {n_touch - 1} before."))
        key = self._key((up[1], up[0]), (dn[1], dn[0]))
        for r in self.rows:
            if key not in r["keys"]:
                continue
            if r["start"] == start:
                main = self._key((r["up"]["strike"], r["up"]["opt"]), (r["dn"]["strike"], r["dn"]["opt"])) == key
                out.append((True, f"Signalled: row {r['time']}" + ("" if main else " (listed in its Also column; "
                                                                    "the main pair ranked first)") + "."))
                return out
            end = r.get("fail_time") or r.get("held_time")
            if r["start"] < start and (r["status"] == "watching" or (end and end > label)):
                out.append((False, f"Blocked: the same pair was still WATCHING (row {r['time']}, "
                                   f"{self.status_text(r)})."))
                return out
        if all(ok is not False for ok, _t in out):
            out.append((None, "All rules pass but there's no row: the candle may not be processed yet."))
        return out

    # ------------------------------------------------------------------ views
    @staticmethod
    def pair_text(r) -> str:
        return f"Buy {r['up']['strike']}{r['up']['opt']} / opponent {r['dn']['strike']}{r['dn']['opt']}"

    @staticmethod
    def status_text(r) -> str:
        if r["status"] == "watching":
            return f"WATCHING {r['n']}/{int(_cfg('BEP_CROSS_WATCH_CANDLES', 6))}"
        if r["status"] == "held":
            return f"HELD · {r['held_time']}"
        return f"FAILED · {r['fail_time']}"

    def export_rows(self) -> list:
        out = []
        for r in self.rows:
            out.append({
                "index": self.index, "time": r["time"], "direction": "Buy CE" if r["dir"] == "up" else "Buy PE", "nifty": r["nifty"] if r["nifty"] is not None else "", "atm": r["atm"],
                "up_leg": f"{r['up']['strike']}{r['up']['opt']}", "up_bep": r["up"]["bep"], "up_close": r["up"]["close"],
                "dn_leg": f"{r['dn']['strike']}{r['dn']['opt']}", "dn_bep": r["dn"]["bep"], "dn_close": r["dn"]["close"],
                "best_up": r["best_up"], "best_dn": r["best_dn"], "status": self.status_text(r),
                "fail_why": r["fail_why"], "also": ", ".join(r["also"]),
            })
        return out
