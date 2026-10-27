"""Broker integration adapters, featuring a first-class Trading 212 Public REST API client.

Trading 212 Official API Capabilities & Constraints:
- Practice (Demo) Base URL: https://demo.trading212.com/api/v0
- Live Base URL:            https://live.trading212.com/api/v0
- Scope: Trading 212's official API supports Invest & ISA (Equities and ETFs) accounts only.
  It does NOT support CFD / Forex / Crypto accounts via API.
- Authentication: HTTP header `Authorization: <API_KEY>` (generated in Trading 212 App -> Settings -> API (Beta)).
- Order Types: Market (`/equity/orders/market`), Limit (`/equity/orders/limit`),
  Stop (`/equity/orders/stop`), Stop-Limit (`/equity/orders/stop_limit`).
- Selling: In Trading 212 Invest/ISA accounts, selling uses a negative `quantity` on `/equity/orders/market`
  to close or reduce an existing long position (short-selling is not allowed on Invest/ISA accounts).
"""

from __future__ import annotations

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


# Default mapping from Aegis canonical symbols to Trading 212 Invest instrument tickers.
# Users on UK/EU accounts can override these (e.g. SPY -> VUAA_UK_EQ or CSPX_UK_EQ) via env/config.
DEFAULT_T212_TICKER_MAP: dict[str, str] = {
    "SPY": "SPY_US_EQ",
    "QQQ": "QQQ_US_EQ",
    "MSFT": "MSFT_US_EQ",
    "NVDA": "NVDA_US_EQ",
    "SHY": "SHY_US_EQ",
}


@dataclass
class ExternalOrderReceipt:
    broker: str
    environment: str
    symbol: str
    broker_ticker: str
    side: str
    quantity: float
    notional_usd: float
    status: str
    broker_order_id: str
    stop_order_id: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Trading212BrokerClient:
    """Official Trading 212 v0 REST API client with built-in rate-limit throttling and safety gates."""

    DEMO_BASE_URL = "https://demo.trading212.com/api/v0"
    LIVE_BASE_URL = "https://live.trading212.com/api/v0"

    def __init__(
        self,
        api_key: str | None = None,
        environment: BrokerEnvironment = BrokerEnvironment.TRADING212_DEMO,
        ticker_map: dict[str, str] | None = None,
        min_request_interval_sec: float = 1.1,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = (api_key or os.environ.get("TRADING212_API_KEY", "")).strip()
        self.environment = environment
        self.ticker_map = dict(DEFAULT_T212_TICKER_MAP)
        if ticker_map:
            self.ticker_map.update(ticker_map)
        self.min_request_interval_sec = min_request_interval_sec
        self.timeout = timeout
        self._last_req_ts = 0.0

    @property
    def base_url(self) -> str:
        if self.environment == BrokerEnvironment.TRADING212_LIVE:
            return self.LIVE_BASE_URL
        return self.DEMO_BASE_URL

    def is_configured(self) -> bool:
        return len(self.api_key) > 0

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
            "Authorization": self.api_key,
            "Accept": "application/json",
            "User-Agent": "AegisQuant/0.1",
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

    # --- Account & Portfolio Inspection ---

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

    # --- Order Execution ---

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

    def execute_risk_approved_signal(
        self,
        signal: StrategySignal,
        decision: RiskDecision,
        reference_price: float,
        dry_run_if_unconfigured: bool = True,
    ) -> ExternalOrderReceipt:
        """Translate a RiskGuard-approved Aegis signal into a Trading 212 Invest/Practice order."""
        if not decision.approved or decision.approved_notional_usd <= 0:
            return ExternalOrderReceipt(
                broker="Trading212",
                environment=self.environment.value,
                symbol=signal.symbol,
                broker_ticker="",
                side=signal.side.value,
                quantity=0.0,
                notional_usd=0.0,
                status="REJECTED_BY_RISK_GUARD",
                broker_order_id="",
                stop_order_id=None,
                message="; ".join(decision.rejection_reasons) or "Not approved",
            )

        # Trading 212 official API only supports Equities & ETFs (Invest/ISA), not Forex or Crypto CFDs
        if signal.asset_class not in (AssetClass.EQUITY, AssetClass.CASH):
            return ExternalOrderReceipt(
                broker="Trading212",
                environment=self.environment.value,
                symbol=signal.symbol,
                broker_ticker="",
                side=signal.side.value,
                quantity=0.0,
                notional_usd=0.0,
                status="UNSUPPORTED_ASSET_CLASS_ON_T212_API",
                broker_order_id="",
                stop_order_id=None,
                message=(
                    f"Trading 212 official API only supports Equities/ETFs (Invest/ISA), not {signal.asset_class.value}. "
                    "Routed to internal PaperBroker (use OANDA v20 for Forex or Alpaca/Kraken for Crypto)."
                ),
            )

        if signal.side != OrderSide.BUY:
            return ExternalOrderReceipt(
                broker="Trading212",
                environment=self.environment.value,
                symbol=signal.symbol,
                broker_ticker="",
                side=signal.side.value,
                quantity=0.0,
                notional_usd=0.0,
                status="SKIPPED_SHORT_ON_INVEST_ACCOUNT",
                broker_order_id="",
                stop_order_id=None,
                message="Trading 212 Invest/ISA accounts are long-only; short signals are skipped.",
            )

        t212_ticker = self.ticker_map.get(signal.symbol, f"{signal.symbol}_US_EQ")
        qty = round(decision.approved_notional_usd / max(1e-6, reference_price), 4)

        if not self.is_configured():
            if dry_run_if_unconfigured:
                return ExternalOrderReceipt(
                    broker="Trading212",
                    environment=self.environment.value,
                    symbol=signal.symbol,
                    broker_ticker=t212_ticker,
                    side=signal.side.value,
                    quantity=qty,
                    notional_usd=decision.approved_notional_usd,
                    status="DRY_RUN_READY",
                    broker_order_id="dry-run-no-api-key",
                    stop_order_id=None,
                    message=(
                        f"Validated payload for {self.base_url}/equity/orders/market "
                        f"(ticker={t212_ticker}, quantity={qty}, stop={decision.stop_loss_price:.2f}). "
                        "Set TRADING212_API_KEY to transmit to Trading 212 Demo/Live."
                    ),
                )
            raise ValueError("TRADING212_API_KEY is not configured.")

        mkt_resp = self.place_market_order(ticker=t212_ticker, quantity=qty)
        order_id = str(mkt_resp.get("id", "submitted"))

        stop_id: str | None = None
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
            side=signal.side.value,
            quantity=qty,
            notional_usd=decision.approved_notional_usd,
            status="SUBMITTED",
            broker_order_id=order_id,
            stop_order_id=stop_id,
            message=f"Order submitted to Trading 212 ({self.environment.value}).",
        )
