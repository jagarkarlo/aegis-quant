"""Self-learning meta-controller, Brier calibration tracker, and automated mistake post-mortem engine."""

from __future__ import annotations

from typing import Any

from aegis_quant.models import (
    ExpertState,
    LearnedRule,
    MarketRegime,
    MistakeCategory,
    MistakePostMortem,
    TradeRecord,
)
from aegis_quant.regime_allocator import STRATEGY_EXPERTS
from aegis_quant.storage import AegisStorage


class SelfLearningController:
    """Learns from closed trades and forecast errors using Bayesian bandit weights and post-mortem rules.

    Safety Invariant:
        Autonomous self-learning may tighten conviction thresholds, raise minimum net-edge hurdles,
        require higher-tier news verification, or shrink position sizing multipliers. It can NEVER
        loosen hard portfolio risk limits (leverage, drawdown circuit breakers, or concentration caps).
    """

    def __init__(self, storage: AegisStorage, forgetting_factor: float = 0.92) -> None:
        self.storage = storage
        self.forgetting_factor = forgetting_factor
        self._ensure_experts_initialized()

    def _ensure_experts_initialized(self) -> None:
        existing = self.storage.list_expert_states()
        for name in STRATEGY_EXPERTS:
            if name not in existing:
                self.storage.save_expert_state(
                    ExpertState(
                        strategy_name=name,
                        weight=1.0,
                        alpha_wins=2.0,
                        beta_losses=2.0,
                        ewma_brier_score=0.22,
                        ewma_net_return_bps=5.0,
                        total_predictions=0,
                        calibration_slope=0.85,
                    )
                )

    def get_expert_states(self) -> dict[str, ExpertState]:
        self._ensure_experts_initialized()
        return self.storage.list_expert_states()

    def process_closed_trade(self, trade: TradeRecord) -> MistakePostMortem | None:
        """Update strategy expert weights, calibration slope, and run root-cause post-mortem if needed."""
        states = self.get_expert_states()
        st = states.get(trade.strategy_name)
        if st is None:
            st = ExpertState(
                strategy_name=trade.strategy_name,
                weight=1.0,
                alpha_wins=2.0,
                beta_losses=2.0,
                ewma_brier_score=0.22,
                ewma_net_return_bps=0.0,
                total_predictions=0,
                calibration_slope=0.85,
            )

        win = 1 if trade.net_pnl_usd > 0 else 0
        pred_p = max(0.50, min(0.95, trade.calibrated_prob_at_entry))
        brier = (pred_p - float(win)) ** 2

        g = self.forgetting_factor
        st.alpha_wins = round(st.alpha_wins * g + (1.0 if win else 0.0), 4)
        st.beta_losses = round(st.beta_losses * g + (0.0 if win else 1.0), 4)
        st.ewma_brier_score = round(st.ewma_brier_score * g + brier * (1.0 - g), 4)
        st.ewma_net_return_bps = round(
            st.ewma_net_return_bps * g + trade.net_return_bps * (1.0 - g), 3
        )
        st.total_predictions += 1

        # Adjust calibration slope: shrink overconfident experts, gently reward well-calibrated ones
        if win == 0 and pred_p >= 0.62:
            st.calibration_slope = round(max(0.45, st.calibration_slope * 0.92), 4)
        elif win == 1 and brier < 0.18:
            st.calibration_slope = round(min(1.05, st.calibration_slope * 1.02), 4)

        # Compute multiplicative expert weight from Bayesian win rate + Brier calibration + net return
        bayesian_win_rate = st.alpha_wins / max(1e-6, st.alpha_wins + st.beta_losses)
        brier_bonus = max(0.6, 1.25 - st.ewma_brier_score * 1.5)
        return_bonus = max(0.5, min(1.4, 1.0 + st.ewma_net_return_bps / 60.0))
        st.weight = round(max(0.35, min(1.50, (bayesian_win_rate / 0.50) * brier_bonus * return_bonus)), 4)
        self.storage.save_expert_state(st)

        # Trigger automated mistake post-mortem on any losing trade or severe calibration error
        if trade.net_pnl_usd < 0 or brier >= 0.38:
            pm = self._diagnose_mistake(trade, win, brier)
            self.storage.save_postmortem(pm)
            self._synthesize_or_update_learned_rule(trade, pm)
            return pm

        return None

    def inject_synthetic_mistake(
        self,
        timestamp: str,
        symbol: str,
        asset_class_str: str,
        strategy_name: str,
        regime_str: str,
        category_str: str,
        gross_pnl_usd: float = 18.0,
        cost_usd: float = 42.0,
        predicted_prob: float = 0.76,
    ) -> tuple[MistakePostMortem, LearnedRule]:
        """Inject a controlled mistake scenario to verify or demonstrate self-correction."""
        from aegis_quant.models import AssetClass, OrderSide

        ac = AssetClass(asset_class_str)
        reg = MarketRegime(regime_str)
        cat = MistakeCategory(category_str)
        net_pnl = gross_pnl_usd - cost_usd
        trade = TradeRecord(
            trade_id=f"sim-mistake-{abs(hash((timestamp, symbol, category_str))) % 10000:04d}",
            symbol=symbol,
            asset_class=ac,
            side=OrderSide.BUY,
            strategy_name=strategy_name,
            regime_at_entry=reg,
            opened_at=timestamp,
            closed_at=timestamp,
            entry_price=100.0,
            exit_price=99.6,
            notional_usd=5000.0,
            gross_pnl_usd=gross_pnl_usd,
            total_cost_usd=cost_usd,
            net_pnl_usd=net_pnl,
            net_return_bps=(net_pnl / 5000.0) * 10000.0,
            calibrated_prob_at_entry=predicted_prob,
            exit_reason="STOP_LOSS",
            bars_held=2,
            news_catalyzed=(cat == MistakeCategory.NEWS_WHIPSAW),
        )
        pm = self._diagnose_mistake(trade, 0, (predicted_prob - 0.0) ** 2, forced_category=cat)
        self.storage.save_postmortem(pm)
        rule = self._synthesize_or_update_learned_rule(trade, pm)
        return pm, rule

    def _diagnose_mistake(
        self,
        trade: TradeRecord,
        win: int,
        brier: float,
        forced_category: MistakeCategory | None = None,
    ) -> MistakePostMortem:
        if forced_category is not None:
            category = forced_category
        elif trade.gross_pnl_usd > 0 and trade.net_pnl_usd < 0:
            category = MistakeCategory.COST_EROSION
        elif trade.news_catalyzed and trade.bars_held <= 3 and trade.net_pnl_usd < 0:
            category = MistakeCategory.NEWS_WHIPSAW
        elif (
            trade.strategy_name == "TrendMomentum"
            and trade.regime_at_entry in (MarketRegime.HIGH_DISPERSION_NEUTRAL, MarketRegime.CRISIS_DELEVERAGING)
        ):
            category = MistakeCategory.REGIME_MISMATCH
        elif trade.calibrated_prob_at_entry >= 0.66 and win == 0:
            category = MistakeCategory.OVERCONFIDENCE_BIAS
        elif trade.exit_reason == "STOP_LOSS" and abs(trade.net_return_bps) > 35.0:
            category = MistakeCategory.STOP_LOSS_GAP
        else:
            category = MistakeCategory.REGIME_MISMATCH

        diagnosis_map = {
            MistakeCategory.COST_EROSION: (
                f"Gross PnL was positive (${trade.gross_pnl_usd:+.2f}), but spread, slippage, and swap costs "
                f"(${trade.total_cost_usd:.2f}) turned net PnL negative (${trade.net_pnl_usd:+.2f})."
            ),
            MistakeCategory.NEWS_WHIPSAW: (
                f"News-catalyzed entry on {trade.symbol} reversed within {trade.bars_held} bars, "
                "indicating headline overreaction or low-tier source noise."
            ),
            MistakeCategory.REGIME_MISMATCH: (
                f"Strategy {trade.strategy_name} underperformed in {trade.regime_at_entry.value} regime "
                f"(net return {trade.net_return_bps:+.1f} bps)."
            ),
            MistakeCategory.OVERCONFIDENCE_BIAS: (
                f"High entry probability ({trade.calibrated_prob_at_entry:.2f}) failed on {trade.symbol} "
                f"(Brier error {brier:.3f}), signaling miscalibrated confidence."
            ),
            MistakeCategory.CORRELATED_CROWDING: (
                f"Position on {trade.symbol} suffered correlated drawdown alongside existing exposure."
            ),
            MistakeCategory.STOP_LOSS_GAP: (
                f"Adverse volatility spike on {trade.symbol} caused stop-loss slippage beyond baseline."
            ),
        }

        action_map = {
            MistakeCategory.COST_EROSION: "Raise minimum net-edge hurdle (+3.0 bps) and shrink size multiplier for this asset class.",
            MistakeCategory.NEWS_WHIPSAW: "Require Tier 1/2 official/wire corroboration and raise confidence threshold (+0.03).",
            MistakeCategory.REGIME_MISMATCH: "Penalize strategy weight in this regime (+2.5 bps hurdle, 0.80x size multiplier).",
            MistakeCategory.OVERCONFIDENCE_BIAS: "Shrink expert calibration slope and increase minimum calibrated probability (+0.04).",
            MistakeCategory.CORRELATED_CROWDING: "Tighten cross-asset position sizing multiplier to 0.75x.",
            MistakeCategory.STOP_LOSS_GAP: "Reduce position size multiplier to 0.75x and require wider net-edge buffer (+2.5 bps).",
        }

        severity = round(min(1.0, max(0.15, brier + abs(trade.net_return_bps) / 120.0)), 3)
        pm_id = f"pm-{trade.trade_id}"

        return MistakePostMortem(
            postmortem_id=pm_id,
            trade_id=trade.trade_id,
            timestamp=trade.closed_at,
            symbol=trade.symbol,
            asset_class=trade.asset_class,
            strategy_name=trade.strategy_name,
            regime=trade.regime_at_entry,
            category=category,
            severity=severity,
            predicted_prob=trade.calibrated_prob_at_entry,
            realized_win=win,
            brier_error=round(brier, 4),
            gross_pnl_usd=trade.gross_pnl_usd,
            cost_usd=trade.total_cost_usd,
            net_pnl_usd=trade.net_pnl_usd,
            diagnosis=diagnosis_map[category],
            corrective_action=action_map[category],
        )

    def _synthesize_or_update_learned_rule(
        self, trade: TradeRecord, pm: MistakePostMortem
    ) -> LearnedRule:
        rule_id = f"rule-{trade.strategy_name}-{trade.asset_class.value}-{trade.regime_at_entry.value}"
        existing_rules = {r.rule_id: r for r in self.storage.list_learned_rules()}
        prev = existing_rules.get(rule_id)

        conf_step = 0.025 if pm.category in (MistakeCategory.OVERCONFIDENCE_BIAS, MistakeCategory.NEWS_WHIPSAW) else 0.015
        edge_step = 3.0 if pm.category == MistakeCategory.COST_EROSION else 2.0
        require_t12 = pm.category == MistakeCategory.NEWS_WHIPSAW or (prev.require_tier1_or_tier2_news if prev else False)

        if prev is None:
            rule = LearnedRule(
                rule_id=rule_id,
                created_at=trade.closed_at,
                strategy_name=trade.strategy_name,
                asset_class=trade.asset_class.value,
                regime=trade.regime_at_entry.value,
                mistake_category=pm.category.value,
                min_confidence_boost=round(conf_step, 4),
                min_edge_boost_bps=round(edge_step, 2),
                size_multiplier=0.82,
                require_tier1_or_tier2_news=require_t12,
                cooldown_bars=2 if pm.category == MistakeCategory.NEWS_WHIPSAW else 1,
                evidence_count=1,
                description=pm.corrective_action,
            )
        else:
            rule = LearnedRule(
                rule_id=rule_id,
                created_at=trade.closed_at,
                strategy_name=trade.strategy_name,
                asset_class=trade.asset_class.value,
                regime=trade.regime_at_entry.value,
                mistake_category=pm.category.value,
                min_confidence_boost=round(min(0.12, prev.min_confidence_boost + conf_step), 4),
                min_edge_boost_bps=round(min(15.0, prev.min_edge_boost_bps + edge_step), 2),
                size_multiplier=round(max(0.40, prev.size_multiplier * 0.88), 3),
                require_tier1_or_tier2_news=require_t12,
                cooldown_bars=min(6, prev.cooldown_bars + 1),
                evidence_count=prev.evidence_count + 1,
                description=f"{pm.corrective_action} (reinforced across {prev.evidence_count + 1} incidents)",
            )

        self.storage.save_learned_rule(rule)
        return rule

    def build_learning_summary(self) -> dict[str, Any]:
        experts = [e.to_dict() for e in self.get_expert_states().values()]
        experts.sort(key=lambda x: x["weight"], reverse=True)
        postmortems = [p.to_dict() for p in self.storage.list_postmortems(limit=30)]
        rules = [r.to_dict() for r in self.storage.list_learned_rules()]

        category_counts: dict[str, int] = {}
        for pm in postmortems:
            cat = pm["category"]
            category_counts[cat] = category_counts.get(cat, 0) + 1

        avg_brier = (
            sum(e["ewma_brier_score"] for e in experts) / max(1, len(experts))
        )

        return {
            "average_ewma_brier_score": round(avg_brier, 4),
            "total_postmortems_recorded": len(postmortems),
            "active_learned_rules_count": len(rules),
            "mistake_breakdown_by_category": category_counts,
            "expert_weights": experts,
            "active_learned_rules": rules,
            "recent_postmortems": postmortems[:10],
            "safety_invariant": (
                "Autonomous learning can only tighten conviction/edge hurdles or reduce position sizing; "
                "loosening hard portfolio risk limits requires explicit human sign-off."
            ),
        }
