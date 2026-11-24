"""
config.py — central configuration for the BEP Support monitor.
"""

# App identity (BEP Upstox, PR 1). It runs next to the BEP AlgoDelta app (port 8083) on its own port; each app
# keeps its own settings.json, upstox_token.json, orders_*.csv and common_watch_*.json in its own folder.
APP_NAME = "BEP Upstox"
APP_PORT = 8084

# Nifty index instrument key (spot LTP)
NIFTY_INDEX_KEY = "NSE_INDEX|Nifty 50"

# Underlying key used for option-chain / option-expiry lookups
NIFTY_OPTION_UNDERLYING_KEY = "NSE_INDEX|Nifty 50"

# Indices the BEP Support monitor can watch (NIFTY only). To bring SENSEX back, add:
#   "SENSEX": {"index_key": "BSE_INDEX|SENSEX", "underlying_key": "BSE_INDEX|SENSEX",
#              "strike_interval": 100, "enabled": False, "lot_size": 20},
INDEXES = {
    "NIFTY":  {"index_key": "NSE_INDEX|Nifty 50", "underlying_key": "NSE_INDEX|Nifty 50",
               "strike_interval": 50,  "enabled": True, "lot_size": 65},
}

STRIKE_INTERVAL = 50        
STRIKES_EACH_SIDE = 2       # nearest strike +/- 2 -> 5 strikes total

# Upstox trade book refresh for the AlgoDelta orders table (Entry / Exit of SENT orders).
TRADES_REFRESH_SECONDS = 30
OPTION_LTP_SECONDS = 2                 # live LTP on the Order Management cards (one batched call)

# Manual Entry "Limit" mode (AlgoDelta limit_order, SL / Target / trailing watched by the app; limit_orders.py).
LIMIT_POLL_SECONDS = 5                 # Upstox order book read while a live limit order is open / being cancelled
LIMIT_CANCEL_CONFIRM_SECONDS = 30      # cancel_order not seen in the order book by then -> CANCEL FAILED (Retry)
LIMIT_SANITY_PCT = 20                  # ask first when the limit price is this % or more away from the LTP
STOP_LIMIT_GAP = 1.0              # Manual Stop-limit: default limit price = trigger + this (editable)

# 09:15 opening order. From PREP_START to 09:15 the app reads NIFTY's pre-open price, picks the strike just
# below and just above it (same two for CE and PE) and looks up their BEPs. From 09:15:00 to 09:20 it checks
# only those strikes every OPENING_TICK_SECONDS with one Upstox call. Not prepared before 09:15 = no order today.
OPENING_PREP_START = "09:08"
OPENING_TICK_SECONDS = 1

# Live scan interval (seconds).
SCAN_INTERVAL_SECONDS = 10
RECALC_INTERVAL_SECONDS = 60      

# Candle timing used by the BEP monitor
TRACK_FINALIZE_DELAY_SECONDS = 10   
TRACK_GIVE_UP_SECONDS = 180         

# Upstox
UPSTOX_ACCESS_TOKEN = ""   # paste a pre-generated access token

# Broker orders (BEP Upstox PR 2, brokers/). Mode "dry" (nothing sent) / "sandbox" (api-sandbox.upstox.com, the
# 30-day sandbox token) / "live" (needs the static IP registered in the user's Upstox app). The mode is NOT saved:
# the app always starts in "dry". Until PR 3 only the Setup "Test order" panel uses it; the engine's orders still
# go through the inherited AlgoDelta code (order_bridge.py).
BROKER_MODE = "dry"
BROKER = {
    "broker": "upstox",          # the only adapter for now (brokers.BROKERS)
    "market_protection": -1,     # market / SL-M orders: -1 = automatic, 1-25 = percent (saved)
    "algo_name": "",             # X-Algo-Name header, empty until Upstox gives one (saved)
}

# Market hours (IST).
MARKET_OPEN = "09:15"
MARKET_CLOSE = "15:40"
MARKET_HOLIDAYS = []

# Alert pause switch. While True, BEP Support rows are still found and tabled, but NO toast or 
# browser notification is sent.
ALERTS_PAUSED = False

# BEP Support boards: True = check EVERY strike in the uploaded BEP CSV on every candle;
# False = only strikes inside that candle's ATM window.
SUPPORT_SCAN_ALL_CSV_STRIKES = True

