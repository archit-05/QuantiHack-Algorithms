"""
=============================================================================
Algo 1: Time-Series Momentum (TSMOM)
Team:   Coeus
Event:  QuantiHack 2026 — QuantiHack WORLD v0.1.0 simulated exchange
=============================================================================

Strategy grounded in Moskowitz, Ooi & Pedersen (2012) — "Time Series
Momentum" (Journal of Financial Economics).  Assets with positive recent
excess returns tend to continue in the same direction over the short-to-medium
term.  The implementation adds:

  • EMA(5/20) crossover momentum signal
  • CUSUM changepoint detection (CPD) to exit before momentum crashes
    (concept drawn from kieranjwood/trading-momentum-transformer)
  • ATR-based volatility-scaled position sizing
    (EMA/ATR implementations styled after chrisconlan/algorithmic-trading-with-python)
  • Hard risk limits: per-instrument cap, portfolio cap, cash buffer, stop-loss

Usage
-----
The platform calls ``on_tick()`` on every price update.  Drop this entire
file into the browser IDE and hit Run — no pip installs required.

Platform API stubs (see SECTION 1 below) must be replaced with the real
calls once the competition API docs are available.  Every stub is clearly
labelled ``# STUB``.
=============================================================================
"""

# =============================================================================
# SECTION 0 — IMPORTS
# =============================================================================
import collections
import math
import time

import numpy as np          # array maths
import pandas as pd         # convenience — EMA via ewm()

# =============================================================================
# SECTION 1 — PLATFORM API STUBS
# =============================================================================
# These thin wrappers isolate every external call.  Replace the bodies with
# the real competition API once the docs are confirmed.  All callers in this
# file use these functions, so swapping them out is a one-place change.

def _api_get_price(symbol: str) -> float:
    """
    STUB — Return the current mid-price for *symbol*.
    Replace with the real platform call, e.g.:
        return get_price(symbol)
    """
    raise NotImplementedError(f"_api_get_price({symbol!r}) — replace with real API")


def _api_get_portfolio() -> dict:
    """
    STUB — Return a dict with at least:
        { "cash": <float>, "total_value": <float> }
    Replace with:
        return get_portfolio()
    """
    raise NotImplementedError("_api_get_portfolio() — replace with real API")


def _api_get_positions() -> dict:
    """
    STUB — Return a dict keyed by symbol, value is signed qty (positive=long).
    Replace with:
        return get_positions()
    """
    raise NotImplementedError("_api_get_positions() — replace with real API")


def _api_place_order(symbol: str, side: str, quantity: int, order_type: str = "market") -> bool:
    """
    STUB — Place an order and return True on success.
    side: "buy" or "sell"
    order_type: "market" (default) or "limit"
    Replace with:
        return place_order(symbol, side, quantity, order_type)
    """
    raise NotImplementedError(f"_api_place_order({symbol!r}, {side!r}, {quantity}, {order_type!r}) — replace with real API")


def _api_cancel_all_orders(symbol: str = None) -> bool:
    """
    STUB — Cancel all open orders, optionally filtered by *symbol*.
    Replace with:
        return cancel_all_orders()   # or cancel_all_orders(symbol)
    """
    raise NotImplementedError("_api_cancel_all_orders() — replace with real API")


# =============================================================================
# SECTION 2 — CONSTANTS  (all tunable parameters live here)
# =============================================================================

# --- EMA crossover periods ---
FAST_EMA_PERIOD  = 5    # short-term trend capture
SLOW_EMA_PERIOD  = 20   # medium-term trend baseline

# --- ATR volatility window ---
ATR_PERIOD       = 14   # standard 14-period ATR

# --- Minimum price history before trading ---
WARMUP_TICKS     = 30   # wait until we have 30 bars before placing any order

# --- Price history depth ---
HISTORY_MAXLEN   = 100  # rolling deque length per instrument

