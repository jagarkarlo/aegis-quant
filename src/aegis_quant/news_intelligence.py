"""Continuous global news ingestion, deduplication, rumor filtering, and market simulation."""

from __future__ import annotations

import hashlib
import math
import random
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

from aegis_quant.models import (
    DEFAULT_UNIVERSE,
    AssetClass,
    Instrument,
    MarketBar,
    MarketRegime,
    NewsEvent,
    SourceTier,
)
from aegis_quant.storage import AegisStorage

STOPWORDS = {
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or",
    "is", "are", "was", "were", "as", "by", "with", "from", "after", "amid",
    "breaking", "update", "report", "says", "sources", "market", "markets",
}

TIER_BASE_CREDIBILITY: dict[SourceTier, float] = {
    SourceTier.TIER_1_OFFICIAL: 0.96,
    SourceTier.TIER_2_WIRE: 0.84,
    SourceTier.TIER_3_MEDIA: 0.62,
    SourceTier.TIER_4_RUMOR: 0.18,
}

KEYWORD_IMPACTS: list[tuple[re.Pattern[str], list[str], float, float]] = [
    (re.compile(r"\b(fed|fomc)\b.*\b(hike|hawkish|higher for longer|hot cpi)\b", re.I), ["USD/JPY", "EUR/USD", "SPY", "QQQ", "BTC/USD"], -0.65, 28.0),
    (re.compile(r"\b(fed|fomc)\b.*\b(cut|dovish|cooling inflation|soft landing)\b", re.I), ["SPY", "QQQ", "MSFT", "NVDA", "EUR/USD", "BTC/USD"], 0.72, 30.0),
    (re.compile(r"\b(ecb)\b.*\b(hike|hawkish|sticky inflation)\b", re.I), ["EUR/USD"], 0.68, 22.0),
    (re.compile(r"\b(boj|bank of japan)\b.*\b(intervention|hike|yield curve)\b", re.I), ["USD/JPY"], -0.78, 34.0),
    (re.compile(r"\b(boe|bank of england)\b.*\b(cut|weak gdp|contraction)\b", re.I), ["GBP/USD"], -0.62, 21.0),
    (re.compile(r"\b(nvidia|nvda|microsoft|msft|cloud|ai)\b.*\b(beat|record revenue|raised guidance|datacenter surge)\b", re.I), ["NVDA", "MSFT", "QQQ", "SPY"], 0.82, 36.0),
    (re.compile(r"\b(sec|regulator|exchange)\b.*\b(lawsuit|halt|hack|outflow|crackdown)\b", re.I), ["BTC/USD", "ETH/USD"], -0.80, 48.0),
    (re.compile(r"\b(etf inflow|institutional adoption|protocol upgrade)\b", re.I), ["BTC/USD", "ETH/USD"], 0.64, 38.0),
    (re.compile(r"\b(geopolitical|escalation|liquidity crunch|credit stress)\b", re.I), ["SPY", "QQQ", "BTC/USD", "ETH/USD", "EUR/USD"], -0.75, 35.0),
]


def compute_dedup_fingerprint(headline: str) -> str:
    """Compute a canonical content fingerprint ignoring punctuation and stopwords."""
    tokens = re.findall(r"[a-z0-9]+", headline.lower())
    filtered = [t for t in tokens if t not in STOPWORDS and len(t) > 2]
    filtered.sort()
    canonical = " ".join(filtered[:12])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


