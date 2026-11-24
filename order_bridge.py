"""
order_bridge.py - turns an order signal into the AlgoDelta JSON Bridge payload and POSTs it.
"""
import copy
import json
import re
from datetime import datetime

import requests
import config

_today = {"date": None, "count": 0}

_PLACEHOLDER = re.compile(r"(?<!\{)\{(\w+)\}(?!\})")
# Placeholders that fill a whole JSON value keep their real type (number / true / false), not a string.
_NUMERIC = ("qty", "strike", "tgt", "sl", "trail_trigger", "trail_gap", "trail_point", "is_trail", "exit_on_opposite")


def _fill(value, ctx):
    if isinstance(value, dict):
        return {k: _fill(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, ctx) for v in value]
    if isinstance(value, str):
        m = _PLACEHOLDER.fullmatch(value)
        if m and m.group(1) in _NUMERIC and m.group(1) in ctx:
            return ctx[m.group(1)]                   
        return _PLACEHOLDER.sub(lambda mm: str(ctx[mm.group(1)]) if mm.group(1) in ctx else mm.group(0), value)
    return value


def _expiry_code(expiry) -> str:
    try:
        return datetime.strptime(str(expiry), "%Y-%m-%d").strftime("%d%b%y").upper()
    except ValueError:
        return ""


def symbol(index, expiry, strike, opt) -> str:
    """AlgoDelta script name, e.g. NIFTY26NOV2625400CE (same format the order JSON uses)."""
    return f"{index}{_expiry_code(expiry)}{int(strike)}{opt}"


def _num(value, default):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return int(f) if f == int(f) else f


def build_payload(sig: dict, template: dict = None, params: dict = None) -> dict:
    cfg = config.AUTO_ORDER[sig["index"]]
    order_type = sig.get("order_type", "strategy_order")
    if template is None:
        template = cfg["templates"].get(order_type) or {"type": order_type}
        
    p = {"tgt": cfg["tgt"], "sl": cfg["sl"], "qty": cfg["qty"],
         "tgt_type": cfg["tgt_type"], "sl_type": cfg["sl_type"],
         "exit_on_opposite": cfg.get("exit_on_opposite", False), "trail": cfg.get("trail", False),
         "trail_type": cfg.get("trail_type", "LTP"), "trail_trigger": cfg.get("trail_trigger", 20),
         "trail_point": cfg.get("trail_point", 1), "trail_gap": cfg.get("trail_gap", 20)}
    p.update(params or {})
    
    strike, opt, index = int(sig["strike"]), sig["opt"], sig["index"]
    expiry = _expiry_code(sig.get("expiry"))
    ctx = {
        "index": index, "opt": opt, "strike": strike, "expiry": expiry,
        "symbol": f"{index}{expiry}{strike}{opt}",
        "signal": "UP" if opt == "CE" else "DOWN",
        "bep": sig["bep"], "ltp": sig["ltp"],
        "qty": max(1, int(_num(p["qty"], 1))),
        "tgt": _num(p["tgt"], 0), "sl": _num(p["sl"], 0),
        "tgt_type": p["tgt_type"], "sl_type": p["sl_type"],
        "exit_on_opposite": bool(p["exit_on_opposite"]), "is_trail": bool(p["trail"]),
        "trail_type": p["trail_type"], "trail_trigger": _num(p["trail_trigger"], 0),
        "trail_gap": _num(p["trail_gap"], 0), "trail_point": _num(p["trail_point"], 0),
    }
    payload = _fill(copy.deepcopy(template), ctx)
    # The UI controls always win, even if the JSON template holds fixed values for these keys.
    payload.update({
        "exit_on_opposite": ctx["exit_on_opposite"], "is_trail_set": ctx["is_trail"],
        "trail_type": ctx["trail_type"], "trail_trigger": ctx["trail_trigger"],
        "trail_gap": ctx["trail_gap"], "trail_point": ctx["trail_point"],
    })
    # A CANDLE SL / Target is watched by the app (square-off when hit), so AlgoDelta must not hold its own.
    if p.get("is_sl") is False:
        payload.update(is_sl=False, is_trail_set=False)
    if p.get("is_tgt") is False:
        payload["is_tgt"] = False
    return payload


