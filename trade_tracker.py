"""
trade_tracker.py - Entry / Exit / LTP / Captured Points / P&L for the orders table.

Broker rows (BEP Upstox PR 3, a "bk" position, see positions.py): Entry / Exit / quantity come from the position
              (the broker's fills, or the paper broker in Dry run); only their CANDLE SL / Target rules are
              checked here (custom_hit -> the engine asks the position to exit). The text below is about the
              rows of the old AlgoDelta path (kept for old orders files).

SENT rows:    entry and exit are the real fills from today's Upstox trade book
              (GET /v2/order/trades/get-trades-for-day), matched by the option's instrument key. When several
              orders are on the same option, fills are shared out first-in-first-out by time: the earliest BUY
              fills (lots x lot size units) belong to the earliest order, and SELL fills close them in the same
              order (see _allocate). The last order on an option takes whatever is left.
              BUY fills = entry (quantity-weighted average), SELL fills = exit. Once the sold
              quantity covers the bought quantity the trade is closed and Captured Points = Exit - Entry,
              P&L = points x filled quantity (units). While it is open, Points and P&L show "--".
DRY RUN rows: nothing was placed, so entry = the option LTP when the signal fired and the exit is SIMULATED
              from Upstox 1-minute candles after the entry minute, using the Target / SL / trailing SL that were
              in the order's JSON (see simulate_exit). P&L = points x lots x lot size. Once found, the exit
              is saved in today's orders file. LTP (live) is shown for every tracked row.
FAILED / BLOCKED rows: never entered a trade; all these columns stay "--".
Manual Entry LIMIT rows ("mg" column, see limit_orders.py): entry counts from the limit fill (fill time, filled
              quantity), not from the click. Unfilled = no entry. Their LTP / POINT SL, Target and trailing SL are
              watched by the engine on the live LTP; only CANDLE rules (c_sl / c_tgt) are checked here, and the
              dry-run 15:15 exit is the engine's too.
"""
import json
from datetime import datetime, time as dtime, timedelta

import config
import exit_rules
import limit_orders
import positions
import market_utils


TRACKED = ("SENT", "DRY RUN")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _avg(fills):
    qty = sum(q for q, _p in fills)
    return (sum(q * p for q, p in fills) / qty, qty) if qty else (None, 0)


def _instrument_key(client, row, expiry_by_index):
    """Instrument key of the row's option. Stored on the row once found."""
    if row.get("ik"):
        return row["ik"]
    idx = row.get("index")
    expiry = row.get("expiry") or expiry_by_index.get(idx)
    if not idx or idx not in config.INDEXES or not expiry:
        return None
    mapping, _ = market_utils.option_instrument_keys(client, expiry, config.INDEXES[idx]["underlying_key"])
    try:
        ik = (mapping or {}).get(int(float(row["strike"])), {}).get(row["opt"])
    except (KeyError, TypeError, ValueError):
        ik = None
    if ik:
        row["ik"] = ik
    return ik


def entry_time(r) -> str:
    """Time the position was entered: a limit order's first fill, else the order time."""
    m = limit_orders.parse(r.get("mg"))
    return (m.get("fill_time") if m else None) or r.get("time", "")


def _bk(r):
    """The row's broker position state (BEP Upstox PR 3), or None."""
    v = r.get("_bk")
    if v:
        return v
    try:
        v = json.loads(r.get("bk") or "null")
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def _opt_key(r):
    try:
        return r.get("index"), int(float(r.get("strike"))), r.get("opt")
    except (TypeError, ValueError):
        return None


