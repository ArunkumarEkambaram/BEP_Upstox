"""
limit_orders.py - Manual Entry "Limit" mode: an AlgoDelta limit_order whose SL / Target / trailing SL are watched
by the APP (AlgoDelta's limit_order has no SL / Target fields). When one is hit the app sends squareoff_position.

The state lives on the Order Details row, as JSON in its "mg" column (so it survives a restart):
  price      limit price                       units     ordered quantity in units (lots x lot size)
  state      OPEN | FILLED | CANCEL SENT | CANCELLED | CANCEL FAILED | REJECTED
  msg        last status message               sent_at   "HH:MM:SS" the limit_order was sent
  oid        Upstox order id (live, once found in the order book)
  filled     units filled so far               fill      average fill price     fill_time  "HH:MM:SS" first fill
  sl / tgt   exit_rules rule dicts (LTP / POINT) watched on the live LTP; a CANDLE rule sits in the row's
             c_sl / c_tgt instead (checked on candle closes, like any app-watched rule)
  trail      {"trigger", "gap", "step"} or None (moves the SL, never down)
  high       highest LTP since the fill        cancel_ts  epoch seconds the cancel_order was sent
  exit       text once the app sent its exit (so it is sent only once)
  trigger    stop-limit only (AlgoDelta sl_order, PR #36): the BUY waits at the exchange until the LTP rises to
             the trigger, then it is a limit order at price      triggered  True once that happened

OPEN may already be partly filled: the filled part is protected while the rest waits.
Pure functions only (no Upstox / AlgoDelta calls) so they are easy to test.
"""
import json

PENDING = ("OPEN", "CANCEL SENT", "CANCEL FAILED")      # the order may still (partly) fill
CANCELLABLE = ("OPEN", "CANCEL FAILED")
FINAL = ("FILLED", "CANCELLED", "REJECTED")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse(value):
    """The row's limit state (dict) from its "mg" value; None for a normal order."""
    if not value:
        return None
    if isinstance(value, dict):
        return value
    try:
        m = json.loads(value)
        return m if isinstance(m, dict) and "price" in m else None
    except (TypeError, ValueError):
        return None


def dump(m) -> str:
    return json.dumps(m)


def held(m) -> bool:
    """Some quantity is filled (a position the app protects)."""
    return bool(m) and bool(m.get("filled")) and m.get("fill") is not None


def pending(m) -> bool:
    return bool(m) and m.get("state") in PENDING


def cancellable(m) -> bool:
    return bool(m) and m.get("state") in CANCELLABLE


