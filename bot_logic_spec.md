# Bot Logic Spec — Delta Funding Capture Grid (Big Pickle)

**Status:** PAPER TRADE ONLY. Live tabhi jab user explicitly approve kare (`BOT_LIVE_APPROVED=1`).
**Code:** `delta_bot.py` (pure Python 3 stdlib, no pip).
**Data refs:** `delta_volume.json` (1158 symbols, 24h turnover) · `s5_final_trades.csv` (162 backtest trades) · `delta_level_best_20x.json` (best setup: 20x, 70% margin, start 200 → 4214, max DD -16.44%, 162 trades).

---

## 0. TL;DR — all rules in one place

| # | Rule | Value |
|---|------|-------|
| 1 | Signal | Perp funding capture: funding **negative → LONG**, **positive → SHORT** |
| 2 | Entry | `T-1 min` 1m candle **OPEN** (T = funding timestamp UTC) |
| 3 | Exit | `T+1 min` 1m candle **OPEN** |
| 4 | Chunks | C1–C6 **all active**, no skip; band = `abs(funding_rate)` in % |
| 5 | Trade threshold | `abs(rate) >= 0.10%` (else no trade) |
| 6 | Leverage | **20x baseline**, `min(tier, BOT_LEVERAGE_CAP)`; 30x strictly opt-in |
| 7 | Margin | 70% of equity max per position |
| 8 | Sizing | `base = clamp(0.5% × turnover24h, 5, 2000)` → `margin = min(base, 0.7×equity)` → `notional = margin × leverage` |
| 9 | Fee | 0.05% on exit (entry assumed 0 in baseline, configurable) |
| 10 | Max DD | **20% hard stop** (flatten + halt), 15% warning + half size |
| 11 | Trade frequency | **1 trade / day / coin** (UTC day) |
| 12 | Liquidation guard | `notional <= equity × 0.7 × leverage` (by construction) |
| 13 | Data | Delta `/v2/tickers/{SYM}` (funding_rate) + `/v2/history/candles` 1m |
| 14 | Credentials | **Env vars only** — never hardcoded |
| 15 | Go-live | Paper-trade first, live only after user approval |

---

## 1. Strategy thesis (kyun kaam karta hai)

Funding rate = perp traders ka periodic payment. Jab funding **negative** hota hai, shorts longs ko pay karte hain → long **receive** karta hai. Jab **positive**, longs shorts ko pay karte hain → short **receive** karta hai.

Bot funding timestamp ke **thik pehle** position leta hai (T-1 min) aur **thik baad** nikal jata hai (T+1 min). Is 2-minute window me:
- Funding payment capture hota hai (`abs(rate)` ≈ 0.1%–1.6% per event).
- Price risk sirf 2 minute ka hota hai (chhota directional exposure).
- Chhota position + liquidity cap = trap avoid.

Backtest `s5_final_trades.csv` me funding PnL **hamesha positive** (`funding_pnl = abs(rate)`), price PnL chhota random hota hai. Edge funding se aata hai.

---

## 2. Data inputs & universe

| Source | Use |
|--------|-----|
| `delta_volume.json` | 24h turnover per symbol → position sizing + liquidity ranking |
| `s5_final_trades.csv` | Validation / offline replay (162 trades) |
| `delta_level_best_20x.json` | Reference equity curve, DD, chunk distribution |
| Delta `/v2/tickers/{SYM}` | Live `funding_rate` (string, **percent** per interval), `turnover_usd`, `mark_price`, `product_trading_status` |
| Delta `/v2/history/candles` 1m | Entry/exit candle OPEN prices |

**Universe filter (live scan):**
1. Symbol suffix `USD` (skip `_INR`, skip options/expiries containing `-`, skip `P-`/`C-`).
2. `product_trading_status == "operational"`.
3. `turnover_usd >= BOT_MIN_TURNOVER` (default 5,000 USDT) — illiquid coin ko trap avoid.
4. `abs(funding_rate) >= 0.10` (%).
5. Funding rate sane (`abs(rate) <= 5%`) — data glitch guard.

Only coins passing all filters are eligible. Ranking inside a chunk = turnover descending (liquidity).

---

## 3. Chunk model C1–C6 (all active, koi skip nahi)

