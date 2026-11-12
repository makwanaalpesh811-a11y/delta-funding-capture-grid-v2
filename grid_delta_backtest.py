#!/usr/bin/env python3
"""
Delta Funding Capture — Grid Backtest (Volume-Based Sizing + P/L Line)
Similar to Bybit grid, but on Delta S5 1-trade/day data.

Position sizing: pos = LIQ_FRAC * turnover24h, capped [MIN_POS, MAX_POS].
Volume-based: 10k volume -> smaller position, high volume -> bigger (trap-avoid).
Grid: top-N most liquid coins per chunk, test N=1,2,3,5,10,15,20.
P/L line: equity curve break-even (0) cross karta hai ya nahi.
"""
import csv, json, math, datetime
from collections import defaultdict

# Config
LIQ_FRAC = 0.005        # 0.5% of turnover
MIN_POS = 5.0
MAX_POS = 2000.0
BASE_POS = 200.0        # reference (CSV PnL is per 200 USDT)
FEE = 0.05
CHUNKS = ['C1','C2','C3','C4','C5','C6']

# Load volume
vol = json.load(open('delta_volume.json'))

# Load Delta S5 trades
rows = list(csv.DictReader(open('s5_final_trades.csv')))
print(f'Delta S5 trades: {len(rows)}, columns: {list(rows[0].keys())}')

# Build per-chunk, per-coin stats
# For each trade: symbol, chunk, pnl (net_pnl_usdt)
# Need turnover per symbol
# normalize chunk (Delta format 'C3_0.4-0.6' -> 'C3')
def norm_chunk(c):
    return c.split('_')[0] if '_' in c else c

trades = []
for r in rows:
    sym = r['symbol']
    chunk = norm_chunk(r['chunk'])
    pnl = float(r['net_pnl_usdt'])
    trades.append({'symbol': sym, 'chunk': chunk, 'pnl': pnl})

# turnover lookup (some Delta symbols may not match; use symbol directly)
def get_turnover(sym):
    # try direct, then USD variant
    for s in [sym, sym.replace('USD','USD')]:
        if s in vol:
            return vol[s]['turnover']
    # try find by base
    return None

# per-chunk available coins with turnover
chunk_coins = defaultdict(dict)
for t in trades:
    c = t['chunk']; s = t['symbol']
    to = get_turnover(s)
    if to is not None:
        chunk_coins[c][s] = max(chunk_coins[c].get(s, 0), to)

print('Chunk coins with turnover:')
for c in CHUNKS:
    print(f'  {c}: {len(chunk_coins[c])} coins')

# Position sizing function
def position_size(turnover):
    if turnover is None or turnover <= 0:
        return MIN_POS
    pos = LIQ_FRAC * turnover
    return max(MIN_POS, min(MAX_POS, pos))

# Grid: top-N coins per chunk by turnover, then backtest
def run_combo(combo, label):
    """combo: dict chunk->N coins per chunk (top-N by turnover)."""
    # pick allowed symbols per chunk
    allowed = {}
    for c in CHUNKS:
        n = combo.get(c, 0)
        if n <= 0:
            allowed[c] = set()
            continue
        sorted_coins = sorted(chunk_coins[c].items(), key=lambda x: -x[1])
        allowed[c] = {s for s, _ in sorted_coins[:n]}
    # filter trades
    picks = [t for t in trades if t['symbol'] in allowed.get(t['chunk'], set())]
    if not picks:
        return {'label': label, 'trades': 0, 'net': 0, 'win': 0, 'dd': 0, 'months': [0,0], 'cross': True, 'valid': False}
    # position-size weighted PnL (equity starts at 200)
    net = 0.0; wins = 0
    eq = 200.0; peak = 200.0; maxdd = 0.0; cross = False
    monthly = defaultdict(float)
    for t in picks:
        to = get_turnover(t['symbol'])
        pos = position_size(to)
        scaled = t['pnl'] * (pos / BASE_POS)
        net += scaled
        if scaled > 0: wins += 1
        eq += scaled
        peak = max(peak, eq)
        if eq < 200 and not cross:
            cross = True  # crossed break-even below start
        dd = (eq - peak) / peak * 100 if peak > 0 else 0
        maxdd = min(maxdd, dd)
    return {
        'label': label, 'trades': len(picks), 'net': round(net, 2),
        'win': round(wins / len(picks) * 100, 1), 'dd': round(maxdd, 2),
        'cross': cross, 'valid': True
    }

# Uniform grid: same N every chunk
print('\n=== Uniform Grid (N coins/chunk) ===')
uniform = {}
for n in [1, 2, 3, 5, 10, 15, 20]:
    combo = {c: n for c in CHUNKS}
    res = run_combo(combo, f'N={n}')
    uniform[n] = res
    verdict = 'PASS' if res['valid'] and not res['cross'] else 'FAIL'
    print(f"  N={n:2d}: {res['trades']:3d} tr, net {res['net']:+8.2f}, win {res['win']:5.1f}%, DD {res['dd']:6.2f}%, cross={res['cross']} {verdict}")

# Mixed search: try different N per chunk (simplified: 1,2,3,5,10,15,20 combos)
print('\n=== Mixed Search (limited) ===')
best = None
import itertools
vals = [1, 2, 3, 5, 10, 15, 20]
count = 0
for combo in itertools.product(vals, repeat=6):
    d = {c: combo[i] for i, c in enumerate(CHUNKS)}
    res = run_combo(d, str(d))
    count += 1
    if res['valid'] and not res['cross'] and res['net'] > 0:
        if best is None or res['net'] > best['net']:
            best = res
print(f'  Mixed combos tested: {count}')
if best:
    print(f"  Best: {best['label']} -> {best['trades']} tr, net {best['net']}, win {best['win']}%, DD {best['dd']}%")

# Save
result = {
    'uniform': {str(k): v for k, v in uniform.items()},
    'best_mixed': best,
    'config': {'liq_frac': LIQ_FRAC, 'min_pos': MIN_POS, 'max_pos': MAX_POS, 'base': BASE_POS}
}
json.dump(result, open('grid_delta_result.json', 'w'), indent=2)
print('\nSaved grid_delta_result.json')
