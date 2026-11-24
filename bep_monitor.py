"""
bep_monitor.py — watches the strikes near ATM against each strike's BEP, for ONE index.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, time as dtime, datetime

import config
import market_utils
import support_logic
import bep
import pair_cross

CANDLE_MINUTES = 5
CANDLE = timedelta(minutes=CANDLE_MINUTES)
SESSION_FIRST_CANDLE = dtime(9, 15)
SESSION_LAST_CANDLE = dtime(15, 25)
MAX_CATCHUP_CANDLES = 12
MAX_BACKFILL_CANDLES = 80
FETCH_WORKERS = 8
NOTIFY_MAX_AGE = timedelta(minutes=6)
TICK = 0.05

class BepMonitor:
    def __init__(self, name: str = "NIFTY"):
        self.name = name
        self.cfg = config.INDEXES[name]
        self.strikes = None
        self.last_recalc = None
        self.expiry = None
        self.buckets = {}
        self.last_bucket = None
        
        self.support = {"CE": [], "PE": []}
        self._support_seq = 0
        self.quotes = {}
        self.leg_states = {}
        
        self.first_start = None
        self.first_found = {"CE": None, "PE": None}
        self.first_found_start = {"CE": None, "PE": None}
        self.first_view = {}
        
        self.latest_view = {}
        self._latest_sig = None

        self.candle_windows = {}
        self.candle_atm = {}         # candle start -> ATM strike (nearest to NIFTY's close of that candle)
        self.nifty_close = {}        # candle start -> NIFTY close
        self.new_support = []
        self._notified = set()
        
        # Order tracking
        self.side_lock = {}          # (date, opt) -> {"strike", "time", "status"}: ONE opening order per side per day
        self._open_low = {}          # (date, strike, opt) -> lowest live LTP in opening candle
        self.plan = None             # 09:15 opening plan, prepared before the open (see prepare_opening)
        self._plan_skip_logged = None
        self._far_logged = set()     # (date, strike, opt) already logged as "too far above BEP"
        self.new_orders = []         # signals waiting to be sent
        self.replay = False          # True = a past day rebuilt from historical candles: no alerts, no quotes
        self.bep_fn = None
        self.scan_all_csv = None     # None = config.SUPPORT_SCAN_ALL_CSV_STRIKES
        self.cross = pair_cross.PairCross(name, self.cfg["strike_interval"])   # BEP Pair cross table
        self.new_cross = []          # live pair-cross signals waiting for the engine (alert + Pair row)
        self._step_cache = {}
        self._client = None          # the client of the last step (live or replay), for explain_cross

    def _scan_all(self) -> bool:
        return config.SUPPORT_SCAN_ALL_CSV_STRIKES if self.scan_all_csv is None else self.scan_all_csv

    def get_bep(self, client, strike):
        """BEP of a strike: the uploaded CSV, else calculated. Replay mode swaps in its own lookup (bep_fn)."""
        if self.bep_fn:
            return self.bep_fn(client, strike)
        return bep.get_bep(client, self.expiry, strike, self.name)

    def _reset_leg_state(self, strike, opt):
        self.leg_states[(strike, opt)] = support_logic.new_leg_state()

    def reset_backfill(self):
        self.buckets.clear()
        self.last_bucket = None
        self._reset_support()

    def _reset_support(self):
        self.support = {"CE": [], "PE": []}
        self.cross.reset()
        self.leg_states = {}
        self.latest_view = {}
        self._latest_sig = None
        self.candle_windows = {}
        self.candle_atm = {}
        self.nifty_close = {}
        
        self.first_start = None
        self.first_found = {"CE": None, "PE": None}
        self.first_found_start = {"CE": None, "PE": None}
        self.first_view = {}

    def force_recalc(self):
        self.strikes = None
        self.last_recalc = None

    def step(self, client, index_ltp: float, expiry_hint, now) -> dict:
        self._client = client
        logs = []
        log_fn = logs.append
        result = {"board_changed": False, "recalculated": False, "first_updated": False,
                  "latest_updated": False, "logs": logs}
        if expiry_hint:
            self.expiry = expiry_hint
        if not self.expiry:
            self.expiry = market_utils.current_weekly_expiry(client, self.cfg["underlying_key"])
        if not self.expiry:
            log_fn(f"BEP monitor {self.name}: could not determine weekly expiry.")
            return result

        if self.strikes is None or (now - self.last_recalc).total_seconds() >= config.RECALC_INTERVAL_SECONDS:
            self.strikes = market_utils.build_strike_window(index_ltp, self.cfg["strike_interval"])
            self.last_recalc = now
            self._refresh_quotes(client, log_fn)
            result["recalculated"] = True

        self._schedule(client, now, log_fn)
        if self._process(client, now, log_fn):
            result["board_changed"] = True
        try:
            result["cross_changed"] = self._cross_step(client, now, log_fn)
        except Exception as ex:
            result["cross_changed"] = False
            log_fn(f"BEP {self.name} pair cross error: {ex}")
            
        try:
            result["first_updated"] = self._update_first(client, result["recalculated"] or result["board_changed"], log_fn)
        except Exception as ex:
            log_fn(f"BEP {self.name} first cards error: {ex}")
            
        try:
            result["latest_updated"] = self._update_latest(
                client, result["recalculated"] or result["board_changed"], log_fn)
        except Exception as ex:
            log_fn(f"BEP {self.name} latest-support cards error: {ex}")
        return result

    def pop_order_signals(self) -> list:
        out, self.new_orders = self.new_orders, []
        return out

    # ------------------------------------------------------------------ one order per side per day
    def lock_side(self, day, opt, strike, time_str, status):
        """Marks CE or PE as already ordered for `day` (also used to restore the lock from today's orders file)."""
        self.side_lock[(day, opt)] = {"strike": strike, "time": time_str, "status": status}

    def record_order_result(self, sig, status: str):
        """Called by the app after send_order. BLOCKED (paused / daily limit) frees the side again;
        SENT, DRY RUN and FAILED keep it locked for the rest of the day."""
        key = (sig["date"], sig["opt"])
        if status == "blocked":
            self.side_lock.pop(key, None)
        elif key in self.side_lock:
            self.side_lock[key]["status"] = status

    def side_status_text(self, day: str = None) -> str:
        day = day or datetime.now(market_utils.IST).strftime("%Y-%m-%d")
        names = {"dry_run": "DRY RUN", "sent": "SENT", "failed": "FAILED", "queued": "sending"}
        parts = []
        for opt in ("CE", "PE"):
            lk = self.side_lock.get((day, opt))
            if lk:
                parts.append(f"{opt}: ordered {lk['strike']} at {lk['time']} ({names.get(lk['status'], lk['status'])})")
            else:
                parts.append(f"{opt}: open")
        return "09:15 orders today \u2014 " + " \u00b7 ".join(parts)

    def _queue_order(self, opt, strike, level, ltp, now):
        if config.AUTO_ORDER_PAUSED:
            return False
        day = now.strftime("%Y-%m-%d")
        if (day, opt) in self.side_lock:
            return False
        self.lock_side(day, opt, strike, now.strftime("%H:%M:%S"), "queued")
        sig = {"index": self.name, "opt": opt, "strike": strike, "bep": level, "ltp": ltp, "expiry": self.expiry,
               "kind": "opening", "order_type": "strategy_order", "time": now.strftime("%H:%M:%S"), "date": day}
        sig.update(self._opening_sl(level, now))
        self.new_orders.append(sig)
        return True

    def _opening_sl(self, level, now) -> dict:
        """09:15 order SL (PR #41): a CANDLE close below BEP - open_sl_below on open_sl_tf-minute candles, watched
        by the app (with no max SL the order gets no exchange SL; the 09:15 trailing SL (open_trail,
        its own Setup values) moves the exchange SL). Returns {"params", "app_rules"}."""
        cfg = config.AUTO_ORDER.get(self.name, {})
        below = float(cfg.get("open_sl_below", 2) or 0)
        tf = int(cfg.get("open_sl_tf", 5) or 5)
        rule = {"kind": "sl", "type": "CANDLE", "value": round(level - below, 2), "tf": tf, "set_at": "",
                "custom": False}
        app_rules = {"c_sl": json.dumps(rule)}
        if cfg.get("open_trail", True):           # the 09:15 order's own trail (Setup > 09:15 opening order)
            app_rules["c_trail"] = json.dumps({
                "trigger": float(cfg.get("open_trail_trigger", 20)), "gap": float(cfg.get("open_trail_gap", 19)),
                "step": float(cfg.get("open_trail_point") or 1), "set_at": now.strftime("%H:%M:%S"), "high": None,
                "done": ""})
        max_sl = float(cfg.get("open_max_sl", 17) or 0)
        if max_sl > 0:          # PR #47: an exchange SL-M (fill - max_sl) as a backstop (BEP Upstox: positions.py); the trail moves it
            params = {"sl": max_sl, "sl_type": "POINT", "trail": False}
        else:
            params = {"is_sl": False, "trail": False}   # never the Default order trail
        return {"params": params, "app_rules": app_rules}

    # ------------------------------------------------------------------ 09:15 opening order
    def prepare_opening(self, client, index_ltp: float, now, log_fn) -> bool:
        """Between OPENING_PREP_START and 09:15 on a trading day: picks the strike just below and just above
        NIFTY's pre-open price (same two for CE and PE) and looks up their instrument keys and BEPs.
        Re-run every tick until 09:15 so the plan follows the pre-open price. Returns True if the plan changed."""
        h, mnt = (int(x) for x in config.OPENING_PREP_START.split(":"))
        if not (dtime(h, mnt) <= now.time() < SESSION_FIRST_CANDLE) or not market_utils.is_trading_day(now):
            return False
        day = now.strftime("%Y-%m-%d")
        step = self.cfg["strike_interval"]
        below = int(index_ltp // step * step)               # exactly on a strike -> that strike and the next one up
        strikes = [below, below + step]
        if self.plan and self.plan["day"] == day and self.plan["strikes"] == strikes and self.plan["ready"]:
            self.plan["ref"], self.plan["prepared_at"] = index_ltp, now.strftime("%H:%M:%S")
            return False

        expiry = self.expiry or market_utils.current_weekly_expiry(client, self.cfg["underlying_key"])
        if not expiry:
            log_fn(f"BEP {self.name} opening plan: could not determine the weekly expiry.")
            return False
        self.expiry = expiry
        mapping, err = market_utils.option_instrument_keys(client, expiry, self.cfg["underlying_key"])
        if not mapping:
            log_fn(f"BEP {self.name} opening plan: option instrument keys unavailable ({err}).")
            return False
        legs, missing = {}, []
        for strike in strikes:
            for opt in ("CE", "PE"):
                ik = mapping.get(strike, {}).get(opt)
                level, src = bep.get_bep(client, expiry, strike, self.name)
                if ik and level is not None:
                    legs[(strike, opt)] = {"ik": ik, "bep": level, "src": src}
                else:
                    missing.append(f"{strike}{opt}")
        no_csv = not bep.csv_strikes(self.name)
        self.plan = {"day": day, "ref": index_ltp, "strikes": strikes, "legs": legs, "expiry": expiry,
                     "prepared_at": now.strftime("%H:%M:%S"), "ready": bool(legs), "calc_used": no_csv or any(
                         v["src"] != "csv" for v in legs.values())}
        desc = ", ".join(f"{s}{o} BEP {legs[(s, o)]['bep']:.2f}" for s in strikes for o in ("CE", "PE") if (s, o) in legs)
        log_fn(f"BEP {self.name} opening plan ready: pre-open {index_ltp} -> strikes {strikes[0]} / {strikes[1]}: {desc}"
               + (f"; missing: {', '.join(missing)}" if missing else "")
               + ("; WARNING: no BEP CSV uploaded, using calculated BEPs" if no_csv else ""))
        return True

    def opening_tick(self, client, now, log_fn):
        """09:15:00-09:20: one Upstox OHLC call for the prepared strikes. A strike qualifies when its day low
        (= first candle low) touched BEP and LTP is above the open and above BEP, but less than max_above_bep
        points above BEP. At most ONE CE and ONE PE per day (closest to the pre-open price if both qualify).
        No plan prepared before 09:15 -> no opening order today."""
        if not (SESSION_FIRST_CANDLE <= now.time() < dtime(9, 15 + CANDLE_MINUTES)) or not market_utils.is_trading_day(now):
            return
        day = now.strftime("%Y-%m-%d")
        plan = self.plan
        if not plan or plan["day"] != day or not plan["ready"]:
            if self._plan_skip_logged != day:
                self._plan_skip_logged = day
                log_fn(f"BEP {self.name}: no opening plan prepared before 09:15 (app started late or not connected) "
                       f"- no 09:15 order today.")
            return
        if config.AUTO_ORDER_PAUSED:
            return
        open_sides = [opt for opt in ("CE", "PE") if (day, opt) not in self.side_lock]
        if not open_sides:
            return
        legs = {k: v for k, v in plan["legs"].items() if k[1] in open_sides}
        quotes, err = client.fetch_ohlc(sorted({v["ik"] for v in legs.values()}))
        if quotes is None:
            log_fn(f"BEP {self.name} opening check: quote fetch failed: {err}")
            return
        raw = config.AUTO_ORDER.get(self.name, {}).get("max_above_bep", 15)
        max_above = 15.0 if raw is None or raw == "" else float(raw)          # 0 = no limit
        candidates = {opt: [] for opt in open_sides}
        for (strike, opt), leg in legs.items():
            q = quotes.get(leg["ik"]) or quotes.get(leg["ik"].replace("|", ":")) or {}
            ltp, o, low = q.get("ltp"), q.get("open"), q.get("low")
            if ltp is None or o is None:
                continue
            ltp, o = float(ltp), float(o)
            key = (day, strike, opt)
            seen_low = min(self._open_low.get(key, ltp), ltp, float(low) if low is not None else ltp)
            self._open_low[key] = seen_low
            level = leg["bep"]
            above = float(config.AUTO_ORDER.get(self.name, {}).get("open_entry_above", 2) or 0)
            entry = level + above                            # PR #41: enter at BEP + 2 (0 = just above BEP)
            if not (seen_low <= level + support_logic.TOL and ltp > o and (ltp >= entry if above else ltp > level)):
                continue
            if max_above > 0 and ltp - level >= max_above:
                if key not in self._far_logged:
                    self._far_logged.add(key)
                    log_fn(f"BEP {self.name} opening candle: {opt} {strike} skipped - LTP {ltp:.2f} is "
                           f"{ltp - level:.2f} points above BEP {level:.2f} (limit < {max_above:g})")
                continue
            candidates[opt].append((abs(strike - plan["ref"]), strike, level, ltp))

        for opt, cands in candidates.items():
            if not cands:
                continue
            _dist, strike, level, ltp = min(cands)
            if self._queue_order(opt, strike, level, ltp, now):
                others = ", ".join(str(c[1]) for c in sorted(cands) if c[1] != strike)
                cfg = config.AUTO_ORDER.get(self.name, {})
                log_fn(f"BEP {self.name} opening candle: {opt} {strike} crossed BEP {level:.2f} + "
                       f"{float(cfg.get('open_entry_above', 2) or 0):g} (LTP {ltp:.2f}); SL = {cfg.get('open_sl_tf', 5)}-min "
                       f"close below {level - float(cfg.get('open_sl_below', 2) or 0):.2f} (app-watched)"
                       + (f", max SL {float(cfg.get('open_max_sl', 17) or 0):g} pts below the fill (exchange SL-M)"
                          if float(cfg.get('open_max_sl', 17) or 0) > 0 else "")
                       + f" - order signal queued; no more {opt} orders today"
                       + (f" (also qualified, skipped: {others})" if others else ""))

    def plan_text(self, day: str = None) -> str:
        day = day or datetime.now(market_utils.IST).strftime("%Y-%m-%d")
        p = self.plan
        if not p or p["day"] != day:
            return f"Opening plan: not prepared yet (prepares from {config.OPENING_PREP_START} to 09:15)"
        legs = ", ".join(f"{s}{o} {p['legs'][(s, o)]['bep']:.2f}" for s in p["strikes"] for o in ("CE", "PE")
                         if (s, o) in p["legs"])
        warn = " \u26a0 some BEPs calculated (not in the uploaded CSV)" if p.get("calc_used") else ""
        return f"Opening plan ({p['prepared_at']}, pre-open {p['ref']}): BEP {legs}{warn}"

    def _refresh_quotes(self, client, log_fn):
        strikes = sorted(set(self.strikes or []) | self._card_strikes())
        quotes, err = client.fetch_ce_pe_ltp_for_strikes(
            self.cfg["underlying_key"], self.expiry, strikes
        )
        if quotes is None:
            log_fn(f"BEP monitor {self.name}: option chain fetch failed: {err}")
            return
        self.quotes = quotes

    def _nifty_closes(self, client, log_fn) -> dict:
        candles, err = client.fetch_intraday_candles(self.cfg["index_key"], CANDLE_MINUTES)
        if not candles:
            log_fn(f"BEP monitor {self.name}: {self.name} 5-min candles unavailable ({err}); using live window.")
            return {}
        return {c[0][:16]: float(c[4]) for c in candles}

    def _latest_closed(self, now):
        t = now - timedelta(seconds=config.TRACK_FINALIZE_DELAY_SECONDS)
        cur_start = t.replace(minute=t.minute - t.minute % CANDLE_MINUTES, second=0, microsecond=0)
        latest = cur_start - CANDLE
        if latest.date() != now.date():
            return None
        latest = min(latest, latest.replace(hour=SESSION_LAST_CANDLE.hour, minute=SESSION_LAST_CANDLE.minute))
        first = latest.replace(hour=SESSION_FIRST_CANDLE.hour, minute=SESSION_FIRST_CANDLE.minute)
        return latest if latest >= first else None

    def _schedule(self, client, now, log_fn):
        if self.last_bucket is not None and self.last_bucket.date() != now.date():
            self.last_bucket = None
            self.expiry = None
            self._reset_support()
            
        latest = self._latest_closed(now)
        if latest is None:
            return
        first_of_day = latest.replace(hour=SESSION_FIRST_CANDLE.hour, minute=SESSION_FIRST_CANDLE.minute)

        backfill = self.last_bucket is None
        starts, s = [], (first_of_day if backfill else self.last_bucket + CANDLE)
        limit = MAX_BACKFILL_CANDLES if backfill else MAX_CATCHUP_CANDLES
        while s <= latest and len(starts) < limit:
            starts.append(s)
            s += CANDLE
        if not starts:
            return

        closes = self._nifty_closes(client, log_fn)
        
        for s in starts:
            nifty_close = closes.get(s.strftime("%Y-%m-%dT%H:%M"))
            
            window_strikes = market_utils.build_strike_window(nifty_close, self.cfg["strike_interval"]) if nifty_close else list(self.strikes)
            self.candle_windows[s] = set(window_strikes)
            self.candle_atm[s] = window_strikes[len(window_strikes) // 2]
            if nifty_close:
                self.nifty_close[s] = nifty_close
            
            if s == first_of_day:
                self.first_start = s
                
            strikes_to_scan = window_strikes
            if self._scan_all() or (self.scan_all_csv is None and (self.first_found["CE"] is None
                                                                   or self.first_found["PE"] is None)):
                csv_strikes = bep.csv_strikes(self.name)
                if csv_strikes:
                    strikes_to_scan = sorted(set(window_strikes) | csv_strikes)

            self.buckets[s] = {"strikes": strikes_to_scan, "expiry": self.expiry, "done": set()}
            self.last_bucket = s

    def _prefetch(self, client, cache):
        iks = set()
        for b in self.buckets.values():
            mapping, _ = market_utils.option_instrument_keys(client, b["expiry"], self.cfg["underlying_key"])
            mapping = mapping or {}
            for strike in b["strikes"]:
                for opt in ("CE", "PE"):
                    ik = mapping.get(strike, {}).get(opt)
                    if ik and (strike, opt) not in b["done"]:
                        iks.add(ik)
        if not iks:
            return

        def fetch(ik):
            candles, _ = client.fetch_intraday_candles(ik, CANDLE_MINUTES)
            return ik, candles or []

        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
            for ik, candles in ex.map(fetch, sorted(iks)):
                cache[ik] = candles

    def _process(self, client, now, log_fn) -> bool:
        changed = False
        cache = {}
        give_up = timedelta(seconds=config.TRACK_GIVE_UP_SECONDS)
        self._prefetch(client, cache)
        self._step_cache = cache
        
        for start in sorted(self.buckets):
            b = self.buckets[start]
            end = start + CANDLE
            label = start.strftime("%H:%M")
            mapping, _ = market_utils.option_instrument_keys(client, b["expiry"], self.cfg["underlying_key"])
            mapping = mapping or {}
            
            for strike in b["strikes"]:
                for opt in ("CE", "PE"):
                    leg = (strike, opt)
                    if leg in b["done"]:
                        continue
                    ik = mapping.get(strike, {}).get(opt)
                    if not ik:
                        b["done"].add(leg)
                        continue
                    
                    res = self._evaluate_leg(client, strike, opt, ik, start, label, cache, now)
                    if res:
                        b["done"].add(leg)
                        if res == "changed":
                            changed = True
            
            if len(b["done"]) >= 2 * len(b["strikes"]) or now >= end + give_up:
                for strike in b["strikes"]:                 # legs given up on: their sequence is broken
                    for opt in ("CE", "PE"):
                        if (strike, opt) not in b["done"] and (strike, opt) in self.leg_states:
                            self._reset_leg_state(strike, opt)
                if start in self.buckets:
                    del self.buckets[start]
        return changed

    def _evaluate_leg(self, client, strike, opt, ik, start, label, cache, now):
        start_key = start.strftime("%Y-%m-%dT%H:%M")
        candles = cache.get(ik, [])
        candle = next((c for c in candles if c[0][:16] == start_key), None)

        if candle is None:
            if any(c[0][:16] > start_key for c in candles):
                self._reset_leg_state(strike, opt)       # a missing candle breaks the flush/validate sequence
                return True
            return False

        level, _src = self.get_bep(client, strike)
        if level is None:
            self._reset_leg_state(strike, opt)
            return True

        o, h, l, c = float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4])

        window = self.candle_windows.get(start)
        if window is None:
            window = set(self.strikes or [])

        if (not window or strike in window) and l <= level + support_logic.TOL and c > level and c > o:
            cur, atm = self.first_found[opt], self.candle_atm.get(start)
            if cur is None or (self.first_found_start[opt] == start and atm is not None
                               and abs(strike - atm) < abs(cur - atm)):
                self.first_found[opt] = strike          # first support inside the ATM window; tie -> nearest ATM
                self.first_found_start[opt] = start
                self.first_view = {}

        changed = support_logic.check_failures(self.support[opt], strike, start, c, label)
        if not self._scan_all() and window and strike not in window:
            return "changed" if changed else True

        if (strike, opt) not in self.leg_states:
            self._reset_leg_state(strike, opt)
        st = self.leg_states[(strike, opt)]

        if support_logic.process_candle(
                st, self.support[opt], strike, opt, level, o, l, c, start, label, self.first_start,
                lambda s, p, a, lb, lv, cl, fl, rule: self._add_support(s, p, a, lb, lv, cl, fl, rule, now)):
            changed = True
        return "changed" if changed else True

    def _add_support(self, strike, opt, start, label, level, close, fail_low, rule, now):
        self._support_seq += 1
        # alert (and a \u2b50 row) for the first support of a strike, and again once every earlier row on it failed
        is_first = not any(r["strike"] == strike and r["state"] == "green" for r in self.support[opt])
        row = {
            "id": self._support_seq, "strike": strike, "opt": opt, "bep": level,
            "time": label, "start": start, "fail_time": None, "state": "green", "ltp": close,
            "resolved": False, "active_flush_low": fail_low, "rule": rule, "close": close, "star": is_first,
        }
        self.support[opt].append(row)
        if is_first:
            self._queue_support_alert(row, start, now)

    def _queue_support_alert(self, row, start, now):
        if self.replay:
            return
        try:
            key = (start.strftime("%Y-%m-%d"), row["strike"], row["opt"], row["time"])
            age = now - (start + CANDLE)
            if not (timedelta(seconds=-30) <= age <= NOTIFY_MAX_AGE):
                return
            if not market_utils.is_market_open(now):
                return
            if key not in self._notified:
                self._notified.add(key)
                window = self.candle_windows.get(start) or set(self.strikes or [])
                self.new_support.append({
                    "index": self.name, "strike": row["strike"], "opt": row["opt"], "bep": row["bep"], "close": row["ltp"],
                    "time": row["time"], "rule": row["rule"], "day": start.strftime("%Y-%m-%d"),
                    "in_window": row["strike"] in window,
                })
        except Exception:
            pass

    # ------------------------------------------------------------------ BEP Pair cross (pair_cross.py)
    def _cross_step(self, client, now, log_fn) -> bool:
        """Runs the pair-cross check on every closed candle not yet processed (in time order). Candles come
        from this step's board cache; a leg missing from it is fetched once. Returns True if the table changed."""
        latest = self._latest_closed(now)
        if latest is None:
            return False
        first = latest.replace(hour=SESSION_FIRST_CANDLE.hour, minute=SESSION_FIRST_CANDLE.minute)
        s = first if self.cross.last_start is None else self.cross.last_start + CANDLE
        if s > latest:
            return False
        mapping, _ = market_utils.option_instrument_keys(client, self.expiry, self.cfg["underlying_key"])
        mapping = mapping or {}
        cache = self._step_cache
        give_up = timedelta(seconds=config.TRACK_GIVE_UP_SECONDS)
        changed = False
        while s <= latest:
            if s not in self.candle_windows:            # NIFTY close / window of this candle not scheduled yet
                break
            atm = self.candle_atm.get(s)
            legs = self.cross.needed_legs(atm)
            iks = {leg: mapping.get(leg[0], {}).get(leg[1]) for leg in legs}
            missing = [ik for ik in iks.values() if ik and ik not in cache]
            if missing:
                def fetch(ik):
                    candles, _ = client.fetch_intraday_candles(ik, CANDLE_MINUTES)
                    return ik, candles or []
                with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
                    for ik, candles in ex.map(fetch, missing):
                        cache[ik] = candles
            key = s.strftime("%Y-%m-%dT%H:%M")
            waiting = any(ik and not any(c[0][:16] >= key for c in cache.get(ik, [])) for ik in iks.values())
            if waiting and now < s + CANDLE + give_up:
                break                                   # a needed candle has not arrived yet: try next scan

            def candle_of(strike, opt, start, mapping=mapping, cache=cache):
                ik = mapping.get(strike, {}).get(opt)
                k = start.strftime("%Y-%m-%dT%H:%M")
                c = next((c for c in cache.get(ik, []) if c[0][:16] == k), None) if ik else None
                if c is None and ik and ik not in cache:
                    candles, _ = client.fetch_intraday_candles(ik, CANDLE_MINUTES)
                    cache[ik] = candles or []
                    c = next((c for c in cache[ik] if c[0][:16] == k), None)
                return (float(c[1]), float(c[2]), float(c[3]), float(c[4])) if c else None

            def bep_of(strike):
                return self.get_bep(client, strike)[0]

            before = [(r["id"], r["status"], r["n"]) for r in self.cross.rows]
            new = self.cross.process(s, atm, self.nifty_close.get(s), candle_of, bep_of)
            if new or before != [(r["id"], r["status"], r["n"]) for r in self.cross.rows]:
                changed = True
            for r in new:
                age = now - (s + CANDLE)
                if not self.replay and timedelta(seconds=-30) <= age <= NOTIFY_MAX_AGE \
                        and market_utils.is_market_open(now):
                    self.new_cross.append(dict(r))
            s += CANDLE
        return changed

    def explain_cross(self, ce_strike: int, pe_strike: int, hhmm: str) -> list:
        """BEP Pair cross "Why no row?": checks one CE / PE pair on one candle (start time HH:MM) of the day
        this monitor shows (today, or the replayed day). Returns [(ok, text)] (see PairCross.explain)."""
        client = self._client
        if client is None or not self.candle_windows:
            return [(False, "No candles scanned yet (not connected, market not open yet, or no replay).")]
        try:
            h, m = (int(x) for x in hhmm.strip().split(":"))
        except (ValueError, AttributeError):
            return [(False, "Enter the candle time as HH:MM, e.g. 09:40.")]
        if m % CANDLE_MINUTES or not (SESSION_FIRST_CANDLE <= dtime(h, m) <= SESSION_LAST_CANDLE):
            return [(False, f"Use a 5-min candle start between 09:15 and 15:25 (e.g. 09:40), not {hhmm}.")]
        start = min(self.candle_windows).replace(hour=h, minute=m)
        if start not in self.candle_windows:
            return [(False, f"The {hhmm} candle has not been scanned yet.")]
        mapping, _ = market_utils.option_instrument_keys(client, self.expiry, self.cfg["underlying_key"])
        mapping, cache = mapping or {}, {}

        def candle_of(strike, opt, st):
            ik = mapping.get(strike, {}).get(opt)
            if not ik:
                return None
            if ik not in cache:
                cache[ik] = self._step_cache.get(ik) or client.fetch_intraday_candles(ik, CANDLE_MINUTES)[0] or []
            k = st.strftime("%Y-%m-%dT%H:%M")
            c = next((c for c in cache[ik] if c[0][:16] == k), None)
            return (float(c[1]), float(c[2]), float(c[3]), float(c[4])) if c else None

        return self.cross.explain(int(ce_strike), int(pe_strike), start, self.candle_atm.get(start),
                                  self.nifty_close.get(start), candle_of, lambda k: self.get_bep(client, k)[0])

    def pop_cross_signals(self) -> list:
        out, self.new_cross = self.new_cross, []
        return out

    def pop_support_alerts(self) -> list:
        out, self.new_support = self.new_support, []
        return out

    def board_view(self) -> dict:
        out = {}
        for opt in ("CE", "PE"):
            evs = self.support[opt]
            evs = sorted(
                evs, 
                key=lambda e: (
                    -datetime.strptime(e["time"], "%H:%M").time().hour * 60 - datetime.strptime(e["time"], "%H:%M").time().minute,
                    e["strike"]
                ) if e.get("time") else 0
            )
            first_id = {}
            for e in self.support[opt]:
                cur = first_id.get(e["strike"])
                if cur is None or (e["start"], e["id"]) < cur[0]:
                    first_id[e["strike"]] = ((e["start"], e["id"]), e["id"])
            out[opt] = [{
                "id": str(e["id"]), "strike": str(e["strike"]),
                "first": e.get("star", first_id[e["strike"]][1] == e["id"]), "ltp": f"{e['ltp']:.2f}" if e.get("ltp") else "--",
                "bep": f"{e['bep']:.2f}",
                "time": f"{e['time']} → {e['fail_time']}" if e['state'] == 'red' and e.get('fail_time') else e['time'],
                "kind": e["state"],
            } for e in evs]
        return out

    def export_rows(self) -> list:
        first = {}
        for opt in ("CE", "PE"):
            for e in self.support[opt]:
                k = (opt, e["strike"])
                if k not in first or (e["start"], e["id"]) < first[k]:
                    first[k] = (e["start"], e["id"])
        rows = []
        for opt in ("CE", "PE"):
            for e in self.support[opt]:
                failed = e["state"] == "red"
                rows.append({
                    "_sort": (e["start"], 0 if opt == "CE" else 1, e["strike"]),
                    "index": self.name, "opt": opt, "strike": e["strike"],
                    "first": "Yes" if e.get("star", first[(opt, e["strike"])] == (e["start"], e["id"])) else "",
                    "bep": round(e["bep"], 2), "time": e["time"],
                    "setup": "Failed breakdown" if e.get("rule") == 1 else "Touch and go",
                    "fail_level": round(e["active_flush_low"], 2) if e.get("active_flush_low") is not None else "",
                    "status": "Failed" if failed else "Held",
                    "fail_time": e.get("fail_time") or "" if failed else "",
                    "fail_close": round(e["fail_close"], 2) if failed and e.get("fail_close") is not None else "",
                    "ltp": round(e["ltp"], 2) if e.get("ltp") else "",
                })
        rows.sort(key=lambda r: r["_sort"])
        for r in rows:
            del r["_sort"]
        return rows

    def _card_strikes(self) -> set:
        s = set()
        for found in self.first_found.values():
            if found:
                s.add(found)
        s |= {v["strike"] for v in self.latest_view.values() if v}
        return s

    def _update_first(self, client, refresh, log_fn) -> bool:
        if not refresh and self.first_view:
            return False
            
        mapping, _ = market_utils.option_instrument_keys(client, self.expiry, self.cfg["underlying_key"])
        mapping = mapping or {}
        view = {}
        for opt in ("CE", "PE"):
            found = self.first_found[opt]
            if not found:
                view[opt] = None
                continue

            strike = found
            level, _ = self.get_bep(client, strike)
            ltp = (self.quotes.get(strike) or {}).get(opt)
            if ltp is None:
                ik = mapping.get(strike, {}).get(opt)
                if ik:
                    ltp_map, _ = client.fetch_ltp([ik])
                    ltp = (ltp_map or {}).get(ik)
            view[opt] = {"strike": strike, "bep": level, "ltp": ltp}
        
        self.first_view = view
        return True

    def _latest_event(self, opt):
        evs = self.support[opt]
        if not evs:
            return None
        newest_time_str = max(e["time"] for e in evs)
        newest_events = [e for e in evs if e["time"] == newest_time_str]
        return min(newest_events, key=lambda e: e["strike"])

    def _update_latest(self, client, refresh, log_fn) -> bool:
        events = {opt: self._latest_event(opt) for opt in ("CE", "PE")}
        sig = {opt: (e["id"], e["state"]) if e else None for opt, e in events.items()}
        if sig == self._latest_sig and not refresh and self.latest_view:
            return False
        mapping, _ = market_utils.option_instrument_keys(client, self.expiry, self.cfg["underlying_key"])
        mapping = mapping or {}
        view = {}
        for opt, e in events.items():
            if e is None:
                view[opt] = None
                continue
            ltp = (self.quotes.get(e["strike"]) or {}).get(opt)
            if ltp is None:
                ik = mapping.get(e["strike"], {}).get(opt)
                if ik:
                    ltp_map, _ = client.fetch_ltp([ik])
                    ltp = (ltp_map or {}).get(ik)
            view[opt] = {"strike": e["strike"], "bep": e["bep"], "ltp": ltp, "state": e["state"]}
        self.latest_view = view
        self._latest_sig = sig
        return True