def _describe(sig: dict) -> str:
    head = f"{sig['index']} {sig['opt']} {sig['strike']}"
    if sig.get("kind") == "star":
        return f"{head} [manual, \u2b50 support, BEP {sig['bep']:.2f}] (LTP {sig['ltp']:.2f})"
    if sig.get("kind") == "pair":
        return f"{head} [manual, pair @ {sig['bep']:g}] (LTP {sig['ltp']:.2f})"
    if sig.get("kind") == "manual":
        return f"{head} [manual entry] (LTP {sig['ltp']:.2f})"
    if sig.get("kind") == "common":
        return f"{head} [manual, common price {sig['bep']:g}] (LTP {sig['ltp']:.2f})"
    return f"{head} [opening candle] (BEP {sig['bep']:.2f}, LTP {sig['ltp']:.2f})"


def _res(status, message, payload, detail):
    return {"status": status, "message": message, "payload": payload, "detail": detail}


def send_order(sig: dict) -> dict:
    order_type = sig.get("order_type", "strategy_order")
    cfg = config.AUTO_ORDER[sig["index"]]
    template = cfg["templates"].get(order_type) or {"type": order_type}
    payload = build_payload(sig, None, sig.get("params"))
    text = json.dumps(payload)
    head = _describe(sig)

    if config.AUTO_ORDER_PAUSED:
        return _res("blocked", f"Algo trading is PAUSED; NOT sent: {head}", payload, "Algo trading paused")

    today = datetime.now().strftime("%Y-%m-%d")
    if _today["date"] != today:
        _today.update(date=today, count=0)

    why = None
    if config.AUTO_ORDER_DRY_RUN:
        why = "dry run"
    elif len(template) <= 1:
        why = f"no JSON template for {order_type} yet"
    elif not cfg["confirmed"]:
        why = "JSON template not confirmed"
    elif not cfg["webhook_url"]:
        why = "no webhook URL set"
    if why:
        return _res("dry_run", f"DRY RUN ({why}) {head}: {text}", payload, why)

    if _today["count"] >= config.AUTO_ORDER_DAILY_LIMIT:
        return _res("blocked", f"Daily order limit ({config.AUTO_ORDER_DAILY_LIMIT}) reached; NOT sent: {head}", payload,
                    f"Daily limit ({config.AUTO_ORDER_DAILY_LIMIT}) reached")
    
    _today["count"] += 1
    try:
        resp = requests.post(cfg["webhook_url"], json=payload, timeout=5)
        body = (resp.text or "")[:200]
        if 200 <= resp.status_code < 300:
            return _res("sent", f"ORDER SENT {head} -> HTTP {resp.status_code} {body}", payload, f"HTTP {resp.status_code} {body}")
        return _res("failed", f"ORDER FAILED {head} -> HTTP {resp.status_code} {body} (not retried)", payload,
                    f"HTTP {resp.status_code} {body} (not retried)")
    except Exception as ex:
        return _res("failed", f"ORDER FAILED {head} -> {ex} (not retried)", payload, f"{ex} (not retried)")


def _product(index) -> str:
    return (config.AUTO_ORDER[index]["templates"].get("strategy_order") or {}).get("product", "INTRADAY")


def _post_exit(index: str, payload: dict, head: str) -> dict:
    """POSTs a square-off / cancel. Not blocked by Pause algo trade or the daily order limit (it only closes or
    cancels). Dry run (or no webhook / unconfirmed template) only logs it."""
    cfg = config.AUTO_ORDER[index]
    why = None
    if config.AUTO_ORDER_DRY_RUN:
        why = "dry run"
    elif not cfg["confirmed"]:
        why = "JSON template not confirmed"
    elif not cfg["webhook_url"]:
        why = "no webhook URL set"
    if why:
        return _res("dry_run", f"DRY RUN ({why}) {head}: {json.dumps(payload)}", payload, why)
    try:
        resp = requests.post(cfg["webhook_url"], json=payload, timeout=5)
        body = (resp.text or "")[:200]
        ok = 200 <= resp.status_code < 300
        return _res("sent" if ok else "failed", f"{head} -> HTTP {resp.status_code} {body}", payload,
                    f"HTTP {resp.status_code} {body}")
    except Exception as ex:
        return _res("failed", f"{head} FAILED -> {ex} (not retried)", payload, f"{ex} (not retried)")


def send_squareoff(index: str, opt: str, strike, expiry, reason: str = "") -> dict:
    """AlgoDelta squareoff_position for one option (closes the whole position on it)."""
    sym = symbol(index, expiry, strike, opt)
    payload = {"type": "squareoff_position", "script_type": opt, "script": sym, "product": _product(index)}
    return _post_exit(index, payload, f"SQUARE-OFF {sym}" + (f" ({reason})" if reason else ""))


