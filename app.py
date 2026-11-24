"""
app.py - NiceGUI screens for the BEP Support monitor (single-page app).

Layout: a fixed header (NIFTY LTP, status, connection, Dry run / LIVE, Pause buttons, theme) and a left navigation
drawer with pages served by ui.sub_pages (no full page reloads):
  Dashboard . BEP Support . Order Management (group: Same strike . Pair . BEP Support \u2b50) . Order Details
  . Setup (once a day) . Logs
All trading logic runs once in the background engine (engine.py); pages only read its state and redraw when the
matching version number changes.
"""
import json
from datetime import datetime, timedelta

from nicegui import app, ui

import bep
import brokers
import config
import exit_rules
import export_utils
import limit_orders
import market_utils
import positions
import trade_tracker
from common_watch import CommonWatch
from engine import engine

BOARD_COLUMNS = [
    {"name": "strike", "label": "Strike", "field": "strike", "align": "left"},
    {"name": "ltp", "label": "LTP", "field": "ltp", "align": "right"},
    {"name": "bep", "label": "BEP", "field": "bep", "align": "right"},
    {"name": "time", "label": "Time", "field": "time", "align": "right"},
]
ORDER_COLUMNS = [
    {"name": "time", "label": "Time", "field": "time", "align": "left"},
    {"name": "index", "label": "Index", "field": "index", "align": "left"},
    {"name": "opt", "label": "Type", "field": "opt", "align": "left"},
    {"name": "strike", "label": "Strike", "field": "strike", "align": "left"},
    {"name": "kind", "label": "Order", "field": "kind", "align": "left"},
    {"name": "bep", "label": "BEP", "field": "bep", "align": "left"},
    {"name": "ltp", "label": "Signal LTP", "field": "ltp", "align": "left"},
    {"name": "entry", "label": "Entry", "field": "entry", "align": "left"},
    {"name": "exit", "label": "Exit", "field": "exit", "align": "left"},
    {"name": "ltp_live", "label": "LTP", "field": "ltp_live", "align": "left"},
    {"name": "points", "label": "Captured Points", "field": "points", "align": "left"},
    {"name": "pnl", "label": "P&L ₹", "field": "pnl", "align": "left"},
    {"name": "status", "label": "Status", "field": "status", "align": "left"},
    {"name": "rules", "label": "SL / Target", "field": "rules_text", "align": "left"},
    {"name": "detail", "label": "Detail", "field": "detail", "align": "left"},
    {"name": "action", "label": "Action", "field": "sq_text", "align": "left"},
]
ORDER_EXPORT_COLUMNS = [(c["field"], c["label"]) for c in ORDER_COLUMNS if c["name"] != "action"] + \
    [("sq", "Square-off"), ("payload", "JSON")]
BEP_EXPORT_COLUMNS = [
    ("index", "Index"), ("opt", "Type"), ("strike", "Strike"), ("first", "First of day"),
    ("bep", "BEP"), ("time", "Support time"), ("setup", "Setup"), ("fail_level", "Fail level"),
    ("status", "Status"), ("fail_time", "Failed at"), ("fail_close", "Fail close"), ("ltp", "LTP"),
]
CROSS_EXPORT_COLUMNS = [
    ("index", "Index"), ("time", "Signal candle"), ("direction", "Signal"), ("nifty", "NIFTY close"),
    ("atm", "ATM"), ("up_leg", "Buy leg (closed above BEP)"), ("up_bep", "Buy leg BEP"), ("up_close", "Buy leg close"),
    ("dn_leg", "Opponent (closed below BEP)"), ("dn_bep", "Opponent BEP"), ("dn_close", "Opponent close"),
    ("best_up", "Buy leg best move (pts)"), ("status", "Status"), ("fail_why", "Failed because"), ("also", "Also"),
]
CROSS_COLUMNS = [
    {"name": "time", "label": "Candle", "field": "time", "align": "left"},
    {"name": "dir", "label": "Signal", "field": "dir", "align": "left"},
    {"name": "atm", "label": "NIFTY · ATM", "field": "atm", "align": "left"},
    {"name": "up", "label": "Buy leg (closed above BEP)", "field": "up", "align": "left"},
    {"name": "dn", "label": "Opponent (closed below BEP)", "field": "dn", "align": "left"},
    {"name": "ltp", "label": "LTP buy · opponent", "field": "ltp", "align": "right"},
    {"name": "pts", "label": "Buy leg pts since", "field": "pts", "align": "right"},
    {"name": "best", "label": "Buy leg best", "field": "best", "align": "right"},
    {"name": "status", "label": "Status", "field": "status", "align": "left"},
    {"name": "also", "label": "Also", "field": "also", "align": "left"},
]
CROSS_HELD = ("rgba(34,197,94,0.14)", "#4ade80")
SUPPORT_HELD = ("", "")                                   # default table colours (white text in dark mode)
SUPPORT_RED = ("rgba(239,68,68,0.16)", "#f87171")

OM_ROOT = "/order-management"
# Order Management sub-pages: (path, icon, label, row kind). Add a tuple here (and a page builder) for a new one.
OM_SUB = [
    (OM_ROOT + "/same-strike", "price_change", "Same strike", "same"),
    (OM_ROOT + "/pair", "compare_arrows", "Pair (CE vs PE)", "pair"),
    (OM_ROOT + "/bep-star", "star", "BEP Support \u2b50", "star"),
    (OM_ROOT + "/manual-entry", "edit_note", "Manual Entry", "manual"),
]
SUPPORT_ROOT = "/support"
# BEP Support sub-pages: (path, icon, label, badge key).
SUPPORT_SUB = [
    (SUPPORT_ROOT + "/board", "stacked_line_chart", "Support board", "board"),
    (SUPPORT_ROOT + "/pair-cross", "call_split", "BEP Pair cross", "cross"),
]
NAV = [
    ("/", "space_dashboard", "Dashboard"),
    (SUPPORT_ROOT, "stacked_line_chart", "BEP Support"),    # expandable group of SUPPORT_SUB
    (OM_ROOT, "tune", "Order Management"),             # expandable group of OM_SUB
    ("/order-details", "receipt_long", "Order Details"),
    ("/setup", "settings", "Setup"),
    ("/logs", "article", "Logs"),
]
ORDER_DONE = ("SENT", "DRY RUN", "sending")

CSS = """
<style>
  body.body--dark { background: #0b1220; }
  .body--dark .q-drawer { background: #0f172a; }
  .body--dark .q-card { background: #111a2e; border: 1px solid #1e293b; }
  .body--dark .q-table__card { background: #111a2e; }
  .q-card { border-radius: 12px; }
  .nav-item { border-radius: 8px; margin: 2px 8px; }
  .nav-active { background: rgba(59,130,246,.18); color: #60a5fa; font-weight: 600; }
  /* alerts (PR #36): small toasts top-right, below the header */
  .q-notifications__list--top.items-end { top: 60px; }
  .bep-toast { max-width: 360px; min-width: 0; font-size: 13px; white-space: pre-line; padding: 4px 10px; }
  .bep-toast .q-notification__actions .q-btn { color: #fff !important; min-height: 0; padding: 0 4px; }
  .nav-sub { margin-left: 24px; min-height: 36px; white-space: nowrap; }
  .nav-group-active { color: #60a5fa; font-weight: 600; }
  .stat-label { font-size: 12px; opacity: .65; text-transform: uppercase; letter-spacing: .04em; }
  .stat-value { font-size: 22px; font-weight: 700; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
</style>
"""


def when_changed(keys, fn, interval: float = 1.0):
    """Calls fn() now and again whenever one of the engine's version counters in `keys` changes."""
    last = {"v": None}

    def tick():
        v = tuple(engine.versions[k] for k in keys)
        if v != last["v"]:
            last["v"] = v
            fn()
    ui.timer(interval, tick)


def om_rows(kind, name=None):
    """Order Management rows of one kind ("same" / "pair" / "star") for one index, or for all active ones."""
    out = []
    for n in ([name] if name else engine.active()):
        for r in engine.watches[n].rows:
            src = r.get("source")
            if (src if src in ("pair", "star", "manual") else "same") == kind:
                out.append(r)
    return out


def card_title(text, icon=None):
    with ui.row().classes("items-center gap-2"):
        if icon:
            ui.icon(icon).classes("text-xl text-blue-5")
        ui.label(text).classes("text-md font-bold")


# ====================================================================== shell: header + navigation
@ui.page("/")
@ui.page("/{_:path}")
def root():
    ui.add_head_html(CSS)
    dark = ui.dark_mode(True)
    ui.colors(primary="#3b82f6")
    ui.run_javascript('if (Notification.permission === "default") { Notification.requestPermission(); }')
    nav_items = {}
    nav_state = {"path": None}
    groups = {}                                          # root path -> its ui.expansion

    def set_active(path):
        nav_state["path"] = path
        for p, item in nav_items.items():
            item.classes(remove="nav-active")
            if p == path:
                item.classes(add="nav-active")
        for root_path, exp in groups.items():
            inside = path.startswith(root_path)
            if inside and not exp.value:
                exp.set_value(True)
            exp.props("header-class=" + ("nav-group-active" if inside else '""'))

    with ui.header().classes("items-center gap-3 px-4 py-2 bg-[#0f172a] text-white border-b border-slate-800"):
        ui.button(icon="menu", on_click=lambda: drawer.toggle()).props("flat round color=white dense")
        ui.label(config.APP_NAME).classes("text-lg font-bold tracking-wide")
        ltp_lbl = ui.label("").classes("text-sm bg-slate-800 rounded px-2 py-1")
        status_lbl = ui.label("").classes("text-sm opacity-80")
        ui.space()
        conn_badge = ui.badge("").props("rounded")
        mode_badge = ui.badge("").props("rounded")
        app_sl_badge = ui.badge("").props("rounded color=red-9").classes("text-sm px-2 py-1")
        app_sl_badge.tooltip("Limit orders: the SL / Target are watched by this app - keep this PC on and the app running")
        alerts_badge = ui.badge("ALERTS PAUSED").props("rounded color=red")
        alert_btn = ui.button("", on_click=engine.toggle_alerts).props("dense no-caps")
        algo_btn = ui.button("", on_click=engine.toggle_algo_pause).props("dense no-caps")
        ui.button(icon="dark_mode", on_click=lambda: dark.toggle()).props("flat round color=white dense") \
            .tooltip("Light / dark")

    def refresh_header():
        parts = [f"{n} {engine.ltp[n]}" if engine.ltp[n] is not None else f"{n} --" for n in engine.active()]
        ltp_lbl.set_text(" · ".join(parts) or "--")
        status_lbl.set_text(engine.status)
        conn_badge.set_text("UPSTOX ✓ " + engine.user_name if engine.connected else "UPSTOX ✗")
        conn_badge.props("color=" + ("green-8" if engine.connected else "grey-7"))
        if config.AUTO_ORDER_PAUSED:
            mode_badge.set_text("ALGO PAUSED")
            mode_badge.props("color=grey-7")
        else:
            mode_badge.set_text({"dry": "DRY RUN", "sandbox": "SANDBOX", "live": "LIVE"}[config.BROKER_MODE])
            mode_badge.props("color=" + {"dry": "orange-8", "sandbox": "purple-6", "live": "red"}[config.BROKER_MODE])
        la = engine.limit_active()
        app_sl_badge.set_text(" · ".join(([f"APP-MANAGED SL ACTIVE ({la['held']}) - keep this PC on"] if la["held"] else [])
                                         + ([f"LIMIT OPEN ({la['waiting']})"] if la["waiting"] else [])))
        app_sl_badge.set_visibility(bool(la["held"] or la["waiting"]))
        alerts_badge.set_visibility(config.ALERTS_PAUSED)
        alert_btn.set_text("Resume alerts" if config.ALERTS_PAUSED else "Pause alerts")
        alert_btn.props("icon=" + ("notifications_active color=green" if config.ALERTS_PAUSED
                                   else "notifications_off color=primary"))
        algo_btn.set_text("Resume algo" if config.AUTO_ORDER_PAUSED else "Pause algo")
        algo_btn.props("icon=" + ("play_arrow color=green" if config.AUTO_ORDER_PAUSED else "pause color=orange-8"))

    ui.timer(1.0, refresh_header)

    with ui.left_drawer(value=True, bordered=True).props("width=270").classes("pt-4") as drawer:
        om_badges, support_badges = {}, {}
        for path, icon, label in NAV:
            if path in (OM_ROOT, SUPPORT_ROOT):
                subs, badges = (OM_SUB, om_badges) if path == OM_ROOT else (SUPPORT_SUB, support_badges)
                exp = ui.expansion(label, icon=icon).classes("nav-item").props("dense-toggle")
                groups[path] = exp

                def opened(ev, root_path=path, first=subs[0][0]):   # opening a group goes to its first sub-page
                    if ev.value and not (nav_state["path"] or "").startswith(root_path):
                        ui.navigate.to(first)
                exp.on_value_change(opened)
                with exp:
                    for sub_path, sub_icon, sub_label, kind in subs:
                        with ui.item(on_click=lambda p=sub_path: ui.navigate.to(p)) \
                                .classes("nav-item nav-sub cursor-pointer") as item:
                            with ui.item_section().props("avatar").classes("min-w-0 pr-2"):
                                ui.icon(sub_icon).classes("text-lg")
                            with ui.item_section():
                                ui.label(sub_label).classes("text-sm")
                            with ui.item_section().props("side"):
                                badges[kind] = ui.badge("0").props("rounded color=grey-7")
                        nav_items[sub_path] = item
                continue
            with ui.item(on_click=lambda p=path: ui.navigate.to(p)).classes("nav-item cursor-pointer") as item:
                with ui.item_section().props("avatar"):
                    ui.icon(icon)
                with ui.item_section():
                    ui.label(label)
            nav_items[path] = item
        ui.separator().classes("my-3")
        ui.label("Setup is needed once a day; it is saved for the next start.").classes("text-xs opacity-60 px-4")

    # alerts: every open tab shows each new engine event once (toast + browser notification)
    seen = {"seq": engine.event_seq}

    def pump_events():
        for seq, e in engine.events:
            if seq <= seen["seq"]:
                continue
            seen["seq"] = seq
            ui.notify(e["msg"], type=e["type"], position="top-right", timeout=8000,
                      close_button="✕", classes="bep-toast")
            if e.get("title"):
                ui.run_javascript(f'if (Notification.permission === "granted") '
                                  f'{{ new Notification({json.dumps(e["title"])}, {{ body: {json.dumps(e["msg"])} }}); }}')

    ui.timer(0.5, pump_events)

    def refresh_om_badges():
        """Row count per Order Management sub-page; green when a row is triggered and waiting for Place order."""
        for kind, badge in om_badges.items():
            rows = om_rows(kind)
            ready = any(r["status"] == "triggered" and r.get("order_status") not in ORDER_DONE for r in rows)
            badge.set_text(str(len(rows)))
            badge.props("color=" + ("green-7" if ready else "grey-7"))
    ui.timer(1.0, refresh_om_badges)

    def refresh_support_badges():
        """Support board: rows today. BEP Pair cross: signals today; green while one is being watched."""
        mons = [engine.view_monitor(n) for n in engine.active()]
        support_badges["board"].set_text(str(sum(len(m.support["CE"]) + len(m.support["PE"]) for m in mons)))
        rows = [r for m in mons for r in m.cross.rows]
        support_badges["cross"].set_text(str(len(rows)))
        support_badges["cross"].props("color=" + ("green-7" if any(r["status"] == "watching" for r in rows)
                                                  else "grey-7"))
    ui.timer(1.0, refresh_support_badges)

    def page(path, builder):
        def build():
            set_active(path)
            with ui.column().classes("w-full p-4 gap-4"):
                builder()
        return build

    routes = {path: page(path, b) for path, b in (
        ("/", dashboard_page), (SUPPORT_SUB[0][0], support_page), (SUPPORT_SUB[1][0], pair_cross_page),
        (OM_SUB[0][0], same_strike_page), (OM_SUB[1][0], pair_page), (OM_SUB[2][0], star_page),
        (OM_SUB[3][0], manual_page),
        ("/order-details", orders_page), ("/setup", setup_page), ("/logs", logs_page))}
    routes[OM_ROOT] = page(OM_SUB[0][0], same_strike_page)        # old link: opens Same strike
    routes[SUPPORT_ROOT] = page(SUPPORT_SUB[0][0], support_page)  # old link: opens the Support board
    ui.sub_pages(routes).classes("w-full")


