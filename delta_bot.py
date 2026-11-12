#!/usr/bin/env python3
"""
delta_bot.py -- Delta Funding Capture Grid bot (skeleton, PAPER-TRADE FIRST).

Pure Python 3 standard library only. No pip, no third-party packages.

What it does
------------
Captures perp funding: enter at (T-1 min) candle OPEN, exit at (T+1 min) OPEN,
where T = funding timestamp (UTC). Funding negative -> LONG, positive -> SHORT.
Position sizing is volume based so illiquid coins never get trapped.

All rules live in bot_logic_spec.md. This file is the executable counterpart.

Safety
------
* Default mode is PAPER. No orders are ever sent in paper mode.
* LIVE is refused unless BOT_MODE=live, BOT_LIVE_APPROVED=1,
  DELTA_API_KEY/DELTA_API_SECRET are set, AND --i-understand-live-risk is passed.
* Live order placement is intentionally stubbed (skeleton) until reviewed.
* Credentials come from environment only -- never hardcoded, never logged.

Usage examples
--------------
    python3 delta_bot.py --selftest            # offline: validate 162 trades + 5-trade paper verify
    python3 delta_bot.py --selftest --persist  # same, and write state + trade log files
    python3 delta_bot.py --scan                # live tickers, read-only, paper sizing preview
    python3 delta_bot.py --paper-window --at 2026-11-12T08:00:00Z
    python3 delta_bot.py --status              # state / equity / drawdown
    python3 delta_bot.py --reset               # reset paper state to start equity
    python3 delta_bot.py --live --i-understand-live-risk   # gated; not implemented
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

LOG = logging.getLogger("delta_bot")

# --------------------------------------------------------------------------- #
# Static model (see bot_logic_spec.md sections 3, 4)
# --------------------------------------------------------------------------- #

# Chunk band = abs(funding_rate) in PERCENT. lower inclusive, upper exclusive.
CHUNK_BANDS: List[Tuple[str, float, float]] = [
    ("C1_0.1-0.2", 0.10, 0.20),
    ("C2_0.2-0.4", 0.20, 0.40),
    ("C3_0.4-0.6", 0.40, 0.60),
    ("C4_0.6-0.8", 0.60, 0.80),
    ("C5_0.8-1.0", 0.80, 1.00),
    ("C6_1.0+", 1.00, float("inf")),
]

# Account levels: (name, min_equity, max_equity, coins_per_chunk, max_concurrent)
ACCOUNT_TIERS: List[Tuple[str, float, float, int, int]] = [
    ("L0_Seed", 0.0, 100.0, 1, 3),
    ("L1_Micro", 100.0, 200.0, 1, 6),
    ("L2_Small", 200.0, 500.0, 2, 12),
    ("L3_Standard", 500.0, 2000.0, 3, 18),
    ("L4_Pro", 2000.0, float("inf"), 5, 30),
]

# Reference best setup (delta_level_best_20x.json) for selftest assertion.
REFERENCE_FINAL_EQUITY = 4214.43
REFERENCE_MAX_DD_PCT = -16.44


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


def _env_str(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        LOG.warning("bad float env %s=%r, using %s", name, os.environ.get(name), default)
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        LOG.warning("bad int env %s=%r, using %s", name, os.environ.get(name), default)
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass
class Config:
    base_url: str = "https://api.india.delta.exchange"
    api_key: str = ""
    api_secret: str = ""
    mode: str = "paper"
    live_approved: bool = False
    start_equity: float = 200.0
    state_file: str = "delta_bot_state.json"
    trades_file: str = "delta_bot_paper_trades.csv"
    volume_file: str = "delta_volume.json"
    trades_csv: str = "s5_final_trades.csv"

    leverage_cap: int = 20
    allow_30x: bool = False
    margin_frac: float = 0.70
    turnover_frac: float = 0.005
    min_pos: float = 5.0
    max_pos: float = 2000.0

    min_turnover: float = 5000.0
    min_equity: float = 50.0
    min_abs_rate: float = 0.10
    max_abs_rate: float = 5.0

    max_dd: float = 0.20
    dd_warn: float = 0.15
    fee_pct: float = 0.05
    slippage_pct: float = 0.0
    daily_loss_pct: float = 0.08

    funding_hours: Tuple[int, ...] = (0, 8, 16)
    scan_limit: int = 400
    request_timeout: float = 15.0
    alert_webhook: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        hours_raw = _env_str("BOT_FUNDING_HOURS", "0,8,16")
        hours = tuple(int(h) for h in hours_raw.split(",") if h.strip().isdigit())
        return cls(
            base_url=_env_str("DELTA_BASE_URL", cls.base_url),
            api_key=_env_str("DELTA_API_KEY", ""),
            api_secret=_env_str("DELTA_API_SECRET", ""),
            mode=_env_str("BOT_MODE", "paper").strip().lower(),
            live_approved=_env_bool("BOT_LIVE_APPROVED", False),
            start_equity=_env_float("BOT_START_EQUITY", 200.0),
            state_file=_env_str("BOT_STATE_FILE", "delta_bot_state.json"),
            trades_file=_env_str("BOT_TRADES_FILE", "delta_bot_paper_trades.csv"),
            volume_file=_env_str("BOT_VOLUME_FILE", "delta_volume.json"),
            trades_csv=_env_str("BOT_TRADES_CSV", "s5_final_trades.csv"),
            leverage_cap=_env_int("BOT_LEVERAGE_CAP", 20),
            allow_30x=_env_bool("BOT_ALLOW_30X", False),
            margin_frac=min(_env_float("BOT_MARGIN_FRAC", 0.70), 0.70),
            turnover_frac=_env_float("BOT_TURNOVER_FRAC", 0.005),
            min_pos=_env_float("BOT_MIN_POS", 5.0),
            max_pos=_env_float("BOT_MAX_POS", 2000.0),
            min_turnover=_env_float("BOT_MIN_TURNOVER", 5000.0),
            min_equity=_env_float("BOT_MIN_EQUITY", 50.0),
            min_abs_rate=_env_float("BOT_MIN_ABS_RATE", 0.10),
            max_abs_rate=_env_float("BOT_MAX_ABS_RATE", 5.0),
            max_dd=abs(_env_float("BOT_MAX_DD", 0.20)),
            dd_warn=abs(_env_float("BOT_DD_WARN", 0.15)),
            fee_pct=_env_float("BOT_FEE_PCT", 0.05),
            slippage_pct=_env_float("BOT_SLIPPAGE_PCT", 0.0),
            daily_loss_pct=abs(_env_float("BOT_DAILY_LOSS_PCT", 0.08)),
            funding_hours=hours or (0, 8, 16),
            scan_limit=_env_int("BOT_SCAN_LIMIT", 400),
            request_timeout=_env_float("BOT_REQUEST_TIMEOUT", 15.0),
            alert_webhook=_env_str("BOT_ALERT_WEBHOOK", ""),
        )


# --------------------------------------------------------------------------- #
# Pure logic helpers
# --------------------------------------------------------------------------- #


def chunk_of(rate_pct: float) -> Optional[str]:
    """Return chunk name for a funding rate (percent), or None below the floor."""
    a = abs(rate_pct)
    if a < CHUNK_BANDS[0][1]:
        return None
    for name, lo, hi in CHUNK_BANDS:
        if lo <= a < hi:
            return name
    return None


def side_for(rate_pct: float) -> str:
    """Funding negative -> LONG (receive), positive -> SHORT (receive)."""
    return "long" if rate_pct < 0 else "short"


@dataclass
class Level:
    name: str
    leverage: int
    coins_per_chunk: int
    max_concurrent: int


def detect_level(equity: float, cfg: Config) -> Level:
    name, lev, coins, conc = ACCOUNT_TIERS[-1][0], 20, 5, 30
    for tname, lo, hi, tcoins, tconc in ACCOUNT_TIERS:
        if lo <= equity < hi:
            name, coins, conc = tname, tcoins, tconc
            break
    lev = 20
    if cfg.allow_30x and equity >= 500.0:
        lev = 30
    lev = max(1, min(lev, cfg.leverage_cap))
    return Level(name=name, leverage=lev, coins_per_chunk=coins, max_concurrent=conc)


@dataclass
class Sizing:
    base_position: float
    margin_used: float
    notional: float
    leverage: int
    capped_by_equity: bool
    halved_for_dd: bool = False


def position_size(
    turnover_24h: float,
    equity: float,
    leverage: int,
    cfg: Config,
    dd_pct: float = 0.0,
) -> Sizing:
    """Volume-based sizing (bot_logic_spec.md section 6).

    base    = clamp(0.5% * turnover, MIN_POS, MAX_POS)   # USDT margin budget
    margin  = min(base, 0.70 * equity)                   # never > 70% equity
    notional= margin * leverage                          # liquidation guard
    """
    base = max(cfg.min_pos, min(cfg.max_pos, cfg.turnover_frac * max(turnover_24h, 0.0)))
    margin = min(base, cfg.margin_frac * equity)
    halved = False
    if dd_pct <= -cfg.dd_warn * 100.0 and dd_pct > -cfg.max_dd * 100.0:
        margin *= 0.5
        halved = True
    notional = margin * leverage
    return Sizing(
        base_position=round(base, 4),
        margin_used=round(margin, 4),
        notional=round(notional, 4),
        leverage=leverage,
        capped_by_equity=base > cfg.margin_frac * equity + 1e-9,
        halved_for_dd=halved,
    )


def trade_pnl_pct(rate_pct: float, entry: float, exit_: float, side: str,
                  fee_pct: float, slippage_pct: float = 0.0) -> Tuple[float, float, float, float]:
    """Return (funding_pct, price_pct, cost_pct, net_pct)."""
    funding_pct = abs(rate_pct)
    if side == "long":
        price_pct = (exit_ - entry) / entry * 100.0
    else:
        price_pct = (entry - exit_) / entry * 100.0
    cost_pct = fee_pct + slippage_pct
    return funding_pct, price_pct, cost_pct, funding_pct + price_pct - cost_pct


# --------------------------------------------------------------------------- #
# Time / funding window helpers
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def funding_times_for_date(day: datetime, hours: Tuple[int, ...]) -> List[datetime]:
    base = day.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return [base + timedelta(hours=h) for h in hours]


def next_funding_time(now: datetime, hours: Tuple[int, ...]) -> datetime:
    today = funding_times_for_date(now, hours)
    for t in today:
        if t > now:
            return t
    return funding_times_for_date(now + timedelta(days=1), hours)[0]


def nearest_funding_time(now: datetime, hours: Tuple[int, ...]) -> datetime:
    """Funding time closest to `now` (today or adjacent day)."""
    candidates = (
        funding_times_for_date(now - timedelta(days=1), hours)
        + funding_times_for_date(now, hours)
        + funding_times_for_date(now + timedelta(days=1), hours)
    )
    return min(candidates, key=lambda t: abs((t - now).total_seconds()))


# --------------------------------------------------------------------------- #
# Delta REST client (stdlib urllib + HMAC signing for private calls)
# --------------------------------------------------------------------------- #


class DeltaError(RuntimeError):
    pass


class DeltaClient:
    def __init__(self, cfg: Config):
        self.base = cfg.base_url.rstrip("/")
        self.key = cfg.api_key
        self.secret = cfg.api_secret
        self.timeout = cfg.request_timeout

    def _sign(self, method: str, path_query: str, body: str, ts: str) -> str:
        payload = method + ts + path_query + body
        return hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def request(self, method: str, path: str, params: Optional[dict] = None,
                body: Optional[dict] = None, signed: bool = False):
        query = urllib.parse.urlencode(params) if params else ""
        path_query = path + (("?" + query) if query else "")
        url = self.base + path_query
        raw_body = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"Accept": "application/json", "User-Agent": "delta-funding-bot/0.1"}
        if raw_body:
            headers["Content-Type"] = "application/json"
        if signed:
            if not self.key or not self.secret:
                raise DeltaError("private endpoint needs DELTA_API_KEY / DELTA_API_SECRET")
            ts = str(int(time.time()))
            headers.update({
                "api-key": self.key,
                "timestamp": ts,
                "signature": self._sign(method, path_query, raw_body, ts),
            })
        data = raw_body.encode() if raw_body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise DeltaError(f"HTTP {exc.code} {method} {path_query}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise DeltaError(f"network error {method} {path_query}: {exc.reason}") from exc
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DeltaError(f"bad JSON from {path_query}: {text[:200]}") from exc
        if isinstance(obj, dict) and obj.get("success") is False:
            raise DeltaError(f"API error {path_query}: {obj.get('error')}")
        return obj.get("result") if isinstance(obj, dict) else obj

    # public endpoints -----------------------------------------------------
    def get_ticker(self, symbol: str) -> dict:
        return self.request("GET", f"/v2/tickers/{urllib.parse.quote(symbol)}")

    def get_all_tickers(self) -> List[dict]:
        result = self.request("GET", "/v2/tickers")
        return result if isinstance(result, list) else []

    def get_candles(self, symbol: str, start: datetime, end: datetime,
                    resolution: str = "1m") -> List[dict]:
        params = {
            "resolution": resolution,
            "symbol": symbol,
            "start": int(start.timestamp()),
            "end": int(end.timestamp()),
        }
        result = self.request("GET", "/v2/history/candles", params=params)
        rows = result if isinstance(result, list) else []
        # API returns newest-first; normalise ascending by time.
        return sorted(rows, key=lambda c: int(c.get("time", 0)))


# --------------------------------------------------------------------------- #
# State / trade persistence
# --------------------------------------------------------------------------- #


@dataclass
class BotState:
    mode: str = "paper"
    start_equity: float = 200.0
    equity: float = 200.0
    peak_equity: float = 200.0
    max_dd_pct: float = 0.0
    realized_pnl: float = 0.0
    trade_count: int = 0
    daily_date: str = ""
    daily_start_equity: float = 200.0
    halted: bool = False
    halt_reason: str = ""
    last_update: str = ""
    traded: Dict[str, str] = field(default_factory=dict)  # "SYM|YYYY-MM-DD" -> trade id

    @classmethod
    def fresh(cls, cfg: Config) -> "BotState":
        return cls(mode=cfg.mode, start_equity=cfg.start_equity, equity=cfg.start_equity,
                   peak_equity=cfg.start_equity, daily_start_equity=cfg.start_equity,
                   last_update=iso(utcnow()))

    @classmethod
    def load(cls, path: str, cfg: Config) -> "BotState":
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            raw.setdefault("traded", {})
            return cls(**raw)
        except FileNotFoundError:
            return cls.fresh(cfg)
        except (json.JSONDecodeError, TypeError) as exc:
            LOG.warning("state file %s unreadable (%s); starting fresh", path, exc)
            return cls.fresh(cfg)

    def save(self, path: str) -> None:
        self.last_update = iso(utcnow())
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2, sort_keys=True)
        os.replace(tmp, path)

    def current_dd_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return (self.equity - self.peak_equity) / self.peak_equity * 100.0

    def roll_daily(self, today: str) -> None:
        if self.daily_date != today:
            self.daily_date = today
            self.daily_start_equity = self.equity


TRADE_FIELDS = [
    "ts_utc", "symbol", "chunk", "side", "funding_rate", "entry_time", "exit_time",
    "entry", "exit", "turnover_24h", "level", "leverage", "base_position",
    "margin_used", "notional", "funding_pnl_pct", "price_pnl_pct", "fee_pct",
    "net_pnl_pct", "net_pnl_usdt", "equity_before", "equity_after", "mode",
]


def append_trade(path: str, trade: dict) -> None:
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=TRADE_FIELDS)
        if not exists:
            w.writeheader()
        w.writerow({k: trade.get(k, "") for k in TRADE_FIELDS})


# --------------------------------------------------------------------------- #
# Risk gates + trade booking
# --------------------------------------------------------------------------- #


def pre_trade_gate(state: BotState, symbol: str, trade_date: str, cfg: Config) -> Tuple[bool, str]:
    if state.halted:
        return False, f"halted: {state.halt_reason}"
    if state.equity < cfg.min_equity:
        return False, f"equity {state.equity:.2f} < min {cfg.min_equity:.2f}"
    if state.current_dd_pct() <= -cfg.max_dd * 100.0:
        return False, f"max DD {state.current_dd_pct():.2f}% hit"
    state.roll_daily(trade_date)
    if state.equity <= state.daily_start_equity * (1.0 - cfg.daily_loss_pct):
        return False, f"daily loss stop on {trade_date}"
    if f"{symbol}|{trade_date}" in state.traded:
        return False, "already traded this coin today"
    return True, "ok"


def book_trade(
    state: BotState,
    cfg: Config,
    *,
    symbol: str,
    rate_pct: float,
    entry: float,
    exit_: float,
    turnover_24h: float,
    entry_time: datetime,
    exit_time: datetime,
    mode: str,
    date_override: Optional[str] = None,
) -> Optional[dict]:
    """Apply one funding-capture trade to paper state. Returns trade dict or None."""
    trade_date = date_override or entry_time.astimezone(timezone.utc).strftime("%Y-%m-%d")
    ok, reason = pre_trade_gate(state, symbol, trade_date, cfg)
    if not ok:
        LOG.debug("skip %s %s: %s", symbol, trade_date, reason)
        return None
    chunk = chunk_of(rate_pct)
    if chunk is None:
        return None
    side = side_for(rate_pct)
    level = detect_level(state.equity, cfg)
    size = position_size(turnover_24h, state.equity, level.leverage, cfg,
                         dd_pct=state.current_dd_pct())
    funding_pct, price_pct, cost_pct, net_pct = trade_pnl_pct(
        rate_pct, entry, exit_, side, cfg.fee_pct, cfg.slippage_pct)
    pnl = size.notional * net_pct / 100.0

    before = state.equity
    state.equity = round(state.equity + pnl, 8)
    state.realized_pnl = round(state.realized_pnl + pnl, 8)
    state.trade_count += 1
    state.peak_equity = max(state.peak_equity, state.equity)
    dd = state.current_dd_pct()
    state.max_dd_pct = min(state.max_dd_pct, dd)
    tid = f"{symbol}-{trade_date}-{state.trade_count}"
    state.traded[f"{symbol}|{trade_date}"] = tid

    if dd <= -cfg.max_dd * 100.0:
        state.halted = True
        state.halt_reason = f"max drawdown {dd:.2f}% <= -{cfg.max_dd*100:.0f}%"
        LOG.error("HALT: %s", state.halt_reason)

    trade = {
        "ts_utc": iso(utcnow()),
        "symbol": symbol,
        "chunk": chunk,
        "side": side,
        "funding_rate": round(rate_pct, 10),
        "entry_time": iso(entry_time),
        "exit_time": iso(exit_time),
        "entry": entry,
        "exit": exit_,
        "turnover_24h": round(turnover_24h, 4),
        "level": level.name,
        "leverage": size.leverage,
        "base_position": size.base_position,
        "margin_used": size.margin_used,
        "notional": size.notional,
        "funding_pnl_pct": round(funding_pct, 10),
        "price_pnl_pct": round(price_pct, 10),
        "fee_pct": round(cost_pct, 10),
        "net_pnl_pct": round(net_pct, 10),
        "net_pnl_usdt": round(pnl, 8),
        "equity_before": round(before, 8),
        "equity_after": round(state.equity, 8),
        "mode": mode,
    }
    return trade


# --------------------------------------------------------------------------- #
# Data loaders
# --------------------------------------------------------------------------- #


def load_volume(path: str) -> Dict[str, dict]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected object of symbol -> volume")
    return data


def load_replay_rows(path: str) -> List[dict]:
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def turnover_for(volume: Dict[str, dict], symbol: str) -> Optional[float]:
    rec = volume.get(symbol)
    if not rec:
        return None
    try:
        return float(rec.get("turnover", 0.0))
    except (TypeError, ValueError):
        return None


def is_tradable_ticker(t: dict, cfg: Config) -> bool:
    sym = str(t.get("symbol", ""))
    if not sym.endswith("USD") or "_" in sym or "-" in sym:
        return False
    if t.get("product_trading_status") != "operational":
        return False
    try:
        turnover = float(t.get("turnover_usd") or t.get("turnover") or 0.0)
        rate = float(t.get("funding_rate") or 0.0)
    except (TypeError, ValueError):
        return False
    if turnover < cfg.min_turnover:
        return False
    if not (cfg.min_abs_rate <= abs(rate) <= cfg.max_abs_rate):
        return False
    return True


def ranked_signals(tickers: List[dict], cfg: Config) -> List[dict]:
    out = []
    for t in tickers:
        if not is_tradable_ticker(t, cfg):
            continue
        rate = float(t["funding_rate"])
        out.append({
            "symbol": t["symbol"],
            "rate": rate,
            "chunk": chunk_of(rate),
            "side": side_for(rate),
            "turnover": float(t.get("turnover_usd") or t.get("turnover") or 0.0),
            "mark": float(t.get("mark_price") or t.get("close") or 0.0),
        })
    out.sort(key=lambda r: (abs(r["rate"]), r["turnover"]), reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def _print_trade_table(trades: List[dict]) -> None:
    cols = ["symbol", "chunk", "side", "funding_rate", "entry", "exit",
            "notional", "net_pnl_pct", "net_pnl_usdt", "equity_after"]
    rows = [{c: t[c] for c in cols} for t in trades]
    widths = {c: max(len(c), *(len(f"{r[c]}") for r in rows)) if rows else len(c) for c in cols}
    header = "  ".join(c.rjust(widths[c]) for c in cols)
    print(header)
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(f"{r[c]}".rjust(widths[c]) for c in cols))


def cmd_selftest(args: argparse.Namespace, cfg: Config) -> int:
    LOG.info("selftest: offline validation + 5-trade paper verify (no orders)")
    try:
        volume = load_volume(cfg.volume_file)
        rows = load_replay_rows(cfg.trades_csv)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOG.error("cannot load data: %s", exc)
        return 2
    if not rows:
        LOG.error("no replay rows in %s", cfg.trades_csv)
        return 2

    # ---- A. classification + pnl recompute validation over all rows ----
    class_bad = pnl_bad = 0
    for r in rows:
        rate = float(r["funding_rate"])
        if chunk_of(rate) != r["chunk"] or side_for(rate) != r["side"]:
            class_bad += 1
        entry, exit_ = float(r["entry"]), float(r["exit"])
        side = r["side"]
        f_pct, p_pct, _, _ = trade_pnl_pct(rate, entry, exit_, side, cfg.fee_pct)
        if abs(f_pct - float(r["funding_pnl"])) > 1e-6 or abs(p_pct - float(r["price_pnl"])) > 1e-6:
            pnl_bad += 1

    print("\n=== A. Data / logic validation (s5_final_trades.csv) ===")
    print(f"rows: {len(rows)}")
    print(f"chunk+side classification mismatches: {class_bad}")
    print(f"pnl recompute mismatches:            {pnl_bad}")
    if class_bad or pnl_bad:
        LOG.error("validation FAILED")
        return 1
    print("validation: PASS (100% chunk/side/pnl match)")

    # ---- B. reference reproduction (pure sizing, no risk gates) ----
    ref = BotState.fresh(cfg)
    ref.start_equity = cfg.start_equity
    ref.equity = cfg.start_equity
    ref.peak_equity = cfg.start_equity
    lev = detect_level(cfg.start_equity, cfg).leverage
    for r in rows:
        turnover = turnover_for(volume, r["symbol"])
        if turnover is None:
            continue
        rate = float(r["funding_rate"])
        entry, exit_ = float(r["entry"]), float(r["exit"])
        side = r["side"]
        size = position_size(turnover, ref.equity, lev, cfg)
        f_pct, p_pct, _, net_pct = trade_pnl_pct(rate, entry, exit_, side, cfg.fee_pct)
        ref.equity += size.notional * net_pct / 100.0
        ref.peak_equity = max(ref.peak_equity, ref.equity)
        ref.max_dd_pct = min(ref.max_dd_pct, (ref.equity - ref.peak_equity) / ref.peak_equity * 100.0)

    print("\n=== B. Reference setup reproduction (20x, 70% margin, compounding) ===")
    print(f"start equity : {cfg.start_equity:.2f}")
    print(f"final equity : {ref.equity:.2f}  (reference {REFERENCE_FINAL_EQUITY:.2f})")
    print(f"net pnl      : {ref.equity - cfg.start_equity:+.2f}")
    print(f"max DD       : {ref.max_dd_pct:.2f}%  (reference {REFERENCE_MAX_DD_PCT:.2f}%)")
    ok_final = abs(ref.equity - REFERENCE_FINAL_EQUITY) < 1.0
    ok_dd = abs(ref.max_dd_pct - REFERENCE_MAX_DD_PCT) < 0.1
    print(f"reproduction : {'PASS' if ok_final and ok_dd else 'MISMATCH'}")

    # ---- C. five paper trades through full risk engine ----
    state = BotState.fresh(cfg)
    trades: List[dict] = []
    for r in rows:
        if len(trades) >= 5:
            break
        turnover = turnover_for(volume, r["symbol"])
        if turnover is None:
            continue
        rate = float(r["funding_rate"])
        entry, exit_ = float(r["entry"]), float(r["exit"])
        d = datetime.strptime(r["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        t = book_trade(
            state, cfg,
            symbol=r["symbol"], rate_pct=rate, entry=entry, exit_=exit_,
            turnover_24h=turnover,
            entry_time=d - timedelta(minutes=1),
            exit_time=d + timedelta(minutes=1),
            mode="paper-replay", date_override=r["date"],
        )
        if t:
            trades.append(t)

    print("\n=== C. Five paper trades (full risk engine) ===")
    _print_trade_table(trades)
    print(f"\nequity after 5 trades: {state.equity:.2f} "
          f"(pnl {state.equity - cfg.start_equity:+.2f}, DD {state.current_dd_pct():.2f}%)")
    print(f"chunks traded: {[t['chunk'] for t in trades]}")

    if args.persist:
        state.save(cfg.state_file)
        for t in trades:
            append_trade(cfg.trades_file, t)
        print(f"\npersisted: {cfg.state_file}, {cfg.trades_file}")
    else:
        print("\n(run with --persist to write state + trade log)")

    if len(trades) != 5:
        LOG.error("expected 5 paper trades, booked %d", len(trades))
        return 1
    if not (ok_final and ok_dd):
        return 1
    print("\nselftest: PASS")
    return 0


def cmd_scan(args: argparse.Namespace, cfg: Config) -> int:
    client = DeltaClient(cfg)
    try:
        tickers = client.get_all_tickers()
    except DeltaError as exc:
        LOG.error("scan failed: %s", exc)
        return 2
    signals = ranked_signals(tickers, cfg)
    state = BotState.load(cfg.state_file, cfg)
    level = detect_level(state.equity, cfg)
    limit = args.limit or 20
    print(f"mode={cfg.mode} equity={state.equity:.2f} level={level.name} "
          f"leverage={level.leverage}x dd={state.current_dd_pct():.2f}%")
    print(f"eligible signals: {len(signals)} (top {min(limit, len(signals))})")
    print(f"{'symbol':<20}{'chunk':<14}{'side':<7}{'rate%':>10}{'turnover':>15}"
          f"{'base':>10}{'notional':>12}")
    for s in signals[:limit]:
        size = position_size(s["turnover"], state.equity, level.leverage, cfg,
                             dd_pct=state.current_dd_pct())
        print(f"{s['symbol']:<20}{s['chunk']:<14}{s['side']:<7}{s['rate']:>10.5f}"
              f"{s['turnover']:>15.0f}{size.base_position:>10.2f}{size.notional:>12.2f}")
    return 0


def _open_at(candles: List[dict], minute: datetime) -> Optional[float]:
    target = int(minute.replace(second=0, microsecond=0).timestamp())
    for c in candles:
        if int(c.get("time", -1)) == target:
            try:
                return float(c["open"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def cmd_paper_window(args: argparse.Namespace, cfg: Config) -> int:
    client = DeltaClient(cfg)
    now = parse_iso(args.at) if args.at else utcnow()
    if args.at:
        funding_t = parse_iso(args.at).replace(second=0, microsecond=0)
    else:
        funding_t = nearest_funding_time(now, cfg.funding_hours)
    entry_min = funding_t - timedelta(minutes=1)
    exit_min = funding_t + timedelta(minutes=1)
    age = (utcnow() - funding_t).total_seconds()
    if age > 300:
        LOG.warning("window is %.0fs old; ticker funding_rate is CURRENT, not historical. "
                    "Use --selftest for historical replay.", age)

    try:
        tickers = client.get_all_tickers()
    except DeltaError as exc:
        LOG.error("paper-window failed: %s", exc)
        return 2
    signals = ranked_signals(tickers, cfg)
    if args.symbols:
        wanted = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
        signals = [s for s in signals if s["symbol"] in wanted]

    state = BotState.load(cfg.state_file, cfg)
    limit = args.limit or 20
    booked: List[dict] = []
    for s in signals:
        if len(booked) >= limit:
            break
        try:
            candles = client.get_candles(s["symbol"], entry_min - timedelta(minutes=2),
                                         exit_min + timedelta(minutes=2))
        except DeltaError as exc:
            LOG.warning("%s candles failed: %s", s["symbol"], exc)
            continue
        entry = _open_at(candles, entry_min)
        exit_ = _open_at(candles, exit_min)
        if not entry or not exit_:
            LOG.debug("%s missing candle at entry/exit, skip", s["symbol"])
            continue
        t = book_trade(
            state, cfg,
            symbol=s["symbol"], rate_pct=s["rate"], entry=entry, exit_=exit_,
            turnover_24h=s["turnover"],
            entry_time=entry_min, exit_time=exit_min, mode="paper-live",
        )
        if t:
            booked.append(t)

    print(f"funding T={iso(funding_t)} entry={iso(entry_min)} exit={iso(exit_min)}")
    if booked:
        _print_trade_table(booked)
    else:
        print("no trades booked (gates/skips or missing candles)")
    state.save(cfg.state_file)
    for t in booked:
        append_trade(cfg.trades_file, t)
    print(f"equity={state.equity:.2f} dd={state.current_dd_pct():.2f}% "
          f"trades={state.trade_count} halted={state.halted}")
    return 0


def cmd_status(args: argparse.Namespace, cfg: Config) -> int:
    state = BotState.load(cfg.state_file, cfg)
    level = detect_level(state.equity, cfg)
    print(f"mode         : {state.mode}")
    print(f"equity       : {state.equity:.2f} (start {state.start_equity:.2f})")
    print(f"peak equity  : {state.peak_equity:.2f}")
    print(f"drawdown     : {state.current_dd_pct():.2f}% (max {state.max_dd_pct:.2f}%)")
    print(f"realized pnl : {state.realized_pnl:+.2f}")
    print(f"trades       : {state.trade_count}")
    print(f"level        : {level.name} / {level.leverage}x / {level.coins_per_chunk} coins/chunk")
    print(f"halted       : {state.halted} {state.halt_reason}")
    print(f"traded keys  : {len(state.traded)}")
    print(f"last update  : {state.last_update}")
    return 0


def cmd_reset(args: argparse.Namespace, cfg: Config) -> int:
    state = BotState.fresh(cfg)
    state.save(cfg.state_file)
    print(f"reset paper state -> {cfg.state_file} equity={state.equity:.2f}")
    return 0


def cmd_live(args: argparse.Namespace, cfg: Config) -> int:
    problems = []
    if cfg.mode != "live":
        problems.append("BOT_MODE is not 'live'")
    if not cfg.live_approved:
        problems.append("BOT_LIVE_APPROVED != 1 (user approval missing)")
    if not cfg.api_key or not cfg.api_secret:
        problems.append("DELTA_API_KEY / DELTA_API_SECRET missing")
    if not args.i_understand_live_risk:
        problems.append("--i-understand-live-risk not passed")
    if problems:
        LOG.error("LIVE refused: %s", "; ".join(problems))
        LOG.error("Staying paper. Paper-trade first, live only after approval.")
        return 3
    LOG.error("LIVE gates passed, but live order placement is NOT implemented in this "
              "skeleton. Refusing to send orders. Review paper results and implement "
              "the reviewed live execution layer first.")
    return 4


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Delta funding-capture grid bot (paper-first)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--selftest", action="store_true", help="offline validation + 5 paper trades")
    g.add_argument("--scan", action="store_true", help="read-only live ticker scan + sizing preview")
    g.add_argument("--paper-window", action="store_true", help="paper-trade one funding window")
    g.add_argument("--status", action="store_true", help="show persisted state")
    g.add_argument("--reset", action="store_true", help="reset paper state")
    g.add_argument("--live", action="store_true", help="gated live mode (stub, refused)")
    p.add_argument("--at", help="funding time ISO8601 UTC for --paper-window")
    p.add_argument("--symbols", help="comma list to restrict --paper-window")
    p.add_argument("--limit", type=int, default=0, help="max rows/signals")
    p.add_argument("--persist", action="store_true", help="write state + trade log in --selftest")
    p.add_argument("--i-understand-live-risk", action="store_true", help="required for --live")
    p.add_argument("--log-level", default="INFO", help="DEBUG/INFO/WARNING/ERROR")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cfg = Config.from_env()
    if args.selftest:
        return cmd_selftest(args, cfg)
    if args.scan:
        return cmd_scan(args, cfg)
    if args.paper_window:
        return cmd_paper_window(args, cfg)
    if args.status:
        return cmd_status(args, cfg)
    if args.reset:
        return cmd_reset(args, cfg)
    if args.live:
        return cmd_live(args, cfg)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
