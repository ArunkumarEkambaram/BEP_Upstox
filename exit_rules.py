"""
exit_rules.py - SL / Target rules for an order, evaluated on Upstox 1-minute candles.

A rule is a dict:
    {"kind": "sl" | "tgt", "type": "LTP" | "POINT" | "CANDLE", "value": float, "tf": 1 | 3 | 5 (CANDLE only),
     "set_at": "HH:MM:SS" (rule is active for candles after this minute; "" = from entry),
     "custom": bool (set per order in the app), "trail": {"trigger", "gap", "step"} (common SL only)}

  LTP    : the level is that price.            SL hit when a 1-min low <= level, exit at the level.
  POINT  : the level is entry -/+ value.       Target hit when a 1-min high >= level, exit at the level.
  CANDLE : the level is that price, checked only when a candle of `tf` minutes CLOSES (candles aligned to 09:15):
           SL exits when that close is BELOW the level, Target when it is ABOVE; exit at that close.
           (A brief touch of the level inside the candle does not exit.)
  Trailing SL (common SL only): once the highest price reaches entry + trigger, the SL level becomes
           highest - gap in steps of `step`, never moving down.

Per 1-minute candle, the order of checks is: end of day (if enabled) -> price SLs -> candle SLs -> price
targets -> candle targets -> raise the trailing SL. So when an SL and a target are hit in the same minute,
the SL wins (cautious).
"""
import json
from datetime import datetime, time as dtime, timedelta

import market_utils

IST = market_utils.IST
EXIT_TIME = dtime(15, 15)              # dry-run trades still open are closed at the 15:15 candle's open
SESSION_START = dtime(9, 15)
TYPES = ("LTP", "POINT", "CANDLE")
NONE = "NONE"           # PR #48: Target "None" = this order has no target (exits by SL / trailing SL / manual / 15:15)
TIMEFRAMES = (1, 3, 5)


def make_rule(kind: str, rtype: str, value: float, tf=None, set_at: str = "", custom: bool = True) -> dict:
    rtype = rtype.upper()
    if kind == "tgt" and rtype == NONE:
        return {"kind": kind, "type": NONE, "value": 0.0, "tf": None, "set_at": set_at, "custom": custom}
    if kind not in ("sl", "tgt") or rtype not in TYPES:
        raise ValueError(f"bad rule {kind} {rtype}")
    return {"kind": kind, "type": rtype, "value": float(value),
            "tf": int(tf) if rtype == "CANDLE" else None, "set_at": set_at, "custom": custom}


def parse(value):
    """Rule from a dict or the JSON text stored in the orders file; None if empty / invalid."""
    if not value:
        return None
    if isinstance(value, dict):
        return value
    try:
        rule = json.loads(value)
        return rule if isinstance(rule, dict) and rule.get("type") in TYPES + (NONE,) else None
    except (TypeError, ValueError):
        return None


def describe(rule) -> str:
    if not rule:
        return ""
    if is_none(rule):
        return "None (trail / SL only)"
    v = f"{rule['value']:g}"
    if rule["type"] == "CANDLE":
        side = "below" if rule["kind"] == "sl" else "above"
        return f"{v} on {rule['tf']}m candle close {side}"
    return f"{v} pts" if rule["type"] == "POINT" else f"{v} (LTP)"


def is_none(rule) -> bool:
    """Target "None": no target at all (not the same as no rule = the default target)."""
    return bool(rule) and rule.get("type") == NONE


def _minute(day: datetime, hhmmss: str):
    try:
        hh, mm, *_ = (int(x) for x in hhmmss.split(":"))
    except (ValueError, AttributeError):
        return None
    return day.replace(hour=hh, minute=mm, second=0, microsecond=0)


def _level(rule, entry):
    if rule["type"] == "POINT":
        return entry - rule["value"] if rule["kind"] == "sl" else entry + rule["value"]
    return rule["value"]


def _reason(rule, trailed=False):
    if rule["type"] == "CANDLE":
        base = f"{'Candle SL' if rule['kind'] == 'sl' else 'Candle target'} ({rule['tf']}m)"
    elif rule["kind"] == "sl":
        base = "Trailing SL" if trailed else "SL"
    else:
        base = "Target"
    if not rule.get("custom"):
        return base
    return "Custom " + (base if base.startswith("SL") else base[0].lower() + base[1:])


def evaluate(entry: float, entry_time: str, candles: list, rules: list, now, eod: bool = True):
    """First exit of `rules` on 1-minute `candles` after the entry minute. Returns (price, "HH:MM", reason) or None."""
    now = now.astimezone(IST) if now.tzinfo else IST.localize(now)
    entry_min = _minute(now, entry_time)
    if entry_min is None or entry is None:
        return None
    active = []
    for r in rules or []:
        if not r or r.get("value") is None or is_none(r):
            continue
        start = _minute(now, r.get("set_at") or "") or entry_min
        active.append({**r, "start": max(start, entry_min), "level": _level(r, entry), "trailed": False})
    if not active and not eod:
        return None
    high = entry
    session0 = now.replace(hour=SESSION_START.hour, minute=SESSION_START.minute, second=0, microsecond=0)
    seq = sorted(candles or [], key=lambda c: c[0])
    for c in seq:
        start = datetime.fromisoformat(c[0])
        start = start.astimezone(IST) if start.tzinfo else IST.localize(start)
        if start.date() != entry_min.date() or start <= entry_min:
            continue
        o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
        end = start + timedelta(minutes=1)
        label_now = start.strftime("%H:%M")
        if eod and start.time() >= EXIT_TIME:
            return round(o, 2), label_now, "End of day 15:15"
        live = [r for r in active if start > r["start"]]
        bucket_live = [r for r in active if r["type"] == "CANDLE" and end > r["start"]
                       and int((end - session0).total_seconds() // 60) % r["tf"] == 0]
        hits = [r for r in live if r["kind"] == "sl" and r["type"] != "CANDLE" and l <= r["level"]]
        if hits:
            r = max(hits, key=lambda x: x["level"])
            return round(r["level"], 2), label_now, _reason(r, r["trailed"])
        hits = [r for r in bucket_live if r["kind"] == "sl" and cl < r["level"]]
        if hits:
            return round(cl, 2), end.strftime("%H:%M"), _reason(hits[0])
        hits = [r for r in live if r["kind"] == "tgt" and r["type"] != "CANDLE" and h >= r["level"]]
        if hits:
            r = min(hits, key=lambda x: x["level"])
            return round(r["level"], 2), label_now, _reason(r)
        hits = [r for r in bucket_live if r["kind"] == "tgt" and cl > r["level"]]
        if hits:
            return round(cl, 2), end.strftime("%H:%M"), _reason(hits[0])
        high = max(high, h)
        for r in active:
            t = r.get("trail")
            if r["kind"] == "sl" and t and r["type"] != "CANDLE" and high >= entry + t["trigger"]:
                new = entry + int((high - t["gap"] - entry) // (t.get("step") or 1)) * (t.get("step") or 1)
                if new > r["level"]:
                    r["level"], r["trailed"] = new, True
    if eod and now.time() >= EXIT_TIME and now.date() == entry_min.date() and seq:
        return round(float(seq[-1][4]), 2), now.strftime("%H:%M"), "End of day 15:15"
    return None