class NewsIntelligenceEngine:
    """Ingests, deduplicates, verifies, and cross-checks global financial news."""

    def __init__(self, storage: AegisStorage) -> None:
        self.storage = storage

    def ingest_batch(self, raw_events: list[NewsEvent]) -> dict[str, Any]:
        """Process incoming news events: deduplicate, detect contradictions, and persist."""
        recent = self.storage.list_recent_news(limit=40)
        accepted: list[NewsEvent] = []
        duplicates = 0
        rumors_filtered = 0
        contradictions = 0

        for ev in raw_events:
            fp = ev.dedup_fingerprint or compute_dedup_fingerprint(ev.headline)
            ev.dedup_fingerprint = fp
            if self.storage.has_news_fingerprint(fp):
                duplicates += 1
                continue

            base_cred = TIER_BASE_CREDIBILITY.get(ev.source_tier, 0.5)
            ev.credibility_weight = round(min(1.0, max(0.05, base_cred)), 3)

            if ev.source_tier == SourceTier.TIER_4_RUMOR:
                rumors_filtered += 1
                self.storage.save_news_event(ev)
                accepted.append(ev)
                continue

            # Check for contradictory reports among verified/non-rumor sources on the same symbols
            for prev in recent + accepted:
                if prev.source_tier == SourceTier.TIER_4_RUMOR:
                    continue
                shared = set(ev.affected_symbols).intersection(prev.affected_symbols)
                if not shared:
                    continue
                if ev.sentiment_score * prev.sentiment_score < -0.20:
                    ev.contradicted = True
                    contradictions += 1
                    shared_str = ", ".join(sorted(shared))
                    ev.contradiction_note = (
                        f"Conflicts with {prev.source_name} (Tier {int(prev.source_tier)}) on {shared_str}"
                    )
                    if int(ev.source_tier) >= int(prev.source_tier):
                        ev.credibility_weight = round(ev.credibility_weight * 0.35, 3)
                        ev.uncertainty_bps = round(ev.uncertainty_bps * 1.8, 1)
                    break

            self.storage.save_news_event(ev)
            accepted.append(ev)

        return {
            "accepted_count": len(accepted),
            "duplicates_dropped": duplicates,
            "rumors_flagged": rumors_filtered,
            "contradictions_detected": contradictions,
            "events": accepted,
        }

    def parse_headline_into_event(
        self,
        headline: str,
        summary: str = "",
        source_name: str = "GlobalWire",
        source_tier: SourceTier = SourceTier.TIER_2_WIRE,
        timestamp: str | None = None,
    ) -> NewsEvent:
        """Classify arbitrary headline text into affected instruments, polarity, and impact."""
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        text = f"{headline} {summary}"
        affected: list[str] = []
        sentiment = 0.0
        impact_bps = 12.0

        for pattern, symbols, polarity, mag_bps in KEYWORD_IMPACTS:
            if pattern.search(text):
                for s in symbols:
                    if s not in affected:
                        affected.append(s)
                sentiment = polarity
                impact_bps = mag_bps
                break

        if not affected:
            affected = ["SPY", "EUR/USD"]
            sentiment = 0.05
            impact_bps = 8.0

        # Adjust polarity for USD pairs when Fed hawkishness strengthens USD
        asset_classes: list[AssetClass] = []
        for sym in affected:
            inst = DEFAULT_UNIVERSE.get(sym)
            if inst and inst.asset_class not in asset_classes:
                asset_classes.append(inst.asset_class)

        fp = compute_dedup_fingerprint(headline)
        event_id = f"news-{fp[:10]}-{abs(hash((headline, ts))) % 100000:05d}"
        uncertainty = 10.0 if source_tier == SourceTier.TIER_1_OFFICIAL else (
            16.0 if source_tier == SourceTier.TIER_2_WIRE else 32.0
        )

        return NewsEvent(
            event_id=event_id,
            timestamp=ts,
            headline=headline,
            summary=summary or headline,
            source_name=source_name,
            source_tier=source_tier,
            affected_symbols=affected,
            asset_classes=asset_classes,
            sentiment_score=sentiment,
            impact_magnitude_bps=impact_bps,
            credibility_weight=TIER_BASE_CREDIBILITY[source_tier],
            dedup_fingerprint=fp,
            uncertainty_bps=uncertainty,
        )

    def fetch_live_rss_items(self, rss_url: str, timeout: float = 5.0) -> list[NewsEvent]:
        """Fetch and parse live RSS/Atom XML headlines into structured NewsEvents."""
        req = urllib.request.Request(rss_url, headers={"User-Agent": "AegisQuant/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            xml_bytes = resp.read()
        root = ET.fromstring(xml_bytes)
        events: list[NewsEvent] = []
        for item in root.findall(".//item")[:15]:
            title = (item.findtext("title") or "").strip()
            desc = (item.findtext("description") or "").strip()
            if title:
                events.append(
                    self.parse_headline_into_event(
                        headline=title,
                        summary=desc[:280],
                        source_name="LiveRSSFeed",
                        source_tier=SourceTier.TIER_2_WIRE,
                    )
                )
        return events

    def aggregate_symbol_news_signal(
        self, symbol: str, require_high_tier: bool = False
    ) -> dict[str, Any]:
        """Aggregate verified news catalysts for an instrument, ignoring Tier 4 rumors."""
        recent = self.storage.list_recent_news(limit=25)
        weighted_bps = 0.0
        total_weight = 0.0
        supporting_ids: list[str] = []
        rumors_ignored = 0
        contradictions_seen = 0

        for idx, ev in enumerate(recent):
            if symbol not in ev.affected_symbols:
                continue
            if ev.source_tier == SourceTier.TIER_4_RUMOR:
                rumors_ignored += 1
                continue
            if require_high_tier and int(ev.source_tier) > int(SourceTier.TIER_2_WIRE):
                continue
            if ev.contradicted:
                contradictions_seen += 1

            recency_decay = math.exp(-0.18 * idx)
            w = ev.credibility_weight * recency_decay
            direction = ev.sentiment_score
            # Special FX convention: hawkish US Fed is bearish EUR/USD & GBP/USD but bullish USD/JPY
            if symbol == "USD/JPY" and "fed" in ev.headline.lower() and "hike" in ev.headline.lower():
                direction = abs(ev.sentiment_score)

            weighted_bps += direction * ev.impact_magnitude_bps * w
            total_weight += w
            supporting_ids.append(ev.event_id)

        net_bps = weighted_bps if total_weight > 0 else 0.0
        confidence = min(0.92, 0.50 + min(0.42, abs(net_bps) / 75.0)) if total_weight > 0 else 0.50

        return {
            "symbol": symbol,
            "net_catalyst_bps": round(net_bps, 2),
            "confidence": round(confidence, 4),
            "supporting_event_ids": supporting_ids[:5],
            "rumors_ignored": rumors_ignored,
            "contradictions_seen": contradictions_seen,
        }


class GlobalFeedSimulator:
    """Deterministic multi-regime simulator for Equities, Forex, Crypto, Cash, and Global News."""

    REGIME_SEQUENCE: list[MarketRegime] = [
        MarketRegime.RISK_ON_GROWTH,
        MarketRegime.INFLATION_DEFENSIVE,
        MarketRegime.RATE_SHOCK_TIGHTENING,
        MarketRegime.HIGH_DISPERSION_NEUTRAL,
        MarketRegime.CRISIS_DELEVERAGING,
        MarketRegime.RISK_ON_GROWTH,
    ]

    def __init__(
        self,
        seed: int = 42,
        universe: dict[str, Instrument] | None = None,
    ) -> None:
        self.rng = random.Random(seed)
        self.universe = universe or DEFAULT_UNIVERSE
        self.step_index = 0
        self.base_time = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
        self.prices: dict[str, list[float]] = {
            "SPY": [585.0],
            "QQQ": [505.0],
            "MSFT": [445.0],
            "NVDA": [138.0],
            "EUR/USD": [1.0850],
            "USD/JPY": [151.20],
            "GBP/USD": [1.2940],
            "BTC/USD": [89500.0],
            "ETH/USD": [3120.0],
            "SHY": [82.40],
        }
        for sym in self.universe:
            if sym not in self.prices:
                self.prices[sym] = [100.0]

        # Warm up 22 bars of history so 20-bar indicators are ready immediately
        for _ in range(22):
            self._advance_prices_only(MarketRegime.RISK_ON_GROWTH)

    def current_regime(self) -> MarketRegime:
        block = (self.step_index // 18) % len(self.REGIME_SEQUENCE)
        return self.REGIME_SEQUENCE[block]

    def _regime_drift_and_vol_bps(self, symbol: str, regime: MarketRegime) -> tuple[float, float]:
        inst = self.universe[symbol]
        if inst.asset_class == AssetClass.CASH:
            return (1.6, 2.5)

        if regime == MarketRegime.RISK_ON_GROWTH:
            if inst.asset_class == AssetClass.EQUITY:
                return (14.5 if symbol in ("QQQ", "NVDA", "MSFT") else 11.0, 26.0)
            if inst.asset_class == AssetClass.FOREX:
                return (3.5 if symbol != "USD/JPY" else 5.0, 18.0)
            return (18.0, 88.0)

        if regime == MarketRegime.INFLATION_DEFENSIVE:
            if inst.asset_class == AssetClass.EQUITY:
                return (2.0, 34.0)
            if inst.asset_class == AssetClass.FOREX:
                return (8.5 if symbol == "USD/JPY" else -6.5, 22.0)
            return (-8.0, 95.0)

        if regime == MarketRegime.RATE_SHOCK_TIGHTENING:
            if inst.asset_class == AssetClass.EQUITY:
                return (-11.0, 45.0)
            if inst.asset_class == AssetClass.FOREX:
                return (-9.5 if symbol in ("EUR/USD", "GBP/USD") else 10.5, 25.0)
            return (-24.0, 115.0)

        if regime == MarketRegime.CRISIS_DELEVERAGING:
            if inst.asset_class == AssetClass.EQUITY:
                return (-22.0, 68.0)
            if inst.asset_class == AssetClass.FOREX:
                return (-14.0, 38.0)
            return (-42.0, 155.0)

        # HIGH_DISPERSION_NEUTRAL (mean-reverting chop where overtrading gets eaten by costs)
        if inst.asset_class == AssetClass.EQUITY:
            return (1.5, 28.0)
        if inst.asset_class == AssetClass.FOREX:
            return (0.0, 19.0)
        return (0.0, 92.0)

    def _advance_prices_only(self, regime: MarketRegime, news_shocks: dict[str, float] | None = None) -> None:
        shocks = news_shocks or {}
        for sym, hist in self.prices.items():
            drift_bps, vol_bps = self._regime_drift_and_vol_bps(sym, regime)
            # Add mean-reversion pull in neutral regime
            if regime == MarketRegime.HIGH_DISPERSION_NEUTRAL and len(hist) >= 10:
                mean_10 = sum(hist[-10:]) / 10.0
                dev_bps = ((hist[-1] - mean_10) / mean_10) * 10000.0
                drift_bps -= 0.35 * dev_bps

            ret_bps = drift_bps + self.rng.gauss(0.0, vol_bps) + shocks.get(sym, 0.0)
            next_price = max(0.01, hist[-1] * (1.0 + ret_bps / 10000.0))
            hist.append(next_price)

    def next_tick(self) -> tuple[str, MarketRegime, dict[str, MarketBar], list[NewsEvent]]:
        """Advance simulation by one tick, producing market bars and global news events."""
        self.step_index += 1
        ts = (self.base_time + timedelta(hours=self.step_index)).isoformat()
        regime = self.current_regime()

        events, shocks = self._generate_news_for_tick(ts, regime)
        self._advance_prices_only(regime, shocks)

        bars: dict[str, MarketBar] = {}
        for sym, inst in self.universe.items():
            hist = self.prices[sym]
            mid = hist[-1]
            p_1 = hist[-2]
            p_5 = hist[-6]
            p_20 = hist[-21]

            ret_1b = ((mid / p_1) - 1.0) * 10000.0
            ret_5b = ((mid / p_5) - 1.0) * 10000.0
            ret_20b = ((mid / p_20) - 1.0) * 10000.0

            recent_rets = [
                ((hist[-i] / hist[-i - 1]) - 1.0) * 10000.0 for i in range(1, 21)
            ]
            mean_r = sum(recent_rets) / len(recent_rets)
            var_r = sum((r - mean_r) ** 2 for r in recent_rets) / max(1, len(recent_rets) - 1)
            vol_bps = max(1.0, math.sqrt(var_r))

            mean_20 = sum(hist[-20:]) / 20.0
            std_20 = math.sqrt(sum((p - mean_20) ** 2 for p in hist[-20:]) / 19.0) or (mid * 0.001)
            z_score = (mid - mean_20) / std_20

            # Dynamic spread widens during crisis regimes
            spread_mult = 1.65 if regime == MarketRegime.CRISIS_DELEVERAGING else 1.0
            half_spread = mid * (inst.spread_bps * spread_mult / 20000.0)
            bars[sym] = MarketBar(
                symbol=sym,
                timestamp=ts,
                mid_price=round(mid, 6),
                bid_price=round(mid - half_spread, 6),
                ask_price=round(mid + half_spread, 6),
                return_1b_bps=round(ret_1b, 2),
                return_5b_bps=round(ret_5b, 2),
                return_20b_bps=round(ret_20b, 2),
                realized_vol_bps=round(vol_bps, 2),
                z_score_20b=round(z_score, 3),
                volume_ratio=round(1.0 + abs(ret_1b) / max(10.0, vol_bps), 2),
                stale_seconds=0.0,
            )

        return ts, regime, bars, events

    def _generate_news_for_tick(
        self, ts: str, regime: MarketRegime
    ) -> tuple[list[NewsEvent], dict[str, float]]:
        events: list[NewsEvent] = []
        shocks: dict[str, float] = {}

        if regime == MarketRegime.RISK_ON_GROWTH:
            headline = "Enterprise AI & cloud capital expenditure beats consensus; US core inflation cools to target"
            ev = NewsEvent(
                event_id=f"sim-news-{self.step_index}-1",
                timestamp=ts,
                headline=headline,
                summary="Official SEC 8-K filings and BLS release confirm cooling core inflation alongside resilient cloud earnings growth.",
                source_name="OfficialMacroAndFilings",
                source_tier=SourceTier.TIER_1_OFFICIAL,
                affected_symbols=["SPY", "QQQ", "MSFT", "NVDA"],
                asset_classes=[AssetClass.EQUITY],
                sentiment_score=0.78,
                impact_magnitude_bps=24.0,
                credibility_weight=0.96,
                dedup_fingerprint=compute_dedup_fingerprint(f"{headline}-{self.step_index // 3}"),
                uncertainty_bps=10.0,
            )
            events.append(ev)
            for s in ("SPY", "QQQ", "MSFT", "NVDA"):
                shocks[s] = 10.0

        elif regime == MarketRegime.RATE_SHOCK_TIGHTENING:
            headline = "Federal Reserve signals higher-for-longer policy path after upside services CPI surprise"
            ev = NewsEvent(
                event_id=f"sim-news-{self.step_index}-1",
                timestamp=ts,
                headline=headline,
                summary="FOMC minutes and Treasury auction tail push real yields higher, pressuring duration equities and EUR/USD while lifting USD/JPY.",
                source_name="FederalReserveWire",
                source_tier=SourceTier.TIER_1_OFFICIAL,
                affected_symbols=["USD/JPY", "EUR/USD", "SPY", "QQQ", "BTC/USD"],
                asset_classes=[AssetClass.FOREX, AssetClass.EQUITY, AssetClass.CRYPTO],
                sentiment_score=0.74,
                impact_magnitude_bps=26.0,
                credibility_weight=0.96,
                dedup_fingerprint=compute_dedup_fingerprint(f"{headline}-{self.step_index // 3}"),
                uncertainty_bps=11.0,
            )
            events.append(ev)
            shocks["USD/JPY"] = 14.0
            shocks["EUR/USD"] = -12.0

        elif regime == MarketRegime.CRISIS_DELEVERAGING:
            headline = "Global cross-asset volatility spike triggers systematic de-risking into short-term Treasuries"
            ev = NewsEvent(
                event_id=f"sim-news-{self.step_index}-1",
                timestamp=ts,
                headline=headline,
                summary="Funding spreads widen across offshore FX and crypto derivatives; institutional flows rotate to 1-3Y Treasury bills (SHY).",
                source_name="PrimaryMarketMonitor",
                source_tier=SourceTier.TIER_1_OFFICIAL,
                affected_symbols=["SHY", "SPY", "BTC/USD", "ETH/USD"],
                asset_classes=[AssetClass.CASH, AssetClass.EQUITY, AssetClass.CRYPTO],
                sentiment_score=-0.82,
                impact_magnitude_bps=35.0,
                credibility_weight=0.96,
                dedup_fingerprint=compute_dedup_fingerprint(f"{headline}-{self.step_index // 3}"),
                uncertainty_bps=14.0,
            )
            events.append(ev)
            shocks["SHY"] = 3.0
            shocks["BTC/USD"] = -28.0

        else:
            headline = "Central bank speakers offer mixed macro views ahead of next month's policy meeting"
            ev = NewsEvent(
                event_id=f"sim-news-{self.step_index}-1",
                timestamp=ts,
                headline=headline,
                summary="Cross-asset implied correlations moderate as markets digest conflicting regional PMI surveys.",
                source_name="GlobalMacroWire",
                source_tier=SourceTier.TIER_2_WIRE,
                affected_symbols=["EUR/USD", "USD/JPY", "SPY"],
                asset_classes=[AssetClass.FOREX, AssetClass.EQUITY],
                sentiment_score=0.15,
                impact_magnitude_bps=9.0,
                credibility_weight=0.84,
                dedup_fingerprint=compute_dedup_fingerprint(f"{headline}-{self.step_index // 2}"),
                uncertainty_bps=18.0,
            )
            events.append(ev)

        # Periodically inject a Tier 4 social rumor / whipsaw trap to test rumor filtering & self-learning
        if self.step_index % 4 == 0:
            rumor_hl = f"UNVERIFIED RUMOR: Anonymous social post claims emergency FX/crypto intervention #{self.step_index}"
            events.append(
                NewsEvent(
                    event_id=f"sim-rumor-{self.step_index}",
                    timestamp=ts,
                    headline=rumor_hl,
                    summary="Unconfirmed social media post with no primary regulatory or central bank corroboration.",
                    source_name="AnonSocialStream",
                    source_tier=SourceTier.TIER_4_RUMOR,
                    affected_symbols=["BTC/USD", "EUR/USD"],
                    asset_classes=[AssetClass.CRYPTO, AssetClass.FOREX],
                    sentiment_score=0.90,
                    impact_magnitude_bps=55.0,
                    credibility_weight=0.18,
                    dedup_fingerprint=compute_dedup_fingerprint(rumor_hl),
                    uncertainty_bps=60.0,
                )
            )

        return events, shocks
