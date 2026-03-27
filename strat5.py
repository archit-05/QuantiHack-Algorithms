import quantihack as qh

def on_tick(prices, positions, orders, history):
    result = []

    # ══════════════════════════════════════════════════════════
    # CUSUM EVENT DETECTOR — AGGRESSIVE MODE
    #
    # Same CUSUM detection but now:
    # - Holds multiple positions (up to 5)
    # - 10% hard stop (survive event volatility)
    # - No trailing stop (stop cutting winners)
    # - No momentum death exit (let trends run)
    # ══════════════════════════════════════════════════════════

    CAPITAL = 200000
    MAX_POSITIONS = 5
    ALLOWANCE = 0.001
    CUSUM_THRESHOLD = 0.015
    MIN_TICKS_RISING = 3
    ALLOC_PER_POS = 0.18         # 18% per position
    CASH_RESERVE = 5000
    HARD_STOP = -0.10            # 10% stop — survive everything

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
    active = 0
    for sym in ALL_SYMS:
        try:
            q = positions.get(sym, {}).get('qty', 0)
            if q > 0:
                active = active + 1
                cap_used = cap_used + (q * prices.get(sym, {}).get('price', 0))
        except Exception:
            pass

    # ══════════════════════════════════════
    # EXIT — only on catastrophic 10% loss
    # ══════════════════════════════════════
    for sym in ALL_SYMS:
        if order_count >= 9:
            break
        try:
            pos = positions.get(sym, {})
            qty = pos.get('qty', 0)
            if qty <= 0:
                continue

            price = prices.get(sym, {}).get('price', 0)
            entry = pos.get('avg_entry_price', 0)
            if price <= 0 or entry <= 0:
                continue

            pnl = (price - entry) / entry

            if pnl < HARD_STOP:
                result.append({
                    'symbol': sym,
                    'side': 'SELL',
                    'type': 'MARKET',
                    'qty': qty
                })
                order_count = order_count + 1
                cap_used = cap_used - (qty * price)
                active = active - 1
        except Exception:
            continue

    # ══════════════════════════════════════
    # EVENT DETECTION — CUSUM on all instruments
    # Now allows multiple simultaneous positions
    # ══════════════════════════════════════
    slots = MAX_POSITIONS - active
    if slots <= 0:
        return result

    candidates = []

    for sym in ALL_SYMS:
        try:
            if positions.get(sym, {}).get('qty', 0) > 0:
                continue

            price = prices.get(sym, {}).get('price', 0)
            if price <= 0:
                continue

            hist = []
            try:
                hist = qh.market_history.prices(sym, 50)
            except Exception:
                pass
            if not hist or len(hist) < 8:
                hist = history.get(sym, [])
            if not hist or len(hist) < 5:
                continue

            hlen = len(hist)

            # CUSUM computation
            cusum = 0.0
            i = 1
            while i < hlen:
                prev = hist[i - 1]
                curr = hist[i]
                if prev > 0:
                    ret = (curr - prev) / prev
                    cusum = cusum + ret - ALLOWANCE
                    if cusum < 0:
                        cusum = 0
                i = i + 1

            last_hist = hist[hlen - 1]
            if last_hist > 0:
                live_ret = (price - last_hist) / last_hist
                cusum = cusum + live_ret - ALLOWANCE
                if cusum < 0:
                    cusum = 0

            if cusum < CUSUM_THRESHOLD:
                continue

            # Directional confirmation
            if hlen >= 5:
                rising = 0
                j = hlen - 4
                while j < hlen:
                    if j > 0 and hist[j] > hist[j - 1]:
                        rising = rising + 1
                    j = j + 1
                if price > hist[hlen - 1]:
                    rising = rising + 1
                if rising < MIN_TICKS_RISING:
                    continue

            # Total move > 1%
            oldest = hist[0]
            if oldest > 0:
                total_move = (price - oldest) / oldest
                if total_move < 0.01:
                    continue

            candidates.append((cusum, sym, price))

        except Exception:
            continue

    # Enter strongest CUSUM signals
    candidates.sort(key=lambda x: x[0], reverse=True)

    avail = CAPITAL - cap_used

    for item in candidates:
        if order_count >= 9 or slots <= 0:
            break

        sym = item[1]
        price = item[2]

        alloc = CAPITAL * ALLOC_PER_POS
        spend = min(alloc, avail - CASH_RESERVE)
        if spend < 500:
            break

        raw_qty = int(spend / price) if price > 0 else 0
        max_q = MAX_QTY.get(sym, 100)
        buy_qty = min(raw_qty, max_q)

        if buy_qty > 0:
            result.append({
                'symbol': sym,
                'side': 'BUY',
                'type': 'MARKET',
                'qty': buy_qty
            })
            order_count = order_count + 1
            avail = avail - (buy_qty * price)
            slots = slots - 1

    return result
