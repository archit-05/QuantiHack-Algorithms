import math
import statistics
import quantihack as qh


initialCapital = 1000000.0
cashUtilizationMax = 0.7
perEventAllocation = 0.15
btcAllocationMax = 0.03
maxPositionSize = 10000
stopLossAtrMultiplier = 3.0


eventPositions = {}
eventEntryTimes = {}
fallbackSchedule = []
tickCount = 0


longKeywords = [
    "rally",
    "surge",
    "boom",
    "recovery",
    "growth",
    "harvest good",
    "demand",
]

shortKeywords = [
    "crisis",
    "crash",
    "selloff",
    "cascade",
    "liquidation",
    "collapse",
    "shortage",
    "sanctions",
]

fallbackDefinitions = [
    {"headline": "EV Metals Rally", "instruments": ["CMD-COPPER", "CMD-LITHIUM", "CMD-PLAT"]},
    {"headline": "Tech Selloff Cascade", "instruments": ["SYN-NVDA", "SYN-AAPL", "SYN-MSFT", "SYN-META"]},
    {"headline": "Grain Harvest Crisis", "instruments": ["CMD-WHEAT", "CMD-COFFEE"]},
    {"headline": "Crypto Liquidation Cascade", "instruments": ["CMD-BTC"]},
]


def getCurrentTimeMs(prices):
    for symbol in prices:
        try:
            ticks = qh.market_history.ticks(symbol, 1)
            if ticks and len(ticks) > 0 and "t" in ticks[-1]:
                return int(ticks[-1]["t"])
        except Exception:
            continue
    return 0


def getDirection(headline):
    text = str(headline).lower()
    for keyword in longKeywords:
        if keyword in text:
            return 1
    for keyword in shortKeywords:
        if keyword in text:
            return -1
    return 0


def estimateCash(positions):
    cash = initialCapital
    for symbol, position in positions.items():
        qty = float(position.get("qty", 0))
        avgEntryPrice = float(position.get("avg_entry_price", 0))
        if qty > 0:
            cash -= qty * avgEntryPrice
    return cash


def estimatePortfolioValue(prices, positions, cash):
    value = cash
    for symbol, position in positions.items():
        qty = float(position.get("qty", 0))
        markPrice = float(prices.get(symbol, {}).get("price", position.get("avg_entry_price", 0)))
        value += qty * markPrice
    return value


def atrFromHistory(symbol):
    try:
        samples = qh.market_history.prices(symbol, 20)
    except Exception:
        return 0.0
    if not samples or len(samples) < 2:
        return 0.0
    changes = []
    index = 1
    while index < len(samples):
        changes.append(abs(float(samples[index]) - float(samples[index - 1])))
        index += 1
    if not changes:
        return 0.0
    return statistics.mean(changes)


def getFallbackSchedule(currentTimeMs):
    global fallbackSchedule
    needsRefresh = False
    if not fallbackSchedule:
        needsRefresh = True
    else:
        validTimes = [int(item.get("time", 0)) for item in fallbackSchedule if int(item.get("time", 0)) > 0]
        latestEventTime = max(validTimes) if validTimes else 0
        if latestEventTime == 0 or latestEventTime < currentTimeMs - 60000:
            needsRefresh = True
    if needsRefresh:
        offsets = [90000, 150000, 210000, 270000]
        rebuilt = []
        for index, item in enumerate(fallbackDefinitions):
            rebuilt.append(
                {
                    "headline": item["headline"],
                    "instruments": list(item["instruments"]),
                    "time": currentTimeMs + offsets[index % len(offsets)],
                }
            )
        fallbackSchedule = rebuilt
    return list(fallbackSchedule)


