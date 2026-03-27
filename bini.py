import quantihack as qh
import math
import statistics


def estimate_hurst(data):
    n = len(data)
    if n < 16:
        return 0.5
    rets = []
    i = 1
    while i < n:
        if data[i - 1] > 0:
            rets.append((data[i] - data[i - 1]) / data[i - 1])
        i = i + 1
    if len(rets) < 10:
        return 0.5
    try:
        m = statistics.mean(rets)
        cumdev = []
        running = 0
        for r in rets:
            running = running + (r - m)
            cumdev.append(running)
        R = max(cumdev) - min(cumdev)
        S = statistics.stdev(rets) if len(rets) > 1 else 0.0001
        if S < 0.0000001:
            return 0.5
        RS = R / S
        if RS <= 0:
            return 0.5
        H = math.log(RS) / math.log(len(rets))
        return max(0.1, min(0.9, H))
    except Exception:
        return 0.5


def realized_vol(data):
    if len(data) < 5:
        return 0.02
    rets = []
    i = 1
    while i < len(data):
        if data[i - 1] > 0:
            rets.append((data[i] - data[i - 1]) / data[i - 1])
        i = i + 1
    if len(rets) < 3:
        return 0.02
    try:
        return max(statistics.stdev(rets), 0.001)
    except Exception:
        return 0.02


def kelly_fraction(win_rate, avg_win, avg_loss):
    if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
        return 0.05
    b = avg_win / avg_loss
    q = 1 - win_rate
    f = (win_rate * b - q) / b
    return max(0.02, min(0.25, f)) * 0.5


def compute_signal(sym, price, hist, hurst):
    if len(hist) < 20:
        return 0, 'NONE'
    hlen = len(hist)
    mom_score = 0
    try:
        fast = sum(hist[-5:]) / 5
        slow = sum(hist[-20:]) / 20
        if slow > 0:
            mom_score = (fast - slow) / slow
    except Exception:
        pass

    mr_score = 0
    try:
        mean20 = statistics.mean(hist[-20:])
        std20 = statistics.stdev(hist[-20:]) if len(hist[-20:]) > 1 else 1
        if std20 > 0.0000001:
            zscore = (price - mean20) / std20
            if zscore > 2:
                mr_score = zscore * 0.01
    except Exception:
        pass

    bb_score = 0
    try:
        bb = qh.bollinger_bands(hist, 20, 2.0)
        if bb:
            upper = bb[0]
            lower = bb[2]
            if isinstance(upper, list):
                upper = upper[-1]
            if isinstance(lower, list):
                lower = lower[-1]
            if upper is not None and lower is not None:
                bw = upper - lower
                if bw > 0 and price > upper:
                    bb_score = (price - upper) / bw * 0.02
    except Exception:
        pass

    rsi_score = 0
    try:
        rsi_val = qh.rsi(hist, 14)
        if isinstance(rsi_val, list):
            rsi_val = rsi_val[-1]
        if rsi_val is not None:
            if rsi_val > 85:
                rsi_score = -0.01
            elif rsi_val > 60:
                rsi_score = (rsi_val - 50) / 500
    except Exception:
        pass

    if hurst > 0.55:
        mom_weight = min(0.5 + (hurst - 0.55) * 2, 0.9)
        mr_weight = 1.0 - mom_weight
    elif hurst < 0.45:
        mr_weight = min(0.5 + (0.45 - hurst) * 2, 0.9)
        mom_weight = 1.0 - mr_weight
    else:
        mom_weight = 0.5
        mr_weight = 0.5

    score = mom_score * mom_weight + mr_score * mr_weight + bb_score * 0.3 + rsi_score * 0.2

    direction = 'NONE'
    if score > 0.003:
        up_count = 0
        j = max(0, hlen - 4)
        while j < hlen:
            if j > 0 and hist[j] > hist[j - 1]:
                up_count = up_count + 1
            j = j + 1
        if up_count >= 2:
            direction = 'BUY'

    return score, direction


