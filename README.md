# Delta Funding Capture — Balance-Scaled Grid v2 (Best Setup 20x)

**v2 BEST: Level-based setup — har balance level pe 20x leverage, sab chunks (C1-C6) available. C1 me trade available ho to bhi chalta hai.**

## 📊 Result Summary (20x, All Chunks — BEST)

| Metric | Value |
|--------|-------|
| Exchange | Delta India (perpetual_futures) |
| Grid | Sab chunks (C1-C6) active |
| Leverage | **20x (70% margin, compounding)** |
| Coins | 66 (watchlist) |
| Trades | 162 |
| Win rate | 75.9% |
| **Net PnL** | **+$4,014.43** |
| **Final equity** | **$4,214.43** (start $200) |
| **Max drawdown** | **-16.44%** |
| Monthly positive | 6/7 (Sep -$78) |
| P/L line cross | ❌ NO |

## 🧠 Strategy Logic (Best Setup)

### 1. Leverage (Level-Based)
- **20x leverage, 70% margin, compounding** — sabse best (DD -16.4%)
- $500+ balance → 30x (DD -19%, +$9,938)
- Chhota balance ($1) → bhi 20x kaam karta (DD -17.9%)

### 2. Chunks (C1-C6 ALL)
- **Sab chunks available** — C1 me trade available ho to bhi chalta hai
- Koi chunk skip nahi (162 trades, sab)
- Funding NEGATIVE → LONG, POSITIVE → SHORT

### 3. Position Sizing (20x)
```
base_position = 0.5% of 24h turnover, clamped [5, 2000] USDT
position = min(base_position, 0.7 * equity) * 20   (20x leverage)
```

### 4. Entry/Exit
- Entry = (T−1 min) OPEN, Exit = (T+1 min) OPEN, fee 0.05%

## 🤖 Bot Logic (delta_bot.py)

Complete rules in `bot_logic_spec.md` + `delta_bot.py` (951 lines, pure stdlib):
- **Account levels L0-L4** → leverage/coins-per-chunk/concurrency
- **20x baseline, 70% margin** (30x opt-in)
- **Chunks C1-C6 all active** — no skip
- **Sizing:** `base=clamp(0.5%·turnover, 5, 2000)` → `margin=min(base, 0.7·equity)` → `notional=margin·leverage`
- **Signal:** T−1min entry / T+1min exit, negative→LONG, positive→SHORT
- **Risk:** 20% DD hard stop, 15% warning+half size, 1 trade/day/coin, daily loss stop
- **$1 se bhi chalta hai** (DD -17.9%, +$111)
- Commands: `--selftest --scan --paper-window --status --live`

## 📂 Files

| File | Description |
|------|-------------|
| `delta_level_best_20x.json` | **20x all-chunks equity curve + report** |
| `delta_progressive_grid.json` | Progressive leverage (20x→2x) |
| `delta_stage_grid_*.json` | Stage grid ($1, $50, $200) |
| `delta_balance_grid_20x.json` | Balance grid 20x |
| `DELTA_GRID_STRATEGY.md` | Base strategy doc |
| `s5_final_trades.csv` | 162 trades |
| `s5_final_coins.csv` | 66 coins |
| `delta_volume.json` | Volume data |

## ⚠️ Limitations

- Data sirf 167d (7 months) — 12-month consistency unproven
- Sep 2026 loss (-$78) — 1 negative month
- Stress test negative (leverage kam rakho)

## ⚠️ Disclaimer

Backtest only. Past performance ≠ future results. Not financial advice.
