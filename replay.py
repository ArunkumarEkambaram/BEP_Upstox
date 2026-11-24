"""
replay.py - "Replay a past day" for BEP Support (decision 25, Part E).

Rebuilds the Support board and the BEP Pair cross table of ONE past trading day from Upstox historical 5-min
candles, in a separate BepMonitor (replay=True): no alerts, no star rows, no Pair rows, no orders. The live
monitor is untouched and keeps running.

Limits:
  - Only days whose option contracts are still listed: the current weekly expiry's contracts (days since the
    last expiry). Expired contracts have no instrument keys here.
  - BEP: the uploaded CSV (it must be THAT day's file), or calculated = (CE close + PE close) / 2 of the trading
    day before the replay date.
"""
from datetime import datetime

import bep
import config
import market_utils
from bep_monitor import BepMonitor

IST = market_utils.IST


class ReplayClient:
    """Wraps the real Upstox client: 'today's intraday candles' become the replay date's historical candles,
    and live quotes are empty. Everything else goes to the real client."""

    def __init__(self, client, date_str: str):
        self._client = client
        self.date_str = date_str
        self._candles = {}

    def __getattr__(self, item):
        return getattr(self._client, item)

    def fetch_intraday_candles(self, instrument_key: str, interval_minutes: int = 5):
        key = (instrument_key, interval_minutes)
        if key not in self._candles:
            candles, err = self._client.fetch_candles(instrument_key, self.date_str, interval_minutes)
            if candles is None:
                return None, err
            self._candles[key] = candles
        return self._candles[key], None

    def fetch_ce_pe_ltp_for_strikes(self, *args, **kwargs):
        return {}, None

    def fetch_ltp(self, instrument_keys):
        return {}, None


def build(client, name: str, date_str: str, bep_source: str, expiry: str):
    """Runs the replay. Returns (monitor, info_text, None) or (None, None, error)."""
    cfg = config.INDEXES[name]
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None, None, "Pick a date."
    if day.date() >= datetime.now(IST).date():
        return None, None, "Pick a PAST trading day (today runs live)."
    if not market_utils.is_trading_day(IST.localize(day)):
        return None, None, f"{day:%a %d %b %Y} is not a trading day."
    if not expiry:
        expiry = market_utils.current_weekly_expiry(client, cfg["underlying_key"])
    if not expiry:
        return None, None, "Could not read the weekly expiry from Upstox."
    if expiry < date_str:
        return None, None, f"The contracts of {date_str} have expired (current expiry {expiry}): replay works only " \
                           "for days since the last expiry."
    rc = ReplayClient(client, date_str)
    index_candles, err = rc.fetch_intraday_candles(cfg["index_key"], 5)
    if not index_candles:
        return None, None, f"No {name} candles for {date_str} ({err or 'holiday?'})."
    mapping, err = market_utils.option_instrument_keys(client, expiry, cfg["underlying_key"])
    if not mapping:
        return None, None, f"Option instrument keys unavailable ({err})."

    csv_store = bep.csv_strikes(name)
    calc_cache, missing = {}, set()

    def bep_fn(_client, strike):
        strike = int(strike)
        if bep_source == "csv":
            if strike in csv_store:
                return bep._csv_store(name)[strike], "csv"
            missing.add(strike)
            return None, None
        if strike not in calc_cache:
            legs = mapping.get(strike, {})
            closes = [client.fetch_close_before(legs[o], date_str)[0] if legs.get(o) else None for o in ("CE", "PE")]
            calc_cache[strike] = round((closes[0] + closes[1]) / 2, 2) if None not in closes else None
        if calc_cache[strike] is None:
            missing.add(strike)
        return calc_cache[strike], "calc" if calc_cache[strike] is not None else None

    if bep_source == "csv" and not csv_store:
        return None, None, "No BEP CSV uploaded: upload that day's BEP file on Setup, or choose 'Calculated'."
    mon = BepMonitor(name)
    mon.replay = True
    mon.bep_fn = bep_fn
    if bep_source == "calc":                       # calculated: scan only the per-candle ATM windows
        mon.scan_all_csv = False
    now = IST.localize(day.replace(hour=15, minute=45))
    res = mon.step(rc, float(index_candles[-1][4]), expiry, now)
    mon.replay_logs = res.get("logs", [])
    n_board = len(mon.support["CE"]) + len(mon.support["PE"])
    info = (f"REPLAY {day:%a %d %b %Y} · BEP {'uploaded CSV' if bep_source == 'csv' else 'calculated from the previous day closes'}"
            f" · {n_board} support rows · {len(mon.cross.rows)} pair-cross signals · no alerts, no orders")
    if missing:
        info += f" · no BEP for {len(missing)} strike(s): {', '.join(str(k) for k in sorted(missing)[:8])}" \
                + ("…" if len(missing) > 8 else "")
    return mon, info, None