def on_tick(prices, positions, orders, history):
    result = []
    order_count = 0

    # ── All constants defined INSIDE on_tick for sandbox compatibility ──
    TOTAL_CAPITAL = 200000
    MAX_POSITIONS = 12
    TARGET_VOL = 0.15
    MAX_SINGLE_ALLOC = 0.15
    MIN_ALLOC = 500
    CASH_FLOOR = 5000
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

    SECTORS = {
        'TECH': ['SYN-NVDA', 'SYN-MSFT', 'SYN-GOOG', 'SYN-AAPL', 'SYN-META', 'SYN-AMZN'],
        'GRAINS': ['CMD-WHEAT', 'CMD-COFFEE'],
        'ENERGY': ['CMD-OIL', 'CMD-NATGAS'],
        'METALS': ['CMD-GOLD', 'CMD-PLAT', 'CMD-COPPER', 'CMD-LITHIUM'],
        'EV': ['SYN-TSLA', 'CMD-LITHIUM', 'CMD-COPPER'],
        'INDICES': ['IDX-SP500', 'IDX-NIKKEI', 'IDX-FTSE'],
        'FX_RISK': ['FX-AUDUSD', 'FX-EURUSD', 'FX-GBPUSD'],
    }

    SYM_SECTORS = {}
    for sname, members in SECTORS.items():
        for m in members:
            if m not in SYM_SECTORS:
                SYM_SECTORS[m] = []
            SYM_SECTORS[m].append(sname)

    ALL_SYMS = list(MAX_QTY.keys())

    cap_used = 0
    active = 0
    port_positions = {}
    for sym in ALL_SYMS:
        try:
            pos = positions.get(sym, {})
            q = pos.get('qty', 0)
            if q and q > 0:
                active = active + 1
                p = prices.get(sym, {}).get('price', 0)
                cap_used = cap_used + (q * p)
                port_positions[sym] = {
                    'qty': q,
                    'price': p,
                    'entry': pos.get('avg_entry_price', 0)
                }
        except Exception:
            pass

    # EXIT — hard stop only
    for sym, pdata in port_positions.items():
        if order_count >= 8:
            break
        try:
            qty = pdata['qty']
            price = pdata['price']
            entry = pdata['entry']
            if price <= 0 or entry <= 0 or qty <= 0:
                continue
            pnl_pct = (price - entry) / entry
            if pnl_pct < HARD_STOP:
                result.append({'symbol': sym, 'side': 'SELL', 'type': 'MARKET', 'qty': qty})
                order_count = order_count + 1
                cap_used = cap_used - (qty * price)
                active = active - 1
        except Exception:
            continue

    # ENTRY — regime-adaptive
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
            if not hist or len(hist) < 15:
                continue

            hurst = estimate_hurst(hist)
            vol = realized_vol(hist)
            score, direction = compute_signal(sym, price, hist, hurst)

            if direction != 'BUY':
                continue

            # Contagion bonus
            sectors = SYM_SECTORS.get(sym, [])
            contagion = 0
            for sector in sectors:
                peers = SECTORS.get(sector, [])
                peer_moves = 0
                peer_count = 0
                for peer in peers:
                    if peer == sym:
                        continue
                    try:
                        pp = prices.get(peer, {}).get('price', 0)
                        ph = history.get(peer, [])
                        if not ph or len(ph) < 3 or pp <= 0:
                            continue
                        pm = sum(ph[-5:]) / min(5, len(ph))
                        if pm > 0:
                            pmove = (pp - pm) / pm
                            if pmove > 0.008:
                                peer_moves = peer_moves + pmove
                                peer_count = peer_count + 1
                    except Exception:
                        continue
                if peer_count >= 1:
                    contagion = contagion + peer_moves / peer_count
            score = score + contagion * 0.5

            # Kelly sizing
            wins = 0
            total = 0
            win_sum = 0
            loss_sum = 0
            k = 1
            while k < len(hist):
                if hist[k - 1] > 0:
                    r = (hist[k] - hist[k - 1]) / hist[k - 1]
                    total = total + 1
                    if r > 0:
                        wins = wins + 1
                        win_sum = win_sum + r
                    elif r < 0:
                        loss_sum = loss_sum + abs(r)
                k = k + 1

            if total > 5 and wins > 0 and (total - wins) > 0:
                kf = kelly_fraction(wins / total, win_sum / wins, loss_sum / (total - wins))
            else:
                kf = 0.05

            vol_scale = min(TARGET_VOL / vol, 2.0) if vol > 0 else 1.0
            sizing = kf * vol_scale

            candidates.append((score, sym, price, sizing))

        except Exception:
            continue

    candidates.sort(key=lambda x: x[0], reverse=True)

    avail = TOTAL_CAPITAL - cap_used
    slots = MAX_POSITIONS - active

    for item in candidates:
        if order_count >= 9 or slots <= 0:
            break
        sym = item[1]
        price = item[2]
        sizing = item[3]

        alloc = TOTAL_CAPITAL * min(sizing, MAX_SINGLE_ALLOC)
        spend = min(alloc, avail - CASH_FLOOR)
        if spend < MIN_ALLOC:
            break

        raw_qty = int(spend / price) if price > 0 else 0
        max_q = MAX_QTY.get(sym, 100)
        buy_qty = min(raw_qty, max_q)

        if buy_qty <= 0:
            continue

        result.append({'symbol': sym, 'side': 'BUY', 'type': 'MARKET', 'qty': buy_qty})
        order_count = order_count + 1
        avail = avail - (buy_qty * price)
        slots = slots - 1

    return result
