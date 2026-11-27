"""
engine.py - the app's single background engine.

Everything that is not drawing the screen lives here: the Upstox connection, the BEP Support scan, the 09:15
opening order, Order Management (common price + ⭐ rows), the broker orders (brokers/, positions.py: entry, exchange SL,
trailing, exits) and their Entry / Exit tracking, the log and the alerts.
It runs ONCE for the whole app (app.timer in app.py), however many browser tabs are open. The pages only read
its state; each change bumps a version number so a page knows when to redraw. Alerts are queued as events that
every open tab shows once.
"""
import csv
import json
import os
from datetime import datetime

from nicegui import background_tasks, run

import bep
import brokers
import config
import exit_rules
import gift_nifty
import limit_orders
import market_utils
import notifier
import positions
import replay
import settings_store
import trade_tracker
import upstox_client
from bep_monitor import BepMonitor
from brokers import base as bk
from common_watch import CommonWatch  # noqa: F401  (also used for pair titles)

HERE = os.path.dirname(os.path.abspath(__file__))
ORDER_FILE_COLUMNS = ["id", "time", "index", "opt", "strike", "kind", "bep", "ltp", "status", "detail", "payload",
                      "expiry", "qty", "exit", "exit_time", "exit_reason", "c_sl", "c_tgt", "c_done", "sq", "mg", "c_trail",
                      "bk", "ik", "filled_qty"]
ORDER_LOCKED = ("SENT", "DRY RUN", "sending")
ORDER_STATUS = {"dry_run": "DRY RUN", "sent": "SENT", "failed": "FAILED", "blocked": "BLOCKED"}
_LOCKING_STATUS = {"SENT": "sent", "DRY RUN": "dry_run", "FAILED": "failed"}


def _now():
    return datetime.now(market_utils.IST)


def _stamp():
    return datetime.now().strftime("%H:%M:%S")


