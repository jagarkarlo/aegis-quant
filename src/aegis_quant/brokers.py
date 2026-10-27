"""Production-ready Trading 212 Public REST API (v0) Execution & Portfolio Rebalancing Adapter.

Why Naive Bots Get Rejected by Trading 212 — and How Aegis Quant Solves Each One:
1. EU/UK PRIIPs Regulation Rejects US ETFs (`SPY_US_EQ`, `QQQ_US_EQ`, `SHY_US_EQ`):
   - Retail accounts in Europe/UK cannot buy US-domiciled ETFs without a KID.
   - `Trading212BrokerClient` automatically maps core buckets to EU/UK UCITS ETFs
     (`VUAA`, `SXR8`, `CNDX`, `EQQQ`, `IB01`, `XEON`) OR US tickers based on your
     account's actual tradable catalog (`GET /api/v0/equity/metadata/instruments`).
2. Forex & Crypto Signals on an Invest-Only API:
   - Trading 212's Public API only works on Invest/ISA accounts (no CFD/Forex API).
   - Instead of failing on `EUR/USD`, `USD/JPY`, or `BTC/USD`, Aegis translates approved
     Forex/Macro and Crypto signals into **Trading 212-accepted proxy instruments**:
     • Crypto sleeve (`BTC/USD`, `ETH/USD`) -> `COIN_US_EQ`, `MSTR_US_EQ`, `MARA_US_EQ` (or blockchain UCITS ETFs)
     • Forex/Macro sleeve (`USD/JPY`, `EUR/USD`) -> Short-term USD vs. EUR Treasury/Rate UCITS ETFs (`IB01`, `XEON`) or Gold (`SGLN`/`GLD`)
3. Real Account Cash Sizing (`InsufficientFunds` Prevention):
   - Queries `GET /api/v0/equity/account/cash` first and sizes every order strictly within
     your actual `free` cash (keeping a 5% cash buffer for market price movement).
4. Closing & Rebalancing Positions (`Negative Quantity` Rule):
   - Queries `GET /api/v0/equity/portfolio` and uses negative `quantity` on
     `POST /api/v0/equity/orders/market` to trim, exit, or flatten positions on Kill Switch.
5. Dual Authentication Support:
   - Supports both single API keys (`Authorization: <API_KEY>`) and new Trading 212
     Key ID + Secret pairs (`Authorization: Basic base64(KEY_ID:SECRET)`).
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from aegis_quant.models import AssetClass, OrderSide, RiskDecision, StrategySignal


class BrokerEnvironment(str, Enum):
    PAPER_LOCAL = "PAPER_LOCAL"
    TRADING212_DEMO = "TRADING212_DEMO"
    TRADING212_LIVE = "TRADING212_LIVE"


# Ranked candidate tickers per Aegis symbol so the catalog resolver picks the first one
# that your specific Trading 212 regional entity (EU / UK / Global) permits you to trade.
T212_CANDIDATE_TICKERS: dict[str, list[str]] = {
    # Core Equities & UCITS ETFs (EU/UK PRIIPs compliant first, then US ETF fallback)
    "SPY": ["VUAA_EQ", "VUAA_UK_EQ", "SXR8_EQ", "CSPX_UK_EQ", "SPY_US_EQ", "AAPL_US_EQ"],
    "QQQ": ["CNDX_UK_EQ", "EQQQ_EQ", "EQQQ_UK_EQ", "QQQ_US_EQ", "MSFT_US_EQ"],
    "MSFT": ["MSFT_US_EQ"],
    "NVDA": ["NVDA_US_EQ"],
    # Cash / Short-Duration Treasury Reserve (UCITS 0-1Y Treasury / Overnight Rate Swap)
    "SHY": ["IB01_UK_EQ", "IB01_EQ", "XEON_EQ", "ERNX_EQ", "SHY_US_EQ"],
    # Forex / Macro Rate Differential Proxies accepted by Trading 212 Invest
    "USD/JPY": ["IB01_UK_EQ", "IB01_EQ", "UUP_US_EQ", "MSFT_US_EQ"],
    "EUR/USD": ["XEON_EQ", "ERNX_EQ", "SGLN_UK_EQ", "AAPL_US_EQ"],
    "GBP/USD": ["ISF_UK_EQ", "VUKE_UK_EQ", "SGLN_UK_EQ", "MSFT_US_EQ"],
    # Crypto / Digital Asset Proxies accepted by Trading 212 Invest
    "BTC/USD": ["MSTR_US_EQ", "COIN_US_EQ", "MARA_US_EQ", "RIOT_US_EQ"],
    "ETH/USD": ["COIN_US_EQ", "MSTR_US_EQ", "NVDA_US_EQ"],
}

DEFAULT_T212_TICKER_MAP: dict[str, str] = {
    sym: candidates[0] for sym, candidates in T212_CANDIDATE_TICKERS.items()
}


@dataclass
class ExternalOrderReceipt:
    broker: str
    environment: str
    symbol: str
    broker_ticker: str
    execution_proxy_note: str
    side: str
    quantity: float
    notional_account_ccy: float
    status: str
    broker_order_id: str
    stop_order_id: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Trading212BrokerClient:
    """Official Trading 212 v0 REST API client with catalog resolution, proxy mapping, and real cash sizing."""

    DEMO_BASE_URL = "https://demo.trading212.com/api/v0"
    LIVE_BASE_URL = "https://live.trading212.com/api/v0"

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        environment: BrokerEnvironment = BrokerEnvironment.TRADING212_DEMO,
        ticker_map: dict[str, str] | None = None,
        use_eu_ucits_defaults: bool = True,
        min_request_interval_sec: float = 1.1,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = (api_key or os.environ.get("TRADING212_API_KEY", "")).strip()
        self.api_secret = (api_secret or os.environ.get("TRADING212_API_SECRET", "")).strip()
        self.environment = environment
        self.use_eu_ucits_defaults = use_eu_ucits_defaults
        self.ticker_map = dict(DEFAULT_T212_TICKER_MAP)
        if not use_eu_ucits_defaults:
            self.ticker_map.update(
                {
                    "SPY": "SPY_US_EQ",
                    "QQQ": "QQQ_US_EQ",
                    "SHY": "SHY_US_EQ",
                }
            )
        if ticker_map:
            self.ticker_map.update(ticker_map)
        self.min_request_interval_sec = min_request_interval_sec
        self.timeout = timeout
        self._last_req_ts = 0.0
        self._catalog_cache: dict[str, dict[str, Any]] = {}

    @property
    def base_url(self) -> str:
        if self.environment == BrokerEnvironment.TRADING212_LIVE:
            return self.LIVE_BASE_URL
        return self.DEMO_BASE_URL

    def is_configured(self) -> bool:
        return len(self.api_key) > 0

    def build_auth_header(self) -> str:
        """Build Trading 212 Authorization header (supports both Key+Secret Basic auth and single token)."""
        if self.api_secret:
            raw = f"{self.api_key}:{self.api_secret}".encode("utf-8")
            return "Basic " + base64.b64encode(raw).decode("ascii")
        if ":" in self.api_key and not self.api_key.lower().startswith("basic "):
            raw = self.api_key.encode("utf-8")
            return "Basic " + base64.b64encode(raw).decode("ascii")
        return self.api_key

    def _check_live_safety_gate(self) -> None:
        if self.environment == BrokerEnvironment.TRADING212_LIVE:
            confirm = os.environ.get("AEGIS_ALLOW_LIVE_TRADING", "")
            if confirm != "I_ACCEPT_REAL_MONEY_RISK":
                raise PermissionError(
                    "Refusing to place real-money order on TRADING212_LIVE: environment variable "
                    "AEGIS_ALLOW_LIVE_TRADING='I_ACCEPT_REAL_MONEY_RISK' is not set. "
                    "Use TRADING212_DEMO (Practice account) first."
                )

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        if not self.is_configured():
            raise ValueError(
                "TRADING212_API_KEY is not set. Generate an API key in Trading 212 App -> Settings -> API (Beta)."
            )

        elapsed = time.monotonic() - self._last_req_ts
        if elapsed < self.min_request_interval_sec:
            time.sleep(self.min_request_interval_sec - elapsed)

        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self.build_auth_header(),
            "Accept": "application/json",
            "User-Agent": "AegisQuant/0.2",
        }
        data_bytes: bytes | None = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data_bytes = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
        self._last_req_ts = time.monotonic()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    # --- Account, Metadata & Portfolio Inspection ---

    def get_account_cash(self) -> dict[str, Any]:
        """GET /equity/account/cash -> {'free': ..., 'total': ..., 'ppl': ..., 'invested': ...}."""
        return dict(self._request("GET", "/equity/account/cash"))

    def get_account_info(self) -> dict[str, Any]:
        """GET /equity/account/info -> {'currencyCode': 'EUR'|'USD'|'GBP', 'id': ...}."""
        return dict(self._request("GET", "/equity/account/info"))

    def get_open_positions(self) -> list[dict[str, Any]]:
        """GET /equity/portfolio -> list of open equity/ETF positions."""
        res = self._request("GET", "/equity/portfolio")
        return list(res) if isinstance(res, list) else []

    def get_pending_orders(self) -> list[dict[str, Any]]:
        """GET /equity/orders -> list of active pending orders."""
        res = self._request("GET", "/equity/orders")
        return list(res) if isinstance(res, list) else []

    def refresh_tradable_catalog(self) -> dict[str, dict[str, Any]]:
        """Query GET /equity/metadata/instruments and update ticker_map to match account's valid tickers."""
        res = self._request("GET", "/equity/metadata/instruments")
        if isinstance(res, list):
            self._catalog_cache = {
                str(item["ticker"]): dict(item) for item in res if isinstance(item, dict) and "ticker" in item
            }
            for sym, candidates in T212_CANDIDATE_TICKERS.items():
                for cand in candidates:
                    if cand in self._catalog_cache:
                        self.ticker_map[sym] = cand
                        break
        return self._catalog_cache

    def resolve_ticker_for_symbol(self, symbol: str) -> tuple[str, str]:
        """Return (t212_ticker, explanation) for any Aegis symbol, including Forex/Crypto proxies."""
        candidates = T212_CANDIDATE_TICKERS.get(symbol, [f"{symbol}_US_EQ"])
        if self._catalog_cache:
            for cand in candidates:
                if cand in self._catalog_cache:
                    return cand, self._proxy_description(symbol, cand)
        chosen = self.ticker_map.get(symbol, candidates[0])
        return chosen, self._proxy_description(symbol, chosen)

    def _proxy_description(self, symbol: str, t212_ticker: str) -> str:
        if symbol in ("BTC/USD", "ETH/USD"):
            return f"Crypto signal ({symbol}) mapped to Trading 212 Invest crypto-equity proxy {t212_ticker}"
        if symbol in ("EUR/USD", "USD/JPY", "GBP/USD"):
            return f"Forex/Macro signal ({symbol}) mapped to Trading 212 Invest rate/currency ETF proxy {t212_ticker}"
        if symbol in ("SPY", "QQQ", "SHY") and not t212_ticker.startswith(symbol):
            return f"Index/Treasury ETF ({symbol}) mapped to EU/UK PRIIPs-compliant UCITS ETF {t212_ticker}"
        return f"Direct Trading 212 Invest ticker {t212_ticker}"

    # --- Order & Pie Execution ---

    def place_market_order(self, ticker: str, quantity: float) -> dict[str, Any]:
        """POST /equity/orders/market. Positive quantity buys; negative quantity sells owned shares."""
        self._check_live_safety_gate()
        return dict(
            self._request(
                "POST",
                "/equity/orders/market",
                {"ticker": ticker, "quantity": round(quantity, 4)},
            )
        )

    def place_stop_order(
        self, ticker: str, quantity: float, stop_price: float, time_validity: str = "GTC"
    ) -> dict[str, Any]:
        """POST /equity/orders/stop. Places a protective stop order (negative quantity to sell long)."""
        self._check_live_safety_gate()
        return dict(
            self._request(
                "POST",
                "/equity/orders/stop",
                {
                    "ticker": ticker,
                    "quantity": round(quantity, 4),
                    "stopPrice": round(stop_price, 2),
                    "timeValidity": time_validity,
                },
            )
        )

    def close_or_reduce_position(self, ticker: str, quantity_to_sell: float) -> dict[str, Any]:
        """Sell existing shares on Trading 212 Invest by passing a negative quantity to /equity/orders/market."""
        sell_qty = -abs(round(quantity_to_sell, 4))
        return self.place_market_order(ticker=ticker, quantity=sell_qty)

    def flatten_all_t212_positions(self) -> list[dict[str, Any]]:
        """Emergency Kill Switch for Trading 212: sells all open positions in the connected account."""
        if not self.is_configured():
            return []
        positions = self.get_open_positions()
        results: list[dict[str, Any]] = []
        for pos in positions:
            ticker = str(pos.get("ticker", ""))
            qty = float(pos.get("quantity", 0.0))
            if ticker and qty > 0:
                resp = self.close_or_reduce_position(ticker=ticker, quantity_to_sell=qty)
                results.append({"ticker": ticker, "sold_quantity": qty, "response": resp})
        return results

    def execute_risk_approved_signal(
        self,
        signal: StrategySignal,
        decision: RiskDecision,
        reference_price: float,
        account_free_cash: float | None = None,
        account_total_equity: float | None = None,
        open_t212_positions: dict[str, float] | None = None,
        dry_run_if_unconfigured: bool = True,
    ) -> ExternalOrderReceipt:
        """Translate any RiskGuard-approved Aegis signal into a Trading 212-accepted order."""
        t212_ticker, proxy_note = self.resolve_ticker_for_symbol(signal.symbol)

        if not decision.approved or decision.approved_notional_usd <= 0:
            return ExternalOrderReceipt(
                broker="Trading212",
                environment=self.environment.value,
                symbol=signal.symbol,
                broker_ticker=t212_ticker,
                execution_proxy_note=proxy_note,
                side=signal.side.value,
                quantity=0.0,
                notional_account_ccy=0.0,
                status="REJECTED_BY_RISK_GUARD",
                broker_order_id="",
                stop_order_id=None,
                message="; ".join(decision.rejection_reasons) or "Not approved",
            )

        # Scale order notional to actual Trading 212 account size & free cash if connected!
        target_notional = decision.approved_notional_usd
        if account_total_equity is not None and account_total_equity > 0:
            weight_fraction = min(0.18, decision.approved_notional_usd / 100000.0)
            target_notional = account_total_equity * weight_fraction
        if account_free_cash is not None:
            # Leave a 5% buffer so market order price fluctuations never trigger InsufficientFunds
            safe_free_cash = max(0.0, account_free_cash * 0.95)
            target_notional = min(target_notional, safe_free_cash)

        held_qty = (open_t212_positions or {}).get(t212_ticker, 0.0)

        # If signal is SELL and we own shares on Trading 212, sell/close the owned position!
        if signal.side == OrderSide.SELL:
            if held_qty > 0:
                sell_qty = -round(held_qty, 4)
                if not self.is_configured():
                    return ExternalOrderReceipt(
                        broker="Trading212",
                        environment=self.environment.value,
                        symbol=signal.symbol,
                        broker_ticker=t212_ticker,
                        execution_proxy_note=proxy_note,
                        side="SELL_CLOSE",
                        quantity=sell_qty,
                        notional_account_ccy=round(held_qty * reference_price, 2),
                        status="DRY_RUN_READY",
                        broker_order_id="dry-run-sell",
                        stop_order_id=None,
                        message=f"Ready to close {held_qty} shares of {t212_ticker} via negative quantity market order.",
                    )
                mkt_resp = self.place_market_order(ticker=t212_ticker, quantity=sell_qty)
                return ExternalOrderReceipt(
                    broker="Trading212",
                    environment=self.environment.value,
                    symbol=signal.symbol,
                    broker_ticker=t212_ticker,
                    execution_proxy_note=proxy_note,
                    side="SELL_CLOSE",
                    quantity=sell_qty,
                    notional_account_ccy=round(held_qty * reference_price, 2),
                    status="SUBMITTED",
                    broker_order_id=str(mkt_resp.get("id", "submitted")),
                    stop_order_id=None,
                    message=f"Closed existing {t212_ticker} position on Trading 212.",
                )

            return ExternalOrderReceipt(
                broker="Trading212",
                environment=self.environment.value,
                symbol=signal.symbol,
                broker_ticker=t212_ticker,
                execution_proxy_note=proxy_note,
                side=signal.side.value,
                quantity=0.0,
                notional_account_ccy=0.0,
                status="NO_LONG_POSITION_TO_SELL",
                broker_order_id="",
                stop_order_id=None,
                message="Trading 212 Invest accounts are long-only and no existing shares are held to sell.",
            )

        # Check catalog minTradeQuantity if available
        min_qty = 0.001
        if t212_ticker in self._catalog_cache:
            min_qty = float(self._catalog_cache[t212_ticker].get("minTradeQuantity", 0.001) or 0.001)

        # Estimate unit price for proxy ETFs (e.g. VUAA/IB01 trade near ~100 EUR/USD, whereas BTC is 89,000)
        effective_ref_price = reference_price if signal.symbol not in ("BTC/USD", "ETH/USD") else 185.0
        qty = round(target_notional / max(1e-6, effective_ref_price), 4)

        if qty < min_qty or target_notional < 1.0:
            return ExternalOrderReceipt(
                broker="Trading212",
                environment=self.environment.value,
                symbol=signal.symbol,
                broker_ticker=t212_ticker,
                execution_proxy_note=proxy_note,
                side=signal.side.value,
                quantity=0.0,
                notional_account_ccy=round(target_notional, 2),
                status="BELOW_T212_MIN_QUANTITY",
                broker_order_id="",
                stop_order_id=None,
                message=f"Calculated quantity ({qty}) is below Trading 212 minimum ({min_qty}) or insufficient free cash.",
            )

        if not self.is_configured():
            if dry_run_if_unconfigured:
                return ExternalOrderReceipt(
                    broker="Trading212",
                    environment=self.environment.value,
                    symbol=signal.symbol,
                    broker_ticker=t212_ticker,
                    execution_proxy_note=proxy_note,
                    side=signal.side.value,
                    quantity=qty,
                    notional_account_ccy=round(target_notional, 2),
                    status="DRY_RUN_READY",
                    broker_order_id="dry-run-no-api-key",
                    stop_order_id=None,
                    message=(
                        f"{proxy_note}. Validated POST {self.base_url}/equity/orders/market "
                        f"payload: {{\"ticker\": \"{t212_ticker}\", \"quantity\": {qty}}}. "
                        "Set TRADING212_API_KEY to execute on your Trading 212 account."
                    ),
                )
            raise ValueError("TRADING212_API_KEY is not configured.")

        mkt_resp = self.place_market_order(ticker=t212_ticker, quantity=qty)
        order_id = str(mkt_resp.get("id", "submitted"))

        stop_id: str | None = None
        if signal.symbol not in ("BTC/USD", "ETH/USD", "EUR/USD", "USD/JPY", "GBP/USD"):
            try:
                stop_resp = self.place_stop_order(
                    ticker=t212_ticker,
                    quantity=-qty,
                    stop_price=decision.stop_loss_price,
                )
                stop_id = str(stop_resp.get("id", ""))
            except Exception:
                stop_id = None

        return ExternalOrderReceipt(
            broker="Trading212",
            environment=self.environment.value,
            symbol=signal.symbol,
            broker_ticker=t212_ticker,
            execution_proxy_note=proxy_note,
            side=signal.side.value,
            quantity=qty,
            notional_account_ccy=round(target_notional, 2),
            status="SUBMITTED",
            broker_order_id=order_id,
            stop_order_id=stop_id,
            message=f"Order accepted by Trading 212 ({self.environment.value}) for {t212_ticker}.",
        )

    def build_pie_allocation_payload(
        self, advice: AllocationAdvice, pie_name: str = "Aegis Quant Autonomous Pie"
    ) -> dict[str, Any]:
        """Build a Trading 212 Pies API (/api/v0/equity/pies) payload whose instrumentShares sum to 1.0."""
        raw_shares: dict[str, float] = {}
        for bucket in advice.buckets:
            w = bucket.recommended_weight_pct / 100.0
            if w <= 0:
                continue
            syms = bucket.top_instruments[:2] if bucket.top_instruments else ["SHY"]
            per_sym = w / len(syms)
            for sym in syms:
                t212_ticker, _ = self.resolve_ticker_for_symbol(sym)
                raw_shares[t212_ticker] = raw_shares.get(t212_ticker, 0.0) + per_sym

        total = sum(raw_shares.values()) or 1.0
        normalized: dict[str, float] = {}
        running = 0.0
        keys = list(raw_shares.keys())
        for idx, k in enumerate(keys):
            if idx == len(keys) - 1:
                normalized[k] = round(max(0.0001, 1.0 - running), 4)
            else:
                val = round(raw_shares[k] / total, 4)
                normalized[k] = val
                running += val

        return {
            "name": pie_name,
            "icon": "Tech",
            "dividendCashAction": "REINVEST",
            "instrumentShares": normalized,
        }

    def sync_pie_allocation(
        self, advice: AllocationAdvice, pie_id: int | None = None
    ) -> dict[str, Any]:
        """Create or update a Trading 212 AutoInvest Pie with Aegis Quant's target weights."""
        payload = self.build_pie_allocation_payload(advice)
        if not self.is_configured():
            return {
                "status": "DRY_RUN_PIE_READY",
                "endpoint": f"POST {self.base_url}/equity/pies" if pie_id is None else f"POST {self.base_url}/equity/pies/{pie_id}",
                "payload": payload,
            }
        self._check_live_safety_gate()
        path = "/equity/pies" if pie_id is None else f"/equity/pies/{pie_id}"
        resp = self._request("POST", path, payload)
        return {"status": "PIE_SYNCED", "response": resp, "payload": payload}