Chunk = `abs(funding_rate)` magnitude band, in **percent** (as returned by Delta):

| Chunk | Band (`abs(rate)` %) | Side rule |
|-------|----------------------|-----------|
| **C1** | `0.10 – 0.20` | rate < 0 → LONG, rate > 0 → SHORT |
| **C2** | `0.20 – 0.40` | same |
| **C3** | `0.40 – 0.60` | same |
| **C4** | `0.60 – 0.80` | same |
| **C5** | `0.80 – 1.00` | same |
| **C6** | `>= 1.00` | same |
| — | `< 0.10` | **no trade** (fee/noise floor) |

Boundary convention: lower-inclusive, upper-exclusive (`0.2` → C2, `0.4` → C3, etc.).
**All 6 chunks trade at every account level.** Account level only limits coins/chunk + concurrency (Section 4), never disables a chunk.

Validation: all 162 rows of `s5_final_trades.csv` classify correctly under this rule; `long` iff `rate < 0` (162/162 match).

---

## 4. Account level detection (startup + before every trade)

Bot har trade se pehle equity (`account balance`, paper = simulated equity) padhta hai aur level select karta hai. Level = leverage cap + coins/chunk + concurrent positions.

| Level | Equity (USDT) | Leverage | Margin frac | Max coins/chunk | Max concurrent | Note |
|-------|---------------|----------|-------------|-----------------|----------------|------|
| L0 Seed | `< 100` | 20x | 0.70 | 1 | 3 | fees ke against sabse fragile |
| L1 Micro | `100 – 199` | 20x | 0.70 | 1 | 6 | |
| L2 Small | `200 – 499` | 20x | 0.70 | 2 | 12 | best-setup start = 200 |
| L3 Standard | `500 – 1999` | 20x¹ | 0.70 | 3 | 18 | ¹30x only if opt-in |
| L4 Pro | `>= 2000` | 20x¹ | 0.70 | 5 | 30 | ¹30x only if opt-in |

**Effective leverage = `min(tier_leverage, BOT_LEVERAGE_CAP)`.** Default `BOT_LEVERAGE_CAP=20`, so system **always 20x** unless user deliberately raises cap + `BOT_ALLOW_30X=1`.

- 30x is riskier (DD ≈ -19% in research vs -16.4% at 20x). User directive = 20x, so default OFF.
- Level recomputed at startup **and** before every entry (equity changes → level may change).
- If equity `< BOT_MIN_EQUITY` (default 50) → no new trades (fees eat capital).

Coins/chunk selection: within each chunk, rank eligible coins by 24h turnover desc, take top `max_coins_per_chunk`. This is the only place "coins/chunk" limits apply.

---

## 5. Leverage & margin policy