class Engine:
    def __init__(self):
        self.settings_status = settings_store.load()
        config.AUTO_ORDER_DRY_RUN = True                     # always start in Dry run (= broker mode "dry")
        self.client = upstox_client.UpstoxClient(config.UPSTOX_ACCESS_TOKEN)
        self.monitors = {n: BepMonitor(n) for n in config.INDEXES}
        self.replays = {}                                   # index -> replay BepMonitor (BEP Support "Replay")
        self.replay_info = ""
        self.replay_date = ""
        self.replay_busy = False
        self.watches = {n: CommonWatch(n) for n in config.INDEXES}
        for w in self.watches.values():
            w.load()
        self.connected = False
        self.user_name = ""
        self.connection_text = "Not connected"
        self.status = "Waiting for a valid Upstox token (Setup page)"
        self.ltp = {n: None for n in config.INDEXES}
        self.ltp_change = {n: None for n in config.INDEXES}     # index points vs the previous close (header %)
        self.gift = gift_nifty.GiftNifty()                   # GIFT Nifty for the header (every 30 s)
        self._gift_busy = False
        self.option_ltp = {}                                 # (index, strike, opt) -> live LTP for Order Management cards
        self._opt_ltp_busy = False
        self.log_lines = []
        self.events = []                                     # (seq, {"msg", "type", "title"})
        self.event_seq = 0
        self.versions = {k: 0 for k in ("board", "cards", "watch", "orders", "plan", "log", "settings", "bep",
                                                 "cross", "replay", "broker")}
        self.bep_upload_text = {n: "" for n in config.INDEXES}
        self.scan_timer = None
        self._scan_busy = False
        self._open_busy = False
        self._trades_at = 0.0
        self._book_at = 0.0                                  # last Upstox order-book read (limit orders)
        self._limit_busy = False
        config.BROKER_MODE = "dry"                           # broker orders: always start in Dry run (PR 2)
        self.sandbox_token, self.sandbox_saved = settings_store.load_sandbox_token()
        self.broker = self._make_broker()
        self.test_orders = []                                # Setup "Test order" panel (newest first)
        self._bk_busy = False
        self._tag_seq = 0
        self.order_log = self._load_orders_today()
        self._restore_side_locks()
        self.log(f"App started. {self.settings_status} Dry run is ON.")

    # ------------------------------------------------------------------ helpers
    def bump(self, *keys):
        for k in keys:
            self.versions[k] += 1

    def log(self, msg: str):
        self.log_lines.append(f"[{_stamp()}] {msg}")
        del self.log_lines[:-1000]
        self.bump("log")

    def notify(self, msg: str, type_: str = "info", title: str = None):
        """Queues a toast (and a browser notification if `title`) for every open tab."""
        self.event_seq += 1
        self.events.append((self.event_seq, {"msg": msg, "type": type_, "title": title}))
        del self.events[:-50]

    def active(self):
        return [n for n in config.INDEXES if config.INDEXES[n]["enabled"]]

    def fetch_index_ltps(self, names):
        """Index LTPs ({name: ltp}, error). One full-quote call, which also gives the net change for the header
        (LTP vs the previous close); if it fails, the plain LTP call and no change for this scan."""
        keys = {config.INDEXES[n]["index_key"]: n for n in names}
        quotes, q_err = self.client.fetch_quote(list(keys))
        if quotes:
            got = {keys[k]: v for k, v in quotes.items() if k in keys}
            if not got and len(names) == 1 and len(quotes) == 1:
                got[names[0]] = next(iter(quotes.values()))
            if got:
                for n, q in got.items():
                    self.ltp_change[n] = q.get("net_change")
                return {n: q["ltp"] for n, q in got.items()}, None
        for n in names:
            self.ltp_change[n] = None
        if q_err and q_err != getattr(self, "_quote_err", None):
            self._quote_err = q_err
            self.log(f"NIFTY change %: full quote unavailable ({q_err}); showing the LTP only.")
        ltp_map, err = self.client.fetch_ltp(list(keys))
        if not ltp_map:
            return {}, err
        out = {keys[k]: v for k, v in ltp_map.items() if k in keys}
        if not out and len(names) == 1 and len(ltp_map) == 1:
            out[names[0]] = next(iter(ltp_map.values()))
        return out, err

    async def refresh_gift(self):
        """GIFT Nifty for the header (gift_nifty.py): at most one quote call per 30 s; the key is read once a day."""
        logs = []
        try:
            await run.io_bound(self.gift.poll, self.client, _now().strftime("%Y-%m-%d"), logs.append)
        except Exception as e:                                # never let the header value break the scan
            logs.append(f"GIFT NIFTY refresh failed: {e}")
        finally:
            self._gift_busy = False
        for m in logs:
            self.log(m)

    def index_change(self, name):
        """(points, percent) vs the previous close, or None."""
        ltp, chg = self.ltp.get(name), self.ltp_change.get(name)
        try:
            ltp, chg = float(ltp), float(chg)
        except (TypeError, ValueError):
            return None
        prev = ltp - chg
        return (round(chg, 2), round(chg / prev * 100, 2)) if prev else None

    # ------------------------------------------------------------------ orders file
    @staticmethod
    def _orders_file() -> str:
        return os.path.join(HERE, f"orders_{datetime.now().strftime('%Y-%m-%d')}.csv")

    def _load_orders_today(self) -> list:
        try:
            with open(self._orders_file(), newline="", encoding="utf-8") as f:
                rows = [dict(r) for r in csv.DictReader(f)]
        except OSError:
            return []
        for i, r in enumerate(rows):
            r["id"] = i                                       # older files have no id column
            for k in ("entry", "exit", "points", "pnl"):
                r.setdefault(k, "--")
            r.setdefault("ltp_live", "")
            r.setdefault("sq", "")
            r.setdefault("mg", "")
            r.setdefault("c_trail", "")
            r.setdefault("bk", "")
        return rows

    def _restore_side_locks(self):
        today = _now().strftime("%Y-%m-%d")
        for r in self.order_log:
            status = _LOCKING_STATUS.get(r.get("status"))
            if status and r.get("index") in self.monitors and r.get("opt") in ("CE", "PE") \
                    and r.get("kind", "Opening candle") == "Opening candle":
                mon = self.monitors[r["index"]]
                if (today, r["opt"]) not in mon.side_lock:
                    mon.lock_side(today, r["opt"], r.get("strike"), r.get("time", ""), status)

    def _save_order_row(self, row: dict):
        try:
            path = self._orders_file()
            new = not os.path.exists(path)
            fields = ORDER_FILE_COLUMNS
            if not new:
                with open(path, newline="", encoding="utf-8") as f:
                    fields = next(csv.reader(f), None) or ORDER_FILE_COLUMNS
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                if new:
                    w.writeheader()
                w.writerow({k: row.get(k, "") for k in fields})
        except OSError:
            pass

    def _rewrite_orders_file(self):
        """Rewrites today's orders file (used when a dry-run exit is found, so it survives a restart)."""
        try:
            path = self._orders_file()
            tmp = path + ".tmp"
            with open(tmp, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=ORDER_FILE_COLUMNS, extrasaction="ignore")
                w.writeheader()
                for r in self.order_log:
                    row = {k: r.get(k, "") for k in ORDER_FILE_COLUMNS}
                    if r.get("status") != "DRY RUN" and not r.get("bk"):   # (old AlgoDelta rows: from Upstox)
                        row.update(exit="", exit_time="", exit_reason="")
                    if r.get("_bk"):
                        row["bk"] = json.dumps(r["_bk"])
                    w.writerow(row)
            os.replace(tmp, path)
        except OSError as ex:
            self.log(f"Could not update the orders file ({ex}).")

    def add_order_row(self, sig, res):
        row = {
            "id": len(self.order_log), "time": _stamp(), "index": sig["index"], "opt": sig["opt"],
            "strike": sig["strike"], "kind": sig.get("kind_label", "Opening candle"), "bep": round(float(sig["bep"]), 2),
            "ltp": round(float(sig["ltp"]), 2), "status": ORDER_STATUS.get(res["status"], res["status"]),
            "detail": res["detail"] + (f" | {sig['entry_note']}" if sig.get("entry_note") else ""),
            "payload": json.dumps(res["payload"]),
            "expiry": sig.get("expiry") or "",
            "qty": (sig.get("params") or {}).get("qty") or config.AUTO_ORDER[sig["index"]]["qty"],
            "entry": "--", "exit": "--", "ltp_live": "", "points": "--", "pnl": "--",
            "c_sl": (sig.get("app_rules") or {}).get("c_sl", ""), "c_tgt": (sig.get("app_rules") or {}).get("c_tgt", ""),
            "c_done": "", "sq": "", "mg": sig.get("mg", ""),
            "c_trail": (sig.get("app_rules") or {}).get("c_trail", ""),
            "bk": json.dumps(res["bk"]) if res.get("bk") else "", "ik": (res.get("bk") or {}).get("ik", ""),
            "filled_qty": "",
        }
        if res.get("bk"):
            row["_bk"] = res["bk"]
            if res["bk"].get("fill") is not None:          # sandbox: assumed filled when sent
                row["entry"] = res["bk"]["fill"]
        self.order_log.append(row)
        self._save_order_row(row)
        self.bump("orders")

    def open_pnl(self, o):
        """(points, P&L) of a still-open position, worked out locally: (live LTP - Entry) x quantity.
        LTP = the 2 s batch (no extra API call); quantity = the filled units (live) or lots x lot size.
        None when closed or Entry / LTP unknown."""
        if not o or not self.is_open(o):
            return None
        entry = trade_tracker._f(o.get("entry"))
        ltp = self.option_ltp.get(self._opt_key(o))
        if ltp is None:
            ltp = trade_tracker._f(o.get("ltp_live"))
        if entry is None or ltp is None:
            return None
        units = trade_tracker._f(o.get("filled_qty")) or \
            (trade_tracker._f(o.get("qty")) or 1) * config.INDEXES.get(o.get("index"), {}).get("lot_size", 1)
        pts = round(ltp - entry, 2)
        st = self._bk(o)
        if st and st.get("out_qty"):                  # partial exits: open part on what is held + the realized part
            return pts, round(pts * positions.held(st) + positions.parts_pnl(st), 2)
        return pts, round(pts * units, 2)

    def pnl_summary(self) -> dict:
        pts = [r["points"] for r in self.order_log if isinstance(r.get("points"), (int, float))]
        pnl = [r["pnl"] for r in self.order_log if isinstance(r.get("pnl"), (int, float))]
        opens = [x for x in (self.open_pnl(r) for r in self.order_log) if x]
        return {"orders": len(self.order_log), "closed": len(pts), "points": round(sum(pts), 2),
                "open": len(opens), "open_pnl": round(sum(x[1] for x in opens), 2),
                "pnl": round(sum(pnl), 2), "wins": sum(1 for p in pts if p > 0)}

    def max_loss_state(self, name=None) -> dict:
        """Max loss per day (Setup, default ₹2,000; 0 = off). For now only in Dry run and only DRY RUN orders count:
        today's closed P&L + open P&L. `hit` = the loss reached the limit (new orders ask first / are not placed;
        the 09:15 order is exempt)."""
        name = name or next(iter(config.AUTO_ORDER))
        limit = float(config.AUTO_ORDER.get(name, {}).get("max_loss_day", 2000) or 0)
        rows = [r for r in self.order_log if r.get("status") == "DRY RUN"]
        closed = sum(r["pnl"] for r in rows if isinstance(r.get("pnl"), (int, float)))
        opn = sum(x[1] for x in (self.open_pnl(r) for r in rows) if x)
        pnl = round(closed + opn, 2)
        active = bool(config.AUTO_ORDER_DRY_RUN) and limit > 0
        loss = round(max(0.0, -pnl), 2)
        return {"limit": limit, "pnl": pnl, "loss": loss, "active": active, "hit": active and loss >= limit}

    def max_loss_text(self, name=None) -> str:
        m = self.max_loss_state(name)
        return f"Day loss ₹{m['loss']:,.0f} is past your max loss ₹{m['limit']:,.0f} (Dry run orders, closed + open)."

    def ack_max_loss(self, name, row_id):
        """⚡ -> 'Place anyway' in the max-loss popup: this card may place (also a PENDING entry armed now)."""
        r = self.watches[name].get(row_id)
        if r is not None:
            r["loss_ok"] = _stamp()
            self.log(f"{name} {r.get('strike')}{r.get('opt')}: 'Place anyway' past the max loss per day "
                     f"({self.max_loss_text(name)})")

    # ------------------------------------------------------------------ actions from the pages
    async def connect(self, token: str):
        token = (token or "").strip()
        if not token:
            self.connected, self.connection_text = False, "Token is empty"
            return False, self.connection_text
        self.client.access_token = token
        self.client.headers["Authorization"] = f"Bearer {token}"
        self.connection_text = "Testing..."
        ok, info = await run.io_bound(upstox_client.test_connection, token)
        self.connected = ok
        if ok:
            self.user_name, self.connection_text = info, f"Connected ({info})"
            settings_store.save_token(token, _now().strftime("%Y-%m-%d"))
            self.log("Upstox token verified and saved for today.")
        else:
            self.connection_text = f"Connection failed - {info}"
            self.log(f"Token test failed: {info}")
        self.bump("settings")
        return ok, self.connection_text

    async def auto_connect(self):
        """At start: reconnect with today's saved token, if there is one."""
        token = settings_store.load_token(_now().strftime("%Y-%m-%d"))
        if not token:
            self.log("No saved Upstox token for today - enter it on the Setup page.")
            return
        self.log("Reconnecting with today's saved Upstox token...")
        ok, text = await self.connect(token)
        if not ok:
            self.connection_text += " (saved token rejected - enter a new one)"

    def forget_token(self):
        settings_store.forget_token()
        self.log("Saved Upstox token deleted from this computer.")

    def upload_bep(self, name, data: bytes, fname: str):
        ok, msg, _warns = bep.load_csv(data, fname, name)
        self.bep_upload_text[name] = ("✅ " if ok else "❌ ") + msg
        self.log(f"{name} BEP upload ({fname}): {msg}")
        if ok:
            self.monitors[name].reset_backfill()
            self.log(f"{name} board reset; re-checking all candles since 09:15.")
            self.bump("board", "cards")
        self.bump("bep")
        return ok, msg

    def rescan(self):
        for mon in self.monitors.values():
            mon.reset_backfill()
        self.log("Manual rescan: re-scanning from 09:15 with the current window.")
        self.bump("board", "cards")

    def set_strike_count(self, n: int):
        config.STRIKES_EACH_SIDE = (int(n) - 1) // 2
        for mon in self.monitors.values():
            mon.force_recalc()
        self.log(f"Strike count set to {n}. {settings_store.save()}")
        self.bump("settings")

    def set_scan_interval(self, n: int):
        config.SCAN_INTERVAL_SECONDS = int(n)
        if self.scan_timer is not None:
            self.scan_timer.interval = int(n)
        self.log(f"Scan interval set to {n}s. {settings_store.save()}")
        self.bump("settings")

    def set_daily_limit(self, n: int):
        config.AUTO_ORDER_DAILY_LIMIT = max(1, int(n))
        settings_store.save()
        self.bump("settings")

    def toggle_alerts(self):
        config.ALERTS_PAUSED = not config.ALERTS_PAUSED
        self.log("Alerts PAUSED" if config.ALERTS_PAUSED else "Alerts RESUMED")
        self.bump("settings")

    def toggle_algo_pause(self):
        config.AUTO_ORDER_PAUSED = not config.AUTO_ORDER_PAUSED
        self.log("Algo trade PAUSED: no new entry orders." if config.AUTO_ORDER_PAUSED
                 else "Algo trade RESUMED: entry orders allowed again.")
        self.bump("settings")

    def set_opening_order(self, name, on: bool):
        """Setup > 09:15 opening order switch (saved; default OFF): the automatic 09:15 BEP order is placed or not."""
        config.AUTO_ORDER[name]["open_order_on"] = bool(on)
        settings_store.save()
        self.log(f"{name} 09:15 BEP order switched {'ON' if on else 'OFF'} (Setup).")
        self.bump("settings")

    def set_skip_today(self, name, skip: bool):
        """"No trade today": skip only today's 09:15 BEP order; it resets by itself the next day (saved date)."""
        today = _now().strftime("%Y-%m-%d")
        config.AUTO_ORDER[name]["open_skip_date"] = today if skip else ""
        settings_store.save()
        self.log(f"{name} 09:15 BEP order: " + ("No trade today - today's 09:15 order is skipped." if skip
                                                 else "No trade today removed - today's 09:15 order is allowed."))
        self.bump("settings")

    def save_order_defaults(self, name, nums: dict) -> str:
        """Setup > Order defaults (quantity, Target / SL / trail, per-type targets, 09:15 values, max SL)."""
        config.AUTO_ORDER[name].update(**nums)
        saved = settings_store.save()
        self.log(f"{name} order defaults saved. {saved}")
        self.bump("settings")
        return saved

    # ------------------------------------------------------------------ broker orders (PR 2)
    def _make_broker(self):
        """The broker for the current Setup mode (brokers.create is the one place that picks the adapter)."""
        return brokers.create(config.BROKER_MODE, broker=config.BROKER["broker"],
                              live_token_fn=lambda: self.client.access_token,
                              sandbox_token_fn=lambda: self.sandbox_token,
                              algo_name=config.BROKER["algo_name"],
                              market_protection=int(config.BROKER["market_protection"]))

    def set_broker_mode(self, mode: str):
        if mode not in brokers.MODES:
            return
        config.BROKER_MODE = mode
        config.AUTO_ORDER_DRY_RUN = mode == "dry"
        self.broker = self._make_broker()
        self.log(f"Order mode: {brokers.MODE_LABELS[mode]} ({self.broker.name}"
                 + (", sandbox" if mode == "sandbox" else "") + ")."
                 + (" Orders placed in another mode keep their state but are not managed now." if any(
                     self._bk(o) and not self._bk(o).get("closed") for o in self.order_log) else ""))
        self.bump("broker", "settings")

    def save_broker_settings(self, market_protection: int, algo_name: str) -> str:
        config.BROKER.update(market_protection=int(market_protection), algo_name=(algo_name or "").strip())
        self.broker = self._make_broker()
        saved = settings_store.save()
        self.log(f"Broker settings: market protection {config.BROKER['market_protection']}, algo name "
                 f"{config.BROKER['algo_name'] or '(none)'}. {saved}")
        self.bump("broker")
        return saved

    def save_sandbox_token(self, token: str) -> str:
        token = (token or "").strip()
        if not token:
            return "Sandbox token is empty."
        self.sandbox_token, self.sandbox_saved = token, _now().strftime("%Y-%m-%d")
        ok = settings_store.save_sandbox_token(token, self.sandbox_saved)
        self.log("Upstox sandbox token saved." if ok else "Could not save the sandbox token file (kept in memory).")
        self.bump("broker")
        return "Sandbox token saved (valid 30 days from when Upstox generated it)."

    def forget_sandbox_token(self):
        settings_store.forget_sandbox_token()
        self.sandbox_token = self.sandbox_saved = None
        self.log("Upstox sandbox token deleted from this computer.")
        self.bump("broker")

    def sandbox_token_text(self) -> str:
        if not self.sandbox_token:
            return "No sandbox token saved."
        try:
            age = (_now().date() - datetime.strptime(self.sandbox_saved, "%Y-%m-%d").date()).days
            left = 30 - age
            return f"Sandbox token saved on {self.sandbox_saved}" + (
                f" (about {left} day(s) left)" if left > 0 else " - probably EXPIRED: generate a new one")
        except (TypeError, ValueError):
            return "Sandbox token saved."

    async def option_key(self, name, strike, opt):
        """(instrument_key, error) of a current-expiry option."""
        mon = self.monitors[name]
        if not self.connected:
            return None, "connect Upstox first (Setup > Upstox): the option key and LTP come from it"
        if not mon.expiry:
            return None, "no expiry known yet - wait for the first scan after connecting"
        mapping, err = await run.io_bound(market_utils.option_instrument_keys, self.client, mon.expiry,
                                          config.INDEXES[name]["underlying_key"])
        ik = (mapping or {}).get(int(strike), {}).get(opt)
        return (ik, None) if ik else (None, err or f"{strike}{opt} not found for expiry {mon.expiry}")

    def _test_row(self, order_id):
        return next((t for t in self.test_orders if t["id"] == order_id), None)

    def _test_log(self, text, ok=True):
        self.log(f"[Broker test · {brokers.MODE_LABELS[config.BROKER_MODE]}] {text}")
        self.bump("broker")
        return ok, text

    async def test_place(self, name, strike, opt, lots, side, order_type, price=None, trigger=None):
        """Setup "Test order": places one order through the current broker. Returns (ok, text)."""
        ik, err = await self.option_key(name, strike, opt)
        if not ik:
            return self._test_log(f"not sent: {err}", False)
        ltp = await self.quote_option(name, strike, opt)
        qty = int(lots) * int(config.INDEXES[name]["lot_size"])
        req = bk.OrderRequest(ik, side, qty, order_type, price=float(price or 0), trigger_price=float(trigger or 0),
                              tag=f"BEP-TEST-{_now().strftime('%H%M%S')}", symbol=f"{name} {int(strike)} {opt}",
                              ref_price=ltp)
        res = await run.io_bound(self.broker.place, req)
        mode = config.BROKER_MODE
        if not res.ok:
            return self._test_log(f"{bk.describe(req)}: FAILED - {res.message}"
                                  + (f" ({res.code})" if res.code else ""), False)
        for oid in res.order_ids:
            self.test_orders.insert(0, {"id": oid, "time": _stamp(), "mode": mode, "name": name,
                                        "strike": int(strike), "opt": opt, "key": ik, "side": side, "qty": qty,
                                        "type": order_type, "price": req.price, "trigger": req.trigger_price,
                                        "tag": req.tag, "status": bk.SENT, "filled": 0, "avg": 0.0,
                                        "msg": res.message})
        del self.test_orders[50:]
        for oid in res.order_ids:
            await self.test_status(oid, quiet=True)
        return self._test_log(f"{bk.describe(req)}: placed, order id {', '.join(res.order_ids)} (LTP {ltp})")

    async def test_status(self, order_id, quiet=False):
        t = self._test_row(order_id)
        if not t:
            return False, "unknown test order"
        if t["mode"] != config.BROKER_MODE:
            return False, f"this order was placed in {brokers.MODE_LABELS[t['mode']]} mode"
        if config.BROKER_MODE == "dry":                 # move the paper order on with a fresh LTP
            ltp = await self.quote_option(t["name"], t["strike"], t["opt"])
            self.broker.on_ltp(t["key"], ltp)
        st = await run.io_bound(self.broker.status, order_id)
        if st.status != bk.UNKNOWN:
            t.update(status=st.status, filled=st.filled_qty, avg=st.avg_price, msg=st.message or t["msg"],
                     type=st.order_type or t["type"], price=st.price or t["price"],
                     trigger=st.trigger_price or t["trigger"])
        else:
            t["msg"] = st.message
        self.bump("broker")
        text = f"{order_id}: {st.status}" + (f" {st.filled_qty} @ {st.avg_price:.2f}" if st.filled_qty else "") + (
            f" - {st.message}" if st.message else "")
        return (True, text) if quiet else self._test_log(text, st.status != bk.UNKNOWN)

    async def test_modify(self, order_id, order_type, price=None, trigger=None):
        t = self._test_row(order_id)
        if not t or t["mode"] != config.BROKER_MODE:
            return False, "this order belongs to another mode"
        res = await run.io_bound(lambda: self.broker.modify(
            order_id, order_type=order_type, price=None if price in (None, "") else float(price),
            trigger_price=None if trigger in (None, "") else float(trigger)))
        if res.ok:
            t.update(type=order_type, msg=res.message)
            if price not in (None, ""):
                t["price"] = float(price)
            if trigger not in (None, ""):
                t["trigger"] = float(trigger)
            await self.test_status(order_id, quiet=True)
        return self._test_log(f"modify {order_id} -> {order_type}"
                              + (f" price {float(price):.2f}" if price not in (None, "") else "")
                              + (f" trigger {float(trigger):.2f}" if trigger not in (None, "") else "")
                              + (": done" if res.ok else f": FAILED - {res.message}"), res.ok)

    async def test_cancel(self, order_id):
        t = self._test_row(order_id)
        if not t or t["mode"] != config.BROKER_MODE:
            return False, "this order belongs to another mode"
        res = await run.io_bound(self.broker.cancel, order_id)
        if res.ok:
            t["msg"] = res.message
            if config.BROKER_MODE == "sandbox":         # no status call in the sandbox
                t["status"] = bk.CANCELLED
            await self.test_status(order_id, quiet=True)
        return self._test_log(f"cancel {order_id}: " + ("done" if res.ok else f"FAILED - {res.message}"), res.ok)

    async def test_exit(self, order_id):
        t = self._test_row(order_id)
        if not t or t["mode"] != config.BROKER_MODE:
            return False, "this order belongs to another mode"
        if config.BROKER_MODE == "dry":
            ltp = await self.quote_option(t["name"], t["strike"], t["opt"])
            self.broker.on_ltp(t["key"], ltp)
        res = await run.io_bound(self.broker.exit_by_tag, t["tag"])
        return self._test_log(f"exit tag {t['tag']}: " + (f"done, order id {', '.join(res.order_ids)}" if res.ok
                                                          else f"FAILED - {res.message}"), res.ok)

    # ------------------------------------------------------------------ common price watch
    @staticmethod
    def default_target(name, kind: str):
        """(type, value) a new Order Management row starts with: kind 'same', 'pair' or 'star'."""
        cfg = config.AUTO_ORDER[name]
        return cfg.get("tgt_type", "POINT"), float(cfg.get(f"tgt_{kind}", cfg.get("tgt", 50)))

    def row_target(self, name, r):
        """Default Target of a row = the CURRENT Setup value for its type (read when the order is placed, so a Setup
        change also applies to rows added earlier). A row's own Target is its c_tgt rule (popup on the card)."""
        kind = {"star": "star", "pair": "pair", "manual": "manual"}.get(r.get("source"), "same")
        return self.default_target(name, kind)

    @staticmethod
    def setup_trail(name) -> dict:
        cfg = config.AUTO_ORDER[name]
        return {"on": bool(cfg.get("trail")), "type": cfg.get("trail_type", "LTP"),
                "trigger": float(cfg.get("trail_trigger", 20)), "point": float(cfg.get("trail_point", 1)),
                "gap": float(cfg.get("trail_gap", 20))}

    def row_trail(self, name, r) -> dict:
        """Trailing SL of a card: its own (c_trail, set on the card) or the current Setup values."""
        return r.get("c_trail") or self.setup_trail(name)

    def set_watch_trail(self, name, row_id, trail) -> bool:
        ok = self.watches[name].set_trail(row_id, trail)
        if ok:
            r = self.watches[name].get(row_id)
            what = f"{r.get('strike') or ''}{r.get('opt') or ''}".strip() or f"row {row_id}"
            if trail is None:
                self.log(f"{name} {what}: trailing SL back to Setup.")
            else:
                self.log(f"{name} {what}: trailing SL " + (
                    f"ON ({trail['type']}, trigger {trail['trigger']:g}, gap {trail['gap']:g}, point {trail['point']:g})"
                    if trail["on"] else "OFF") + " for this card.")
            self.bump("watch")
        return ok

    def add_pair(self, name, ce_strike, pe_strike, ce_price, pe_price, buffer, tf, mode=None, trig_on="close",
                 auto_open=False, tf_sec=None):
        r = self.watches[name].add_pair(ce_strike, pe_strike, ce_price, pe_price, buffer, tf, _now(), mode, trig_on,
                                        auto_open, tf_sec)
        self.log(f"{name} Order Management: pair added {CommonWatch.pair_title(r)}, buffer {buffer:g}, "
                 f"{CommonWatch.tf_label(r)}, "
                 f"trigger on {'live LTP' if trig_on == 'ltp' else 'candle close'}"
                 + (f", AUTO ORDER (window {config.PAIR_AUTO_FROM[:5]}-{config.PAIR_AUTO_TO[:5]}, then the Pair rule "
                    "until 15:15)" if auto_open else "")
                 + (", STOP-LIMIT order." if mode == "stoplimit" else ", LIMIT order." if mode == "limit" else ", strategy order."))
        self.bump("watch")

    def add_watch(self, name, strike, leg, price, tf, mode=None, trig_on="close"):
        rows = self.watches[name].add(strike, leg, price, tf, _now(), mode, trig_on)
        self.log(f"{name} common price watch added: "
                 + ", ".join(f"{r['strike']}{r['opt']} @ {r['price']:g} ({r['tf']} min)" for r in rows)
                 + f", trigger on {'live LTP' if trig_on == 'ltp' else 'candle close'}"
                 + (", STOP-LIMIT order." if mode == "stoplimit" else ", LIMIT order." if mode == "limit" else ", strategy order."))
        self.bump("watch")

    def set_watch_auto(self, name, row_id, on) -> bool:
        ok = self.watches[name].set_auto(row_id, on)
        if ok:
            r = self.watches[name].get(row_id)
            self.log(f"{name} Pair {CommonWatch.pair_title(r)}: Auto order " + ("ON" if on else "OFF") + ".")
            self.bump("watch")
        return ok

    async def auto_place(self, name, row_id):
        """Pair "Auto at 09:15": the leg that crossed above its price is ordered at once, with the row's own order
        type and Target / SL / Trail (else the Setup defaults). Nobody answers popups, so: the same option already
        open -> skipped (alert); Stop-limit with the LTP at / above the trigger -> skipped; the ⚡ warnings are only
        logged. Pause algo / the daily limit / max loss per day block it as usual (BLOCKED, ⚡ can be pressed)."""
        cw = self.watches[name]
        r = cw.get(row_id)
        if not r or r["status"] != "triggered" or r.get("order_status") in ORDER_LOCKED + ("PENDING",):
            return None
        head = f"{name} AUTO ORDER Pair {CommonWatch.pair_title(r)}: {r['strike']}{r['opt']}"
        if _now().time() >= exit_rules.EXIT_TIME:         # no new automatic orders from 15:15
            self.log(f"{head} NOT placed - after {exit_rules.EXIT_TIME.strftime('%H:%M')}; the card stays TRIGGERED.")
            return None
        ltp = self.row_live_ltp(name, r)

        def skip(why):
            self.log(f"{head} NOT placed - {why}. The card stays TRIGGERED (⚡ to place it yourself).")
            self.notify(f"{head} NOT placed: {why}", "negative", f"{name} auto order")
            return None
        others = self.open_on_option(name, r["strike"], r["opt"])
        key = (name, int(r["strike"]), r["opt"])
        others += [o for o in self.order_log if o not in others and self._opt_key(o) == key
                   and o.get("status") in ("SENT", "DRY RUN") and (positions.entry_open(self._bk(o)) or limit_orders.pending(limit_orders.parse(o.get("mg"))))]   # entry sent, not filled yet
        if others:
            return skip(f"{r['strike']}{r['opt']} already has an open order ({', '.join(self.order_label(o) for o in others)})")
        cfg = config.AUTO_ORDER[name]
        warns = []
        sl = exit_rules.parse(r.get("c_sl")) or {"type": cfg.get("sl_type"), "value": cfg.get("sl")}
        if sl.get("type") in ("LTP", "CANDLE") and ltp is not None and sl.get("value") is not None \
                and float(sl["value"]) >= ltp:
            warns.append(f"SL {float(sl['value']):g} is at or above the LTP {ltp:.2f}")
        if exit_rules.is_none(exit_rules.parse(r.get("c_tgt"))) and not self.row_trail(name, r)["on"]:
            warns.append("Target None and trailing SL OFF")
        if warns:
            self.log(f"{head}: placed without asking (auto); warnings: " + "; ".join(warns) + ".")
        if r.get("entry_mode") == "limit":
            trigger = self.stop_prices(r)[0]
            if trigger and ltp is not None and ltp >= trigger:
                return skip(f"Stop-limit trigger {trigger:.2f} is already at or below the LTP {ltp:.2f}")
            self.log(f"{head}: {CommonWatch.auto_why(r)} (LTP {ltp}) - sending the "
                     + ("stop-limit" if trigger else "limit") + " order.")
            res = await self.place_limit_order(name, row_id)
        elif r.get("entry_price"):
            ok, msg = self.arm_watch_entry(name, row_id)
            self.log(f"{head}: {CommonWatch.auto_why(r)} - entry price armed: {msg}")
            self.notify(f"{head}: entry {'armed' if ok else 'NOT armed'} - {msg}", "positive" if ok else "negative",
                        f"{name} auto order")
            return None
        else:
            res = await self.place_watch_order(name, row_id, fill_ltp=ltp,
                                               entry_note=f"Auto order: {CommonWatch.auto_why(r)}, LTP {ltp}")
        if res:
            st = ORDER_STATUS.get(res["status"], res["status"].upper())
            self.log(f"{head}: {st}.")
            self.notify(f"{head} {st}" + (f" ({res.get('detail')})" if res["status"] in ("blocked", "failed") else ""),
                        {"sent": "positive", "dry_run": "info"}.get(res["status"], "negative"), f"{name} auto order")
        return res

    def set_watch_mode(self, name, row_id, mode) -> bool:
        """Card order type: "limit" (LIMIT or, with a trigger, SL BUY at the exchange) or None (market entry)."""
        ok = self.watches[name].set_mode(row_id, mode)
        if ok:
            r = self.watches[name].get(row_id)
            what = CommonWatch.pair_title(r) if r.get("kind") == "pair" and not r.get("opt") else f"{r['strike']}{r['opt']}"
            t, p = self.stop_prices(r)
            self.log(f"{name} {what}: order type " + (
                ("STOP-LIMIT " + (f"trigger {t:.2f} limit {p:.2f}" if t else "at the fired leg's price")
                 if mode == "stoplimit" else
                 "LIMIT" + (f" at {r['entry_price']:.2f}" if r.get("entry_price") else " at the fired leg's price"))
                if mode in ("limit", "stoplimit") else "strategy order") + ".")
            self.bump("watch")
        return ok

    async def row_bep(self, name, r):
        """BEP behind a card, for the "far above BEP" warning (PR #43): a ⭐ card's level, a BEP cross Pair's
        fired leg BEP, or a Manual Entry strike's BEP (uploaded CSV, else calculated). None for other cards."""
        if r.get("source") == "star" or (r.get("auto") == "cross" and r.get("opt")):
            return float(r["price"]) if r.get("price") else None
        if r.get("source") == "manual" and self.connected and r.get("strike"):
            mon = self.monitors.get(name)
            try:
                v, _src = await run.io_bound(mon.get_bep, self.client, int(r["strike"]))
            except Exception:
                return None
            return float(v) if v is not None else None
        return None

    @staticmethod
    def is_stop(r) -> bool:
        """Stop-limit card (SL BUY): Manual with a trigger, or a card in Stop-limit mode."""
        return r.get("entry_mode") == "limit" and bool(r.get("entry_trigger") or r.get("entry_stop"))

    @staticmethod
    def stop_prices(r):
        """(trigger, limit) of a stop-limit card. The trigger defaults to the row's level (a Pair: the fired leg's
        price, known once it fired), the limit to trigger + STOP_LIMIT_GAP. (None, None) while unknown."""
        if not Engine.is_stop(r):
            return None, None
        t = r.get("entry_trigger") or (r.get("price") if r.get("source") != "manual" else None)
        if not t:
            return None, None
        p = r.get("entry_price") if r.get("entry_trigger") else None
        return round(float(t), 2), round(float(p) if p else float(t) + config.STOP_LIMIT_GAP, 2)

    @staticmethod
    def limit_price(r):
        """Price a limit card is sent at: its Entry Price, else the row's level (Pair: the fired leg's price).
        A Manual Entry card always has its own price."""
        if Engine.is_stop(r):
            return Engine.stop_prices(r)[1]
        p = r.get("entry_price")
        if not p and r.get("source") != "manual":
            p = r.get("price")
        return float(p) if p else None

    def row_qty(self, name, r) -> int:
        """Lots for an Order Management row: its own value, else the Setup quantity."""
        return int(r.get("qty") or config.AUTO_ORDER[name]["qty"])

    def set_watch_qty(self, name, row_id, lots) -> bool:
        ok = self.watches[name].set_qty(row_id, lots)
        if ok:
            self.log(f"{name} Order Management row {row_id}: quantity set to {int(lots)} lot(s).")
            self.bump("watch")
        return ok

    def edit_watch(self, name, row_id, tf, price, pe_price=None, buffer=None, trig_on=None, tf_sec=None) -> bool:
        ok = self.watches[name].edit(row_id, tf, price, pe_price, buffer, trig_on, tf_sec)
        if ok:
            r = self.watches[name].get(row_id)
            what = CommonWatch.pair_title(r) + f", buffer {r['buffer']:g}" if r.get("kind") == "pair" else \
                f"{r['strike']}{r['opt']} @ {r['price']:g}"
            self.log(f"{name} Order Management row {row_id} edited: {what} ({CommonWatch.tf_label(r)}).")
            self.bump("watch")
        return ok

    def row_order(self, r):
        """The Order Details row placed from this Order Management row, or None."""
        oid = r.get("order_id")
        if oid is None or oid == "":
            return None
        return next((o for o in self.order_log if o["id"] == oid), None)

    async def refresh_option_ltps(self):
        """One batched Upstox LTP call for every option shown on the Order Management cards (both legs of a pair)."""
        if not self.connected or self._opt_ltp_busy or not market_utils.is_market_open():
            return
        wanted = set()
        for name in self.active():
            for r in self.watches[name].rows:
                if r.get("kind") == "pair":
                    wanted.update({(name, r["ce_strike"], "CE"), (name, r["pe_strike"], "PE")})
                elif r.get("strike") is not None:
                    wanted.add((name, int(r["strike"]), r["opt"]))
        for name in self.active():                   # BEP Pair cross rows still being watched
            for c in self.monitors[name].cross.rows:
                if c["status"] == "watching":
                    wanted.update({(name, c["up"]["strike"], c["up"]["opt"]), (name, c["dn"]["strike"], c["dn"]["opt"])})
        for o in self.order_log:                     # limit orders / app trails keep their live LTP without a card
            if o.get("index") in self.monitors and (self.entry_open(o) or self.is_open(o)):
                wanted.add((o["index"], int(float(o["strike"])), o["opt"]))
        if not wanted:
            return
        self._opt_ltp_busy = True
        try:
            keys = {}
            for name in {w[0] for w in wanted}:
                mon = self.monitors[name]
                if not mon.expiry:
                    continue
                mapping, _err = await run.io_bound(market_utils.option_instrument_keys, self.client, mon.expiry,
                                                   config.INDEXES[name]["underlying_key"])
                for w in wanted:
                    ik = (mapping or {}).get(w[1], {}).get(w[2]) if w[0] == name else None
                    if ik:
                        keys[ik] = w
            if not keys:
                return
            ltps, _err = await run.io_bound(self.client.fetch_ltp, list(keys))
            for ik, v in (ltps or {}).items():
                w = keys.get(ik) or keys.get(ik.replace(":", "|"))
                if w:
                    self.option_ltp[w] = round(float(v), 2)
            moved = False                            # Order Details: open orders follow the same 2 s LTP
            for o in self.order_log:
                v = self.option_ltp.get(self._opt_key(o))
                if v is not None and self.is_open(o) and o.get("ltp_live") != v:
                    o["ltp_live"] = v
                    moved = True
            if moved:
                self.bump("orders")
        except Exception as ex:
            self.log(f"Order Management LTP error: {ex}")
        finally:
            self._opt_ltp_busy = False

    def remove_watch(self, name, row_id):
        self.watches[name].remove(row_id)
        self.bump("watch")

    async def cancel_dead_limits(self, name):
        """A card that turned FAILED / EXITED cancels its limit order if it is still open at the exchange
        (a filled part stays, protected by the app SL / Target)."""
        for r in list(self.watches[name].rows):
            if r["status"] not in ("failed", "exited"):
                continue
            o = self.row_order(r)
            if o is not None and self.entry_cancellable(o):
                await self.cancel_limit(o["id"], f"card {r['status'].upper()}")

    async def delete_watch(self, name, row_id):
        """Deletes a card. A limit order still open at the exchange is cancelled first (cancel_order); an app-side
        pending entry simply stops. A placed order stays in Order Details (its app SL / Target keep running)."""
        r = self.watches[name].get(row_id)
        o = self.row_order(r) if r else None
        if o is not None and self.entry_cancellable(o):
            await self.cancel_limit(o["id"], "Card deleted")
        self.remove_watch(name, row_id)

    # ------------------------------------------------------------------ entry at a price (Order Management cards)
    @staticmethod
    def _row_key(name, r):
        return (name, int(r["strike"]), r["opt"]) if r.get("strike") is not None and r.get("opt") else None

    def row_live_ltp(self, name, r):
        k = self._row_key(name, r)
        return self.option_ltp.get(k) if k else None

    async def quote_option(self, name, strike, opt):
        """Fresh LTP for one option (also stored for the cards). None when unavailable."""
        mon = self.monitors[name]
        if not self.connected or not mon.expiry:
            return self.option_ltp.get((name, int(strike), opt))
        try:
            mapping, _err = await run.io_bound(market_utils.option_instrument_keys, self.client, mon.expiry,
                                               config.INDEXES[name]["underlying_key"])
            ik = (mapping or {}).get(int(strike), {}).get(opt)
            if ik:
                ltps, _err = await run.io_bound(self.client.fetch_ltp, [ik])
                v = (ltps or {}).get(ik) or (ltps or {}).get(ik.replace("|", ":"))
                if v is not None:
                    self.option_ltp[(name, int(strike), opt)] = round(float(v), 2)
        except Exception as ex:
            self.log(f"{name} {strike}{opt} quote error: {ex}")
        return self.option_ltp.get((name, int(strike), opt))

    def add_manual(self, name, strike, opt, lots, entry_price=None, mode=None, trigger=None):
        r = self.watches[name].add_manual(strike, opt, lots, entry_price, _now(), mode, trigger)
        self.log(f"{name} Manual Entry added: {strike}{opt}, {lots} lot(s), "
                 + (f"STOP-LIMIT trigger {trigger:.2f} limit {entry_price:.2f}" if mode == "limit" and trigger else
                    f"LIMIT at {entry_price:.2f}" if mode == "limit" else
                    f"entry at {entry_price:.2f}" if entry_price else "market") + ".")
        self.bump("watch")
        return r

    def set_stoplimit(self, name, row_id, trigger, price):
        r = self.watches[name].set_stoplimit(row_id, trigger, price)
        if r:
            self.log(f"{name} Manual {r['strike']}{r['opt']}: " + (
                f"switched to STOP-LIMIT trigger {trigger:.2f} limit {price:.2f}." if trigger else
                f"switched to LIMIT {price:.2f}."))
            self.bump("watch")
        return r

    def set_watch_entry(self, name, row_id, price, direction=None) -> bool:
        """Card's entry price (None = market at the click) and direction ("below" / "above", None = automatic from
        the LTP when armed). Not while pending or once ordered."""
        cw = self.watches[name]
        r = cw.get(row_id)
        if not r or r.get("order_status") in ORDER_LOCKED + ("PENDING",):
            return False
        if r.get("entry_mode") == "limit" and not price and r.get("source") == "manual":
            return False                                     # a Manual limit card always needs its price
        r["entry_price"] = round(float(price), 2) if price else None
        r["entry_dir"] = direction if r["entry_price"] else None
        cw.save()
        self.bump("watch")
        return True

    def arm_watch_entry(self, name, row_id):
        """Arms a TRIGGERED card with an entry price: the order is sent when the live LTP reaches it."""
        cw = self.watches[name]
        r = cw.get(row_id)
        if not r or r["status"] != "triggered" or r.get("order_status") in ORDER_LOCKED + ("PENDING",):
            return False, "Only a TRIGGERED card that is not ordered can be armed."
        price = r.get("entry_price")
        if not price:
            return False, "Set an entry price first."
        ltp = self.row_live_ltp(name, r)
        direction = r.get("entry_dir")
        if not direction:
            if ltp is None:
                return False, "No live LTP yet - wait a few seconds, or pick the direction with the arrow."
            direction = "below" if price <= ltp else "above"
        r.update(pending={"price": price, "dir": direction, "armed_at": _stamp()}, order_status="PENDING")
        cw.save()
        arrow = "↓" if direction == "below" else "↑"
        what = f"{r['strike']}{r['opt']}"
        self.log(f"{name} {what}: entry ARMED at {price:.2f} {arrow} (LTP {ltp if ltp is not None else '--'}); "
                 f"the order is sent when the LTP is at or {direction} the price.")
        self.bump("watch")
        return True, f"{what}: waiting for {price:.2f} {arrow}"

    def cancel_watch_entry(self, name, row_id, reason="cancelled") -> bool:
        cw = self.watches[name]
        r = cw.get(row_id)
        if not r or r.get("order_status") != "PENDING":
            return False
        p = r.get("pending") or {}
        r.update(pending=None, order_status="")
        cw.save()
        msg = f"{name} {r.get('strike')}{r.get('opt')}: pending entry at {p.get('price', 0):.2f} {reason}."
        self.log(msg)
        if reason != "cancelled" and not config.ALERTS_PAUSED:
            self.notify(msg, "warning", f"{name} pending entry {reason}")
        self.bump("watch")
        return True

    async def check_pending_entries(self, now=None):
        """Every OPTION_LTP_SECONDS: sends a pending card's order when its LTP reaches the price; cancels it when
        the card fails / exits, or at 15:15."""
        now = now or _now()
        eod = now.replace(hour=exit_rules.EXIT_TIME.hour, minute=exit_rules.EXIT_TIME.minute, second=0, microsecond=0)
        for name in self.active():
            for r in list(self.watches[name].rows):
                p = r.get("pending")
                if r.get("order_status") != "PENDING" or not p:
                    continue
                if r["status"] != "triggered":
                    self.cancel_watch_entry(name, r["id"], f"cancelled (card {r['status'].upper()})")
                    continue
                if now >= eod:
                    self.cancel_watch_entry(name, r["id"], "cancelled at 15:15")
                    continue
                ltp = self.row_live_ltp(name, r)
                if ltp is None:
                    continue
                if (p["dir"] == "below" and ltp <= p["price"]) or (p["dir"] == "above" and ltp >= p["price"]):
                    arrow = "\u2193" if p["dir"] == "below" else "\u2191"
                    note = f"Entry at price {p['price']:.2f} {arrow} hit, LTP {ltp:.2f} (armed {p['armed_at']})"
                    res = await self.place_watch_order(name, r["id"], fill_ltp=ltp, entry_note=note)
                    if res:
                        self.notify(f"{name} {r['strike']}{r['opt']}: {note} - order {ORDER_STATUS.get(res['status'], res['status'].upper())}"
                                    + (f" ({res.get('detail')})" if res["status"] == "blocked" else ""),
                                    {"sent": "positive", "dry_run": "info"}.get(res["status"], "negative"),
                                    f"{name} entry {r['strike']}{r['opt']}")

    async def check_live_watch(self):
        """Every OPTION_LTP_SECONDS in market hours: arms Same strike / Pair legs on the live LTP and fires rows set
        to Trigger on live LTP (alert + Place order ready, like a candle trigger)."""
        now = _now()
        for name in self.active():
            fired, changed = self.watches[name].live_step(
                lambda strike, opt, name=name: self.option_ltp.get((name, int(strike), opt)), now)
            auto = [r for r in fired if r.get("trig_how") == "auto" or r.get("auto_fire")]
            fired = [r for r in fired if r not in auto]
            for r in fired:
                self.log("COMMON PRICE (live LTP): " + notifier.common_label(r))
            if fired and not config.ALERTS_PAUSED:
                self.notify("\n".join(notifier.common_short(r) for r in fired), "positive", f"{name} Order Management")
            if changed:
                self.bump("watch")
            await self._auto_fired(name, auto)

    async def _auto_fired(self, name, rows):
        """Pair rows with "Auto order" ON that just triggered (window rule or Pair rule): order at once."""
        for r in rows:
            self.log(f"{name} AUTO Pair {CommonWatch.pair_title(r)}: {CommonWatch.auto_why(r)} at {r['trig_time']} "
                     f"(opponent {r.get('trig_opp')}).")
            await self.auto_place(name, r["id"])

    async def option_tick(self):
        await self.refresh_option_ltps()
        if self.connected and market_utils.is_market_open():
            await self.check_live_watch()
            await self.check_pending_entries()
            await self.check_limits()
            await self.check_app_trails()
            await self.broker_tick()

    async def place_watch_order(self, name, row_id, fill_ltp=None, entry_note="", entry=None):
        """Sends the card's order now (market). Signal LTP = the live LTP at the click / at the price hit (dry-run
        Entry uses it), falling back to the last candle close when no live LTP is known yet."""
        cw, mon = self.watches[name], self.monitors[name]
        r = cw.get(row_id)
        if not r or r["status"] != "triggered" or r["order_status"] in ("SENT", "DRY RUN", "sending"):
            return None
        kind = {"star": "star", "pair": "pair", "manual": "manual"}.get(r.get("source"), "common")
        label = {"star": "Manual — ⭐ support", "pair": "Manual — pair",
                 "common": "Manual — common price", "manual": "Manual entry"}[kind]
        if entry:                                     # PR 4: Limit / Stop-limit entry
            label += " — stop-limit" if entry.get("type") == bk.SL else " — limit"
        ltp = next((v for v in (fill_ltp, self.row_live_ltp(name, r), r["last_close"], r["trig_close"])
                    if v is not None), None)
        if ltp is None:                               # e.g. a Manual Entry card placed right after adding
            ltp = await self.quote_option(name, r["strike"], r["opt"])
        if ltp is None:
            self.log(f"{name} {r['strike']}{r['opt']}: no LTP available - order not sent.")
            self.notify(f"{name} {r['strike']}{r['opt']}: no LTP yet - order not sent. Try again in a few seconds.",
                        "warning")
            return None
        tgt_type, tgt = self.row_target(name, r)
        tr = self.row_trail(name, r)
        params, app_rules = {"tgt": tgt, "tgt_type": tgt_type, "qty": self.row_qty(name, r),
                             "trail": tr["on"], "trail_type": tr["type"], "trail_trigger": tr["trigger"],
                             "trail_point": tr["point"], "trail_gap": tr["gap"]}, {}
        for k in ("sl", "tgt"):                       # the row's own SL / Target (popup in Order Management)
            rule = exit_rules.parse(r.get("c_" + k))
            if not rule:
                continue
            if exit_rules.is_none(rule):              # Target None (PR #48): no target
                params["is_" + k] = False
                continue
            if rule["type"] == "CANDLE":              # watched by the app on candle closes (+ max SL at the exchange)
                params["is_" + k] = False
                app_rules["c_" + k] = json.dumps({**rule, "set_at": ""})
            else:                                     # LTP / POINT -> the exchange SL / the app target (positions.py)
                params[k], params[k + "_type"] = rule["value"], rule["type"]
        sl_rule = exit_rules.parse(r.get("c_sl"))
        if sl_rule and sl_rule["type"] == "CANDLE" and tr["on"]:   # trail kept as c_trail; positions.py moves the SL
            app_rules["c_trail"] = self._trail_json(tr)
        sig = {"index": name, "opt": r["opt"], "strike": r["strike"],
               "bep": (r.get("entry_price") or ltp) if kind == "manual" else r["price"],
               "ltp": ltp,
               "entry_note": entry_note,
               "expiry": mon.expiry, "order_type": "strategy_order", "kind": kind,
               "kind_label": label, "time": _stamp(), "params": params, "app_rules": app_rules, "entry": entry,
               "loss_ok": bool(r.pop("loss_ok", None))}
        r["pending"] = None
        cw.set_order_status(row_id, "sending")
        self.bump("watch")
        res = await self.place_entry(sig)
        cw.set_order_status(row_id, ORDER_STATUS.get(res["status"], res["status"]))
        self.log(res["message"])
        self.add_order_row(sig, res)
        r["order_id"] = self.order_log[-1]["id"]          # card shows this order's Entry / LTP / P&L
        cw.save()
        self.bump("watch")
        return res

    async def handle_pair_exits(self, name):
        """Opponent SL of a pair: the order exits through its position (exchange SL -> MARKET, next 2 s tick).
        Skipped if the order already has an exit (target / SL / trail hit first)."""
        cw, mon = self.watches[name], self.monitors[name]
        for ev in cw.pop_exits():
            row = next((o for o in self.order_log if o["id"] == ev.get("order_id")), None)
            opp = "PE" if ev["opt"] == "CE" else "CE"
            ol = CommonWatch.opp_leg(ev)
            why = (f"opponent {ol[1]}{ol[0]} closed {ev['opp_close']:.2f} at / above its line {ol[2]:.2f}, "
                   f"{CommonWatch.candle_span(ev, ev['fail_time'])}" if ol else
                   f"opponent {opp} closed {ev['opp_close']:.2f} at {ev['fail_time']}")
            if row is None:
                continue
            already = isinstance(row.get("exit"), (int, float)) or trade_tracker._f(row.get("exit")) is not None \
                or self.squared_off(row)
            m = limit_orders.parse(row.get("mg"))
            if m is not None and not already:              # limit order: square off a fill, cancel what is open
                if self.is_open(row):
                    res = await self.square_off(row["id"], "Opponent SL")
                    status = ORDER_STATUS.get(res["status"], res["status"].upper()) if res else "not needed"
                elif limit_orders.cancellable(m):
                    res = await self.cancel_limit(row["id"], "Opponent SL")
                    status = "limit cancel " + (ORDER_STATUS.get(res["status"], res["status"].upper()) if res else "-")
                else:
                    status = "not needed"
                self.log(f"Pair {CommonWatch.pair_title(ev)}: {why} - limit order: {status}.")
            elif already:
                self.log(f"Pair {CommonWatch.pair_title(ev)}: {why}, but the order had already exited - nothing to do.")
                status = "not needed"
            elif self._bk(row):
                ok = self._request_exit(row, "Opponent SL")
                status = "EXIT REQUESTED" if ok else "not needed"
                self.log(f"Pair {CommonWatch.pair_title(ev)}: {why} - " + (
                    "exit requested (the exchange SL becomes MARKET)." if ok else "nothing to do."))
                if ok:
                    self.notify(f"{name} {ev['strike']}{ev['opt']}: opponent SL - exiting", "warning",
                                f"{name} exit {ev['strike']}{ev['opt']}")
            else:
                status = "not needed"
            live = cw.get(ev["id"])
            if live:
                live["squareoff"] = status
                cw.save()
            self.bump("watch", "orders")

    # ------------------------------------------------------------------ BEP Support replay (replay.py)
    def view_monitor(self, name):
        """The monitor the BEP Support pages show: the replayed past day, else the live one."""
        return self.replays.get(name) or self.monitors[name]

    async def run_replay(self, date_str: str, bep_source: str) -> str:
        """Rebuilds the Support board + BEP Pair cross of a past day. Returns an error text, or "" when done."""
        if not self.connected:
            return "Connect to Upstox first (Setup)."
        if self.replay_busy:
            return "A replay is already running."
        self.replay_busy = True
        self.bump("replay")
        try:
            out = {}
            for name in self.active():
                mon, info, err = await run.io_bound(replay.build, self.client, name, date_str, bep_source,
                                                    self.monitors[name].expiry)
                if err:
                    self.log(f"{name} replay {date_str}: {err}")
                    return err
                for msg in getattr(mon, "replay_logs", []):
                    self.log(f"{name} replay: {msg}")
                out[name] = (mon, info)
            self.replays = {n: m for n, (m, _i) in out.items()}
            self.replay_date = date_str
            self.replay_info = " | ".join(i for _m, i in out.values())
            self.log(self.replay_info)
            return ""
        except Exception as ex:
            self.log(f"Replay {date_str} error: {ex}")
            return str(ex)
        finally:
            self.replay_busy = False
            self.bump("replay", "board", "cross")

    async def explain_cross(self, name, ce_strike, pe_strike, hhmm) -> list:
        """BEP Pair cross "Why no row?" for the day the BEP Support pages show (live or replay)."""
        try:
            return await run.io_bound(self.view_monitor(name).explain_cross, ce_strike, pe_strike, hhmm)
        except Exception as ex:
            return [(False, f"Check failed: {ex}")]

    def stop_replay(self):
        self.replays, self.replay_info, self.replay_date = {}, "", ""
        self.bump("replay", "board", "cross")

    def _cross_signals(self, name, signals) -> bool:
        """Live BEP Pair cross signals: alert (unless paused) and add a TRIGGERED Pair row to Order Management."""
        added = False
        signals = signals or []
        if signals:
            if config.ALERTS_PAUSED:
                self.log(f"Alerts paused: {len(signals)} BEP Pair cross alert(s) not shown.")
            else:                                          # one short toast per scan; full text in the Log
                self.notify("\n".join(notifier.cross_short(s) for s in signals), "positive", f"{name} BEP Pair cross")
        for sig in signals:
            self.log(notifier.cross_label(sig))
            if not getattr(config, "BEP_CROSS_ADD_PAIR", True):
                continue
            row, why = self.watches[name].add_cross_pair(sig)
            if row:
                added = True
                self.log(f"{name} BEP Pair cross {sig['time']} added to Order Management > Pair: "
                         f"{CommonWatch.pair_title(row)} (buffer 0) - Place order is ready.")
                for s in self.watches[name].drop_star_twins(row):
                    self.log(f"{name} \u2b50 {s['strike']}{s['opt']} at {s['trig_time']} removed from Order Management "
                             f"(BEP cross Pair card on the same option, same candle).")
            else:
                self.log(f"{name} BEP Pair cross {sig['time']} not added to Order Management: {why}.")
        return added

    def _add_star_rows(self, name, events) -> bool:
        mon, cw, added = self.monitors[name], self.watches[name], False
        for e in events or []:
            if not e.get("in_window"):
                self.log(f"{name} ⭐ {e['strike']}{e['opt']} at {e['time']} not added to Order Management (outside ATM window).")
                continue
            if e["time"] == "09:15" and (e.get("day"), e["opt"]) in mon.side_lock:
                self.log(f"{name} ⭐ {e['strike']}{e['opt']} at 09:15 not added to Order Management "
                         f"({e['opt']} already has its 09:15 opening order).")
                continue
            if cw.cross_twin(e["strike"], e["opt"], e["time"]):
                self.log(f"{name} \u2b50 {e['strike']}{e['opt']} at {e['time']} not added to Order Management "
                         f"(BEP cross Pair card on the same option, same candle).")
                continue
            row = cw.add_star(e)
            if row:
                added = True
                self.log(f"{name} ⭐ {e['strike']}{e['opt']} at {e['time']} added to Order Management "
                         f"(BEP {e['bep']:.2f}, close {e['close']:.2f}) - Place order is ready.")
        return added

    # ------------------------------------------------------------------ order sending
    async def dispatch_orders(self, name):
        mon = self.monitors[name]
        for sig in mon.pop_order_signals():
            res = await self.place_entry(sig)
            mon.record_order_result(sig, res["status"])
            self.log(res["message"])
            self.add_order_row(sig, res)
            self.notify(res["message"][:160], {"sent": "positive", "dry_run": "info"}.get(res["status"], "negative"))
        self.bump("plan")

    # ------------------------------------------------------------------ background loops
    async def scan(self):
        """Main scan, every SCAN_INTERVAL_SECONDS: index LTP, BEP Support board, cards, watch, orders tracking."""
        if not self.connected:
            self.status = "Waiting for a valid Upstox token (Setup page)"
            return
        active = self.active()
        if not self._gift_busy and self.gift.due():          # GIFT Nifty: its own task, never delays the scan
            self._gift_busy = True
            background_tasks.create(self.refresh_gift())
        active = self.active()
        if not active or self._scan_busy:
            return
        self._scan_busy = True
        try:
            ltps, err = await run.io_bound(self.fetch_index_ltps, active)
            self.status = ("Monitoring " + " + ".join(active)) if market_utils.is_market_open() else \
                f"Market closed (Mon–Fri {config.MARKET_OPEN}–{config.MARKET_CLOSE} IST)"
            for name in active:
                ltp = ltps.get(name)
                if ltp is None:
                    self.log(f"ERROR: {name} LTP unavailable ({err})")
                    continue
                self.ltp[name] = ltp
                mon = self.monitors[name]
                try:
                    res = await run.io_bound(mon.step, self.client, ltp, None, _now())
                    for msg in res["logs"]:
                        self.log(msg)
                    if res["board_changed"] or res["recalculated"]:
                        self.bump("board")
                    if res.get("first_updated") or res.get("latest_updated") or res["recalculated"]:
                        self.bump("cards")

                    alerts = mon.pop_support_alerts()
                    if alerts:
                        if config.ALERTS_PAUSED:
                            self.log(f"Alerts paused: {len(alerts)} BEP support alert(s) not shown.")
                        for e in alerts:
                            self.log(notifier.support_label(e))
                        if not config.ALERTS_PAUSED:               # one short toast per scan; full text in the Log
                            self.notify("\n".join(notifier.support_short(alerts)), "positive", f"{name} BEP Support")
                    if res.get("cross_changed"):
                        self.bump("cross")
                    cross_failed = self.watches[name].sync_cross_failures(mon.cross.rows)   # first: frees a
                    cross_added = self._cross_signals(name, mon.pop_cross_signals())        # failed pair's strikes
                    # ⭐ after the BEP cross: a ⭐ on the cross buy leg (same option, same candle) is not added
                    star_added = self._add_star_rows(name, alerts)
                    star_failed = self.watches[name].sync_star_failures(mon.support)

                    now = _now()
                    before = [(r["id"], r["status"], r["last_time"]) for r in self.watches[name].rows]
                    fired = await run.io_bound(self.watches[name].step, self.client, mon.expiry, now)
                    auto_rows = [r for r in fired if r.get("trig_how") == "auto" or r.get("auto_fire")]
                    fired = [r for r in fired if r not in auto_rows]
                    for r in fired:
                        self.log("COMMON PRICE: " + notifier.common_label(r))
                    if fired:
                        if config.ALERTS_PAUSED or not market_utils.is_market_open(now):
                            self.log("Common price alert not shown (alerts paused or market closed).")
                        else:                              # one short toast per scan; full text in the Log
                            self.notify("\n".join(notifier.common_short(r) for r in fired), "positive",
                                        f"{name} Order Management")
                    after = [(r["id"], r["status"], r["last_time"]) for r in self.watches[name].rows]
                    if star_added or star_failed or cross_added or cross_failed or fired or before != after:
                        self.bump("watch")
                    await self._auto_fired(name, auto_rows)         # "Auto order" Pair rows: order at once

                    await self.handle_pair_exits(name)
                    await self.cancel_dead_limits(name)
                    await self.dispatch_orders(name)
                except Exception as ex:
                    self.log(f"{name} BEP monitor error: {ex}")
            await self.refresh_order_tracking()
        finally:
            self._scan_busy = False

    async def opening(self):
        """Every OPENING_TICK_SECONDS. Prep window (OPENING_PREP_START-09:15): read the pre-open price and prepare
        strikes / BEPs. 09:15:00-09:20: check only the prepared strikes and send at once."""
        if not self.connected or self._open_busy:
            return
        now = _now()
        h, m = (int(x) for x in config.OPENING_PREP_START.split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        first = now.replace(hour=9, minute=15, second=0, microsecond=0)
        end = now.replace(hour=9, minute=20, second=0, microsecond=0)
        if not (start <= now < end) or not market_utils.is_trading_day(now):
            return
        self._open_busy = True
        try:
            for name in self.active():
                mon, msgs = self.monitors[name], []
                try:
                    if now < first:
                        ltps, _err = await run.io_bound(self.fetch_index_ltps, [name])
                        if ltps.get(name) is not None:
                            self.ltp[name] = ltps[name]
                            await run.io_bound(mon.prepare_opening, self.client, ltps[name], now, msgs.append)
                    else:
                        await run.io_bound(mon.opening_tick, self.client, now, msgs.append)
                except Exception as ex:
                    msgs.append(f"{name} opening order error: {ex}")
                for msg in msgs:
                    self.log(msg)
                await self.dispatch_orders(name)
        finally:
            self._open_busy = False

    async def refresh_order_tracking(self):
        if not any(r.get("status") in trade_tracker.TRACKED for r in self.order_log):
            return
        trades = None
        now_ts = datetime.now().timestamp()
        if any(r.get("status") == "SENT" and not r.get("bk") for r in self.order_log) and \
                now_ts - self._trades_at >= config.TRADES_REFRESH_SECONDS:
            trades, t_err = await run.io_bound(self.client.fetch_trades_for_day)
            self._trades_at = now_ts
            if trades is None:
                self.log(f"Upstox trade book unavailable ({t_err}); Entry/Exit not updated.")
        expiries = {n: self.monitors[n].expiry for n in self.monitors}
        try:
            updates, _err = await run.io_bound(trade_tracker.compute, self.client, self.order_log, expiries, trades,
                                               None, dict(self.option_ltp))
        except Exception as ex:
            self.log(f"Order tracking error: {ex}")
            return
        if updates:
            new_exits, custom_exits = [], []
            for r in self.order_log:
                if r["id"] in updates:
                    u = updates[r["id"]]
                    hit = u.pop("custom_hit", None)
                    if hit and (r.get("status") == "SENT" or self._bk(r)) and not r.get("c_done") \
                            and not self.squared_off(r):
                        custom_exits.append((r, hit))
                    if u.pop("exit_new", False):
                        u["detail"] = f"{r.get('detail', '')} | Exit: {u['exit_reason']} {u['exit_time']}".strip(" |")
                        new_exits.append(r)
                    r.update(u)
            for r in new_exits:
                self.log(f"DRY RUN {r['index']} {r['opt']} {r['strike']}: simulated exit {r['exit_reason']} at "
                         f"{r['exit_time']}, price {r['exit']} -> {r['points']} points, P&L {r['pnl']}")
            for r, (price, at, reason) in custom_exits:
                ok = self._request_exit(r, reason)
                r["c_done"] = f"{at} {reason} " + ("EXIT REQUESTED" if ok else "NOT MANAGED")
                r["detail"] = f"{r.get('detail', '')} | {reason} {at} ({price}): " + (
                    "exit requested" if ok else "not managed in this mode").strip(" |")
                self.log(f"{r['index']} {r['strike']}{r['opt']}: {reason} at {at} ({price}) - "
                         + ("exit requested." if ok else "order placed in another mode: not managed now."))
                self.notify(f"{r['index']} {r['strike']}{r['opt']}: {reason} at {at} - "
                            + ("exiting" if ok else "NOT managed (other mode)"),
                            "warning" if ok else "negative", f"{r['index']} exit {r['strike']}{r['opt']}")
            if new_exits or custom_exits:
                self._rewrite_orders_file()
            self.bump("orders")

    # ------------------------------------------------------------------ broker orders: entry + position (PR 3)
    def _bk(self, o):
        """The order's broker position state (positions.py), parsed once and kept on the row; None for rows
        without one."""
        if not o:
            return None
        if o.get("_bk") is None:
            try:
                o["_bk"] = json.loads(o.get("bk") or "null")
            except (TypeError, ValueError):
                o["_bk"] = None
            if o["_bk"] is None:
                o["_bk"] = False
        return o["_bk"] or None

    def _manager(self):
        return positions.PositionManager(self.broker, float(config.AUTO_ORDER_SL_LIMIT_GAP))

    def _live_count_today(self) -> int:
        return sum(1 for o in self.order_log if o.get("status") == "SENT" and (self._bk(o) or {}).get("mode") != "dry")

    def _order_plan(self, sig) -> dict:
        """SL / Target / trail of a new order (positions.new_state plan) from the signal's params and app rules."""
        cfg = config.AUTO_ORDER[sig["index"]]
        p, app = sig.get("params") or {}, sig.get("app_rules") or {}
        sl = None if p.get("is_sl") is False else {"type": p.get("sl_type", cfg.get("sl_type", "POINT")),
                                                   "value": float(p.get("sl", cfg.get("sl")))}
        tgt = None if p.get("is_tgt") is False else {"type": p.get("tgt_type", cfg.get("tgt_type", "POINT")),
                                                     "value": float(p.get("tgt", cfg.get("tgt")))}
        max_sl = None
        c_sl = exit_rules.parse(app.get("c_sl"))
        if sl is None and c_sl and c_sl.get("type") == "CANDLE" and sig.get("kind") != "opening":
            max_sl = float(cfg.get("candle_max_sl", 17) or 0) or None       # safety SL for a CANDLE-only SL
        trail = None
        t = self.app_trail({"c_trail": app.get("c_trail", "")})
        if t:
            trail = {"trigger": float(t["trigger"]), "gap": float(t["gap"]), "step": float(t.get("step") or 1)}
        elif p.get("trail", cfg.get("trail")):
            trail = {"trigger": float(p.get("trail_trigger", cfg.get("trail_trigger", 20))),
                     "gap": float(p.get("trail_gap", cfg.get("trail_gap", 20))),
                     "step": float(p.get("trail_point", cfg.get("trail_point", 1)) or 1)}
        return {"sl": sl, "max_sl": max_sl, "tgt": tgt, "trail": trail}

    @staticmethod
    def _payload(sig, plan, mode, broker_name, tag, lots, units) -> dict:
        """What was sent, kept in the orders file (JSON column). Uses the keys trade_tracker._params reads."""
        sl, tgt, tr = plan.get("sl"), plan.get("tgt"), plan.get("trail")
        return {"type": "broker_order", "broker": broker_name, "mode": mode, "order_type": "MARKET",
                "transaction_type": "BUY", "qty": lots, "units": units, "tag": tag,
                "is_sl": bool(sl), "sl_type": (sl or {}).get("type", ""), "sl_value": (sl or {}).get("value"),
                "max_sl": plan.get("max_sl"),
                "is_tgt": bool(tgt), "tgt_type": (tgt or {}).get("type", ""), "tgt_value": (tgt or {}).get("value"),
                "is_trail_set": bool(tr), "trail_trigger": (tr or {}).get("trigger"),
                "trail_gap": (tr or {}).get("gap"), "trail_point": (tr or {}).get("step")}

    async def place_entry(self, sig) -> dict:
        """Sends one entry (market BUY) through the broker of the current mode and starts its position.
        Returns {"status": sent|dry_run|failed|blocked, "message", "payload", "detail", "bk"}.
        Pause algo and (Sandbox / Live) the daily order limit apply. The SL goes to the exchange after the fill."""
        name, mode = sig["index"], config.BROKER_MODE
        lots = int((sig.get("params") or {}).get("qty") or config.AUTO_ORDER[name]["qty"])
        units = lots * int(config.INDEXES[name]["lot_size"])
        plan = self._order_plan(sig)
        self._tag_seq += 1
        tag = f"BEP-{_now().strftime('%H%M%S')}-{self._tag_seq}"
        payload = self._payload(sig, plan, mode, self.broker.name, tag, lots, units)
        head = f"{name} {sig['opt']} {sig['strike']} [{sig.get('kind_label', 'Opening candle')}]"
        if config.AUTO_ORDER_PAUSED:
            return {"status": "blocked", "message": f"Algo trading is PAUSED; NOT sent: {head}", "payload": payload,
                    "detail": "Algo trading paused"}
        if sig.get("kind") != "opening" and not sig.get("loss_ok"):          # max loss per day (09:15 exempt)
            ml = self.max_loss_state(name)
            if ml["hit"]:
                why = (f"Max loss per day reached (loss ₹{ml['loss']:,.0f}, limit ₹{ml['limit']:,.0f})")
                return {"status": "blocked", "message": f"{why}; NOT sent: {head}", "payload": payload,
                        "detail": why}
        if mode != "dry" and self._live_count_today() >= config.AUTO_ORDER_DAILY_LIMIT:
            why = f"Daily limit ({config.AUTO_ORDER_DAILY_LIMIT}) reached"
            return {"status": "blocked", "message": f"{why}; NOT sent: {head}", "payload": payload, "detail": why}
        ik, err = await self.option_key(name, sig["strike"], sig["opt"])
        if not ik:
            return {"status": "failed", "message": f"ORDER FAILED {head}: {err}", "payload": payload,
                    "detail": err}
        ent = sig.get("entry") or {}
        etype = ent.get("type") or bk.MARKET
        eprice, etrig = float(ent.get("price") or 0), float(ent.get("trigger") or 0)
        payload.update(order_type=etype, price=eprice or None, trigger_price=etrig or None)
        st = positions.new_state(mode, tag, ik, units, plan, float(sig["ltp"]), sig.get("kind", ""),
                                 etype=etype, eprice=eprice, etrig=etrig)
        ref = float(sig["ltp"])
        if mode == "dry" and etype != bk.MARKET and not market_utils.is_market_open():
            ref = None                                # a stale pre-open LTP must not fill a dry-run limit
        req = bk.OrderRequest(ik, bk.BUY, units, etype, price=eprice, trigger_price=etrig, tag=tag,
                              symbol=f"{name} {int(sig['strike'])} {sig['opt']}", ref_price=ref)
        res = await run.io_bound(self._manager().place_entry, st, req, _stamp())
        label = brokers.MODE_LABELS[mode].upper()
        plan_txt = (("SL" if etype == bk.MARKET else "SL (from the fill)") +
                    f" {positions.sl_trigger(plan, eprice or float(sig['ltp'])) or '-'} at the exchange after the fill"
                    + (f", target {positions.target_level(plan, float(sig['ltp']))}" if plan.get("tgt") else
                       ", no target")
                    + (f", trail {plan['trail']['trigger']:g}/{plan['trail']['gap']:g}" if plan.get("trail") else ""))
        if res.ok:
            status = "dry_run" if mode == "dry" else "sent"
            detail = f"{label} {bk.describe(req)}, order {res.order_id}; {plan_txt}"
            return {"status": status, "message": f"ORDER {label} {head} (LTP {float(sig['ltp']):.2f}): {detail}",
                    "payload": payload, "detail": detail, "bk": st}
        detail = f"{res.message}" + (f" ({res.code})" if res.code else "")
        return {"status": "failed", "message": f"ORDER FAILED {head}: {detail}", "payload": payload,
                "detail": detail + (" (not retried)" if not res.uncertain else "")}

    def _request_exit(self, o, reason) -> bool:
        """Asks the position manager to exit this order (done on the next 2 s tick)."""
        st = self._bk(o)
        if not st or st.get("mode") != config.BROKER_MODE:
            return False
        ok = positions.PositionManager.request_exit(st, reason, _stamp())
        if ok:
            o["detail"] = f"{o.get('detail', '')} | Exit requested: {reason} {_stamp()}".strip(" |")
            self._rewrite_orders_file()
            self.bump("orders", "watch")
        return ok

    def open_lots(self, o) -> int:
        """Lots still held by a broker position (after partial exits); 0 if none / closed."""
        st = self._bk(o)
        if not st or not positions.is_open(st):
            return 0
        return positions.held(st) // int(config.INDEXES.get(o.get("index"), {}).get("lot_size", 1) or 1)

    def partial_exitable(self, o) -> bool:
        """The order can sell part of its lots now: open broker position in this mode, 2+ lots held, nothing
        else running."""
        st = self._bk(o)
        return bool(st) and st.get("mode") == config.BROKER_MODE and self.open_lots(o) >= 2 \
            and not st.get("exit_req") and not st.get("part_req") and not positions.entry_open(st) \
            and not self.squared_off(o)

    def partial_exit(self, order_id, lots) -> str:
        """Sells `lots` of an open order now (market); the exchange SL is lowered to what stays first.
        Not blocked by Pause algo / the daily limit (an exit). Returns "" or why not."""
        o = next((x for x in self.order_log if x["id"] == order_id), None)
        st = self._bk(o)
        if not o or not st:
            return "not a broker order"
        if st.get("mode") != config.BROKER_MODE:
            return f"this order was placed in {st.get('mode', '?').upper()} mode - switch back to it first"
        lot = int(config.INDEXES.get(o["index"], {}).get("lot_size", 1) or 1)
        held = self.open_lots(o)
        if not 1 <= int(lots) < held:
            return f"choose 1 to {held - 1} lot(s) ({held} open); Square off closes all"
        why = positions.PositionManager.request_partial(st, int(lots) * lot, _stamp())
        if why:
            return why
        left = self.open_lots(o) - int(lots)
        o["detail"] = f"{o.get('detail', '')} | Partial exit requested: {int(lots)} lot(s) {_stamp()}".strip(" |")
        self.log(f"{o['index']} {o['strike']}{o['opt']} #{o['id']}: partial exit {int(lots)} lot(s) requested; "
                 f"{left} lot(s) stay (the exchange SL is lowered first).")
        self._rewrite_orders_file()
        self.bump("orders", "watch")
        return ""

    def entry_open(self, o) -> bool:
        """The order's Limit / Stop-limit entry still waits (or is partly filled) at the exchange."""
        st = self._bk(o)
        if st:
            return st.get("etype", bk.MARKET) != bk.MARKET and positions.entry_open(st)
        m = limit_orders.parse(o.get("mg"))
        return bool(m) and limit_orders.pending(m)

    def entry_cancellable(self, o) -> bool:
        st = self._bk(o)
        if st:
            return self.entry_open(o) and not st.get("cancel_req") and st.get("mode") == config.BROKER_MODE
        return limit_orders.cancellable(limit_orders.parse(o.get("mg")))

    def _card_for_order(self, o):
        cw = self.watches.get(o.get("index"))
        if not cw:
            return None
        return next((r for r in cw.rows if r.get("order_id") == o["id"]), None)

    async def broker_tick(self):
        """Every OPTION_LTP_SECONDS (market hours): moves every open broker position on (positions.py)."""
        if self._bk_busy:
            return
        items, rows = [], {}
        for o in self.order_log:
            st = self._bk(o)
            if not st or st.get("closed") or st.get("mode") != config.BROKER_MODE:
                continue
            items.append((o["id"], st, st.get("ik") or o.get("ik"), self.option_ltp.get(self._opt_key(o))))
            rows[o["id"]] = o
        if not items:
            return
        self._bk_busy = True
        try:
            now = _now()
            eod = now.strftime("%H:%M") >= config.EOD_EXIT_TIME
            events = await run.io_bound(self._manager().tick, items, _stamp(), eod)
        except Exception as ex:
            self.log(f"Broker tick error: {ex}")
            return
        finally:
            self._bk_busy = False
        for ev in events:
            o = rows.get(ev["key"])
            if o is None:
                continue
            what = f"{o['index']} {o['strike']}{o['opt']} #{o['id']}"
            kind = ev["kind"]
            if kind == "fill":
                o["entry"], o["filled_qty"] = ev["price"], ev["qty"]
                self.log(f"{what}: entry filled {ev['qty']} @ {ev['price']:.2f}.")
            elif kind == "sl":
                self.log(f"{what}: {ev['text']}.")
            elif kind == "partial":
                lot = int(config.INDEXES.get(o["index"], {}).get("lot_size", 1) or 1)
                fill = (self._bk(o) or {}).get("fill")
                pnl = round((ev["price"] - fill) * ev["qty"], 2) if fill is not None else None
                o["detail"] = (f"{o.get('detail', '')} | Partial exit {ev['qty'] // lot} lot(s) @ {ev['price']} "
                               f"{ev['time']}" + (f", P&L {pnl}" if pnl is not None else "")).strip(" |")
                self.log(f"{what}: PARTIAL EXIT {ev['qty']} @ {ev['price']}" + (f" (P&L {pnl})" if pnl is not None else "")
                         + f"; {ev['left']} units ({ev['left'] // lot} lot(s)) stay.")
                self.notify(f"{o['index']} {o['strike']}{o['opt']}: partial exit {ev['qty'] // lot} lot(s) @ {ev['price']}"
                            f" - {ev['left'] // lot} lot(s) stay", "info", f"{o['index']} partial exit")
            elif kind == "exit":
                o.update(exit=ev["price"], exit_time=ev["time"][:5], exit_reason=ev["reason"])
                o["detail"] = f"{o.get('detail', '')} | Exit: {ev['reason']} {ev['time']} @ {ev['price']}".strip(" |")
                card = self._card_for_order(o)
                if card and card.get("status") != "exited":
                    card["status"] = "exited"
                    self.watches[o["index"]].save()
                self.log(f"{what}: EXIT {ev['reason']} @ {ev['price']}.")
                self.notify(f"{o['index']} {o['strike']}{o['opt']}: exit {ev['reason']} @ {ev['price']}",
                            "info", f"{o['index']} exit {o['strike']}{o['opt']}")
            elif kind == "cancelled":
                o["status"] = "CANCELLED"
                o["detail"] = f"{o.get('detail', '')} | {ev['text']}".strip(" |")
                card = self._card_for_order(o)
                if card:
                    self.watches[o["index"]].set_order_status(card["id"], "CANCELLED")
                self.log(f"{what}: {ev['text']}.")
                self.notify(f"{o['index']} {o['strike']}{o['opt']}: {ev['text']}", "info")
            elif kind == "failed":
                o["status"] = "FAILED"
                o["detail"] = f"{o.get('detail', '')} | {ev['text']}".strip(" |")
                card = self._card_for_order(o)
                if card:
                    self.watches[o["index"]].set_order_status(card["id"], "FAILED")
                self.log(f"{what}: {ev['text']}.")
                self.notify(f"{o['index']} {o['strike']}{o['opt']}: {ev['text']}", "negative")
            elif kind == "alert":
                o["detail"] = f"{o.get('detail', '')} | ALERT {ev['text']} {_stamp()}".strip(" |")
                self.log(f"ALERT {what}: {ev['text']}.")
                self.notify(f"{o['index']} {o['strike']}{o['opt']}: {ev['text']} - use Square off / Exit now",
                            "negative", f"{o['index']} {ev['text'][:20]} {o['strike']}{o['opt']}")
            else:
                self.log(f"{what}: {ev.get('text', '')}")
        self._rewrite_orders_file()
        self.bump("orders", "watch")

    # ------------------------------------------------------------------ manual square-off
    @staticmethod
    def squared_off(r) -> bool:
        """A manual square-off was sent (live) or recorded (dry run) for this order."""
        return str(r.get("sq") or "").startswith(("SENT", "DRY RUN"))

    def is_open(self, r) -> bool:
        """Order still holds a position as far as the app knows: placed, no exit yet, not squared off.
        A limit order holds one only once something is filled."""
        st = self._bk(r)
        if st:
            return r.get("status") in ("SENT", "DRY RUN") and positions.is_open(st)
        m = limit_orders.parse(r.get("mg"))
        if m is not None and not limit_orders.held(m):
            return False
        return (r.get("status") in ("SENT", "DRY RUN") and trade_tracker._f(r.get("exit")) is None
                and not self.squared_off(r) and not str(r.get("c_done") or "").endswith(("SENT", "DRY RUN"))
                and "Square-off SENT" not in str(r.get("detail") or ""))

    def open_on_option(self, index, strike, opt, exclude_id=None) -> list:
        """Open orders on the same option (asked before placing another order on it)."""
        try:
            k = int(float(strike))
        except (TypeError, ValueError):
            return []
        return [o for o in self.order_log if o["id"] != exclude_id and o.get("index") == index
                and o.get("opt") == opt and int(float(o.get("strike") or 0)) == k and self.is_open(o)]

    @staticmethod
    def order_label(o) -> str:
        return f"#{o['id']} {o.get('kind', 'Opening candle')} {o.get('time', '')}"

    async def _exit_ltp(self, o):
        """LTP for a dry-run manual exit: a fresh Upstox quote, else the cards' / tracker's last LTP."""
        try:
            expiries = {n: self.monitors[n].expiry for n in self.monitors}
            ik = await run.io_bound(trade_tracker._instrument_key, self.client, o, expiries)
            if ik and self.connected:
                ltps, _err = await run.io_bound(self.client.fetch_ltp, [ik])
                v = (ltps or {}).get(ik) or (ltps or {}).get(ik.replace("|", ":"))
                if v is not None:
                    return round(float(v), 2)
        except Exception:
            pass
        for v in (self.option_ltp.get((o.get("index"), int(float(o.get("strike") or 0)), o.get("opt"))),
                  trade_tracker._f(o.get("ltp_live")), trade_tracker._f(o.get("entry")), trade_tracker._f(o.get("ltp"))):
            if v is not None:
                return round(float(v), 2)
        return None

    async def square_off(self, order_id, reason="Manual square-off"):
        """Exits THIS order only: its exchange SL order becomes MARKET (or a market SELL without one) on the next
        2 s tick. Not blocked by Pause algo / the daily limit. Returns {"status", "message"} or None."""
        o = next((x for x in self.order_log if x["id"] == order_id), None)
        if not o:
            return None
        st = self._bk(o)
        if not st:
            self.log(f"#{order_id}: not a broker order - square it off in the Upstox app.")
            return {"status": "failed", "message": "not a broker order"}
        if st.get("mode") != config.BROKER_MODE:
            msg = (f"#{order_id} was placed in {brokers.MODE_LABELS.get(st.get('mode'), st.get('mode'))} mode; "
                   f"switch the order mode back to square it off.")
            self.notify(msg, "warning")
            return {"status": "failed", "message": msg}
        if not self._request_exit(o, reason):
            return None
        t = _stamp()
        o["sq"] = f"SENT {t}" if st.get("mode") != "dry" else f"DRY RUN {t}"
        self.log(f"{o['index']} {o['strike']}{o['opt']} #{o['id']}: {reason} - exit requested.")
        self.notify(f"{o['index']} {o['strike']}{o['opt']}: {reason} - exiting", "warning",
                    f"{o['index']} square-off {o['strike']}{o['opt']}")
        self._rewrite_orders_file()
        self.bump("orders", "watch")
        return {"status": "sent" if st.get("mode") != "dry" else "dry_run", "message": "exit requested"}

    async def square_off_all(self):
        """Exits every open order (each one on its own)."""
        results = []
        for o in list(self.order_log):
            if self.is_open(o):
                results.append(await self.square_off(o["id"], "Square off all"))
        return [r for r in results if r]

    # ------------------------------------------------------------------ Manual Entry LIMIT orders (limit_orders.py)
    def _limit_rules(self, name, r):
        """(sl, tgt, trail, app_rules) for a limit card. LTP / POINT SL and Target (the card's own, else Setup) and
        the trailing SL are watched by the app on the live LTP; a CANDLE rule goes to c_sl / c_tgt instead (checked
        on candle closes, like any app-watched rule; no trailing with a CANDLE SL)."""
        cfg = config.AUTO_ORDER[name]
        defaults = {"sl": (cfg.get("sl_type", "POINT"), cfg.get("sl")), "tgt": self.row_target(name, r)}
        out, app_rules = {}, {}
        for k in ("sl", "tgt"):
            rule = exit_rules.parse(r.get("c_" + k))
            if rule is None:
                t, v = defaults[k]
                try:
                    rule = exit_rules.make_rule(k, t if t in ("LTP", "POINT") else "POINT", float(v), custom=False)
                except (TypeError, ValueError):
                    rule = None
            if exit_rules.is_none(rule):              # Target None (PR #48): no app target
                out[k] = None
            elif rule and rule["type"] == "CANDLE":
                app_rules["c_" + k] = json.dumps({**rule, "set_at": ""})
                out[k] = None
            else:
                out[k] = {"type": rule["type"], "value": float(rule["value"])} if rule else None
        tr = self.row_trail(name, r)
        trail = {"trigger": float(tr["trigger"]), "gap": float(tr["gap"]), "step": float(tr["point"] or 1)} \
            if tr["on"] and out["sl"] else None
        return out["sl"], out["tgt"], trail, app_rules

    def _limit_save(self, o, m):
        o["mg"] = limit_orders.dump(m)

    @staticmethod
    def _opt_key(o):
        try:
            return o["index"], int(float(o["strike"])), o["opt"]
        except (KeyError, TypeError, ValueError):
            return None

    def cancel_side_effects(self, o) -> list:
        """Other orders on the same option that AlgoDelta's cancel_order (no order id) may also hit: other open
        limit orders, and open strategy orders whose SL / Target orders wait at the broker."""
        k = self._opt_key(o)
        out = []
        for x in self.order_log:
            if x["id"] == o["id"] or self._opt_key(x) != k:
                continue
            mx = limit_orders.parse(x.get("mg"))
            if (mx is None and self.is_open(x)) or limit_orders.pending(mx):
                out.append(x)
        return out

    def limit_active(self) -> dict:
        """Counts for the header banner: limit orders waiting at the exchange / positions protected by the app."""
        waiting = held = 0
        for o in self.order_log:
            st = self._bk(o)
            if st:                                    # broker position: its SL is at the exchange
                if self.entry_open(o) and st.get("fill") is None:
                    waiting += 1
                continue
            m = limit_orders.parse(o.get("mg"))
            if m and limit_orders.pending(m) and not limit_orders.held(m):
                waiting += 1
            elif m and self.is_open(o):
                held += 1
        return {"waiting": waiting, "held": held}

    async def place_limit_order(self, name, row_id):
        """⚡ on a Limit / Stop-limit card (PR 4): a LIMIT BUY at the card's price, or an SL BUY (stop-limit:
        waits at the exchange until the LTP rises to the trigger, then a limit). After the fill it is the same
        position as a market entry: exchange SL, trailing, target, exits (positions.py)."""
        cw = self.watches[name]
        r = cw.get(row_id)
        if not r or r["status"] != "triggered" or r.get("order_status") in ORDER_LOCKED:
            return None
        price = self.limit_price(r)
        if not price:
            self.notify(f"{name} {r['strike']}{r['opt']}: set the limit price first.", "warning")
            return None
        trigger = self.stop_prices(r)[0]
        ltp = self.row_live_ltp(name, r)
        if ltp is None:
            ltp = await self.quote_option(name, r["strike"], r["opt"])
        if trigger and ltp is not None and ltp >= trigger:
            self.notify(f"{name} {r['strike']}{r['opt']}: the LTP {ltp:.2f} is already at or above the trigger "
                        f"{trigger:.2f} - raise the trigger, or use Limit / Market.", "warning")
            return None
        entry = {"type": bk.SL if trigger else bk.LIMIT, "price": float(price), "trigger": float(trigger or 0)}
        note = (f"STOP-LIMIT trigger {trigger:.2f} limit {price:.2f}" if trigger else f"LIMIT {price:.2f}") + (
            f", LTP {ltp:.2f} at the click" if ltp is not None else "")
        res = await self.place_watch_order(name, row_id, fill_ltp=ltp, entry_note=note, entry=entry)
        if res:
            st = ORDER_STATUS.get(res["status"], res["status"].upper())
            if res["status"] == "dry_run" and not market_utils.is_market_open():
                st += " (market not open yet: the dry-run limit waits for 09:15)"
            self.notify(f"{name} {r['strike']}{r['opt']}: "
                        + (f"STOP-LIMIT {trigger:.2f} → {price:.2f} " if trigger else f"LIMIT {price:.2f} ") + st
                        + ("" if res["status"] in ("sent", "dry_run") else f" - {res['detail']}"),
                        {"sent": "positive", "dry_run": "info"}.get(res["status"], "negative"),
                        f"{name} limit {r['strike']}{r['opt']}")
        return res

    async def cancel_limit(self, order_id, reason="Cancelled by you"):
        """Cancels THIS order's unfilled Limit / Stop-limit entry by its order ID (a filled part stays, with its
        exchange SL). Done on the next 2 s tick. Old AlgoDelta rows (mg) can't be cancelled from here."""
        o = next((x for x in self.order_log if x["id"] == order_id), None)
        st = self._bk(o)
        if st:
            if not self.entry_cancellable(o) or not positions.PositionManager.request_cancel(st, reason):
                return None
            filled = st.get("fill") is not None
            self.log(f"{o['index']} {o['strike']}{o['opt']} #{o['id']}: cancel requested ({reason})"
                     + (f"; {st['fill_qty']} filled stay, with the exchange SL." if filled else "."))
            self.notify(f"{o['index']} {o['strike']}{o['opt']}: cancelling the "
                        + ("unfilled rest" if filled else "limit order") + f" ({reason})", "info",
                        f"{o['index']} cancel {o['strike']}{o['opt']}")
            o["detail"] = f"{o.get('detail', '')} | Cancel ({reason}) {_stamp()}".strip(" |")
            self._rewrite_orders_file()
            self.bump("orders", "watch")
            return {"status": "sent", "message": "cancel requested"}
        return await self._cancel_old_limit(order_id, reason)

    async def _cancel_old_limit(self, order_id, reason="Cancelled by you"):
        """Sends AlgoDelta cancel_order for a limit order still open at the exchange (or CANCEL FAILED: retry).
        Live: CANCEL SENT, then the Upstox order book confirms it (else CANCEL FAILED after
        LIMIT_CANCEL_CONFIRM_SECONDS). Dry run: CANCELLED at once. Not blocked by Pause algo / the daily limit."""
        o = next((x for x in self.order_log if x["id"] == order_id), None)
        if not o or not limit_orders.cancellable(limit_orders.parse(o.get("mg"))):
            return None
        mon = self.monitors.get(o["index"])
        side = self.cancel_side_effects(o)
        res = {"status": "failed", "message": f"cancel of old limit orders: not available (PR 4) - cancel "
               f"{o['strike']}{o['opt']} in the Upstox app", "detail": "PR 4"}
        self.log(res["message"])
        if side:
            self.log(f"WARNING: cancel_order has no order id - it may also cancel the pending orders of "
                     + ", ".join(self.order_label(x) for x in side) + f" on {o['strike']}{o['opt']}.")
        t = _stamp()
        m = limit_orders.parse(o.get("mg"))                 # fresh: it may have changed while sending
        kept = f"; {m['filled']} filled at {m['fill']:.2f} stay protected" if limit_orders.held(m) else ""
        if o.get("status") == "DRY RUN":
            m.update(state="CANCELLED", msg=f"Cancelled {t} ({reason}, dry run){kept}")
        elif res["status"] == "sent":
            m.update(state="CANCEL SENT", cancel_ts=datetime.now().timestamp(),
                     msg=f"cancel_order sent {t} ({reason}); waiting for Upstox to confirm")
            self._book_at = 0.0                              # read the order book at the next tick
        else:
            m.update(state="CANCEL FAILED",
                     msg=f"cancel_order {ORDER_STATUS.get(res['status'], res['status']).upper()} {t}: {res['detail']}")
        self._limit_save(o, m)
        o["detail"] = f"{o.get('detail', '')} | Cancel ({reason}) {t}: {m['state']}".strip(" |")
        self._rewrite_orders_file()
        self.notify(f"{o['index']} {o['strike']}{o['opt']}: limit {m['price']:.2f} - {m['state']} ({reason}){kept}",
                    {"CANCELLED": "info", "CANCEL SENT": "warning"}.get(m["state"], "negative"),
                    f"{o['index']} cancel {o['strike']}{o['opt']}")
        self.bump("orders", "watch")
        return res

    def _limit_event(self, o, m, events):
        what = f"{o['index']} {o['strike']}{o['opt']}"
        for ev in events:
            if ev == "filled":
                self._trades_at = 0.0                    # fetch the fill from the trade book at the next scan
                note = f"LIMIT FILLED {m['filled']} at {m['fill']:.2f}"
                o["detail"] = f"{o.get('detail', '')} | {note} {m.get('fill_time', '')}".strip(" |")
                self.log(f"{what}: {note} - app SL / Target active ({limit_orders.describe(m) or 'none set'}).")
                self.notify(f"{what}: {note} - app SL / Target active.", "positive", f"{o['index']} limit filled")
            elif ev == "more":
                self.log(f"{what}: limit now {m['filled']} filled, average {m['fill']:.2f}.")
            elif ev == "cancelled":
                self.log(f"{what}: limit order CANCELLED ({m['msg']}).")
                self.notify(f"{what}: limit {m['price']:.2f} cancelled.", "info")
            elif ev == "rejected":
                o["detail"] = f"{o.get('detail', '')} | {m['msg']}".strip(" |")
                self.log(f"{what}: limit order {m['msg']}")
                self.notify(f"{what}: limit order {m['msg']}", "negative", f"{o['index']} limit rejected")

    async def check_limits(self, now=None):
        """Every OPTION_LTP_SECONDS in market hours: follows the limit orders and protects their fills.
        Live: reads the Upstox order book (every LIMIT_POLL_SECONDS while one is open / being cancelled).
        Dry run: fills at the price once the LTP is at or below it. Then the app's SL / Target / trailing SL are
        checked on the live LTP and a hit sends squareoff_position (once). At 15:15: open limits are cancelled and
        filled ones squared off."""
        if self._limit_busy or not any(o.get("mg") for o in self.order_log):
            return
        self._limit_busy = True
        dirty = False
        try:
            now = now or _now()
            t, ts = _stamp(), datetime.now().timestamp()
            eod = now >= now.replace(hour=exit_rules.EXIT_TIME.hour, minute=exit_rules.EXIT_TIME.minute,
                                     second=0, microsecond=0)

            def mine(pred):
                return [o for o in self.order_log if pred(o, limit_orders.parse(o.get("mg")))]

            # 1. live orders: the Upstox order book
            live = mine(lambda o, m: m and o.get("status") == "SENT" and limit_orders.pending(m))
            if live and ts - self._book_at >= config.LIMIT_POLL_SECONDS:
                self._book_at = ts
                book, err = await run.io_bound(self.client.fetch_order_book)
                if book is None:
                    self.log(f"Upstox order book unavailable ({err}); limit orders not updated.")
                else:
                    expiries = {n: self.monitors[n].expiry for n in self.monitors}
                    taken = {limit_orders.parse(o["mg"]).get("oid") for o in mine(lambda o, m: m and m.get("oid"))}
                    for o in live:
                        m = limit_orders.parse(o.get("mg"))
                        ik = await run.io_bound(trade_tracker._instrument_key, self.client, o, expiries)
                        hit = limit_orders.match_order(book, m, ik, taken - {m.get("oid")})
                        if hit is None:
                            continue
                        taken.add(hit.get("order_id"))
                        before = limit_orders.dump(m)
                        events = limit_orders.apply_book(m, hit, t)
                        if limit_orders.dump(m) != before:
                            self._limit_save(o, m)
                            dirty = True
                        self._limit_event(o, m, events)

            # 2. a cancel Upstox has not confirmed in time
            for o in mine(lambda o, m: m and m.get("state") == "CANCEL SENT" and m.get("cancel_ts")):
                m = limit_orders.parse(o["mg"])
                if ts - m["cancel_ts"] >= config.LIMIT_CANCEL_CONFIRM_SECONDS:
                    m.update(state="CANCEL FAILED",
                             msg="Cancel not confirmed by Upstox - Retry cancel, or cancel it in Upstox")
                    self._limit_save(o, m)
                    dirty = True
                    self.log(f"{o['index']} {o['strike']}{o['opt']}: {m['msg']}")
                    self.notify(f"{o['index']} {o['strike']}{o['opt']}: {m['msg']}", "negative",
                                f"{o['index']} cancel failed")

            # 3. dry run fills
            for o in mine(lambda o, m: m and o.get("status") == "DRY RUN" and m.get("state") == "OPEN"):
                m = limit_orders.parse(o["mg"])
                before = limit_orders.dump(m)
                filled = limit_orders.dry_fill(m, self.option_ltp.get(self._opt_key(o)), t)
                if limit_orders.dump(m) != before:                # filled, or a stop-limit triggered
                    self._limit_save(o, m)
                    dirty = True
                if filled:
                    self._limit_event(o, m, ["filled"])
                elif m.get("triggered") and '"triggered": false' in before:
                    self.log(f"{o['index']} {o['strike']}{o['opt']}: {m['msg']}")

            # 4. 15:15: cancel what is still open at the exchange (once)
            if eod:
                for o in mine(lambda o, m: limit_orders.cancellable(m) and not m.get("eod_cancel")):
                    m = limit_orders.parse(o["mg"])
                    m["eod_cancel"] = True
                    self._limit_save(o, m)
                    await self.cancel_limit(o["id"], "15:15")

            # 5. app SL / Target / trailing SL on the live LTP; 15:15 square-off
            for o in mine(lambda o, m: m and not m.get("exit") and self.is_open(o)):
                m = limit_orders.parse(o["mg"])
                ltp = self.option_ltp.get(self._opt_key(o))
                high = m.get("high")
                reason = "App 15:15 square-off" if eod else limit_orders.check_exit(m, ltp)
                if not reason:
                    if m.get("high") != high:
                        self._limit_save(o, m)
                        dirty = True
                    continue
                m["exit"] = f"{reason} {t}"
                self._limit_save(o, m)
                self.log(f"{o['index']} {o['strike']}{o['opt']}: {reason} hit (LTP {ltp}, "
                         f"{limit_orders.describe(m)}) - squaring off.")
                await self.square_off(o["id"], reason)
        except Exception as ex:
            self.log(f"Limit order check error: {ex}")
        finally:
            self._limit_busy = False
            if dirty:
                self._rewrite_orders_file()
                self.bump("orders", "watch")

    # ------------------------------------------------------------------ app-watched trailing SL (normal orders)
    @staticmethod
    def _trail_json(tr) -> str:
        return json.dumps({"trigger": float(tr["trigger"]), "gap": float(tr["gap"]),
                           "step": float(tr.get("point") or tr.get("step") or 1), "set_at": _stamp(), "high": None,
                           "done": ""})

    @staticmethod
    def app_trail(o):
        """The order's app-watched trailing SL (dict) or None. Limit orders keep theirs in mg instead."""
        try:
            t = json.loads(o.get("c_trail") or "null")
        except (TypeError, ValueError):
            return None
        return t if isinstance(t, dict) and "trigger" in t else None

    @staticmethod
    def trail_level(t, entry):
        """Current level of an app trailing SL: None until the high reaches entry + trigger."""
        high = t.get("high")
        if entry is None or high is None or high < entry + t["trigger"]:
            return None
        step = t.get("step") or 1
        return round(entry + int((high - t["gap"] - entry) // step) * step, 2)

    def set_order_trail(self, order_id, trail) -> bool:
        """Adds / changes (trail with on=True) or removes an app-watched trailing SL on a placed order (not a limit
        order, not the 09:15 order). It runs on the live LTP every 2 s; AlgoDelta's own trail (if sent) stays."""
        o = next((x for x in self.order_log if x["id"] == order_id), None)
        if not o or not self.order_rule_editable(o):
            return False
        st = self._bk(o)
        if st:                                           # broker position: replaces its trail (moves the exchange SL)
            on = bool(trail and trail.get("on"))
            st["plan"]["trail"] = {"trigger": float(trail["trigger"]), "gap": float(trail["gap"]),
                                   "step": float(trail.get("point") or 1)} if on else None
            self.log(f"{o['index']} {o['strike']}{o['opt']} #{o['id']}: trailing SL " + (
                f"set (trigger {trail['trigger']:g}, gap {trail['gap']:g}, point {trail.get('point', 1):g}); it "
                "moves the exchange SL" if on else "removed (the exchange SL stays where it is)") + ".")
            self._rewrite_orders_file()
            self.bump("orders", "watch")
            return True
        if o.get("mg"):                                  # limit / stop-limit: replaces mg.trail (PR #42)
            m = limit_orders.parse(o["mg"])
            on = bool(trail and trail.get("on"))
            m["trail"] = {"trigger": float(trail["trigger"]), "gap": float(trail["gap"]),
                          "step": float(trail.get("point") or 1)} if on else None
            self._limit_save(o, m)
            self.log(f"{o['index']} {o['strike']}{o['opt']} limit order: app trailing SL " + (
                f"set (trigger {trail['trigger']:g}, gap {trail['gap']:g}, point {trail.get('point', 1):g})"
                if on else "removed") + ".")
            self._rewrite_orders_file()
            self.bump("orders", "watch")
            return True
        o["c_trail"] = self._trail_json(trail) if trail and trail.get("on") else ""
        self.log(f"{o['index']} {o['strike']}{o['opt']} order: app trailing SL " + (
            f"set (trigger {trail['trigger']:g}, gap {trail['gap']:g}, point {trail.get('point', 1):g})"
            if o["c_trail"] else "removed") + ".")
        self._rewrite_orders_file()
        self.bump("orders", "watch")
        return True

    async def check_app_trails(self):
        """Every OPTION_LTP_SECONDS in market hours: app trailing SLs of open orders on the live LTP. Once the high
        reaches entry + trigger, SL = high - gap (steps of point), never down; LTP at or below it -> square-off
        (once). Entry = the order's Entry (Upstox fill live, signal LTP in dry run)."""
        dirty = False
        for o in list(self.order_log):
            t = self.app_trail(o)
            if not t or t.get("done") or o.get("mg") or self._bk(o) or not self.is_open(o):
                continue
            entry = trade_tracker._f(o.get("entry"))
            ltp = self.option_ltp.get(self._opt_key(o))
            if entry is None or ltp is None:
                continue
            if t.get("high") is None:
                t["high"] = entry
            level = self.trail_level(t, entry)
            if level is not None and ltp <= level:
                t["done"] = f"{_stamp()} at {level:g}"
                o["c_trail"] = json.dumps(t)
                self.log(f"{o['index']} {o['strike']}{o['opt']}: app trailing SL hit (LTP {ltp}, level {level:g}) "
                         "- squaring off.")
                await self.square_off(o["id"], "App trailing SL")
                continue
            if ltp > t["high"]:
                t["high"] = round(ltp, 2)
                o["c_trail"] = json.dumps(t)
                dirty = True
        if dirty:
            self._rewrite_orders_file()
            self.bump("orders", "watch")

    def trail_text(self, o) -> str:
        """e.g. "Trail 20/19 (app) · SL 155" for the card / Order Details."""
        st = self._bk(o)
        if st:
            tr = (st.get("plan") or {}).get("trail")
            if not tr:
                return ""
            lvl = positions.trail_level(tr, st.get("fill"), st.get("high"))
            return (f"Trail {tr['trigger']:g}/{tr['gap']:g}" + (f" \u00b7 SL {lvl:g}" if lvl is not None else "")
                    + (f" \u00b7 exchange SL {st['sl_trg']:.2f}" if st.get("sl_trg") is not None else ""))
        t = self.app_trail(o)
        if not t:
            return ""
        lvl = self.trail_level(t, trade_tracker._f(o.get("entry")))
        return (f"Trail {t['trigger']:g}/{t['gap']:g} (app)" + (f" \u00b7 SL {lvl:g}" if lvl is not None else "")
                + (f" \u2192 hit {t['done']}" if t.get("done") else ""))

    # ------------------------------------------------------------------ per-order SL / Target (Order Details)
    @staticmethod
    def order_rule_editable(r) -> bool:
        if Engine._bk(None, r):   # broker position: an edit REPLACES its rule (SL: the exchange order is modified)
            return r.get("status") in ("SENT", "DRY RUN") and positions.is_open(r["_bk"]) and \
                not r["_bk"].get("exit_req") and r["_bk"].get("mode") == config.BROKER_MODE
        m = limit_orders.parse(r.get("mg"))
        if m:                     # limit / stop-limit (PR #42): the app holds its rules, so an edit REPLACES them
            return (r.get("status") in ("SENT", "DRY RUN") and not m.get("exit") and not r.get("c_done")
                    and not Engine.squared_off(r) and trade_tracker._f(r.get("exit")) is None
                    and (limit_orders.pending(m) or limit_orders.held(m)))
        return (r.get("status") in ("SENT", "DRY RUN") and r.get("kind", "Opening candle") != "Opening candle"
                and trade_tracker._f(r.get("exit")) is None and not r.get("c_done") and not Engine.squared_off(r))

    @staticmethod
    def is_opening(r) -> bool:
        return r.get("kind", "Opening candle") == "Opening candle" and not r.get("mg")

    @staticmethod
    def order_rule(o, kind):
        """The SL / Target rule an order is watched with now (dict or None): its app rule (c_sl / c_tgt); for a
        limit / stop-limit order also the LTP / POINT rule kept in mg."""
        rule = exit_rules.parse(o.get("c_" + kind))
        if exit_rules.is_none(rule):
            rule = None
        st = Engine._bk(None, o)
        if rule is None and st and (st.get("plan") or {}).get(kind):
            p = st["plan"][kind]
            return exit_rules.make_rule(kind, p["type"], p["value"], custom=False)
        m = limit_orders.parse(o.get("mg"))
        if rule is None and m and m.get(kind):
            rule = exit_rules.make_rule(kind, m[kind]["type"], m[kind]["value"], custom=False)
        return rule

    @staticmethod
    def order_trail(o):
        """App trailing SL of an order as {"trigger", "gap", "step", ...} (limit orders: from mg) or None."""
        st = Engine._bk(None, o)
        if st:
            return (st.get("plan") or {}).get("trail")
        m = limit_orders.parse(o.get("mg"))
        if m:
            return m.get("trail")
        return Engine.app_trail(o)

    def _set_limit_rule(self, o, kind, rule):
        """Limit / stop-limit order: replaces its SL / Target. LTP / POINT go to mg (live LTP check), CANDLE to
        c_sl / c_tgt (candle closes); None removes it."""
        m = limit_orders.parse(o["mg"])
        if exit_rules.is_none(rule):                  # Target None = no target, the same as removing it
            rule = None
        m[kind] = {"type": rule["type"], "value": float(rule["value"])} if rule and rule["type"] != "CANDLE" else None
        o["c_" + kind] = json.dumps({**rule, "set_at": _stamp()}) if rule and rule["type"] == "CANDLE" else ""
        self._limit_save(o, m)

    def set_order_custom(self, row_id, kind, rule):
        """Sets (rule) or clears (None) an order's own SL / Target after it was placed. Watched by the app.
        Broker position (BEP Upstox): the new rule REPLACES the old one (an LTP / POINT SL moves the exchange SL
        order). Limit / stop-limit order (old path, PR 4): the new rule REPLACES the old one."""
        r = next((o for o in self.order_log if o["id"] == row_id), None)
        if not r or not self.order_rule_editable(r):
            return False
        st = self._bk(r)
        if st:                       # broker position: the new rule REPLACES the old one
            plan = st["plan"]
            if exit_rules.is_none(rule):
                rule = None
            if rule and rule["type"] == "CANDLE":
                r["c_" + kind] = json.dumps({**rule, "set_at": _stamp()})
                plan[kind] = None
                if kind == "sl" and not plan.get("max_sl"):
                    plan["max_sl"] = float(config.AUTO_ORDER[r["index"]].get("candle_max_sl", 17) or 0) or None
            else:
                r["c_" + kind] = ""
                plan[kind] = {"type": rule["type"], "value": float(rule["value"])} if rule else None
            if kind == "sl":
                positions.PositionManager.reset_sl(st)      # move / place the exchange SL on the next tick
            what = "SL" if kind == "sl" else "Target"
            self.log(f"{r['index']} {r['strike']}{r['opt']} #{r['id']}: {what} "
                     + (f"changed to {exit_rules.describe(rule)}" if rule else "removed")
                     + (" (the exchange SL is moved)" if kind == "sl" and rule and rule["type"] != "CANDLE" else ""))
            self._rewrite_orders_file()
            self.bump("orders", "watch")
            return True
        if r.get("mg"):
            self._set_limit_rule(r, kind, rule)
            self.log(f"{r['index']} {r['strike']}{r['opt']} limit order: {'SL' if kind == 'sl' else 'Target'} "
                     + (f"changed to {exit_rules.describe(rule)}" if rule else "removed") + " (app-watched).")
            self._rewrite_orders_file()
            self.bump("orders", "watch")
            return True
        r["c_" + kind] = json.dumps({**rule, "set_at": _stamp()}) if rule else ""
        what = "SL" if kind == "sl" else "Target"
        self.log(f"{r['index']} {r['strike']}{r['opt']} order: {what} "
                 + (f"set to {exit_rules.describe(rule)}" if rule else "removed"))
        self._rewrite_orders_file()
        self.bump("orders")
        return True


engine = Engine()