# ====================================================================== Dashboard
def dashboard_page():
    ui.label("Dashboard").classes("text-2xl font-bold")
    if not engine.connected:
        with ui.card().classes("w-full bg-orange-1"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("warning").classes("text-2xl text-orange-8")
                ui.label("Not connected to Upstox. Open Setup to enter today's token and upload the BEP CSV.") \
                    .classes("text-orange-10")
                ui.button("Go to Setup", on_click=lambda: ui.navigate.to("/setup")).props("dense")

    with ui.row().classes("w-full gap-4"):
        stats = {}
        for key, label in (("ltp", "NIFTY LTP"), ("window", "Strike window"), ("pnl", "Today P&L ₹"),
                           ("points", "Captured points"), ("orders", "Orders today")):
            with ui.card().classes("flex-1 min-w-[170px]"):
                ui.label(label).classes("stat-label")
                stats[key] = ui.label("--").classes("stat-value whitespace-nowrap")
                stats[key + "_sub"] = ui.label("").classes("text-xs opacity-60")

    def refresh_stats():
        name = engine.active()[0] if engine.active() else None
        mon = engine.monitors[name] if name else None
        stats["ltp"].set_text(str(engine.ltp.get(name)) if name and engine.ltp.get(name) is not None else "--")
        stats["window"].set_text(f"{min(mon.strikes)}–{max(mon.strikes)}" if mon and mon.strikes else "--")
        s = engine.pnl_summary()
        stats["pnl"].set_text(f"{s['pnl']:,.2f}")
        stats["pnl"].classes(remove="text-green-6 text-red-6")
        stats["pnl"].classes(add="text-green-6" if s["pnl"] >= 0 else "text-red-6")
        stats["points"].set_text(f"{s['points']:+.2f}")
        stats["points_sub"].set_text(f"{s['wins']} of {s['closed']} closed trades won")
        stats["window_sub"].set_text(f"{len(mon.strikes)} strikes" if mon and mon.strikes else "")
        stats["orders"].set_text(str(s["orders"]))

    ui.timer(1.0, refresh_stats)

    for name in engine.active():
        mon = engine.monitors[name]
        with ui.card().classes("w-full"):
            card_title(f"{name} — 09:15 opening order", "alarm")
            plan_lbl = ui.label("").classes("text-sm")
            lock_lbl = ui.label("").classes("text-sm font-medium text-blue-5")

            def refresh_plan(mon=mon, plan_lbl=plan_lbl, lock_lbl=lock_lbl):
                plan_lbl.set_text(mon.plan_text())
                lock_lbl.set_text(mon.side_status_text())
            ui.timer(2.0, refresh_plan)

        cards = {}
        with ui.row().classes("w-full gap-4"):
            for key, title, colour in (("first_CE", "First CE", "green"), ("first_PE", "First PE", "red"),
                                       ("latest_CE", "Latest Support CE", "green"),
                                       ("latest_PE", "Latest Support PE", "red")):
                with ui.card().classes("flex-1 min-w-[220px]") as c:
                    with ui.row().classes("items-center justify-between w-full no-wrap"):
                        with ui.row().classes("items-center gap-2 no-wrap"):
                            ui.icon("circle", color=colour).classes("text-xl")
                            ui.label(title).classes("stat-label")
                        badge = ui.badge("--").props("color=grey")
                    nm = ui.label("--").classes("text-xl font-bold")
                    det = ui.label("").classes("text-sm opacity-70")
                cards[key] = {"card": c, "badge": badge, "name": nm, "detail": det}

        def fill(w, kind, leg, state_colours, name=name):
            w["card"].classes(remove="bg-green-10 bg-red-10")
            if not leg:
                w["name"].set_text(f"NO {kind}")
                w["detail"].set_text("Waiting for first candle" if state_colours is None else "")
                w["badge"].set_text("--")
                w["badge"].props("color=grey")
                return
            w["name"].set_text(f"{name} {leg['strike']}{kind}")
            ltp, level = leg.get("ltp"), leg.get("bep")
            w["detail"].set_text(f"BEP {level:.2f} · LTP {ltp:.2f}" if ltp is not None else f"BEP {level:.2f} · LTP --")
            if ltp is not None:
                gap = ltp - level
                w["badge"].set_text(f"{gap:+.2f}")
                w["badge"].props(f"color={'green' if gap >= 0 else 'red'}")
            if state_colours is not None and leg.get("state") == "red":
                w["card"].classes(add="bg-red-10")

        def refresh_cards(mon=mon, cards=cards, fill=fill):
            for kind in ("CE", "PE"):
                fill(cards[f"first_{kind}"], kind, (mon.first_view or {}).get(kind), None)
                fill(cards[f"latest_{kind}"], kind, (mon.latest_view or {}).get(kind), True)

        when_changed(["cards"], refresh_cards)

    with ui.card().classes("w-full"):
        card_title("Recent activity", "history")
        recent = ui.column().classes("w-full gap-0 mono text-xs")

        def refresh_recent():
            recent.clear()
            with recent:
                for line in engine.log_lines[-12:][::-1]:
                    ui.label(line).classes("whitespace-pre-wrap")
        when_changed(["log"], refresh_recent, 2.0)


# ====================================================================== BEP Support
def last_trading_day() -> str:
    d = datetime.now(market_utils.IST).date() - timedelta(days=1)
    while not market_utils.is_trading_day(market_utils.IST.localize(datetime(d.year, d.month, d.day, 12))):
        d -= timedelta(days=1)
    return d.isoformat()


def support_toolbar(title):
    """Title, Download XLSX (Support board + BEP Pair cross sheets) and the Replay bar of both BEP Support pages."""
    with ui.row().classes("w-full items-center justify-between"):
        ui.label(title).classes("text-2xl font-bold")

        def download_bep_xlsx():
            board, cross = [], []
            for n in engine.active():
                mon = engine.view_monitor(n)
                board.extend(mon.export_rows())
                cross.extend(mon.cross.export_rows())
            if not board and not cross:
                ui.notify("No BEP Support rows to export yet", type="warning")
                return
            day = engine.replay_date or datetime.now().strftime('%Y-%m-%d')
            ui.download(export_utils.build_xlsx_sheets([("Support board", board, BEP_EXPORT_COLUMNS),
                                                         ("BEP Pair cross", cross, CROSS_EXPORT_COLUMNS)]),
                        f"bep_support_{day}{'_replay' if engine.replays else ''}.xlsx")
        ui.button("Download XLSX", icon="download", on_click=download_bep_xlsx).props("dense")

    with ui.card().classes("w-full py-2"):
        with ui.row().classes("w-full items-center gap-4"):
            ui.icon("history").classes("text-xl text-blue-5")
            ui.label("Replay a past day").classes("text-sm font-bold")
            r_date = ui.input("Date", value=engine.replay_date or last_trading_day()).props("dense type=date") \
                .classes("w-40")
            r_src = ui.toggle({"csv": "BEP: uploaded CSV", "calc": "BEP: calculated"},
                              value="csv" if bep.csv_strikes("NIFTY") else "calc").props("dense no-caps")
            r_src.tooltip("Uploaded CSV = upload THAT day's BEP file on Setup first. Calculated = (CE close + PE close) / 2 "
                          "of the day before (slower: 2 calls per strike).")

            async def go():
                run_btn.disable()
                err = await engine.run_replay(r_date.value or "", r_src.value)
                run_btn.enable()
                if err:
                    ui.notify(err, type="negative", multi_line=True)
                else:
                    ui.notify("Replay ready (no alerts, no orders).", type="positive")
            run_btn = ui.button("Replay", icon="play_arrow", on_click=go).props("dense no-caps")
            live_btn = ui.button("Back to live", icon="sensors", on_click=engine.stop_replay) \
                .props("dense no-caps color=green-8")
            r_info = ui.label("").classes("text-sm font-medium")
        ui.label("Rebuilds the Support board and BEP Pair cross of that day from Upstox historical candles. "
                 "No alerts, no ⭐ / Pair rows, no orders; the live scan keeps running. Only days since the last "
                 "expiry (older contracts are not listed).").classes("text-xs opacity-60")

        def refresh():
            busy = engine.replay_busy
            r_info.set_text("Replaying… (reading candles)" if busy else engine.replay_info or "Showing LIVE data")
            r_info.classes(remove="text-amber-5 text-green-5")
            r_info.classes(add="text-amber-5" if engine.replays or busy else "text-green-5")
            live_btn.set_visibility(bool(engine.replays))
        when_changed(["replay"], refresh, 0.5)


def support_page():
    support_toolbar("BEP Support — Support board")
    ui.label("Each row = a green candle touched BEP (low <= BEP) and closed above it. Red = one of the next 3 candles (15 min) "
             "closed below the fail level. ⭐ = first support of the day for that strike, or the first new one after its earlier rows all failed (alerts, and is added to "
             "Order Management when inside the ATM window).").classes("text-xs opacity-70")

    for name in engine.active():
        tables = {}
        with ui.row().classes("w-full no-wrap items-start gap-4"):
            for opt in ("CE", "PE"):
                with ui.card().classes("flex-1 min-w-0"):
                    card_title(f"{name} — {opt}", "trending_up" if opt == "CE" else "trending_down")
                    t = ui.table(columns=BOARD_COLUMNS, rows=[], row_key="id").classes("w-full") \
                        .props("dense flat").style("max-height: 70vh; overflow-y: auto")
                    t.add_slot("body", """
                        <q-tr :props="props" :style="props.row.bg ? {backgroundColor: props.row.bg, color: props.row.fg} : {}">
                          <q-td key="strike" :props="props"><span class="text-weight-bold">{{ props.row.strike }}</span><span v-if="props.row.first" style="margin-left:6px">⭐<q-tooltip>First support for this strike (or the first new one after its earlier rows failed): alerted</q-tooltip></span></q-td>
                          <q-td key="ltp" :props="props">{{ props.row.ltp }}</q-td>
                          <q-td key="bep" :props="props">{{ props.row.bep }}</q-td>
                          <q-td key="time" :props="props">{{ props.row.time }}</q-td>
                        </q-tr>""")
                    tables[opt] = t

        def render(name=name, tables=tables):
            view = engine.view_monitor(name).board_view()
            for opt, table in tables.items():
                rows = []
                for r in view.get(opt, []):
                    row = dict(r)
                    row["bg"], row["fg"] = SUPPORT_HELD if r["kind"] == "green" else SUPPORT_RED
                    rows.append(row)
                table.rows = rows
                table.update()
        when_changed(["board", "replay"], render)


def pair_cross_page():
    support_toolbar("BEP Support — BEP Pair cross")
    tol, n_touch = getattr(config, "BEP_CROSS_TOUCH_TOL", 1.0), getattr(config, "BEP_CROSS_TOUCH_CANDLES", 3)
    itm = getattr(config, "BEP_CROSS_ITM_STRIKES", 2)
    ui.label(f"5-min candles, ATM = strike nearest NIFTY's close. A row = on this candle one leg closed ABOVE its BEP (the "
             f"one to BUY) and the opponent closed BELOW its BEP, for the first time (either leg may have crossed first). "
             f"Buy CE: the CE at ATM or up to {itm} strikes ITM, the opponent PE at ATM ±1 strike; Buy PE the other way. "
             f"Both legs must have touched their "
             f"BEP (±{tol:g}) on this candle or the {n_touch - 1} before. Watched 30 min: FAILED if the leg above closes "
             f"below its BEP or the leg below closes above its BEP, else HELD. Live signals alert and are added to "
             f"Order Management > Pair (prices = BEPs, buffer 0). Independent of the Support board / ⭐.") \
        .classes("text-xs opacity-70")
    for name in engine.active():
        step = config.INDEXES[name]["strike_interval"]
        with ui.card().classes("w-full py-2"):
            with ui.row().classes("items-end gap-4"):
                ui.icon("help_outline").classes("text-xl text-blue-5 self-center")
                ui.label("Why no row?").classes("text-sm font-bold self-center")
                w_ce = ui.number("CE strike", value=centre_strike(name), step=step, precision=0).classes("w-28")
                w_pe = ui.number("PE strike", value=centre_strike(name), step=step, precision=0).classes("w-28")
                w_t = ui.input("Candle (HH:MM)", value="09:40").classes("w-32")

                async def check(name=name, w_ce=w_ce, w_pe=w_pe, w_t=w_t):
                    try:
                        ce, pe = int(w_ce.value), int(w_pe.value)
                    except (TypeError, ValueError):
                        ui.notify("Enter the CE and PE strikes.", type="negative")
                        return
                    lines = await engine.explain_cross(name, ce, pe, w_t.value or "")
                    day = engine.replay_date or "today"
                    with ui.dialog() as dlg, ui.card().classes("min-w-[560px]"):
                        ui.label(f"{ce}CE / {pe}PE · {w_t.value} candle · {day}").classes("text-md font-bold")
                        for ok, text in lines:
                            icon, colour = {True: ("check_circle", "text-green-5"), False: ("cancel", "text-red-5")} \
                                .get(ok, ("info", "text-blue-5"))
                            with ui.row().classes("items-start gap-2 no-wrap"):
                                ui.icon(icon).classes(f"text-lg {colour}")
                                ui.label(text).classes("text-sm")
                        ui.button("Close", on_click=dlg.close).props("flat")
                    dlg.open()
                ui.button("Check", icon="search", on_click=check).props("dense no-caps")
                ui.label("Checks one CE / PE pair on one candle of the day shown (today or the replayed day) "
                         "against every rule.").classes("text-xs opacity-60 self-center")
        with ui.card().classes("w-full"):
            card_title(f"{name} — BEP Pair cross", "call_split")
            t = ui.table(columns=CROSS_COLUMNS, rows=[], row_key="id").classes("w-full").props("dense flat") \
                .style("max-height: 75vh; overflow-y: auto")
            t.add_slot("body", """
                <q-tr :props="props" :style="props.row.bg ? {backgroundColor: props.row.bg, color: props.row.fg} : {}">
                  <q-td v-for="col in props.cols" :key="col.name" :props="props">
                    <q-badge v-if="col.name === 'dir'" :color="props.row.buy === 'CE' ? 'green-8' : 'red-8'">{{ col.value }}</q-badge>
                    <span v-else :class="col.name === 'status' || col.name === 'up' ? 'text-weight-bold' : ''">{{ col.value }}</span>
                    <q-tooltip v-if="col.name === 'status' && props.row.why">{{ props.row.why }}</q-tooltip>
                  </q-td>
                </q-tr>""")

        def render(name=name, t=t):
            mon = engine.view_monitor(name)
            live = not engine.replays
            rows = []
            for r in reversed(mon.cross.rows):
                up, dn = r["up"], r["dn"]
                lu = engine.option_ltp.get((name, up["strike"], up["opt"])) if live and r["status"] == "watching" else None
                ld = engine.option_ltp.get((name, dn["strike"], dn["opt"])) if live and r["status"] == "watching" else None
                pu = (lu if lu is not None else up["last"]) - up["close"]
                bg, fg = {"failed": SUPPORT_RED, "held": CROSS_HELD}.get(r["status"], SUPPORT_HELD)
                rows.append({
                    "id": r["id"], "time": r["time"], "dir": f"Buy {up['opt']}", "buy": up["opt"],
                    "atm": f"{r['nifty']:.2f} · {r['atm']}" if r["nifty"] is not None else f"-- · {r['atm']}",
                    "up": f"{up['strike']}{up['opt']}  BEP {up['bep']:.2f} · {up['close']:.2f}",
                    "dn": f"{dn['strike']}{dn['opt']}  BEP {dn['bep']:.2f} · {dn['close']:.2f}",
                    "ltp": (f"{lu:.2f} · {ld:.2f}" if lu is not None and ld is not None
                            else f"close {up['last']:.2f} · {dn['last']:.2f}"),
                    "pts": f"{pu:+.2f}", "best": f"{r['best_up']:+.2f}",
                    "status": mon.cross.status_text(r), "why": r.get("fail_why") or "",
                    "also": ", ".join(r["also"]), "bg": bg, "fg": fg,
                })
            t.rows = rows
            t.update()
        when_changed(["cross", "replay"], render)

        def tick(name=name, render=render):              # live LTP of watched rows (2 s batch)
            if not engine.replays and any(r["status"] == "watching" for r in engine.view_monitor(name).cross.rows):
                render()
        ui.timer(2.0, tick)


# ====================================================================== Order Management (common price + stars)
def rule_dialog(title, kind, current, on_save, on_cancel=None, allow_none=False):
    """Popup to set an SL / Target rule: Type LTP / POINT / CANDLE, Time frame (CANDLE), Value.
    allow_none (Target only, PR #48): adds Type "None" = no target for this order (Setup default unchanged).
    Created on the page layout (not inside the row) so a row redraw during a scan cannot close it."""
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 380px"):
        ui.label(title).classes("text-lg font-bold")
        types = {t: t for t in exit_rules.TYPES}
        if allow_none and kind == "tgt":
            types[exit_rules.NONE] = "None"
        cur_type = (current or {}).get("type", "CANDLE")
        typ = ui.select(types, value=cur_type if cur_type in types else "CANDLE",
                        label="Type").classes("w-full")
        tf = ui.select({1: "1 Min", 3: "3 Min", 5: "5 Min"}, value=(current or {}).get("tf") or 5,
                       label="Time frame").classes("w-full")
        tf.bind_visibility_from(typ, "value", lambda v: v == "CANDLE")
        val = ui.number("Value", value=(current or {}).get("value") if cur_type != exit_rules.NONE else None,
                        min=0, step=0.05).classes("w-full")
        val.bind_visibility_from(typ, "value", lambda v: v != exit_rules.NONE)
        hint = ui.label("").classes("text-xs opacity-70")

        def explain(_=None):
            side = "below" if kind == "sl" else "above"
            sign = "entry \u2212 value" if kind == "sl" else "entry + value"
            hint.set_text({"CANDLE": f"Exit when a {tf.value}-min candle CLOSES {side} the value (a wick does not exit).",
                           "LTP": "The value is a price; exit when the price touches it.",
                           "POINT": f"The value is points from entry ({sign}).",
                           exit_rules.NONE: "No target for this order: it exits by the SL, the trailing SL, a manual "
                                            "square-off or 15:15. The Setup default target is not changed."
                           }[typ.value])
        typ.on_value_change(explain)
        tf.on_value_change(explain)
        explain()

        def save():
            if typ.value == exit_rules.NONE:
                on_save(exit_rules.make_rule(kind, exit_rules.NONE, 0))
                dlg.close()
                return
            if val.value is None or float(val.value) <= 0:
                ui.notify("Value must be above 0.", type="negative")
                return
            on_save(exit_rules.make_rule(kind, typ.value, float(val.value), tf.value))
            dlg.close()

        def cancel():
            dlg.close()
            if on_cancel:
                try:
                    on_cancel()
                except Exception:                      # the row was redrawn meanwhile; nothing to reset
                    pass
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Close", on_click=cancel).props("color=red")
            ui.button("Add" if not current else "Save", on_click=save)
    dlg.props("persistent")
    dlg.open()


def rule_toggle(label, kind, current, default_text, locked, on_set, allow_none=False):
    """Switch + summary. ON opens the popup; OFF goes back to the default. Click the summary to edit."""
    rule = exit_rules.parse(current)
    with ui.row().classes("items-center gap-1 no-wrap"):
        sw = ui.switch(label, value=bool(rule)).props("dense")
        summary = ui.label(exit_rules.describe(rule) if rule else default_text).classes(
            "text-xs " + ("text-blue-4 cursor-pointer underline" if rule and not locked else "opacity-60"))

        def changed(ev):
            if ev.value and not exit_rules.parse(current):
                rule_dialog(f"Add {'StopLoss' if kind == 'sl' else 'Target'}", kind, None, on_set,
                            on_cancel=lambda: sw.set_value(False), allow_none=allow_none)
            elif not ev.value and exit_rules.parse(current):
                on_set(None)
        sw.on_value_change(changed)
        if rule and not locked:
            summary.on("click", lambda: rule_dialog(f"Edit {'StopLoss' if kind == 'sl' else 'Target'}", kind, rule, on_set,
                                                    allow_none=allow_none))
        if locked:
            sw.disable()


def sl_tgt_cell(name, r):
    """Order Management row: its own SL / Target (popup); OFF = Setup SL / the row's default target."""
    locked = r.get("order_status") in ("SENT", "DRY RUN", "sending")
    cfg = config.AUTO_ORDER[name]
    t_type, t_val = engine.row_target(name, r)

    def setter(kind):
        def on_set(rule):
            if engine.watches[name].set_custom(r["id"], kind, rule):
                engine.log(f"{name} Order Management row {r['id']}: {'SL' if kind == 'sl' else 'Target'} "
                           + (f"set to {exit_rules.describe(rule)}" if rule else "back to default"))
                engine.bump("watch")
        return on_set
    with ui.column().classes("gap-0"):
        rule_toggle("SL", "sl", r.get("c_sl"), f"Setup {cfg['sl']:g} {cfg['sl_type']}", locked, setter("sl"))
        rule_toggle("Target", "tgt", r.get("c_tgt"), f"Default {t_val:g} {t_type}", locked, setter("tgt"),
                    allow_none=True)


def section(title, icon, rows, empty_text, place, name):
    """One block of Order Management rows (Common Price or BEP Support \u2b50)."""
    card_title(title, icon)
    if not rows:
        ui.label(empty_text).classes("text-sm opacity-70")
        return
    with ui.grid(columns="90px 80px 50px 90px 60px 140px 1fr 130px 230px 200px").classes("w-full items-center gap-x-3 gap-y-1"):
        for head in ("Source", "Strike", "Type", level_name(rows[0]), "TF", "Last close", "Status", "Order",
                     "SL / Target", ""):
            ui.label(head).classes("stat-label")
        for r in rows:
            bg = {"triggered": "bg-green-10 text-green-2", "failed": "bg-red-10 text-red-2"}.get(r["status"], "")
            src = f"\u2b50 {r['trig_time']}" if r.get("source") == "star" else "Manual"
            level = manual_type(r) if r.get("source") == "manual" else f"{r['price']:g}"
            cells = [src, str(r["strike"]), r["opt"], level, f"{r['tf']} min" + (" · LTP" if r.get("trig_on") == "ltp" else ""),
                     (f"{r['last_close']:.2f} ({r['last_time']})" if r["last_close"] is not None else "--"),
                     CommonWatch.status_text(r)]
            for txt in cells:
                ui.label(txt).classes(f"text-sm px-2 py-1 rounded {bg}")
            order_type_cell(name, r)
            sl_tgt_cell(name, r)
            with ui.row().classes("gap-1 no-wrap"):
                done = r["order_status"] in ("SENT", "DRY RUN", "sending")
                b = ui.button(f"Ordered ({r['order_status']})" if done else "Place order",
                              on_click=lambda _e, i=r["id"]: place(i)).props("dense size=sm color=green")
                b.set_enabled(r["status"] == "triggered" and not done)
                ui.button(icon="delete", on_click=lambda _e, r=r: confirm_delete(name, r)) \
                    .props("dense flat size=sm color=grey-6")


LEVEL_NAME = {"star": "BEP", "pair": "Pair price", "manual": "Order type"}


def level_name(r) -> str:
    """Heading of a card's level box: Common price (Same strike) / Pair price / BEP (\u2b50) / Order type (Manual)."""
    if r.get("auto") == "cross":                       # BEP Pair cross row: the Pair prices are the legs' BEPs
        return "BEP"
    return LEVEL_NAME.get(r.get("source"), "Common price")


def manual_type(r) -> str:
    """Manual Entry card's order type: Limit / Price (app waits for the price, then market) / Market."""
    if r.get("entry_trigger"):
        return "Stop-limit"
    return "Limit" if r.get("entry_mode") == "limit" else ("Price" if r.get("entry_price") else "Market")


def card_mode(r) -> str:
    """strategy / limit / stoplimit (toggle value)."""
    return "stoplimit" if engine.is_stop(r) else "limit" if r.get("entry_mode") == "limit" else "strategy"


def order_type_text(r) -> str:
    if r.get("entry_mode") != "limit":
        return "Strategy"
    if engine.is_stop(r):
        t, p = engine.stop_prices(r)
        return f"Stop {t:g} \u2192 {p:g}" if t else "Stop @ leg price"
    p = engine.limit_price(r)
    return f"Limit @ {p:g}" if p else "Limit @ leg price"


def order_type_editable(r) -> bool:
    return (r.get("source") != "manual" and r["status"] not in ("failed", "exited")
            and (r.get("order_status") or "") not in ORDER_DONE + ("PENDING",))


def order_type_cell(name, r):
    """Compact view: the row's order type; click to change it (until ordered)."""
    lbl = ui.label(order_type_text(r)).classes(
        "text-sm px-2 py-1 rounded " + ("text-amber-5" if r.get("entry_mode") == "limit" else ""))
    if order_type_editable(r):
        lbl.classes("cursor-pointer underline decoration-dotted")
        lbl.on("click", lambda: order_type_dialog(name, r))
        lbl.tooltip("Click to choose Strategy, Limit or Stop-limit")


def stoplimit_dialog(name, r, on_cancel=None):
    """Stop-limit trigger / limit popup for a Same strike / Pair / \u2b50 card (PR #45). The trigger must be above
    the live LTP; it starts at the card's level (BEP) only when that is above the LTP. A Pair that has not fired
    may stay empty = the fired leg's price at \u26a1."""
    pair_unfired = r.get("kind") == "pair" and not r.get("opt")
    ltp = None if pair_unfired else engine.row_live_ltp(name, r)
    level = None if pair_unfired else (float(r["price"]) if r.get("price") else None)
    t0, p0 = engine.stop_prices(r) if engine.is_stop(r) else (None, None)
    if t0 is None and level is not None and (ltp is None or level > ltp):
        t0 = level
    if t0 is not None and p0 is None:
        p0 = round(t0 + config.STOP_LIMIT_GAP, 2)
    state = {"saved": False}
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 400px"):
        what = CommonWatch.pair_title(r) if r.get("kind") == "pair" else f"{r['strike']}{r['opt']}"
        ui.label(f"Stop-limit \u2014 {what}").classes("text-lg font-bold")
        ui.label(("LTP " + (f"{ltp:.2f}" if ltp is not None else "--")) +
                 (f" \u00b7 {level_name(r)} {level:.2f}" if level is not None else "")).classes("text-sm")
        trg = ui.number("Trigger (LTP rises to)", value=t0, min=0, step=0.05).classes("w-full")
        lim = ui.number("Limit price (most you pay)", value=p0, min=0, step=0.05).classes("w-full")
        if pair_unfired:
            note = "Not fired yet: leave both empty to use the fired leg's price (trigger) + " \
                   f"{config.STOP_LIMIT_GAP:g} (limit) at \u26a1."
        elif ltp is not None:
            note = f"The trigger must be above the LTP {ltp:.2f}" + (
                f" (the {level_name(r)} {level:.2f} is already below it)." if level is not None and level <= ltp else ".")
        else:
            note = "No live LTP yet: the trigger is checked against the LTP again at \u26a1."
        ui.label(note).classes("text-xs text-amber-5")
        err = ui.label("").classes("text-xs text-red-5")

        def trigger_set(_e=None):
            if trg.value:
                lim.set_value(round(float(trg.value) + config.STOP_LIMIT_GAP, 2))
        trg.on("blur", trigger_set)

        def save():
            if pair_unfired and trg.value is None and lim.value is None:
                engine.set_watch_mode(name, r["id"], "stoplimit")
            else:
                try:
                    t, p = float(trg.value), float(lim.value)
                except (TypeError, ValueError):
                    err.set_text("Enter the trigger and the limit price.")
                    return
                if t <= 0 or p < t:
                    err.set_text("The limit must be at or above the trigger (both above 0).")
                    return
                if ltp is not None and t <= ltp:
                    err.set_text(f"The trigger must be above the LTP {ltp:.2f}.")
                    return
                if not engine.is_stop(r):
                    engine.set_watch_mode(name, r["id"], "stoplimit")
                engine.set_stoplimit(name, r["id"], t, p)
            state["saved"] = True
            dlg.close()

        def cancel():
            dlg.close()
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=cancel).props("flat")
            ui.button("Save", on_click=save)
    dlg.on("hide", lambda: (on_cancel() if on_cancel and not state["saved"] else None))
    dlg.open()


