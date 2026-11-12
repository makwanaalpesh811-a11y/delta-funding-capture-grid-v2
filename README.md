# Delta Funding Capture — Balance-Scaled Grid v2 (20x)

**v2: Balance-scaled progressive grid — jese balance badhta hai, wese zyada coins/chunk add hote hain. 20x leverage, 70% margin.**

## 📊 Result Summary (20x Locked)

| Metric | Value |
|--------|-------|
| Exchange | Delta India (perpetual_futures) |
| Grid | **Balance-scaled** (progressive tiers) |
| Leverage | **20x (70% margin, compounding)** |
| Coins | 66 (watchlist) |
| Trades | 139 |
| Win rate | 75.9% |
| **Net PnL** | **+$3,744.90** |
| **Final equity** | **$3,944.90** (start $200) |
| **Max drawdown** | **-16.22%** |
| Monthly positive | 6/7 (Sep -$78) |
| P/L line cross | ❌ NO |

## 🧠 Strategy Logic (Balance-Scaled)

### 1. Balance Tiers — Progressive Grid
Jese balance badhta hai, wese zyada coins/chunk add:

| Balance | Coins/Chunk | Chunks Active | Trades/Day |
|---------|------------|---------------|-----------|
| $1-50 | 1 | C6 | 1 |
| $50-200 | 1 | C6, C5 | 1-2 |
| $200-500 | 2 | C6,C5,C4,C3 | 2-4 |
| $500-1000 | 3 | C6-C2 | 3-5 |
| $1,000+ | 5 | C6-C1 (sab) | 5+ |

**Rule:** Chhota balance → C6 focus (high funding, high profit/trade). Grow → expand chunks.

### 2. Signal (Funding Capture)
- Funding NEGATIVE → LONG, POSITIVE → SHORT
- Entry = (T−1 min) OPEN, Exit = (T+1 min) OPEN, fee 0.05%

### 3. Position Sizing (20x, 70% margin)
```
base_position = 0.5% of 24h turnover, clamped [5, 2000] USDT
position = min(base_position, 0.7 * equity) * 20   (20x leverage)
```

### 4. Leverage Safety
- **20x → DD -16.2%** — 20% limit ke andar ✅
- **25x → DD -20.4%** — 20% ke ~karib
- **30x → DD -24.1%** — bahar (risky)

## 📂 Files

| File | Description |
|------|-------------|
| `DELTA_GRID_STRATEGY.md` | Base strategy documentation |
| `delta_balance_grid_20x.json` | **20x equity curve + report** |
| `delta_balance_grid.json` | Balance grid (1x) results |
| `balance_grid_delta.py` | Balance grid backtest script |
| `grid_delta_result.json` | Grid backtest results |
| `s5_final_trades.csv` | 162 trades |
| `s5_final_coins.csv` | 66 coins |
| `delta_volume.json` | Volume data |

## ⚠️ Limitations

- Data sirf 167d (7 months) — 12-month consistency unproven
- Sep 2026 loss (-$78) — 1 negative month
- Stress test negative (leverage kam rakho)

## ⚠️ Disclaimer

Backtest only. Past performance ≠ future results. Not financial advice.
