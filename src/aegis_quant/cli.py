"""Command-line interface for Aegis Quant."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from aegis_quant.backtester import WalkForwardBacktester
from aegis_quant.engine import AutonomousQuantEngine
from aegis_quant.models import SourceTier
from aegis_quant.server import AegisDashboardServer


def _print_output(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return

    if "primary_verdict" in payload:
        print("=" * 84)
        print(f"AEGIS QUANT // CROSS-ASSET ALLOCATION VERDICT ({payload['regime']})")
        print("=" * 84)
        print(f"Verdict: {payload['primary_verdict']}")
        print(f"Do Nothing / Hold Cash Triggered: {payload['do_nothing_recommended']}")
        print("-" * 84)
        for b in payload["buckets"]:
            print(
                f"  [{b['asset_class']:<7}] Weight: {b['recommended_weight_pct']:>5.1f}% | "
                f"Net Edge: {b['expected_net_edge_bps']:>+6.1f} bps | "
                f"Vol: {b['annualized_vol_pct']:>4.1f}% | Top: {', '.join(b['top_instruments'])}"
            )
            print(f"            Why:  {b['rationale']}")
            print(f"            Risk: {b['key_risks']}")
        print("-" * 84)
        print(f"Counter-Thesis: {payload['counter_thesis']}")
        return

    if "aegis_self_learning" in payload:
        a = payload["aegis_self_learning"]
        b = payload["passive_60_40_benchmark"]
        n = payload["unfiltered_naive_bot"]
        print("=" * 84)
        print(f"AEGIS QUANT // WALK-FORWARD OUT-OF-SAMPLE BACKTEST ({payload['bars_evaluated']} BARS)")
        print("=" * 84)
        print(
            f"1. Aegis Self-Learning : Final=${a['final_equity_usd']:,.2f} ({a['total_return_pct']:+.2f}%) | "
            f"MaxDD={a['max_drawdown_pct']:.2f}% | Sharpe={a['sharpe_ratio']:.2f} | "
            f"Rules Learned={a['learned_rules_generated']}"
        )
        print(
            f"2. 60/40 Core Benchmark: Final=${b['final_equity_usd']:,.2f} ({b['total_return_pct']:+.2f}%) | "
            f"MaxDD={b['max_drawdown_pct']:.2f}% | Sharpe={b['sharpe_ratio']:.2f}"
        )
        print(
            f"3. Naive News Bot      : Final=${n['final_equity_usd']:,.2f} ({n['total_return_pct']:+.2f}%) | "
            f"MaxDD={n['max_drawdown_pct']:.2f}% | Costs=${n['total_transaction_costs_usd']:,.2f}"
        )
        print("-" * 84)
        print(f"Takeaway: {payload['key_takeaway']}")
        return

    print(json.dumps(payload, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aegis-quant",
        description="Autonomous multi-asset market intelligence, paper trading, and self-correcting learning engine.",
    )
    parser.add_argument(
        "--db",
        default=":memory:",
        help="Path to SQLite state database (default: :memory:)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic simulation seed (default: 42)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_adv = sub.add_parser(
        "advise",
        help="Evaluate world news & market regime and recommend Companies vs Forex vs Crypto vs Cash.",
    )
    p_adv.add_argument("--json", action="store_true", help="Emit JSON output")

    p_cyc = sub.add_parser(
        "cycle",
        help="Run N autonomous perception, risk-guard, paper-execution, and self-learning cycles.",
    )
    p_cyc.add_argument("--cycles", type=int, default=12, help="Number of cycles to run")
    p_cyc.add_argument("--json", action="store_true", help="Emit JSON output")

    p_bt = sub.add_parser(
        "backtest",
        help="Run walk-forward comparison against 60/40 benchmark and naive overtrading bot.",
    )
    p_bt.add_argument("--bars", type=int, default=90, help="Number of chronological bars")
    p_bt.add_argument("--json", action="store_true", help="Emit JSON output")

    p_lrn = sub.add_parser(
        "learn-report",
        help="Inspect self-learning strategy weights, Brier calibration, mistake post-mortems, and learned rules.",
    )
    p_lrn.add_argument("--cycles", type=int, default=25, help="Warmup cycles before report")
    p_lrn.add_argument("--json", action="store_true", help="Emit JSON output")

    p_hl = sub.add_parser(
        "ingest-headline",
        help="Parse, deduplicate, and score a custom global news headline.",
    )
    p_hl.add_argument("headline", help="News headline text")
    p_hl.add_argument(
        "--tier",
        type=int,
        choices=[1, 2, 3, 4],
        default=2,
        help="Source credibility tier (1=Official, 2=Wire, 3=Media, 4=Rumor)",
    )
    p_hl.add_argument("--json", action="store_true", help="Emit JSON output")

    p_t212 = sub.add_parser(
        "t212-sync",
        help="Run autonomous cycle and route approved Equity/ETF orders to Trading 212 Demo/Live API.",
    )
    p_t212.add_argument(
        "--env",
        choices=["demo", "live"],
        default="demo",
        help="Trading 212 environment: demo (https://demo.trading212.com) or live",
    )
    p_t212.add_argument("--live-feeds", action="store_true", help="Attempt live market & RSS feeds")
    p_t212.add_argument("--json", action="store_true", help="Emit JSON output")

    p_dmn = sub.add_parser(
        "daemon",
        help="Run continuous self-managing autonomous loop (news + quotes + risk guard + Trading 212 / paper execution + self-learning).",
    )
    p_dmn.add_argument("--interval", type=float, default=15.0, help="Seconds between autonomous cycles")
    p_dmn.add_argument("--max-cycles", type=int, default=5, help="Max cycles to run (0 = infinite 24/7 loop)")
    p_dmn.add_argument("--live-feeds", action="store_true", help="Use live RSS & market quote feeds with fallback")
    p_dmn.add_argument("--t212", action="store_true", help="Route approved Equity/ETF orders to Trading 212")
    p_dmn.add_argument("--json", action="store_true", help="Emit JSON summary")

    p_srv = sub.add_parser(
        "serve",
        help="Launch the interactive Web Control Center & REST API server.",
    )
    p_srv.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    p_srv.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    p_srv.add_argument(
        "--auto-interval",
        type=float,
        default=0.0,
        help="Optional background autonomous tick interval in seconds (0=manual/UI driven)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "backtest":
        bt = WalkForwardBacktester(seed=args.seed).run_comparison(bars_count=args.bars)
        _print_output(bt, as_json=args.json)
        return 0

    engine = AutonomousQuantEngine(db_path=args.db, seed=args.seed)

    if args.command == "advise":
        engine.run_cycles(count=3)
        assert engine.latest_advice is not None
        _print_output(engine.latest_advice.to_dict(), as_json=args.json)
        return 0

    if args.command == "cycle":
        results = engine.run_cycles(count=args.cycles)
        summary = {
            "cycles_completed": len(results),
            "final_portfolio": results[-1]["portfolio"],
            "latest_regime": results[-1]["detected_regime"],
            "latest_verdict": results[-1]["allocation_advice"]["primary_verdict"],
            "learning_summary": engine.learner.build_learning_summary(),
        }
        _print_output(summary, as_json=args.json)
        return 0

    if args.command == "learn-report":
        engine.run_cycles(count=args.cycles)
        # Ensure at least one explicit cost-erosion and news-whipsaw post-mortem is demonstrated if none occurred
        if engine.learner.build_learning_summary()["total_postmortems_recorded"] == 0:
            engine.learner.inject_synthetic_mistake(
                timestamp="2026-10-27T12:00:00+00:00",
                symbol="EUR/USD",
                asset_class_str="FOREX",
                strategy_name="MacroNewsCatalyst",
                regime_str="HIGH_DISPERSION_NEUTRAL",
                category_str="COST_EROSION",
            )
        report = engine.learner.build_learning_summary()
        _print_output(report, as_json=args.json)
        return 0

    if args.command == "ingest-headline":
        engine.step_cycle()
        ev = engine.news_engine.parse_headline_into_event(
            headline=args.headline,
            source_tier=SourceTier(args.tier),
        )
        ing = engine.news_engine.ingest_batch([ev])
        _print_output(
            {
                "event": ev.to_dict(),
                "ingestion_result": {
                    "accepted_count": ing["accepted_count"],
                    "duplicates_dropped": ing["duplicates_dropped"],
                    "rumors_flagged": ing["rumors_flagged"],
                    "contradictions_detected": ing["contradictions_detected"],
                },
            },
            as_json=args.json,
        )
        return 0

    if args.command == "t212-sync":
        from aegis_quant.brokers import BrokerEnvironment

        engine.t212_client.environment = (
            BrokerEnvironment.TRADING212_LIVE
            if args.env == "live"
            else BrokerEnvironment.TRADING212_DEMO
        )
        res = engine.step_cycle(
            prefer_live_feeds=args.live_feeds,
            route_to_trading212=True,
        )
        _print_output(
            {
                "trading212_environment": engine.t212_client.environment.value,
                "trading212_base_url": engine.t212_client.base_url,
                "api_key_configured": engine.t212_client.is_configured(),
                "feed_mode": res["feed_mode"],
                "detected_regime": res["detected_regime"],
                "primary_verdict": res["allocation_advice"]["primary_verdict"],
                "trading212_order_receipts": res["trading212_receipts"],
                "portfolio": res["portfolio"],
            },
            as_json=args.json,
        )
        return 0

    if args.command == "daemon":
        import time

        target_cycles = args.max_cycles
        completed = 0
        last_res: dict[str, Any] = {}
        while target_cycles == 0 or completed < target_cycles:
            last_res = engine.step_cycle(
                prefer_live_feeds=args.live_feeds,
                route_to_trading212=args.t212,
            )
            completed += 1
            if target_cycles == 0 or completed < target_cycles:
                time.sleep(max(0.05, args.interval))

        _print_output(
            {
                "daemon_cycles_completed": completed,
                "feed_mode": last_res.get("feed_mode", "SIMULATED"),
                "latest_regime": last_res.get("detected_regime"),
                "latest_verdict": last_res.get("allocation_advice", {}).get("primary_verdict"),
                "trading212_receipts": last_res.get("trading212_receipts", []),
                "portfolio": last_res.get("portfolio"),
                "learning_summary": engine.learner.build_learning_summary(),
            },
            as_json=args.json,
        )
        return 0

    if args.command == "serve":
        interval = args.auto_interval if args.auto_interval > 0 else None
        srv = AegisDashboardServer(
            engine=engine,
            host=args.host,
            port=args.port,
            background_interval_sec=interval,
        )
        print(f"Aegis Quant Control Center listening on http://{args.host}:{args.port}")
        srv.serve_forever()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
