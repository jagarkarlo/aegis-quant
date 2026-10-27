"""Non-bypassable Risk Guardrail & Kill-Switch Engine.

The AI strategy and allocation layers cannot override this module. Every candidate order
must pass through IndependentRiskGuard before reaching the paper or live broker.
"""

from __future__ import annotations

from aegis_quant.models import (
    DEFAULT_UNIVERSE,
    AssetClass,
    Instrument,
    LearnedRule,
    MarketBar,
    MarketRegime,
    OrderSide,
    Position,
    RiskConfig,
    RiskDecision,
    StrategySignal,
)


class IndependentRiskGuard:
    """Enforces hard position, leverage, drawdown, correlation, spread, and learned rules."""

    def __init__(
        self,
        config: RiskConfig | None = None,
        universe: dict[str, Instrument] | None = None,
    ) -> None:
        self.config = config or RiskConfig()
        self.universe = universe or DEFAULT_UNIVERSE
        self.manual_kill_switch: bool = False

    def evaluate_signal(
        self,
        signal: StrategySignal,
        bar: MarketBar,
        regime: MarketRegime,
        equity_usd: float,
        cash_usd: float,
        current_drawdown_pct: float,
        daily_drawdown_pct: float,
        open_positions: list[Position],
        learned_rules: list[LearnedRule],
    ) -> RiskDecision:
        """Validate a strategy signal against all static and self-learned risk guardrails."""
        reasons: list[str] = []
        active_rule_ids: list[str] = []
        inst = self.universe[signal.symbol]

        # 1. Kill switch & drawdown circuit breakers
        kill_active = (
            self.manual_kill_switch
            or daily_drawdown_pct >= self.config.max_daily_drawdown_pct
            or current_drawdown_pct >= self.config.max_total_drawdown_pct
        )
        if self.manual_kill_switch:
            reasons.append("Manual kill switch is engaged.")
        if daily_drawdown_pct >= self.config.max_daily_drawdown_pct:
            reasons.append(
                f"Daily drawdown ({daily_drawdown_pct:.2f}%) breached limit ({self.config.max_daily_drawdown_pct:.2f}%)."
            )
        if current_drawdown_pct >= self.config.max_total_drawdown_pct:
            reasons.append(
                f"Total drawdown ({current_drawdown_pct:.2f}%) breached circuit breaker ({self.config.max_total_drawdown_pct:.2f}%)."
            )

        # 2. Stale data & spread spike checks
        if bar.stale_seconds > self.config.max_stale_seconds:
            reasons.append(
                f"Market feed for {signal.symbol} is stale ({bar.stale_seconds:.1f}s > {self.config.max_stale_seconds:.1f}s)."
            )

        live_spread_bps = ((bar.ask_price - bar.bid_price) / max(1e-9, bar.mid_price)) * 10000.0
        if inst.asset_class == AssetClass.FOREX and live_spread_bps > self.config.max_spread_bps_forex:
            reasons.append(
                f"Forex spread spike ({live_spread_bps:.2f} bps > {self.config.max_spread_bps_forex:.2f} bps)."
            )
        if inst.asset_class == AssetClass.CRYPTO and live_spread_bps > self.config.max_spread_bps_crypto:
            reasons.append(
                f"Crypto spread spike ({live_spread_bps:.2f} bps > {self.config.max_spread_bps_crypto:.2f} bps)."
            )

        # 3. Check learned rules from self-learning post-mortems (tightening only!)
        min_prob = self.config.min_calibrated_prob
        min_edge = self.config.min_net_edge_bps
        size_mult = 1.0

        for rule in learned_rules:
            strat_match = rule.strategy_name in (signal.strategy_name, "*")
            ac_match = rule.asset_class in (inst.asset_class.value, "*")
            reg_match = rule.regime in (regime.value, "*")
            if strat_match and ac_match and reg_match:
                active_rule_ids.append(rule.rule_id)
                min_prob += rule.min_confidence_boost
                min_edge += rule.min_edge_boost_bps
                size_mult = min(size_mult, rule.size_multiplier)

        if signal.calibrated_probability < min_prob:
            reasons.append(
                f"Calibrated probability ({signal.calibrated_probability:.3f}) below hurdle ({min_prob:.3f})."
            )
        if signal.expected_net_edge_bps < min_edge:
            reasons.append(
                f"Expected net edge ({signal.expected_net_edge_bps:.2f} bps) below cost-adjusted hurdle ({min_edge:.2f} bps)."
            )

        # 4. Prevent duplicate position in same symbol
        if any(p.symbol == signal.symbol for p in open_positions):
            reasons.append(f"Position already open for {signal.symbol}.")

        # 5. Risk-based position sizing (stop-loss distance + fractional Kelly cap)
        stop_bps = max(12.0, signal.stop_loss_bps)
        take_bps = max(20.0, signal.take_profit_bps)
        max_loss_usd = equity_usd * (self.config.max_risk_per_trade_pct / 100.0)
        risk_based_notional = max_loss_usd / (stop_bps / 10000.0)

        # Cap by single-position limit, learned size multiplier, and instrument max leverage
        max_single_notional = equity_usd * (self.config.max_single_position_pct / 100.0)
        requested_notional = min(risk_based_notional, max_single_notional) * size_mult

        # 6. Asset-class concentration cap
        current_ac_notional = sum(
            p.notional_usd for p in open_positions if p.asset_class == inst.asset_class
        )
        max_ac_pct = self.config.max_asset_class_pct.get(inst.asset_class.value, 25.0)
        max_ac_notional = equity_usd * (max_ac_pct / 100.0)
        available_ac_notional = max(0.0, max_ac_notional - current_ac_notional)
        if available_ac_notional < equity_usd * 0.02:
            reasons.append(
                f"{inst.asset_class.value} allocation cap ({max_ac_pct:.1f}%) reached."
            )

        # 7. Total portfolio leverage cap
        total_open_notional = sum(p.notional_usd for p in open_positions)
        max_total_notional = equity_usd * self.config.max_portfolio_leverage
        available_leverage_notional = max(0.0, max_total_notional - total_open_notional)
        if available_leverage_notional < equity_usd * 0.02:
            reasons.append(
                f"Portfolio leverage cap ({self.config.max_portfolio_leverage:.2f}x) reached."
            )

        # 8. Signed USD directional correlation guard for Forex pairs
        if inst.asset_class == AssetClass.FOREX:
            def _signed_usd_notional(sym: str, side: OrderSide, notional_val: float) -> float:
                pair = self.universe[sym]
                sign = 1.0 if side == OrderSide.BUY else -1.0
                if pair.base_currency == "USD":
                    return sign * notional_val
                if pair.quote_currency == "USD":
                    return -sign * notional_val
                return 0.0

            current_net_usd = sum(
                _signed_usd_notional(p.symbol, p.side, p.notional_usd)
                for p in open_positions
                if p.asset_class == AssetClass.FOREX
            )
            candidate_usd = _signed_usd_notional(signal.symbol, signal.side, requested_notional)
            projected_abs_usd = abs(current_net_usd + candidate_usd)
            max_usd_notional = equity_usd * (self.config.max_usd_directional_exposure_pct / 100.0)
            if projected_abs_usd > max_usd_notional and projected_abs_usd > abs(current_net_usd):
                headroom = max(0.0, max_usd_notional - abs(current_net_usd))
                if headroom < equity_usd * 0.02:
                    reasons.append(
                        f"Signed USD directional exposure cap ({self.config.max_usd_directional_exposure_pct:.1f}%) reached."
                    )
                else:
                    requested_notional = min(requested_notional, headroom)

        approved_notional = min(
            requested_notional,
            available_ac_notional,
            available_leverage_notional,
            max(0.0, cash_usd * inst.max_leverage),
        )

        if approved_notional < equity_usd * 0.015 and not reasons:
            reasons.append("Approved notional below minimum economic trade size (1.5% of equity).")

        if signal.side == OrderSide.BUY:
            stop_price = bar.ask_price * (1.0 - stop_bps / 10000.0)
            tp_price = bar.ask_price * (1.0 + take_bps / 10000.0)
        else:
            stop_price = bar.bid_price * (1.0 + stop_bps / 10000.0)
            tp_price = bar.bid_price * (1.0 - take_bps / 10000.0)

        approved = len(reasons) == 0 and not kill_active and approved_notional > 0.0

        return RiskDecision(
            approved=approved,
            symbol=signal.symbol,
            side=signal.side,
            requested_notional_usd=round(requested_notional, 2),
            approved_notional_usd=round(approved_notional if approved else 0.0, 2),
            stop_loss_price=round(stop_price, 6),
            take_profit_price=round(tp_price, 6),
            rejection_reasons=reasons,
            active_learned_rules=active_rule_ids,
            kill_switch_active=kill_active,
        )
