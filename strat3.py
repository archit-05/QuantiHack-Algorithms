import quantihack as qh

def on_tick(prices, positions, orders, history):
    result = []

    # ══════════════════════════════════════════════════════════════
    # CROSS-ASSET CONTAGION DETECTOR (Leader-Follower)
    #
    # Based on:
    # - Lo & MacKinlay (1990): cross-autocorrelation in returns
    # - Hou (2007): industry information diffusion & lead-lag effect
    # - Quantihack event structure: events affect instrument GROUPS
    #
    # KEY INSIGHT: When NVDA surges on earnings, MSFT and GOOG
    # follow — but with a DELAY. This algo detects the "leader"
    # (first mover in a sector), then buys the "followers"
    # (correlated instruments that haven't moved yet).
    #
    # This is the ONLY algo in our suite that uses CROSS-INSTRUMENT
    # signals. All others analyze each instrument independently.
    #
    # Sector correlations are hardcoded from the Quantihack event
    # structure (news feed shows which instruments move together).
    # ══════════════════════════════════════════════════════════════

    CAPITAL = 200000
    MAX_POSITIONS = 8
    ALLOC_PER_POS = 0.12
    CASH_RESERVE = 5000
    HARD_STOP = -0.10

    # Leader must move >1.2% from recent mean to qualify
    LEADER_THRESHOLD = 0.012
    # Follower must NOT have moved much yet (<0.5%)
    FOLLOWER_MAX_MOVE = 0.005

    # ── SECTOR MAP ──
    # Derived from Quantihack event structure
    # Each group represents instruments that move together on events
    SECTORS = {
        'TECH': ['SYN-NVDA', 'SYN-MSFT', 'SYN-GOOG', 'SYN-AAPL', 'SYN-META', 'SYN-AMZN'],
        'BANKING': ['SYN-JPM', 'IDX-SP500', 'IDX-FTSE'],
        'GRAINS': ['CMD-WHEAT', 'CMD-COFFEE'],
        'ENERGY': ['CMD-OIL', 'CMD-NATGAS'],
        'METALS': ['CMD-GOLD', 'CMD-PLAT', 'CMD-COPPER'],
        'INDICES': ['IDX-SP500', 'IDX-NIKKEI', 'IDX-FTSE'],
        'FX_RISK': ['FX-AUDUSD', 'FX-EURUSD', 'FX-GBPUSD'],
    }

    # Reverse map: instrument → list of sectors it belongs to
    SYM_SECTORS = {}
    for sector_name, members in SECTORS.items():
        for m in members:
            if m not in SYM_SECTORS:
                SYM_SECTORS[m] = []
            SYM_SECTORS[m].append(sector_name)

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
    # EXIT — only catastrophic 10% stop
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

    # ══════════════════════════════════════════════════
    # PHASE 1: Detect LEADERS — instruments surging
    # ══════════════════════════════════════════════════
    leaders = {}  # sym -> move_magnitude

    for sym in ALL_SYMS:
        try:
            price = prices.get(sym, {}).get('price', 0)
            if price <= 0:
                continue

            # Get recent history to compute baseline
            hist = []
            try:
                hist = qh.market_history.prices(sym, 30)
            except Exception:
                pass
            if not hist or len(hist) < 5:
                hist = history.get(sym, [])
            if not hist or len(hist) < 3:
                continue

            hlen = len(hist)

            # Compute recent mean (last 10 ticks or available)
            window = min(10, hlen)
            total = 0
            k = 0
            while k < window:
                total = total + hist[hlen - 1 - k]
                k = k + 1
            recent_mean = total / window

            if recent_mean <= 0:
                continue

            # How much has this instrument moved from its recent mean?
            move = (price - recent_mean) / recent_mean

            # Is this a leader? (moving strongly upward)
            if move > LEADER_THRESHOLD:
                leaders[sym] = move

        except Exception:
            continue

    # ══════════════════════════════════════════════════
    # PHASE 2: Find FOLLOWERS — sector peers that
    # haven't moved yet (information diffusion lag)
    # ══════════════════════════════════════════════════
    follower_scores = {}

    for leader_sym, leader_move in leaders.items():
        # What sectors does this leader belong to?
        leader_sectors = SYM_SECTORS.get(leader_sym, [])

        for sector in leader_sectors:
            peers = SECTORS.get(sector, [])

            for peer in peers:
                if peer == leader_sym:
                    continue
                if positions.get(peer, {}).get('qty', 0) > 0:
                    continue

                try:
                    peer_price = prices.get(peer, {}).get('price', 0)
                    if peer_price <= 0:
                        continue

                    # Check how much the peer has moved
                    peer_hist = history.get(peer, [])
                    if not peer_hist or len(peer_hist) < 3:
                        continue

                    peer_hlen = len(peer_hist)
                    peer_window = min(10, peer_hlen)
                    peer_total = 0
                    pk = 0
                    while pk < peer_window:
                        peer_total = peer_total + peer_hist[peer_hlen - 1 - pk]
                        pk = pk + 1
                    peer_mean = peer_total / peer_window

                    if peer_mean <= 0:
                        continue

                    peer_move = (peer_price - peer_mean) / peer_mean

                    # Peer must NOT have moved much — it's the "follower"
                    # that hasn't caught up yet
                    if peer_move < FOLLOWER_MAX_MOVE:
                        # Score = leader's move magnitude (stronger leader = more confident)
                        # accumulated across multiple leaders in same sector
                        old_score = follower_scores.get(peer, 0)
                        follower_scores[peer] = old_score + leader_move

                except Exception:
                    continue

    # ══════════════════════════════════════════════════
    # PHASE 3: Also add standalone momentum candidates
    # If no cross-asset signals, fall back to buying
    # instruments with strong individual momentum
    # (ensures capital stays deployed)
    # ══════════════════════════════════════════════════
    for sym in ALL_SYMS:
        if sym in follower_scores:
            continue
        if positions.get(sym, {}).get('qty', 0) > 0:
            continue
        try:
            price = prices.get(sym, {}).get('price', 0)
            if price <= 0:
                continue
            h = history.get(sym, [])
            if not h or len(h) < 3:
                continue
            first = h[0]
            if first <= 0:
                continue
            mom = (price - first) / first
            if mom > 0.005:
                # Individual momentum gets lower score than cross-asset
                follower_scores[sym] = mom * 0.3
        except Exception:
            continue

    # ══════════════════════════════════════════════════
    # PHASE 4: Enter positions ranked by contagion score
    # ══════════════════════════════════════════════════
    candidates = []
    for sym, score in follower_scores.items():
        if positions.get(sym, {}).get('qty', 0) > 0:
            continue
        price = prices.get(sym, {}).get('price', 0)
        if price > 0:
            candidates.append((score, sym, price))

    candidates.sort(key=lambda x: x[0], reverse=True)

    avail = CAPITAL - cap_used
    slots = MAX_POSITIONS - active

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