# --- CUSUM changepoint detection ---
CUSUM_THRESHOLD  = 4.0  # lower = more sensitive to regime changes
CUSUM_DRIFT      = 0.5  # allowance for natural drift
CUSUM_LOOKBACK   = 30   # number of recent prices fed into the detector

# --- Position sizing ---
RISK_PCT         = 0.01  # risk 1 % of portfolio per trade (ATR-scaled)
MAX_SINGLE_ALLOC = 0.10  # hard cap: max 10 % of portfolio in one instrument
BTC_MAX_ALLOC    = 0.02  # BTC-specific hard cap: 2 % (massive spread)

# --- Portfolio-level risk limits ---
MAX_TOTAL_ALLOC  = 0.50  # max 50 % of portfolio in all positions combined
MIN_CASH_BUFFER  = 0.20  # always keep at least 20 % as cash

# --- Stop-loss ---
STOP_LOSS_ATR_MULT = 2.0  # close position if price moves 2× ATR against entry

# --- Cooldown after CPD fires ---
CPD_COOLDOWN_TICKS = 10  # wait 10 ticks before re-entering after a changepoint

# --- Portfolio construction ---
MAX_LONG_POSITIONS  = 3  # hold at most 3 long positions simultaneously
MAX_SHORT_POSITIONS = 2  # hold at most 2 short positions simultaneously

# --- Instrument universe (all 25) ---
EQUITIES = [
    "SYN-AAPL", "SYN-GOOG", "SYN-TSLA", "SYN-AMZN",
    "SYN-MSFT", "SYN-NVDA", "SYN-META", "SYN-JPM",
]
FX_PAIRS = [
    "FX-EURUSD", "FX-GBPUSD", "FX-USDJPY", "FX-AUDUSD", "FX-USDCNY",
]
COMMODITIES = [
    "CMD-GOLD", "CMD-OIL", "CMD-BTC", "CMD-DMND", "CMD-PLAT",
    "CMD-COPPER", "CMD-NATGAS", "CMD-WHEAT", "CMD-COFFEE", "CMD-LITHIUM",
]
INDICES = [
    "IDX-SP500", "IDX-NIKKEI", "IDX-FTSE",
]
ALL_INSTRUMENTS = EQUITIES + FX_PAIRS + COMMODITIES + INDICES  # 25 total

# BTC symbol — used to apply the special hard cap
BTC_SYMBOL = "CMD-BTC"

# =============================================================================
# SECTION 3 — GLOBAL STATE
# =============================================================================
# Everything the algo needs to persist across ticks lives here.

class AlgoState:
    """Central state store — one instance shared across all ticks."""

    def __init__(self):
        # --- Price history ---
        # Keyed by symbol → deque of (price, high_approx, low_approx) tuples.
        # Because the platform delivers a single mid-price, we approximate
        # high/low from the rolling max/min over the last tick window.
        self.price_history: dict[str, collections.deque] = {
            sym: collections.deque(maxlen=HISTORY_MAXLEN)
            for sym in ALL_INSTRUMENTS
        }

        # --- Open positions ---
        # Keyed by symbol → dict with keys:
        #   side        : "long" or "short"
        #   qty         : int (always positive)
        #   entry_price : float
        #   entry_atr   : float
        #   entry_tick  : int
        self.positions: dict[str, dict] = {}

        # --- CPD cooldowns ---
        # Keyed by symbol → tick number when the cooldown *expires*
        self.cooldowns: dict[str, int] = {}

        # --- Tick counter ---
        self.tick: int = 0

        # --- P&L snapshot (for logging) ---
        self.last_portfolio_value: float = 0.0


# Single global instance — the platform calls on_tick() repeatedly
STATE = AlgoState()

# =============================================================================
# SECTION 4 — HELPER FUNCTIONS
# =============================================================================

