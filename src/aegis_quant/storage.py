"""SQLite persistence layer for portfolio ledger, news events, post-mortems, and self-learning state."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from aegis_quant.models import (
    AssetClass,
    ExpertState,
    LearnedRule,
    MarketRegime,
    MistakeCategory,
    MistakePostMortem,
    NewsEvent,
    OrderSide,
    Position,
    SourceTier,
    TradeRecord,
)


class AegisStorage:
    """Durable SQLite store for Aegis Quant telemetry, trades, and self-learning memory."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS portfolio_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                closed_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                asset_class TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                net_pnl_usd REAL NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS news_events (
                event_id TEXT PRIMARY KEY,
                dedup_fingerprint TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                source_tier INTEGER NOT NULL,
                contradicted INTEGER NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS expert_states (
                strategy_name TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mistake_postmortems (
                postmortem_id TEXT PRIMARY KEY,
                trade_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                category TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                asset_class TEXT NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS learned_rules (
                rule_id TEXT PRIMARY KEY,
                strategy_name TEXT NOT NULL,
                asset_class TEXT NOT NULL,
                regime TEXT NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS equity_history (
                step_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                equity_usd REAL NOT NULL,
                cash_usd REAL NOT NULL,
                drawdown_pct REAL NOT NULL,
                regime TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- Portfolio Key/Value State ---

    def set_state(self, key: str, value: Any) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO portfolio_state (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
        self._conn.commit()

    def get_state(self, key: str, default: Any = None) -> Any:
        row = self._conn.execute(
            "SELECT value FROM portfolio_state WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])

    # --- Positions & Trades ---

    def save_position(self, pos: Position) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO positions (symbol, payload) VALUES (?, ?)",
            (pos.symbol, json.dumps(pos.to_dict())),
        )
        self._conn.commit()

    def delete_position(self, symbol: str) -> None:
        self._conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
        self._conn.commit()

    def list_positions(self) -> list[Position]:
        rows = self._conn.execute("SELECT payload FROM positions ORDER BY symbol").fetchall()
        result: list[Position] = []
        for row in rows:
            d = json.loads(row["payload"])
            result.append(
                Position(
                    symbol=d["symbol"],
                    asset_class=AssetClass(d["asset_class"]),
                    side=OrderSide(d["side"]),
                    quantity=float(d["quantity"]),
                    entry_price=float(d["entry_price"]),
                    current_price=float(d["current_price"]),
                    stop_loss_price=float(d["stop_loss_price"]),
                    take_profit_price=float(d["take_profit_price"]),
                    notional_usd=float(d["notional_usd"]),
                    unrealized_pnl_usd=float(d["unrealized_pnl_usd"]),
                    accrued_costs_usd=float(d["accrued_costs_usd"]),
                    opened_at=d["opened_at"],
                    strategy_name=d["strategy_name"],
                    regime_at_entry=MarketRegime(d["regime_at_entry"]),
                    calibrated_prob_at_entry=float(d["calibrated_prob_at_entry"]),
                    bars_held=int(d.get("bars_held", 0)),
                    news_catalyzed=bool(d.get("news_catalyzed", False)),
                )
            )
        return result

    def save_trade(self, trade: TradeRecord) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO trades
            (trade_id, closed_at, symbol, asset_class, strategy_name, net_pnl_usd, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade.trade_id,
                trade.closed_at,
                trade.symbol,
                trade.asset_class.value,
                trade.strategy_name,
                trade.net_pnl_usd,
                json.dumps(trade.to_dict()),
            ),
        )
        self._conn.commit()

    def list_trades(self, limit: int = 100) -> list[TradeRecord]:
        rows = self._conn.execute(
            "SELECT payload FROM trades ORDER BY closed_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result: list[TradeRecord] = []
        for row in rows:
            d = json.loads(row["payload"])
            result.append(
                TradeRecord(
                    trade_id=d["trade_id"],
                    symbol=d["symbol"],
                    asset_class=AssetClass(d["asset_class"]),
                    side=OrderSide(d["side"]),
                    strategy_name=d["strategy_name"],
                    regime_at_entry=MarketRegime(d["regime_at_entry"]),
                    opened_at=d["opened_at"],
                    closed_at=d["closed_at"],
                    entry_price=float(d["entry_price"]),
                    exit_price=float(d["exit_price"]),
                    notional_usd=float(d["notional_usd"]),
                    gross_pnl_usd=float(d["gross_pnl_usd"]),
                    total_cost_usd=float(d["total_cost_usd"]),
                    net_pnl_usd=float(d["net_pnl_usd"]),
                    net_return_bps=float(d["net_return_bps"]),
                    calibrated_prob_at_entry=float(d["calibrated_prob_at_entry"]),
                    exit_reason=d["exit_reason"],
                    bars_held=int(d["bars_held"]),
                    news_catalyzed=bool(d.get("news_catalyzed", False)),
                )
            )
        return result

    def record_equity_point(
        self, timestamp: str, equity_usd: float, cash_usd: float, drawdown_pct: float, regime: str
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO equity_history (timestamp, equity_usd, cash_usd, drawdown_pct, regime)
            VALUES (?, ?, ?, ?, ?)
            """,
            (timestamp, equity_usd, cash_usd, drawdown_pct, regime),
        )
        self._conn.commit()

    def list_equity_history(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT step_id, timestamp, equity_usd, cash_usd, drawdown_pct, regime
            FROM equity_history
            ORDER BY step_id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # --- News Events ---

    def has_news_fingerprint(self, fingerprint: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM news_events WHERE dedup_fingerprint = ? LIMIT 1",
            (fingerprint,),
        ).fetchone()
        return row is not None

    def save_news_event(self, event: NewsEvent) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO news_events
            (event_id, dedup_fingerprint, timestamp, source_tier, contradicted, payload)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.dedup_fingerprint,
                event.timestamp,
                int(event.source_tier),
                1 if event.contradicted else 0,
                json.dumps(event.to_dict()),
            ),
        )
        self._conn.commit()

    def list_recent_news(self, limit: int = 50) -> list[NewsEvent]:
        rows = self._conn.execute(
            "SELECT payload FROM news_events ORDER BY timestamp DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        events: list[NewsEvent] = []
        for row in rows:
            d = json.loads(row["payload"])
            events.append(
                NewsEvent(
                    event_id=d["event_id"],
                    timestamp=d["timestamp"],
                    headline=d["headline"],
                    summary=d["summary"],
                    source_name=d["source_name"],
                    source_tier=SourceTier(int(d["source_tier"])),
                    affected_symbols=list(d["affected_symbols"]),
                    asset_classes=[AssetClass(ac) for ac in d["asset_classes"]],
                    sentiment_score=float(d["sentiment_score"]),
                    impact_magnitude_bps=float(d["impact_magnitude_bps"]),
                    credibility_weight=float(d["credibility_weight"]),
                    contradicted=bool(d["contradicted"]),
                    contradiction_note=d.get("contradiction_note", ""),
                    dedup_fingerprint=d.get("dedup_fingerprint", ""),
                    uncertainty_bps=float(d.get("uncertainty_bps", 15.0)),
                )
            )
        return events

    # --- Self-Learning Memory (Experts, Mistakes, Learned Rules) ---

    def save_expert_state(self, state: ExpertState) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO expert_states (strategy_name, payload) VALUES (?, ?)",
            (state.strategy_name, json.dumps(state.to_dict())),
        )
        self._conn.commit()

    def list_expert_states(self) -> dict[str, ExpertState]:
        rows = self._conn.execute("SELECT payload FROM expert_states").fetchall()
        out: dict[str, ExpertState] = {}
        for row in rows:
            d = json.loads(row["payload"])
            st = ExpertState(
                strategy_name=d["strategy_name"],
                weight=float(d["weight"]),
                alpha_wins=float(d["alpha_wins"]),
                beta_losses=float(d["beta_losses"]),
                ewma_brier_score=float(d["ewma_brier_score"]),
                ewma_net_return_bps=float(d["ewma_net_return_bps"]),
                total_predictions=int(d["total_predictions"]),
                calibration_slope=float(d.get("calibration_slope", 0.85)),
            )
            out[st.strategy_name] = st
        return out

    def save_postmortem(self, pm: MistakePostMortem) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO mistake_postmortems
            (postmortem_id, trade_id, timestamp, category, strategy_name, asset_class, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pm.postmortem_id,
                pm.trade_id,
                pm.timestamp,
                pm.category.value,
                pm.strategy_name,
                pm.asset_class.value,
                json.dumps(pm.to_dict()),
            ),
        )
        self._conn.commit()

    def list_postmortems(self, limit: int = 100) -> list[MistakePostMortem]:
        rows = self._conn.execute(
            "SELECT payload FROM mistake_postmortems ORDER BY timestamp DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result: list[MistakePostMortem] = []
        for row in rows:
            d = json.loads(row["payload"])
            result.append(
                MistakePostMortem(
                    postmortem_id=d["postmortem_id"],
                    trade_id=d["trade_id"],
                    timestamp=d["timestamp"],
                    symbol=d["symbol"],
                    asset_class=AssetClass(d["asset_class"]),
                    strategy_name=d["strategy_name"],
                    regime=MarketRegime(d["regime"]),
                    category=MistakeCategory(d["category"]),
                    severity=float(d["severity"]),
                    predicted_prob=float(d["predicted_prob"]),
                    realized_win=int(d["realized_win"]),
                    brier_error=float(d["brier_error"]),
                    gross_pnl_usd=float(d["gross_pnl_usd"]),
                    cost_usd=float(d["cost_usd"]),
                    net_pnl_usd=float(d["net_pnl_usd"]),
                    diagnosis=d["diagnosis"],
                    corrective_action=d["corrective_action"],
                )
            )
        return result

    def save_learned_rule(self, rule: LearnedRule) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO learned_rules
            (rule_id, strategy_name, asset_class, regime, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                rule.rule_id,
                rule.strategy_name,
                rule.asset_class,
                rule.regime,
                json.dumps(rule.to_dict()),
            ),
        )
        self._conn.commit()

    def list_learned_rules(self) -> list[LearnedRule]:
        rows = self._conn.execute(
            "SELECT payload FROM learned_rules ORDER BY rule_id"
        ).fetchall()
        return [LearnedRule(**json.loads(r["payload"])) for r in rows]
