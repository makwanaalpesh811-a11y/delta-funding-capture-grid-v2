# Delta Funding Capture — Grid Strategy (Volume-Based Sizing)

**Final strategy: Delta Exchange India perpetual futures, funding capture, grid N=20 (volume-scaled position).**

## 📊 Result Summary

| Metric | Value |
|--------|-------|
| Exchange | Delta India (perpetual_futures) |
| Grid | N=20 coins/chunk (volume-ranked) |
| Coins | 66 (watchlist, all chunks) |
| Trades | 162 (1/day, 167d data) |
| Win rate | 75.9% |
| Net PnL | +$459.62 (volume-scaled) |
| Final equity | $659.62 (start $200) |
| Max drawdown | -6.4% |
| P/L line cross | ❌ NO (break-even line safe) |
| Monthly positive | 7/7 (100%) |
| Walk-forward | PASS (OOS 76.2% win) |
| Luck check | PASS (no-top5 +$59) |

## 🧠 Strategy Logic

### 1. Signal (Funding Capture)
- **Funding NEGATIVE → LONG** | **Funding POSITIVE → SHORT**
- T = funding time (UTC)
- Entry = (T−1 min) candle OPEN
- Exit = (T+1 min) candle OPEN
- Fee 0.05% on exit only

### 2. Chunk Selection (|funding| %)
| Chunk | Range | Priority |
|-------|-------|----------|
| C6 | 1.0%+ | Highest |
| C5 | 0.8–1.0% | |
| C4 | 0.6–0.8% | |
| C3 | 0.4–0.6% | |
| C2 | 0.2–0.4% | |
| C1 | 0.1–0.2% | Lowest |

### 3. Grid (N=20 per chunk)
- Har chunk me **top-20 most liquid coins** (24h turnover se ranked)
- Priority: C6 → C5 → C4 → C3 → C2 → C1
- 1 trade/day — best coin per day by chunk priority + Wilson score

### 4. Volume-Based Position Sizing (TRAP-AVOID)
```
position = 0.5% of 24h turnover
position = clamp(position, MIN 5 USDT, MAX 2000 USDT)
```

| 24h Turnover | Position Size |
|-------------|--------------|
| 10k | $50 |
| 50k | $250 |
| 100k | $500 |
| 250k | $1,250 |
| 500k+ | $2,000 (cap) |

**Rule:** Jo coin ka turnover KAM → chhota position (trap avoid). Jo ZYADA → bada (cap $2,000). Position kabhi 0.5% turnover se zyada nahi.

### 5. Profit/Loss Line
- Equity curve break-even (0) **CROSS nahi karti** — N=3+ se safe
- N=1/2 pe CROSS karti hai (FAIL) — **kam se kam 3 coins/chunk chahiye**

## 📂 Files

| File | Description |
|------|-------------|
| `grid_delta_result.json` | Grid backtest results (all N + mixed) |
| `delta_grid_equity.json` | N=20 equity curve |
| `s5_final_trades.csv` | 162 trades (1/day) |
| `s5_final_coins.csv` | 66 coins watchlist |
| `grid_delta_backtest.py` | Grid backtest script |
| `delta_volume.json` | 24h volume data (1158 symbols) |

## ⚠️ Limitations

- **Data sirf 167d** (May 29–Nov 11, 2026) — 7 months, 12 nahi
- In-sample bias (walk-forward partially mitigates)
- Volume snapshot current (24h) — live me refresh karna
- Stress test: exit worst + slippage → negative (leverage kam rakho)

## ⚠️ Disclaimer

Backtest only. Past performance ≠ future results. Leverage >5x carries high risk. Not financial advice.
