"""Multi-asset paper broker with realistic spread, slippage, commission, and swap cost simulation."""

from __future__ import annotations

from typing import Any

from aegis_quant.models import (
    DEFAULT_UNIVERSE,
    Instrument,
    MarketBar,
    MarketRegime,
    OrderSide,
    Position,
    RiskDecision,
    StrategySignal,
    TradeRecord,
)
from aegis_quant.storage import AegisStorage


class PaperBroker:
    """Executes approved paper trades, marks positions to market, and enforces bracket exits."""

    def __init__(
        self,
        storage: AegisStorage,
        initial_equity_usd: float = 100000.0,
        universe: dict[str, Instrument] | None = None,
        max_holding_bars: int = 8,
    ) -> None:
        self.storage = storage
        self.universe = universe or DEFAULT_UNIVERSE
        self.max_holding_bars = max_holding_bars

        if self.storage.get_state("cash_usd") is None:
            self.storage.set_state("initial_equity_usd", initial_equity_usd)
            self.storage.set_state("cash_usd", initial_equity_usd)
            self.storage.set_state("peak_equity_usd", initial_equity_usd)
            self.storage.set_state("day_start_equity_usd", initial_equity_usd)
            self.storage.set_state("current_trading_day", "")
            self.storage.set_state("trade_counter", 0)

    @property
    def cash_usd(self) -> float:
        return float(self.storage.get_state("cash_usd", 100000.0))

    @property
    def peak_equity_usd(self) -> float:
        return float(self.storage.get_state("peak_equity_usd", 100000.0))

    @property
    def day_start_equity_usd(self) -> float:
        return float(self.storage.get_state("day_start_equity_usd", 100000.0))

    def _margin_for_position(self, pos: Position) -> float:
        inst = self.universe[pos.symbol]
        return pos.notional_usd / max(1.0, inst.max_leverage)

    def compute_portfolio_summary(self) -> dict[str, Any]:
        positions = self.storage.list_positions()
        cash = self.cash_usd
        unrealized = sum(p.unrealized_pnl_usd for p in positions)
        margin_locked = sum(self._margin_for_position(p) for p in positions)
        open_notional = sum(p.notional_usd for p in positions)
        equity = cash + margin_locked + unrealized
        peak = max(self.peak_equity_usd, equity)
        if peak > self.peak_equity_usd:
            self.storage.set_state("peak_equity_usd", peak)

        total_dd_pct = max(0.0, ((peak - equity) / max(1.0, peak)) * 100.0)
        day_start = self.day_start_equity_usd
        daily_dd_pct = max(0.0, ((day_start - equity) / max(1.0, day_start)) * 100.0)

        return {
            "equity_usd": round(equity, 2),
            "cash_usd": round(cash, 2),
            "margin_locked_usd": round(margin_locked, 2),
            "open_notional_usd": round(open_notional, 2),
            "unrealized_pnl_usd": round(unrealized, 2),
            "peak_equity_usd": round(peak, 2),
            "total_drawdown_pct": round(total_dd_pct, 3),
            "daily_drawdown_pct": round(daily_dd_pct, 3),
            "open_positions_count": len(positions),
            "positions": [p.to_dict() for p in positions],
        }

    def mark_to_market_and_check_exits(
        self, timestamp: str, bars: dict[str, MarketBar], force_flatten_reason: str | None = None
    ) -> list[TradeRecord]:
        """Update all open positions with latest bar prices and execute stop/target/horizon exits."""
        trading_day = timestamp[:10]
        last_day = str(self.storage.get_state("current_trading_day", ""))
        if trading_day and trading_day != last_day:
            curr_eq = self.compute_portfolio_summary()["equity_usd"]
            self.storage.set_state("day_start_equity_usd", curr_eq)
            self.storage.set_state("current_trading_day", trading_day)

        closed_trades: list[TradeRecord] = []
        positions = self.storage.list_positions()

        for pos in positions:
            bar = bars.get(pos.symbol)
            if bar is None:
                continue
            inst = self.universe[pos.symbol]
            pos.bars_held += 1

            # Accrue per-bar overnight financing/swap cost
            swap_cost = pos.notional_usd * (max(0.0, inst.overnight_swap_bps) / 10000.0) * (1.0 / 6.0)
            pos.accrued_costs_usd = round(pos.accrued_costs_usd + swap_cost, 4)

            mark_price = bar.mid_price
            pos.current_price = mark_price

            if pos.side == OrderSide.BUY:
                gross_pnl = (mark_price - pos.entry_price) * pos.quantity
                hit_stop = bar.bid_price <= pos.stop_loss_price
                hit_tp = bar.bid_price >= pos.take_profit_price
            else:
                gross_pnl = (pos.entry_price - mark_price) * pos.quantity
                hit_stop = bar.ask_price >= pos.stop_loss_price
                hit_tp = bar.ask_price <= pos.take_profit_price

            pos.unrealized_pnl_usd = round(gross_pnl - pos.accrued_costs_usd, 2)

            exit_reason: str | None = None
            if force_flatten_reason:
                exit_reason = force_flatten_reason
            elif hit_stop:
                exit_reason = "STOP_LOSS"
            elif hit_tp:
                exit_reason = "TAKE_PROFIT"
            elif pos.bars_held >= self.max_holding_bars:
                exit_reason = "TIME_HORIZON"

            if exit_reason is not None:
                trade = self._close_position(pos, bar, timestamp, exit_reason)
                closed_trades.append(trade)
            else:
                self.storage.save_position(pos)

        return closed_trades

    def execute_approved_order(
        self,
        timestamp: str,
        signal: StrategySignal,
        decision: RiskDecision,
        bar: MarketBar,
        regime: MarketRegime,
    ) -> Position | None:
        """Open a paper position with explicit mid-price gross accounting and exact cost tracking."""
        if not decision.approved or decision.approved_notional_usd <= 0:
            return None

        inst = self.universe[signal.symbol]
        notional = decision.approved_notional_usd
        cash = self.cash_usd
        max_notional_by_cash = cash * max(1.0, inst.max_leverage)
        if notional > max_notional_by_cash:
            notional = max_notional_by_cash
        if notional <= 0:
            return None

        margin_required = notional / max(1.0, inst.max_leverage)
        vol_factor = max(0.5, bar.realized_vol_bps / 25.0)
        entry_slippage_bps = (inst.slippage_bps_per_vol * 0.5) * vol_factor
        entry_half_spread_bps = inst.spread_bps * 0.5
        entry_comm_usd = notional * (inst.commission_bps / 10000.0)
        entry_slip_usd = notional * (entry_slippage_bps / 10000.0)
        entry_spread_usd = notional * (entry_half_spread_bps / 10000.0)

        entry_mid = bar.mid_price
        quantity = notional / max(1e-9, entry_mid)
        initial_costs = round(entry_comm_usd + entry_slip_usd + entry_spread_usd, 4)

        pos = Position(
            symbol=signal.symbol,
            asset_class=inst.asset_class,
            side=signal.side,
            quantity=round(quantity, 6),
            entry_price=round(entry_mid, 6),
            current_price=round(entry_mid, 6),
            stop_loss_price=decision.stop_loss_price,
            take_profit_price=decision.take_profit_price,
            notional_usd=round(notional, 2),
            unrealized_pnl_usd=round(-initial_costs, 2),
            accrued_costs_usd=initial_costs,
            opened_at=timestamp,
            strategy_name=signal.strategy_name,
            regime_at_entry=regime,
            calibrated_prob_at_entry=signal.calibrated_probability,
            bars_held=0,
            news_catalyzed=len(signal.news_event_ids) > 0,
        )

        self.storage.set_state("cash_usd", round(cash - margin_required, 4))
        self.storage.save_position(pos)
        return pos

    def _close_position(
        self, pos: Position, bar: MarketBar, closed_at: str, exit_reason: str
    ) -> TradeRecord:
        inst = self.universe[pos.symbol]
        vol_factor = max(0.5, bar.realized_vol_bps / 25.0)
        exit_slippage_bps = (inst.slippage_bps_per_vol * 0.5) * vol_factor
        if exit_reason == "STOP_LOSS":
            exit_slippage_bps *= 1.35  # Realistic adverse stop-loss slippage

        exit_mid = bar.mid_price
        if pos.side == OrderSide.BUY:
            gross_pnl = (exit_mid - pos.entry_price) * pos.quantity
        else:
            gross_pnl = (pos.entry_price - exit_mid) * pos.quantity

        exit_comm_usd = pos.notional_usd * (inst.commission_bps / 10000.0)
        exit_slip_usd = pos.notional_usd * (exit_slippage_bps / 10000.0)
        exit_spread_usd = pos.notional_usd * ((inst.spread_bps * 0.5) / 10000.0)

        gross_rounded = round(gross_pnl, 2)
        total_cost = round(
            pos.accrued_costs_usd + exit_comm_usd + exit_slip_usd + exit_spread_usd, 2
        )
        net_pnl = round(gross_rounded - total_cost, 2)
        net_ret_bps = (net_pnl / max(1.0, pos.notional_usd)) * 10000.0

        counter = int(self.storage.get_state("trade_counter", 0)) + 1
        self.storage.set_state("trade_counter", counter)
        trade_id = f"trd-{counter:05d}"

        trade = TradeRecord(
            trade_id=trade_id,
            symbol=pos.symbol,
            asset_class=pos.asset_class,
            side=pos.side,
            strategy_name=pos.strategy_name,
            regime_at_entry=pos.regime_at_entry,
            opened_at=pos.opened_at,
            closed_at=closed_at,
            entry_price=round(pos.entry_price, 6),
            exit_price=round(exit_mid, 6),
            notional_usd=round(pos.notional_usd, 2),
            gross_pnl_usd=gross_rounded,
            total_cost_usd=total_cost,
            net_pnl_usd=net_pnl,
            net_return_bps=round(net_ret_bps, 2),
            calibrated_prob_at_entry=pos.calibrated_prob_at_entry,
            exit_reason=exit_reason,
            bars_held=pos.bars_held,
            news_catalyzed=pos.news_catalyzed,
        )

        margin_locked = self._margin_for_position(pos)
        new_cash = self.cash_usd + margin_locked + net_pnl
        self.storage.set_state("cash_usd", round(new_cash, 4))
        self.storage.delete_position(pos.symbol)
        self.storage.save_trade(trade)
        return trade