def order_type_dialog(name, r):
    """Strategy (strategy_order) or Limit (limit_order at a price; SL / Target watched by the app)."""
    pair_unfired = r.get("kind") == "pair" and not r.get("opt")
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 380px"):
        ui.label("Order type").classes("text-lg font-bold")
        mode = ui.toggle({"strategy": "Strategy", "limit": "Limit", "stoplimit": "Stop-limit"}, value=card_mode(r)).props("no-caps")
        price = ui.number("Limit price" + (" (empty = the fired leg's price)" if pair_unfired else " (empty = the level)"),
                          value=r.get("entry_price") if r.get("entry_mode") == "limit" else r.get("price"),
                          min=0, step=0.05).classes("w-full")
        price.bind_visibility_from(mode, "value", lambda v: v == "limit")
        ui.label("Strategy = market BUY. Limit = a LIMIT BUY at the price or cheaper (fills at once if the LTP is "
                 "already below it). Stop-limit = an SL BUY that waits at the exchange until the LTP RISES to the "
                 "trigger (asked next, above the LTP), then a limit at the limit price. After the fill every type "
                 "gets the SL at the exchange (SL-M), trailing moves it, the target is watched by the app. An "
                 "unfilled order is cancelled (by its ID) if the card fails, at 15:15 or when deleted.") \
            .classes("text-xs opacity-70")

        def save():
            limit = mode.value == "limit"
            if limit and price.value is not None and float(price.value) <= 0:
                ui.notify("The limit price must be above 0.", type="negative")
                return
            if mode.value == "stoplimit":              # trigger / limit popup (PR #45)
                dlg.close()
                stoplimit_dialog(name, r)
                return
            engine.set_watch_mode(name, r["id"], mode.value if mode.value != "strategy" else None)
            if limit and price.value:
                engine.set_watch_entry(name, r["id"], float(price.value))
            dlg.close()
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Close", on_click=dlg.close).props("flat")
            ui.button("Save", on_click=save)
    dlg.open()


def order_mode_toggle():
    """Add forms: Order type toggle (Strategy default)."""
    with ui.column().classes("gap-0"):
        ui.label("Order").classes("text-xs opacity-70")
        return ui.toggle({"strategy": "Strategy", "limit": "Limit", "stoplimit": "Stop-limit"}, value="strategy").props("dense no-caps")


def pair_section(rows, place, name):
    """Pair rows: CE strike vs PE strike around a common price."""
    card_title(f"Pairs \u2014 CE vs PE ({len(rows)})", "compare_arrows")
    if not rows:
        ui.label("No pairs yet - add one above.").classes("text-sm opacity-70")
        return
    with ui.grid(columns="240px 60px 60px 170px 1fr 130px 230px 210px").classes("w-full items-center gap-x-3 gap-y-1"):
        for head in ("Pair", "Buffer", "TF", "Last close CE \u00b7 PE", "Status", "Order", "SL / Target", ""):
            ui.label(head).classes("stat-label")
        for r in rows:
            bg = {"triggered": "bg-green-10 text-green-2", "failed": "bg-red-10 text-red-2",
                  "exited": "bg-red-10 text-red-2"}.get(r["status"], "")
            lc = r["legs"]["CE"].get("last_close"), r["legs"]["PE"].get("last_close")
            closes = " \u00b7 ".join(f"{v:.2f}" if v is not None else "--" for v in lc)
            for txt in (CommonWatch.pair_title(r), f"{r['buffer']:g}",
                        f"{r['tf']} min" + (" · LTP" if r.get("trig_on") == "ltp" else ""), closes, CommonWatch.status_text(r)):
                ui.label(txt).classes(f"text-sm px-2 py-1 rounded {bg}")
            order_type_cell(name, r)
            sl_tgt_cell(name, r)
            with ui.row().classes("gap-1 no-wrap"):
                done = r["order_status"] in ("SENT", "DRY RUN", "sending")
                text = f"Ordered {r['opt']} ({r['order_status']})" if done else \
                    (f"Place order {r['opt']}" if r["status"] == "triggered" else "Place order")
                b = ui.button(text, on_click=lambda _e, i=r["id"]: place(i)).props("dense size=sm color=green")
                b.set_enabled(r["status"] == "triggered" and not done)
                ui.button(icon="delete", on_click=lambda _e, r=r: confirm_delete(name, r)) \
                    .props("dense flat size=sm color=grey-6")


# ---------------------------------------------------------------------- Order Management cards
OM_VIEW = {"compact": False}                      # Cards / Compact (table) view, shared by every tab
CHIP = {"WAITING": "grey-7", "TRIGGERED": "green-7", "SENDING": "blue-7", "ORDERED": "blue-7",
        "EXITED": "purple-6", "FAILED": "red-7", "ORDER FAILED": "red-7", "BLOCKED": "orange-8", "PENDING": "amber-9",
        "SQUARING OFF": "deep-orange-8", "SQUARE-OFF FAILED": "red-7", "LIMIT OPEN": "amber-9", "FILLED": "blue-7",
        "CANCELLING": "deep-orange-8", "CANCEL FAILED": "red-7", "CANCELLED": "grey-7", "REJECTED": "red-7",
        "NO SL": "red-7", "EXIT FAILED": "red-7", "EXITING": "deep-orange-8", "STOP": "amber-9"}


def card_state(r, order=None) -> str:
    """Card status chip: WAITING / TRIGGERED / SENDING / ORDERED · SENT|DRY RUN / EXITED / FAILED / ORDER FAILED."""
    os_ = r.get("order_status") or ""
    if os_ == "PENDING" and r.get("pending"):
        p = r["pending"]
        return f"PENDING @ {p['price']:.2f} " + ("\u2193" if p["dir"] == "below" else "\u2191")
    if os_ == "sending":
        return "SENDING"
    if os_ in ("SENT", "DRY RUN"):
        st = engine._bk(order) if order else None
        if st:                                             # broker position (positions.py)
            if st.get("exit") is not None or (order and trade_tracker._f(order.get("exit")) is not None):
                return f"EXITED · {st.get('exit_reason') or 'Exit'}"
            if st.get("alert"):
                return st["alert"]
            if st.get("exit_req"):
                return f"EXITING · {st['exit_req']}"
            et = positions.entry_text(st)               # Limit / Stop-limit waiting or partly filled (PR 4)
            if et and (st.get("fill") is None or not st.get("sl_id")):
                return et
            if st.get("fill") is None:
                return f"ORDERED · {os_} · waiting for the fill"
            if st.get("sl_trg") is not None and st.get("sl_id"):
                return f"ORDERED · SL {st['sl_trg']:.2f}"
            return f"ORDERED · {os_}"
        m = limit_orders.parse((order or {}).get("mg"))
        if m and not limit_orders.held(m):                 # limit order with nothing filled yet
            return limit_orders.state_text(m)
        exited = r["status"] == "exited" or (order is not None and trade_tracker._f(order.get("exit")) is not None)
        sq = str((order or {}).get("sq") or "")
        if exited:
            if m and m.get("exit"):
                return f"EXITED · {limit_orders.exit_label(m)}"
            return "EXITED · Manual" if engine.squared_off(order or {}) else "EXITED"
        if sq.startswith("SENT"):
            return "SQUARING OFF"
        if sq.startswith("FAILED"):
            return "SQUARE-OFF FAILED"
        if m:
            return f"FILLED @ {m['fill']:.2f}" + (" · PARTIAL" if limit_orders.pending(m) else "")
        return f"ORDERED · {os_}"
    if r["status"] == "failed":
        return "FAILED"
    if r["status"] == "exited":
        return "EXITED"
    if r["status"] == "triggered":
        return {"FAILED": "ORDER FAILED", "BLOCKED": "BLOCKED", "CANCELLED": "CANCELLED"}.get(os_, "TRIGGERED")
    return "WAITING"


def _chip_colour(state):
    return CHIP.get(state.split(" ·")[0].split(" @")[0], "blue-7")


def _num_text(v, fmt="{:.2f}"):
    f = trade_tracker._f(v)
    return fmt.format(f) if f is not None else "--"


def card_symbol(name, r) -> str:
    exp = engine.monitors[name].expiry
    if r.get("kind") == "pair":
        if r.get("opt"):                             # a side fired: show the leg that is ordered / orderable
            return market_utils.option_symbol(name, exp, r["strike"], r["opt"])
        return f"{market_utils.option_symbol(name, exp, r['ce_strike'], 'CE')} / {r['pe_strike']}PE"
    return market_utils.option_symbol(name, exp, r["strike"], r["opt"])


def card_ltp_text(name, r) -> str:
    lt = engine.option_ltp
    if r.get("kind") == "pair":
        ce, pe = lt.get((name, r["ce_strike"], "CE")), lt.get((name, r["pe_strike"], "PE"))
        return " · ".join(f"{o} {v:.2f}" if v is not None else f"{o} --" for o, v in (("CE", ce), ("PE", pe)))
    v = lt.get((name, int(r["strike"]), r["opt"]))
    if v is None and r.get("last_close") is not None:
        return f"{r['last_close']:.2f}*"            # * = last candle close until a live LTP arrives
    return f"{v:.2f}" if v is not None else "--"


def cross_bep_gaps(name, r):
    """BEP cross Pair card: live LTP minus BEP for the CE and the PE leg (None until an LTP arrives)."""
    lt = engine.option_ltp
    out = []
    for opt, strike, bep_ in (("CE", r["ce_strike"], r["ce_price"]), ("PE", r["pe_strike"], r["pe_price"])):
        v = lt.get((name, int(strike), opt))
        out.append((opt, None if v is None else round(v - bep_, 2)))
    return out


def card_box(title, icon=None):
    """One box of the card body (title on top, content below)."""
    col = ui.column().classes("items-center justify-start gap-0 px-2 py-1 min-w-[120px] flex-1 "
                              "border-r border-slate-700 last:border-r-0")
    with col:
        with ui.row().classes("items-center gap-1 no-wrap"):
            if icon:
                ui.icon(icon).classes("text-sm text-amber-5")
            ui.label(title).classes("text-sm opacity-60")
    return col


