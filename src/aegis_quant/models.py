"""Core domain models for Aegis Quant."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    FOREX = "FOREX"
    CRYPTO = "CRYPTO"
    CASH = "CASH"


class SourceTier(int, Enum):
    TIER_1_OFFICIAL = 1  # Central banks, SEC filings, official economic releases
    TIER_2_WIRE = 2      # Major financial wires & primary exchange feeds
    TIER_3_MEDIA = 3     # General financial media & analyst commentary
    TIER_4_RUMOR = 4     # Unverified social feeds / rumor mill


class MarketRegime(str, Enum):
    RISK_ON_GROWTH = "RISK_ON_GROWTH"
    INFLATION_DEFENSIVE = "INFLATION_DEFENSIVE"
    RATE_SHOCK_TIGHTENING = "RATE_SHOCK_TIGHTENING"
    CRISIS_DELEVERAGING = "CRISIS_DELEVERAGING"
    HIGH_DISPERSION_NEUTRAL = "HIGH_DISPERSION_NEUTRAL"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class MistakeCategory(str, Enum):
    OVERCONFIDENCE_BIAS = "OVERCONFIDENCE_BIAS"
    NEWS_WHIPSAW = "NEWS_WHIPSAW"
    COST_EROSION = "COST_EROSION"
    REGIME_MISMATCH = "REGIME_MISMATCH"
    CORRELATED_CROWDING = "CORRELATED_CROWDING"
    STOP_LOSS_GAP = "STOP_LOSS_GAP"


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    asset_class: AssetClass
    base_currency: str
    quote_currency: str
    spread_bps: float
    commission_bps: float
    slippage_bps_per_vol: float
    overnight_swap_bps: float
    max_leverage: float
    annual_yield_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["asset_class"] = self.asset_class.value
        return data


DEFAULT_UNIVERSE: dict[str, Instrument] = {
    "SPY": Instrument(
        symbol="SPY",
        name="S&P 500 Diversified US Equities ETF",
        asset_class=AssetClass.EQUITY,
        base_currency="SPY",
        quote_currency="USD",
        spread_bps=1.2,
        commission_bps=0.5,
        slippage_bps_per_vol=1.5,
        overnight_swap_bps=0.0,
        max_leverage=1.0,
        annual_yield_pct=1.4,
    ),
    "QQQ": Instrument(
        symbol="QQQ",
        name="Nasdaq-100 Global Tech & Growth ETF",
        asset_class=AssetClass.EQUITY,
        base_currency="QQQ",
        quote_currency="USD",
        spread_bps=1.5,
        commission_bps=0.5,
        slippage_bps_per_vol=1.8,
        overnight_swap_bps=0.0,
        max_leverage=1.0,
        annual_yield_pct=0.6,
    ),
    "MSFT": Instrument(
        symbol="MSFT",
        name="Microsoft Corp. (Enterprise Cloud & AI)",
        asset_class=AssetClass.EQUITY,
        base_currency="MSFT",
        quote_currency="USD",
        spread_bps=2.0,
        commission_bps=0.8,
        slippage_bps_per_vol=2.2,
        overnight_swap_bps=0.0,
        max_leverage=1.0,
        annual_yield_pct=0.8,
    ),
    "NVDA": Instrument(
        symbol="NVDA",
        name="NVIDIA Corp. (Accelerated Computing)",
        asset_class=AssetClass.EQUITY,
        base_currency="NVDA",
        quote_currency="USD",
        spread_bps=2.5,
        commission_bps=0.8,
        slippage_bps_per_vol=3.0,
        overnight_swap_bps=0.0,
        max_leverage=1.0,
        annual_yield_pct=0.1,
    ),
    "EUR/USD": Instrument(
        symbol="EUR/USD",
        name="Euro / US Dollar Spot Forex",
        asset_class=AssetClass.FOREX,
        base_currency="EUR",
        quote_currency="USD",
        spread_bps=1.0,
        commission_bps=0.4,
        slippage_bps_per_vol=1.4,
        overnight_swap_bps=0.45,
        max_leverage=3.0,
        annual_yield_pct=-1.2,
    ),
    "USD/JPY": Instrument(
        symbol="USD/JPY",
        name="US Dollar / Japanese Yen Spot Forex",
        asset_class=AssetClass.FOREX,
        base_currency="USD",
        quote_currency="JPY",
        spread_bps=1.2,
        commission_bps=0.4,
        slippage_bps_per_vol=1.7,
        overnight_swap_bps=-0.35,
        max_leverage=3.0,
        annual_yield_pct=3.5,
    ),
    "GBP/USD": Instrument(
        symbol="GBP/USD",
        name="British Pound / US Dollar Spot Forex",
        asset_class=AssetClass.FOREX,
        base_currency="GBP",
        quote_currency="USD",
        spread_bps=1.5,
        commission_bps=0.5,
        slippage_bps_per_vol=1.9,
        overnight_swap_bps=0.30,
        max_leverage=3.0,
        annual_yield_pct=-0.2,
    ),
    "BTC/USD": Instrument(
        symbol="BTC/USD",
        name="Bitcoin / US Dollar Spot Crypto",
        asset_class=AssetClass.CRYPTO,
        base_currency="BTC",
        quote_currency="USD",
        spread_bps=6.0,
        commission_bps=4.0,
        slippage_bps_per_vol=5.5,
        overnight_swap_bps=1.2,
        max_leverage=1.0,
        annual_yield_pct=0.0,
    ),
    "ETH/USD": Instrument(
        symbol="ETH/USD",
        name="Ethereum / US Dollar Spot Crypto",
        asset_class=AssetClass.CRYPTO,
        base_currency="ETH",
        quote_currency="USD",
        spread_bps=8.0,
        commission_bps=4.5,
        slippage_bps_per_vol=6.5,
        overnight_swap_bps=1.4,
        max_leverage=1.0,
        annual_yield_pct=2.8,
    ),
    "SHY": Instrument(
        symbol="SHY",
        name="1-3 Year US Treasury Bond / Cash Reserve",
        asset_class=AssetClass.CASH,
        base_currency="SHY",
        quote_currency="USD",
        spread_bps=0.5,
        commission_bps=0.2,
        slippage_bps_per_vol=0.4,
        overnight_swap_bps=0.0,
        max_leverage=1.0,
        annual_yield_pct=4.35,
    ),
}


@dataclass
class MarketBar:
    symbol: str
    timestamp: str
    mid_price: float
    bid_price: float
    ask_price: float
    return_1b_bps: float
    return_5b_bps: float
    return_20b_bps: float
    realized_vol_bps: float
    z_score_20b: float
    volume_ratio: float
    stale_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NewsEvent:
    event_id: str
    timestamp: str
    headline: str
    summary: str
    source_name: str
    source_tier: SourceTier
    affected_symbols: list[str]
    asset_classes: list[AssetClass]
    sentiment_score: float       # -1.0 (strongly bearish) to +1.0 (strongly bullish)
    impact_magnitude_bps: float  # estimated raw price move in bps
    credibility_weight: float    # 0.0 to 1.0 after tier + corroboration adjustment
    contradicted: bool = False
    contradiction_note: str = ""
    dedup_fingerprint: str = ""
    uncertainty_bps: float = 15.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_tier"] = int(self.source_tier)
        data["asset_classes"] = [ac.value for ac in self.asset_classes]
        return data


@dataclass
class StrategySignal:
    strategy_name: str
    symbol: str
    asset_class: AssetClass
    side: OrderSide
    raw_confidence: float
    calibrated_probability: float
    expected_gross_bps: float
    expected_cost_bps: float
    expected_net_edge_bps: float
    stop_loss_bps: float
    take_profit_bps: float
    rationale: str
    invalidation_criteria: str
    news_event_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["asset_class"] = self.asset_class.value
        data["side"] = self.side.value
        return data


@dataclass
class AssetBucketAdvice:
    asset_class: AssetClass
    recommended_weight_pct: float
    expected_net_edge_bps: float
    annualized_vol_pct: float
    suitability_score: float
    top_instruments: list[str]
    rationale: str
    key_risks: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["asset_class"] = self.asset_class.value
        return data


@dataclass
class AllocationAdvice:
    timestamp: str
    regime: MarketRegime
    regime_confidence: float
    primary_verdict: str
    do_nothing_recommended: bool
    buckets: list[AssetBucketAdvice]
    actionable_signals: list[StrategySignal]
    counter_thesis: str
    verified_news_count: int
    rejected_rumor_count: int
    contradiction_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "regime": self.regime.value,
            "regime_confidence": round(self.regime_confidence, 4),
            "primary_verdict": self.primary_verdict,
            "do_nothing_recommended": self.do_nothing_recommended,
            "buckets": [b.to_dict() for b in self.buckets],
            "actionable_signals": [s.to_dict() for s in self.actionable_signals],
            "counter_thesis": self.counter_thesis,
            "verified_news_count": self.verified_news_count,
            "rejected_rumor_count": self.rejected_rumor_count,
            "contradiction_count": self.contradiction_count,
        }


@dataclass
class RiskConfig:
    max_risk_per_trade_pct: float = 0.75
    max_single_position_pct: float = 18.0
    max_asset_class_pct: dict[str, float] = field(
        default_factory=lambda: {
            AssetClass.EQUITY.value: 65.0,
            AssetClass.FOREX.value: 25.0,
            AssetClass.CRYPTO.value: 12.0,
            AssetClass.CASH.value: 100.0,
        }
    )
    max_portfolio_leverage: float = 1.25
    max_daily_drawdown_pct: float = 2.5
    max_total_drawdown_pct: float = 6.0
    min_net_edge_bps: float = 6.0
    min_calibrated_prob: float = 0.54
    max_spread_bps_forex: float = 3.5
    max_spread_bps_crypto: float = 18.0
    max_stale_seconds: float = 30.0
    max_usd_directional_exposure_pct: float = 35.0


@dataclass
class RiskDecision:
    approved: bool
    symbol: str
    side: OrderSide
    requested_notional_usd: float
    approved_notional_usd: float
    stop_loss_price: float
    take_profit_price: float
    rejection_reasons: list[str]
    active_learned_rules: list[str]
    kill_switch_active: bool

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["side"] = self.side.value
        return data


@dataclass
class Position:
    symbol: str
    asset_class: AssetClass
    side: OrderSide
    quantity: float
    entry_price: float
    current_price: float
    stop_loss_price: float
    take_profit_price: float
    notional_usd: float
    unrealized_pnl_usd: float
    accrued_costs_usd: float
    opened_at: str
    strategy_name: str
    regime_at_entry: MarketRegime
    calibrated_prob_at_entry: float
    bars_held: int = 0
    news_catalyzed: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["asset_class"] = self.asset_class.value
        data["side"] = self.side.value
        data["regime_at_entry"] = self.regime_at_entry.value
        return data


@dataclass
class TradeRecord:
    trade_id: str
    symbol: str
    asset_class: AssetClass
    side: OrderSide
    strategy_name: str
    regime_at_entry: MarketRegime
    opened_at: str
    closed_at: str
    entry_price: float
    exit_price: float
    notional_usd: float
    gross_pnl_usd: float
    total_cost_usd: float
    net_pnl_usd: float
    net_return_bps: float
    calibrated_prob_at_entry: float
    exit_reason: str
    bars_held: int
    news_catalyzed: bool

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["asset_class"] = self.asset_class.value
        data["side"] = self.side.value
        data["regime_at_entry"] = self.regime_at_entry.value
        return data


@dataclass
class MistakePostMortem:
    postmortem_id: str
    trade_id: str
    timestamp: str
    symbol: str
    asset_class: AssetClass
    strategy_name: str
    regime: MarketRegime
    category: MistakeCategory
    severity: float
    predicted_prob: float
    realized_win: int
    brier_error: float
    gross_pnl_usd: float
    cost_usd: float
    net_pnl_usd: float
    diagnosis: str
    corrective_action: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["asset_class"] = self.asset_class.value
        data["regime"] = self.regime.value
        data["category"] = self.category.value
        return data


@dataclass
class LearnedRule:
    rule_id: str
    created_at: str
    strategy_name: str
    asset_class: str
    regime: str
    mistake_category: str
    min_confidence_boost: float
    min_edge_boost_bps: float
    size_multiplier: float
    require_tier1_or_tier2_news: bool
    cooldown_bars: int
    evidence_count: int
    description: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExpertState:
    strategy_name: str
    weight: float
    alpha_wins: float
    beta_losses: float
    ewma_brier_score: float
    ewma_net_return_bps: float
    total_predictions: int
    calibration_slope: float = 0.85

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