def sl_level(m):
    """(current SL level, trailed?) from the fill price, the SL rule and the trailing SL; (None, False) if no SL."""
    fill = _f(m.get("fill"))
    rule, t = m.get("sl"), m.get("trail")
    if fill is None or (not rule and not t):
        return None, False
    base = (rule["value"] if rule["type"] == "LTP" else fill - rule["value"]) if rule else None
    high = _f(m.get("high"))
    if t and high is not None and high >= fill + t["trigger"]:     # trails even with only a CANDLE SL (PR #42)
        step = t.get("step") or 1
        new = fill + int((high - t["gap"] - fill) // step) * step
        if base is None or new > base:
            return round(new, 2), True
    return (round(base, 2), False) if base is not None else (None, False)


def tgt_level(m):
    fill = _f(m.get("fill"))
    rule = m.get("tgt")
    if fill is None or not rule:
        return None
    return round(rule["value"] if rule["type"] == "LTP" else fill + rule["value"], 2)


def check_exit(m, ltp):
    """Checks one live LTP against the SL (first) and the Target, then raises the high (trailing SL).
    Returns the exit reason ("App SL" / "App trailing SL" / "App target") or None. Changes m["high"]."""
    if not held(m) or ltp is None or m.get("exit"):
        return None
    sl, trailed = sl_level(m)
    if sl is not None and ltp <= sl:
        return "App trailing SL" if trailed else "App SL"
    tgt = tgt_level(m)
    if tgt is not None and ltp >= tgt:
        return "App target"
    m["high"] = round(max(_f(m.get("high")) or ltp, ltp), 2)
    return None


def dry_fill(m, ltp, stamp) -> bool:
    """Dry run (market hours only - the engine checks): a BUY limit fills once the live LTP is at or below the
    price, at the LOWER of the two (a gap below the price fills near the LTP, like the exchange would).
    Returns True when filled."""
    if m.get("state") != "OPEN" or held(m) or ltp is None:
        return False
    if m.get("trigger") and not m.get("triggered"):           # stop-limit: first the LTP must rise to the trigger
        if ltp < m["trigger"]:
            return False
        m.update(triggered=True, msg=f"Triggered at LTP {ltp:.2f} (dry run): now a limit at {m['price']:.2f}")
    if ltp > m["price"]:
        return False
    fill = round(min(m["price"], ltp), 2)
    m.update(filled=m["units"], fill=fill, fill_time=stamp, high=fill, state="FILLED",
             msg=f"Filled at {fill:.2f} (dry run, LTP {ltp:.2f}, limit {m['price']:.2f})")
    return True


def _hms(ts) -> str:
    s = str(ts or "")
    return s[11:19] if len(s) >= 19 else ""


def _secs(hms):
    try:
        h, mi, s = (int(x) for x in hms.split(":"))
        return h * 3600 + mi * 60 + s
    except (ValueError, AttributeError):
        return None


def match_order(book, m, ik, taken=()):
    """The Upstox order-book entry of this limit order: its order id if known, else the earliest BUY LIMIT on the
    same instrument at the same price, placed from 2 minutes before it was sent, not claimed by another row."""
    if m.get("oid"):
        return next((o for o in book if o.get("order_id") == m["oid"]), None)
    sent = _secs(m.get("sent_at", ""))
    cands = []
    for o in book or []:
        if o.get("order_id") in taken:
            continue
        if (o.get("instrument_token") or o.get("instrument_key")) not in (ik, (ik or "").replace("|", ":")):
            continue
        want = "SL" if m.get("trigger") else "LIMIT"            # Upstox order_type of a stop-limit is "SL"
        if (o.get("transaction_type") or "").upper() != "BUY" or (o.get("order_type") or "").upper() != want:
            continue
        if m.get("trigger") and abs((_f(o.get("trigger_price")) or 0) - m["trigger"]) > 0.051:
            continue
        price = _f(o.get("price"))
        if price is None or abs(price - m["price"]) > 0.051:
            continue
        t = _secs(_hms(o.get("order_timestamp")))
        if sent is not None and t is not None and t < sent - 120:
            continue
        qty_off = 0 if _f(o.get("quantity")) == m.get("units") else 1
        cands.append((qty_off, _hms(o.get("order_timestamp")), o))
    if not cands:
        return None
    return sorted(cands, key=lambda c: (c[0], c[1]))[0][2]


def apply_book(m, o, stamp) -> list:
    """Updates m from its Upstox order-book entry. Returns events: "filled" (first fill), "more" (more filled),
    "cancelled", "rejected"."""
    events = []
    if not m.get("oid") and o.get("order_id"):
        m["oid"] = o["order_id"]
    filled, avg = _f(o.get("filled_quantity")) or 0, _f(o.get("average_price"))
    if filled and avg and filled != (m.get("filled") or 0):
        events.append("more" if m.get("filled") else "filled")
        if not m.get("filled"):
            m["fill_time"] = stamp
        m.update(filled=int(filled), fill=round(avg, 2))
        m["high"] = round(max(_f(m.get("high")) or avg, avg), 2)
    st = (o.get("status") or "").lower()
    if st == "complete":
        m["state"] = "FILLED"
        m["msg"] = f"Filled {int(filled)} at {avg:.2f}" if avg else "Filled"
    elif st == "cancelled":
        if m.get("state") != "CANCELLED":
            events.append("cancelled")
        m["state"] = "CANCELLED"
        m["msg"] = "Cancelled" + (f" ({int(filled)} filled at {avg:.2f} kept)" if filled and avg else "")
    elif st == "rejected":
        if m.get("state") != "REJECTED":
            events.append("rejected")
        m["state"] = "REJECTED"
        m["msg"] = "Rejected: " + str(o.get("status_message") or o.get("status_message_raw") or "no reason given")
    elif m.get("state") not in ("CANCEL SENT", "CANCEL FAILED"):
        m["state"] = "OPEN"
        if m.get("trigger") and "trigger" in st:              # stop-limit still waiting for its trigger
            m["msg"] = f"Waiting at the exchange for the trigger {m['trigger']:.2f} ({o.get('status')})"
        else:
            if m.get("trigger"):
                m["triggered"] = True
            m["msg"] = f"Open at the exchange ({o.get('status')})" + (f", {int(filled)} filled" if filled else "")
    if m.get("trigger") and filled:
        m["triggered"] = True
    return events


def exit_label(m) -> str:
    """Short reason of the app's / your exit for the card chip, e.g. "App trailing SL", "15:15", "Manual"."""
    text = (m or {}).get("exit") or ""
    reason = text.rsplit(" ", 1)[0] if text[-8:].count(":") == 2 else text
    return {"Manual square-off": "Manual", "Square off all": "Manual", "App 15:15 square-off": "15:15",
            "Opponent SL": "Opponent SL"}.get(reason, reason)


def describe(m) -> str:
    """Short text of the app-watched rules, e.g. "SL 88 · Tgt 150 · Trail 20/20"."""
    parts = []
    lvl, trailed = sl_level(m)
    if m.get("sl"):
        r = m["sl"]
        parts.append(f"SL {r['value']:g}{' pts' if r['type'] == 'POINT' else ''}"
                     + (f" (now {lvl:g}{', trailed' if trailed else ''})" if lvl is not None else ""))
    if m.get("tgt"):
        r = m["tgt"]
        parts.append(f"Tgt {r['value']:g}{' pts' if r['type'] == 'POINT' else ''}")
    if m.get("trail"):
        parts.append(f"Trail {m['trail']['trigger']:g}/{m['trail']['gap']:g}"
                     + (f" (SL now {lvl:g})" if not m.get("sl") and lvl is not None else ""))
    return " · ".join(parts)


def state_text(m) -> str:
    """Card chip for a limit order that has no position yet (None once something is filled)."""
    s = m.get("state")
    if held(m):
        return None
    if s == "OPEN" and m.get("trigger") and not m.get("triggered"):
        return f"STOP @ {m['trigger']:.2f} → LIMIT {m['price']:.2f}"
    return {"OPEN": f"LIMIT OPEN @ {m['price']:.2f}", "CANCEL SENT": "CANCELLING",
            "CANCEL FAILED": "CANCEL FAILED", "CANCELLED": "CANCELLED", "REJECTED": "REJECTED"}.get(s, s or "")