def card_rule_box(name, r, kind, locked):
    """Target / Stoploss box: value of the row's own rule (click to edit, x to clear) or 'Set ...' link."""
    what = "Target" if kind == "tgt" else "Stoploss"
    rule = exit_rules.parse(r.get("c_" + kind))
    cfg = config.AUTO_ORDER[name]
    if kind == "tgt":
        d_type, d_val = engine.row_target(name, r)
    else:
        d_type, d_val = cfg["sl_type"], cfg["sl"]
    order = engine.row_order(r) if locked else None
    m = limit_orders.parse(order.get("mg")) if order else None
    if m:                                             # limit order: the rule the app watches
        if m.get(kind):
            d_type, d_val = m[kind]["type"], m[kind]["value"]
    elif order:                                       # show what was actually sent with the order
        prm = trade_tracker._params(order)
        if prm.get(kind) is not None:
            d_type, d_val = prm[kind + "_type"], prm[kind]
    sent_none = bool(order) and not m and kind == "tgt" and exit_rules.is_none(rule)   # PR #48: sent with no target

    def on_set(new_rule):
        if engine.watches[name].set_custom(r["id"], kind, new_rule):
            engine.log(f"{name} Order Management row {r['id']}: {'SL' if kind == 'sl' else 'Target'} "
                       + (f"set to {exit_rules.describe(new_rule)}" if new_rule else "back to default"))
            engine.bump("watch")

    if order is not None:                             # ordered: app-watched rule (limit: replaces; strategy: on top)
        o_rule = engine.order_rule(order, kind)
        editable = engine.order_rule_editable(order)

        def on_order_set(new_rule, oid=order["id"], entry=trade_tracker._f(order.get("entry"))):
            def apply(rule):
                engine.set_order_custom(oid, kind, rule)
                engine.bump("watch")
            guarded_sl_set(kind, new_rule, entry, apply)
        with card_box(what, "edit" if editable else None):
            with ui.row().classes("items-center gap-1 no-wrap"):
                if o_rule:
                    ui.icon("candlestick_chart" if o_rule["type"] == "CANDLE" else "trending_flat").classes("text-sm")
                v = ui.label(f"{o_rule['value']:.2f}" if o_rule else
                             ("--" if m else ("None" if sent_none else f"{d_val:g} {d_type}"))) \
                    .classes("text-lg font-bold")
                if editable:
                    v.classes("cursor-pointer")
                    v.on("click", lambda: rule_dialog(f"{what} (app-watched)", kind, o_rule, on_order_set,
                                                      allow_none=bool(m)))
                    if o_rule:
                        ui.button(icon="close", on_click=lambda: on_order_set(None)) \
                            .props("flat dense round size=xs color=red") \
                            .tooltip(f"Remove the {what} (no {what} left)" if m else "Remove the app rule")
            if m:                                     # limit / stop-limit: the app holds the only rule
                sub = exit_rules.describe(o_rule) if o_rule else ("none" + (" \u00b7 click to set" if editable else ""))
                if kind == "sl":
                    lvl, trailed = limit_orders.sl_level(m)
                    if lvl is not None and (trailed or (o_rule and o_rule["type"] != "CANDLE")):
                        sub += f" \u00b7 at {lvl:g}" + (" (trailed)" if trailed else "")
                ui.label(sub + " \u00b7 app").classes("text-xs text-amber-5")
                return
            ui.label(exit_rules.describe(o_rule) + " \u00b7 app" if o_rule else
                     ("no target (trail / SL only)" if sent_none else "as sent")
                     + (" \u00b7 click to add an app rule" if editable else "")) \
                .classes("text-xs " + ("text-amber-5" if o_rule else "opacity-60"))
        return

    with card_box(what, "edit" if rule and not locked else None):
        if rule:
            with ui.row().classes("items-center gap-1 no-wrap"):
                ui.icon("block" if exit_rules.is_none(rule) else
                        "candlestick_chart" if rule["type"] == "CANDLE" else "trending_flat").classes("text-sm")
                v = ui.label("None" if exit_rules.is_none(rule) else f"{rule['value']:.2f}").classes("text-lg font-bold")
                if not locked:
                    v.classes("cursor-pointer")
                    v.on("click", lambda: rule_dialog(f"Edit {what}", kind, rule, on_set, allow_none=True))
                    ui.button(icon="close", on_click=lambda: on_set(None)) \
                        .props("flat dense round size=xs color=red").tooltip("Back to default")
            ui.label(exit_rules.describe(rule)).classes("text-xs text-amber-5")
        else:
            if locked:
                ui.label(f"{d_val:g} {d_type}").classes("text-lg font-bold")
                sub = "as sent" if order else "Setup"
                if m:
                    sub = "app-watched"
                    lvl, trailed = limit_orders.sl_level(m) if kind == "sl" else (limit_orders.tgt_level(m), False)
                    if lvl is not None:
                        sub += f" \u00b7 at {lvl:g}" + (" (trailed)" if trailed else "")
                ui.label(sub).classes("text-xs " + ("text-amber-5" if m else "opacity-60"))
            else:
                ui.label(f"Set {what}").classes("text-blue-5 font-medium cursor-pointer") \
                    .on("click", lambda: rule_dialog(f"Add {what}", kind, None, on_set, allow_none=True))
                ui.label(f"Default {d_val:g} {d_type} (Setup)").classes("text-xs opacity-60")


def trail_dialog(name, r, cur=None, on_save=None, note=None):
    """Trailing SL popup: ON/OFF, Type, Trigger, Point, Gap. Default: the card's own (before ordering). With
    on_save: a placed order's app-watched trailing SL (cur = its values)."""
    order_mode = on_save is not None
    cur = cur or engine.row_trail(name, r)
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 380px"):
        ui.label("App trailing SL for this order" if order_mode else "Trailing SL for this card").classes("text-lg font-bold")
        on = ui.switch("Trailing SL", value=cur["on"])
        with ui.column().classes("w-full gap-1") as fields:
            typ = ui.select(["LTP", "POINT"], value=cur["type"], label="Trail type").classes("w-full")
            with ui.row().classes("w-full no-wrap gap-2"):
                trig = ui.number("Trigger", value=cur["trigger"], min=0, step=1).classes("flex-1")
                point = ui.number("Point", value=cur["point"], min=0, step=1).classes("flex-1")
                gap = ui.number("Gap", value=cur["gap"], min=0, step=1).classes("flex-1")
            ui.label("Once the high reaches entry + Trigger, SL = high \u2212 Gap (in steps of Point); it never moves "
                     "down. Gap = Trigger moves the SL to cost at the trigger.").classes("text-xs opacity-70")
        fields.bind_visibility_from(on, "value")
        sl_rule = exit_rules.parse((r or {}).get("c_sl"))
        ui.label(note or ("Watched by the app for this limit order (no change after placing)."
                          if (r or {}).get("entry_mode") == "limit" else
                          "Moves this order's exchange SL up (modify), checked on the live LTP every 2 s.")
                 ).classes("text-xs opacity-70")

        def save():
            if on.value:
                try:
                    t, pt, g = float(trig.value), float(point.value), float(gap.value)
                except (TypeError, ValueError):
                    ui.notify("Enter Trigger, Point and Gap.", type="negative")
                    return
                if min(t, pt, g) <= 0:
                    ui.notify("Trigger, Point and Gap must be above 0.", type="negative")
                    return
                if g > t:
                    ui.notify("Gap must not be larger than Trigger.", type="negative")
                    return
                trail = {"on": True, "type": typ.value, "trigger": t, "point": pt, "gap": g}
            else:
                trail = {**cur, "on": False}
            if order_mode:
                on_save(trail)
            else:
                engine.set_watch_trail(name, r["id"], trail)
            dlg.close()

        def use_setup():
            if order_mode:
                on_save(None)
            else:
                engine.set_watch_trail(name, r["id"], None)
            dlg.close()
        with ui.row().classes("w-full justify-between"):
            if order_mode:
                ui.button("Remove", on_click=use_setup).props("flat" + ("" if cur.get("on") else " disable"))
            else:
                ui.button("Use Setup", on_click=use_setup).props("flat" + ("" if r.get("c_trail") else " disable"))
            with ui.row().classes("gap-2"):
                ui.button("Close", on_click=dlg.close).props("color=red")
                ui.button("Save", on_click=save)
    dlg.props("persistent")
    dlg.open()


def card_trail_box(name, r, locked):
    """Trailing box: the card's own trailing SL or Setup's; click to change until ordered. With a CANDLE SL the
    trail moves the exchange SL order. Once ordered: the trail can be added / changed while the order is open."""
    sl_rule = exit_rules.parse(r.get("c_sl"))
    order = engine.row_order(r) if locked else None
    own = bool(r.get("c_trail"))
    with card_box("Trailing", "edit" if not locked and own else None):
        m = limit_orders.parse(order.get("mg")) if order else None
        if m:                                         # limit order: the app's own trailing SL (editable, PR #42)
            t = m.get("trail")
            v = ui.label(f"{t['trigger']:g} / {t['gap']:g}" if t else "OFF").classes(
                "text-lg font-bold" + ("" if t else " opacity-70"))
            lvl, trailed = limit_orders.sl_level(m)
            ui.label("app-watched" + (f" \u00b7 SL {lvl:g}" if t and trailed else
                                      f" \u00b7 step {t['step']:g}" if t else "")).classes("text-xs text-amber-5")
            if engine.order_rule_editable(order):
                cur = {"on": True, "type": "LTP", "trigger": t["trigger"], "point": t["step"], "gap": t["gap"]} if t \
                    else {**engine.setup_trail(name), "on": True}
                v.classes("cursor-pointer")
                v.on("click", lambda _e=None, cur=cur: trail_dialog(
                    name, r, cur=cur, on_save=lambda tr: engine.set_order_trail(order["id"], tr),
                    note="Watched by the app on the live LTP. This replaces the order's trailing SL."))
            return
        if order:                                     # app trail if set, else what was actually sent
            prm = trade_tracker._params(order)
            t = engine.app_trail(order)
            editable = engine.order_rule_editable(order)
            if t:
                v = ui.label(f"{t['trigger']:g} / {t['gap']:g}").classes("text-lg font-bold")
                lvl = engine.trail_level(t, trade_tracker._f(order.get("entry")))
                ui.label("app-watched" + (f" \u00b7 SL {lvl:g}" if lvl is not None else f" \u00b7 step {t['step']:g}")
                         + (" \u00b7 hit" if t.get("done") else "")).classes("text-xs text-amber-5")
            elif prm["trail"] and prm["is_sl"]:
                v = ui.label(f"{prm['trigger']:g} / {prm['gap']:g}").classes("text-lg font-bold")
                ui.label(f"as sent \u00b7 step {prm['step']:g}").classes("text-xs opacity-60")
            else:
                v = ui.label("OFF").classes("text-lg font-bold opacity-70")
                ui.label("as sent" + (" \u00b7 click to add" if editable else "")).classes("text-xs opacity-60")
            if editable:
                cur = {"on": True, "type": "LTP", "trigger": t["trigger"], "point": t["step"], "gap": t["gap"]} if t \
                    else {**engine.row_trail(name, r), "on": True}
                v.classes("cursor-pointer")
                v.on("click", lambda _e=None, cur=cur: trail_dialog(
                    name, r, cur=cur, on_save=lambda tr: engine.set_order_trail(order["id"], tr),
                    note="Replaces this order's trailing SL: it moves the exchange SL order (never down)."))
            return
        candle_sl = bool(sl_rule and sl_rule["type"] == "CANDLE")
        tr = engine.row_trail(name, r)
        src = "this card" if own else "Setup"
        if tr["on"]:
            v = ui.label(f"{tr['trigger']:g} / {tr['gap']:g}").classes("text-lg font-bold")
            sub = f"{src} \u00b7 {tr['type']} \u00b7 step {tr['point']:g}" + (" \u00b7 app (CANDLE SL)" if candle_sl else "")
        else:
            v = ui.label("OFF" if own or locked else "Set Trailing").classes(
                "text-lg font-bold opacity-70" if own or locked else "text-blue-5 font-medium")
            sub = f"{src}" + ("" if own else " (OFF)")
        if not locked:
            v.classes("cursor-pointer")
            v.on("click", lambda: trail_dialog(name, r))
        with ui.row().classes("items-center gap-1 no-wrap"):
            ui.label(sub).classes("text-xs " + ("text-amber-5" if own else "opacity-60"))
            if own and not locked:
                ui.button(icon="close", on_click=lambda: engine.set_watch_trail(name, r["id"], None)) \
                    .props("flat dense round size=xs color=red").tooltip("Back to Setup")


def edit_dialog(name, r):
    """Edit a WAITING row: level(s) and timeframe (pair: CE price, PE price, buffer)."""
    pair = r.get("kind") == "pair"
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 360px"):
        ui.label("Edit " + (CommonWatch.pair_title(r) if pair else f"{r['strike']} {r['opt']}")).classes("text-lg font-bold")
        price = ui.number("CE price" if pair else "Common price", value=r["ce_price"] if pair else r["price"],
                          min=0, step=0.05).classes("w-full")
        pe_price = ui.number("PE price", value=r.get("pe_price"), min=0, step=0.05).classes("w-full")
        buffer = ui.number("Buffer", value=r.get("buffer"), min=0, step=0.05).classes("w-full")
        pe_price.set_visibility(pair)
        buffer.set_visibility(pair)
        tf = ui.radio({1: "1 min", 3: "3 min", 5: "5 min"}, value=r["tf"]).props("inline dense")
        trig = trig_toggle(r.get("trig_on") or "close")
        ui.label("Saving restarts the check from the candles before the row was added (arming starts again)."
                 ).classes("text-xs opacity-70")

        def save():
            try:
                p = float(price.value)
                pp = float(pe_price.value) if pair and pe_price.value else p
                b = float(buffer.value or 0) if pair else None
            except (TypeError, ValueError):
                ui.notify("Enter the price.", type="negative")
                return
            if p <= 0 or pp <= 0 or (b is not None and b < 0):
                ui.notify("Prices above 0; buffer 0 or more.", type="negative")
                return
            if engine.edit_watch(name, r["id"], int(tf.value), p, pp, b, trig.value):
                dlg.close()
            else:
                ui.notify("Only a WAITING row can be edited.", type="warning")
                dlg.close()
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Close", on_click=dlg.close).props("color=red")
            ui.button("Save", on_click=save)
    dlg.open()


def cancel_warning(o):
    """Orange box: other orders on the same option that AlgoDelta's cancel_order (no order id) may also hit."""
    side = engine.cancel_side_effects(o)
    if not side:
        return
    with ui.card().classes("w-full bg-orange-10 text-orange-2"):
        ui.label("cancel_order has no order id, so it may also cancel the pending orders of:").classes("text-sm font-medium")
        for g in side:
            m = limit_orders.parse(g.get("mg"))
            ui.label("\u2022 " + engine.order_label(g) + (" (its limit order)" if m else
                                                         " (its SL / Target orders at the broker!)")).classes("text-sm")


def confirm_delete(name, r):
    order = engine.row_order(r)
    open_limit = order is not None and engine.entry_cancellable(order)
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 440px"):
        ui.label("Delete this row?").classes("text-lg font-bold")
        if open_limit:
            ui.label("Its Limit / Stop-limit order is still open at the exchange: deleting cancels it first (by its "
                     "order ID; other orders on this option are not touched).").classes("text-sm text-orange-4")
            if order.get("mg"):
                cancel_warning(order)
        elif r.get("order_status") == "PENDING":
            ui.label("Its pending entry stops (nothing was sent yet).").classes("text-sm opacity-70")
        ui.label("An order already placed stays in Order Details"
                 + (" and its exchange SL / app Target keep running." if order is not None and order.get("bk")
                    else " and its app SL / Target keep running." if order is not None and order.get("mg") else ".")) \
            .classes("text-sm opacity-70")

        async def go():
            dlg.close()
            await engine.delete_watch(name, r["id"])
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Cancel order & delete" if open_limit else "Delete", on_click=go).props("color=red")
    dlg.open()


def cancel_limit_dialog(order_id):
    """Confirm AlgoDelta cancel_order for an open limit order (or retry a failed cancel)."""
    o = next((x for x in engine.order_log if x["id"] == order_id), None)
    if o is not None and engine._bk(o):
        return cancel_entry_dialog(o)
    m = limit_orders.parse(o.get("mg")) if o else None
    if not limit_orders.cancellable(m):
        return
    retry = m["state"] == "CANCEL FAILED"
    sym = market_utils.option_symbol(o["index"], o.get("expiry") or engine.monitors[o["index"]].expiry, o["strike"], o["opt"])
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 440px"):
        ui.label(("Retry cancel " if retry else "Cancel limit order ") + sym).classes("text-lg font-bold")
        ui.label(f"Limit {m['price']:.2f} \u00b7 {engine.order_label(o)} \u00b7 {o.get('status')}").classes("text-sm")
        if limit_orders.held(m):
            ui.label(f"{m['filled']} already filled at {m['fill']:.2f}: only the rest is cancelled; the filled part "
                     "stays protected by the app SL / Target.").classes("text-sm text-amber-5")
        cancel_warning(o)
        ui.label("Sends AlgoDelta cancel_order. Live: the Upstox order book confirms it (else CANCEL FAILED and "
                 "Retry). Dry run: cancelled at once.").classes("text-xs opacity-70")

        async def go():
            dlg.close()
            await engine.cancel_limit(o["id"])
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Close", on_click=dlg.close).props("flat")
            ui.button("Retry cancel" if retry else "Cancel order", icon="cancel", on_click=go).props("color=orange-9")
    dlg.open()


def cancel_entry_dialog(o):
    """Confirm the cancel of a broker Limit / Stop-limit entry (PR 4): by its order ID, this order only."""
    st = engine._bk(o)
    if not engine.entry_cancellable(o):
        return
    sym = market_utils.option_symbol(o["index"], o.get("expiry") or engine.monitors[o["index"]].expiry, o["strike"], o["opt"])
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 440px"):
        ui.label("Cancel order " + sym).classes("text-lg font-bold")
        ui.label(f"{positions.entry_text(st)} \u00b7 {engine.order_label(o)} \u00b7 {o.get('status')}").classes("text-sm")
        if st.get("fill") is not None:
            ui.label(f"{st['fill_qty']} already filled at {st['fill']:.2f}: only the rest is cancelled; the filled part "
                     "keeps its exchange SL.").classes("text-sm text-amber-5")
        ui.label("Cancels this order by its order ID within about 2 s. Other orders on this option are not touched.") \
            .classes("text-xs opacity-70")

        async def go():
            dlg.close()
            await engine.cancel_limit(o["id"])
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Close", on_click=dlg.close).props("flat")
            ui.button("Cancel order", icon="cancel", on_click=go).props("color=orange-9")
    dlg.open()


def square_off_dialog(order_id):
    """Confirm a manual square-off; lists every open order on the same option (closed together)."""
    o = next((x for x in engine.order_log if x["id"] == order_id), None)
    if not o:
        return
    retry = str(o.get("sq") or "").startswith("FAILED")
    group = engine.open_on_option(o["index"], o["strike"], o["opt"], exclude_id=o["id"])
    lots = trade_tracker._f(o.get("qty")) or 1
    units = lots * config.INDEXES.get(o["index"], {}).get("lot_size", 1)
    entry, ltp = trade_tracker._f(o.get("entry")), trade_tracker._f(o.get("ltp_live"))
    pnl = (ltp - entry) * units if entry is not None and ltp is not None else None
    sym = market_utils.option_symbol(o["index"], o.get("expiry") or engine.monitors[o["index"]].expiry, o["strike"], o["opt"])
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 440px"):
        ui.label(("Retry square off " if retry else "Square off ") + sym).classes("text-lg font-bold")
        with ui.grid(columns=2).classes("w-full gap-x-6 gap-y-1 text-sm"):
            for k, v in (("Order", engine.order_label(o)), ("Mode", o.get("status")), ("Quantity", f"{lots:g} lot(s)"),
                         ("Entry", f"{entry:.2f}" if entry is not None else "--"),
                         ("LTP", f"{ltp:.2f}" if ltp is not None else "--"),
                         ("Approx. P&L", f"\u20b9{pnl:,.2f}" if pnl is not None else "--")):
                ui.label(k).classes("opacity-60")
                ui.label(str(v)).classes("font-medium")
        ui.label("Exits THIS order only: its exchange SL order becomes MARKET (or a market SELL without one) within "
                 "about 2 s. Other orders on the same option stay. Not blocked by Pause algo or the daily limit."
                 + (" Dry run: the paper broker fills it at the LTP." if o.get("status") == "DRY RUN" else "")
                 ).classes("text-xs opacity-70")

        async def go():
            dlg.close()
            await engine.square_off(o["id"])
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Retry square off" if retry else "Square off", icon="logout", on_click=go).props("color=red")
    dlg.open()