def compute(client, rows, expiry_by_index, trades=None, now=None, known=None):
    """
    Works out the live columns for every tracked row. Returns ({row_id: fields}, error_or_None).
    `trades` is today's Upstox trade list (None = not fetched this time; SENT rows then keep their fills).
    `known` = {(index, strike, opt): LTP} the engine already fetched (2 s batch); only the rest is fetched here.
    Runs on a worker thread: it only reads `rows` (plus caching "ik") and returns the new values.
    """
    tracked = [r for r in rows if r.get("status") in TRACKED]
    if not tracked:
        return {}, None

    iks = {}
    for r in tracked:
        ik = _instrument_key(client, r, expiry_by_index)
        if ik:
            iks[r["id"]] = ik

    known = known or {}
    missing = sorted({ik for rid, ik in iks.items()
                      if known.get(_opt_key(next(r for r in tracked if r["id"] == rid))) is None})
    ltp_map, err = client.fetch_ltp(missing) if missing else ({}, None)
    ltp_map = ltp_map or {}

    fills = {}
    if trades is not None:
        for n, t in sorted(enumerate(trades), key=lambda it: (_trade_time(it[1]), it[0])):
            ik = t.get("instrument_token") or t.get("instrument_key")
            side = (t.get("transaction_type") or "").upper()
            qty, price = _f(t.get("quantity")), _f(t.get("average_price"))
            if ik and side in ("BUY", "SELL") and qty and price is not None:
                fills.setdefault(ik, {"BUY": [], "SELL": []})[side].append((qty, price))
    alloc = {}
    if trades is not None:
        by_ik = {}
        for r in tracked:
            m = limit_orders.parse(r.get("mg"))
            if r["status"] == "SENT" and iks.get(r["id"]) and (m is None or limit_orders.held(m)):
                by_ik.setdefault(iks[r["id"]], []).append(r)
        for ik, orders in by_ik.items():
            alloc.update(_allocate(orders, fills.get(ik, {"BUY": [], "SELL": []})))

    out = {}
    for r in tracked:
        ik = iks.get(r["id"])
        ltp = known.get(_opt_key(r))
        if ltp is None and ik:
            ltp = ltp_map.get(ik)
        if ltp is None and ik:                        # key may come back as NSE_FO:xxx instead of NSE_FO|xxx
            ltp = ltp_map.get(ik.replace("|", ":"))
        fields = {"ltp_live": round(ltp, 2) if ltp is not None else _keep(r, "ltp_live")}

        b = _bk(r)
        if b:                                         # broker position (positions.py): Entry / Exit come from it
            entry, exit_ = _f(b.get("fill")), _f(positions.avg_exit(b))   # avg over partial exits + the final one
            qty_units = _f(b.get("fill_qty")) or _f(b.get("units"))
            rules = [x for x in custom_rules(r) if x.get("type") == "CANDLE"]   # LTP / POINT: the position does it
            if rules and exit_ is None and entry is not None and not r.get("c_done") and ik \
                    and not b.get("exit_req"):
                candles, _cerr = client.fetch_intraday_candles(ik, 1)
                hit = exit_rules.evaluate(entry, (b.get("fill_time") or r.get("time", ""))[:8], candles or [],
                                          rules, now or datetime.now(market_utils.IST), eod=False)
                if hit:
                    fields["custom_hit"] = hit
            fields["filled_qty"] = int(b["fill_qty"]) if b.get("fill_qty") else ""
            fields.update(_points(entry, exit_, ltp, qty_units))
            if b.get("parts") and b.get("exit") is not None and entry is not None:   # exact, not via the avg price
                fields["pnl"] = round(positions.parts_pnl(b) + (float(b["exit"]) - entry) * positions.held(b), 2)
            out[r["id"]] = fields
            continue
        m = limit_orders.parse(r.get("mg"))
        if r["status"] == "DRY RUN":
            entry = _f(r.get("ltp")) if m is None else (_f(m.get("fill")) if limit_orders.held(m) else None)
            lots = _f(r.get("qty")) or _f(config.AUTO_ORDER.get(r.get("index"), {}).get("qty")) or 1
            qty_units = lots * config.INDEXES.get(r.get("index"), {}).get("lot_size", 1)
            exit_ = _f(r.get("exit"))
            rules = custom_rules(r) if m else base_rules(_params(r)) + custom_rules(r)
            if exit_ is None and entry is not None and ik and (rules or m is None):
                candles, _cerr = client.fetch_intraday_candles(ik, 1)
                res = exit_rules.evaluate(entry, entry_time(r), candles or [], rules,
                                          now or datetime.now(market_utils.IST), eod=m is None)
                if res:
                    exit_, exit_time, reason = res
                    fields.update(exit_time=exit_time, exit_reason=reason, exit_new=True)
        else:                                         # SENT: real fills from Upstox
            if trades is None:
                out[r["id"]] = {**fields, **_points(_f(r.get("entry")), _f(r.get("exit")), ltp,
                                                    _f(r.get("filled_qty")))}
                continue
            a = alloc.get(r["id"], {})
            entry, bought, exit_ = a.get("entry"), a.get("bought", 0), a.get("exit")
            qty_units = bought or None
            rules = custom_rules(r)
            if rules and exit_ is None and entry is not None and not r.get("c_done") and ik:
                candles, _cerr = client.fetch_intraday_candles(ik, 1)
                hit = exit_rules.evaluate(entry, entry_time(r), candles or [], rules,
                                          now or datetime.now(market_utils.IST), eod=False)
                if hit:
                    fields["custom_hit"] = hit
            fields["filled_qty"] = int(bought) if bought else ""
        fields.update(_points(entry, exit_, ltp, qty_units))
        out[r["id"]] = fields
    return out, err