def send_cancel(index: str, opt: str, strike, expiry, reason: str = "") -> dict:
    """AlgoDelta cancel_order for one option. It has no order id, so it may cancel EVERY pending order on that
    option (other limit orders, and possibly the broker SL / Target orders of an open strategy order)."""
    sym = symbol(index, expiry, strike, opt)
    payload = {"type": "cancel_order", "script_type": opt, "script": sym, "product": _product(index)}
    return _post_exit(index, payload, f"CANCEL ORDER {sym}" + (f" ({reason})" if reason else ""))


def limit_payload(sig: dict) -> dict:
    """AlgoDelta limit_order (BUY). quantity = lots, like strategy_order. No SL / Target fields."""
    return {"type": "limit_order", "script_type": sig["opt"],
            "script": symbol(sig["index"], sig.get("expiry"), sig["strike"], sig["opt"]),
            "product": _product(sig["index"]),
            "quantity": max(1, int(_num((sig.get("params") or {}).get("qty"), 1))),
            "price": _num(round(float(sig["price"]), 2), 0), "transaction_type": "BUY"}


def entry_payload(sig: dict) -> dict:
    """limit_order, or AlgoDelta sl_order (BUY stop-limit: waits at the exchange until the LTP rises to
    trigger_price, then becomes a limit order at price) when the signal has a trigger."""
    p = limit_payload(sig)
    if sig.get("trigger"):
        p = {**p, "type": "sl_order", "trigger_price": _num(round(float(sig["trigger"]), 2), 0)}
        p = {k: p[k] for k in ("type", "script_type", "script", "product", "quantity", "price", "trigger_price",
                               "transaction_type")}
    return p


def send_limit(sig: dict) -> dict:
    """Sends a limit_order (or sl_order, with a trigger) entry. Same gates as an entry order: Pause algo, dry run, webhook / confirmation and
    the daily order limit."""
    cfg = config.AUTO_ORDER[sig["index"]]
    payload = entry_payload(sig)
    text = json.dumps(payload)
    what = f"STOP-LIMIT trigger {payload['trigger_price']} limit {payload['price']}" if sig.get("trigger") \
        else f"LIMIT {payload['price']}"
    head = f"{sig['index']} {sig['opt']} {sig['strike']} [manual entry, {what}] (LTP {sig['ltp']:.2f})"
    if config.AUTO_ORDER_PAUSED:
        return _res("blocked", f"Algo trading is PAUSED; NOT sent: {head}", payload, "Algo trading paused")
    today = datetime.now().strftime("%Y-%m-%d")
    if _today["date"] != today:
        _today.update(date=today, count=0)
    why = None
    if config.AUTO_ORDER_DRY_RUN:
        why = "dry run"
    elif not cfg["confirmed"]:
        why = "JSON template not confirmed"
    elif not cfg["webhook_url"]:
        why = "no webhook URL set"
    if why:
        return _res("dry_run", f"DRY RUN ({why}) {head}: {text}", payload, why)
    if _today["count"] >= config.AUTO_ORDER_DAILY_LIMIT:
        return _res("blocked", f"Daily order limit ({config.AUTO_ORDER_DAILY_LIMIT}) reached; NOT sent: {head}", payload,
                    f"Daily limit ({config.AUTO_ORDER_DAILY_LIMIT}) reached")
    _today["count"] += 1
    try:
        resp = requests.post(cfg["webhook_url"], json=payload, timeout=5)
        body = (resp.text or "")[:200]
        if 200 <= resp.status_code < 300:
            return _res("sent", f"{'SL' if sig.get('trigger') else 'LIMIT'} ORDER SENT {head} -> HTTP {resp.status_code} {body}", payload,
                        f"HTTP {resp.status_code} {body}")
        return _res("failed", f"{'SL' if sig.get('trigger') else 'LIMIT'} ORDER FAILED {head} -> HTTP {resp.status_code} {body} (not retried)", payload,
                    f"HTTP {resp.status_code} {body} (not retried)")
    except Exception as ex:
        return _res("failed", f"{'SL' if sig.get('trigger') else 'LIMIT'} ORDER FAILED {head} -> {ex} (not retried)", payload, f"{ex} (not retried)")