def calculate_ema(prices: list[float], period: int) -> float:
    """
    Exponential Moving Average of *prices* for the given *period*.
    Uses pandas ewm() which matches the standard formula:
        EMA_t = alpha * price_t + (1 - alpha) * EMA_{t-1}
        alpha = 2 / (period + 1)
    Returns the most recent EMA value.
    Returns NaN if there are fewer prices than the period.
    """
    if len(prices) < period:
        return float("nan")
    series = pd.Series(prices)
    ema_values = series.ewm(span=period, adjust=False).mean()
    return float(ema_values.iloc[-1])


def calculate_atr(prices: list[tuple], period: int = ATR_PERIOD) -> float:
    """
    Average True Range — measures recent volatility.

    *prices* is a list of (close, high, low) tuples in chronological order.
    ATR = mean of True Range over last *period* bars.
    True Range = max(high-low, |high-prev_close|, |low-prev_close|)

    Returns 0.0 if not enough data.
    """
    if len(prices) < period + 1:
        return 0.0

    closes = [p[0] for p in prices]
    highs  = [p[1] for p in prices]
    lows   = [p[2] for p in prices]

    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        true_ranges.append(tr)

    # Return the average of the last *period* true ranges
    recent_trs = true_ranges[-period:]
    return float(np.mean(recent_trs)) if recent_trs else 0.0


def cusum_changepoint_detected(prices: list[float],
                                threshold: float = CUSUM_THRESHOLD,
                                drift: float = CUSUM_DRIFT) -> bool:
    """
    CUSUM (Cumulative Sum) changepoint detector.
    Concept adapted from kieranjwood/trading-momentum-transformer (Bayesian CPD
    simplified to CUSUM for speed and simplicity).

    Standardises prices to z-scores around the window mean, then accumulates
    positive and negative deviations.  Returns True when either accumulator
    exceeds *threshold*, signalling a structural break in the price process.

    Parameters
    ----------
    prices    : recent price observations (last CUSUM_LOOKBACK ticks)
    threshold : sensitivity — lower catches breaks earlier but raises false-positives
    drift     : allowance subtracted each step; prevents false alerts on gradual trends

    Returns
    -------
    True if a structural break is detected, False otherwise.
    """
    if len(prices) < 2:
        return False

    mean = float(np.mean(prices))
    std  = float(np.std(prices)) + 1e-8  # guard against zero std

    s_pos = 0.0
    s_neg = 0.0
    for p in prices:
        z      = (p - mean) / std
        s_pos  = max(0.0, s_pos + z - drift)
        s_neg  = max(0.0, s_neg - z - drift)
        if s_pos > threshold or s_neg > threshold:
            return True
    return False


def calculate_position_size(portfolio_value: float,
                             atr: float,
                             price: float,
                             symbol: str,
                             risk_pct: float = RISK_PCT) -> int:
    """
    Volatility-scaled (ATR-based) position sizer.
    Risks *risk_pct* of *portfolio_value* per trade, divided by ATR so that
    a volatile instrument gets a proportionally smaller position.

    Hard caps applied:
      • MAX_SINGLE_ALLOC  — 10 % of portfolio per instrument (all symbols)
      • BTC_MAX_ALLOC     — 2 % of portfolio for CMD-BTC specifically

    Returns the integer number of units to trade (0 if inputs are invalid).
    """
    if atr <= 0 or price <= 0 or portfolio_value <= 0:
        return 0

    dollar_risk      = portfolio_value * risk_pct
    units            = dollar_risk / atr              # inverse-volatility sizing
    position_value   = units * price

    # Apply per-instrument cap
    alloc_cap = BTC_MAX_ALLOC if symbol == BTC_SYMBOL else MAX_SINGLE_ALLOC
    max_position_value = portfolio_value * alloc_cap
    if position_value > max_position_value:
        units = max_position_value / price

    return max(0, int(units))

# =============================================================================
# SECTION 5 — RISK FUNCTIONS
# =============================================================================

