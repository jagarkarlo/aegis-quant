# Integrating Aegis Quant with Trading 212 (Step-by-Step Guide)

Aegis Quant includes a built-in, zero-dependency adapter (`src/aegis_quant/brokers.py` -> `Trading212BrokerClient`) for the **official Trading 212 Public REST API (`v0`)**.

---

## 1. Important Realities About Trading 212's Official API

Before connecting your account, understand what Trading 212's API officially supports and what it blocks:

| Capability | Trading 212 Official API (`v0`) | How Aegis Quant Handles It |
|---|---|---|
| **Invest & ISA Accounts (Stocks & ETFs)** | **Supported** (`demo.trading212.com` & `live.trading212.com`) | Directly routes approved `SPY`, `QQQ`, `MSFT`, `NVDA`, and `SHY` (or EU/UK UCITS equivalents) orders and protective stops. |
| **Practice (Demo) Money Mode** | **Supported** (`https://demo.trading212.com/api/v0`) | Default environment (`TRADING212_DEMO`) so you can run Aegis autonomously with virtual funds first. |
| **CFD / Forex / Crypto Accounts** | **Not directly exposed on Trading 212 API** | Trading 212's API only works on **Invest/ISA** accounts. To make Trading 212 accept all 4 asset sleeves, Aegis automatically maps **EU/UK PRIIPs-compliant UCITS ETFs & listed proxies**:<br/>• `SPY` $\to$ `VUAA_EQ` / `SXR8_EQ` (S&P 500 UCITS ETF)<br/>• `QQQ` $\to$ `CNDX_UK_EQ` / `EQQQ_EQ` (Nasdaq-100 UCITS ETF)<br/>• `SHY` $\to$ `IB01_UK_EQ` / `XEON_EQ` (0-1Y Treasury / Overnight Rate UCITS ETF)<br/>• `EUR/USD`, `USD/JPY` $\to$ `XEON_EQ` / `IB01_UK_EQ` (EUR vs. USD Rate/Currency ETFs)<br/>• `BTC/USD`, `ETH/USD` $\to$ `MSTR_US_EQ` / `COIN_US_EQ` (Listed Crypto-Equity Proxies) |
| **Account Cash & Catalog Validation** | **Enforced (`GET /equity/account/cash` & `/equity/metadata/instruments`)** | Queries your real tradable catalog and scales every order to your actual `free` cash (with a 5% buffer so orders are never rejected for `InsufficientFunds`). |
| **AutoInvest Pies API** | **Supported (`POST /api/v0/equity/pies`)** | `build_pie_allocation_payload()` & `sync_pie_allocation()` generate a valid Trading 212 Pie with `instrumentShares` summing to `1.0000`. |
| **Short Selling** | **Not allowed on Invest/ISA** | `Trading212BrokerClient` automatically skips `SELL` (short-entry) signals and only uses negative `quantity` to sell/close shares you already own. |

---

## 2. How to Generate Your Trading 212 API Key

1. Open the **Trading 212** app (mobile or web) and switch to **Practice Mode (Trading 212 Invest)** first.
2. Go to **Settings** $\to$ **API (Beta)** $\to$ **Generate API Key**.
3. Select ONLY the permissions needed:
   - `Account data` (read cash & account currency)
   - `Portfolio` (read open positions)
   - `Orders` (place/cancel market & stop orders)
   - *(Never grant withdrawal permissions—Trading 212's API does not allow withdrawals by design).*
4. Copy the generated token and export it in your shell:

```bash
export TRADING212_API_KEY="your_trading212_api_key_here"
```

---

## 3. Running Aegis Quant with Trading 212

### Step A: Dry-Run / Practice Account Sync (`t212-sync`)
Even without `TRADING212_API_KEY` set, you can preview the exact Trading 212 order payloads that pass `IndependentRiskGuard`:

```bash
PYTHONPATH=src python3 -m aegis_quant.cli t212-sync --env demo --live-feeds
```

When `TRADING212_API_KEY` is exported, `t212-sync --env demo` transmits approved Equity/ETF orders directly to `https://demo.trading212.com/api/v0/equity/orders/market` and attaches a protective stop order via `/api/v0/equity/orders/stop`.

### Step B: 24/7 Autonomous Self-Managing Daemon (`daemon`)
To run the full loop continuously (live RSS news + live market quotes + regime detection + non-bypassable risk guard + Trading 212 Demo execution + post-mortem self-learning):

```bash
PYTHONPATH=src python3 -m aegis_quant.cli --db aegis_state.db daemon \
  --interval 60 \
  --max-cycles 0 \
  --live-feeds \
  --t212
```

- `--db aegis_state.db` persists all positions, trades, Brier calibration scores, mistake post-mortems, and `LearnedRule` guardrails across restarts.
- `--max-cycles 0` runs continuously until stopped (or until the automatic drawdown circuit breaker / kill switch halts trading).

### Step C: Double Safety Gate for Live Money (`--env live`)
Aegis Quant will **refuse** to send orders to `https://live.trading212.com/api/v0` unless you explicitly set both `--env live` AND the confirmation environment variable:

```bash
export TRADING212_API_KEY="your_live_key"
export AEGIS_ALLOW_LIVE_TRADING="I_ACCEPT_REAL_MONEY_RISK"
PYTHONPATH=src python3 -m aegis_quant.cli --db aegis_live.db t212-sync --env live --live-feeds
```

---

## 4. Customizing Ticker Mapping for EU / UK Accounts

In EU/UK Trading 212 Invest accounts, US ETFs (`SPY`, `QQQ`, `SHY`) are typically traded via UCITS equivalents (such as `VUAA_UK_EQ`, `EQQQ_UK_EQ`, `IB01_UK_EQ`), while US stocks (`MSFT_US_EQ`, `NVDA_US_EQ`) trade directly. You can customize `DEFAULT_T212_TICKER_MAP` in `src/aegis_quant/brokers.py` or pass a `ticker_map` dictionary to `Trading212BrokerClient`.
