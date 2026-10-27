# Aegis Quant

**Autonomous Multi-Asset Intelligence, Cross-Asset Allocation, Paper-Trading & Self-Correcting Learning Engine with Non-Bypassable Risk Guardrails.**

[![CI](https://github.com/jagarkarlo/aegis-quant/actions/workflows/ci.yml/badge.svg)](https://github.com/jagarkarlo/aegis-quant/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Why Aegis Quant Exists

Most retail "AI trading bots" fail for four predictable reasons:

1. **Unverified Headline Chasing:** They treat every social media rumor or syndicated article as actionable alpha, buying into volatility spikes after institutional algorithms have already priced the move.
2. **Transaction Cost Blindness:** They optimize gross backtest returns while ignoring bid-ask spreads, volatility slippage, commissions, and overnight Forex/Crypto financing swaps (`COST_EROSION`).
3. **Unconstrained Adaptation:** They allow models to "learn" by increasing leverage or overfitting to recent noise, turning a temporary drawdown into a catastrophic account wipeout.
4. **Single-Asset Tunnel Vision:** They force trades in Forex or Crypto even when risk-adjusted expected value is negative, instead of comparing **Companies/Equities (`SPY`, `QQQ`, `MSFT`, `NVDA`) vs. Spot Forex (`EUR/USD`, `USD/JPY`, `GBP/USD`) vs. Crypto (`BTC/USD`, `ETH/USD`) vs. Short-Duration Treasury Cash (`SHY`)**—and choosing **"Do Nothing / Hold Treasury Cash"** when active edge does not clear transaction costs.

**Aegis Quant** solves this with a disciplined, four-layer architecture where **autonomous self-learning can tighten conviction hurdles and shrink risk after mistakes, but can never override hard portfolio guardrails**.

---

## Core Architecture

```text
+-----------------------------------------------------------------------------------+
| 1. GLOBAL NEWS & MARKET INTELLIGENCE (aegis_quant/news_intelligence.py)           |
|    • Live RSS/Atom XML ingestion + deterministic multi-regime market simulator    |
|    • Canonical token fingerprint deduplication                                    |
|    • Source credibility tiers (Tier 1 Official -> Tier 4 Unverified Rumor)        |
|    • Automatic cross-source contradiction detection & uncertainty widening        |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| 2. CROSS-ASSET REGIME & ALLOCATION ADVISOR (aegis_quant/regime_allocator.py)      |
|    • Classifies macro regime: RISK_ON_GROWTH, INFLATION_DEFENSIVE,                |
|      RATE_SHOCK_TIGHTENING, HIGH_DISPERSION_NEUTRAL, CRISIS_DELEVERAGING          |
|    • Compares Companies/Equities vs. Forex vs. Crypto vs. Treasury Cash (SHY)     |
|    • Computes Net Edge = Expected Gross Move (bps) - (Spread + Slippage + Swap)   |
|    • Explicitly recommends "DO NOTHING / HOLD CASH" when net edge < hurdle        |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| 3. SELF-LEARNING & MISTAKE POST-MORTEM BRAIN (aegis_quant/self_learning.py)       |
|    • Bayesian Beta-Bernoulli + Multiplicative Weights over 5 Strategy Experts     |
|    • Online Brier Score calibration tracking & overconfidence slope shrinkage     |
|    • Automated Root-Cause Post-Mortem on every losing or miscalibrated trade:     |
|      COST_EROSION | NEWS_WHIPSAW | REGIME_MISMATCH | OVERCONFIDENCE | STOP_GAP    |
|    • Synthesizes persistent LearnedRules in SQLite (one-way guardrail tightening) |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| 4. NON-BYPASSABLE RISK GUARD & PAPER BROKER (risk_guard.py / paper_broker.py)     |
|    • IndependentRiskGuard: max risk/trade (0.75%), max daily DD (2.5%),           |
|      max total DD (6.0%), asset-class caps, USD correlation cap, stale-feed check |
|    • Hardware/Software Kill Switch that halts orders & flattens open exposure     |
|    • Realistic PaperBroker with bid/ask spreads, volatility slippage & swap costs |
+-----------------------------------------------------------------------------------+
```

---

## Quick Start

Zero external C-extension dependencies are required—Aegis Quant runs out of the box on Python 3.11+ using the standard library (`sqlite3`, `http.server`, `urllib`, `xml`, `math`).

```bash
# Clone the repository
git clone https://github.com/jagarkarlo/aegis-quant.git
cd aegis-quant

# 1. Ask Aegis where capital belongs right now (Companies vs. Forex vs. Crypto vs. Cash)
PYTHONPATH=src python3 -m aegis_quant.cli advise

# 2. Run 15 autonomous perception -> allocation -> risk-guard -> paper-trade -> self-learning cycles
PYTHONPATH=src python3 -m aegis_quant.cli cycle --cycles 15

# 3. Inspect the Self-Learning Brain, Brier Calibration, Mistake Post-Mortems & Learned Rules
PYTHONPATH=src python3 -m aegis_quant.cli learn-report

# 4. Run a 90-bar Walk-Forward Out-of-Sample Backtest (Aegis vs. 60/40 Benchmark vs. Naive Bot)
PYTHONPATH=src python3 -m aegis_quant.cli backtest --bars 90

# 5. Ingest and verify a custom global macro headline
PYTHONPATH=src python3 -m aegis_quant.cli ingest-headline \
  "Federal Reserve signals dovish rate cut as core inflation cools" --tier 1

# 6. Sync with Trading 212 Practice/Demo API (or preview exact payloads in dry-run mode)
PYTHONPATH=src python3 -m aegis_quant.cli t212-sync --env demo --live-feeds

# 7. Run the 24/7 Autonomous Self-Managing Daemon (persistent SQLite memory + live feeds + T212)
PYTHONPATH=src python3 -m aegis_quant.cli --db aegis_state.db daemon --interval 30 --max-cycles 5 --live-feeds --t212

# 8. Launch the Interactive Web Control Center & REST API
PYTHONPATH=src python3 -m aegis_quant.cli serve --host 127.0.0.1 --port 8765
```

See [docs/TRADING212_INTEGRATION.md](./docs/TRADING212_INTEGRATION.md) for the complete Trading 212 API setup guide, endpoint reference, EU/UK UCITS ticker mapping, and safety gates.

---

## How Self-Learning & Mistake Correction Work

Every closed paper trade and forecast is audited by `SelfLearningController` (`src/aegis_quant/self_learning.py`):

1. **Probabilistic Calibration (Brier Score):**
   Each strategy expert outputs a calibrated win probability $p \in [0.50, 0.90]$. Upon trade exit ($y \in \{0, 1\}$), the controller computes Brier error $(p - y)^2$ and updates the expert's exponential moving average Brier score and calibration slope. Overconfident experts have their probabilities shrunk toward $0.50$.
2. **Bayesian Bandit Re-Weighting:**
   Strategy experts (`MacroNewsCatalyst`, `TrendMomentum`, `MeanReversionZScore`, `CarryAndYield`, `VolatilityBreakout`) are weighted dynamically using discounted Beta posteriors ($\alpha_{\text{wins}}, \beta_{\text{losses}}$) combined with cost-adjusted net return in basis points.
3. **Root-Cause Mistake Taxonomy:**
   Whenever a trade loses money or exhibits high calibration error, `MistakePostMortem` classifies the failure into one of six actionable categories:
   - `COST_EROSION`: Gross PnL was positive, but bid-ask spread, slippage, and overnight financing made Net PnL negative.
   - `NEWS_WHIPSAW`: A news-catalyzed entry reversed within $\le 3$ bars.
   - `REGIME_MISMATCH`: A trend strategy fired in a mean-reverting/crisis regime (or vice versa).
   - `OVERCONFIDENCE_BIAS`: High predicted probability ($\ge 66\%$) resulted in a loss.
   - `CORRELATED_CROWDING`: Simultaneous drawdown across correlated USD or beta exposures.
   - `STOP_LOSS_GAP`: Volatility spike caused adverse stop-loss slippage.
4. **One-Way Guardrail Tightening (`LearnedRule`):**
   Each post-mortem creates or reinforces a persistent `LearnedRule` in SQLite that automatically increases required net-edge hurdles (`+bps`), boosts minimum required probability, requires Tier 1/2 official news corroboration, or reduces the position-sizing multiplier (`0.82x` $\to$ `0.40x`). **Safety Invariant:** Autonomous learning can never loosen portfolio risk caps.

---

## Running Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

---

## Disclaimer

Aegis Quant is an engineering, quantitative research, and paper-trading decision-support system. No software or AI can eliminate market risk or guarantee profits. Always validate strategies net of realistic costs and never risk capital you cannot afford to lose.
