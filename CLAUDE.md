# CLAUDE.md: BEP Upstox project context (read at the start of every session)

Repo: `ArunkumarEkambaram/BEP_Upstox` (default branch `master`, latest merge = PR #7; updated together with each feature PR).
Run: `python app.py` → http://localhost:**8084** (`config.APP_PORT`). Needs `nicegui>=3.1.0`, Python 3.14 on the user's PC. Cloud server: `deploy/README.md` (PR 5).
**What it is:** a copy of `ArunkumarEkambaram/BEP-AlgoDelta` (`main` at its PR #49, full history kept) that will send orders **directly to Upstox** (with a registered static IP, later on a cloud server) instead of through AlgoDelta. The BEP logic (Support board, ⭐, Pair cross, Replay, Order Management triggers, exit rules, Order Details) stays the same; the order path is replaced.
Related repos: `ArunkumarEkambaram/BEP-AlgoDelta` (the app in daily use until this one is proven live; port 8083; important BEP-logic fixes are ported to **both** apps until one is retired), `ArunkumarEkambaram/LTP_Crossing` (reference only, do not change).
**Since PR 3 there is no AlgoDelta code in this app:** every order goes through the broker layer (`brokers/`, `positions.py`) in the mode chosen on Setup › Broker orders: Dry run (default at every start) / Sandbox / Live. The BEP-AlgoDelta app is the safety backup.

## Working rules agreed with the user (same as BEP-AlgoDelta)
- **Always discuss requirements before writing code.** Ask questions with "Suggested:" answers and wait for the user's go-ahead.
- Work on a branch, open a PR, and merge **only when the user says "merge PR #n"**. This applies to **every** change, docs-only ones included (no direct pushes to the default branch; confirmed again after PR #17).
- During market hours, **don't push**. Keep work on a local branch until the user says so.
- **Never change the default branch directly** (no commits, pushes or merges to it on Claude's own initiative), especially near or during market hours. Every change, docs-only ones included, goes to a **new branch**. It's merged later, only when the user says "merge PR #n". The user's running app stays on the default branch it pulled. (Asked on 20 Nov 2026, just before the open. The user can allow an exception in chat, as for PR #23.)
- After merging, the user runs `git pull` and restarts the app.
- `__pycache__/`, `settings.json`, `upstox_token.json`, `sandbox_token.json`, `auth.json`, `.storage_secret`, `.nicegui/`, `.venv/`, `orders_*.csv` and `common_watch_*.json` are git-ignored. Never commit them.
- The cloud session can't delete GitHub branches (proxy 403). The user deletes merged branches, or turns on "Automatically delete head branches".
- This repo's default branch is `master` (created by the user on GitHub). PRs target `master`.
- **New features and fixes go into BOTH projects** (BEP_Upstox and BEP-AlgoDelta) until one is retired (user, 24 Nov): one PR per repo, and remind the user to merge both. Examples: NIFTY change % (PR 6 here, BEP-AlgoDelta PR #50), max loss per day (PR 7 here, BEP-AlgoDelta PR #52).

## BEP Upstox plan and status
- **PR 1 (copy and rename):** this repo = BEP-AlgoDelta `main` with its history, title "BEP Upstox" (`config.APP_NAME`), port 8084 (`config.APP_PORT`). Settings, token, orders and Order Management files are kept next to `app.py`, so a separate folder gives this app its own `settings.json`, `upstox_token.json`, `orders_*.csv` and `common_watch_*.json`. The same Upstox token works in both apps.
- **PR 2 (broker layer + Upstox adapter):** see "BEP Upstox decisions" below (decisions 3–4).
- **PR 3 (engine on the broker layer, AlgoDelta removed):** decision 6 below.
- **PR 4 (Limit / Stop-limit on the broker layer):** decision 7 below.
- **PR 5 (password login + cloud server scripts):** decision 8 below.
- **PR 6 (NIFTY change % in the header):** decision 9 below (BEP-AlgoDelta PR #50).
- **PR 7 (max loss per day, Dry run only, 09:15 exempt):** decision 10 below (BEP-AlgoDelta PR #52). All merged on 24 Nov 2026.
- **Work branch:** `claude/dazzling-keller-bxdbcj` (the same name in BEP-AlgoDelta), recreated from `master` for each PR; the user deletes it after a merge.
- **Next:** set up the server together with the user (outside market hours), Dry run / Sandbox on trading days (including the max loss: a low test limit, e.g. ₹100, to see the popup, the header badge and a BLOCKED PENDING card), then 1-lot live tests once the static IP is registered. Max loss for Sandbox / Live is to be decided after the dry-run test.

- The plan as agreed in BEP-AlgoDelta on 23 Nov 2026 (copied unchanged; "this app" there means BEP-AlgoDelta):
  - **PLANNED: a separate "BEP Upstox" app (Upstox direct + cloud), agreed 23 Nov 2026; start on 24 Nov (market holiday).** This app (AlgoDelta) stays unchanged and in use until BEP Upstox is proven live. **Target: switch before the AlgoDelta subscription ends (joined 15 Nov 2026, paid to about 14 Dec).**
    - **Estimate given (23 Nov):** about a quarter of the code changes (1,500–2,500 lines over 6–8 PRs). Unchanged: BEP Support, ⭐, Pair cross, Replay, Order Management triggers, exit rules, Order Details. Replaced: `order_bridge.py` → a new `upstox_orders.py` (place market / limit / SL, modify, cancel by order ID, exit a position, errors, rate limits). The biggest change is in `engine.py`: the app holds the SL / Target / trail itself instead of one `strategy_order`. `limit_orders.py` gets simpler (match by order ID). Square-off becomes per order. `app.py` Setup: the AlgoDelta section → Upstox order settings. Tokens: roughly 1 to 1.5× the PR #36–#45 session (about 3–6 M processed, mostly cached re-reads), spread over several sessions. Live 1-lot tests take the most time.
    - **Suggested answers (the user agreed to record them; confirm each before coding):**
      1. **(Done in PR 1, repo `BEP_Upstox`.) New GitHub repo** (e.g. `BEP-Upstox`), a copy of `main` with its history. The user creates the empty repo (no README); Claude adds it with `add_repo` and pushes the copy. Important BEP-logic fixes are ported to both apps until one is retired. (Not chosen: one repo with two entry points.)
      2. **(Done in PR 1.) Side by side:** port **8084**, its own `settings.json`, orders files and logs; the same Upstox token works in both.
      3. **Phase 1 before the static IP:** the Upstox order module, dry run, the flow fill → SL order at the exchange → trailing by modifying that SL, and a Setup switch. Live orders need the registered static IP; check whether the Upstox order-API **sandbox** can test the calls without it.
      4. **SL at the exchange** (SL sell order right after the fill; trailing modifies it), so it stays active if the PC / app is down. **Target watched by the app** (at target: cancel the SL, then sell). No resting Target sell next to the SL sell (both could fill and leave a short). Ask Upstox whether GTT (entry + target + SL) supports trailing.
      5. **Cloud parts built now, deployed after the replies:** password login, deploy script (`git pull` + restart), run on boot, IST clock. Suggested provider: AWS Lightsail Mumbai (static IP included).
      6. **PR order (new repo):** (1) copy and rename (port 8084, own files, title "BEP Upstox"); (2) Upstox order module + dry run; (3) strategy orders with SL at the exchange + trailing; (4) Limit / Stop-limit / cancel / square-off by order ID; (5) login + cloud deploy script. Same working rules as here.
      7. **New question (added 23 Nov):** hold the SL as a **plain SL order that the app modifies to trail**, or as an **Upstox GTT order with ENTRY + TARGET + STOPLOSS legs** (broker-held, with a trailing gap; minimum 10 % of the LTP-to-SL distance). Check the GTT docs first (e.g. can the entry leg be a stop-limit, how the trailing gap compares to our trigger / gap rule), then suggest an answer. A broker-held SL is safer than the app placing it after the fill (the app could crash in between).
      - **Design:** keep the order code in its own module (cleaner to test). **Upstox only:** no broker switch and no Dhan work (the user's decision, 23 Nov).
    - **Timeline (agreed 23 Nov; aim for 14 Dec, but the date must not force an untested switch):**
      - **Tue 24 Nov (holiday):** the user creates the empty repo (no README) and asks Upstox about static-IP registration and the tech-savvy undertaking. Claude: confirm the 7 questions, then PR 1 (copy and rename) and PR 2 (order module + dry run).
      - **Wed 25 – Fri 27 Nov (evenings):** PR 3 (market entry → SL at the exchange → trailing by modify) and PR 4 (Limit / Stop-limit / cancel / square-off by order ID). The user runs Wednesday's AlgoDelta test, creates the cloud server (e.g. Lightsail Mumbai) and registers its IP in Upstox **after market hours**.
      - **Sat 28 – Sun 29 Nov:** PR 5 (login + deploy script) and the server set up together.
      - **Mon 30 Nov – Fri 4 Dec:** the new app on the cloud in **dry run** next to the AlgoDelta app, then **1-lot live tests** one at a time (entry, SL order, trail modify, cancel, exit, 15:15).
      - **Mon 7 – Fri 11 Dec:** small size live on BEP Upstox, with AlgoDelta as the backup.
      - **Fri 11 Dec: decide.** Switch, or renew AlgoDelta for one month (about ₹944) if the live tests aren't clean.
      - Scope until the switch: only move the existing features to Upstox direct; no new features. Phase 1 of the old plan (cloud + AlgoDelta) is skipped.
    - **Upstox static IP (checked 23 Nov):**
      - Required for place / modify / cancel / multi-order / GTT (error UDAPI1154 otherwise). Market data, candles, order book and trade book work without it (per a community guide).
      - Set in the Upstox developer site: My Apps → the app → Static IPs (primary required, secondary optional), or the API `PUT /user/ip`. **Saving invalidates the access token**: log in again, after market hours.
      - A third-party guide says the IP can be changed only **once a week** (not confirmed by Upstox): copy it carefully. Ask Upstox about the algo name / ID ("X-Algo-Name").
      - AlgoDelta uses its own IP; ask AlgoDelta to confirm that changing the user's Upstox app IP doesn't affect it.
    - **Broker comparison (reference only, 23 Nov; the user has Dhan, Upstox and AngelOne accounts but uses Upstox only):** Dhan's **Super Order** = entry + target + SL + trailing jump in one broker-held order (SL exit as market, no trailing SL-limit); Dhan trading APIs free, data APIs ₹499/month; static IP needed, locked 7 days. Upstox: trading + data APIs free, GTT with entry / target / SL legs and a trailing gap, about ₹20 per order. Not pursued.
    - **Wednesday 25 Nov tests on this app (1 lot):** the 09:15 max SL (AlgoDelta should show a 17-point SL, no trail), the BEP on the Manual Entry form, Target None, and the Stop-limit test moved from 24 Nov.
- Older plan notes (20 Nov, "ON HOLD: cloud hosting / Upstox direct": setups A / B / C, cloud cost estimates) are in BEP-AlgoDelta's CLAUDE.md. Setup C (cloud + Upstox direct) is what this repo builds; Phase 1 (cloud + AlgoDelta) is skipped.

## BEP Upstox decisions (24 Nov 2026)
1. **Q3, Q4 confirmed (yes):** build before the static IP, with dry run, sandbox and a Setup switch. The SL is a real SL order at the exchange, placed right after the fill; trailing modifies it. The target is watched by the app; at the target the app **modifies the SL order to MARKET** (one sell order, no double sell / short). The same for the 5-min candle SL and the Pair opponent exit.
2. **Q7 (GTT): plain orders now, GTT maybe later** (after the 1-lot tests, as an option for market entries only). From the Upstox docs (checked 24 Nov):
   - GTT: `type` SINGLE / MULTIPLE, `product` I / D / MTF, legs ENTRY (ABOVE / BELOW / IMMEDIATE), TARGET and STOPLOSS (IMMEDIATE only), `trigger_price`, `trailing_gap` (STOPLOSS only, a fixed gap from the price, min 10 % of the LTP-to-SL distance, no trigger / step: it can't do decision 4's trail), `market_protection`. No limit-price field on a leg.
   - After the entry is OPEN, the legs' trigger prices can still be modified (only IMMEDIATE for the entry); the quantity can't. Intraday GTT lasts the day. Leg statuses: SCHEDULED, TRIGGERED, EXPIRED, OPEN, COMPLETED, CANCELLED, PENDING, FAILED, INACTIVE; each leg has an `order_id`.
   - Needs the static IP; **not** in the sandbox. Unclear: whether ABOVE / BELOW entries and the exits go as market or limit.
   - Why GTT came up: it holds SL + target at the broker from the moment of the fill (no gap after the fill, target held while the app is down). Upstox normal orders can't hold SL and target together.
3. **Broker interface (user, 24 Nov):** Upstox only for now, but the project must stay broker-independent so another broker is one new adapter later ("dependency injection", like .NET). `brokers/base.py` = the `Broker` Protocol (place, modify, cancel, status, exit_by_tag) + `OrderRequest` / `OrderResult` / `OrderStatus` + capability flags; `brokers/upstox.py`, `brokers/paper.py` (dry run is a broker); `brokers.create()` is the one place that picks the adapter. Market data stays on `upstox_client.py` (already injected into the monitors; a data interface can come later).
4. **PR 2 choices (all "suggested", 24 Nov):**
   - Setup card **Broker orders (Upstox direct)**: Broker (Upstox only), mode **Dry run / Sandbox / Live** (`config.BROKER_MODE`, not saved, starts in Dry run; Live asks to confirm), **Market protection** (`config.BROKER["market_protection"]`, default −1 = automatic, 1–25 %, 0 refused), **Algo name** (`X-Algo-Name` header, empty until Upstox says), the sandbox token (`sandbox_token.json`, git-ignored, 30 days) and the **Test order** panel (strike, CE/PE, lots, BUY/SELL, LIMIT / SL / SL-M / MARKET, price, trigger → place; per order: status, modify, cancel, exit by tag). Engine: `set_broker_mode`, `test_place` / `test_status` / `test_modify` / `test_cancel` / `test_exit`, `option_key`.
   - Upstox **v3** place / modify / cancel on `https://api-hft.upstox.com` (sandbox `https://api-sandbox.upstox.com`); status `GET /v2/order/details`, exit `POST /v2/order/positions/exit?tag=` on `https://api.upstox.com` (live only). Quantity in units (lots × 65), prices rounded to 0.05, `product` I, `validity` DAY, `slice` true.
   - **Tag** on every order: `BEP-<row id>` (exit `BEP-<row id>-X`), test orders `BEP-TEST-hhmmss` (max 40 chars; tags work for intraday positions only).
   - **Retries:** a place is never retried (a timeout is reported as "MAY have been placed: check the order book"); modify / cancel / status are retried once on a network error.
   - Errors: Upstox `{"status":"error","errors":[{"errorCode","message"}]}`; UDAPI1154 = static IP not registered, UDAPI1158 = market order without protection, UDAPI100050 = invalid token.
   - **Sandbox:** only place / modify / cancel (v2, v3) and multi place; token from Upstox developer → Manage Apps → New Sandbox App → Generate (valid 30 days, one sandbox app per user). Whether it needs the static IP isn't documented (from this cloud session it answered 401 "Invalid token" to a dummy token, i.e. reachable). No status there, so the panel can't show fills in Sandbox.
   - Tests: `python -m unittest discover -s tests -v` (`tests/test_brokers.py`, fake HTTP session, nothing sent).
5. **Doubts answered (24 Nov):**
   - 09:15 order on Upstox: same entry rules (app) → market BUY; max SL 17 = SL-M sell at fill − 17 at the exchange; 5-min close below BEP − 2 = the app modifies that SL order to MARKET; target and trail by the app.
   - Same strike / Pair / ⭐ / Manual: triggers unchanged; Strategy = market BUY + exchange SL; Limit = LIMIT, Stop-limit = SL BUY, both get an exchange SL once filled; cancel / exit / square-off act on one order (no more whole-option square-off). Open for PR 3: a safety "max SL" at the exchange for cards with only a CANDLE SL (today they have no broker SL).
   - In the cloud the PC doesn't need to be on; a browser (PC or phone) is needed for the morning token, the BEP CSV, ⚡ clicks and to see alerts.
   - **Single user:** one trader, many devices. Multi-user would be a redesign and, under NSE Circular 58/2026, would make the user an algo provider (empanelment needed). Not planned.

6. **PR 3: the engine on the broker layer; AlgoDelta removed (user, 24 Nov: "remove AlgoDelta"; all PR 3 questions "suggested").**
   - **Removed:** `order_bridge.py`, the webhook / JSON template / "I checked this JSON" / "Exit on opposite", the old Trading "Dry run" switch (the order mode is the only switch; `config.AUTO_ORDER_DRY_RUN` = mode is "dry"). `init.txt` removed. The header badge shows DRY RUN / SANDBOX / LIVE.
   - **Setup › Order defaults** (was "AlgoDelta order settings"): quantity, Target / SL type + value, per-type targets, trailing (trigger / point / gap), the 09:15 values, Warn above BEP, and new **"Max SL for cards with a CANDLE SL"** (`candle_max_sl`, default 17, 0 = none). `Engine.save_order_defaults`.
   - **`positions.py`** (`PositionManager`, state `bk` = JSON in the orders file, new columns `bk`, `ik`, `filled_qty`):
     - Entry = market BUY (market protection), tag `BEP-hhmmss-n` (`Engine.place_entry`; Pause algo, and for Sandbox / Live the daily limit apply).
     - After the fill (order book every 2 s, `broker.order_book()`, one call): an **SL-M SELL** at the exchange. Trigger: LTP rule = value, POINT = fill − value, CANDLE-only = fill − `candle_max_sl`; 09:15 order = fill − `open_max_sl` (its candle SL stays app-watched). Already below the SL → exit at once.
     - SL not placed: retried every 2 s, the 2nd and 3rd try as an **SL limit** (limit = trigger − `AUTO_ORDER_SL_LIMIT_GAP` 5); after 3 tries **NO SL** alert (toast, notification, red chip); the user decides (Square off). An SL-M **rejected** later → re-placed as SL limit.
     - **Trailing** (decision 4's rule, card trail or Setup) modifies the SL trigger up, at most once per 2 s, never down.
     - **Exits** (target LTP / POINT on the live LTP; CANDLE SL / target via `trade_tracker` `custom_hit`; Pair opponent; Square off / Square off all, **this order only**; 15:15 `EOD_EXIT_TIME`): the SL order is **modified to MARKET**; without one, a market SELL. Exit failing 3 times → "EXIT FAILED" alert. An unfilled entry is cancelled at 15:15 / on an exit request.
     - The SL order filling = exit "SL" or "Trailing SL" (trigger above the first one).
     - **Editing after placing** replaces the rule: LTP / POINT SL moves the exchange SL (also down: the user's choice), CANDLE → app rule + max SL; Target LTP / POINT / CANDLE / None; trail → moves the SL.
     - **Sandbox** (no order status): entry assumed filled at the LTP, an accepted exit assumed filled at the LTP, the SL assumed hit when LTP ≤ trigger, so the calls can be tried end to end.
     - **Dry run:** the paper broker, moved by the 2 s LTP (replaces the 1-min candle simulation for new orders). After a restart it has forgotten its orders: a filled entry stays, a lost SL order is placed again (IDs `PAPER-hhmmss-n`).
     - **Restart:** positions resume from the orders file; any filled position without a live SL gets one on the next tick. Positions placed in another mode are left alone (switch back to manage them).
     - Card chips: `ORDERED · SL 135.00`, `ORDERED · SENT · waiting for the fill`, `EXITING · Target`, `NO SL`, `EXIT FAILED`, `EXITED · Target / SL / Trailing SL / Candle SL / Opponent SL / Manual square-off / 15:15`. Order Details SL / Target column adds `positions.summary` (e.g. "SL-M 135.00 at the exchange").
   - **Limit / Stop-limit** were refused at ⚡ until PR 4 (decision 7).
   - Checked (24 Nov): 38 unit tests (`tests/test_brokers.py`, `tests/test_positions.py`), and the real engine in Dry run with stubbed data: fill 152 → SL-M 135 → trail 155 / 165 → target 202 → SL to MARKET → exit 203, P&L ₹3,315; a 2-lot card → square-off of that order only; a restart re-placing the lost SL. **Not yet tested:** Sandbox / Live (needs a trading day, the sandbox token, and for Live the static IP).

7. **PR 4: Limit / Stop-limit on the broker layer (user, 24 Nov: all suggested).**
   - **Entry:** Limit = a `LIMIT` BUY at the card's price; Stop-limit = an `SL` BUY (trigger → limit), both through the same `Engine.place_entry` / `positions.py` position as a market entry (`place_limit_order` → `place_watch_order(..., entry={"type","price","trigger"})`; state `etype` / `eprice` / `etrig` / `triggered`). After the fill: the same exchange SL-M, trailing, target and exits (decision 6).
   - **Partial fills:** the position follows the entry's filled quantity; the exchange SL is placed for what is filled and its quantity is modified as more fills come (`sl_qty`). An exit first cancels the unfilled rest, then exits the filled part (one sell).
   - **Cancel by order ID** (this order only): Cancel order (card / Order Details), Delete card, card FAILED / EXITED (`cancel_dead_limits`), Pair opponent before the fill, 15:15 (`PositionManager.request_cancel`, `Engine.cancel_limit`, `entry_open` / `entry_cancellable`). A cancelled unfilled entry → order status `CANCELLED`, card chip CANCELLED (⚡ can be pressed again). A partly filled one keeps its filled part with its exchange SL. The "cancel_order may hit other orders" warnings are gone for these orders.
   - **Warnings at ⚡ unchanged:** typo guard 20 %, "limit above LTP fills immediately" (Switch to Stop-limit), Stop-limit refused when LTP ≥ trigger (also checked in `place_limit_order`).
   - **Chips:** `LIMIT OPEN @ 130.00`, `STOP @ 140.00 → LIMIT 141.00`, `FILLED 65 @ 129.00 · PARTIAL`, `CANCELLING`, then the decision 6 chips. Header "LIMIT OPEN (n)" counts waiting broker limits; the "APP-MANAGED SL" badge counts only old `mg` rows.
   - **Dry run:** the paper broker fills on the 2 s LTP (Limit at min(price, LTP); Stop-limit triggers at LTP ≥ trigger, then fills at LTP ≤ limit); placed outside market hours it gets no stale LTP (waits for 09:15). After a restart a lost unfilled entry is placed again at the same prices. **Sandbox:** the same rules are assumed on the LTP (no order status there).
   - **Not done (by choice):** modifying the price / trigger of an unfilled order (cancel and place again; a Modify button after the switch). `limit_orders.py` / the `mg` column stay only to read old orders files; remove after the switch.
   - Checked (24 Nov): 47 unit tests (9 new: limit / stop-limit fills, cancel, 15:15, exit before fill, partial fills growing the SL, partial + exit, sandbox, restart); the real engine in Dry run: Manual Stop-limit 140 → 141 waits (STOP chip), triggers and fills at 140.5 → SL 128.5 → square-off; a Limit card deleted → cancelled; Stop-limit refused with LTP above the trigger; all pages load without errors.

8. **PR 5: password login and the cloud server (user, 24 Nov: all suggested).**
   - **Server:** AWS Lightsail Mumbai, Ubuntu 24.04, 1 GB plan, its static IP (registered in the Upstox app after market hours; that logs the token out).
   - **Access: Tailscale** (private network, HTTPS `https://<host>.<tailnet>.ts.net` via `tailscale serve --bg 8084`); the app listens on 127.0.0.1 there (`BEP_HOST`, `config.APP_HOST`; on the PC the default stays 0.0.0.0). Firewall (ufw): SSH + `tailscale0` only; port 8084 never open.
   - **Login (`auth.py`):** one password, salted scrypt hash in `auth.json` (git-ignored; set with `.venv/bin/python auth.py set-password`, off with `remove-password`, min 8 characters). No `auth.json` = no login (the PC). `AuthMiddleware` in `app.py` sends every page except `/login` (and NiceGUI's `/_nicegui…`) to `/login`; the browser stays logged in 30 days (`app.storage.user` `auth_until` + `auth_epoch`: a new password logs every browser out; session cookie `max_age` 30 days, secret in `.storage_secret`). Logout icon in the header. 5 wrong tries from one address → blocked 5 min (behind Tailscale serve all browsers share 127.0.0.1, so the block is effectively global). Logins / failures go to the Log.
   - **Service:** `bep-upstox` (systemd: starts at boot, `Restart=always`, 5 s, `TZ=Asia/Kolkata`, `BEP_HOST=127.0.0.1`); server clock IST; runs all day, every day. Logs: `journalctl -u bep-upstox -f`.
   - **Scripts:** `deploy/setup_server.sh` (one time, from the cloned repo: apt, IST, uv + Python 3.14, `.venv`, packages, the service, ufw, Tailscale) and `deploy/update.sh` (`git pull --ff-only origin master`, packages, restart, status; refused Mon–Fri 09:00–15:35 IST unless `--force`). The repo is cloned with a read-only GitHub **deploy key**. Guide: `deploy/README.md`.
   - Checked (24 Nov): 53 unit tests (6 new in `tests/test_auth.py`); a real run with a password: `/order-details` → `/login`, wrong password "4 tries left", login → `/`, `/setup` reachable, Logout → `/login`; 5 wrong → blocked, another address unaffected; without `auth.json` `/` and `/setup` answer 200. Scripts pass `bash -n`; **not yet run on a real server.**

9. **NIFTY change % in the header (user, 24 Nov; also BEP-AlgoDelta; all suggested).** The header chip shows `NIFTY 26,050.35  +448.10 (+1.75%)`, green up / red down. `Engine.fetch_index_ltps` uses the Upstox **full market quote** (`GET /v2/market-quote/quotes`, `upstox_client.fetch_quote`: `last_price` + `net_change`) instead of the LTP call: the same one call per scan. % = net change ÷ (LTP − net change) × 100 (`Engine.index_change`, `ltp_change`). Before 09:15 and on holidays it is the last session's change (Upstox's own figure; on 24 Nov it should read +1.75 %). If the full quote fails: the plain LTP call, no change for that scan, one Log line. Header only (not the Dashboard).

10. **Max loss per day (PR 7, merged 24 Nov; user, 24 Nov; also BEP-AlgoDelta PR #52; all suggested).** Setup › Order defaults **"Max loss per day (₹, 0 = off; Dry run only for now)"** (`max_loss_day`, default 2000, saved in settings.json). **For now only in Dry run** (Sandbox / Live later, after testing): day loss = today's closed + open P&L of the **DRY RUN** orders (`Engine.max_loss_state`); the limit is hit at a loss ≥ the value.
   - **⚡ / Add & place** (first of the ⚡ checks): popup "Max loss per day reached: Day loss ₹2,350 is past your max loss ₹2,000", **Place anyway** / Cancel. Place anyway marks the card (`loss_ok`, `Engine.ack_max_loss`, logged), so a PENDING entry armed then still fires.
   - **Orders nobody clicks** (a PENDING card firing later): not placed, card BLOCKED (⚡ can be pressed again), toast + Log "Max loss per day reached (loss ₹x, limit ₹y); NOT sent". Checked in `Engine.place_entry` (`sig["loss_ok"]` skips it).
   - **The 09:15 order is not affected** (user: "This loss limit is not applicable for 9:15 order"). Open positions are left alone (no auto square-off). Test orders on Setup are not affected.
   - Header: a red **MAX LOSS ₹2,350 / 2,000** badge once hit.
   - Checked: 58 unit tests (`tests/test_max_loss.py`: dry-run rows only, open P&L counts, 0 = off, not in Sandbox / Live, blocked / 09:15 exempt / Place anyway); Setup shows the field.

## Inherited from BEP-AlgoDelta (up to its PR #49)
Everything below describes the app as copied. PR numbers (#2 … #49) are **BEP-AlgoDelta** PRs. **Since PR 3 here, every mention of AlgoDelta JSON, `order_bridge.py`, webhooks, `squareoff_position` / `cancel_order` or "AlgoDelta holds the SL / Target / trail" is replaced by decision 6 above** (the BEP logic, triggers, warnings and screens still apply). The Limit / Stop-limit parts come back in PR 4.
## Architecture (after PR #3)
- `engine.py`: **one background engine** for the whole app (`app.timer`). It handles the Upstox connection, scans, the 09:15 order, Order Management, orders, tracking, log and alerts. Pages only read its state; version counters trigger redraws, and events trigger toasts and browser notifications in every tab.
- `app.py`: NiceGUI **single-page app** (`ui.sub_pages`), dark theme with a toggle, a header (LTP, status, Upstox ✓, DRY RUN/LIVE, Pause alerts, Pause algo) and a left nav. The pages are:
  - **Dashboard**
  - **BEP Support**: a nav group (PR #33) with **Support board** (`/support/board`; `/support` opens it) and **BEP Pair cross** (`/support/pair-cross`, decision 25). Both pages have **Download XLSX** (two sheets) and the **Replay a past day** bar.
  - **Order Management**: a nav group with **four** sub-pages, each with its own Add form (⭐ has none, rows are added automatically) and list. They are `/order-management/same-strike`, `/order-management/pair`, `/order-management/bep-star` and `/order-management/manual-entry`; `/order-management` opens Same strike. Each sub-item shows a row count, and its badge is green when a row is triggered and waiting for Place order. Add a sub-page through `OM_SUB` in `app.py`. Each row is an **order card** (like AlgoDelta's): a header with symbol/Buy/INTRADAY/TF, a status chip (WAITING, TRIGGERED, PENDING, ORDERED · SENT/DRY RUN, SQUARING OFF, EXITED · reason, FAILED, plus the limit chips of decision 16) and ⚡ Place / ✕ Delete / ✏ Edit (WAITING only). The body shows Entry Price (**Entry (filled)** once ordered), Quantity (lots, editable per card until ordered), P&L (**open P&L while running**, decision 22), live LTP, the level box (**Common price / Pair price / BEP / Order type**, decision 19), Target, Stoploss and Trailing (per card, PR #15; editable as app rules while the order is open, decision 18). A "Compact view" switch brings back the table. Live option LTP comes from `engine.refresh_option_ltps` (one batched call every `OPTION_LTP_SECONDS` = 2 s). Each card is linked to its order through `order_id`, which supplies Entry and the realized P&L (open P&L is computed locally from the 2 s LTP). **Entry at a price (PR C):**
    - Entry Price on a card is editable until it's ordered. Empty means a market order at ⚡.
    - With a price, ⚡ (only once TRIGGERED, so no pre-arm) **arms** the card. The chip shows `PENDING @ 95.00 ↓`, the app checks the live LTP every 2 s, and when LTP is ≤ the price (↓ dip) or ≥ it (↑ breakout) it sends the normal market `strategy_order`.
    - The direction is set automatically from the LTP at arming, and the arrow flips it.
    - It cancels via the button, automatically when the card turns FAILED/EXITED, and at 15:15. Pause algo and the daily limit apply, inside `send_order`.
    - The signal LTP of a manual order (= dry-run Entry) is now the **live LTP at the click or the price hit**, not the candle close.
    - AlgoDelta's `strategy_order` has **no entry-price field** (checked by the user), so this app-side PENDING logic stays.
  - **Order Details**
  - **Setup**
  - **Logs**
- `bep_monitor.py`: BEP Support board, ⭐ first support, and the 09:15 opening order (prepare + 1 s tick).
- `pair_cross.py`: BEP Pair cross table (decision 25), run by `BepMonitor._cross_step` on the board's candles.
- `replay.py`: Replay a past day (a separate `BepMonitor` with `replay=True`, historical candles through `ReplayClient`).
- `common_watch.py`: Order Management rows (Same strike, Pair, ⭐, Manual Entry).
- ~~`order_bridge.py`~~ (AlgoDelta JSON): **removed in BEP Upstox PR 3.**
- `brokers/` (BEP Upstox PR 2): the broker layer (`base.py` interface incl. `order_book()`, `upstox.py`, `paper.py`, `create()` in `__init__.py`); see "BEP Upstox decisions".
- `positions.py` (BEP Upstox PR 3): broker-held positions (entry → exchange SL → trail → exit); see decision 6.
- `limit_orders.py`: Manual Entry Limit mode helpers (state in the order row's `mg` column, app SL/Target/trail checks, Upstox order-book matching).
- `trade_tracker.py`: Order Details Entry/Exit/LTP/Points/P&L. Live orders use Upstox fills (FIFO per order); dry run is simulated.
- `exit_rules.py`: one evaluator for SL/Target rules on 1-min candles (LTP / POINT / CANDLE, trailing SL, 15:15 exit).
- `settings_store.py`: `settings.json` (Setup values) and `upstox_token.json` (today's token only).
- `config.py`: NIFTY only (lot size 65, strike step 50), defaults, and `OPENING_PREP_START = "09:08"`.

## Feature decisions (in order)
1. **Polling fix (PR #2):** the polling block was wrongly indented inside `build_panel()` after its `return`.
2. **NIFTY only**; SENSEX removed (re-add notes are in `config.py`). Port 8083.
3. **AlgoDelta JSON:** a template popup with a default/edited badge. Trailing SL (type LTP/POINT, trigger, point, gap) and Exit on opposite: both switches default OFF, and the controls always override the template.
4. **Trailing SL rule:** once the high reaches entry + trigger, SL = high − gap (in steps of point), and it never moves down. For example, entry 178, SL 166, trigger 20, gap 20: at 198 the SL goes to 178 (cost), and at 210 it's 190. Gap may equal Trigger.
5. **09:15 opening order (automatic):**
   - 09:08–09:15: read NIFTY's pre-open price and pick the strike just below and just above it (the same two for CE and PE), with their BEPs. The plan freezes at 09:15.
   - 09:15:00–09:20: every 1 s, one Upstox OHLC call. The rule: the first candle's low touched BEP, LTP is above the open and above BEP, and **LTP − BEP < 15** (editable).
   - **One CE and one PE per day.** If both strikes qualify, the one closest to the pre-open price wins. SENT, DRY RUN and FAILED lock the side; BLOCKED frees it. The lock is restored from today's orders file.
   - **No plan before 09:15** (late start or restart) means **no 09:15 order** that day.
   - Still unverified live: whether Upstox's NIFTY value between 09:08 and 09:15 equals the pre-open price. A manual override was offered if it doesn't.
   - **Entry at BEP + 2 and a CANDLE SL at BEP − 2 (PR #41, user request on 23 Nov):**
     - Entry: LTP ≥ **BEP + `open_entry_above`** (2) instead of LTP > BEP. The touch, LTP > open, one CE + one PE, `max_above_bep` (still from BEP) and the market `strategy_order` stay. With `open_entry_above` = 0 it's the old LTP > BEP.
     - SL: a **5-min candle close below BEP − `open_sl_below`** (2), e.g. BEP 150 → exit on a 5-min close below 148 (a wick doesn't exit). It's stored on the order as a CANDLE `c_sl` (`BepMonitor._opening_sl`) and the order is sent with `is_sl` false, so **AlgoDelta holds no SL**: the app squares off (keep the PC on). The Setup trailing SL, if on, is watched by the app too (`c_trail`). The Target stays the Setup Target, held by AlgoDelta.
     - Setup: "09:15 entry above BEP +" (2), "09:15 SL: close below BEP −" (2), "SL candle" 1 / 3 / 5 min (5) (`open_entry_above`, `open_sl_below`, `open_sl_tf` in `AUTO_ORDER`, saved in settings.json).
   - **Max SL 17 points, held by AlgoDelta (PR #47, user request 23 Nov):** the candle SL had no cap (a fall inside a 5-min candle exits only at the close) and nothing exited if the PC was off. Now the order is sent with `is_sl` true and a **POINT SL of `open_max_sl`** (17) from AlgoDelta's fill, e.g. BEP 150, entry 152 → max SL 135, and `is_trail_set` false (the Setup trail stays with the app, `c_trail`, so AlgoDelta's 135 stays fixed). The 5-min close below BEP − 2 stays the normal exit; whichever hits first exits. Near the top of the range (entry 165) both are 148, but the max SL fires on the LTP. Planned risk was 4 to ~17 points (entry BEP + 2 to < BEP + 15).
     - Setup "09:15 max SL: points below the fill (AlgoDelta, 0 = none)" (`open_max_sl`, saved; 0 = the old `is_sl` false). The Log line and Order Details SL / Target show "Max SL 17 pts (AlgoDelta)". Dry run simulates both rules (checked: drift → candle SL 147; crash to 130 inside a candle → 135).
     - No double exit: live, the app's candle rule is checked only while the Upstox trade book shows no exit (30 s refresh).
6. **Order Details:** Entry, Exit, LTP, Captured Points (**Exit − Entry** once exited; while open, `x (open)` from the live LTP, decision 22) and P&L ₹ (the same).
   - **Live:** Upstox trade book (`get-trades-for-day`, every 30 s). Fills are matched by option and **shared FIFO between orders on the same option** (PR #10).
   - **Dry run:** entry = LTP at the signal. The exit is **simulated** from 1-min candles using the order's SL/Target/trail (SL first if both hit in the same minute; 15:15 square-off). It's saved in the orders file.
   - The JSON column is hidden in the table but kept in the file and the CSV/XLSX downloads.
7. **Order Management** (manual trades: alert, then **Place order**; nothing automatic). Sections (now sub-pages; Manual Entry came later, decision 13):
   - **Same strike:** strike, Leg (Both/CE/PE), common price, timeframe **1/3/5 min**, Trigger on **Candle close / Live LTP**, Order **Strategy / Limit** (decisions 17, 20). Fires when an **armed** leg (it traded at or below the price after the add: close, wick or live LTP) closes above the price, or with Live LTP its LTP goes above it, **once**. A later close below turns the row red, with no alert. Rows added pre-open use the **first candle's open** as the previous price, so the earliest trigger is 09:20 / 09:18 (PR #10).
   - **Pair (CE vs PE):** CE strike, PE strike, common price (optional separate PE price), buffer (default 1).
     - **Entry, either side:** a leg closes above its price while the opponent closes below price − buffer (or, with Trigger on Live LTP, the same on the LTPs). The leg must have traded at or below its price first ("armed": a close, a wick or the live LTP, decision 20).
     - **Exit:** the order's SL/Target, **or** the opponent closing at or above price − buffer. Live, that sends `squareoff_position` automatically, and it isn't blocked by Pause algo or the daily limit. In dry run the exit is recorded.
   - **BEP Support ⭐:** a live first support inside the ATM window is added automatically and is ready to order (option A). It turns red when the board row fails. A 09:15 ⭐ is skipped if that side already has its opening order. Same-strike and ⭐ rows can coexist at different levels.
   - "Both" legs are independent rows (no change wanted).
8. **Targets per type (PR #8):** Setup holds default targets for Same strike, Pair and ⭐, and the 09:15 order uses the main Target. The SL and trailing SL are common.
9. **Per-order SL/Target (PR #9):** SL and Target switches open a popup with Type **LTP / POINT / CANDLE**, Time frame 1/3/5 min, and Value.
   - **CANDLE:** exit only when a candle **closes** below (SL) or above (Target) the value; a wick doesn't exit. Candles are aligned to 09:15.
   - **Before placing (Order Management):** LTP/POINT are sent to AlgoDelta. CANDLE is sent with `is_sl`/`is_tgt` false (no trail), and the app watches it and squares off.
   - **After placing (Order Details, not the 09:15 order):** the app watches the rule. AlgoDelta's original SL/Target stay active, because there's no modify syntax.
10. **UI (PR #3, plus tweaks):**
    - BEP Support rows are white, and only failed rows are red.
    - Pages are renamed: Order Management (formerly Common Price Watch) and Order Details (formerly Orders).
    - The log is full height.
    - Setup is saved, except that Dry run always starts ON.
    - The Upstox token is saved for **today only** and reconnects automatically on start; there's a "Forget saved token" button.
11. **Per-card Trailing SL (PR #15):**
   - Click the card's Trailing box to open a popup: ON/OFF, Type, Trigger, Point and Gap. It starts from Setup and is stored as `c_trail` (`engine.row_trail`).
   - It's sent in that card's JSON (and simulated in dry run) and locks once ordered. With a CANDLE SL (`is_sl` false), AlgoDelta can't trail, so since PR #24 **the app** watches the trail instead (decision 18).
   - Ordered cards show the Target, SL and Trail **as sent** (from the order JSON).
   - Start/End time was dropped (the user doesn't want it).
12. **Manual square-off (PR #16):**
   - **Where:** a **Square off** button on ordered cards (Order Management) and in the Action column of Order Details (every open order, including the 09:15 one). Order Details also has **Square off all** (one `squareoff_position` per option).
   - **Confirm popup:** shows the quantity, entry, LTP and approximate P&L. AlgoDelta's `squareoff_position` has **no order ID or quantity**, so it closes the **whole position on that script**. The popup lists the other open orders on the same option, and all of them are marked.
   - **Placing a second order on an option that's already open** asks for confirmation first (option A; there's no hard block).
   - **Not blocked** by Pause algo or the daily limit.
   - **Live:** `sq` = `SENT hh:mm:ss`, the card shows SQUARING OFF, and the trade book is refreshed at once. Exit/P&L then come from Upstox, and the card shows EXITED · Manual. On failure: `FAILED`, with a **Retry square off** button.
   - **Dry run:** Exit = a fresh LTP at the click.
   - **After a square-off:** pair opponent exits and app-watched CANDLE/custom rules are skipped (`engine.squared_off`).
   - **Saved:** `sq` is stored in the orders file.
   - **Verified by the user:** AlgoDelta's square-off closes the position, including its pending SL/Target.
13. **Manual Entry (PR #17):**
   - A 4th Order Management sub-page, `/order-management/manual-entry`, with rows of source `manual`.
   - **Form:** Strike, CE/PE, Lots, **Price / Market / Limit / Stop-limit** (Limit added in PR #20 and the default from PR #21; Stop-limit added in PR #36 and the default since PR #40) and the price (pre-filled with the live LTP from `engine.quote_option`).
   - **Buttons:** **Add card** (set Target/SL/Trail on the card, then ⚡) or **Add & place** (uses the defaults).
   - The card is ready at once (TRIGGERED, no candle checks). **Price** reuses the PENDING entry logic; **Market** sends `strategy_order` now. AlgoDelta holds the SL, Target and trail.
   - Setup has a **Manual entry** default target (`tgt_manual`).
   - **Limit mode** was added in PR #20 (decision 16) through AlgoDelta's `limit_order` and `cancel_order`, with the SL/Target watched by the app. Since 1 April 2026, Upstox order APIs (place/modify/cancel) need a static IP registered in the user's own Upstox app, and AlgoDelta's IP can't be used. With a static IP, an "Upstox direct" mode could add modify and per-order square-off, and keep the SL at the broker.
   - The user checked AlgoDelta's Syntax Generator: `strategy_order` has **no entry-price field**, so the Price mode stays app-side.
14. **Target fix (PR #15):** rows no longer copy the Setup target when they're added (the old `_stamp_target`). The default Target is read from Setup **when the order is placed**, so a Setup change applies to existing rows too.
15. **Order Management sub-pages and cards (PRs #12 to #14):**
   - The nav group has Same strike, Pair, BEP Support ⭐ and Manual Entry.
   - Cards are modelled on AlgoDelta's order card, with a Compact view switch.
   - Entry at a price (PENDING): the app waits for the LTP, then sends a market `strategy_order` (details in Architecture).

16. **Manual Entry Limit mode (PR #20):**
   - **Form:** a third mode, **Price | Market | Limit**, with a "Limit price" field. The form starts on **Limit** (PR #21; it was Price before).
   - **⚡ sends** AlgoDelta `limit_order` (BUY, quantity = lots, the price). Pause algo, dry run and the daily limit apply.
   - **Typo guard:** a popup asks first if the price is ≥ `LIMIT_SANITY_PCT` (20%) away from the LTP.
   - **SL/Target/trail are watched by the app**, not AlgoDelta (`limit_order` has no SL/Target fields):
     - LTP/POINT rules and the trailing SL are checked on the live LTP every 2 s;
     - a CANDLE rule goes to `c_sl`/`c_tgt` (candle path);
     - a hit sends `squareoff_position` once (`mg.exit`);
     - at 15:15: open limits are cancelled, filled ones are squared off.
   - **Live fill:** read from the Upstox **order book** (`/v2/order/retrieve-all`, read-only, no static IP) every `LIMIT_POLL_SECONDS` = 5 s while open. Matching is by instrument, BUY LIMIT, price and time, then the `order_id`. The trade book is refreshed at once after a fill.
   - **Card chips:** LIMIT OPEN @ x → FILLED @ x (· PARTIAL) → EXITED; also CANCELLING, CANCELLED, CANCEL FAILED (Retry cancel) and REJECTED (with the reason).
   - **Cancel:** AlgoDelta `cancel_order` (`{"type":"cancel_order","script_type","script","product"}`) has **no order ID**, so it may cancel every pending order on that option (other limits and the broker SL/Target of open strategy orders). The popup warns about this.
     - Live: CANCEL SENT, then the order book confirms it, else CANCEL FAILED after 30 s.
     - Dry run: CANCELLED at once.
     - A partial fill keeps its filled part protected.
   - **Deleting a card** with an open limit sends `cancel_order` first (user request). A square-off of a partly filled limit cancels the rest first.
   - **Dry run (fixed in PR #23):** fills only in market hours (never before 09:15, when the LTP is stale) at **min(price, live LTP)** once LTP ≤ price, so a gap below the price fills near the LTP. The exit is a fresh LTP via the normal square-off. Placing before 09:15 shows "market not open yet: the limit waits for 09:15".
   - **Exit chip (PR #23):** EXITED · App SL / App trailing SL / App target / 15:15 / Opponent SL / Manual (from `mg.exit`, also set by `square_off`).
   - **Header badge:** "APP-MANAGED SL ACTIVE (n) - keep this PC on" / "LIMIT OPEN (n)".
   - **Restart:** the state is in the orders file (`mg`), so watching resumes.
   - **Order Details:** shows the limit state and app rules in SL/Target, plus a Cancel order action.
   - **Editing after placing (PR #42, user request 23 Nov; it was locked before):** SL, Target and Trailing of a Limit / Stop-limit order are editable while it's pending or filled and not exited (the card boxes and the Order Details popup; `order_rule_editable`). AlgoDelta holds none for these orders, so an edit **replaces** the rule: LTP / POINT → `mg.sl` / `mg.tgt` (live LTP), CANDLE → `c_sl` / `c_tgt` (candle closes), × removes it (`Engine._set_limit_rule`, `order_rule`, `order_trail`). The trail goes to `mg.trail`, and it now also works with only a CANDLE SL (`limit_orders.sl_level`). The "Check the stoploss" warning applies.

17. **Limit for Same strike, Pair and ⭐ (PR #22):**
   - **Choosing the type:**
     - The Same strike and Pair Add forms have an **Order: Strategy | Limit** toggle (default **Strategy**).
     - Each card (Same strike, Pair, ⭐) has the same toggle in its Entry Price box until it's ordered (`CommonWatch.set_mode`, which resets the Entry Price).
     - Compact view has an **Order** column (`Strategy` / `Limit @ x`); click it for a popup.
     - Anything other than Limit sends `strategy_order` (market, or the app-side PENDING entry when an Entry Price is set).
   - **Limit price:**
     - It's the card's Entry Price, pre-filled with the row's level (common price / ⭐ BEP).
     - Pair before it fires: the fired leg's own price (`Engine.limit_price`).
     - Editing a Same-strike level moves a limit price that was still equal to the level.
   - **Sending:** ⚡ works only once TRIGGERED. The order goes through `place_limit_order` (the same app-watched SL/Target/trail as Manual Limit, using the per-type default targets).
   - **Auto-cancel:**
     - A card that turns FAILED/EXITED cancels its open limit (`cancel_dead_limits`, each scan); a filled part stays protected.
     - Pair opponent exit: a filled limit is squared off and an open one is cancelled (`handle_pair_exits`).
     - Delete and 15:15 work as for Manual.

18. **App trailing SL and editing after placing (PR #24):**
   - **CANDLE SL + trailing:** the card's trail (own or Setup) is no longer forced OFF. It's stored on the order as `c_trail` (`{"trigger","gap","step","set_at","high","done"}`, a new orders-file column) and watched by the app on the **live LTP every 2 s** (`check_app_trails`).
     - The rule: once the high reaches entry + trigger, SL = high − gap (steps of point), and it never moves down.
     - LTP ≤ SL → `squareoff_position` **once** ("App trailing SL").
     - Entry = the order's Entry (Upstox fill live, signal LTP in dry run).
     - The CANDLE SL is still checked on candle closes; whichever hits first exits.
   - **Ordered, still-open cards (not limit orders, not the 09:15 order)** are editable while open:
     - Target / Stoploss: a click opens the rule popup and sets an app-watched rule (`set_order_custom`, the same as Order Details).
     - Trailing: a click opens the trail popup (`set_order_trail`; Remove clears it).
     - AlgoDelta's original SL/Target/trail stay active (no modify syntax); whichever hits first exits.
   - **Order Details:** the SL/Target popup gains **Set trailing / Change**, and the SL/Target column shows `Trail a/b (app) · SL x`.
   - **Dry run:** the same check on the live LTP; the exit is a fresh LTP via the square-off.

19. **Card box names (PR #26):**
   - The level box is named per type (`level_name`): **Common price** (Same strike), **Pair price**, **BEP** (⭐) and **Order type** (Manual: Market / Price / Limit, from `manual_type`).
   - The first box is **Entry Price** before ordering and **Entry (filled)** after.
   - The Compact view Level column uses the same names; Manual rows show their order type.
   - **SL after placing (discussed, left as is by the user):** an app rule set after ⚡ only **adds** an exit, because AlgoDelta's original SL (e.g. 12 POINT) stays active with no modify syntax. To use only a CANDLE SL, set it **before** ⚡. Offered but not built: show both SLs on ordered cards, warn in the popup, and a Setup "SL held by AlgoDelta / App" switch.

20. **Arming on a touch, Trigger on Live LTP, 1 min (PR #27; Same strike and Pair):**
   - **Why:** the user's Pair (25500CE/25750PE @ 196) never fired. The CE was already above 196 when added and never **closed** back at or below it within 5 min.
   - **Armed:** a leg is armed once it trades at or below its price after the row is added: a candle close, a candle **low** (wick) of a candle that started after the add, or the **live LTP** (every 2 s, `CommonWatch.live_step` via `engine.check_live_watch`). Same strike rows now have `armed` too. Cards and Compact status show "armed / not armed yet".
   - **Trigger on** (per row `trig_on`; toggle on both forms and in Edit, default **Candle close**):
     - **Candle close:** an armed leg's candle closes above the price (Pair: the opponent's close below price − buffer).
     - **Live LTP:** an armed leg's LTP goes above the price (Pair: the opponent's LTP below price − buffer), within ~2 s (`trig_how` = "ltp", `trig_time` HH:MM:SS).
   - FAILED and the Pair opponent exit stay on candle closes.
   - **Timeframes:** 1 / 3 / 5 min (forms and Edit). Editing a row restarts arming.
   - Nothing is placed automatically: a trigger means alert + TRIGGERED, then ⚡.
   - "Arm now" (count a leg already above as armed) was declined.

21. **LTP on the Same strike and Pair forms (PR #28):**
   - An **LTP** box with a ↻ button (`form_ltp`), like Manual Entry's: Same strike shows the chosen leg(s) (Both → `CE x · PE y`); Pair shows the CE strike's CE LTP and the PE strike's PE LTP.
   - It refreshes on strike blur, on a leg change, with ↻, and once when the page opens (`engine.quote_option`).
   - The Common price is **not** pre-filled with the LTP (it's a level the user chooses).

22. **Open (live) P&L, calculated locally (PR #29):**
   - Before this, P&L showed only after the exit (0 while open).
   - Open position: **(live LTP − Entry) × qty** (`Engine.open_pnl`), qty = filled units (live) or lots × lot size.
   - **No extra API call:** the LTP is the one 2 s batch (`refresh_option_ltps`, which now covers every open order and updates its `ltp_live`). `trade_tracker.compute(..., known=option_ltp)` only fetches options not in that batch.
   - **Cards:** P&L in green/red with `+x pts · open`; the realized P&L after the exit.
   - **Order Details:** the Captured Points / P&L ₹ columns show `x (open)` for open orders (`open_points`/`open_pnl`, not saved), and the summary adds "open n: P&L ₹x" (the closed totals are unchanged).
   - Upstox is still needed for the LTP itself and for live fills/exits (trade book every 30 s).

23. **Pre-place / SL safety warnings (PR #30):**
   - **Why:** a Same strike card (level 120) was placed at 14:03 with LTP 111 and a CANDLE SL of 120. The next 5-min close (109.30 < 120) exited at once.
   - **At ⚡ ("Check before placing", Place anyway / Cancel; after the limit typo guard, before the same-option confirm):**
     - an SL (the card's own, else Setup's) of type LTP/CANDLE **at or above** the price you buy at (the LTP, the Entry Price or the limit price);
     - the LTP is **already back below** the row's level (Common price / Pair price / BEP): "the setup may have failed". Not for Manual cards.
   - **Setting an SL after placing** (the ordered card or the Order Details popup): "Check the stoploss" when it's at or above the Entry (`guarded_sl_set`).
   - POINT SLs are always below the entry, so they're never flagged.
   - **Far above BEP (PR #43, user request 23 Nov):** on ⭐, BEP cross Pair (fired leg) and Manual Entry cards (the strike's BEP from the CSV, else calculated; `Engine.row_bep`), the popup adds "LTP 168.70 is **11.13 points above** BEP 157.57 (warning above 10): late entry, bigger risk to the SL" when LTP − BEP > Setup **"Warn at ⚡ when LTP is more than X points above BEP"** (`warn_above_bep`, default 10; 0 = off). For Limit / Stop-limit the limit price is checked the same way. Same strike and normal Pair cards (own levels) and the automatic 09:15 order (its own `max_above_bep`) are not affected.

24. **BEP Support setup fixes (PR #32):** in `support_logic.process_candle` (5-min candles).
   - **Setup break and expiry:** a validated "Failed breakdown" setup is **broken** when a later candle's low goes below the flush low. It **expires** `SUPPORT_SETUP_EXPIRY_CANDLES` = 3 candles (15 min) after the validate candle. A trigger whose own low breaks the flush low is "Touch and go" (fail level = its own low).
   - **New flush:** a candle that resets a flush or setup and itself **closed below BEP** starts a **new flush**.
   - **09:15 exception:** the validate low may break the flush low only when the **flush candle** is 09:15 (the old check never ran).
   - **Follow-ups decided after the review (PR #33):**
     - **Repeat alerts:** a support alerts (and adds a ⭐) for the first row of a strike/side, and again once every earlier row on that strike has **failed** (`row["star"]`; the board's ⭐ marks those rows).
     - **First support card:** only strikes inside that candle's ATM window count; on a tie in the same candle, the strike **nearest ATM** wins.
     - **A missing candle** (never arrived, given up, or no BEP) resets that leg to idle.
     - The trigger keeps its +0.05 tolerance and the fail rule none (kept as is).
     - 09:15 order: `max_above_bep` **0 = no limit**.

25. **BEP Pair cross, Pair auto-add and Replay (PR #33):**
   - **What:** on each closed 5-min candle (the board's candles, no extra API calls), a CE and a PE near ATM that **split around their BEPs**. It's independent of the Support board and ⭐. Examples from the user: 20 Nov 09:45 25450CE/25450PE (same strike, full body) and 11:35 25500CE/25550PE (different strikes, wicks).
   - **Pairs:** ATM = the strike nearest NIFTY's close of that candle.
     - **Buy CE:** the leg above (the one to buy) is the CE at ATM or up to **2 strikes ITM** (ATM − 50, ATM − 100; `BEP_CROSS_ITM_STRIKES` = 2, PR #35; it was 1); the opponent below is the PE at ATM − 50, ATM or ATM + 50.
     - **Buy PE:** the leg above is the PE at ATM or up to 2 strikes ITM (ATM + 50, ATM + 100); the opponent is the CE at ATM ± 50.
     - **Why 2 (PR #35):** on 20 Nov at 09:40, 25400CE closed above its BEP and 25450PE below, but NIFTY was about 25,485 (ATM 25500), so 25400 was 2 strikes ITM and was never checked. The user confirmed on the 20 Nov replay (after PR #35) that the 09:40 row now shows.
   - **Signal:** the up-leg closes > its BEP **and** the down-leg closes < its BEP, and the previous candle wasn't in that state. Either leg may have crossed first (e.g. PE below at 09:40, CE above at 09:45). A pair already split on the first candle counts. No margin past BEP.
   - **Touch (user request):** both legs must have **touched** their BEP (low ≤ BEP + `BEP_CROSS_TOUCH_TOL` = 1 and high ≥ BEP − 1) on the signal candle or the 2 before (`BEP_CROSS_TOUCH_CANDLES` = 3). A leg far away from its BEP doesn't count.
   - **Labels (PR #35, the user is an option buyer and never shorts):** the Signal column shows a **Buy CE** (green) / **Buy PE** (red) badge (no ↑/↓ arrows); the columns are "Buy leg (closed above BEP)" and "Opponent (closed below BEP)"; Pts since / Best are for the **buy leg only**; alerts read "BUY 25400CE … | opponent 25450PE …".
   - **"Why no row?" checker (PR #35):** CE strike, PE strike, candle HH:MM + Check on the BEP Pair cross page (today or the replayed day). It lists each rule as ✓/✗ (`PairCross.explain` via `BepMonitor.explain_cross`): candles/BEPs, split, buy leg in range (NIFTY close, ATM), opponent in range, fresh, touch of each leg (times), already WATCHING, or signalled (main row or "Also").
   - **Rows:** one per candle and direction. The best pair is the same strike first, else the smallest BEP gap; the others go in "Also". No new row for a pair while it's WATCHING; after FAILED it can signal again.
   - **Watch 30 min** (`BEP_CROSS_WATCH_CANDLES` = 6): FAILED · time if the up-leg closes below its BEP or the down-leg closes above its BEP, else HELD. The table shows the best move of each leg and live LTPs (the 2 s batch) while watching.
   - **Alerts:** a toast + browser notification **only on a live signal** (respects Pause alerts). Rows rebuilt after a late start don't alert.
   - **Order Management > Pair (Part C):** a live signal adds a **TRIGGERED** Pair row (`add_cross_pair`), unless `BEP_CROSS_ADD_PAIR` is False.
     - CE/PE price = each leg's BEP, **buffer 0**, 5 min, Strategy, the up-leg is the fired leg, chip "BEP cross hh:mm".
     - Later candles use the normal Pair rules (opponent closing at or above its BEP = FAILED before ordering, opponent SL after).
     - A table FAILED marks it FAILED before ⚡ (`sync_cross_failures`, run before new rows are added).
     - It's skipped (logged) when a Pair row with the same strikes is still watching/triggered. Nothing is placed automatically.
   - **Replay a past day (Part E):** a date + "BEP: uploaded CSV / calculated" + Replay on both BEP Support pages.
     - It rebuilds the Support board and the Pair cross from Upstox **historical** candles in a separate monitor: no alerts, no ⭐ / Pair rows, no orders, and the live scan keeps running. "Back to live" returns. Downloads include "_replay".
     - Only days since the last expiry (older contracts aren't listed). The CSV must be **that day's** file; calculated = (CE + PE) / 2 of the previous day's closes (`fetch_close_before`, 2 calls per strike, ATM windows only).
     - Not available on non-trading days for "today": the board only reads today's intraday candles, so on a weekend the live board is empty (the user asked on Sat 21 Nov).

26. **Short toasts, top-right (PR #36):** the user said the long toasts covered the screen at the moment of the signal.
   - Toasts are top-right below the header, max 360 px wide, shown for 8 s, with a ✕ (`app.py` `pump_events`, CSS `.bep-toast`).
   - The text is short: `⭐ Support 26200PE · touch and go`, `Pair cross: BUY 25950PE · opp 25900CE`, `Pair 25450CE/25450PE: CE triggered → Place order` (`notifier.support_short` / `cross_short` / `common_short`). BEP, close and time are in the tables.
   - Alerts of one type in one scan are merged into **one** toast (e.g. `⭐ Support PE: 26200, 26100, 26050 · touch and go`).
   - The browser notification shows the same short text; the **full** labels still go to the Log.
   - Other toasts (order results, form messages) keep their text but use the same position and size.
27. **BEP box on BEP cross Pair cards (PR #36):** the user wants the BEP next to the live LTP, to compare.
   - Rows added by a BEP cross (`auto` = "cross") name the level box **BEP** (not "Pair price"): `CE 137.32` / `PE 126.15` on two lines, each with its live **LTP − BEP** (`+9.03 above` green / `-23.35 below` red, the 2 s LTP batch, `cross_bep_gaps`).
   - The header chip / Compact Pair column read `25600CE / 25650PE · BEP 137.32 / 126.15` (`CommonWatch.pair_title`).
   - Pair cards (all) show the LTP as `CE x` / `PE y` on two lines (the one-line text overlapped the next box).
   - Manual Pair rows keep "Pair price" (the user's own levels).
28. **Stop-limit entry and the limit-above-LTP warning (PR #36):**
   - **Why:** the user set a Manual BUY **Limit 140** with the LTP at 134.65 and it filled at once. A BUY limit means "at 140 **or cheaper**". What they wanted was a broker **Buy StopLimit**: wait until the price **rises** to 140.
   - **AlgoDelta `sl_order`** (checked by the user in the Syntax Generator, 23 Nov 2026): `{"type":"sl_order","script_type","script","product","quantity","price","trigger_price","transaction_type":"BUY"}`. It waits **at the exchange** (Upstox status "trigger pending") until LTP ≥ trigger, then it's a limit at `price`.
   - **Manual Entry:** a 4th mode, **Price | Market | Limit | Stop-limit**, with **Trigger (LTP rises to)** and **Limit price** (default trigger + `STOP_LIMIT_GAP` = 1, editable). The row stays `entry_mode` "limit" with **`entry_trigger`** set, so everything else is the Limit flow (decision 16): app-watched SL/Target/trail, order-book fill tracking, cancel, Delete, 15:15.
     - The card's Entry box has two inputs (trigger → limit). The Order type box shows **Stop-limit** with "at LTP ≥ x, limit y".
     - The chip shows `STOP @ 140.00 → LIMIT 141.00` until triggered, then `LIMIT OPEN @ 141.00` → `FILLED @ x`.
     - ⚡ is refused when the LTP is already ≥ the trigger (it would trigger at once): raise the trigger, or use Limit / Market.
   - **`limit_orders`:** `mg.trigger` / `mg.triggered`. The order book is matched on `order_type` "SL" plus the trigger price. "trigger pending" = waiting; any other open status, or a fill, = triggered.
     - **Dry run:** triggers when the live LTP ≥ the trigger, then fills once LTP ≤ the limit, at min(limit, LTP). A spike past the limit stays open, like a real stop-limit.
   - **Limit-above-LTP warning (all Limit cards):** ⚡ on a BUY limit above the LTP asks "This limit fills immediately", with **Send limit anyway** / **Switch to Stop-limit** (Manual cards: trigger = the price, limit = price + 1; then check the card and ⚡ again) / Cancel. It replaces the 20 % typo popup in that case; stop-limits skip the typo popup.
   - **Stop-limit on Same strike, Pair and ⭐ cards (PR #38, user request on 23 Nov):** every Order toggle (Add forms, card, Compact dialog) is **Strategy | Limit | Stop-limit**.
     - The row keeps `entry_mode` "limit" plus **`entry_stop`** = True. The trigger defaults to the row's level (⭐ = BEP; Pair = the **fired leg's** price, known once it fires) and the limit to trigger + 1. Both are editable on the card (`entry_trigger` / `entry_price` once set). Helpers: `Engine.is_stop` / `Engine.stop_prices`.
     - A Same-strike level edit moves a trigger that was still equal to the level.
     - All Pair rules stay: opponent exit (squares off a filled order, cancels a pending one), FAILED cancels it, 15:15, Delete.
     - At ⚡: no "price back below the level" warning (a stop-limit waits for the rise). It's refused when the LTP is already ≥ the trigger. **Switch to Stop-limit** in the limit-above-LTP popup now works on every card.
     - **Trigger / limit popup (PR #45, user request 23 Nov):** choosing Stop-limit on a card (or in the Compact Order popup) opens **"Stop-limit — strike"** with the live LTP and the level / BEP, **Trigger (LTP rises to)** and **Limit price**. The trigger starts at the level only when it's above the LTP, otherwise empty with "must be above the LTP x (the BEP y is already below it)"; the limit follows as trigger + 1. Save checks trigger > LTP and limit ≥ trigger; Cancel keeps the old type. The card shows `trigger → limit` as a clickable label that reopens the popup (no inline boxes). A Pair not fired yet may stay empty (= the fired leg's price at ⚡). `stoplimit_dialog` in `app.py`. Why: a ⭐ at 12:50 (BEP 137.32, LTP 151.95) took the BEP as the trigger, which ⚡ would refuse.
     - Example: the 23 Nov 10:15 BEP cross 25500CE (BEP 167.57, LTP 156.60): Stop-limit sends `sl_order` trigger 167.57 → limit 168.57 and waits for the CE to rise back above its BEP.
     - Merged 23 Nov 2026; the user confirmed Stop-limit shows on Pair cards.
   - **Manual Entry default = Stop-limit (PR #40, user request 23 Nov):** the form opens on Stop-limit. The trigger is **not** pre-filled (it must be above the LTP; the LTP shows beside it); typing it fills the limit = trigger + 1. The LTP pre-fill of the price box now applies only to Price / Limit.
   - **Not yet tested live:** AlgoDelta `sl_order` → Upstox (order_type "SL", status names), and what `cancel_order` does to a trigger-pending order. Test with 1 lot. Merged 23 Nov 2026; the user confirmed the card shows correctly and planned the 1-lot live test on 24 Nov, but 24 Nov is a market holiday, so it moves to the next trading day.

29. **Manual Entry BEP and Target "None" (PR #48, user request 23 Nov):**
   - **BEP on the Manual Entry form:** next to the LTP, `BEP 157.57 (+11.13)` (LTP − BEP, green above / red below). It comes from the uploaded CSV, else calculated (`Engine.row_bep`). It refreshes with the LTP (strike blur, CE/PE change, ↻) and once when the page opens. Shown only, no check; the ⚡ "far above BEP" warning (decision 23) stays.
   - **Target type None** (the user trails and wants bigger targets without changing the Setup default): the Target popup on every card (Same strike, Pair, ⭐, Manual; card box and Compact view) has **LTP / POINT / CANDLE / None**. It's stored as a `c_tgt` rule of type `NONE` (`exit_rules.NONE`, `is_none`; never evaluated). Switch OFF / × still means the Setup default.
     - Strategy order: sent with `is_tgt` false (AlgoDelta holds no target). Limit / Stop-limit: no app target (`_limit_rules`). Dry run simulates no target.
     - After placing: Limit / Stop-limit orders can be set to None (= remove the target, `_set_limit_rule`). Strategy orders can't (AlgoDelta's target was sent, no modify syntax), so the popup there has no None. An ordered card sent with None shows "None · no target (trail / SL only)".
     - At ⚡: "Check before placing" adds "Target is None and the trailing SL is OFF: this order exits only by the SL, a manual square-off or 15:15" when the card's trail is off.
     - The 09:15 order is unchanged (Setup target).

## AlgoDelta JSON used (BEP-AlgoDelta only; not used here since PR 3)
- Entry `strategy_order`: the template in `config._STRATEGY_ORDER`, with placeholders `{symbol}` (e.g. NIFTY23NOV2625400CE), `{qty}`, `{tgt}`, `{sl}`, `{is_trail}`, `{trail_*}`, `{exit_on_opposite}` and others.
- Limit entry: `{"type": "limit_order", "script_type": "CE", "script": "NIFTY23NOV2625200CE", "product": "INTRADAY", "quantity": 1, "price": 100, "transaction_type": "BUY"}` (quantity = lots).
- Stop-limit entry (PR #36): `{"type": "sl_order", "script_type": "CE", "script": "NIFTY23NOV2625650CE", "product": "INTRADAY", "quantity": 1, "price": 150, "trigger_price": 150, "transaction_type": "BUY"}` (quantity = lots; the app sends price = the limit, trigger_price = the trigger).
- Cancel: `{"type": "cancel_order", "script_type": "CE", "script": "NIFTY23NOV2625200CE", "product": "INTRADAY"}` (no order ID).
- Square-off: `{"type": "squareoff_position", "script_type": "CE", "script": "NIFTY23NOV2625300CE", "product": "INTRADAY"}`.
- AlgoDelta has **no modify-SL/Target syntax** (and no modify-order syntax) and no public read API (positions/fills), so live P&L comes from Upstox.

## Daily checklist (user, BEP Upstox)
1. Before 09:08: start the app. On Setup, enter today's Upstox token and upload the BEP CSV; the order defaults are already saved.
2. Choose the order mode on Setup › Broker orders: Dry run (default), Sandbox (sandbox token saved) or Live (static IP registered in the Upstox app). The header shows DRY RUN / SANDBOX / LIVE.
3. Keep the app running (PC, or the cloud server, which runs it all day): the SL is at the exchange, but trailing, the target, candle SLs and 15:15 need the app. On the server, open it through Tailscale and log in.
4. Add Order Management rows before 09:15 if you want first-candle triggers.

## Open / not yet verified
- **Not yet tested during market hours or against the real AlgoDelta webhook.** Test live with 1 lot first:
  - `market-quote/ohlc`, the pre-open index value, the `get-trades-for-day` fields and 3-min candles;
  - live option LTP on the cards (2 s), and a PENDING entry firing;
  - Entry and P&L filling in on cards;
  - a live square-off going SQUARING OFF → EXITED · Manual;
  - Manual Entry (Market and Price);
  - **Limit mode with 1 lot:** the order book fields/status names and the order matching, a fill → app SL square-off, `cancel_order` → CANCELLED, and what `cancel_order` does to other pending orders on the same option (does it also cancel a strategy order's SL/Target?);
  - per-card trailing as sent.
- **Waiting on the user:**
  - their own static IP (from their ISP, or an AWS Elastic IP / Azure static IP if hosted in the cloud) (for "Upstox direct" limit, modify, cancel and per-order square-off).
- **Branches:** the merged feature branches are deleted. Each cloud session has its own work branch (PRs #20 to #31: `claude/amazing-allen-klxor4`; PRs #32 to #49: `claude/affectionate-albattani-a69mce`; PR #40: `claude/manual-stoplimit-default`; PR #43: `claude/bep-distance-warning`), recreated from `main` for each PR. The user deletes it after a merge.
- **NSE Circular 58/2026 (NSE/INSP/77559, 20 Nov 2026, effective immediately): Tech-savvy Retail Investors / Static IP.** The user shared it on 21 Nov 2026.
  - **What it says:**
    - Static IPs are **only** for "Tech-savvy Retail Investors" (retail clients who **develop their own algos using programming knowledge**, SEBI circular of 4 Feb 2025).
    - Algo providers giving static IPs to other retail clients is a violation. Brokers must take an **undertaking** from each tech-savvy client (own algos; **no recourse to the exchange grievance mechanism** for losses from the algo logic).
    - Penalties: brokers ₹5,000 per client (max ₹1 lakh, escalating); algo providers' **empanelment may be suspended or revoked**.
    - It repeats (NSE/INVG/69255, Annexure I, Para 14): a tech-savvy client must host the algo **"using a static IP at their end where Algo logic resides"**.
  - **Impact on this app:**
    - The user writes their own algo (this app), so they fit "tech-savvy" in substance.
    - But the static IP is **AlgoDelta's** (the ₹400/month add-on), not at the user's end. That's the model the circular targets.
    - AlgoDelta's future is **uncertain**: it may comply (undertakings via the broker), restrict or withdraw the static-IP add-on, or face suspension. Not legal advice.
  - **Decided / advised:**
    - **Setup C (cloud server with the user's own static IP + Upstox direct)** now also reduces regulatory and dependency risk, not only adds features. Phase 1 (cloud + AlgoDelta) may be skipped if the answers aren't clear.
    - Avoid new features that only work through AlgoDelta (e.g. relying on `cancel_order` / `limit_order` behaviour).
    - **Questions drafted** for the user to send (21 Nov 2026):
      - **AlgoDelta:** compliance and empanelment; will the static IP add-on continue (date, notice); is "algo on my PC + AlgoDelta's IP" accepted; who collects the undertaking; will webhooks keep working; algo ID / registration / order-per-second limit; refund; what happens to open positions and pending SL/Target if suspended.
      - **Upstox:** how to register as a tech-savvy retail client and the undertaking text; algo registration and the order-per-second threshold; registering a cloud static IP (AWS Elastic IP / Lightsail, Mumbai), how many and how to change; which order APIs are allowed (place/modify/cancel by ID, SL/SL-M, GTT, single-position exit); which calls don't need the IP; charges; rate limits; moving off the third-party platform; emergency exit if it stops.
    - **Waiting on the user:** the replies from AlgoDelta and Upstox. When they arrive, turn them into the cloud server + Upstox direct plan.
- **Ideas offered but not built:** a manual pre-open override; an AlgoDelta exit webhook for other features; per-order trailing for the 09:15 order (it uses Setup); "Modify price" for a limit (cancel + new limit; declined for now); the **Upstox WebSocket market feed** (prices pushed in real time instead of the 2 s polling; read-only, no static IP; medium effort, later).