def check_stop_loss(symbol: str, current_price: float) -> bool:
    """
    Returns True if the current price has breached the 2× ATR stop-loss for
    an open position in *symbol*.

    Stop rule:
      Long  → stop triggered if price < entry_price - STOP_LOSS_ATR_MULT * entry_atr
      Short → stop triggered if price > entry_price + STOP_LOSS_ATR_MULT * entry_atr
    """
    pos = STATE.positions.get(symbol)
    if pos is None:
        return False

    stop_distance = STOP_LOSS_ATR_MULT * pos["entry_atr"]
    if pos["side"] == "long":
        return current_price < pos["entry_price"] - stop_distance
    else:  # short
        return current_price > pos["entry_price"] + stop_distance


def get_total_exposure(portfolio_value: float, current_prices: dict[str, float]) -> float:
    """
    Returns the fraction of *portfolio_value* currently tied up across all
    open positions.  Used to enforce the MAX_TOTAL_ALLOC constraint.
    """
    if portfolio_value <= 0:
        return 0.0
    total_exposure = 0.0
    for sym, pos in STATE.positions.items():
        price = current_prices.get(sym, 0.0)
        total_exposure += pos["qty"] * price
    return total_exposure / portfolio_value


def cash_buffer_ok(portfolio_info: dict, current_prices: dict[str, float]) -> bool:
    """
    Returns True only if the remaining cash is at least MIN_CASH_BUFFER of
    total portfolio value.  Prevents over-investing.
    """
    portfolio_value = portfolio_info.get("total_value", 0.0)
    cash            = portfolio_info.get("cash", 0.0)
    if portfolio_value <= 0:
        return False
    return (cash / portfolio_value) >= MIN_CASH_BUFFER


def close_position(symbol: str, current_price: float, reason: str = "") -> bool:
    """
    Market-close an open position in *symbol*.
    Sends the opposite-side market order and removes the position from state.
    Returns True on success, False if no position exists or the API call fails.
    """
    pos = STATE.positions.get(symbol)
    if pos is None:
        return False

    side = "sell" if pos["side"] == "long" else "buy"
    qty  = pos["qty"]

    try:
        _api_cancel_all_orders(symbol)          # cancel any pending limit orders first
        success = _api_place_order(symbol, side, qty, "market")
        if success:
            pnl = _calc_position_pnl(pos, current_price)
            print(f"[CLOSE] {symbol} | side={pos['side']} qty={qty} "
                  f"entry={pos['entry_price']:.4f} exit={current_price:.4f} "
                  f"PnL≈{pnl:+.2f} | reason: {reason}")
            del STATE.positions[symbol]
            return True
        else:
            print(f"[WARN] close_position({symbol}) — _api_place_order returned False")
            return False
    except Exception as exc:
        print(f"[ERROR] close_position({symbol}): {exc}")
        return False


def _calc_position_pnl(pos: dict, current_price: float) -> float:
    """Internal helper — estimates unrealised P&L for logging purposes."""
    delta = current_price - pos["entry_price"]
    if pos["side"] == "short":
        delta = -delta
    return delta * pos["qty"]

# =============================================================================
# SECTION 6 — SIGNAL FUNCTIONS
# =============================================================================

def compute_momentum_signal(symbol: str) -> float:
    """
    Computes the EMA crossover momentum signal for *symbol*.

    Signal = EMA(FAST_EMA_PERIOD) − EMA(SLOW_EMA_PERIOD)
      Positive → bullish momentum (long bias)
      Negative → bearish momentum  (short bias)
      Magnitude → conviction strength — used for ranking

    Returns NaN if there is insufficient price history.
    """
    history = list(STATE.price_history[symbol])
    if len(history) < SLOW_EMA_PERIOD:
        return float("nan")

    closes = [p[0] for p in history]   # index 0 is the close price
    fast   = calculate_ema(closes, FAST_EMA_PERIOD)
    slow   = calculate_ema(closes, SLOW_EMA_PERIOD)

    if math.isnan(fast) or math.isnan(slow):
        return float("nan")
    return fast - slow


