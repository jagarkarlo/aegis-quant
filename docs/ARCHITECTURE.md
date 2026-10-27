# Aegis Quant Architecture & Quantitative Design Notes

## 1. The Multi-Asset Decision Hierarchy

Aegis Quant treats capital allocation as a hierarchical problem rather than isolated ticker prediction:

1. **Macro Regime Detection (`CrossAssetRegimeAllocator.detect_regime`)**
   Uses 20-bar cross-asset returns, realized volatility (`SPY`, `BTC/USD`), and FX rate-differential proxies (`USD/JPY` vs. `EUR/USD`) to classify the environment into:
   - `RISK_ON_GROWTH`
   - `INFLATION_DEFENSIVE`
   - `RATE_SHOCK_TIGHTENING`
   - `HIGH_DISPERSION_NEUTRAL`
   - `CRISIS_DELEVERAGING`

2. **Cost-Adjusted Net Edge Calculation**
   For every instrument $i$, round-trip execution friction is modeled explicitly:
   $$\text{Cost}_i(\text{bps}) = \text{Spread}_i + 2 \times \text{Commission}_i + \text{Slippage}_i(\sigma_i) + \text{Swap}_i(T)$$
   A signal is only considered actionable when:
   $$\text{Expected Net Edge}_i = \text{Calibrated Gross Move}_i - \text{Cost}_i - \Delta_{\text{learned}} \ge \text{Hurdle}_{\min}$$

3. **"Do Nothing / Hold Cash" Default**
   Whenever active signals in Equities, Forex, or Crypto fail to clear the cost-adjusted hurdle, unused risk weight is routed automatically to `SHY` (1–3 Year US Treasury Cash Reserve yielding ~4.35% annually).

## 2. News Verification & Contradiction Resolution

`NewsIntelligenceEngine` prevents headline-chasing whipsaws through three filters:
- **Canonical Fingerprint Deduplication:** Strips stopwords, normalizes tokens, and hashes sorted stems so syndicated wire copies are never double-counted.
- **Source Tier Credibility:**
  - Tier 1 (`0.96`): Official central bank statements, statistical agencies, regulatory filings.
  - Tier 2 (`0.84`): Major institutional financial wires.
  - Tier 3 (`0.62`): Secondary financial commentary.
  - Tier 4 (`0.18`): Unverified social rumors (automatically excluded from trade triggers).
- **Contradiction Penalty:** When two recent stories on the same symbol carry opposing polarity ($\text{sentiment}_1 \times \text{sentiment}_2 < -0.20$), the event is marked `contradicted = True`, its credibility weight is slashed by 65%, and uncertainty bounds widen by $1.8\times$.

## 3. Non-Bypassable Risk Guard (`IndependentRiskGuard`)

Every candidate signal must pass eight independent checks before execution:
1. Manual and automatic Drawdown Kill Switch (`daily_dd < 2.5%`, `total_dd < 6.0%`).
2. Market data freshness (`stale_seconds <= 30s`) and live spread spike gates.
3. Self-learned conviction and net-edge hurdle boosts (`LearnedRule`).
4. Duplicate symbol prevention.
5. Stop-loss distance risk budgeting (`max_risk_per_trade_pct = 0.75%` of equity).
6. Asset-class concentration caps (`EQUITY <= 65%`, `FOREX <= 25%`, `CRYPTO <= 12%`).
7. Portfolio gross leverage ceiling (`<= 1.25x`).
8. Cross-asset USD directional exposure cap (`<= 35%`).
