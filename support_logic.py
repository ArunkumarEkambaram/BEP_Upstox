"""
support_logic.py - the BEP Support rules for ONE option leg, shared by the live monitor
(bep_monitor.py) and the backtest (backtest_support.py) so the two can never drift apart.

Per 5-minute candle of one leg (one strike, CE or PE):

  TRIGGER  green candle (close > open), low <= BEP + 0.05, close > BEP. The open can be anywhere.
           Every qualifying candle becomes its own green row.
  FAIL     a LATER candle whose close is below the row's fail level turns the row red, but only
           within config.SUPPORT_FAIL_WINDOW_CANDLES candles after the trigger candle (default 3 =
           15 minutes). Once that window has passed the row stays green for good:
             rule 1 (a failed-breakdown setup stood behind the trigger):
                 fail level = the low of the flush candle
             rule 2 (no setup):
                 fail level = the low of the trigger candle itself
  SETUP    flush: a candle closes below BEP.
           validate: the next candle opens at or below BEP and its low does not break the flush
           candle's low (if the flush candle is the day's first candle, 09:15, the low check is
           waived). The trigger candle may itself be the validate candle.
           A validated setup is broken (back to idle) when a later candle's low goes below the
           flush low, and it expires config.SUPPORT_SETUP_EXPIRY_CANDLES candles after the
           validate candle (default 3). A trigger candle whose own low breaks the flush low counts
           as "no setup" (rule 2).
           A candle that closes below BEP while a flush/setup is reset starts a NEW flush.
"""
from datetime import timedelta

import config

TOL = 0.05
CANDLE_MINUTES = 5


def _within_fail_window(row_start, start) -> bool:
    """True if candle `start` is inside the failure window of a row triggered at `row_start`."""
    n = getattr(config, "SUPPORT_FAIL_WINDOW_CANDLES", 0)
    if not n:                       # 0 / None = no limit (old behaviour)
        return True
    try:
        return start <= row_start + timedelta(minutes=CANDLE_MINUTES * n)
    except TypeError:               # start times are not datetimes: cannot limit, keep old behaviour
        return True


def _within_setup_window(valid_start, start) -> bool:
    """True if candle `start` may still use a setup validated by the candle at `valid_start`."""
    n = getattr(config, "SUPPORT_SETUP_EXPIRY_CANDLES", 0)
    if not n or valid_start is None:     # 0 / None = no expiry
        return True
    try:
        return start <= valid_start + timedelta(minutes=CANDLE_MINUTES * n)
    except TypeError:
        return True


def new_leg_state() -> dict:
    return {"state": "idle", "flush_low": None, "flush_start": None, "valid_start": None,
            "active_row_id": None}


def _reset(st, start, l, c, level):
    """Drops the current flush/setup. If this candle itself closed below BEP it starts a new flush."""
    st.update(new_leg_state())
    if c < level:
        st.update(state="flushing", flush_low=l, flush_start=start)


def check_failures(rows: list, strike, start, close, label) -> bool:
    """Turns every green, unresolved row of this strike red if a LATER candle, inside the failure
    window, closes below its fail level. Returns True if any row changed."""
    changed = False
    for row in rows:
        if row["strike"] == strike and row["state"] == "green" and not row["resolved"] \
                and row["start"] < start and _within_fail_window(row["start"], start) \
                and close < row["active_flush_low"]:
            row["state"] = "red"
            row["fail_time"] = label
            row["fail_close"] = close
            row["resolved"] = True
            changed = True
    return changed


def process_candle(st, rows, strike, opt, level, o, l, c, start, label, first_start, add_row) -> bool:
    """Trigger check plus the flush/validate state machine for one candle of one leg.
    `st` is the leg's state dict (mutated in place). `add_row(strike, opt, start, label, level,
    close, fail_low, rule)` creates and stores a new green row. Returns True if a row was added."""
    flush_is_first = st.get("flush_start") is not None and st.get("flush_start") == first_start

    # Is a valid setup standing behind this candle? (flush done and validated, not broken, not expired)
    setup_low = None
    if st["state"] == "waiting":
        if l >= st["flush_low"] and _within_setup_window(st.get("valid_start"), start):
            setup_low = st["flush_low"]
    elif st["state"] == "flushing" and o <= level + TOL and (l >= st["flush_low"] or flush_is_first):
        setup_low = st["flush_low"]              # this candle is also the validate candle

    if c > o and c > level and l <= level + TOL:
        added = False
        if not any(r["strike"] == strike and r["time"] == label for r in rows):
            add_row(strike, opt, start, label, level, c,
                    setup_low if setup_low is not None else l,
                    1 if setup_low is not None else 2)
            added = True
        st.update(new_leg_state())
        return added

    if st["state"] == "flushing":
        if o <= level + TOL and (l >= st["flush_low"] or flush_is_first):
            st["state"] = "waiting"
            st["valid_start"] = start
        else:
            _reset(st, start, l, c, level)
    elif st["state"] == "waiting":
        if l < st["flush_low"] or not _within_setup_window(st.get("valid_start"), start):
            _reset(st, start, l, c, level)
    elif st["state"] == "idle":
        if c < level:
            st.update(state="flushing", flush_low=l, flush_start=start)
    return False
