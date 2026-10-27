"""Cross-asset regime detection, multi-expert signal generation, and Equities vs Forex vs Crypto vs Cash advisor."""

from __future__ import annotations

from typing import Any

from aegis_quant.models import (
    DEFAULT_UNIVERSE,
    AllocationAdvice,
    AssetBucketAdvice,
    AssetClass,
    ExpertState,
    Instrument,
    LearnedRule,
    MarketBar,
    MarketRegime,
    OrderSide,
    SourceTier,
    StrategySignal,
)
from aegis_quant.news_intelligence import NewsIntelligenceEngine


STRATEGY_EXPERTS: list[str] = [
    "MacroNewsCatalyst",
    "TrendMomentum",
    "MeanReversionZScore",
    "CarryAndYield",
    "VolatilityBreakout",
]


class CrossAssetRegimeAllocator:
    """Evaluates live market bars + verified news to recommend asset-class allocation and trade signals."""

    def __init__(
        self,
        news_engine: NewsIntelligenceEngine,
        universe: dict[str, Instrument] | None = None,
    ) -> None:
        self.news_engine = news_engine
        self.universe = universe or DEFAULT_UNIVERSE

    def detect_regime(self, bars: dict[str, MarketBar]) -> tuple[MarketRegime, float]:
        """Classify current macro/market regime from cross-asset price action and volatility."""
        spy = bars.get("SPY")
        qqq = bars.get("QQQ")
        usdjpy = bars.get("USD/JPY")
        eurusd = bars.get("EUR/USD")
        btc = bars.get("BTC/USD")

        eq_ret_20 = ((spy.return_20b_bps if spy else 0.0) + (qqq.return_20b_bps if qqq else 0.0)) / 2.0
        eq_vol = spy.realized_vol_bps if spy else 25.0
        fx_usd_strength = (
            (usdjpy.return_20b_bps if usdjpy else 0.0) - (eurusd.return_20b_bps if eurusd else 0.0)
        ) / 2.0
        crypto_vol = btc.realized_vol_bps if btc else 85.0

        if eq_vol > 52.0 or (eq_ret_20 < -180.0 and crypto_vol > 110.0):
            return MarketRegime.CRISIS_DELEVERAGING, 0.88
        if fx_usd_strength > 75.0 and eq_ret_20 < -40.0:
            return MarketRegime.RATE_SHOCK_TIGHTENING, 0.82
        if eq_ret_20 > 85.0 and eq_vol < 42.0:
            return MarketRegime.RISK_ON_GROWTH, 0.86
        if fx_usd_strength > 35.0 and abs(eq_ret_20) <= 85.0:
            return MarketRegime.INFLATION_DEFENSIVE, 0.75
        return MarketRegime.HIGH_DISPERSION_NEUTRAL, 0.72

    def estimate_roundtrip_cost_bps(
        self, inst: Instrument, bar: MarketBar, expected_holding_bars: int = 6
    ) -> float:
        """Estimate full round-trip cost: spread + 2x commission + volatility slippage + financing."""
        vol_factor = max(0.5, bar.realized_vol_bps / 25.0)
        slippage = inst.slippage_bps_per_vol * vol_factor
        financing = max(0.0, inst.overnight_swap_bps) * (expected_holding_bars / 6.0)
        return round(inst.spread_bps + (2.0 * inst.commission_bps) + slippage + financing, 2)

    def generate_expert_signals(
        self,
        bars: dict[str, MarketBar],
        regime: MarketRegime,
        expert_states: dict[str, ExpertState],
        learned_rules: list[LearnedRule],
    ) -> list[StrategySignal]:
        """Generate candidate signals across all instruments and weight them via self-learning state."""
        signals: list[StrategySignal] = []

        for sym, inst in self.universe.items():
            bar = bars.get(sym)
            if bar is None:
                continue

            cost_bps = self.estimate_roundtrip_cost_bps(inst, bar)
            matching_rules = [
                r
                for r in learned_rules
                if (r.asset_class in (inst.asset_class.value, "*"))
                and (r.regime in (regime.value, "*"))
            ]
            require_high_tier = any(
                r.require_tier1_or_tier2_news
                for r in matching_rules
                if r.strategy_name in ("MacroNewsCatalyst", "*")
            )
            news_sig = self.news_engine.aggregate_symbol_news_signal(
                sym, require_high_tier=require_high_tier
            )

            candidates = self._evaluate_instrument_experts(inst, bar, regime, cost_bps, news_sig)

            for sig in candidates:
                if sig.side == OrderSide.HOLD:
                    continue
                exp_state = expert_states.get(sig.strategy_name)
                weight = exp_state.weight if exp_state else 1.0
                slope = exp_state.calibration_slope if exp_state else 0.85

                # Apply learned rule adjustments from past post-mortems
                rule_conf_boost = sum(
                    r.min_confidence_boost
                    for r in matching_rules
                    if r.strategy_name in (sig.strategy_name, "*")
                )
                rule_edge_boost = sum(
                    r.min_edge_boost_bps
                    for r in matching_rules
                    if r.strategy_name in (sig.strategy_name, "*")
                )

                calibrated = 0.50 + (sig.raw_confidence - 0.50) * slope * min(1.25, max(0.35, weight))
                calibrated = max(0.50, min(0.90, calibrated - rule_conf_boost * 0.35))
                adjusted_gross = sig.expected_gross_bps * min(1.25, max(0.40, weight))
                net_edge = adjusted_gross - cost_bps - rule_edge_boost

                sig.calibrated_probability = round(calibrated, 4)
                sig.expected_gross_bps = round(adjusted_gross, 2)
                sig.expected_net_edge_bps = round(net_edge, 2)

                if sig.expected_net_edge_bps > 0:
                    signals.append(sig)

        signals.sort(
            key=lambda s: (s.expected_net_edge_bps / max(15.0, s.stop_loss_bps)) * s.calibrated_probability,
            reverse=True,
        )
        return signals

    def _evaluate_instrument_experts(
        self,
        inst: Instrument,
        bar: MarketBar,
        regime: MarketRegime,
        cost_bps: float,
        news_sig: dict[str, Any],
    ) -> list[StrategySignal]:
        out: list[StrategySignal] = []

        # 1. MacroNewsCatalyst Expert
        net_news_bps = float(news_sig["net_catalyst_bps"])
        if abs(net_news_bps) >= 6.0 and news_sig["contradictions_seen"] == 0:
            side = OrderSide.BUY if net_news_bps > 0 else OrderSide.SELL
            if inst.asset_class in (AssetClass.EQUITY, AssetClass.CASH) and side == OrderSide.SELL:
                side = OrderSide.HOLD
            if side != OrderSide.HOLD:
                raw_conf = min(0.88, 0.55 + abs(net_news_bps) / 80.0)
                gross = abs(net_news_bps) * 1.15
                out.append(
                    StrategySignal(
                        strategy_name="MacroNewsCatalyst",
                        symbol=inst.symbol,
                        asset_class=inst.asset_class,
                        side=side,
                        raw_confidence=round(raw_conf, 4),
                        calibrated_probability=round(raw_conf, 4),
                        expected_gross_bps=round(gross, 2),
                        expected_cost_bps=cost_bps,
                        expected_net_edge_bps=round(gross - cost_bps, 2),
                        stop_loss_bps=round(max(22.0, bar.realized_vol_bps * 1.8), 1),
                        take_profit_bps=round(max(38.0, gross * 1.6), 1),
                        rationale=f"Verified Tier 1/2 news catalyst ({net_news_bps:+.1f} bps) aligned with {regime.value}.",
                        invalidation_criteria="Contradictory official release or reversal below 5-bar VWAP/mid.",
                        news_event_ids=list(news_sig["supporting_event_ids"]),
                    )
                )

        # 2. TrendMomentum Expert (works best in RISK_ON_GROWTH & RATE_SHOCK_TIGHTENING)
        if regime in (MarketRegime.RISK_ON_GROWTH, MarketRegime.RATE_SHOCK_TIGHTENING, MarketRegime.INFLATION_DEFENSIVE):
            if abs(bar.return_20b_bps) > max(28.0, bar.realized_vol_bps * 1.1):
                side = OrderSide.BUY if bar.return_20b_bps > 0 else OrderSide.SELL
                if inst.asset_class in (AssetClass.EQUITY, AssetClass.CASH) and side == OrderSide.SELL:
                    side = OrderSide.HOLD
                if side != OrderSide.HOLD:
                    trend_strength = min(1.0, abs(bar.return_20b_bps) / max(40.0, bar.realized_vol_bps * 3.5))
                    raw_conf = 0.54 + 0.28 * trend_strength
                    gross = max(12.0, abs(bar.return_20b_bps) * 0.24)
                    out.append(
                        StrategySignal(
                            strategy_name="TrendMomentum",
                            symbol=inst.symbol,
                            asset_class=inst.asset_class,
                            side=side,
                            raw_confidence=round(raw_conf, 4),
                            calibrated_probability=round(raw_conf, 4),
                            expected_gross_bps=round(gross, 2),
                            expected_cost_bps=cost_bps,
                            expected_net_edge_bps=round(gross - cost_bps, 2),
                            stop_loss_bps=round(max(25.0, bar.realized_vol_bps * 1.9), 1),
                            take_profit_bps=round(max(45.0, bar.realized_vol_bps * 3.2), 1),
                            rationale=f"20-bar momentum ({bar.return_20b_bps:+.1f} bps) persistent in {regime.value}.",
                            invalidation_criteria="20-bar z-score crosses zero against trend direction.",
                        )
                    )

        # 3. MeanReversionZScore Expert (selective in HIGH_DISPERSION_NEUTRAL)
        if regime == MarketRegime.HIGH_DISPERSION_NEUTRAL and abs(bar.z_score_20b) > 1.75:
            side = OrderSide.BUY if bar.z_score_20b < -1.75 else OrderSide.SELL
            if inst.asset_class in (AssetClass.EQUITY, AssetClass.CASH) and side == OrderSide.SELL:
                side = OrderSide.HOLD
            if side != OrderSide.HOLD:
                raw_conf = min(0.78, 0.52 + (abs(bar.z_score_20b) - 1.5) * 0.12)
                gross = max(10.0, abs(bar.z_score_20b) * bar.realized_vol_bps * 0.45)
                out.append(
                    StrategySignal(
                        strategy_name="MeanReversionZScore",
                        symbol=inst.symbol,
                        asset_class=inst.asset_class,
                        side=side,
                        raw_confidence=round(raw_conf, 4),
                        calibrated_probability=round(raw_conf, 4),
                        expected_gross_bps=round(gross, 2),
                        expected_cost_bps=cost_bps,
                        expected_net_edge_bps=round(gross - cost_bps, 2),
                        stop_loss_bps=round(max(20.0, bar.realized_vol_bps * 1.6), 1),
                        take_profit_bps=round(max(30.0, gross * 1.3), 1),
                        rationale=f"Extreme 20-bar z-score ({bar.z_score_20b:+.2f}) in range-bound regime.",
                        invalidation_criteria="Breakout continuation beyond 2.8 standard deviations.",
                    )
                )

        # 4. CarryAndYield Expert (Positive yield in Cash/Treasuries or USD/JPY carry)
        if inst.annual_yield_pct >= 2.5 and bar.return_20b_bps >= -15.0:
            raw_conf = 0.68 if inst.asset_class == AssetClass.CASH else 0.61
            gross = 14.0 if inst.asset_class == AssetClass.CASH else 20.0
            out.append(
                StrategySignal(
                    strategy_name="CarryAndYield",
                    symbol=inst.symbol,
                    asset_class=inst.asset_class,
                    side=OrderSide.BUY,
                    raw_confidence=raw_conf,
                    calibrated_probability=raw_conf,
                    expected_gross_bps=gross,
                    expected_cost_bps=cost_bps,
                    expected_net_edge_bps=round(gross - cost_bps, 2),
                    stop_loss_bps=round(max(15.0, bar.realized_vol_bps * 1.5), 1),
                    take_profit_bps=round(max(28.0, gross * 1.8), 1),
                    rationale=f"Positive carry/yield ({inst.annual_yield_pct:.2f}% annual) with controlled volatility.",
                    invalidation_criteria="Central bank rate cut or sudden funding squeeze.",
                )
            )

        return out

    def build_allocation_advice(
        self,
        timestamp: str,
        bars: dict[str, MarketBar],
        expert_states: dict[str, ExpertState],
        learned_rules: list[LearnedRule],
        min_edge_hurdle_bps: float = 6.0,
    ) -> AllocationAdvice:
        """Produce a transparent cross-asset recommendation: Companies vs Forex vs Crypto vs Cash."""
        regime, regime_conf = self.detect_regime(bars)
        signals = self.generate_expert_signals(bars, regime, expert_states, learned_rules)
        actionable = [
            s
            for s in signals
            if s.expected_net_edge_bps >= min_edge_hurdle_bps and s.calibrated_probability >= 0.54
        ]

        # Regime-based baseline weights before tactical tilts
        base_weights: dict[AssetClass, float] = {
            MarketRegime.RISK_ON_GROWTH: {
                AssetClass.EQUITY: 60.0,
                AssetClass.FOREX: 12.0,
                AssetClass.CRYPTO: 8.0,
                AssetClass.CASH: 20.0,
            },
            MarketRegime.INFLATION_DEFENSIVE: {
                AssetClass.EQUITY: 38.0,
                AssetClass.FOREX: 22.0,
                AssetClass.CRYPTO: 0.0,
                AssetClass.CASH: 40.0,
            },
            MarketRegime.RATE_SHOCK_TIGHTENING: {
                AssetClass.EQUITY: 25.0,
                AssetClass.FOREX: 25.0,
                AssetClass.CRYPTO: 0.0,
                AssetClass.CASH: 50.0,
            },
            MarketRegime.CRISIS_DELEVERAGING: {
                AssetClass.EQUITY: 15.0,
                AssetClass.FOREX: 5.0,
                AssetClass.CRYPTO: 0.0,
                AssetClass.CASH: 80.0,
            },
            MarketRegime.HIGH_DISPERSION_NEUTRAL: {
                AssetClass.EQUITY: 45.0,
                AssetClass.FOREX: 10.0,
                AssetClass.CRYPTO: 0.0,
                AssetClass.CASH: 45.0,
            },
        }[regime]

        buckets: list[AssetBucketAdvice] = []
        for ac in (AssetClass.EQUITY, AssetClass.FOREX, AssetClass.CRYPTO, AssetClass.CASH):
            ac_signals = [s for s in actionable if s.asset_class == ac]
            best_edge = max((s.expected_net_edge_bps for s in ac_signals), default=0.0)
            if ac == AssetClass.CASH and best_edge == 0.0:
                best_edge = 11.5  # Risk-free short-duration Treasury yield baseline

            weight = base_weights[ac]
            if ac != AssetClass.CASH and not ac_signals:
                # If no positive net edge after costs exists in Forex or Crypto, shift weight to Cash!
                if ac in (AssetClass.FOREX, AssetClass.CRYPTO):
                    weight = 0.0

            buckets.append(
                self._describe_bucket(ac, weight, best_edge, ac_signals, regime)
            )

        # Normalize weights to 100% by routing unused tactical risk budget to Cash/Short-Term Treasuries
        non_cash_sum = sum(b.recommended_weight_pct for b in buckets if b.asset_class != AssetClass.CASH)
        for b in buckets:
            if b.asset_class == AssetClass.CASH:
                b.recommended_weight_pct = round(max(0.0, 100.0 - non_cash_sum), 1)

        non_cash_actionable = [s for s in actionable if s.asset_class != AssetClass.CASH]
        do_nothing = len(non_cash_actionable) == 0

        recent_news = self.news_engine.storage.list_recent_news(limit=30)
        verified_count = sum(1 for n in recent_news if int(n.source_tier) <= 2 and not n.contradicted)
        rumor_count = sum(1 for n in recent_news if n.source_tier == SourceTier.TIER_4_RUMOR)
        contradiction_count = sum(1 for n in recent_news if n.contradicted)

        if do_nothing:
            verdict = (
                f"[{regime.value}] Hold Cash / Short-Duration Treasuries (SHY) & existing core ETFs. "
                "Active Forex and Crypto signals currently do not clear transaction costs and risk hurdles."
            )
        else:
            primary_bucket = max(buckets, key=lambda b: b.recommended_weight_pct)
            top = non_cash_actionable[0]
            verdict = (
                f"[{regime.value}] Core allocation: {self._asset_label(primary_bucket.asset_class)} "
                f"({primary_bucket.recommended_weight_pct:.0f}% weight); top risk-adjusted signal: "
                f"{top.symbol} ({top.side.value}, net edge {top.expected_net_edge_bps:+.1f} bps, "
                f"calibrated win prob {top.calibrated_probability * 100:.1f}%)."
            )

        counter_thesis = (
            "If an unscheduled central-bank policy reversal or sudden liquidity shock invalidates "
            "current 20-bar trend and news catalysts, cross-asset correlations can spike toward 1.0; "
            "hard stop-losses and daily drawdown circuit breakers remain mandatory."
        )

        return AllocationAdvice(
            timestamp=timestamp,
            regime=regime,
            regime_confidence=regime_conf,
            primary_verdict=verdict,
            do_nothing_recommended=do_nothing,
            buckets=buckets,
            actionable_signals=actionable[:6],
            counter_thesis=counter_thesis,
            verified_news_count=verified_count,
            rejected_rumor_count=rumor_count,
            contradiction_count=contradiction_count,
        )

    def _asset_label(self, ac: AssetClass) -> str:
        return {
            AssetClass.EQUITY: "Companies & Diversified Equities (SPY/QQQ/MSFT/NVDA)",
            AssetClass.FOREX: "Spot Forex Majors (EUR/USD, USD/JPY, GBP/USD)",
            AssetClass.CRYPTO: "Digital Assets / Crypto (BTC/USD, ETH/USD)",
            AssetClass.CASH: "Short-Duration Treasuries & Cash Reserve (SHY)",
        }[ac]

    def _describe_bucket(
        self,
        ac: AssetClass,
        weight: float,
        best_edge_bps: float,
        ac_signals: list[StrategySignal],
        regime: MarketRegime,
    ) -> AssetBucketAdvice:
        top_syms = [s.symbol for s in ac_signals[:3]]
        if ac == AssetClass.EQUITY:
            return AssetBucketAdvice(
                asset_class=ac,
                recommended_weight_pct=round(weight, 1),
                expected_net_edge_bps=round(best_edge_bps, 2),
                annualized_vol_pct=15.5,
                suitability_score=round(min(1.0, weight / 65.0), 2),
                top_instruments=top_syms or ["SPY", "QQQ"],
                rationale="Core compounding engine backed by corporate cash flows and low proportional trading spreads.",
                key_risks="Valuation compression during rate shocks and cyclical earnings drawdowns.",
            )
        if ac == AssetClass.FOREX:
            return AssetBucketAdvice(
                asset_class=ac,
                recommended_weight_pct=round(weight, 1),
                expected_net_edge_bps=round(best_edge_bps, 2),
                annualized_vol_pct=9.5,
                suitability_score=round(min(1.0, weight / 30.0), 2),
                top_instruments=top_syms or ["USD/JPY", "EUR/USD"],
                rationale="Tactical macro expression only when central-bank policy divergence clears spread + swap costs.",
                key_risks="Overnight financing erosion, central-bank whipsaws, and excessive retail leverage.",
            )
        if ac == AssetClass.CRYPTO:
            return AssetBucketAdvice(
                asset_class=ac,
                recommended_weight_pct=round(weight, 1),
                expected_net_edge_bps=round(best_edge_bps, 2),
                annualized_vol_pct=62.0,
                suitability_score=round(min(1.0, weight / 15.0), 2),
                top_instruments=top_syms or ["BTC/USD"],
                rationale="Strictly capped satellite sleeve; only active in confirmed RISK_ON_GROWTH when net edge exceeds wide spreads.",
                key_risks="Extreme tail drawdowns, wide execution slippage (15+ bps), and regulatory/exchange shocks.",
            )
        return AssetBucketAdvice(
            asset_class=ac,
            recommended_weight_pct=round(weight, 1),
            expected_net_edge_bps=round(best_edge_bps, 2),
            annualized_vol_pct=1.8,
            suitability_score=round(min(1.0, weight / 80.0), 2),
            top_instruments=["SHY"],
            rationale="Earns ~4.35% yield while preserving dry powder and protecting capital when active edges are weak.",
            key_risks="Modest opportunity cost during strong bull runs; inflation risk over multi-year horizons.",
        )