def square_off_all_dialog():
    opens = [o for o in engine.order_log if engine.is_open(o)]
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 440px"):
        ui.label("Square off ALL open orders").classes("text-lg font-bold")
        if not opens:
            ui.label("No open orders.").classes("text-sm opacity-70")
            ui.button("Close", on_click=dlg.close).props("flat")
            dlg.open()
            return
        for o in opens:
            ui.label(f"\u2022 {o['index']} {o['strike']}{o['opt']} \u2014 {engine.order_label(o)} ({o['status']})") \
                .classes("text-sm")
        ui.label("One squareoff_position per option. Not blocked by Pause algo or the daily limit.") \
            .classes("text-xs opacity-70")

        async def go():
            dlg.close()
            res = await engine.square_off_all()
            ui.notify(f"Square off all: {len(res)} option(s) sent.", type="warning")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Square off all", icon="logout", on_click=go).props("color=red")
    dlg.open()


def confirm_same_option(name, r, then):
    """Warn before ordering an option that already has an open order (one square-off closes both)."""
    if r.get("kind") == "pair" and not r.get("opt"):
        return then()
    opens = engine.open_on_option(name, r.get("strike"), r.get("opt"))
    if not opens:
        return then()
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 420px"):
        ui.label(f"{r['strike']}{r['opt']} is already open").classes("text-lg font-bold")
        for o in opens:
            ui.label("\u2022 " + engine.order_label(o)).classes("text-sm")
        ui.label("Each order keeps its own SL and exits on its own.").classes("text-sm opacity-70")

        async def go():
            dlg.close()
            res = then()
            if hasattr(res, "__await__"):
                await res
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Continue", on_click=go).props("color=orange-9")
    dlg.open()


def entry_price_box(name, r, pending):
    """Entry Price before ordering: empty = market at the click; a price = wait for the LTP (arrow = direction)."""
    price, direction = r.get("entry_price"), r.get("entry_dir")
    if pending:
        p = r["pending"]
        with ui.row().classes("items-center gap-1 no-wrap"):
            ui.label(f"{p['price']:.2f}").classes("text-lg font-bold text-amber-5")
            ui.icon("south" if p["dir"] == "below" else "north").classes("text-amber-5")
        ui.label(f"waiting \u00b7 armed {p['armed_at']}").classes("text-xs text-amber-5")
        return
    if r.get("source") != "manual":                   # Same strike / Pair / \u2b50: Strategy or Limit
        tg = ui.toggle({"strategy": "Strategy", "limit": "Limit", "stoplimit": "Stop-limit"}, value=card_mode(r)).props("dense no-caps size=sm")
        def mode_changed(e, tg=tg):
            if e.value == card_mode(r):
                return
            if e.value == "stoplimit":                # ask the trigger / limit first (PR #45)
                stoplimit_dialog(name, r, on_cancel=lambda: tg.set_value(card_mode(r)))
                return
            engine.set_watch_mode(name, r["id"], e.value if e.value != "strategy" else None)
        tg.on_value_change(mode_changed)
    if engine.is_stop(r):                             # stop-limit (SL BUY at the exchange): trigger + limit
        t0, p0 = engine.stop_prices(r)
        lbl = ui.label(f"{t0:.2f} \u2192 {p0:.2f}" if t0 else "leg \u2192 +%g" % config.STOP_LIMIT_GAP) \
            .classes("text-base font-bold cursor-pointer underline decoration-dotted whitespace-nowrap") \
            .tooltip("Click to change the trigger / limit")
        lbl.on("click", lambda: stoplimit_dialog(name, r))
        ui.label("STOP-LIMIT at \u26a1: trigger \u2192 limit" if t0 else "STOP-LIMIT at the fired leg's price") \
            .classes("text-xs text-amber-5")
        return
    if r.get("entry_mode") == "limit":                # limit order: a price, no direction
        manual = r.get("source") == "manual"
        inp = ui.number(value=price, min=0.05, step=0.05, placeholder="" if manual else "level") \
            .props("dense borderless input-class=text-center").classes("w-20 text-lg font-bold")

        def save_limit(_e=None):
            if not engine.set_watch_entry(name, r["id"], inp.value):
                ui.notify("A limit card needs a price above 0.", type="warning")
                inp.set_value(r.get("entry_price"))
        inp.on("blur", save_limit)
        inp.on("keydown.enter", save_limit)
        ui.label("LIMIT order at \u26a1" if price or manual else "LIMIT at the fired leg's price") \
            .classes("text-xs text-amber-5")
        return
    if not direction and price:
        ltp = engine.row_live_ltp(name, r)
        direction = ("below" if price <= ltp else "above") if ltp is not None else None
    with ui.row().classes("items-center gap-0 no-wrap"):
        inp = ui.number(value=price, min=0, step=0.05, placeholder="Market") \
            .props("dense borderless input-class=text-center").classes("w-20 text-lg font-bold")

        def save(_e=None):
            engine.set_watch_entry(name, r["id"], inp.value, r.get("entry_dir"))
        inp.on("blur", save)
        inp.on("keydown.enter", save)
        if price:
            def flip():
                engine.set_watch_entry(name, r["id"], price, "above" if direction == "below" else "below")
            ui.button(icon="south" if direction == "below" else "north" if direction == "above" else "swap_vert",
                      on_click=flip).props("flat dense round size=sm color=amber") \
                .tooltip("At or below (dip) / at or above (breakout). Click to flip." +
                         ("" if r.get("entry_dir") else " Automatic from the LTP."))
    sign = {"below": "\u2264 ", "above": "\u2265 "}.get(direction, "")
    ui.label("market at \u26a1" if not price else f"wait LTP {sign}price, then market").classes("text-xs opacity-60")


def order_card(name, r, place, live):
    """One Order Management row as a card. `live` collects callbacks that refresh LTP / Entry / P&L / status."""
    order = engine.row_order(r)
    state = card_state(r, order)
    done = (r.get("order_status") or "") in ORDER_DONE
    pending = r.get("order_status") == "PENDING"
    locked = done
    border = {"FAILED": "#b91c1c", "TRIGGERED": "#15803d"}.get(state)
    with ui.card().classes("w-full gap-2").style(f"border: 1px solid {border}" if border else ""):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            with ui.row().classes("items-center gap-2"):
                ui.label(card_symbol(name, r)).classes("text-md font-bold mono")
                ui.badge("Buy").props("color=green-9 text-color=green-2")
                ui.badge("INTRADAY").props("color=grey-8")
                if r.get("source") != "manual":
                    ui.badge(f"{r['tf']} min").props("color=grey-8")
                if r.get("source") == "star":
                    ui.badge(f"\u2b50 {r.get('trig_time', '')}").props("color=amber-9")
                if r.get("source") == "manual":
                    ui.badge("Manual entry").props("color=indigo-7")
                if r.get("kind") == "pair" and r.get("opt"):
                    ui.badge(f"{r['opt']} fired · {CommonWatch.pair_title(r)}").props("color=grey-8")
                if r.get("auto") == "cross":
                    ui.badge(f"BEP cross {r.get('trig_time', '')}").props("color=teal-8") \
                        .tooltip("Added automatically by BEP Support > BEP Pair cross (prices = BEPs, buffer 0)")
            with ui.row().classes("items-center gap-2 no-wrap"):
                chip = ui.badge(state).props(f"color={_chip_colour(state)}").classes("text-sm px-2 py-1")
                if order is not None and (engine.is_open(order) or str(order.get("sq") or "").startswith("FAILED")) \
                        and r["status"] != "exited":
                    retry = str(order.get("sq") or "").startswith("FAILED")
                    ui.button("Retry square off" if retry else "Square off", icon="logout",
                              on_click=lambda: square_off_dialog(order["id"])) \
                        .props("dense unelevated no-caps color=red-8").tooltip("Exit this position now (market)")
                m = limit_orders.parse(order.get("mg")) if order is not None else None
                if order is not None and engine.entry_cancellable(order):
                    ui.button("Retry cancel" if m and m["state"] == "CANCEL FAILED" else "Cancel order",
                              icon="cancel", on_click=lambda: cancel_limit_dialog(order["id"])) \
                        .props("dense unelevated no-caps color=orange-9") \
                        .tooltip("Cancel this order's unfilled Limit / Stop-limit entry (by its order ID)")
                if pending:
                    ui.button("Cancel pending", icon="block",
                              on_click=lambda: engine.cancel_watch_entry(name, r["id"])) \
                        .props("dense unelevated no-caps color=orange-9").tooltip("Stop waiting for the entry price")
                else:
                    can_place = r["status"] == "triggered" and not done
                    b = ui.button(icon="bolt", on_click=lambda _e, i=r["id"]: place(i)) \
                        .props("dense unelevated color=" + ("green" if can_place else "grey-8"))
                    b.tooltip("Send a STOP-LIMIT order: trigger %.2f, limit %.2f" % engine.stop_prices(r)
                              if engine.stop_prices(r)[0] else
                              f"Send a LIMIT order at {engine.limit_price(r):.2f}" if r.get("entry_mode") == "limit"
                              and engine.limit_price(r) else "Send a LIMIT order" if r.get("entry_mode") == "limit" else
                              f"Arm: send when LTP reaches {r['entry_price']:.2f}" if r.get("entry_price")
                              else "Place order now (market)")
                    b.set_enabled(can_place)
                ui.button(icon="close", on_click=lambda: confirm_delete(name, r)) \
                    .props("dense unelevated color=red").tooltip("Delete row")
                can_edit = r["status"] == "watching" and r.get("source") != "star" and not r.get("order_status")
                e = ui.button(icon="edit", on_click=lambda: edit_dialog(name, r)) \
                    .props("dense unelevated color=" + ("primary" if can_edit else "grey-8"))
                e.tooltip("Edit level / timeframe")
                e.set_enabled(can_edit)

        with ui.row().classes("w-full no-wrap items-stretch overflow-x-auto"):
            entry_lbl = None
            editable = not done and not pending and r["status"] not in ("failed", "exited")
            with card_box("Entry (filled)" if done else "Entry Price", "edit" if editable else None):
                if done or r["status"] in ("failed", "exited"):
                    entry_lbl = ui.label("--").classes("text-lg font-bold")
                else:
                    entry_price_box(name, r, pending)
            with card_box("Quantity"):
                if locked:
                    ui.label(str(trade_tracker._f(order.get("qty")) and int(trade_tracker._f(order.get("qty")))
                                 if order else engine.row_qty(name, r))).classes("text-lg font-bold")
                else:
                    q = ui.number(value=engine.row_qty(name, r), min=1, step=1, precision=0) \
                        .props("dense borderless input-class=text-center").classes("w-16 text-lg font-bold")
                    q.on("blur", lambda _e, q=q: q.value and engine.set_watch_qty(name, r["id"], int(q.value)))
                    q.on("keydown.enter", lambda _e, q=q: q.value and engine.set_watch_qty(name, r["id"], int(q.value)))
                ui.label("lots").classes("text-xs opacity-60")
            with card_box("P&L"):
                pnl_lbl = ui.label("--").classes("text-lg font-bold")
                pts_lbl = ui.label("").classes("text-xs opacity-70")
            with card_box("LTP"):
                ltp_lbl = ui.label("--").classes("text-lg font-bold whitespace-pre-line text-center leading-tight")
            gap_row = None
            if r.get("source") == "manual":
                with card_box(level_name(r)):
                    mt = manual_type(r)
                    ui.label(mt).classes("text-lg font-bold")
                    ui.label({"Limit": "SL / Target by the app",
                              "Stop-limit": f"at LTP \u2265 {r.get('entry_trigger') or 0:.2f}, limit "
                                            f"{r.get('entry_price') or 0:.2f} \u00b7 SL / Target by the app",
                              "Price": "waits for the price, then market",
                              "Market": "market order at \u26a1"}[mt]).classes(
                        "text-xs " + ("text-amber-5" if mt in ("Limit", "Stop-limit") else "opacity-60"))
            elif r.get("auto") == "cross":                 # BEP Pair cross row: each leg's BEP and live LTP - BEP
                with card_box("BEP"):
                    gap_row = []
                    for o in ("CE", "PE"):
                        with ui.row().classes("items-center gap-2 no-wrap"):
                            ui.label(f"{o} {r[o.lower() + '_price']:.2f}").classes("text-base font-bold whitespace-nowrap")
                            gap_row.append(ui.label("").classes("text-xs font-bold whitespace-nowrap"))
                    ui.label(f"{r['opt']} closed above · {r['tf']} min · buffer {r['buffer']:g}").classes("text-xs opacity-60")
            else:
                with card_box(level_name(r)):
                    with ui.row().classes("items-center gap-1 no-wrap"):
                        ui.icon("show_chart").classes("text-sm")
                        if r.get("kind") == "pair" and r["pe_price"] != r["ce_price"]:
                            ui.label(f"CE {r['ce_price']:g} · PE {r['pe_price']:g}") \
                                .classes("text-base font-bold whitespace-nowrap")
                        else:
                            ui.label(f"{(r['ce_price'] if r.get('kind') == 'pair' else r['price']):.2f}") \
                                .classes("text-lg font-bold")
                        ui.icon("arrow_upward").classes("text-sm")
                    ui.label(("LTP above (live)" if r.get("trig_on") == "ltp" else "close above") + f" · {r['tf']} min"
                             + (f" · buffer {r['buffer']:g}" if r.get("kind") == "pair" else "")).classes("text-xs opacity-60")
            card_rule_box(name, r, "tgt", locked)
            card_rule_box(name, r, "sl", locked)
            card_trail_box(name, r, locked)

        ui.separator()
        with ui.row().classes("w-full items-center gap-6 text-sm"):
            with ui.row().classes("items-center gap-1 no-wrap"):
                ui.icon("schedule").classes("text-blue-5")
                try:
                    created = datetime.fromisoformat(r["added_at"]).strftime("%Y-%m-%d %H:%M:%S")
                except (KeyError, ValueError):
                    created = "--"
                ui.label(f"Created {created}").classes("opacity-80")
            ui.label(CommonWatch.status_text(r)).classes("opacity-80")
            if r.get("order_status"):
                ui.label(f"Order: {r['order_status']}" + (f" · {order.get('time', '')}" if order else "")).classes("opacity-80")
            msg_lbl = ui.label("").classes("text-amber-5")

    def refresh():
        o = engine.row_order(r)
        ltp_lbl.set_text(card_ltp_text(name, r).replace(" · ", "\n"))   # pair: CE and PE on two lines
        if gap_row is not None:
            for lbl, (opt, gap) in zip(gap_row, cross_bep_gaps(name, r)):
                lbl.set_text("--" if gap is None else f"{gap:+.2f} {'above' if gap >= 0 else 'below'}")
                lbl.classes(remove="text-green-5 text-red-5 opacity-60")
                lbl.classes(add="opacity-60" if gap is None else ("text-green-5" if gap >= 0 else "text-red-5"))
        mo = limit_orders.parse(o.get("mg")) if o else None
        if entry_lbl is not None:
            entry = o.get("entry") if o else None
            if trade_tracker._f(entry) is None and limit_orders.held(mo):
                entry = mo["fill"]                      # live: order-book fill until the trade book has it
            bst = engine._bk(o) if o else None
            if trade_tracker._f(entry) is None and bst and bst.get("fill") is not None:
                entry = bst["fill"]
            entry_lbl.set_text(_num_text(entry) if o else "--")
        msg_lbl.set_text((f"Stop-limit {mo['trigger']:.2f} \u2192 {mo['price']:.2f}: " if mo and mo.get("trigger") else
                          f"Limit {mo['price']:.2f}: " if mo else "") + (mo.get("msg", "") if mo else ""))
        pnl = trade_tracker._f(o.get("pnl")) if o else None
        pts = trade_tracker._f(o.get("points")) if o else None
        live = engine.open_pnl(o) if pnl is None else None   # open position: (LTP - Entry) x qty, every 2 s
        shown = pnl if pnl is not None else (live[1] if live else None)
        pnl_lbl.set_text(f"{shown:,.2f}" if shown is not None else ("0" if o else "--"))
        pnl_lbl.classes(remove="text-green-5 text-red-5")
        if shown is not None:
            pnl_lbl.classes(add="text-green-5" if shown >= 0 else "text-red-5")
        pts_lbl.set_text(f"{pts:+.2f} pts (exited {_num_text(o.get('exit'))})" if pts is not None else
                         (f"{live[0]:+.2f} pts \u00b7 open" if live else ""))
        st = card_state(r, o)
        chip.set_text(st)
        chip.props(f"color={_chip_colour(st)}")
    refresh()
    live.append(refresh)


def card_list(name, rows, place, live, empty_text):
    if not rows:
        ui.label(empty_text).classes("text-sm opacity-70")
        return
    for r in sorted(rows, key=lambda x: x["id"], reverse=True):     # newest first
        order_card(name, r, place, live)


def om_header(title, help_text):
    with ui.row().classes("w-full items-center justify-between"):
        ui.label(f"Order Management — {title}").classes("text-2xl font-bold")
        sw = ui.switch("Compact view", value=OM_VIEW["compact"]).props("dense")

        def flip(ev):
            OM_VIEW["compact"] = bool(ev.value)
            engine.bump("watch")                          # redraw the lists in the new view
        sw.on_value_change(flip)
    ui.label(help_text + " Nothing is placed automatically: use ⚡ Place order.").classes("text-xs opacity-70")


def sl_warning(rule, ref, ref_name) -> str:
    """Warning text when an SL rule (LTP / CANDLE) is at or above the reference price (entry / LTP / limit),
    i.e. it would exit at once or at the next candle close. "" when fine (POINT is always below the entry)."""
    if not rule or ref is None or rule.get("type") not in ("LTP", "CANDLE") or rule.get("value") is None:
        return ""
    v = float(rule["value"])
    if v < ref:
        return ""
    when = (f"the next {rule.get('tf') or 5}-min candle close below {v:g} exits at once"
            if rule["type"] == "CANDLE" else "it exits at once")
    return f"SL {v:g} ({rule['type']}) is at or above the {ref_name} {ref:.2f}: {when}."


def confirm_warnings(title, lines, then, ok_label="Continue"):
    """Popup listing warnings; Continue runs `then` (sync or async)."""
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 460px"):
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.icon("warning").classes("text-2xl text-orange-5")
            ui.label(title).classes("text-lg font-bold")
        for line in lines:
            ui.label("\u2022 " + line).classes("text-sm")

        async def go():
            dlg.close()
            res = then()
            if hasattr(res, "__await__"):
                await res
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button(ok_label, on_click=go).props("color=orange-9")
    dlg.open()


def guarded_sl_set(kind, rule, entry, apply):
    """Setting an SL after placing: asks first when it is at or above the Entry (it would exit at once)."""
    warn = sl_warning(rule, entry, "Entry") if kind == "sl" else ""
    if warn:
        confirm_warnings("Check the stoploss", [warn], lambda: apply(rule), "Set anyway")
    else:
        apply(rule)