def rank_instruments(current_prices: dict[str, float]) -> tuple[list[str], list[str]]:
    """
    Scores all 25 instruments by momentum signal magnitude and separates them
    into ranked LONG and SHORT candidate lists.

    Filters applied before ranking:
      • Warm-up:  must have at least WARMUP_TICKS of price history
      • CPD:      changepoint detected → skip (do not enter new trade)
      • Cooldown: instrument in cooldown period → skip

    Returns (top_longs, top_shorts) — truncated to MAX_LONG_POSITIONS and
    MAX_SHORT_POSITIONS respectively.
    """
    long_candidates = []   # list of (symbol, signal)
    short_candidates = []

    for sym in ALL_INSTRUMENTS:
        history = STATE.price_history[sym]

        # --- Warm-up gate ---
        if len(history) < WARMUP_TICKS:
            continue

        # --- Cooldown gate ---
        if STATE.cooldowns.get(sym, 0) > STATE.tick:
            continue

        signal = compute_momentum_signal(sym)
        if math.isnan(signal):
            continue

        # --- CPD gate — run on the last CUSUM_LOOKBACK closes ---
        closes = [p[0] for p in list(history)[-CUSUM_LOOKBACK:]]
        if cusum_changepoint_detected(closes):
            # Fire cooldown and log the event
            STATE.cooldowns[sym] = STATE.tick + CPD_COOLDOWN_TICKS
            print(f"[CPD] Changepoint detected on {sym} at tick {STATE.tick} "
                  f"— cooldown until tick {STATE.cooldowns[sym]}")
            # If we hold a position, close it immediately
            if sym in STATE.positions:
                price = current_prices.get(sym)
                if price:
                    close_position(sym, price, reason="CPD regime change")
            continue

        if signal > 0:
            long_candidates.append((sym, signal))
        elif signal < 0:
            short_candidates.append((sym, abs(signal)))

    # Sort by absolute signal magnitude descending (highest conviction first)
    long_candidates.sort(key=lambda x: x[1], reverse=True)
    short_candidates.sort(key=lambda x: x[1], reverse=True)

    top_longs  = [sym for sym, _ in long_candidates[:MAX_LONG_POSITIONS]]
    top_shorts = [sym for sym, _ in short_candidates[:MAX_SHORT_POSITIONS]]

    return top_longs, top_shorts

# =============================================================================
# SECTION 7 — EXECUTION FUNCTION
# =============================================================================

