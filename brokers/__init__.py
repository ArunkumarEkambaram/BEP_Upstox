"""
brokers - the order layer of BEP Upstox (PR 2).

    from brokers import base, create
    broker = create("sandbox", live_token_fn=..., sandbox_token_fn=..., algo_name="", market_protection=-1)
    result = broker.place(base.OrderRequest("NSE_FO|43919", base.BUY, 65, base.LIMIT, price=120.5, tag="BEP-12"))

`create` is the one place that picks an adapter (the composition root, like Program.cs in .NET). To add another
broker later: write brokers/<name>.py with the same five methods as `base.Broker`, and add it here and to
BROKERS. The trading logic does not change.
"""
from . import base
from .paper import PaperBroker
from .upstox import UpstoxBroker

MODES = ("dry", "sandbox", "live")
MODE_LABELS = {"dry": "Dry run", "sandbox": "Sandbox", "live": "Live"}
BROKERS = ("upstox",)            # Setup "Broker" choice (only Upstox for now)


def create(mode: str, *, broker: str = "upstox", live_token_fn=None, sandbox_token_fn=None, algo_name: str = "",
           market_protection: int = -1):
    """The broker for a Setup mode: "dry" = PaperBroker (nothing sent), "sandbox" / "live" = the real adapter."""
    if mode == "dry":
        return PaperBroker()
    if broker != "upstox":
        raise ValueError(f"unknown broker {broker!r}")
    if mode == "sandbox":
        return UpstoxBroker(sandbox_token_fn or (lambda: ""), sandbox=True, algo_name=algo_name,
                            market_protection=market_protection)
    if mode == "live":
        return UpstoxBroker(live_token_fn or (lambda: ""), sandbox=False, algo_name=algo_name,
                            market_protection=market_protection)
    raise ValueError(f"unknown mode {mode!r}")


__all__ = ["base", "create", "MODES", "MODE_LABELS", "BROKERS", "PaperBroker", "UpstoxBroker"]