```
leverage    = min(account_level.leverage, BOT_LEVERAGE_CAP)   # default 20
margin_frac = min(BOT_MARGIN_FRAC, 0.70)                      # default 0.70
```
- 20x baseline: `margin_frac 0.70` → max 70% equity as margin per position.
- Hard invariant: `notional <= equity × 0.70 × leverage`.
- 30x only if `BOT_ALLOW_30X=1 AND BOT_LEVERAGE_CAP>=30 AND equity>=500`.
- Cross vs isolated: paper assumes **isolated** per-position margin (one position can't drain whole account). Live must use isolated margin.
- Liquidation buffer at 20x ≈ 5% adverse move; 2-minute holding window makes this acceptable, but bot still flattens at T+1 min unconditionally.

---

## 6. Position sizing (volume-based, TRAP-AVOID)

Exact formula (task directive):

```
base_position = clamp(0.005 * turnover_24h, MIN_POS=5, MAX_POS=2000)   # USDT margin budget
margin_used   = min(base_position, 0.70 * equity)                      # never > 70% equity
notional      = margin_used * leverage                                 # leverage applied
```

Properties:
- `turnover` kam → `base` chhota → position chhota (illiquid coin me trap nahi).
- `turnover` zyada → `base` bada, but **hard cap 2,000 USDT margin**.
- `0.5%` liquidity rule: capital-at-risk per trade ≈ 0.5% of the coin's 24h turnover (at 20x, liquidation ≈ margin), so a single exit can't consume more than ~0.5% of daily liquidity.
- `margin_used <= 0.7 × equity` = max 70% account on one trade.
- `notional <= equity × 0.7 × leverage` = liquidation guard (Section 8).

Worked examples (leverage 20x, margin_frac 0.70):

| turnover_24h | base (0.5%, clamp) | equity 200 → margin | notional | equity 5000 → margin | notional |
|---|---|---|---|---|---|
| 10,000 | 50 | 50 | 1,000 | 50 | 1,000 |
| 1,000,000 | 2,000 (cap) | 140 (0.7×200) | 2,800 | 2,000 | 40,000 |
| 50,000 | 250 | 140 | 2,800 | 250 | 5,000 |

> Sizing uses the **snapshot** in `delta_volume.json` for backtest/replay, and live `/v2/tickers` `turnover_usd` for live/paper runs. If turnover missing → skip coin (never guess).

---

## 7. Signal detection (T-1 entry, T+1 exit)

**Funding timestamps (UTC):** `BOT_FUNDING_HOURS=0,8,16` (configurable). Delta perp funding typically 00:00 / 08:00 / 16:00 UTC; verify per contract before live.

For a funding time `T` (UTC):
```
entry_time = T - 1 minute    # use 1m candle OPEN at this minute
exit_time  = T + 1 minute    # use 1m candle OPEN at this minute
rate       = funding_rate in effect at T   (percent)
```

Algorithm per candidate coin:
1. Get `rate` (live ticker just before entry, or recorded funding rate in replay).
2. `chunk = chunk_of(abs(rate))`; if `< 0.10` → skip.
3. `side = LONG if rate < 0 else SHORT`.
4. Check risk gates (Section 8): DD, 1-trade/day/coin, max concurrent, min equity.
5. Size (Section 6).
6. `entry = open` of candle at/covering `entry_time`; execute paper fill.
7. At `exit_time`, `exit = open` of candle at `exit_time`; close, book PnL.
8. Write trade record + update equity/state.

**1 trade/day/coin rule:** key = `(symbol, UTC date of T)`. If already traded that day → skip all later windows for that coin. (Backtest shows 1/day/coin; bot enforces it.)

**Funding rate source in live:** call ticker at `T-1min` and cache it; do **not** use the rate that appears after T. In replay, use the rate stored in `s5_final_trades.csv`.
**Candle lookup:** request `resolution=1m`, `start=T-120s`, `end=T+120s`; match candle `time == entry/exit minute` (bucket start). If exact minute missing → abort trade (data quality guard), never extrapolate.

---

## 8. PnL accounting

For each trade (position notional = `N`, funding rate `r` in %, holding entry→exit):

```
funding_pnl_pct = abs(r)                                  # funding capture (always positive)
price_move_pct  = (exit-entry)/entry*100  if LONG
                = (entry-exit)/entry*100  if SHORT
fee_pct         = BOT_FEE_PCT (default 0.05, applied on exit)
net_pct         = funding_pnl_pct + price_move_pct - fee_pct
net_usdt        = N * net_pct / 100
equity_new      = equity + net_usdt
```

Reference: `s5_final_trades.csv` uses fixed 200 USDT notional and `net_pnl_usdt = 2*(funding_pnl + price_pnl - fee)`; sum = 80.62 USDT unlevered. Compounded 20x setup grows 200 → 4214 with max DD -16.44% (`delta_level_best_20x.json`).

Slippage: `BOT_SLIPPAGE_PCT` (default 0.0 for parity; set 0.02–0.05 in live sanity runs). Applied as a cost on net_pct.

---

## 9. Risk management (BOT RULES — hard)

| Rule | Value | Action |
|------|-------|--------|
| Max drawdown | **20%** from equity peak (closed + unrealized) | Flatten all, halt new entries, kill switch |
| DD warning | 15% | Log alert, **halve** position size |
| 1 trade/day/coin | 1 | Skip duplicate (symbol, UTC date) |
| Min equity | 50 USDT | No new trades |
| Max concurrent positions | per level (3–30) | Queue/skip excess |
| Liquidation guard | `notional <= equity*0.7*leverage` | Reject oversized order |
| Liquidity guard | `base <= 0.5% turnover`, cap 2000 | Reject/shrink |
| Funding threshold | `abs(rate) >= 0.10%` | Skip |
| Rate sanity | `abs(rate) <= 5%` | Skip (bad data) |
| Per-trade time stop | exit at `T+1 min` unconditionally | No hold-over |
| Daily loss soft stop | 8% of start-of-day equity (`BOT_DAILY_LOSS_PCT`) | Halt for the UTC day |
| Data staleness | ticker/candle older than 120s | Skip trade |
| Exchange/API error | 3 consecutive failures | Backoff, then halt if unresolved |

- **No sleep between funding events / no skipping chunks** — sab eligible funding windows evaluate honge (subject to 1/day/coin).
- DD is measured on **peak-to-trough equity**, not per-trade. Peak resets only on new equity high.
- Kill switch = write state `halted=true` + reason; requires manual clear.

---

## 10. Execution state machine

```
STARTUP
  -> load state (equity, peak, traded_today, halted)
  -> detect account level from equity
  -> if halted: exit (manual clear needed)
SCAN (every funding window)
  -> if now not in [T-1min, T+1min]: idle
  -> fetch tickers for universe; compute signals
  -> for each candidate (ranked by |rate| then turnover):
       gates: threshold, sanity, chunk, 1/day/coin, concurrent, DD, min equity
       -> size -> paper/live entry at T-1m open
POSITION OPEN
  -> wait until T+1m
  -> exit at T+1m open
  -> compute pnl -> update equity/peak/DD -> persist trade
  -> if DD >= 20%: halt
MONITOR (every 30s while open)
  -> check DD warning/stop, position age, API health
```

---

## 11. Paper → Live gating

- Default mode: **`paper`**. No orders ever sent.
- Live requires ALL of:
  1. `BOT_MODE=live`
  2. `BOT_LIVE_APPROVED=1` (explicit user approval)
  3. `DELTA_API_KEY` + `DELTA_API_SECRET` present in env
  4. `--i-understand-live-risk` CLI flag
- If any missing → bot refuses to start live, stays paper, logs reason.
- Credentials **never** hardcoded, never logged. Signature = HMAC-SHA256 (stdlib `hmac`/`hashlib`) over `method + timestamp + path+query + body`.
- Live order placement is intentionally **stubbed** in the skeleton (raises `NotImplementedError` after gate checks) until a reviewed release.
- Paper results must be reviewed by user before flipping live. Minimum: 5 verified paper trades + spec sign-off.

---

## 12. Monitoring & alerts (30s cadence)

- Heartbeat log: mode, level, equity, peak, DD%, open positions, trades today, halted flag.
- Alert thresholds: DD `>=15%` WARN, `>=20%` STOP, API fail `>=3`.
- Persist: `delta_bot_state.json` (equity, peak, DD, halted, traded_today) + `delta_bot_paper_trades.csv` (append-only trade log).
- Alerts are stdout + optional `BOT_ALERT_WEBHOOK` (HTTP POST, stdlib `urllib`).

---

## 13. Config / env vars (no secrets in code)

| Env var | Default | Meaning |
|---------|---------|---------|
| `DELTA_BASE_URL` | `https://api.india.delta.exchange` | API base |
| `DELTA_API_KEY` | — | live key (required only for live) |
| `DELTA_API_SECRET` | — | live secret (required only for live) |
| `BOT_MODE` | `paper` | `paper` \| `live` |
| `BOT_LIVE_APPROVED` | `0` | must be `1` for live |
| `BOT_START_EQUITY` | `200` | paper starting equity |
| `BOT_STATE_FILE` | `delta_bot_state.json` | state path |
| `BOT_TRADES_FILE` | `delta_bot_paper_trades.csv` | trade log path |
| `BOT_VOLUME_FILE` | `delta_volume.json` | turnover snapshot |
| `BOT_TRADES_CSV` | `s5_final_trades.csv` | replay source |
| `BOT_LEVERAGE_CAP` | `20` | hard leverage ceiling |
| `BOT_ALLOW_30X` | `0` | enable 30x tier (opt-in) |
| `BOT_MARGIN_FRAC` | `0.70` | max equity as margin |
| `BOT_TURNOVER_FRAC` | `0.005` | 0.5% of turnover |
| `BOT_MIN_POS` / `BOT_MAX_POS` | `5` / `2000` | position band (USDT margin) |
| `BOT_MIN_TURNOVER` | `5000` | universe liquidity floor |
| `BOT_MIN_EQUITY` | `50` | no trades below |
| `BOT_MAX_DD` | `0.20` | hard stop |
| `BOT_DD_WARN` | `0.15` | warning / half size |
| `BOT_FEE_PCT` | `0.05` | exit fee % |
| `BOT_SLIPPAGE_PCT` | `0.0` | extra cost % |
| `BOT_FUNDING_HOURS` | `0,8,16` | UTC funding hours |
| `BOT_DAILY_LOSS_PCT` | `0.08` | intraday halt |
| `BOT_MAX_CONCURRENT` | per level | override concurrency |
| `BOT_SCAN_LIMIT` | `400` | max tickers fetched per scan |
| `BOT_ALERT_WEBHOOK` | — | optional alert URL |

---

## 14. Backtest validation reference

- Source: `s5_final_trades.csv` — 162 trades, 35 coins, 2026-05-29 → 2026-11-11, 1 trade/day/coin.
- Chunk distribution: C1 58 · C2 39 · C3 26 · C4 17 · C5 14 · C6 8.
- Sides: LONG 95 / SHORT 67 (rate-negative → long, rate-positive → short; 162/162 correct).
- Best setup (`delta_level_best_20x.json`): 20x, 70% margin, start 200 → 4214.43, net +4014.43, **max DD -16.44%** (< 20% limit), 162 trades.
- Chunk PnL (unlevered, USDT): C5 +32.0, C2 +16.8, C1 +11.5, C3 +7.8, C4 +6.5, C6 +6.0.

`delta_bot.py --selftest` reproduces the chunk/side/PnL classification on all 162 rows, then re-runs the same 20x/70%-margin compounding formula and gets **final 4214.40 / max DD -16.44%** (reference 4214.43 / -16.44%). It also books the first 5 trades through the full risk engine and (with `--persist`) writes `delta_bot_state.json` + `delta_bot_paper_trades.csv`.

---

## 15. Known limitations / live cautions

1. **Funding schedule varies by contract** — verify 8h/4h/1h before live; wrong T = missed/incorrect capture.
2. **Funding rate used must be the one applied at T**, not post-T ticker. Skeleton fetches at T-1m; live should snapshot and reconcile with exchange funding history.
3. **Notional = margin × leverage** can exceed 0.5% of turnover; the trap guard is on margin (capital-at-risk), which at 20x ≈ liquidation distance. For very illiquid coins, also enforce exchange max order size.
4. **Slippage/impact** not modeled in baseline (0%). Live 2-min round trip in thin books can eat funding edge → always paper + slippage stress first.
5. **1/day/coin picks first qualifying window**, not theoretically best window of the day.
6. **Cross-margin** must not be used — one bad coin can liquidate the account.
7. No funding if position opened after snapshot cutoff / closed before settlement; some venues snapshot at T-1 min exactly → timing race risk.
8. Backtest assumes fills at candle OPEN; live market orders will differ.
9. `s5_final_trades.csv` `net_pnl_usdt` uses fixed 200 notional; bot uses dynamic sizing → expect different absolute PnL, same edge direction.

---

## 16. File map / how to run

```
bot_logic_spec.md            # this file — single source of truth for all rules
delta_bot.py                 # bot skeleton (stdlib only)
delta_volume.json            # 24h turnover snapshot (1158 symbols)
s5_final_trades.csv          # 162-trade validation set
delta_level_best_20x.json    # best-setup reference result
delta_bot_state.json         # runtime state (created by bot)
delta_bot_paper_trades.csv   # paper trade log (created by bot)
```

```bash
# offline validation + 5-trade paper verify (no network, no keys)
python3 delta_bot.py --selftest

# live current funding scan (read-only tickers, paper sizing)
python3 delta_bot.py --scan

# simulate one funding window (paper, uses candles; --at for past UTC time)
python3 delta_bot.py --paper-window --at 2026-11-12T08:00:00Z

# show state/equity/DD
python3 delta_bot.py --status

# LIVE (refused unless all gates pass; not implemented in skeleton)
python3 delta_bot.py --live --i-understand-live-risk
```
