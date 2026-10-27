"""Live market data and global news feed collectors with automatic offline/proxy fallback.

Supports zero-key public endpoints for real-time/delayed cross-asset snapshots:
- Forex spot rates via ECB / Frankfurter JSON API
- Crypto spot rates via CoinGecko / Coinbase public JSON API
- Equity/ETF bars via Yahoo Finance v8 chart JSON API
- Multi-source global financial RSS/Atom feeds
Automatically falls back to GlobalFeedSimulator if a corporate proxy or network outage blocks access.
"""

from __future__ import annotations

import json
import math
import urllib.request
from datetime import datetime, timezone
from typing import Any

from aegis_quant.models import DEFAULT_UNIVERSE, Instrument, MarketBar, NewsEvent, SourceTier
from aegis_quant.news_intelligence import GlobalFeedSimulator, NewsIntelligenceEngine

DEFAULT_RSS_FEEDS: list[tuple[str, str, SourceTier]] = [
    ("https://www.federalreserve.gov/feeds/press_all.xml", "FederalReserveOfficial", SourceTier.TIER_1_OFFICIAL),
    ("https://www.ecb.europa.eu/rss/press.html", "ECBOfficial", SourceTier.TIER_1_OFFICIAL),
    ("https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664", "CNBCFinanceWire", SourceTier.TIER_2_WIRE),
    ("https://feeds.content.dowjones.io/public/rss/mw_topstories", "MarketWatchWire", SourceTier.TIER_2_WIRE),
]