def make_place(name):
    """The card's \u26a1: market order now, or arm the entry price. Warns first when the option is already open."""
    async def place(row_id):
        r = engine.watches[name].get(row_id)
        if not r:
            return

        async def do():
            if r.get("entry_mode") == "limit":
                await engine.place_limit_order(name, row_id)
                return
            if r.get("entry_price"):
                if engine.row_live_ltp(name, r) is None and not r.get("entry_dir"):
                    await engine.quote_option(name, r["strike"], r["opt"])
                ok, msg = engine.arm_watch_entry(name, row_id)
                ui.notify(msg, type="positive" if ok else "warning")
                return
            await engine.place_watch_order(name, row_id)
        async def checked():
            res = confirm_same_option(name, r, do)
            if hasattr(res, "__await__"):
                await res
        ltp = engine.row_live_ltp(name, r)
        if ltp is None and r.get("strike") is not None:
            ltp = await engine.quote_option(name, r["strike"], r["opt"])
        limit = r.get("entry_mode") == "limit"
        lp = engine.limit_price(r) if limit else None

        async def guarded():
            """SL at / above the price you buy at, or the price already back below the level: ask first."""
            warns = []
            cfg = config.AUTO_ORDER[name]
            sl = exit_rules.parse(r.get("c_sl")) or {"type": cfg.get("sl_type"), "value": cfg.get("sl")}
            ref = lp if limit else (r.get("entry_price") or ltp)
            w = sl_warning(sl, ref, "limit price" if limit else ("entry price" if r.get("entry_price") else "LTP"))
            if w:
                warns.append(w)
            thr = float(cfg.get("warn_above_bep", 10) or 0)          # far above BEP (PR #43)
            bep_v = await engine.row_bep(name, r) if thr > 0 else None
            if bep_v is not None:
                if ltp is not None and ltp - bep_v > thr:
                    warns.append(f"LTP {ltp:.2f} is {ltp - bep_v:.2f} points above BEP {bep_v:.2f} (warning above "
                                 f"{thr:g}): late entry, bigger risk to the SL.")
                if limit and lp and lp - bep_v > thr:
                    warns.append(f"{'Stop-limit' if engine.is_stop(r) else 'Limit'} price {lp:.2f} is "
                                 f"{lp - bep_v:.2f} points above BEP {bep_v:.2f} (warning above {thr:g}).")
            lvl = r.get("price") if r.get("source") != "manual" and not engine.is_stop(r) else None
            if lvl and ltp is not None and ltp < float(lvl):     # (a stop-limit waits for the rise: no warning)
                warns.append(f"The price is already back below the {level_name(r)} {float(lvl):g} "
                             f"(LTP {ltp:.2f}): the setup may have failed.")
            if exit_rules.is_none(exit_rules.parse(r.get("c_tgt"))) and not engine.row_trail(name, r)["on"]:
                warns.append("Target is None and the trailing SL is OFF: this order exits only by the SL, a manual "
                             "square-off or 15:15.")                  # PR #48
            if warns:
                confirm_warnings("Check before placing", warns, checked, "Place anyway")
                return
            await checked()
        if limit:
            if not lp:
                ui.notify("Set the limit price first.", type="warning")
                return
            trigger = engine.stop_prices(r)[0]
            if trigger:                                  # stop-limit: the trigger must still be above the LTP
                if ltp is not None and ltp >= trigger:
                    ui.notify(f"Stop-limit: LTP {ltp:.2f} is already at or above the trigger {trigger:.2f}. "
                              "Raise the trigger, or use " + ("Limit / Market." if r.get("source") == "manual" else "Strategy / Limit."), type="warning", multi_line=True)
                    return
            elif ltp is not None and lp > ltp:           # BUY limit above the LTP: fills at once (PR #36)
                limit_above_ltp(name, r, lp, ltp, guarded)
                return
            if not trigger and not limit_sanity_ok(lp, ltp, guarded):
                return
        await guarded()
    return place


def limit_above_ltp(name, r, price, ltp, then):
    """A BUY limit above the LTP fills at once near the LTP. Asks first; the card can switch to Stop-limit
    (an SL BUY: waits at the exchange until the LTP rises to the price)."""
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 460px"):
        ui.label("This limit fills immediately").classes("text-lg font-bold")
        ui.label(f"Limit {price:.2f} is above the LTP {ltp:.2f}: a BUY limit means \"at {price:.2f} or cheaper\", "
                 f"so it fills at once at about {ltp:.2f}.").classes("text-sm")
        ui.label(f"To buy when the price RISES to {price:.2f}, use Stop-limit (trigger {price:.2f} \u2192 limit "
                 f"{price + config.STOP_LIMIT_GAP:.2f}).").classes("text-sm text-orange-4")

        async def go():
            dlg.close()
            await then()

        def switch():
            dlg.close()
            if engine.set_stoplimit(name, r["id"], price, round(price + config.STOP_LIMIT_GAP, 2)):
                ui.notify(f"Switched to Stop-limit: trigger {price:.2f} \u2192 limit "
                          f"{price + config.STOP_LIMIT_GAP:.2f}. Check it on the card, then \u26a1.", type="positive")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Switch to Stop-limit", on_click=switch).props("color=primary")
            ui.button("Send limit anyway", on_click=go).props("color=orange-9")
    dlg.open()


def limit_sanity_ok(price, ltp, then) -> bool:
    """True when the limit price is near the LTP. Otherwise asks first (typo guard) and calls `then` on Continue."""
    if ltp is None or ltp <= 0 or abs(price - ltp) / ltp * 100 < config.LIMIT_SANITY_PCT:
        return True
    with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 420px"):
        ui.label("Check the limit price").classes("text-lg font-bold")
        ui.label(f"Limit {price:.2f} is {abs(price - ltp) / ltp * 100:.0f}% "
                 f"{'above' if price > ltp else 'below'} the LTP {ltp:.2f}.").classes("text-sm")
        if price > ltp:
            ui.label("A BUY limit above the LTP fills at once, near the LTP.").classes("text-sm text-orange-4")

        async def go():
            dlg.close()
            await then()
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Send anyway", on_click=go).props("color=orange-9")
    dlg.open()
    return False


def om_list(name, title, icon, draw):
    """One Order Management list for an index: cards (default) or the compact table. Redrawn when the
    engine's watch rows change; LTP / Entry / P&L / status on the cards refresh every second without a redraw."""
    card_title(f"{name} — {title}", icon)
    holder = ui.column().classes("w-full gap-3")
    live = []

    place = make_place(name)

    def render():
        holder.clear()
        live.clear()
        with holder:
            if OM_VIEW["compact"]:
                with ui.card().classes("w-full"):
                    draw(place, live, True)
            else:
                draw(place, live, False)
    when_changed(["watch"], render)

    def tick():
        for fn in list(live):
            try:
                fn()
            except Exception:                             # the card was redrawn meanwhile
                pass
    ui.timer(1.0, tick)


def tf_radio():
    with ui.column().classes("gap-0"):
        ui.label("Timeframe").classes("text-xs opacity-70")
        return ui.radio({1: "1 min", 3: "3 min", 5: "5 min"}, value=5).props("inline dense")


def form_ltp(name, legs):
    """Add-form LTP box (Same strike / Pair): legs() -> [(strike value, "CE"/"PE"), ...]. Shows e.g.
    "CE 120.50 · PE 98.20". Returns the refresh coroutine (strike blur / leg change / the \u21bb button)."""
    with ui.column().classes("gap-0 items-start"):
        with ui.row().classes("items-center gap-0 no-wrap"):
            ui.label("LTP").classes("text-xs opacity-70")
            btn = ui.button(icon="refresh").props("flat dense round size=xs color=primary").tooltip("Refresh LTP")
        lbl = ui.label("--").classes("text-lg font-bold whitespace-nowrap")

    async def refresh(_e=None):
        parts = []
        for k, opt in legs():
            try:
                k = int(k)
            except (TypeError, ValueError):
                continue
            v = await engine.quote_option(name, k, opt)
            parts.append(f"{opt} {v:.2f}" if v is not None else f"{opt} --")
        lbl.set_text(" \u00b7 ".join(parts) or "--")
    btn.on_click(refresh)
    ui.timer(0.5, refresh, once=True)                # first value for the default strike
    return refresh


TRIG_ON = {"close": "Candle close", "ltp": "Live LTP"}


def trig_toggle(value="close"):
    """Trigger on: Candle close (default) or Live LTP (within ~2 s of crossing the price)."""
    with ui.column().classes("gap-0"):
        ui.label("Trigger on").classes("text-xs opacity-70")
        t = ui.toggle(TRIG_ON, value=value).props("dense no-caps")
        t.tooltip("Candle close: an armed leg's candle closes above the price. Live LTP: its LTP goes above the "
                  "price (Pair: the opponent's LTP below price - buffer). A leg is armed once it trades at or "
                  "below its price after the row is added.")
        return t