def on_tick():
    """
    Main entry point — called by the platform on every price tick.

    Execution flow
    --------------
    1. Increment tick counter and fetch portfolio info
    2. Update price history for all 25 instruments
    3. Run stop-loss checks on all open positions → close any breached
    4. Rank instruments by momentum signal (with CPD/cooldown gates)
    5. Close positions where the signal has reversed
    6. Check global exposure and cash buffer; trim if needed
    7. Open new positions (up to MAX_LONG + MAX_SHORT simultaneously)
    8. Log a summary of the current state
    """
    STATE.tick += 1
    tick = STATE.tick

    # -------------------------------------------------------------------------
    # Step 1 — Fetch portfolio info (defensive)
    # -------------------------------------------------------------------------
    try:
        portfolio_info = _api_get_portfolio()
        portfolio_value = portfolio_info.get("total_value", 0.0)
        cash            = portfolio_info.get("cash", 0.0)
    except Exception as exc:
        print(f"[ERROR] tick={tick} — _api_get_portfolio failed: {exc}")
        return  # cannot proceed without portfolio value

    # -------------------------------------------------------------------------
    # Step 2 — Update price history for all 25 instruments
    # -------------------------------------------------------------------------
    current_prices: dict[str, float] = {}

    for sym in ALL_INSTRUMENTS:
        try:
            price = _api_get_price(sym)
            if price is None or price <= 0:
                continue  # skip bad price feeds gracefully
            current_prices[sym] = price

            # Approximate high/low from the last recorded price (single-feed platform).
            # When the API provides OHLC data, replace these with the real values.
            history = STATE.price_history[sym]
            if history:
                prev_close = history[-1][0]
                approx_high = max(price, prev_close)
                approx_low  = min(price, prev_close)
            else:
                approx_high = price
                approx_low  = price

            history.append((price, approx_high, approx_low))

        except Exception as exc:
            print(f"[WARN] tick={tick} — price fetch failed for {sym}: {exc}")
            # Continue with other instruments — never crash the algo

    # Need prices to continue
    if not current_prices:
        print(f"[WARN] tick={tick} — no valid prices received; skipping tick")
        return

    # -------------------------------------------------------------------------
    # Step 3 — Stop-loss enforcement on all open positions
    # -------------------------------------------------------------------------
    # Iterate over a copy because close_position modifies STATE.positions
    for sym in list(STATE.positions.keys()):
        price = current_prices.get(sym)
        if price is None:
            continue  # price unavailable this tick — do not close yet

        if check_stop_loss(sym, price):
            close_position(sym, price, reason=f"stop-loss at {price:.4f}")

    # -------------------------------------------------------------------------
    # Step 4 — Rank instruments by momentum signal
    # -------------------------------------------------------------------------
    top_longs, top_shorts = rank_instruments(current_prices)

    # -------------------------------------------------------------------------
    # Step 5 — Close positions where the signal has reversed
    # -------------------------------------------------------------------------
    for sym, pos in list(STATE.positions.items()):
        price = current_prices.get(sym)
        if price is None:
            continue

        signal = compute_momentum_signal(sym)
        if math.isnan(signal):
            continue

        # Long position, but signal turned negative → exit
        if pos["side"] == "long" and signal < 0:
            close_position(sym, price, reason="signal reversal (long→negative)")

        # Short position, but signal turned positive → exit
        elif pos["side"] == "short" and signal > 0:
            close_position(sym, price, reason="signal reversal (short→positive)")

    # -------------------------------------------------------------------------
    # Step 6 — Portfolio-level exposure check
    # -------------------------------------------------------------------------
    total_exposure = get_total_exposure(portfolio_value, current_prices)
    if total_exposure > MAX_TOTAL_ALLOC:
        # Trim the weakest (smallest |signal|) positions until back under limit
        _trim_weakest_positions(current_prices, portfolio_value, total_exposure)

    # Recompute after trimming
    total_exposure = get_total_exposure(portfolio_value, current_prices)

    # -------------------------------------------------------------------------
    # Step 7 — Open new positions (if exposure and cash allow)
    # -------------------------------------------------------------------------
    for sym in top_longs:
        if sym in STATE.positions:
            continue  # already holding this instrument
        if total_exposure >= MAX_TOTAL_ALLOC:
            break
        if not cash_buffer_ok(portfolio_info, current_prices):
            break
        _open_position(sym, "long", current_prices, portfolio_value)
        # Refresh exposure estimate (rough) for subsequent loop iterations
        total_exposure = get_total_exposure(portfolio_value, current_prices)

    for sym in top_shorts:
        if sym in STATE.positions:
            continue
        if total_exposure >= MAX_TOTAL_ALLOC:
            break
        if not cash_buffer_ok(portfolio_info, current_prices):
            break
        _open_position(sym, "short", current_prices, portfolio_value)
        total_exposure = get_total_exposure(portfolio_value, current_prices)

    # -------------------------------------------------------------------------
    # Step 8 — Logging
    # -------------------------------------------------------------------------
    _log_tick_summary(tick, portfolio_value, cash, current_prices)


# =============================================================================
# SECTION 8 — INTERNAL EXECUTION HELPERS
# =============================================================================

