"""Comprehensive unit and integration test suite for Aegis Quant."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from aegis_quant.backtester import WalkForwardBacktester
from aegis_quant.cli import main as cli_main
from aegis_quant.engine import AutonomousQuantEngine
from aegis_quant.models import (
    DEFAULT_UNIVERSE,
    AssetClass,
    MarketBar,
    MarketRegime,
    MistakeCategory,
    NewsEvent,
    OrderSide,
    SourceTier,
    StrategySignal,
)
from aegis_quant.news_intelligence import (
     NewsIntelligenceEngine,
    compute_dedup_fingerprint,
)
from aegis_quant.server import AegisDashboardServer
from aegis_quant.storage import AegisStorage


class TestAegisQuant(unittest.TestCase):
    def test_news_deduplication_rumor_filtering_and_contradiction_detection(self) -> None:
        storage = AegisStorage(":memory:")
        news_eng = NewsIntelligenceEngine(storage)

        ev1 = news_eng.parse_headline_into_event(
            "Fed signals dovish cut after cooling inflation report",
            source_name="FederalReserveWire",
            source_tier=SourceTier.TIER_1_OFFICIAL,
        )
        # Near-identical syndicated headline should produce same fingerprint and be deduplicated
        ev1_dup = news_eng.parse_headline_into_event(
            "BREAKING: Fed signals dovish cut after cooling inflation report!",
            source_name="SecondaryWire",
            source_tier=SourceTier.TIER_2_WIRE,
        )
        self.assertEqual(
            compute_dedup_fingerprint(ev1.headline),
            compute_dedup_fingerprint(ev1_dup.headline),
        )

        # Opposing headline on overlapping symbols should be flagged as contradicted
        ev_conflict = NewsEvent(
            event_id="news-conflict-1",
            timestamp=ev1.timestamp,
            headline="Fed signals hawkish hike after hot CPI surprise",
            summary="Conflicting macro flash",
            source_name="FastMedia",
            source_tier=SourceTier.TIER_3_MEDIA,
            affected_symbols=["SPY", "EUR/USD"],
            asset_classes=[AssetClass.EQUITY, AssetClass.FOREX],
            sentiment_score=-0.75,
            impact_magnitude_bps=25.0,
            credibility_weight=0.62,
        )

        # Tier 4 rumor should be flagged and ignored in signal aggregation
        ev_rumor = NewsEvent(
            event_id="news-rumor-1",
            timestamp=ev1.timestamp,
            headline="Unverified social post claims huge EUR/USD emergency move",
            summary="Anon rumor",
            source_name="SocialFeed",
            source_tier=SourceTier.TIER_4_RUMOR,
            affected_symbols=["EUR/USD"],
            asset_classes=[AssetClass.FOREX],
            sentiment_score=0.95,
            impact_magnitude_bps=80.0,
            credibility_weight=0.18,
        )

        stats = news_eng.ingest_batch([ev1, ev1_dup, ev_conflict, ev_rumor])
        self.assertEqual(stats["accepted_count"], 3)
        self.assertEqual(stats["duplicates_dropped"], 1)
        self.assertEqual(stats["rumors_flagged"], 1)
        self.assertEqual(stats["contradictions_detected"], 1)

        sig = news_eng.aggregate_symbol_news_signal("EUR/USD", require_high_tier=True)
        self.assertGreaterEqual(sig["rumors_ignored"], 1)
        storage.close()

    def test_cross_asset_regime_allocator_and_cash_fallback(self) -> None:
        engine = AutonomousQuantEngine(db_path=":memory:", seed=42)
        res = engine.step_cycle()
        advice = res["allocation_advice"]
        self.assertIn("regime", advice)
        self.assertEqual(len(advice["buckets"]), 4)
        total_weight = sum(b["recommended_weight_pct"] for b in advice["buckets"])
        self.assertAlmostEqual(total_weight, 100.0, places=1)
        engine.storage.close()

    def test_non_bypassable_risk_guard_and_kill_switch(self) -> None:
        engine = AutonomousQuantEngine(db_path=":memory:", seed=7)
        engine.step_cycle()
        bar = engine.latest_bars["EUR/USD"]

        # 1. Low net-edge signal must be rejected
        weak_sig = StrategySignal(
            strategy_name="MacroNewsCatalyst",
            symbol="EUR/USD",
            asset_class=AssetClass.FOREX,
            side=OrderSide.BUY,
            raw_confidence=0.52,
            calibrated_probability=0.51,
            expected_gross_bps=4.0,
            expected_cost_bps=2.5,
            expected_net_edge_bps=1.5,
            stop_loss_bps=25.0,
            take_profit_bps=40.0,
            rationale="Weak test signal",
            invalidation_criteria="None",
        )
        dec_weak = engine.risk_guard.evaluate_signal(
            signal=weak_sig,
            bar=bar,
            regime=MarketRegime.RISK_ON_GROWTH,
            equity_usd=100000.0,
            cash_usd=100000.0,
            current_drawdown_pct=0.0,
            daily_drawdown_pct=0.0,
            open_positions=[],
            learned_rules=[],
        )
        self.assertFalse(dec_weak.approved)
        self.assertTrue(any("below hurdle" in r or "below cost-adjusted" in r for r in dec_weak.rejection_reasons))

        # 2. Stale market data must be rejected
        stale_bar = MarketBar(**{**bar.to_dict(), "stale_seconds": 95.0})
        strong_sig = StrategySignal(
            strategy_name="MacroNewsCatalyst",
            symbol="EUR/USD",
            asset_class=AssetClass.FOREX,
            side=OrderSide.BUY,
            raw_confidence=0.75,
            calibrated_probability=0.72,
            expected_gross_bps=28.0,
            expected_cost_bps=3.0,
            expected_net_edge_bps=25.0,
            stop_loss_bps=25.0,
            take_profit_bps=50.0,
            rationale="Strong test signal",
            invalidation_criteria="None",
        )
        dec_stale = engine.risk_guard.evaluate_signal(
            signal=strong_sig,
            bar=stale_bar,
            regime=MarketRegime.RISK_ON_GROWTH,
            equity_usd=100000.0,
            cash_usd=100000.0,
            current_drawdown_pct=0.0,
            daily_drawdown_pct=0.0,
            open_positions=[],
            learned_rules=[],
        )
        self.assertFalse(dec_stale.approved)
        self.assertTrue(any("stale" in r for r in dec_stale.rejection_reasons))

        # 3. Engaging kill switch blocks new orders and flattens open positions
        ks_res = engine.set_kill_switch(True)
        self.assertTrue(ks_res["kill_switch_active"])
        self.assertEqual(ks_res["portfolio"]["open_positions_count"], 0)
        engine.storage.close()

    def test_self_learning_postmortem_and_one_way_guardrail_tightening(self) -> None:
        engine = AutonomousQuantEngine(db_path=":memory:", seed=42)
        engine.step_cycle()

        # Inject a COST_EROSION mistake (positive gross PnL, negative net PnL due to FX spreads/swaps)
        pm1, rule1 = engine.learner.inject_synthetic_mistake(
            timestamp="2026-10-27T12:00:00+00:00",
            symbol="EUR/USD",
            asset_class_str="FOREX",
            strategy_name="MacroNewsCatalyst",
            regime_str="HIGH_DISPERSION_NEUTRAL",
            category_str=MistakeCategory.COST_EROSION.value,
            gross_pnl_usd=22.0,
            cost_usd=45.0,
            predicted_prob=0.74,
        )
        self.assertEqual(pm1.category, MistakeCategory.COST_EROSION)
        self.assertGreater(rule1.min_edge_boost_bps, 0.0)
        self.assertLess(rule1.size_multiplier, 1.0)

        # Reinforce with a second mistake on the same tuple and verify rule tightens further
        _, rule2 = engine.learner.inject_synthetic_mistake(
            timestamp="2026-10-27T13:00:00+00:00",
            symbol="EUR/USD",
            asset_class_str="FOREX",
            strategy_name="MacroNewsCatalyst",
            regime_str="HIGH_DISPERSION_NEUTRAL",
            category_str=MistakeCategory.NEWS_WHIPSAW.value,
            gross_pnl_usd=-30.0,
            cost_usd=12.0,
            predicted_prob=0.78,
        )
        self.assertEqual(rule2.evidence_count, 2)
        self.assertGreater(rule2.min_edge_boost_bps, rule1.min_edge_boost_bps)
        self.assertLess(rule2.size_multiplier, rule1.size_multiplier)
        self.assertTrue(rule2.require_tier1_or_tier2_news)
        engine.storage.close()

    def test_walk_forward_backtester_outperforms_naive_overtrading_bot(self) -> None:
        bt = WalkForwardBacktester(seed=42, initial_equity_usd=100000.0).run_comparison(bars_count=60)
        aegis_final = bt["aegis_self_learning"]["final_equity_usd"]
        naive_final = bt["unfiltered_naive_bot"]["final_equity_usd"]
        self.assertGreater(aegis_final, naive_final)
        self.assertLessEqual(bt["aegis_self_learning"]["max_drawdown_pct"], 6.0)

    def test_exact_pnl_and_cost_reconciliation_for_long_and_short(self) -> None:
        engine = AutonomousQuantEngine(db_path=":memory:", seed=42)
        engine.run_cycles(count=25)
        trades = engine.storage.list_trades(limit=100)
        self.assertGreater(len(trades), 0)
        for t in trades:
            self.assertAlmostEqual(
                t.net_pnl_usd,
                round(t.gross_pnl_usd - t.total_cost_usd, 2),
                places=2,
            )
        engine.storage.close()

    def test_cli_and_http_dashboard_endpoints(self) -> None:
        self.assertEqual(cli_main(["advise", "--json"]), 0)

        engine = AutonomousQuantEngine(db_path=":memory:", seed=42)
        engine.run_cycles(count=3)
        srv = AegisDashboardServer(engine=engine, host="127.0.0.1", port=0)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv._make_handler())
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertIn("portfolio", data)
                self.assertIn("allocation_advice", data)
                self.assertIn("learning", data)

            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/simulate-mistake",
                data=json.dumps({"category": "COST_EROSION"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                pm_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(pm_data["postmortem"]["category"], "COST_EROSION")
        finally:
            httpd.shutdown()
            httpd.server_close()
            engine.storage.close()

    def test_trading212_broker_client_and_autonomous_daemon(self) -> None:
        from aegis_quant.brokers import BrokerEnvironment, Trading212BrokerClient

        engine = AutonomousQuantEngine(db_path=":memory:", seed=42)
        res = engine.step_cycle(prefer_live_feeds=False, route_to_trading212=True)
        self.assertIn("trading212_receipts", res)
        self.assertGreater(len(res["trading212_receipts"]), 0)
        first_receipt = res["trading212_receipts"][0]
        self.assertEqual(first_receipt["status"], "DRY_RUN_READY")
        self.assertTrue(first_receipt["broker_ticker"].endswith("_US_EQ"))

        # Live environment must block real-money orders unless AEGIS_ALLOW_LIVE_TRADING is explicitly set
        live_client = Trading212BrokerClient(
            api_key="test-key",
            environment=BrokerEnvironment.TRADING212_LIVE,
        )
        with self.assertRaises(PermissionError):
            live_client.place_market_order("MSFT_US_EQ", 1.5)

        # CLI t212-sync and daemon smoke test
        self.assertEqual(cli_main(["t212-sync", "--env", "demo", "--json"]), 0)
        self.assertEqual(cli_main(["daemon", "--interval", "0.05", "--max-cycles", "2", "--t212", "--json"]), 0)
        engine.storage.close()


if __name__ == "__main__":
    unittest.main()