def centre_strike(name):
    mon = engine.monitors[name]
    return mon.strikes[len(mon.strikes) // 2] if mon.strikes else None


def same_strike_page():
    om_header("Same strike", "Alerts when a leg's candle closes ABOVE the common price (crossing up from at or below), "
              "once. Red = closed back below after triggering (no alert). Rows added before 09:15 use the first "
              "candle's open as the previous price.")
    for name in engine.active():
        step = config.INDEXES[name]["strike_interval"]
        with ui.card().classes("w-full"):
            card_title(f"{name} — add same strike", "add_circle")
            with ui.row().classes("items-end gap-6"):
                strike = ui.number("Strike", value=centre_strike(name), step=step, precision=0).classes("w-32")
                with ui.column().classes("gap-0"):
                    ui.label("Leg").classes("text-xs opacity-70")
                    leg = ui.radio(["Both", "CE", "PE"], value="Both").props("inline dense")
                price = ui.number("Common price", value=None, min=0, step=0.05).classes("w-36")
                ltp_refresh = form_ltp(name, lambda strike=strike, leg=leg: [
                    (strike.value, o) for o in (("CE", "PE") if leg.value == "Both" else (leg.value,))])
                strike.on("blur", ltp_refresh)
                leg.on_value_change(ltp_refresh)
                tf = tf_radio()
                trig = trig_toggle()
                omode = order_mode_toggle()

                def add(name=name, step=step, strike=strike, leg=leg, price=price, tf=tf, omode=omode, trig=trig):
                    try:
                        k, p = int(strike.value), float(price.value)
                    except (TypeError, ValueError):
                        ui.notify("Enter a strike and a common price.", type="negative")
                        return
                    if k <= 0 or k % step or p <= 0:
                        ui.notify(f"Strike must be a multiple of {step}; common price above 0.", type="negative")
                        return
                    om = omode.value if omode.value != "strategy" else None
                    engine.add_watch(name, k, leg.value, p, int(tf.value), om, trig.value)
                    ui.notify(f"Watching {k} {leg.value} at {p:g} ({tf.value} min), "
                              + (f"STOP-LIMIT order: trigger {p:g}, limit {p + config.STOP_LIMIT_GAP:g}." if om == "stoplimit"
                                 else f"LIMIT order at {p:g}." if om else "strategy order."), type="positive")
                ui.button("Add", icon="add", on_click=add)

        def draw(place, live, compact, name=name):
            rows = om_rows("same", name)
            if compact:
                section(f"Same strike ({len(rows)})", "price_change", rows,
                        "No same-strike rows yet - add one above.", place, name)
            else:
                card_list(name, rows, place, live, "No same-strike rows yet - add one above.")
        om_list(name, "watched strikes", "visibility", draw)


def pair_page():
    om_header("Pair (CE vs PE)", "Entry: a leg closes above its price while the opponent closes below price − "
              "buffer (the leg must have been at or below its price first). After you order, the opponent closing "
              "back at or above that line squares off the position (live) or records the exit (dry run).")
    for name in engine.active():
        step = config.INDEXES[name]["strike_interval"]
        with ui.card().classes("w-full"):
            card_title(f"{name} — add pair", "add_circle")
            with ui.row().classes("items-end gap-6"):
                p_ce = ui.number("CE strike", value=centre_strike(name), step=step, precision=0).classes("w-32")
                p_pe = ui.number("PE strike", value=centre_strike(name), step=step, precision=0).classes("w-32")
                p_price = ui.number("Common price", value=None, min=0, step=0.05).classes("w-36")
                p_pe_price = ui.number("PE price (if different)", value=None, min=0, step=0.05).classes("w-44")
                ltp_refresh = form_ltp(name, lambda p_ce=p_ce, p_pe=p_pe: [(p_ce.value, "CE"), (p_pe.value, "PE")])
                p_ce.on("blur", ltp_refresh)
                p_pe.on("blur", ltp_refresh)
                p_buffer = ui.number("Buffer (opponent below price − buffer)", value=1, min=0, step=0.05) \
                    .classes("w-64")
                tf = tf_radio()
                trig = trig_toggle()
                omode = order_mode_toggle()

                def add(name=name, step=step, p_ce=p_ce, p_pe=p_pe, p_price=p_price, p_pe_price=p_pe_price,
                        p_buffer=p_buffer, tf=tf, omode=omode, trig=trig):
                    try:
                        ce, pe, price = int(p_ce.value), int(p_pe.value), float(p_price.value)
                        pe_price = float(p_pe_price.value) if p_pe_price.value else price
                        buffer = float(p_buffer.value or 0)
                    except (TypeError, ValueError):
                        ui.notify("Enter the CE strike, PE strike and common price.", type="negative")
                        return
                    if min(ce, pe) <= 0 or ce % step or pe % step or price <= 0 or pe_price <= 0 or buffer < 0:
                        ui.notify(f"Strikes must be multiples of {step}; prices above 0; buffer 0 or more.",
                                  type="negative")
                        return
                    om = omode.value if omode.value != "strategy" else None
                    engine.add_pair(name, ce, pe, price, pe_price, buffer, int(tf.value), om, trig.value)
                    ui.notify(f"Watching pair {ce}CE / {pe}PE at {price:g}, "
                              + ("STOP-LIMIT order at the fired leg's price." if om == "stoplimit" else
                                 "LIMIT order at the fired leg's price." if om else "strategy order."), type="positive")
                ui.button("Add", icon="add", on_click=add)

        def draw(place, live, compact, name=name):
            rows = om_rows("pair", name)
            if compact:
                pair_section(rows, place, name)
            else:
                card_list(name, rows, place, live, "No pairs yet - add one above.")
        om_list(name, "watched pairs", "visibility", draw)


def star_page():
    om_header("BEP Support ⭐", "Rows are added automatically for a live first support of the day inside the ATM "
              "window (Level = BEP) and are ready to order. Red when the BEP Support board row fails. A 09:15 ⭐ is "
              "skipped if that side already has its opening order.")
    for name in engine.active():
        def draw(place, live, compact, name=name):
            rows = om_rows("star", name)
            empty = "No ⭐ yet - live first supports inside the ATM window appear here automatically."
            if compact:
                section(f"BEP Support ⭐ ({len(rows)})", "star", rows, empty, place, name)
            else:
                card_list(name, rows, place, live, empty)
        om_list(name, "first supports", "star", draw)


def manual_page():
    om_header("Manual Entry", "Pick a strike and place a BUY yourself - no trigger condition. Market = order at "
              "⚡ / Add & place. Price = the app waits until the LTP reaches your price (either way: ↓ a dip or "
              "↑ a breakout), then sends a market order. After the fill the Stoploss waits at the exchange (SL-M), "
              "Trailing moves it and the Target is watched by the app (it turns the SL into MARKET). Limit = a LIMIT "
              "BUY at this price OR CHEAPER (fills at once if the LTP is already below it). Stop-limit = an SL BUY "
              "that waits at the exchange until the LTP RISES to the trigger, then a limit at the limit price. "
              "After the fill they get the same exchange SL / trailing / target. Deleting a card cancels its open "
              "order (by its ID); unfilled orders are cancelled at 15:15.")
    for name in engine.active():
        step = config.INDEXES[name]["strike_interval"]
        with ui.card().classes("w-full"):
            card_title(f"{name} — new manual entry", "add_circle")
            with ui.row().classes("items-end gap-6"):
                strike = ui.number("Strike", value=centre_strike(name), step=step, precision=0).classes("w-32")
                with ui.column().classes("gap-0"):
                    ui.label("Option").classes("text-xs opacity-70")
                    opt = ui.radio(["CE", "PE"], value="CE").props("inline dense")
                lots = ui.number("Lots", value=config.AUTO_ORDER[name]["qty"], min=1, step=1, precision=0).classes("w-24")
                with ui.column().classes("gap-0"):
                    ui.label("Order").classes("text-xs opacity-70")
                    mode = ui.toggle({"price": "Price", "market": "Market", "limit": "Limit",
                                      "stoplimit": "Stop-limit"}, value="stoplimit").props("dense no-caps")   # default (PR #40)
                trigger = ui.number("Trigger (LTP rises to)", value=None, min=0, step=0.05).classes("w-40")
                trigger.bind_visibility_from(mode, "value", lambda v: v == "stoplimit")
                price = ui.number("Limit price", value=None, min=0, step=0.05).classes("w-32")
                price.bind_visibility_from(mode, "value", lambda v: v in ("price", "limit", "stoplimit"))
                mode.on_value_change(lambda e, price=price: price.props(
                    f'label="{"Limit price" if e.value in ("limit", "stoplimit") else "Entry price"}"'))

                def trigger_set(_e=None, trigger=trigger, price=price):     # limit = trigger + gap, editable
                    if trigger.value:
                        price.set_value(round(float(trigger.value) + config.STOP_LIMIT_GAP, 2))
                trigger.on("blur", trigger_set)
                with ui.column().classes("gap-0 items-start"):
                    ui.label("LTP").classes("text-xs opacity-70")
                    ltp_lbl = ui.label("--").classes("text-lg font-bold")
                with ui.column().classes("gap-0 items-start"):     # PR #48: the strike's BEP, shown only (no check)
                    ui.label("BEP").classes("text-xs opacity-70")
                    with ui.row().classes("items-baseline gap-1 no-wrap"):
                        bep_lbl = ui.label("--").classes("text-lg font-bold")
                        gap_lbl = ui.label("").classes("text-sm")

            async def get_ltp(_e=None, name=name, strike=strike, opt=opt, ltp_lbl=ltp_lbl, price=price, mode=mode,
                              bep_lbl=bep_lbl, gap_lbl=gap_lbl):
                try:
                    k = int(strike.value)
                except (TypeError, ValueError):
                    return None
                v = await engine.quote_option(name, k, opt.value)
                ltp_lbl.set_text(f"{v:.2f}" if v is not None else "--")
                b = await engine.row_bep(name, {"source": "manual", "strike": k, "opt": opt.value})
                bep_lbl.set_text(f"{b:.2f}" if b is not None else "--")
                d = round(v - b, 2) if v is not None and b is not None else None
                gap_lbl.set_text("" if d is None else f"({d:+.2f})")
                gap_lbl.classes(replace="text-sm " + ("" if d is None else "text-green-5" if d >= 0 else "text-red-5"))
                if v is not None and not price.value and mode.value in ("price", "limit"):   # Stop-limit: the
                    price.set_value(v)                    # trigger must be above the LTP, so you type it
                return v
            strike.on("blur", get_ltp)
            opt.on_value_change(get_ltp)

            def validated(name=name, step=step, strike=strike, opt=opt, lots=lots, mode=mode, price=price,
                          trigger=trigger):
                try:
                    k, q = int(strike.value), int(lots.value)
                except (TypeError, ValueError):
                    ui.notify("Enter the strike and lots.", type="negative")
                    return None
                if k <= 0 or k % step or q < 1:
                    ui.notify(f"Strike must be a multiple of {step}; lots 1 or more.", type="negative")
                    return None
                p = t = None
                if mode.value == "stoplimit":
                    try:
                        t = float(trigger.value)
                    except (TypeError, ValueError):
                        ui.notify("Enter the trigger price.", type="negative")
                        return None
                if mode.value in ("price", "limit", "stoplimit"):
                    try:
                        p = float(price.value)
                    except (TypeError, ValueError):
                        ui.notify("Enter the price (or choose Market).", type="negative")
                        return None
                    if p <= 0:
                        ui.notify("Entry price must be above 0.", type="negative")
                        return None
                if t is not None and (t <= 0 or p < t):
                    ui.notify("Stop-limit: the limit price must be at or above the trigger.", type="negative")
                    return None
                return k, opt.value, q, p, ("limit" if mode.value in ("limit", "stoplimit") else None), t

            def add(name=name, validated=validated):
                v = validated()
                if v:
                    engine.add_manual(name, *v)
                    ui.notify(f"Manual entry card added: {v[0]}{v[1]}. Set Target / SL / Trailing on the card, "
                              "then ⚡.", type="positive")

            async def add_and_place(name=name, validated=validated):
                v = validated()
                if not v:
                    return
                r = engine.add_manual(name, *v)
                await make_place(name)(r["id"])
            with ui.row().classes("items-center gap-3"):
                ui.button("LTP", icon="refresh", on_click=get_ltp).props("flat dense")
                ui.button("Add card", icon="add", on_click=add)
                ui.button("Add & place", icon="bolt", on_click=add_and_place).props("color=green")
                ui.label("Add card lets you set Target / SL / Trailing first; Add & place uses the defaults.") \
                    .classes("text-xs opacity-60")
            ui.timer(0.5, get_ltp, once=True)              # LTP + BEP once when the page opens (PR #48)

        def draw(place, live, compact, name=name):
            rows = om_rows("manual", name)
            empty = "No manual entries yet - add one above."
            if compact:
                section(f"Manual Entry ({len(rows)})", "edit_note", rows, empty, place, name)
            else:
                card_list(name, rows, place, live, empty)
        om_list(name, "manual entries", "edit_note", draw)


# ====================================================================== Orders
def orders_page():
    with ui.row().classes("w-full items-center justify-between"):
        ui.label("Order Details").classes("text-2xl font-bold")
        with ui.row().classes("gap-2"):
            ui.button("Square off all", icon="logout", on_click=square_off_all_dialog).props("dense color=red-8")
            ui.button("CSV", icon="download", on_click=lambda: ui.download(
                export_utils.build_csv(list(engine.order_log), ORDER_EXPORT_COLUMNS),
                f"algodelta_orders_{datetime.now().strftime('%Y-%m-%d')}.csv")).props("dense outline")
            ui.button("XLSX", icon="download", on_click=lambda: ui.download(
                export_utils.build_xlsx(list(engine.order_log), ORDER_EXPORT_COLUMNS),
                f"algodelta_orders_{datetime.now().strftime('%Y-%m-%d')}.xlsx")).props("dense outline")
    summary = ui.label("").classes("text-sm")
    with ui.card().classes("w-full"):
        table = ui.table(columns=ORDER_COLUMNS, rows=[], row_key="id").classes("w-full").props("dense flat")
        table.add_slot("body-cell-status", '''
            <q-td key="status" :props="props">
                <q-badge :color="props.value === 'SENT' ? 'green' : props.value === 'DRY RUN' ? 'blue' : props.value === 'FAILED' ? 'red' : 'orange'">{{ props.value }}</q-badge>
            </q-td>''')
        for col in ("points", "pnl"):
            table.add_slot(f"body-cell-{col}", f'''
            <q-td key="{col}" :props="props" :class="typeof props.value === 'number' ? (props.value >= 0 ? 'text-green-6 text-weight-bold' : 'text-red-6 text-weight-bold') : (typeof props.row.open_{col} === 'number' ? (props.row.open_{col} >= 0 ? 'text-green-6' : 'text-red-6') : '')">{{{{ typeof props.value === 'number' ? props.value : (typeof props.row.open_{col} === 'number' ? props.row.open_{col} + ' (open)' : props.value) }}}}</q-td>''')
        table.add_slot("body-cell-detail", '''
            <q-td key="detail" :props="props" style="max-width: 260px; white-space: normal; word-break: break-word">{{ props.value }}</q-td>''')
    ui.label("LIVE (SENT) orders: Entry / Exit are the real fills from today's Upstox trade book (refreshed every "
             f"{config.TRADES_REFRESH_SECONDS} s). DRY RUN orders: Entry = LTP at the signal; Exit is simulated from "
             "1-minute candles with the order's Target / SL / trailing SL (SL first if both hit in one minute; "
             "closed at 15:15 if still open). Captured Points = Exit \u2212 Entry. LIMIT orders (Manual Entry): "
             "Entry = the fill; the SL / Target / trailing SL are watched by the app on the live LTP "
             "(square-off when hit, once).").classes("text-xs opacity-70")

    table.add_slot("body-cell-rules", '''
        <q-td key="rules" :props="props" style="white-space: normal; max-width: 220px">
            <q-btn v-if="props.row.rules_edit" dense flat no-caps size="sm" color="primary" icon="tune"
                   :label="props.row.rules_text || 'Set SL / Target'" @click="() => $parent.$emit('rules', props.row.id)" />
            <span v-else class="text-caption">{{ props.row.rules_text }}</span>
        </q-td>''')

    def open_rules(e):
        rid = e.args
        r = next((o for o in engine.order_log if o["id"] == rid), None)
        if not r or not engine.order_rule_editable(r):
            ui.notify("This order can no longer be changed.", type="warning")
            return
        cfg = config.AUTO_ORDER.get(r["index"], {})
        with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 460px"):
            ui.label(f"{r['index']} {r['strike']}{r['opt']} \u2014 SL / Target").classes("text-lg font-bold")
            lim = bool(r.get("mg")) or bool(engine._bk(r))
            ui.label("A change REPLACES this order's SL / Target. LTP / POINT SL: the exchange SL order is moved; "
                     "CANDLE: watched by the app on candle closes (the max SL stays at the exchange); Target: watched "
                     "by the app, at the target the SL order becomes MARKET.").classes("text-xs opacity-70")

            def setter(kind):
                def on_set(rule):
                    dlg.close()
                    guarded_sl_set(kind, rule, trade_tracker._f(r.get("entry")),
                                   lambda rl: engine.set_order_custom(rid, kind, rl))
                return on_set
            cur_rule = (lambda k: json.dumps(engine.order_rule(r, k)) if engine.order_rule(r, k) else "") if lim \
                else (lambda k: r.get("c_" + k))
            rule_toggle("SL", "sl", cur_rule("sl"), "none" if lim else f"Common {cfg.get('sl', '')} {cfg.get('sl_type', '')}",
                        False, setter("sl"))
            rule_toggle("Target", "tgt", cur_rule("tgt"), "none" if lim else "Common (as sent)", False, setter("tgt"),
                        allow_none=lim)
            t = engine.order_trail(r)

            def open_trail():
                dlg.close()
                cur = {"on": True, "type": "LTP", "trigger": t["trigger"], "point": t["step"], "gap": t["gap"]} if t \
                    else {**engine.setup_trail(r["index"]), "on": True}
                trail_dialog(r["index"], {}, cur=cur, on_save=lambda tr: engine.set_order_trail(rid, tr),
                             note="This replaces the order's trailing SL (it moves the exchange SL order).")
            with ui.row().classes("items-center gap-2"):
                ui.label("Trailing: " + ((f"Trail {t['trigger']:g}/{t['gap']:g} (app)" if t else "none (app)") if lim
                                         else (engine.trail_text(r) or "none (app)"))).classes("text-sm")
                ui.button("Set trailing" if not t else "Change", icon="trending_up", on_click=open_trail) \
                    .props("dense flat no-caps color=primary")
            ui.button("Close", on_click=dlg.close).props("flat")
        dlg.open()
    table.on("rules", open_rules)

    table.add_slot("body-cell-action", '''
        <q-td key="action" :props="props" style="white-space: nowrap">
            <q-btn v-if="props.row.cx_action" dense unelevated no-caps size="sm" color="orange-9" icon="cancel"
                   :label="props.row.cx_action" @click="() => $parent.$emit('cancellimit', props.row.id)" class="q-mr-xs" />
            <q-btn v-if="props.row.sq_action" dense unelevated no-caps size="sm" color="red-8" icon="logout"
                   :label="props.row.sq_action" @click="() => $parent.$emit('squareoff', props.row.id)" />
            <span v-else class="text-caption">{{ props.row.sq_text }}</span>
        </q-td>''')
    table.on("squareoff", lambda e: square_off_dialog(e.args))
    table.on("cancellimit", lambda e: cancel_limit_dialog(e.args))

    def render():
        for r in engine.order_log:
            parts = [f"{'SL' if k == 'sl' else 'Tgt'} {exit_rules.describe(exit_rules.parse(r.get('c_' + k)))}"
                     for k in ("sl", "tgt") if exit_rules.parse(r.get("c_" + k))]
            m = limit_orders.parse(r.get("mg"))
            if m:                                       # limit order: state + the rules the app watches
                parts = [(limit_orders.state_text(m) or f"FILLED {m['filled']} @ {m['fill']:.2f}"),
                         "App " + (limit_orders.describe(m) or "no SL / Target")] + parts
                if m.get("exit"):
                    parts.append(m["exit"])
            st = engine._bk(r)
            if st:                                      # broker position: where its SL is now
                parts.append(positions.summary(st))
            if engine.trail_text(r):
                parts.append(engine.trail_text(r))
            r["rules_text"] = " \u00b7 ".join(parts) + (f" \u2192 {r['c_done']}" if r.get("c_done") else "")
            r["cx_action"] = ("Retry cancel" if m and m["state"] == "CANCEL FAILED" else "Cancel order") \
                if engine.entry_cancellable(r) else ""
            r["rules_edit"] = engine.order_rule_editable(r)
            sq = str(r.get("sq") or "")
            r["sq_action"] = "Retry square off" if sq.startswith("FAILED") and trade_tracker._f(r.get("exit")) is None \
                else ("Square off" if engine.is_open(r) else "")
            r["sq_text"] = f"Squared off {sq}" if sq else ""
        for r in engine.order_log:                    # open positions: (LTP - Entry) x qty, worked out locally
            live = engine.open_pnl(r)
            r["open_points"], r["open_pnl"] = (live if live else (None, None))
        table.rows = list(reversed(engine.order_log))
        table.update()
        s = engine.pnl_summary()
        summary.set_text(f"{s['orders']} orders · {s['closed']} closed · {s['wins']} won · "
                         f"points {s['points']:+.2f} · P&L ₹{s['pnl']:,.2f}"
                         + (f" · open {s['open']}: P&L ₹{s['open_pnl']:,.2f}" if s["open"] else ""))
    when_changed(["orders"], render)


# ====================================================================== Setup (once a day)
def setup_page():
    ui.label("Setup").classes("text-2xl font-bold")
    ui.label("Needed once a day. Everything here is saved on this computer and comes back after a restart "
             "(the Upstox token only for the same day); the app always starts in Dry run.").classes("text-xs opacity-70")

    with ui.row().classes("w-full gap-4 items-stretch"):
        # ---- Upstox
        with ui.card().classes("flex-1 min-w-[320px]"):
            card_title("Upstox", "key")
            token = ui.input("Upstox access token", password=True, password_toggle_button=True).classes("w-full")
            conn = ui.label(engine.connection_text).classes("text-sm")

            async def connect():
                conn.set_text("Testing...")
                ok, text = await engine.connect(token.value)
                conn.set_text(text)
                ui.notify(text, type="positive" if ok else "negative")
            ui.button("Save & test connection", icon="link", on_click=connect).classes("w-full")
            ui.label("A verified token is saved on this computer for today only, so a restart the same day "
                     "reconnects automatically.").classes("text-xs opacity-70")
            ui.button("Forget saved token", icon="delete", on_click=lambda: (engine.forget_token(),
                                                                             ui.notify("Saved token deleted"))) \
                .props("flat dense no-caps color=grey-6")
            ui.timer(1.0, lambda: conn.set_text(engine.connection_text))

        # ---- BEP files
        with ui.card().classes("flex-1 min-w-[320px]"):
            card_title("BEP files", "upload_file")
            import bep as _bep
            uploaders = {}
            for name in config.INDEXES:
                status = ui.label(_bep.status_text(name)).classes("text-sm opacity-80")
                result = ui.label(engine.bep_upload_text[name]).classes("text-sm")

                async def on_upload(e, name=name, status=status, result=result):
                    if hasattr(e, "file"):
                        data, fname = await e.file.read(), getattr(e.file, "name", "")
                    else:
                        data, fname = e.content.read(), getattr(e, "name", "")
                    engine.upload_bep(name, data, fname)
                    result.set_text(engine.bep_upload_text[name])
                    status.set_text(_bep.status_text(name))
                    try:
                        uploaders[name].reset()
                    except Exception:
                        pass
                up = uploaders[name] = ui.upload(on_upload=on_upload, auto_upload=True, max_files=1).props("accept=.csv").classes("hidden")
                ui.button(f"Upload {name} BEP CSV", icon="upload", on_click=lambda u=up: u.run_method("pickFiles")) \
                    .classes("w-full")

        # ---- Scanner
        with ui.card().classes("flex-1 min-w-[320px]"):
            card_title("Scanner", "radar")
            total = 2 * config.STRIKES_EACH_SIDE + 1
            slider = ui.slider(min=1, max=11, step=2, value=total).classes("w-full")
            ui.label().bind_text_from(slider, "value", lambda v: f"{int(v)} strikes (centre ± {(int(v) - 1) // 2})") \
                .classes("text-sm")
            ui.button("Save strike count", on_click=lambda: (engine.set_strike_count(int(slider.value)),
                                                             ui.notify("Strike count saved", type="positive"))) \
                .classes("w-full")
            interval = ui.number("Scan interval (seconds)", value=config.SCAN_INTERVAL_SECONDS, min=1, step=1,
                                 precision=0).classes("w-full")

            def save_interval():
                if not interval.value or int(interval.value) < 1:
                    ui.notify("Scan interval must be at least 1 second.", type="negative")
                    return
                engine.set_scan_interval(int(interval.value))
                ui.notify("Scan interval saved", type="positive")
            ui.button("Save scan interval", on_click=save_interval).classes("w-full")
            ui.button("Rescan from 09:15", icon="restart_alt", on_click=lambda: (engine.rescan(),
                                                                                ui.notify("Rescanning from 09:15"))) \
                .props("color=orange-8").classes("w-full")

        # ---- Trading controls
        with ui.card().classes("flex-1 min-w-[320px]"):
            card_title("Trading", "shield")
            ui.label("Dry run / Sandbox / Live: the order mode in Broker orders below (it starts in Dry run).") \
                .classes("text-sm")
            ui.number("Max orders/day", value=config.AUTO_ORDER_DAILY_LIMIT, min=1, precision=0, step=1,
                      on_change=lambda ev: engine.set_daily_limit(int(ev.value or 1))).classes("w-full")
            ui.label("Pause alerts / Pause algo are in the header.").classes("text-xs opacity-70")

    broker_card()
    for name in config.INDEXES:
        order_defaults_card(name)


_STATUS_COLOR = {"COMPLETE": "green", "CANCELLED": "grey-7", "REJECTED": "red", "OPEN": "blue",
                 "TRIGGER_PENDING": "orange-8", "SENT": "blue-grey", "UNKNOWN": "grey-6"}


def broker_card():
    """Setup > Broker orders: the order mode (Dry run / Sandbox / Live) of EVERY order, the Upstox order settings,
    the sandbox token and the Test order panel."""
    name = engine.active()[0] if engine.active() else next(iter(config.INDEXES))
    with ui.card().classes("w-full"):
        card_title("Broker orders (Upstox direct)", "account_balance")
        ui.label("The mode applies to every order: the 09:15 order, Order Management cards and the Test order "
                 "panel. Entry = market BUY; after the fill an SL-M SELL waits at the exchange (trailing moves it; "
                 "target / candle SL / square-off turn it into MARKET).").classes("text-xs opacity-70")
        with ui.row().classes("w-full gap-6 items-start"):
            # ---- mode + settings
            with ui.column().classes("flex-1 min-w-[300px] gap-2"):
                ui.select({"upstox": "Upstox"}, value=config.BROKER["broker"], label="Broker") \
                    .props("disable").classes("w-full").tooltip("Only Upstox for now (brokers/ adapters)")
                mode = ui.toggle(dict(brokers.MODE_LABELS), value=config.BROKER_MODE)
                mode_note = ui.label("").classes("text-xs opacity-70")

                def show_mode():
                    mode_note.set_text({
                        "dry": "Dry run: nothing is sent; a paper broker fills orders on the live LTP.",
                        "sandbox": "Sandbox: sent to api-sandbox.upstox.com with the sandbox token (no real money). "
                                   "It has no order status, so fills are assumed at the LTP.",
                        "live": "LIVE: real orders on your Upstox account. Needs the static IP registered in your "
                                "Upstox app (else error UDAPI1154)."}[config.BROKER_MODE])

                def on_mode(ev):
                    if ev.value == config.BROKER_MODE:
                        return
                    if ev.value != "live":
                        engine.set_broker_mode(ev.value)
                        show_mode()
                        return
                    with ui.dialog() as dlg, ui.card():
                        ui.label("Switch to LIVE? Every order (09:15, Order Management, Test order) will be a real "
                                 "Upstox order with real money.")
                        with ui.row():
                            ui.button("Cancel", on_click=dlg.close)
                            ui.button("Yes, live", on_click=lambda: (engine.set_broker_mode("live"), dlg.close())) \
                                .props("color=red")
                    dlg.on("hide", lambda: (mode.set_value(config.BROKER_MODE), show_mode()))
                    dlg.open()
                mode.on_value_change(on_mode)
                show_mode()
                mp = ui.number("Market protection % (market / SL-M orders; -1 = automatic)",
                               value=config.BROKER["market_protection"], min=-1, max=25, step=1, precision=0) \
                    .classes("w-full")
                algo = ui.input("Algo name (X-Algo-Name header; leave empty until Upstox gives one)",
                                value=config.BROKER["algo_name"]).classes("w-full")

                def save_settings():
                    v = int(mp.value if mp.value is not None else -1)
                    if v == 0 or v < -1 or v > 25:
                        ui.notify("Market protection must be -1 (automatic) or 1 to 25. 0 is rejected by Upstox "
                                  "for market orders.", type="negative")
                        return
                    ui.notify(engine.save_broker_settings(v, algo.value), type="positive")
                ui.button("Save broker settings", icon="save", on_click=save_settings).classes("w-full")

                ui.separator()
                sb_token = ui.input("Upstox sandbox token (30 days)", password=True,
                                    password_toggle_button=True).classes("w-full")
                sb_text = ui.label(engine.sandbox_token_text()).classes("text-xs opacity-70")

                def save_sb():
                    ui.notify(engine.save_sandbox_token(sb_token.value))
                    sb_token.set_value("")
                with ui.row().classes("w-full gap-2"):
                    ui.button("Save sandbox token", icon="key", on_click=save_sb)
                    ui.button("Forget", icon="delete", on_click=engine.forget_sandbox_token) \
                        .props("flat dense no-caps color=grey-6")
                ui.label("Create it at Upstox developer > Manage Apps > New Sandbox App > Generate.") \
                    .classes("text-xs opacity-70")

            # ---- test order form
            with ui.column().classes("flex-1 min-w-[320px] gap-2"):
                ui.label("Test order").classes("font-bold")
                ui.label("Place, modify, cancel and check one order through the current mode. Use 1 lot. "
                         "Needs the Upstox connection (option key and LTP).").classes("text-xs opacity-70")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    strike = ui.number("Strike", step=config.INDEXES[name]["strike_interval"], precision=0) \
                        .classes("flex-1")
                    opt = ui.toggle(["CE", "PE"], value="CE")
                    lots = ui.number("Lots", value=1, min=1, step=1, precision=0).classes("w-20")
                with ui.row().classes("w-full gap-2 items-center no-wrap"):
                    side = ui.toggle(["BUY", "SELL"], value="BUY")
                    otype = ui.select(["LIMIT", "SL", "SL-M", "MARKET"], value="LIMIT", label="Type").classes("w-28")
                    ltp_lbl = ui.label("LTP -").classes("text-sm opacity-80")

                    async def get_ltp():
                        if not strike.value:
                            return
                        v = await engine.quote_option(name, int(strike.value), opt.value)
                        ltp_lbl.set_text(f"LTP {v:.2f}" if v is not None else "LTP -")
                    ui.button(icon="refresh", on_click=get_ltp).props("flat round dense").tooltip("Fetch the LTP")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    price = ui.number("Limit price", step=0.05, format="%.2f").classes("flex-1")
                    trigger = ui.number("Trigger price", step=0.05, format="%.2f").classes("flex-1")

                def show_fields():
                    price.set_visibility(otype.value in ("LIMIT", "SL"))
                    trigger.set_visibility(otype.value in ("SL", "SL-M"))
                otype.on_value_change(lambda _e: show_fields())
                show_fields()

                async def do_place():
                    if not strike.value or not lots.value:
                        ui.notify("Enter the strike and lots.", type="warning")
                        return
                    if int(lots.value) > 1:
                        ui.notify("Test orders: 1 lot is enough.", type="warning")

                    async def go():
                        ok, text = await engine.test_place(name, int(strike.value), opt.value, int(lots.value),
                                                           side.value, otype.value, price.value, trigger.value)
                        ui.notify(text, type="positive" if ok else "negative")
                    if config.BROKER_MODE != "live":
                        await go()
                        return
                    with ui.dialog() as dlg, ui.card():
                        ui.label(f"Place a REAL {side.value} {otype.value} order for {int(lots.value)} lot(s) of "
                                 f"{int(strike.value)}{opt.value}?").classes("font-bold")
                        with ui.row():
                            ui.button("Cancel", on_click=dlg.close)

                            async def yes():
                                dlg.close()
                                await go()
                            ui.button("Place live order", on_click=yes).props("color=red")
                    dlg.open()
                ui.button("Place test order", icon="send", on_click=do_place).classes("w-full")

        # ---- test orders list
        ui.label("Test orders (this session)").classes("font-bold mt-2")
        box = ui.column().classes("w-full gap-1")

        def modify_dialog(t):
            with ui.context.client.layout, ui.dialog() as dlg, ui.card().style("width: 380px"):
                ui.label(f"Modify {t['id']} ({t['strike']}{t['opt']} {t['side']})").classes("font-bold")
                mt = ui.select(["LIMIT", "SL", "SL-M", "MARKET"], value=t["type"], label="Type").classes("w-full")
                mp_ = ui.number("Limit price", value=t["price"] or None, step=0.05, format="%.2f").classes("w-full")
                mtr = ui.number("Trigger price", value=t["trigger"] or None, step=0.05, format="%.2f") \
                    .classes("w-full")

                def vis():
                    mp_.set_visibility(mt.value in ("LIMIT", "SL"))
                    mtr.set_visibility(mt.value in ("SL", "SL-M"))
                mt.on_value_change(lambda _e: vis())
                vis()

                async def save():
                    dlg.close()
                    ok, text = await engine.test_modify(
                        t["id"], mt.value, mp_.value if mt.value in ("LIMIT", "SL") else None,
                        mtr.value if mt.value in ("SL", "SL-M") else None)
                    ui.notify(text, type="positive" if ok else "negative")
                with ui.row():
                    ui.button("Cancel", on_click=dlg.close)
                    ui.button("Modify", on_click=save).props("color=primary")
            dlg.open()

        async def act(fn, *args):
            ok, text = await fn(*args)
            ui.notify(text, type="positive" if ok else "negative")

        def render():
            box.clear()
            with box:
                if not engine.test_orders:
                    ui.label("None yet.").classes("text-sm opacity-60")
                for t in engine.test_orders:
                    same_mode = t["mode"] == config.BROKER_MODE
                    final = t["status"] in ("COMPLETE", "CANCELLED", "REJECTED")
                    with ui.row().classes("w-full items-center gap-2 text-sm no-wrap"):
                        ui.label(t["time"]).classes("opacity-60 w-16")
                        ui.badge(brokers.MODE_LABELS[t["mode"]]).props("outline color=grey-6")
                        ui.label(f"{t['side']} {t['qty']} {t['strike']}{t['opt']}").classes("w-36")
                        px = {"MARKET": "market", "LIMIT": f"limit {t['price']:.2f}",
                              "SL-M": f"SL-M trg {t['trigger']:.2f}",
                              "SL": f"SL trg {t['trigger']:.2f} lmt {t['price']:.2f}"}.get(t["type"], t["type"])
                        ui.label(px).classes("w-44")
                        ui.badge(t["status"].replace("_", " ")).props(f"color={_STATUS_COLOR.get(t['status'], 'grey')}")
                        if t["filled"]:
                            ui.label(f"{t['filled']} @ {t['avg']:.2f}")
                        ui.label(t["id"]).classes("opacity-60 text-xs")
                        ui.label(t["msg"] or "").classes("opacity-70 text-xs ellipsis flex-1")
                        if same_mode:
                            ui.button(icon="refresh", on_click=lambda t=t: act(engine.test_status, t["id"])) \
                                .props("flat round dense").tooltip("Status")
                            if not final:
                                ui.button(icon="edit", on_click=lambda t=t: modify_dialog(t)) \
                                    .props("flat round dense").tooltip("Modify")
                                ui.button(icon="close", on_click=lambda t=t: act(engine.test_cancel, t["id"])) \
                                    .props("flat round dense color=red").tooltip("Cancel")
                            if t["status"] == "COMPLETE" and config.BROKER_MODE != "sandbox":
                                ui.button(icon="logout", on_click=lambda t=t: act(engine.test_exit, t["id"])) \
                                    .props("flat round dense color=orange").tooltip("Exit the position of this tag")
            sb_text.set_text(engine.sandbox_token_text())
            if mode.value != config.BROKER_MODE:
                mode.set_value(config.BROKER_MODE)
                show_mode()
        when_changed(["broker"], render)


def order_defaults_card(name):
    """Setup > Order defaults: quantity, Target / SL / trail, per-type targets, 09:15 values and the exchange
    max SL for cards whose SL is CANDLE only (was "AlgoDelta order settings" before BEP Upstox PR 3)."""
    ad_cfg = config.AUTO_ORDER[name]
    with ui.card().classes("w-full"):
        card_title(f"{name} — Order defaults", "tune")
        with ui.row().classes("w-full gap-6 items-start"):
            with ui.column().classes("flex-1 min-w-[320px] gap-2"):
                ad_qty = ui.number("Quantity (lots)", value=ad_cfg["qty"], min=1, precision=0, step=1).classes("w-full")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    ad_tgt_type = ui.select(["POINT", "LTP"], value=ad_cfg.get("tgt_type", "POINT"), label="Target type").classes("flex-1")
                    ad_tgt = ui.number("Target value", value=ad_cfg["tgt"], min=0, step=1).classes("flex-1")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    ad_sl_type = ui.select(["POINT", "LTP"], value=ad_cfg.get("sl_type", "POINT"), label="Stop loss type").classes("flex-1")
                    ad_sl = ui.number("Stop loss value", value=ad_cfg["sl"], min=0, step=1).classes("flex-1")
                ad_candle_max = ui.number("Max SL for cards with a CANDLE SL: points below the fill (exchange, 0 = none)",
                                          value=ad_cfg.get("candle_max_sl", 17), min=0, step=1).classes("w-full") \
                    .tooltip("A card whose SL is CANDLE only (exit on a candle close) also gets an SL-M order at the "
                             "exchange this many points below the fill: it covers a fast fall inside a candle and the "
                             "app being down.")
                ad_trail = ui.switch("Trailing SL", value=ad_cfg.get("trail", False))
                with ui.row().classes("w-full gap-2 no-wrap"):
                    ad_trail_trigger = ui.number("Trigger", value=ad_cfg.get("trail_trigger", 20), min=0, step=1).classes("flex-1")
                    ad_trail_point = ui.number("Point", value=ad_cfg.get("trail_point", 1), min=0, step=1).classes("flex-1")
                    ad_trail_gap = ui.number("Gap", value=ad_cfg.get("trail_gap", 20), min=0, step=1).classes("flex-1")
                for w in (ad_trail_trigger, ad_trail_point, ad_trail_gap):
                    w.bind_enabled_from(ad_trail, "value")
                ui.label("Trailing: once the high reaches entry + Trigger, the exchange SL moves to high - Gap (in "
                         "steps of Point), never down.").classes("text-xs opacity-70")
            with ui.column().classes("flex-1 min-w-[320px] gap-2"):
                ui.label("Default target for new Order Management rows (each row can be changed until ordered; "
                         "type follows Target type; the 09:15 order uses Target value)").classes("text-xs opacity-70")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    ad_tgt_same = ui.number("Same strike", value=ad_cfg.get("tgt_same", ad_cfg["tgt"]), min=0, step=1).classes("flex-1")
                    ad_tgt_pair = ui.number("Pair", value=ad_cfg.get("tgt_pair", ad_cfg["tgt"]), min=0, step=1).classes("flex-1")
                    ad_tgt_star = ui.number("BEP Support \u2b50", value=ad_cfg.get("tgt_star", ad_cfg["tgt"]), min=0, step=1).classes("flex-1")
                    ad_tgt_manual = ui.number("Manual entry", value=ad_cfg.get("tgt_manual", ad_cfg["tgt"]), min=0, step=1).classes("flex-1")
                ad_max_above = ui.number("09:15 order: max points above BEP (skip at or above)",
                                         value=ad_cfg.get("max_above_bep", 15), min=0, step=1).classes("w-full")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    ad_open_entry = ui.number("09:15 entry above BEP +", value=ad_cfg.get("open_entry_above", 2),
                                              min=0, step=0.5).classes("flex-1") \
                        .tooltip("The 09:15 order fires at LTP >= BEP + this (0 = just above BEP)")
                    ad_open_sl = ui.number("09:15 SL: close below BEP -", value=ad_cfg.get("open_sl_below", 2),
                                           min=0, step=0.5).classes("flex-1") \
                        .tooltip("CANDLE SL watched by the app: exits when a candle CLOSES below BEP - this")
                    ad_open_tf = ui.select({1: "1 min", 3: "3 min", 5: "5 min"}, value=int(ad_cfg.get("open_sl_tf", 5)),
                                           label="SL candle").classes("w-28")
                ad_open_max = ui.number("09:15 max SL: points below the fill (exchange, 0 = none)",
                                        value=ad_cfg.get("open_max_sl", 17), min=0, step=1).classes("w-full") \
                    .tooltip("An SL-M order at the exchange from the fill (e.g. entry 152 - 17 = 135), on top of the "
                             "candle SL: exits on a fast fall or if the app is down.")
                ad_warn_bep = ui.number("Warn at \u26a1 when LTP is more than X points above BEP (0 = off)",
                                        value=ad_cfg.get("warn_above_bep", 10), min=0, step=1).classes("w-full") \
                    .tooltip("BEP Support \u2b50, BEP cross Pair and Manual Entry cards: 'Check before placing' "
                             "shows the LTP - BEP difference")
        ad_status = ui.label("").classes("text-sm")
        ui.button("Save", icon="save", on_click=lambda: save())

    def numbers():
        def num(v):
            return None if v is None else (int(v) if float(v) == int(v) else float(v))
        tgt, sl, qty = num(ad_tgt.value), num(ad_sl.value), num(ad_qty.value)
        if tgt is None or sl is None or tgt <= 0 or sl <= 0:
            return None, "Target and SL must be numbers above 0."
        if qty is None or qty < 1 or qty != int(qty):
            return None, "Quantity must be a whole number of at least 1."
        defaults = {k: num(w.value) for k, w in (("tgt_same", ad_tgt_same), ("tgt_pair", ad_tgt_pair),
                                                     ("tgt_star", ad_tgt_star), ("tgt_manual", ad_tgt_manual))}
        if any(v is None or v <= 0 for v in defaults.values()):
            return None, "Default targets (Same strike / Pair / BEP Support / Manual entry) must be numbers above 0."
        max_above = num(ad_max_above.value)
        if max_above is None or max_above <= 0:
            return None, "Max points above BEP must be a number above 0."
        warn_bep = num(ad_warn_bep.value)
        if warn_bep is None or warn_bep < 0:
            return None, "Warn above BEP must be a number of 0 or more (0 = off)."
        open_entry, open_sl = num(ad_open_entry.value), num(ad_open_sl.value)
        if open_entry is None or open_sl is None or open_entry < 0 or open_sl < 0:
            return None, "09:15 entry above BEP and SL below BEP must be numbers of 0 or more."
        open_max, candle_max = num(ad_open_max.value), num(ad_candle_max.value)
        if open_max is None or open_max < 0 or candle_max is None or candle_max < 0:
            return None, "The max SL values must be numbers of 0 or more (0 = none)."
        trail = bool(ad_trail.value)
        t_trig, t_point, t_gap = num(ad_trail_trigger.value), num(ad_trail_point.value), num(ad_trail_gap.value)
        if trail:
            if None in (t_trig, t_point, t_gap) or t_trig <= 0 or t_point <= 0 or t_gap <= 0:
                return None, "Trail Trigger, Point and Gap must be numbers above 0."
            if t_gap > t_trig:
                return None, "Gap must not be larger than Trigger (Gap = Trigger moves the SL to cost at the trigger)."
        return {"tgt": tgt, "sl": sl, "qty": int(qty), "tgt_type": ad_tgt_type.value, "sl_type": ad_sl_type.value,
                "trail": trail, "max_above_bep": max_above, **defaults,
                "open_entry_above": open_entry, "open_sl_below": open_sl, "open_sl_tf": int(ad_open_tf.value or 5),
                "open_max_sl": open_max, "candle_max_sl": candle_max, "warn_above_bep": warn_bep,
                "trail_trigger": t_trig if t_trig is not None else ad_cfg.get("trail_trigger", 20),
                "trail_point": t_point if t_point is not None else ad_cfg.get("trail_point", 1),
                "trail_gap": t_gap if t_gap is not None else ad_cfg.get("trail_gap", 20)}, ""

    def save():
        nums, err = numbers()
        if nums is None:
            ad_status.set_text("❌ " + err)
            return
        saved = engine.save_order_defaults(name, nums)
        trail_txt = (f"trail ON (trigger {nums['trail_trigger']}, point {nums['trail_point']}, gap {nums['trail_gap']})"
                     if nums["trail"] else "trail OFF")
        ad_status.set_text(f"✅ {saved} Target {nums['tgt']} ({nums['tgt_type']}; rows: same {nums['tgt_same']:g}, "
                           f"pair {nums['tgt_pair']:g}, \u2b50 {nums['tgt_star']:g}, manual {nums['tgt_manual']:g}), "
                           f"SL {nums['sl']} ({nums['sl_type']}), qty {nums['qty']}; {trail_txt}; "
                           f"CANDLE-SL cards: max SL {nums['candle_max_sl']:g} pts at the exchange; "
                           f"09:15 order at LTP >= BEP + {nums['open_entry_above']:g} and < BEP + {nums['max_above_bep']}, "
                           f"SL = {nums['open_sl_tf']}-min close below BEP - {nums['open_sl_below']:g} (app)"
                           + (f", max SL {nums['open_max_sl']:g} pts (exchange)." if nums['open_max_sl'] else "."))


# ====================================================================== Logs
def logs_page():
    with ui.row().classes("w-full items-center justify-between"):
        ui.label("Logs").classes("text-2xl font-bold")
        ui.button("Download", icon="download", on_click=lambda: ui.download(
            "\n".join(engine.log_lines).encode("utf-8"), f"bep_log_{datetime.now().strftime('%Y-%m-%d')}.txt")) \
            .props("dense outline")
    box = ui.log(max_lines=1000).classes("w-full mono text-sm").style("height: calc(100vh - 170px)")
    shown = {"n": 0}

    def push():
        lines = engine.log_lines
        if shown["n"] > len(lines):                       # the engine trimmed old lines
            box.clear()
            shown["n"] = 0
        for line in lines[shown["n"]:]:
            box.push(line)
        shown["n"] = len(lines)
    when_changed(["log"], push)


# ====================================================================== background engine (once for the whole app)
app.on_startup(engine.auto_connect)
engine.scan_timer = app.timer(config.SCAN_INTERVAL_SECONDS, engine.scan)
app.timer(config.OPENING_TICK_SECONDS, engine.opening)
app.timer(config.OPTION_LTP_SECONDS, engine.option_tick)

ui.run(title=config.APP_NAME, port=config.APP_PORT, reload=False, dark=True)