class LiveMarketAndNewsCollector:
    """Collects live multi-asset market bars and RSS news with resilient fallback."""

    def __init__(
        self,
        news_engine: NewsIntelligenceEngine,
        simulator: GlobalFeedSimulator,
        universe: dict[str, Instrument] | None = None,
        rss_feeds: list[tuple[str, str, SourceTier]] | None = None,
        timeout: float = 4.0,
    ) -> None:
        self.news_engine = news_engine
        self.simulator = simulator
        self.universe = universe or DEFAULT_UNIVERSE
        self.rss_feeds = rss_feeds or DEFAULT_RSS_FEEDS
        self.timeout = timeout
        self.price_history: dict[str, list[float]] = {
            sym: list(hist) for sym, hist in self.simulator.prices.items()
        }

    def collect_tick(self, prefer_live: bool = True) -> dict[str, Any]:
        """Fetch live quotes and RSS headlines; seamlessly merge with simulator fallback if unreachable."""
        ts, sim_regime, sim_bars, sim_news = self.simulator.next_tick()
        if not prefer_live:
            return {
                "timestamp": ts,
                "mode": "SIMULATED",
                "live_quotes_fetched": 0,
                "live_news_fetched": 0,
                "bars": sim_bars,
                "news_events": sim_news,
            }

        live_prices: dict[str, float] = {}
        live_prices.update(self._fetch_live_fx_rates())
        live_prices.update(self._fetch_live_crypto_rates())
        live_prices.update(self._fetch_live_equity_quotes(["SPY", "QQQ", "MSFT", "NVDA", "SHY"]))

        now_iso = datetime.now(timezone.utc).isoformat()
        bars: dict[str, MarketBar] = {}
        for sym, inst in self.universe.items():
            if sym in live_prices and live_prices[sym] > 0:
                hist = self.price_history.setdefault(sym, [live_prices[sym]] * 22)
                hist.append(live_prices[sym])
                bars[sym] = self._build_bar_from_history(sym, inst, hist, now_iso)
            else:
                bars[sym] = sim_bars[sym]

        live_news = self._fetch_all_rss_news()
        combined_news = live_news if live_news else sim_news
        mode = "LIVE_HYBRID" if (live_prices or live_news) else "SIMULATED_FALLBACK"

        return {
            "timestamp": now_iso if mode == "LIVE_HYBRID" else ts,
            "mode": mode,
            "live_quotes_fetched": len(live_prices),
            "live_news_fetched": len(live_news),
            "bars": bars,
            "news_events": combined_news,
        }

    def _build_bar_from_history(
        self, sym: str, inst: Instrument, hist: list[float], timestamp: str
    ) -> MarketBar:
        while len(hist) < 22:
            hist.insert(0, hist[0])
        mid = hist[-1]
        p_1 = hist[-2]
        p_5 = hist[-6]
        p_20 = hist[-21]

        ret_1b = ((mid / max(1e-9, p_1)) - 1.0) * 10000.0
        ret_5b = ((mid / max(1e-9, p_5)) - 1.0) * 10000.0
        ret_20b = ((mid / max(1e-9, p_20)) - 1.0) * 10000.0

        recent_rets = [
            ((hist[-i] / max(1e-9, hist[-i - 1])) - 1.0) * 10000.0 for i in range(1, 21)
        ]
        mean_r = sum(recent_rets) / len(recent_rets)
        var_r = sum((r - mean_r) ** 2 for r in recent_rets) / max(1, len(recent_rets) - 1)
        vol_bps = max(5.0, math.sqrt(var_r))

        mean_20 = sum(hist[-20:]) / 20.0
        std_20 = math.sqrt(sum((p - mean_20) ** 2 for p in hist[-20:]) / 19.0) or (mid * 0.001)
        z_score = (mid - mean_20) / std_20
        half_spread = mid * (inst.spread_bps / 20000.0)

        return MarketBar(
            symbol=sym,
            timestamp=timestamp,
            mid_price=round(mid, 6),
            bid_price=round(mid - half_spread, 6),
            ask_price=round(mid + half_spread, 6),
            return_1b_bps=round(ret_1b, 2),
            return_5b_bps=round(ret_5b, 2),
            return_20b_bps=round(ret_20b, 2),
            realized_vol_bps=round(vol_bps, 2),
            z_score_20b=round(z_score, 3),
            volume_ratio=1.0,
            stale_seconds=0.0,
        )

    def _fetch_live_fx_rates(self) -> dict[str, float]:
        out: dict[str, float] = {}
        try:
            req = urllib.request.Request(
                "https://api.frankfurter.app/latest?from=USD&to=EUR,JPY,GBP",
                headers={"User-Agent": "AegisQuant/0.1"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            rates = data.get("rates", {})
            if "EUR" in rates and float(rates["EUR"]) > 0:
                out["EUR/USD"] = round(1.0 / float(rates["EUR"]), 6)
            if "JPY" in rates and float(rates["JPY"]) > 0:
                out["USD/JPY"] = round(float(rates["JPY"]), 6)
            if "GBP" in rates and float(rates["GBP"]) > 0:
                out["GBP/USD"] = round(1.0 / float(rates["GBP"]), 6)
        except Exception:
            pass
        return out

    def _fetch_live_crypto_rates(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for sym, pair in (("BTC/USD", "BTC-USD"), ("ETH/USD", "ETH-USD")):
            try:
                req = urllib.request.Request(
                    f"https://api.coinbase.com/v2/prices/{pair}/spot",
                    headers={"User-Agent": "AegisQuant/0.1"},
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                amt = float(data["data"]["amount"])
                if amt > 0:
                    out[sym] = amt
            except Exception:
                continue
        return out

    def _fetch_live_equity_quotes(self, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for sym in symbols:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 AegisQuant/0.1"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                meta = data["chart"]["result"][0]["meta"]
                price = float(meta.get("regularMarketPrice", 0.0))
                if price > 0:
                    out[sym] = price
            except Exception:
                continue
        return out

    def _fetch_all_rss_news(self) -> list[NewsEvent]:
        events: list[NewsEvent] = []
        for url, source_name, tier in self.rss_feeds:
            try:
                fetched = self.news_engine.fetch_live_rss_items(url, timeout=self.timeout)
                for ev in fetched[:5]:
                    ev.source_name = source_name
                    ev.source_tier = tier
                    events.append(ev)
            except Exception:
                continue
        return events