def on_tick(prices, positions, orders, history):
    global eventPositions
    global eventEntryTimes
    global tickCount
    tickCount += 1

    ords = []
    currentTimeMs = getCurrentTimeMs(prices)

    newsRecent = []
    try:
        newsRecent = qh.news.recent()
    except Exception:
        newsRecent = []

    usingFallback = False
    try:
        upcomingEvents = qh.news.upcoming()
        if not isinstance(upcomingEvents, list):
            upcomingEvents = []
    except Exception:
        usingFallback = True
        upcomingEvents = getFallbackSchedule(currentTimeMs)

    if not upcomingEvents and usingFallback:
        upcomingEvents = getFallbackSchedule(currentTimeMs)

    estimatedCash = estimateCash(positions)
    portfolioValue = estimatePortfolioValue(prices, positions, estimatedCash)
    maxSpend = max(0.0, estimatedCash * cashUtilizationMax)
    spendUsed = 0.0

    for event in upcomingEvents:
        if not isinstance(event, dict):
            continue
        headline = str(event.get("headline", ""))
        instruments = event.get("instruments", [])
        eventTime = int(event.get("time", 0))
        if not instruments or eventTime <= 0 or currentTimeMs <= 0:
            continue

        eventKey = headline + "|" + str(eventTime)
        secondsUntil = (eventTime - currentTimeMs) / 1000.0
        direction = getDirection(headline)
        if direction == 0:
            continue

        if 60.0 <= secondsUntil <= 120.0 and eventKey not in eventPositions:
            validSymbols = [symbol for symbol in instruments if symbol in prices]
            if not validSymbols:
                continue
            perEventBudget = portfolioValue * perEventAllocation
            perSymbolBudget = perEventBudget / float(len(validSymbols))
            eventPositions[eventKey] = {}
            for symbol in validSymbols:
                ticker = prices.get(symbol, {})
                if direction > 0:
                    px = float(ticker.get("ask", ticker.get("price", 0)))
                    side = "BUY"
                else:
                    px = float(ticker.get("bid", ticker.get("price", 0)))
                    side = "SELL"
                if px <= 0:
                    continue

                symbolBudget = perSymbolBudget
                if symbol == "CMD-BTC":
                    symbolBudget = min(symbolBudget, portfolioValue * btcAllocationMax)

                positionQty = int(float(positions.get(symbol, {}).get("qty", 0)))
                room = maxPositionSize - abs(positionQty)
                if room <= 0:
                    continue

                qty = int(math.floor(symbolBudget / px))
                qty = min(qty, room)
                if qty <= 0:
                    continue

                if side == "BUY":
                    remainCash = maxSpend - spendUsed
                    maxByCash = int(math.floor(remainCash / px))
                    qty = min(qty, maxByCash)
                    if qty <= 0:
                        continue
                    spendUsed += qty * px

                ords.append({"symbol": symbol, "side": side, "type": "MARKET", "qty": qty})
                eventPositions[eventKey][symbol] = {"side": side, "qty": qty}
                eventEntryTimes[symbol] = currentTimeMs

    activeKeys = list(eventPositions.keys())
    for eventKey in activeKeys:
        if "|" not in eventKey:
            continue
        parts = eventKey.rsplit("|", 1)
        if len(parts) != 2:
            continue
        eventTime = int(parts[1])
        secondsUntil = (eventTime - currentTimeMs) / 1000.0 if currentTimeMs > 0 else 999999.0
        symbolMap = eventPositions.get(eventKey, {})
        symbols = list(symbolMap.keys())
        shouldClose = secondsUntil < -60.0

        for symbol in symbols:
            posData = positions.get(symbol, {})
            qtyNow = int(float(posData.get("qty", 0)))
            if qtyNow == 0:
                symbolMap.pop(symbol, None)
                eventEntryTimes.pop(symbol, None)
                continue

            px = float(prices.get(symbol, {}).get("price", posData.get("avg_entry_price", 0)))
            avgEntryPrice = float(posData.get("avg_entry_price", px))
            atr = atrFromHistory(symbol)
            stopDistance = stopLossAtrMultiplier * atr
            stopTriggered = False
            if stopDistance > 0:
                if qtyNow > 0 and px <= avgEntryPrice - stopDistance:
                    stopTriggered = True
                if qtyNow < 0 and px >= avgEntryPrice + stopDistance:
                    stopTriggered = True

            if shouldClose or stopTriggered:
                closeSide = "SELL" if qtyNow > 0 else "BUY"
                ords.append({"symbol": symbol, "side": closeSide, "type": "MARKET", "qty": abs(qtyNow)})
                symbolMap.pop(symbol, None)
                eventEntryTimes.pop(symbol, None)

        if not symbolMap:
            eventPositions.pop(eventKey, None)

    openPositionCount = sum(1 for symbol in positions if int(float(positions[symbol].get("qty", 0))) != 0)
    sentOrders = ords[:10]
    print(
        "tick",
        tickCount,
        "events",
        len(upcomingEvents),
        "open",
        openPositionCount,
        "orders",
        len(sentOrders),
        "recentNews",
        len(newsRecent) if isinstance(newsRecent, list) else 0,
    )
    return sentOrders
