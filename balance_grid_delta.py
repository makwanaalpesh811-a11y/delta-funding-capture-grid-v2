#!/usr/bin/env python3
"""
Delta Funding Capture — Balance-Scaled Progressive Grid Backtest.
Jese balance badhta hai, wese zyada coins/chunk add hote hain.

Balance tiers:
  Tier 0: $1-50    -> C6 top 1 only (1 trade/day)
  Tier 1: $50-200  -> C6+C5 top 1 each (up to 2 trades/day)
  Tier 2: $200-500 -> top 1 each C6,C5,C4,C3 (up to 4 trades/day)
  Tier 3: $500+    -> full strategy (top N by balance)

Balance-based: kam balance -> C6 pe focus (high funding), grow -> expand.
"""
import csv, json, math
from collections import defaultdict

vol = json.load(open('delta_volume.json'))
rows = list(csv.DictReader(open('s5_final_trades.csv')))
def norm(c): return c.split('_')[0] if '_' in c else c

# Build trade list (chronological by date)
trades = []
for r in rows:
    trades.append({'symbol': r['symbol'], 'chunk': norm(r['chunk']),
                   'pnl': float(r['net_pnl_usdt']), 'date': r['date']})
trades.sort(key=lambda t: t['date'])

def get_to(s): return vol.get(s, {}).get('turnover', 0)

# Per-chunk top coins by turnover (liquidity rank)
chunk_coins = defaultdict(dict)
for t in trades:
    to = get_to(t['symbol'])
    chunk_coins[t['chunk']][t['symbol']] = max(chunk_coins[t['chunk']].get(t['symbol'], 0), to)

# Balance tier -> allowed coins per chunk
# Balance chhota: sirf C6/C5 (high funding). Balance bada: sab chunks expand.
def allowed_coins(balance):
    """Balance ke hisaab se har chunk ke top-N coins allowed."""
    tiers = [
        # (balance_min, coins_per_chunk, which_chunks)
        (0,    1, ['C6']),                    # $1-50: C6 top1 only
        (50,   1, ['C6', 'C5']),              # $50-200: C6+C5 top1
        (200,  2, ['C6', 'C5', 'C4', 'C3']),  # $200-500: top2 each
        (500,  3, ['C6', 'C5', 'C4', 'C3', 'C2']),
        (1000, 5, ['C6', 'C5', 'C4', 'C3', 'C2', 'C1']),  # full
    ]
    for bmin, n, chunks in reversed(tiers):
        if balance >= bmin:
            return {c: n for c in chunks}, n, chunks
    return {'C6': 1}, 1, ['C6']

# Simulate balance-scaled grid
# Start balance, grow by net pnl each trade (fixed 200 base position scaled by balance?)
# User: balance ke hisab se trade. Position = balance-based (chhota balance = chhota position)
# Simpler: balance grows, position = min(balance, some cap), trades = allowed per tier

def run_balance_grid(start_balance=200.0, pos_frac=0.7):
    """Simulate balance-scaled grid. pos = pos_frac * balance (margin), leverage 1x."""
    balance = start_balance
    peak = balance
    maxdd = 0.0
    trades_done = 0
    daily_trades = 0
    curve = []
    by_tier = defaultdict(int)
    for t in trades:
        # allowed coins for current balance
        allowed, n, chunks = allowed_coins(balance)
        # pick trades: this trade's symbol in allowed? (already 1/day, check chunk allowed)
        if t['chunk'] in allowed:
            to = get_to(t['symbol'])
            base_pos = max(5, min(2000, 0.005 * to))
            # position scales with balance (70% margin, but not more than base_pos)
            pos = min(base_pos, pos_frac * balance)
            scaled = t['pnl'] * (pos / 200.0)  # pnl per 200 notional
            balance += scaled
            peak = max(peak, balance)
            maxdd = min(maxdd, (balance - peak) / peak * 100)
            trades_done += 1
            by_tier[t['chunk']] += 1
            curve.append([t['date'], round(balance, 2)])
    return {'final': balance, 'net': balance - start_balance, 'dd': maxdd,
            'trades': trades_done, 'curve': curve, 'by_tier': dict(by_tier)}

# Run from different start balances
print('=== BALANCE-SCALED GRID (70% margin, 1x) ===')
for start in [1, 10, 50, 100, 200, 500, 1000]:
    res = run_balance_grid(start)
    print(f'Start ${start:>4}: final ${res["final"]:>9,.2f}, net ${res["net"]:>+9,.2f}, '
          f'DD {res["dd"]:6.1f}%, {res["trades"]} trades')

# Save $200 result
res200 = run_balance_grid(200)
json.dump(res200, open('delta_balance_grid.json', 'w'), indent=2)
print('\nSaved delta_balance_grid.json (start $200):')
print(f'  final ${res200["final"]:,.2f}, net ${res200["net"]:+,.2f}, DD {res200["dd"]:.1f}%')
print(f'  trades {res200["trades"]}, by tier: {res200["by_tier"]}')
