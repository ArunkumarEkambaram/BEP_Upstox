"""
gift_nifty.py — GIFT Nifty LTP for the header (user, 26 Nov 2026; the same file in BEP-AlgoDelta and BEP_Upstox).

Upstox lists GIFT Nifty in its Global Instruments file (segment GLOBAL_INDEX, effective 11 May 2026). The key is
read from that file once a day (no token needed), else the documented example `GLOBAL_INDEX|SGX NIFTY`. The quote
uses the same full-market-quote call as NIFTY (LTP + net change), every REFRESH_SECONDS. Upstox itself refreshes
GIFT Nifty only about every 120 s, so a faster poll would not show more. Header only: no trading logic reads it.
"""
import gzip
import io
import json
import time

import requests

GLOBAL_FILE_URL = "https://assets.upstox.com/market-quote/instruments/exchange/global.json.gz"
DEFAULT_KEY = "GLOBAL_INDEX|SGX NIFTY"
REFRESH_SECONDS = 30
STALE_SECONDS = 300          # no change for 5 min -> the chip greys out (holiday, outside GIFT hours, feed stuck)


def find_key(rows) -> str | None:
    """The GIFT Nifty instrument_key in the Global Instruments list (None if not found)."""
    best = None
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        key = str(r.get("instrument_key") or "")
        seg = str(r.get("segment") or key.split("|")[0]).upper()
        text = " ".join(str(r.get(f) or "") for f in ("trading_symbol", "name", "short_name")).upper()
        if seg != "GLOBAL_INDEX" or not key:
            continue
        if "GIFT" in text and "NIFTY" in text:
            if str(r.get("trading_symbol") or "").upper().strip() == "GIFT NIFTY":
                return key
            best = best or key
        elif key.upper() == DEFAULT_KEY:
            best = best or key
    return best


def load_key(timeout=20) -> tuple[str, str | None]:
    """(instrument_key, error or None). Downloads Upstox's global.json.gz; falls back to DEFAULT_KEY."""
    try:
        resp = requests.get(GLOBAL_FILE_URL, timeout=timeout)
        if resp.status_code != 200:
            return DEFAULT_KEY, f"global instruments file: HTTP {resp.status_code}"
        rows = json.load(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(resp.content)), encoding="utf-8"))
        key = find_key(rows)
        return (key, None) if key else (DEFAULT_KEY, "GIFT NIFTY not found in the global instruments file")
    except Exception as e:                                   # network, gzip or JSON error
        return DEFAULT_KEY, f"global instruments file: {e}"


class GiftNifty:
    """Holds the header's GIFT Nifty value. `poll(client)` is cheap to call every scan: it calls Upstox at most
    once per REFRESH_SECONDS. All times are epoch seconds (`clock`, injectable for tests)."""

    def __init__(self, clock=time.time, key_loader=load_key):
        self.clock = clock
        self.key_loader = key_loader
        self.key = None
        self.key_day = None
        self.ltp = None
        self.net_change = None
        self.checked_at = None       # last successful call
        self.changed_at = None       # last time the LTP moved
        self.error = None
        self._next = 0.0

    def due(self) -> bool:
        return self.clock() >= self._next

    def poll(self, client, day: str, log=None):
        """One refresh if due. `day` (YYYY-MM-DD) re-reads the key once a day. Logs each new error once."""
        now = self.clock()
        if now < self._next:
            return False
        self._next = now + REFRESH_SECONDS
        if self.key is None or self.key_day != day:
            key, err = self.key_loader()
            self.key, self.key_day = key, day
            if log:
                log(f"GIFT NIFTY: instrument key {key}" + (f" (default; {err})" if err else ""))
        quotes, err = client.fetch_quote([self.key])
        q = None
        if quotes:
            q = quotes.get(self.key)
            if q is None:                                    # the response may key it differently (e.g. ':' form)
                want = self.key.split("|")[-1].upper()
                q = next((v for k, v in quotes.items() if str(k).upper().endswith(want)), None)
                if q is None and len(quotes) == 1:
                    q = next(iter(quotes.values()))
        if q is None:
            ltp_map, err2 = client.fetch_ltp([self.key])
            if ltp_map:
                q = {"ltp": ltp_map.get(self.key, next(iter(ltp_map.values()))), "net_change": None}
            else:
                err = err or err2 or "no data for " + self.key
        if q is None or q.get("ltp") is None:
            self.ltp = self.net_change = None
            msg = str(err or "no data")
            if msg != self.error and log:
                log(f"GIFT NIFTY unavailable ({msg}); the header shows GIFT NIFTY --")
            self.error = msg
            return True
        ltp = float(q["ltp"])
        if self.ltp is None or abs(ltp - self.ltp) > 1e-9:
            self.changed_at = now
        self.ltp, self.net_change, self.checked_at = ltp, q.get("net_change"), now
        if self.error and log:
            log("GIFT NIFTY available again")
        self.error = None
        return True

    def change(self):
        """(points, percent) vs the previous close, or None."""
        try:
            ltp, chg = float(self.ltp), float(self.net_change)
        except (TypeError, ValueError):
            return None
        prev = ltp - chg
        return (round(chg, 2), round(chg / prev * 100, 2)) if prev else None

    def gap(self, nifty_ltp):
        """GIFT Nifty − NIFTY in points, or None."""
        try:
            return round(float(self.ltp) - float(nifty_ltp), 2)
        except (TypeError, ValueError):
            return None

    def stale(self) -> bool:
        return self.ltp is None or self.changed_at is None or self.clock() - self.changed_at > STALE_SECONDS

    def tooltip(self) -> str:
        fmt = lambda t: time.strftime("%H:%M:%S", time.localtime(t)) if t else "--"
        base = "GIFT Nifty (NSE IX) from Upstox, which updates it about every 120 s. "
        if self.ltp is None:
            return base + (f"Unavailable: {self.error}" if self.error else "Not loaded yet.")
        return (base + f"Last change {fmt(self.changed_at)}, checked {fmt(self.checked_at)}."
                + (" No change for over 5 min (holiday / outside GIFT hours?)." if self.stale() else ""))
