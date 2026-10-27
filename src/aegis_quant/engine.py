"""Main autonomous orchestration loop connecting ingestion, allocation, risk guard, paper broker, and self-learning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aegis_quant.brokers import BrokerEnvironment, ExternalOrderReceipt, Trading212BrokerClient
from aegis_quant.live_feeds import LiveMarketAndNewsCollector
from aegis_quant.models import (
    DEFAULT_UNIVERSE,
    AllocationAdvice,
    Instrument,
    MarketBar,
    RiskConfig,
    RiskDecision,
)
from aegis_quant.news_intelligence import GlobalFeedSimulator, NewsIntelligenceEngine
from aegis_quant.paper_broker import PaperBroker
from aegis_quant.regime_allocator import CrossAssetRegimeAllocator
from aegis_quant.risk_guard import IndependentRiskGuard
from aegis_quant.self_learning import SelfLearningController
from aegis_quant.storage import AegisStorage


class AutonomousQuantEngine:
    """End-to-end multi-asset market intelligence, paper execution, and self-learning engine."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        seed: int = 42,
        initial_equity_usd: float = 100000.0,
        risk_config: RiskConfig | None = None,
        universe: dict[str, Instrument] | None = None,
    ) -> None:
        self.universe = universe or DEFAULT_UNIVERSE
        self.storage = AegisStorage(db_path=db_path)
        self.simulator = GlobalFeedSimulator(seed=seed, universe=self.universe)
        self.news_engine = NewsIntelligenceEngine(storage=self.storage)
        self.allocator = CrossAssetRegimeAllocator(
            news_engine=self.news_engine, universe=self.universe
        )
        self.risk_guard = IndependentRiskGuard(
            config=risk_config or RiskConfig(), universe=self.universe
        )
        self.broker = PaperBroker(
            storage=self.storage,
            initial_equity_usd=initial_equity_usd,
            universe=self.universe,
        )
        self.learner = SelfLearningController(storage=self.storage)
        self.live_collector = LiveMarketAndNewsCollector(
            news_engine=self.news_engine,
            simulator=self.simulator,
            universe=self.universe,
        )
        self.t212_client = Trading212BrokerClient(
            environment=BrokerEnvironment.TRADING212_DEMO
        )
        self.latest_bars: dict[str, MarketBar] = {}
        self.latest_advice: AllocationAdvice | None = None
        self.latest_decisions: list[RiskDecision] = []
        self.latest_t212_receipts: list[ExternalOrderReceipt] = []

    def step_cycle(
        self,
        prefer_live_feeds: bool = False,
        route_to_trading212: bool = False,
    ) -> dict[str, Any]:
        """Run one complete autonomous perception -> exit -> post-mortem -> allocation -> risk -> trade cycle."""
        if prefer_live_feeds:
            tick = self.live_collector.collect_tick(prefer_live=True)
            ts = tick["timestamp"]
            true_regime = self.simulator.current_regime()
            bars = tick["bars"]
            raw_news = tick["news_events"]
            feed_mode = tick["mode"]
        else:
            ts, true_regime, bars, raw_news = self.simulator.next_tick()
            feed_mode = "SIMULATED"
        self.latest_bars = bars

        # 1. Ingest and verify incoming world news
        news_stats = self.news_engine.ingest_batch(raw_news)

        # 2. Mark existing positions to market and run bracket exits
        force_reason = "KILL_SWITCH_FLATTEN" if self.risk_guard.manual_kill_switch else None
        closed_trades = self.broker.mark_to_market_and_check_exits(
            ts, bars, force_flatten_reason=force_reason
        )

        # 2b. If automatic daily or total drawdown circuit breaker is breached, flatten remaining positions
        post_mark_summary = self.broker.compute_portfolio_summary()
        if (
            post_mark_summary["daily_drawdown_pct"] >= self.risk_guard.config.max_daily_drawdown_pct
            or post_mark_summary["total_drawdown_pct"] >= self.risk_guard.config.max_total_drawdown_pct
        ) and post_mark_summary["open_positions_count"] > 0:
            cb_trades = self.broker.mark_to_market_and_check_exits(
                ts, bars, force_flatten_reason="CIRCUIT_BREAKER_FLATTEN"
            )
            closed_trades.extend(cb_trades)

        # 3. Self-learning & automated post-mortems on closed trades
        new_postmortems = []
        for tr in closed_trades:
            pm = self.learner.process_closed_trade(tr)
            if pm is not None:
                new_postmortems.append(pm.to_dict())

        # 4. Build cross-asset allocation advice (Equities vs Forex vs Crypto vs Cash)
        expert_states = self.learner.get_expert_states()
        learned_rules = self.storage.list_learned_rules()
        advice = self.allocator.build_allocation_advice(
            timestamp=ts,
            bars=bars,
            expert_states=expert_states,
            learned_rules=learned_rules,
            min_edge_hurdle_bps=self.risk_guard.config.min_net_edge_bps,
        )
        self.latest_advice = advice

        # 5. Evaluate top actionable signals through IndependentRiskGuard & execute in PaperBroker (+ optional Trading 212)
        portfolio = self.broker.compute_portfolio_summary()
        decisions: list[RiskDecision] = []
        opened_positions = []
        t212_receipts: list[ExternalOrderReceipt] = []

        for sig in advice.actionable_signals[:3]:
            bar = bars[sig.symbol]
            open_pos = self.storage.list_positions()
            portfolio = self.broker.compute_portfolio_summary()
            decision = self.risk_guard.evaluate_signal(
                signal=sig,
                bar=bar,
                regime=advice.regime,
                equity_usd=portfolio["equity_usd"],
                cash_usd=portfolio["cash_usd"],
                current_drawdown_pct=portfolio["total_drawdown_pct"],
                daily_drawdown_pct=portfolio["daily_drawdown_pct"],
                open_positions=open_pos,
                learned_rules=learned_rules,
            )
            decisions.append(decision)
            if decision.approved:
                pos = self.broker.execute_approved_order(
                    timestamp=ts,
                    signal=sig,
                    decision=decision,
                    bar=bar,
                    regime=advice.regime,
                )
                if pos is not None:
                    opened_positions.append(pos.to_dict())
                if route_to_trading212:
                    receipt = self.t212_client.execute_risk_approved_signal(
                        signal=sig,
                        decision=decision,
                        reference_price=bar.mid_price,
                        dry_run_if_unconfigured=True,
                    )
                    t212_receipts.append(receipt)

        self.latest_decisions = decisions
        self.latest_t212_receipts = t212_receipts
        final_portfolio = self.broker.compute_portfolio_summary()
        self.storage.record_equity_point(
            timestamp=ts,
            equity_usd=final_portfolio["equity_usd"],
            cash_usd=final_portfolio["cash_usd"],
            drawdown_pct=final_portfolio["total_drawdown_pct"],
            regime=advice.regime.value,
        )

        return {
            "timestamp": ts,
            "feed_mode": feed_mode,
            "simulated_regime": true_regime.value,
            "detected_regime": advice.regime.value,
            "news_ingestion": {
                "accepted": news_stats["accepted_count"],
                "duplicates_dropped": news_stats["duplicates_dropped"],
                "rumors_flagged": news_stats["rumors_flagged"],
                "contradictions_detected": news_stats["contradictions_detected"],
            },
            "closed_trades": [t.to_dict() for t in closed_trades],
            "new_postmortems": new_postmortems,
            "opened_positions": opened_positions,
            "trading212_receipts": [r.to_dict() for r in t212_receipts],
            "risk_decisions": [d.to_dict() for d in decisions],
            "allocation_advice": advice.to_dict(),
            "portfolio": final_portfolio,
        }

    def run_cycles(self, count: int = 10) -> list[dict[str, Any]]:
        return [self.step_cycle() for _ in range(max(1, count))]

    def set_kill_switch(self, active: bool) -> dict[str, Any]:
        """Engage or reset the manual kill switch. When engaged, flattens all open paper positions."""
        self.risk_guard.manual_kill_switch = active
        flattened = []
        if active and self.latest_bars:
            ts = next(iter(self.latest_bars.values())).timestamp
            trades = self.broker.mark_to_market_and_check_exits(
                ts, self.latest_bars, force_flatten_reason="MANUAL_KILL_SWITCH"
            )
            flattened = [t.to_dict() for t in trades]
        return {
            "kill_switch_active": self.risk_guard.manual_kill_switch,
            "flattened_trades": flattened,
            "portfolio": self.broker.compute_portfolio_summary(),
        }

    def full_status_snapshot(self) -> dict[str, Any]:
        if not self.latest_bars:
            self.step_cycle()
        return {
            "portfolio": self.broker.compute_portfolio_summary(),
            "allocation_advice": self.latest_advice.to_dict() if self.latest_advice else None,
            "market_bars": {k: v.to_dict() for k, v in self.latest_bars.items()},
            "recent_news": [n.to_dict() for n in self.storage.list_recent_news(limit=20)],
            "recent_trades": [t.to_dict() for t in self.storage.list_trades(limit=25)],
            "learning": self.learner.build_learning_summary(),
            "equity_history": self.storage.list_equity_history(limit=120),
            "kill_switch_active": self.risk_guard.manual_kill_switch,
            "latest_risk_decisions": [d.to_dict() for d in self.latest_decisions],
        }