def _params(row) -> dict:
    """Target / SL / trail settings that were sent with this order (its saved JSON); current settings as fallback."""
    try:
        p = json.loads(row.get("payload") or "{}")
    except ValueError:
        p = {}
    cfg = config.AUTO_ORDER.get(row.get("index"), {})
    return {
        "tgt_type": p.get("tgt_type", cfg.get("tgt_type", "POINT")), "tgt": _f(p.get("tgt_value", cfg.get("tgt"))),
        "sl_type": p.get("sl_type", cfg.get("sl_type", "POINT")), "sl": _f(p.get("sl_value", cfg.get("sl"))),
        "trail": bool(p.get("is_trail_set", cfg.get("trail", False))),
        "trigger": _f(p.get("trail_trigger", cfg.get("trail_trigger"))),
        "gap": _f(p.get("trail_gap", cfg.get("trail_gap"))), "step": _f(p.get("trail_point", cfg.get("trail_point"))) or 1,
        "is_sl": bool(p.get("is_sl", True)), "is_tgt": bool(p.get("is_tgt", True)),
    }


def base_rules(prm: dict) -> list:
    """The common SL / Target the order was sent with (AlgoDelta holds these for live orders)."""
    rules = []
    if prm.get("is_sl", True) and prm.get("sl") is not None:
        r = exit_rules.make_rule("sl", prm["sl_type"] if prm["sl_type"] in ("LTP", "POINT") else "POINT",
                                 prm["sl"], custom=False)
        if prm.get("trail") and prm.get("trigger") is not None and prm.get("gap") is not None:
            r["trail"] = {"trigger": prm["trigger"], "gap": prm["gap"], "step": prm.get("step") or 1}
        rules.append(r)
    if prm.get("is_tgt", True) and prm.get("tgt") is not None:
        rules.append(exit_rules.make_rule("tgt", prm["tgt_type"] if prm["tgt_type"] in ("LTP", "POINT") else "POINT",
                                          prm["tgt"], custom=False))
    return rules


def custom_rules(row) -> list:
    """Per-order SL / Target set in the app (Order Management before placing, Order Details after)."""
    return [r for r in (exit_rules.parse(row.get("c_sl")), exit_rules.parse(row.get("c_tgt")))
            if r and not exit_rules.is_none(r)]


def simulate_exit(entry: float, entry_time: str, candles: list, prm: dict, now):
    """Dry-run exit with the order's common SL / Target / trailing SL (see exit_rules.evaluate).
    Returns (exit_price, "HH:MM", reason) or None while the trade is still open."""
    return exit_rules.evaluate(entry, entry_time, candles, base_rules(prm), now, eod=True)


def _trade_time(t) -> datetime:
    """Fill time of an Upstox trade (formats vary); unknown -> very late, so it keeps the list order."""
    for key in ("exchange_timestamp", "order_timestamp", "trade_timestamp"):
        v = t.get(key)
        if not v:
            continue
        for fmt in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(str(v)[:19], fmt)
            except ValueError:
                pass
    return datetime.max


def _order_units(r) -> float:
    m = limit_orders.parse(r.get("mg"))
    if m and m.get("filled"):
        return float(m["filled"])
    lots = _f(r.get("qty")) or _f(config.AUTO_ORDER.get(r.get("index"), {}).get("qty")) or 1
    return lots * config.INDEXES.get(r.get("index"), {}).get("lot_size", 1)


def _allocate(orders: list, f: dict) -> dict:
    """FIFO: shares one option's BUY / SELL fills between its SENT orders, earliest order first.
    Returns {row_id: {"entry", "bought", "exit"}}; exit only once that order's bought quantity is fully sold."""
    orders = sorted(orders, key=lambda r: (str(entry_time(r)), r["id"]))
    buys = [list(x) for x in f["BUY"]]
    sells = [list(x) for x in f["SELL"]]

    def take(queue, need):                     # need None = everything left
        got = []
        while queue and (need is None or need > 1e-9):
            q, p = queue[0]
            use = q if need is None else min(q, need)
            got.append((use, p))
            queue[0][0] -= use
            if queue[0][0] <= 1e-9:
                queue.pop(0)
            if need is not None:
                need -= use
        return got

    out = {}
    for i, r in enumerate(orders):
        last = i == len(orders) - 1
        entry, bought = _avg(take(buys, None if last else _order_units(r)))
        out[r["id"]] = {"entry": entry, "bought": bought, "exit": None}
    for i, r in enumerate(orders):
        last = i == len(orders) - 1
        bought = out[r["id"]]["bought"]
        if not bought:
            continue
        sold_avg, sold = _avg(take(sells, None if last else bought))
        if sold >= bought - 1e-9:
            out[r["id"]]["exit"] = sold_avg
    return out


def _keep(row, key):
    return row.get(key, "")


def _points(entry, exit_, ltp, qty_units):
    if entry is None:
        return {"entry": "--", "exit": "--", "points": "--", "pnl": "--"}
    pts = round(exit_ - entry, 2) if exit_ is not None else None      # Captured Points only once exited
    pnl = round(pts * qty_units, 2) if pts is not None and qty_units else None
    return {"entry": round(entry, 2), "exit": round(exit_, 2) if exit_ is not None else "--",
            "points": pts if pts is not None else "--", "pnl": pnl if pnl is not None else "--"}
