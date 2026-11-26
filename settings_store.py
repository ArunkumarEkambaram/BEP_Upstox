"""
settings_store.py - keeps the "once a day" settings in settings.json next to the app, so a restart does
not ask for them again: strike count, scan interval, max orders/day and each index's order defaults
(target / SL / trail, quantity, per-type targets, 09:15 values, max SL for candle-SL cards).

The Upstox token is saved separately in upstox_token.json with today's date and is only reused on that same
date (Upstox tokens expire daily). Deliberately NOT saved: the order mode (the app always starts in Dry run),
Pause alerts and Pause algo trade. settings.json and upstox_token.json are in .gitignore.
Broker orders (PR 2): Market protection and Algo name are saved in settings.json ("broker"); the broker mode is not
(the app starts in Dry run). The 30-day Upstox sandbox token is kept in sandbox_token.json (also git-ignored).
"""
import json
import os

import config

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "upstox_token.json")
SANDBOX_TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox_token.json")
_BROKER_KEYS = ("market_protection", "algo_name")
_AUTO_KEYS = ("tgt", "sl", "tgt_type", "sl_type", "qty", "trail", "candle_max_sl",
              "trail_type", "trail_trigger", "trail_point", "trail_gap", "max_above_bep",
              "tgt_same", "tgt_pair", "tgt_star", "tgt_manual", "open_entry_above", "open_sl_below", "open_sl_tf", "open_max_sl",
              "open_trail", "open_trail_trigger", "open_trail_point", "open_trail_gap",
              "warn_above_bep", "max_loss_day", "open_order_on", "open_skip_date")


def load() -> str:
    """Applies settings.json to config. Returns a short status text."""
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return "No saved settings yet (defaults in use)."
    except (OSError, ValueError) as ex:
        return f"Could not read settings.json ({ex}); defaults in use."
    if isinstance(data.get("strikes_each_side"), int):
        config.STRIKES_EACH_SIDE = max(0, data["strikes_each_side"])
    if isinstance(data.get("scan_interval"), int) and data["scan_interval"] >= 1:
        config.SCAN_INTERVAL_SECONDS = data["scan_interval"]
    if isinstance(data.get("daily_limit"), int) and data["daily_limit"] >= 1:
        config.AUTO_ORDER_DAILY_LIMIT = data["daily_limit"]
    for name, saved in (data.get("auto_order") or {}).items():
        if name in config.AUTO_ORDER and isinstance(saved, dict):
            config.AUTO_ORDER[name].update({k: v for k, v in saved.items() if k in _AUTO_KEYS})
    saved_broker = data.get("broker") or {}
    if isinstance(saved_broker, dict):
        config.BROKER.update({k: v for k, v in saved_broker.items() if k in _BROKER_KEYS})
    return "Saved settings loaded."


def save() -> str:
    data = {
        "strikes_each_side": config.STRIKES_EACH_SIDE,
        "scan_interval": config.SCAN_INTERVAL_SECONDS,
        "daily_limit": config.AUTO_ORDER_DAILY_LIMIT,
        "auto_order": {n: {k: v for k, v in cfg.items() if k in _AUTO_KEYS} for n, cfg in config.AUTO_ORDER.items()},
        "broker": {k: v for k, v in config.BROKER.items() if k in _BROKER_KEYS},
    }
    try:
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, PATH)
        return "Settings saved."
    except OSError as ex:
        return f"Could not save settings ({ex})."


def save_token(token: str, day: str):
    """Keeps today's Upstox token on this computer so a restart the same day reconnects without asking."""
    try:
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            json.dump({"date": day, "token": token}, f)
        try:
            os.chmod(TOKEN_PATH, 0o600)               # readable by this user only (where the OS supports it)
        except OSError:
            pass
    except OSError:
        pass


def load_token(day: str):
    """Today's saved Upstox token, or None (missing, unreadable, or saved on another date)."""
    try:
        with open(TOKEN_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data.get("token") if data.get("date") == day and data.get("token") else None


def save_sandbox_token(token: str, day: str):
    """The Upstox sandbox token lasts 30 days, so it is kept (with the day it was saved) until it is replaced."""
    try:
        with open(SANDBOX_TOKEN_PATH, "w", encoding="utf-8") as f:
            json.dump({"saved": day, "token": token}, f)
        try:
            os.chmod(SANDBOX_TOKEN_PATH, 0o600)
        except OSError:
            pass
        return True
    except OSError:
        return False


def load_sandbox_token():
    """(token, saved_day) or (None, None)."""
    try:
        with open(SANDBOX_TOKEN_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, None
    return (data.get("token") or None), data.get("saved")


def forget_sandbox_token():
    try:
        os.remove(SANDBOX_TOKEN_PATH)
    except OSError:
        pass


def forget_token():
    try:
        os.remove(TOKEN_PATH)
    except OSError:
        pass
