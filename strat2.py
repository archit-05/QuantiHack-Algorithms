import quantihack as qh
import math
import statistics

def on_tick(prices, positions, orders, history):
    result = []

    # ══════════════════════════════════════
    # Z-SCORE MOMENTUM — AGGRESSIVE MODE
    # Same Z-score entry but now:
    # - No take-profit (was 7% — let winners run)
    # - 10% hard stop (was 2.5%)
    # - No trailing stop (was 1.5%)
    # - No momentum fade exit
    # - Minimal cash reserve
    # - 8 positions
    # ══════════════════════════════════════

    CAPITAL = 200000
    MAX_POSITIONS = 8
    BASE_ALLOC = 0.12             # 12% per position
    CASH_RESERVE = 5000
    ZSCORE_ENTRY = 2.5
    ZSCORE_SCALE_MAX = 6.0
    RSI_CEILING = 85
    HARD_STOP = -0.10             # 10% stop — survive event vol

    MAX_QTY = {
        'CMD-NATGAS': 10000, 'CMD-COFFEE': 10000, 'CMD-WHEAT': 5000,
        'CMD-LITHIUM': 5000, 'CMD-COPPER': 5000, 'CMD-DMND': 3000,
        'CMD-PLAT': 500, 'CMD-OIL': 500, 'CMD-GOLD': 30,
        'CMD-BTC': 5, 'SYN-AAPL': 500, 'SYN-GOOG': 500,
        'SYN-TSLA': 200, 'SYN-AMZN': 500, 'SYN-MSFT': 500,
        'SYN-NVDA': 100, 'SYN-META': 200, 'SYN-JPM': 500,
        'FX-EURUSD': 5000, 'FX-GBPUSD': 5000, 'FX-USDJPY': 1000,
        'FX-USDCNY': 1000, 'FX-AUDUSD': 5000,
        'IDX-SP500': 50, 'IDX-NIKKEI': 10, 'IDX-FTSE': 50,
    }

    ALL_SYMS = list(MAX_QTY.keys())
    order_count = 0

    cap_used = 0
    active_count = 0
    for sym in ALL_SYMS:
        try:
            q = positions.get(sym, {}).get('qty', 0)
            if q > 0:
                active_count = active_count + 1
                p = prices.get(sym, {}).get('price', 0)
                cap_used = cap_used + (q * p)
        except Exception:
            pass

    # ══════════════════════════════════════
    # EXIT — only catastrophic 10% stop
    # ══════════════════════════════════════
    for sym in ALL_SYMS:
        if order_count >= 8:
            break
        try:
            pos = positions.get(sym, {})
            qty = pos.get('qty', 0)
            if qty <= 0:
                continue
            if sym not in prices:
                continue

            price = prices[sym].get('price', 0)
            entry_price = pos.get('avg_entry_price', 0)
            if price <= 0 or entry_price <= 0:
                continue

            pnl_pct = (price - entry_price) / entry_price

            if pnl_pct < HARD_STOP:
                result.append({
                    'symbol': sym,
                    'side': 'SELL',
                    'type': 'MARKET',
                    'qty': qty
                })
                order_count = order_count + 1
                cap_used = cap_used - (qty * price)
                active_count = active_count - 1

        except Exception:
            continue

    available_cap = CAPITAL - cap_used

    # ══════════════════════════════════════
    # ENTRY — Z-SCORE BREAKOUT
    # ══════════════════════════════════════
    candidates = []

    for sym in ALL_SYMS:
        try:
            if positions.get(sym, {}).get('qty', 0) > 0:
                continue
            if sym not in prices:
                continue

            price = prices[sym].get('price', 0)
            if price <= 0:
                continue

            hist = []
            try:
                hist = qh.market_history.prices(sym, 200)
            except Exception:
                pass
            if not hist or len(hist) < 20:
                hist = history.get(sym, [])
            if not hist or len(hist) < 20:
                continue

            hlen = len(hist)

            last20 = hist[hlen - 20:]
            try:
                mean20 = statistics.mean(last20)
                std20 = statistics.stdev(last20) if len(last20) > 1 else 0
            except Exception:
                continue

            if std20 < 0.0000001:
                continue

            zscore = (price - mean20) / std20

            if zscore < ZSCORE_ENTRY:
                continue

            # Momentum confirmation — 3 consecutive rising
            if hlen < 3:
                continue
            p1 = hist[hlen - 3]
            p2 = hist[hlen - 2]
            p3 = hist[hlen - 1]
            if not (p3 > p2 and p2 > p1):
                continue
            if price <= p3:
                continue

            # RSI filter
            if hlen >= 14:
                try:
                    rsi_val = qh.rsi(hist, 14)
                    if rsi_val > RSI_CEILING:
                        continue
                except Exception:
                    pass

            candidates.append((zscore, sym, price))

        except Exception:
            continue

    candidates.sort(key=lambda x: x[0], reverse=True)

    slots_open = MAX_POSITIONS - active_count

    for item in candidates:
        if order_count >= 8 or slots_open <= 0:
            break

        zscore_val = item[0]
        sym = item[1]
        price = item[2]

        alloc = CAPITAL * BASE_ALLOC
        max_deployable = available_cap - CASH_RESERVE
        if max_deployable < 1000:
            break
        alloc = min(alloc, max_deployable)

        raw_qty = int(alloc / price) if price > 0 else 0
        max_q = MAX_QTY.get(sym, 100)
        buy_qty = min(raw_qty, max_q)

        if buy_qty <= 0:
            continue

        result.append({
            'symbol': sym,
            'side': 'BUY',
            'type': 'MARKET',
            'qty': buy_qty
        })
        order_count = order_count + 1
        available_cap = available_cap - (buy_qty * price)
        slots_open = slots_open - 1

    return result
