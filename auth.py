"""
auth.py - password login for BEP Upstox (PR 5).

One user (CLAUDE.md BEP Upstox decision 5): the password is stored only as a salted scrypt hash in auth.json next
to app.py (git-ignored). No auth.json = no login (the app on your own PC, localhost); once a password is set, every
page asks for it. After MAX_TRIES wrong passwords from one browser address, login is blocked for LOCK_SECONDS.

Set or change the password (on the server, in the app folder):
    .venv/bin/python auth.py set-password        (asks twice, nothing is shown)
    .venv/bin/python auth.py remove-password     (turns the login off)
"""
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "auth.json")
SECRET_PATH = os.path.join(HERE, ".storage_secret")
MAX_TRIES = 5
LOCK_SECONDS = 300
REMEMBER_DAYS = 30
MIN_LENGTH = 8
_N, _R, _P = 2 ** 14, 8, 1               # scrypt cost (about 50 ms per check)

_fails = {}                              # address -> [count, locked_until]


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)


def _load():
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if data.get("hash") and data.get("salt") else None
    except (OSError, ValueError):
        return None


def enabled() -> bool:
    """True once a password is set (auth.json exists)."""
    return _load() is not None


def set_password(password: str) -> str:
    if len(password or "") < MIN_LENGTH:
        raise ValueError(f"the password must have at least {MIN_LENGTH} characters")
    salt = secrets.token_bytes(16)
    data = {"salt": salt.hex(), "hash": _hash(password, salt).hex(), "set": time.strftime("%Y-%m-%d %H:%M"),
            # a new password logs out every browser that was remembered
            "epoch": secrets.token_hex(8)}
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, PATH)
    try:
        os.chmod(PATH, 0o600)
    except OSError:
        pass
    return data["epoch"]


def remove_password():
    try:
        os.remove(PATH)
    except OSError:
        pass


def epoch() -> str:
    data = _load()
    return data.get("epoch", "") if data else ""


def locked_for(address: str) -> int:
    """Seconds this address must still wait (0 = may try)."""
    count, until = _fails.get(address, (0, 0.0))
    left = int(until - time.time())
    return left if left > 0 else 0


def check(password: str, address: str = "") -> tuple:
    """(ok, message). Counts wrong tries per address; blocks after MAX_TRIES for LOCK_SECONDS."""
    wait = locked_for(address)
    if wait:
        return False, f"Too many wrong passwords - try again in {wait // 60 + 1} min."
    data = _load()
    if data is None:
        return True, ""
    ok = hmac.compare_digest(_hash(password or "", bytes.fromhex(data["salt"])), bytes.fromhex(data["hash"]))
    if ok:
        _fails.pop(address, None)
        return True, ""
    count = _fails.get(address, (0, 0.0))[0] + 1
    if count >= MAX_TRIES:
        _fails[address] = (0, time.time() + LOCK_SECONDS)
        return False, f"Wrong password. Blocked for {LOCK_SECONDS // 60} minutes."
    _fails[address] = (count, 0.0)
    return False, f"Wrong password ({MAX_TRIES - count} tries left)."


def storage_secret() -> str:
    """Random secret for the session cookie, created once and kept in .storage_secret (git-ignored)."""
    try:
        with open(SECRET_PATH, encoding="utf-8") as f:
            value = f.read().strip()
        if len(value) >= 32:
            return value
    except OSError:
        pass
    value = secrets.token_hex(32)
    with open(SECRET_PATH, "w", encoding="utf-8") as f:
        f.write(value)
    try:
        os.chmod(SECRET_PATH, 0o600)
    except OSError:
        pass
    return value


def _cli(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "set-password":
        first = getpass.getpass("New password: ")
        if first != getpass.getpass("Again: "):
            print("The two passwords differ - nothing changed.")
            return 1
        try:
            set_password(first)
        except ValueError as ex:
            print(f"Not set: {ex}.")
            return 1
        print("Password set. Restart the app (sudo systemctl restart bep-upstox); every browser must log in again.")
        return 0
    if cmd == "remove-password":
        remove_password()
        print("Password removed: the login is OFF. Restart the app.")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