def _open_position(symbol: str, side: str,
                   current_prices: dict[str, float],
                   portfolio_value: float) -> bool:
    """
    Sizes and places a new market order for *symbol* on the given *side*.
    Computes ATR-based size, enforces hard caps, then sends the order.
    Registers the position in STATE.positions on success.
    Returns True on success, False otherwise.
    """
    price = current_prices.get(symbol)
    if price is None or price <= 0:
        return False

    history = list(STATE.price_history[symbol])
    if len(history) < ATR_PERIOD + 1:
        return False  # not enough bars for ATR

    atr = calculate_atr(history, ATR_PERIOD)
    if atr <= 0:
        return False  # cannot size without a volatility estimate

    qty = calculate_position_size(portfolio_value, atr, price, symbol)
    if qty <= 0:
        return False

    order_side = "buy" if side == "long" else "sell"
    try:
        success = _api_place_order(symbol, order_side, qty, "market")
        if success:
            STATE.positions[symbol] = {
                "side":        side,
                "qty":         qty,
                "entry_price": price,
                "entry_atr":   atr,
                "entry_tick":  STATE.tick,
            }
            print(f"[OPEN] {symbol} | side={side} qty={qty} "
                  f"price={price:.4f} atr={atr:.4f}")
            return True
        else:
            print(f"[WARN] _open_position({symbol}, {side}) — order rejected by platform")
            return False
    except Exception as exc:
        print(f"[ERROR] _open_position({symbol}, {side}): {exc}")
        return False


def _trim_weakest_positions(current_prices: dict[str, float],
                             portfolio_value: float,
                             total_exposure: float) -> None:
    """
    Closes positions with the weakest (lowest magnitude) momentum signal until
    total portfolio exposure drops back below MAX_TOTAL_ALLOC.
    This enforces the 50 % gross exposure cap.
    """
    # Build (symbol, |signal|) list for all open positions
    position_signals = []
    for sym in list(STATE.positions.keys()):
        sig = compute_momentum_signal(sym)
        position_signals.append((sym, abs(sig) if not math.isnan(sig) else 0.0))

    # Sort weakest first (smallest |signal|)
    position_signals.sort(key=lambda x: x[1])

    for sym, _ in position_signals:
        if total_exposure <= MAX_TOTAL_ALLOC:
            break
        price = current_prices.get(sym)
        if price:
            close_position(sym, price, reason="portfolio exposure trim")
            total_exposure = get_total_exposure(portfolio_value, current_prices)


def _log_tick_summary(tick: int, portfolio_value: float,
                      cash: float, current_prices: dict[str, float]) -> None:
    """
    Prints a concise one-line summary every tick plus a detailed position table
    every 10 ticks.  Keeps the console readable without flooding it.
    """
    n_pos          = len(STATE.positions)
    exposure_pct   = get_total_exposure(portfolio_value, current_prices) * 100
    cash_pct       = (cash / portfolio_value * 100) if portfolio_value > 0 else 0.0
    pnl_delta      = portfolio_value - STATE.last_portfolio_value
    STATE.last_portfolio_value = portfolio_value

    print(f"[TICK {tick:>5}] PV={portfolio_value:,.2f} | Cash={cash_pct:.1f}% "
          f"| Exposure={exposure_pct:.1f}% | Positions={n_pos} "
          f"| ΔPnL={pnl_delta:+,.2f}")

    # Detailed position table every 10 ticks
    if tick % 10 == 0 and STATE.positions:
        print("  ── Open Positions ─────────────────────────────────────────────")
        for sym, pos in STATE.positions.items():
            price = current_prices.get(sym, 0.0)
            pnl   = _calc_position_pnl(pos, price)
            signal = compute_momentum_signal(sym)
            print(f"  {sym:<15} {pos['side']:<5} qty={pos['qty']:>6} "
                  f"entry={pos['entry_price']:.4f} now={price:.4f} "
                  f"PnL≈{pnl:+.2f} signal={signal:.4f}")
        print("  ─────────────────────────────────────────────────────────────")
