# QuantiHack: Regime-Adaptive Stochastic Algorithmic Trading Architectures

This repository contains the codebase developed during the QuantiHack quantitative finance hackathon. The overarching project focuses on researching, developing, and backtesting robust algorithmic trading strategies capable of dynamic asset allocation across disparate financial instruments (Equities, Commodities, FX, and Indices) in stochastic market environments.

## Repository Overview 
The core of the architecture revolves around a suite of proprietary, multi-asset algorithmic models designed to adapt to shifting market macro-regimes. The repository is modular, featuring discrete execution strategies ranging from aggressive momentum-based (Relative Strength) systems to sophisticated, regime-adaptive statistical arbitrage frameworks that utilize cross-asset contagion metrics.

### Key Mathematical & Quantitative Components
- **Hurst Exponent Estimation ($H$)**: Implemented to probabilistically categorize market regimes into mean-reverting ($H < 0.5$), random walk ($H \approx 0.5$), or trending/persistent ($H > 0.5$) dynamics.
- **Dynamic Kelly Criterion Sizing**: Employed to optimize position sizing and geometrically compound capital by analytically balancing empirical win rates with asymmetric payoff ratios.
- **Stochastic Volatility Scaling**: Realized volatility estimators are integrated natively into the scoring functions, allowing the models to inversely scale allocations contingent on observed temporal variance.
- **Cross-Sector Contagion Modeling**: The `bini.py` model explicitly analyzes inter-asset dependency structures and beta contagion within thematic sectors (e.g., Tech, Energy, Metals) to uncover latent pair-trading or basket alpha characteristics.
- **Multi-Factor Alpha Generation**: The signal generation engine fuses Momentum (rate of change relative to historical means), Statistical Mean Reversion (Z-score normalized deviations), Bollinger Band expansions, and Relative Strength (RSI) indices into a consolidated weighted regime-score.

## Strategy Archetypes
1. **`bini.py` (Regime-Adaptive Framework)**: The flagship algorithm. Integrates Hurst-derived regime classification with multi-factor scoring (Momentum, Z-Scores, BB width) and peer-group contagion analysis. Employs mathematically optimal Kelly fractions for risk-adjusted capital allocation across a predefined matrix of 20+ instruments.
2. **`strat4.py` (Asymmetric Momentum Allocation)**: An aggressive, relative-strength paradigm that capitalizes on extreme momentum permutations. It features a hard 10% tail-risk stop-loss while eschewing traditional take-profit bounds to allow right-tailed leptokurtic winners to run unabated in high-liquidity environments.
3. **`strat2.py`, `strat3.py`, `strat5.py`**: A spectrum of specialized and heuristically optimized architectures designed for diversified macro environments, each fine-tuned for specific market efficiency hypotheses.

## Implementation & Execution Engine
The algorithms are designed to interface seamlessly with the proprietary `quantihack` execution sandbox (`import quantihack as qh`). State management parameters such as `prices`, `positions`, `orders`, and `history` are parsed sequentially on a tick-by-tick basis, making the logic theoretically capable of scaling from minute-level granularity up to daily structural rebalancing.

## Technical Merits
- **Resilience**: Features embedded catastrophic tail-loss protocols (hard stops) and minimal cash reserve floors to mitigate ruin probabilities.
- **Non-Stationary Adaptation**: Recognizing the non-stationary nature of financial time-series, the algorithms continuously re-weight signal significance based on the immediate statistical regime.

---
*Developed for QuantiHack. Strictly for academic and demonstrative utility.*