# BEP Support failure window: 3 = 15 minutes.
SUPPORT_FAIL_WINDOW_CANDLES = 3

# BEP Support "Failed breakdown" setup: a validated setup can be used by a trigger only within this
# many candles after the validate candle (3 = 15 minutes). 0 = no expiry.
SUPPORT_SETUP_EXPIRY_CANDLES = 3

# BEP Pair cross (BEP Support > BEP Pair cross, pair_cross.py): a CE and a PE near ATM split around their BEPs.
BEP_CROSS_ITM_STRIKES = 2        # the buy leg (closing above BEP) may be ATM or up to this many strikes ITM
BEP_CROSS_TOUCH_TOL = 1.0         # a leg "touched" its BEP when low <= BEP + this and high >= BEP - this
BEP_CROSS_TOUCH_CANDLES = 3       # the touch must be on the signal candle or the 2 candles before it
BEP_CROSS_WATCH_CANDLES = 6       # watched for 6 candles (30 min) after the signal: HELD or FAILED
BEP_CROSS_ADD_PAIR = True         # a live signal also adds a TRIGGERED Pair row to Order Management (buffer 0)

# ---------------------------------------------------------------- ALGO DELTA ORDERS
AUTO_ORDER_DRY_RUN = True         # "Dry run" checkbox. True = log the JSON only, send nothing.
AUTO_ORDER_PAUSED = False         # "Pause algo trade" button.
AUTO_ORDER_DAILY_LIMIT = 5       # "Max orders/day" box: hard cap on LIVE orders per day.

# Per-index AlgoDelta settings. 
# "tgt_type", "sl_type" and "trail_type" can be "POINT" or "LTP".
# The UI controls always decide exit_on_opposite, is_trail_set and the trail_* values
# (order_bridge.build_payload writes them over whatever the JSON template holds).
import copy as _copy

_STRATEGY_ORDER = {
    "type": "strategy_order",
    "exit_on_opposite": "{exit_on_opposite}",
    "is_tgt": True,
    "is_sl": True,
    "is_trail_set": "{is_trail}",
    "position_size": "{{strategy.position_size}}",
    "transaction_type": "BUY",
    "script_type": "{opt}",
    "is_rollover": False,
    "option_selection": "SYMBOL_BASED",
    "script": "{symbol}",
    "product": "INTRADAY",
    "quantity": "{qty}",
    "signal_type": "{signal}",
    "trail_type": "{trail_type}",
    "trail_trigger": "{trail_trigger}",
    "trail_gap": "{trail_gap}",
    "trail_point": "{trail_point}",
    "tgt_type": "{tgt_type}",
    "tgt_value": "{tgt}",
    "sl_type": "{sl_type}",
    "sl_value": "{sl}",
}

def _default_auto_order() -> dict:
    return {
        "webhook_url": "", "confirmed": False,
        "tgt": 50, "sl": 12, "tgt_type": "POINT", "sl_type": "POINT", "qty": 1,
        "exit_on_opposite": False,
        "max_above_bep": 15,          # 09:15 order is skipped when LTP - BEP >= this many points
        # 09:15 order (PR #41): enters at LTP >= BEP + open_entry_above; its SL is a CANDLE close below
        # BEP - open_sl_below on open_sl_tf-minute candles, watched by the app (AlgoDelta gets is_sl false)
        "open_entry_above": 2, "open_sl_below": 2, "open_sl_tf": 5,
        # PR #47: a hard max SL held by AlgoDelta (POINT, from its fill) on top of the app CANDLE SL; 0 = none
        "open_max_sl": 17,
        # ⚡ on a BEP card (⭐ / BEP cross Pair / Manual Entry): "Check before placing" when the LTP (or the limit
        # price) is more than this many points above the strike's BEP (PR #43; 0 = off)
        "warn_above_bep": 10,
        # default targets per Order Management type (the 09:15 opening order uses "tgt" / "tgt_type");
        # each row can override value and type until it is ordered
        "tgt_same": 50, "tgt_pair": 50, "tgt_star": 50, "tgt_manual": 50,
        "trail": False, "trail_type": "LTP", "trail_trigger": 20, "trail_point": 1, "trail_gap": 19,
        "templates": {"strategy_order": _copy.deepcopy(_STRATEGY_ORDER)},
    }

AUTO_ORDER = {name: _default_auto_order() for name in INDEXES}