"""
brokers/base.py - the broker interface (BEP Upstox PR 2).

The trading logic (BEP Support, Order Management, exit rules, engine) talks only to a `Broker`: place, modify,
cancel, status and exit by tag. Each broker (Upstox today; another one later) is one adapter in this package that
turns these calls into its own API. Dry run is the `PaperBroker` adapter, so the logic never checks "dry run or live".

Like an interface in C#: `Broker` is a typing.Protocol, so an adapter only needs the same methods (no base class).
The adapter is chosen in one place, `brokers.create()` (the composition root), from the Setup values.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional, Protocol, runtime_checkable

# Order types, sides, products (Upstox names; another adapter maps them to its own).
MARKET, LIMIT, SL, SLM = "MARKET", "LIMIT", "SL", "SL-M"     # SL = stop-limit, SL-M = stop-market
ORDER_TYPES = (MARKET, LIMIT, SL, SLM)
BUY, SELL = "BUY", "SELL"
INTRADAY = "I"

# Normalised order statuses (each adapter maps its broker's status names to these).
SENT = "SENT"                         # accepted by the broker, status not read yet
OPEN = "OPEN"                         # waiting at the exchange (limit not filled yet)
TRIGGER_PENDING = "TRIGGER_PENDING"   # stop order waiting for its trigger
COMPLETE = "COMPLETE"                 # fully filled
CANCELLED = "CANCELLED"
REJECTED = "REJECTED"
UNKNOWN = "UNKNOWN"                   # could not be read (error, or not offered by this broker / mode)
FINAL = (COMPLETE, CANCELLED, REJECTED)

# Capabilities an adapter may offer (the engine checks these instead of the broker's name).
CAP_MODIFY = "modify"                 # modify price / trigger / type of an open order by its ID
CAP_CANCEL_BY_ID = "cancel_by_id"     # cancel one order by its ID
CAP_STATUS = "status"                 # read one order's status by its ID
CAP_ORDER_BOOK = "order_book"         # read all of today's orders in one call
CAP_EXIT_BY_TAG = "exit_by_tag"       # exit the intraday position opened by orders with one tag
CAP_STOP_MARKET = "stop_market"       # SL-M orders
CAP_GTT = "gtt"                       # broker-held entry + target + SL (not used yet)

TICK = 0.05                           # NSE option tick size


def round_tick(price) -> float:
    """Rounds a price to the 0.05 tick (exchanges reject other prices)."""
    return round(round(float(price) / TICK) * TICK, 2)


@dataclass
class OrderRequest:
    """One order, broker-independent. `quantity` is in units (lots x lot size)."""
    instrument_key: str                  # e.g. "NSE_FO|43919" (Upstox key; other brokers map it)
    side: str                            # BUY / SELL
    quantity: int                        # units
    order_type: str = MARKET             # MARKET / LIMIT / SL / SL-M
    price: float = 0.0                   # limit price (LIMIT, SL)
    trigger_price: float = 0.0           # trigger (SL, SL-M)
    product: str = INTRADAY
    tag: str = ""                        # e.g. "BEP-12" (max 40 characters on Upstox)
    symbol: str = ""                     # for the log only, e.g. "NIFTY 25500 CE"
    ref_price: Optional[float] = None    # LTP when sent: the paper broker fills market orders at it
    market_protection: Optional[int] = None   # None = the adapter's Setup value

    def as_dict(self):
        return asdict(self)


@dataclass
class OrderResult:
    """Answer to place / modify / cancel / exit. `order_ids` has one ID, or more if the broker sliced it."""
    ok: bool
    order_ids: list = field(default_factory=list)
    message: str = ""
    code: str = ""                       # the broker's error code, e.g. "UDAPI1154" (static IP)
    raw: Optional[dict] = None           # the broker's reply
    sent: Optional[dict] = None          # what was sent (for the log)
    uncertain: bool = False              # no reply: the order MAY have gone through (check before re-sending)

    @property
    def order_id(self) -> str:
        return self.order_ids[0] if self.order_ids else ""


@dataclass
class OrderStatus:
    order_id: str
    status: str = UNKNOWN
    order_type: str = ""
    side: str = ""
    quantity: int = 0
    filled_qty: int = 0
    pending_qty: int = 0
    avg_price: float = 0.0
    price: float = 0.0
    trigger_price: float = 0.0
    tag: str = ""
    message: str = ""
    raw: Optional[dict] = None


@runtime_checkable
class Broker(Protocol):
    name: str                 # "Upstox", "Paper"
    mode: str                 # "dry" / "sandbox" / "live"
    capabilities: frozenset

    def place(self, req: OrderRequest) -> OrderResult: ...

    def modify(self, order_id: str, *, order_type: str = None, price: float = None,
               trigger_price: float = None, quantity: int = None) -> OrderResult: ...

    def cancel(self, order_id: str) -> OrderResult: ...

    def status(self, order_id: str) -> OrderStatus: ...

    def order_book(self) -> Optional[list]: ...          # [OrderStatus] of today's orders, None when not offered

    def exit_by_tag(self, tag: str) -> OrderResult: ...


def validate(req: OrderRequest) -> str:
    """Empty string when the request is complete, else the reason (checked before anything is sent)."""
    if not req.instrument_key:
        return "no instrument key"
    if req.side not in (BUY, SELL):
        return f"side must be BUY or SELL, not {req.side!r}"
    if req.order_type not in ORDER_TYPES:
        return f"order type must be one of {', '.join(ORDER_TYPES)}"
    if not isinstance(req.quantity, int) or req.quantity <= 0:
        return "quantity must be a whole number above 0"
    if req.order_type in (LIMIT, SL) and not (req.price and req.price > 0):
        return f"{req.order_type} needs a price above 0"
    if req.order_type in (SL, SLM) and not (req.trigger_price and req.trigger_price > 0):
        return f"{req.order_type} needs a trigger price above 0"
    if req.order_type == SL:
        # a BUY stop-limit triggers on a rise: trigger <= limit; a SELL stop-limit on a fall: trigger >= limit
        if req.side == BUY and req.trigger_price > req.price:
            return "BUY stop-limit: the trigger must be at or below the limit price"
        if req.side == SELL and req.trigger_price < req.price:
            return "SELL stop-limit: the trigger must be at or above the limit price"
    if len(req.tag) > 40:
        return "tag longer than 40 characters"
    return ""


def describe(req: OrderRequest) -> str:
    """Short text for the log, e.g. 'BUY 65 NIFTY 25500 CE SL trigger 140.00 limit 141.00 [BEP-12]'."""
    what = req.symbol or req.instrument_key
    px = {MARKET: "market", LIMIT: f"limit {req.price:.2f}", SLM: f"SL-M trigger {req.trigger_price:.2f}",
          SL: f"SL trigger {req.trigger_price:.2f} limit {req.price:.2f}"}.get(req.order_type, req.order_type)
    return f"{req.side} {req.quantity} {what} {px}" + (f" [{req.tag}]" if req.tag else "")
