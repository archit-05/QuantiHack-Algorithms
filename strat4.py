import quantihack as qh

def on_tick(prices, positions, orders, history):
    result = []

    # ══════════════════════════════════════
    # RELATIVE STRENGTH — AGGRESSIVE MODE
    # Same entry logic but now:
    # - No take-profit (was 3% — let winners run)
    # - 10% hard stop (was 1.5%)
    # - No momentum death exit
    # - 8 positions, minimal cash reserve
    # ══════════════════════════════════════

    CAPITAL = 200000
    MAX_POSITIONS = 8
    HARD_STOP = -0.10

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

    # ── EXIT: only 10% catastrophic stop ──
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

    # ── ENTRIES: rank by momentum, buy the winners ──
    ranked = []
    for sym in ALL_SYMS:
        try:
            if positions.get(sym, {}).get('qty', 0) > 0:
                continue

            price = prices.get(sym, {}).get('price', 0)
            if price <= 0:
                continue

            h = history.get(sym, [])
            if not h or len(h) < 2:
                continue

            first = h[0]
            if first <= 0:
                continue

            mom = (price - first) / first

            if mom > 0.0005:
                ranked.append((mom, sym, price))
        except Exception:
            continue

    ranked.sort(key=lambda x: x[0], reverse=True)

    avail = CAPITAL - cap_used
    slots = MAX_POSITIONS - active
    per_pos = CAPITAL * 0.12

    for item in ranked:
        if order_count >= 9 or slots <= 0:
            break

        sym = item[1]
        price = item[2]

        spend = min(per_pos, avail - 5000)
        if spend < 500:
            break

        raw_qty = int(spend / price) if price > 0 else 0
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
        avail = avail - (buy_qty * price)
        slots = slots - 1

    return result
