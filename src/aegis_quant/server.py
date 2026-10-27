"""Interactive Web Control Center & REST API for Aegis Quant."""

from __future__ import annotations

import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from aegis_quant.backtester import WalkForwardBacktester
from aegis_quant.engine import AutonomousQuantEngine
from aegis_quant.models import SourceTier

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Aegis Quant — Autonomous Multi-Asset Intelligence & Self-Learning Terminal</title>
<style>
  :root {
    --bg: #0b0f17;
    --panel: #131b2b;
    --border: #23314d;
    --text: #e6edf7;
    --muted: #8ea0c0;
    --accent: #38bdf8;
    --green: #22c55e;
    --red: #ef4444;
    --amber: #f59e0b;
    --purple: #a855f7;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.45;
  }
  header {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    padding: 16px 24px;
    border-bottom: 1px solid var(--border);
    background: #0e1524;
    gap: 12px;
  }
  .brand h1 { margin: 0; font-size: 1.25rem; letter-spacing: 0.4px; }
  .brand p { margin: 2px 0 0; font-size: 0.82rem; color: var(--muted); }
  .controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  button {
    background: #1e293b;
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 13px;
    font-size: 0.83rem;
    cursor: pointer;
    font-weight: 600;
  }
  button:hover { border-color: var(--accent); }
  button.primary { background: #0284c7; border-color: #38bdf8; }
  button.danger { background: #7f1d1d; border-color: #ef4444; }
  .container { padding: 20px 24px; max-width: 1550px; margin: 0 auto; }
  .kpi-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    gap: 12px;
    margin-bottom: 18px;
  }
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 16px;
  }
  .kpi-label { font-size: 0.76rem; text-transform: uppercase; color: var(--muted); }
  .kpi-val { font-size: 1.45rem; font-weight: 700; margin-top: 4px; }
  .verdict-banner {
    background: linear-gradient(90deg, #112239, #162035);
    border-left: 4px solid var(--accent);
    padding: 14px 18px;
    border-radius: 8px;
    margin-bottom: 18px;
  }
  .grid-2 {
    display: grid;
    grid-template-columns: 1.15fr 0.85fr;
    gap: 16px;
    margin-bottom: 18px;
  }
  @media (max-width: 1024px) { .grid-2 { grid-template-columns: 1fr; } }
  h2 { margin: 0 0 12px; font-size: 1rem; color: var(--accent); }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid #1d2840; }
  th { color: var(--muted); font-weight: 600; }
  .badge {
    display: inline-block;
    padding: 2px 7px;
    border-radius: 99px;
    font-size: 0.72rem;
    font-weight: 700;
  }
  .b-green { background: rgba(34,197,94,0.18); color: #4ade80; }
  .b-red { background: rgba(239,68,68,0.18); color: #f87171; }
  .b-amber { background: rgba(245,158,11,0.18); color: #fbbf24; }
  .b-purple { background: rgba(168,85,247,0.18); color: #c084fc; }
  .input-row { display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
  input, select {
    background: #0d1422;
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 7px 10px;
    font-size: 0.83rem;
  }
</style>
</head>
<body>
<header>
  <div class="brand">
    <h1>AEGIS QUANT // Autonomous Multi-Asset Intelligence & Self-Correcting Engine</h1>
    <p>Continuous News Verification • Equities vs. Forex vs. Crypto vs. Cash Advisor • Non-Bypassable Risk Guardrails</p>
  </div>
  <div class="controls">
    <button class="primary" onclick="stepCycles(1)">+1 Autonomous Tick</button>
    <button onclick="stepCycles(10)">+10 Ticks (Fast-Forward)</button>
    <button onclick="toggleAutoLoop()" id="autoBtn">Start Live 2s Loop</button>
    <button onclick="runBacktest()">Run Walk-Forward Backtest</button>
    <button class="danger" onclick="toggleKillSwitch()" id="killBtn">Engage Kill Switch</button>
  </div>
</header>

<div class="container">
  <div class="kpi-row" id="kpiRow"></div>
  <div class="verdict-banner" id="verdictBanner">Loading cross-asset allocation intelligence...</div>

  <div class="grid-2">
    <div class="card">
      <h2>Where Should Money Go Right Now? (Companies vs. Forex vs. Crypto vs. Cash)</h2>
      <table>
        <thead>
          <tr>
            <th>Asset Bucket</th>
            <th>Target Weight</th>
            <th>Net Edge (bps)</th>
            <th>Ann. Vol</th>
            <th>Top Vehicles</th>
            <th>Why / Key Risk</th>
          </tr>
        </thead>
        <tbody id="bucketsBody"></tbody>
      </table>
    </div>
    <div class="card">
      <h2>Self-Learning Strategy Experts (Bayesian Bandit + Brier Calibration)</h2>
      <table>
        <thead>
          <tr>
            <th>Expert Strategy</th>
            <th>Weight</th>
            <th>Calib. Slope</th>
            <th>EWMA Brier</th>
            <th>EWMA Net (bps)</th>
            <th>Trades</th>
          </tr>
        </thead>
        <tbody id="expertsBody"></tbody>
      </table>
    </div>
  </div>

  <div class="grid-2">
    <div class="card">
      <h2>Global News & Macro Catalyst Stream (Deduplicated & Rumor-Filtered)</h2>
      <div class="input-row">
        <input id="newsHeadline" style="flex:1;min-width:240px;" placeholder="Inject headline (e.g. 'Fed signals dovish cut as core inflation cools' or 'SEC crackdown on crypto exchange')" />
        <select id="newsTier">
          <option value="1">Tier 1: Official Central Bank / Filing</option>
          <option value="2" selected>Tier 2: Major Financial Wire</option>
          <option value="4">Tier 4: Unverified Social Rumor</option>
        </select>
        <button onclick="injectHeadline()">Ingest Headline</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>Tier</th>
            <th>Headline</th>
            <th>Assets</th>
            <th>Impact</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody id="newsBody"></tbody>
      </table>
    </div>

    <div class="card">
      <h2>Self-Correction Brain: Mistake Post-Mortems & Learned Guardrail Rules</h2>
      <div class="input-row">
        <select id="mistakeCat">
          <option value="COST_EROSION">Simulate Mistake: COST_EROSION (Spread/Swap ate Forex/Crypto profit)</option>
          <option value="NEWS_WHIPSAW">Simulate Mistake: NEWS_WHIPSAW (Headline reversal trap)</option>
          <option value="OVERCONFIDENCE_BIAS">Simulate Mistake: OVERCONFIDENCE_BIAS (Miscalibrated win prob)</option>
          <option value="REGIME_MISMATCH">Simulate Mistake: REGIME_MISMATCH (Trend trade in chop)</option>
        </select>
        <button onclick="simulateMistake()">Trigger Post-Mortem & Learn</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>Rule / Post-Mortem</th>
            <th>Category</th>
            <th>Self-Correcting Action Taken</th>
          </tr>
        </thead>
        <tbody id="learningBody"></tbody>
      </table>
    </div>
  </div>

  <div class="grid-2">
    <div class="card">
      <h2>Open Paper Positions & Non-Bypassable Risk Guardrail Decisions</h2>
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Class</th>
            <th>Side</th>
            <th>Notional</th>
            <th>Entry / Mark</th>
            <th>Stop / Target</th>
            <th>Net Unrealized</th>
          </tr>
        </thead>
        <tbody id="positionsBody"></tbody>
      </table>
    </div>
    <div class="card">
      <h2>Walk-Forward Out-of-Sample Backtest & Recent Closed Trades</h2>
      <div id="backtestBox" style="margin-bottom:10px;font-size:0.82rem;color:var(--muted);">
        Click "Run Walk-Forward Backtest" to compare Aegis Self-Learning vs. 60/40 Benchmark vs. Naive Unfiltered Bot.
      </div>
      <table>
        <thead>
          <tr>
            <th>Trade</th>
            <th>Symbol</th>
            <th>Strategy</th>
            <th>Gross</th>
            <th>Costs</th>
            <th>Net PnL</th>
            <th>Exit</th>
          </tr>
        </thead>
        <tbody id="tradesBody"></tbody>
      </table>
    </div>
  </div>
</div>

<script>
let autoTimer = null;
let killActive = false;

async function fetchStatus() {
  const res = await fetch('/api/status');
  const data = await res.json();
  render(data);
}

async function stepCycles(n) {
  const res = await fetch('/api/step', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({cycles: n})
  });
  await fetchStatus();
}

function toggleAutoLoop() {
  const btn = document.getElementById('autoBtn');
  if (autoTimer) {
    clearInterval(autoTimer);
    autoTimer = null;
    btn.textContent = 'Start Live 2s Loop';
  } else {
    autoTimer = setInterval(() => stepCycles(1), 2000);
    btn.textContent = 'Stop Live Loop';
  }
}

async function toggleKillSwitch() {
  killActive = !killActive;
  await fetch('/api/kill-switch', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({active: killActive})
  });
  await fetchStatus();
}

async function injectHeadline() {
  const hl = document.getElementById('newsHeadline').value.trim();
  const tier = parseInt(document.getElementById('newsTier').value, 10);
  if (!hl) return;
  await fetch('/api/ingest-news', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({headline: hl, source_tier: tier})
  });
  document.getElementById('newsHeadline').value = '';
  await fetchStatus();
}

async function simulateMistake() {
  const cat = document.getElementById('mistakeCat').value;
  await fetch('/api/simulate-mistake', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({category: cat})
  });
  await fetchStatus();
}

async function runBacktest() {
  const box = document.getElementById('backtestBox');
  box.textContent = 'Running 90-bar walk-forward comparison...';
  const res = await fetch('/api/backtest', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({bars: 90})
  });
  const bt = await res.json();
  const a = bt.aegis_self_learning;
  const b = bt.passive_60_40_benchmark;
  const n = bt.unfiltered_naive_bot;
  box.innerHTML = `
    <strong>90-Bar Walk-Forward Results ($100k initial):</strong><br/>
    • <span style="color:#4ade80">Aegis Self-Learning:</span> $${a.final_equity_usd.toLocaleString()} (${a.total_return_pct >= 0 ? '+' : ''}${a.total_return_pct}%, MaxDD ${a.max_drawdown_pct}%, Sharpe ${a.sharpe_ratio}, Learned Rules: ${a.learned_rules_generated})<br/>
    • <span style="color:#38bdf8">60/40 Core Benchmark:</span> $${b.final_equity_usd.toLocaleString()} (${b.total_return_pct >= 0 ? '+' : ''}${b.total_return_pct}%, MaxDD ${b.max_drawdown_pct}%)<br/>
    • <span style="color:#f87171">Naive Unfiltered News Bot:</span> $${n.final_equity_usd.toLocaleString()} (${n.total_return_pct}%, Costs Paid: $${n.total_transaction_costs_usd}, Rumor Losses: $${n.rumor_whipsaw_losses_usd})
  `;
}

function render(data) {
  const p = data.portfolio;
  const adv = data.allocation_advice;
  const learn = data.learning;
  killActive = data.kill_switch_active;
  document.getElementById('killBtn').textContent = killActive ? 'Reset Kill Switch (ACTIVE)' : 'Engage Kill Switch';

  document.getElementById('kpiRow').innerHTML = `
    <div class="card"><div class="kpi-label">Portfolio Equity</div><div class="kpi-val">$${p.equity_usd.toLocaleString()}</div></div>
    <div class="card"><div class="kpi-label">Treasury Cash Reserve</div><div class="kpi-val">$${p.cash_usd.toLocaleString()}</div></div>
    <div class="card"><div class="kpi-label">Peak Drawdown / Limit</div><div class="kpi-val">${p.total_drawdown_pct}% / 6.0%</div></div>
    <div class="card"><div class="kpi-label">Detected Regime</div><div class="kpi-val" style="font-size:1.05rem;color:#38bdf8">${adv ? adv.regime : 'N/A'}</div></div>
    <div class="card"><div class="kpi-label">Learned Guardrail Rules</div><div class="kpi-val">${learn.active_learned_rules_count} Active (Brier: ${learn.average_ewma_brier_score})</div></div>
    <div class="card"><div class="kpi-label">Kill Switch Status</div><div class="kpi-val" style="color:${killActive ? '#ef4444' : '#22c55e'}">${killActive ? 'HALTED' : 'ARMED & HEALTHY'}</div></div>
  `;

  if (adv) {
    document.getElementById('verdictBanner').innerHTML = `
      <strong>Primary Allocation Verdict:</strong> ${adv.primary_verdict}<br/>
      <span style="font-size:0.82rem;color:var(--muted);"><strong>Counter-Thesis & Guardrail:</strong> ${adv.counter_thesis}</span>
    `;
    document.getElementById('bucketsBody').innerHTML = adv.buckets.map(b => `
      <tr>
        <td><strong>${b.asset_class}</strong></td>
        <td><span class="badge ${b.recommended_weight_pct >= 30 ? 'b-green' : 'b-amber'}">${b.recommended_weight_pct}%</span></td>
        <td>${b.expected_net_edge_bps >= 0 ? '+' : ''}${b.expected_net_edge_bps} bps</td>
        <td>${b.annualized_vol_pct}%</td>
        <td>${b.top_instruments.join(', ')}</td>
        <td style="color:var(--muted)">${b.rationale}</td>
      </tr>
    `).join('');
  }

  document.getElementById('expertsBody').innerHTML = learn.expert_weights.map(e => `
    <tr>
      <td><strong>${e.strategy_name}</strong></td>
      <td><span class="badge b-purple">${e.weight.toFixed(3)}x</span></td>
      <td>${e.calibration_slope.toFixed(2)}</td>
      <td>${e.ewma_brier_score.toFixed(3)}</td>
      <td>${e.ewma_net_return_bps >= 0 ? '+' : ''}${e.ewma_net_return_bps.toFixed(1)}</td>
      <td>${e.total_predictions}</td>
    </tr>
  `).join('');

  document.getElementById('newsBody').innerHTML = data.recent_news.slice(0, 8).map(n => `
    <tr>
      <td><span class="badge ${n.source_tier <= 2 ? 'b-green' : 'b-red'}">Tier ${n.source_tier}</span></td>
      <td>${n.headline}</td>
      <td>${n.affected_symbols.join(', ')}</td>
      <td>${n.sentiment_score >= 0 ? '+' : ''}${(n.sentiment_score * n.impact_magnitude_bps).toFixed(1)} bps</td>
      <td>${n.source_tier === 4 ? '<span class="badge b-red">RUMOR IGNORED</span>' : (n.contradicted ? '<span class="badge b-amber">CONTRADICTED</span>' : '<span class="badge b-green">VERIFIED</span>')}</td>
    </tr>
  `).join('');

  const rulesHtml = learn.active_learned_rules.map(r => `
    <tr>
      <td><strong>${r.strategy_name}</strong> (${r.asset_class})</td>
      <td><span class="badge b-amber">${r.mistake_category}</span></td>
      <td>+${r.min_edge_boost_bps} bps hurdle, +${r.min_confidence_boost} prob, ${r.size_multiplier}x size (${r.evidence_count}x)</td>
    </tr>
  `).join('');
  const pmHtml = learn.recent_postmortems.slice(0, 5).map(pm => `
    <tr>
      <td>${pm.symbol} (${pm.trade_id})</td>
      <td><span class="badge b-red">${pm.category}</span></td>
      <td style="color:var(--muted)">${pm.diagnosis}</td>
    </tr>
  `).join('');
  document.getElementById('learningBody').innerHTML = (rulesHtml + pmHtml) || '<tr><td colspan="3" style="color:var(--muted)">No mistakes recorded yet — click "Trigger Post-Mortem & Learn" or advance ticks.</td></tr>';

  document.getElementById('positionsBody').innerHTML = p.positions.map(pos => `
    <tr>
      <td><strong>${pos.symbol}</strong></td>
      <td>${pos.asset_class}</td>
      <td><span class="badge ${pos.side === 'BUY' ? 'b-green' : 'b-amber'}">${pos.side}</span></td>
      <td>$${pos.notional_usd.toLocaleString()}</td>
      <td>${pos.entry_price} → ${pos.current_price}</td>
      <td>${pos.stop_loss_price} / ${pos.take_profit_price}</td>
      <td style="color:${pos.unrealized_pnl_usd >= 0 ? '#4ade80' : '#f87171'}">$${pos.unrealized_pnl_usd.toFixed(2)}</td>
    </tr>
  `).join('') || '<tr><td colspan="7" style="color:var(--muted)">No open positions (holding Treasury Cash Reserve or awaiting high-edge signal).</td></tr>';

  document.getElementById('tradesBody').innerHTML = data.recent_trades.slice(0, 7).map(t => `
    <tr>
      <td>${t.trade_id}</td>
      <td><strong>${t.symbol}</strong></td>
      <td>${t.strategy_name}</td>
      <td>$${t.gross_pnl_usd.toFixed(2)}</td>
      <td style="color:#f87171">-$${t.total_cost_usd.toFixed(2)}</td>
      <td style="color:${t.net_pnl_usd >= 0 ? '#4ade80' : '#f87171'}"><strong>$${t.net_pnl_usd.toFixed(2)}</strong></td>
      <td><span class="badge b-purple">${t.exit_reason}</span></td>
    </tr>
  `).join('') || '<tr><td colspan="7" style="color:var(--muted)">No closed trades yet.</td></tr>';
}

fetchStatus();
</script>
</body>
</html>
"""


class AegisDashboardServer:
    """Thread-safe HTTP server wrapping AutonomousQuantEngine."""

    def __init__(
        self,
        engine: AutonomousQuantEngine,
        host: str = "127.0.0.1",
        port: int = 8765,
        background_interval_sec: float | None = None,
    ) -> None:
        self.engine = engine
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        self._stop_event = threading.Event()
        self._bg_thread: threading.Thread | None = None
        self.background_interval_sec = background_interval_sec
        self._httpd: ThreadingHTTPServer | None = None

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server_ref = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _read_json_body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    return {}
                raw = self.rfile.read(length).decode("utf-8")
                return json.loads(raw) if raw else {}

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/":
                    raw = DASHBOARD_HTML.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return

                if parsed.path == "/api/status":
                    with server_ref.lock:
                        snap = server_ref.engine.full_status_snapshot()
                    self._send_json(snap)
                    return

                self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                body = self._read_json_body()

                if parsed.path == "/api/step":
                    cycles = int(body.get("cycles", 1))
                    with server_ref.lock:
                        res = server_ref.engine.run_cycles(count=min(100, max(1, cycles)))
                    self._send_json({"cycles_executed": len(res), "latest": res[-1]})
                    return

                if parsed.path == "/api/kill-switch":
                    active = bool(body.get("active", True))
                    with server_ref.lock:
                        res = server_ref.engine.set_kill_switch(active=active)
                    self._send_json(res)
                    return

                if parsed.path == "/api/ingest-news":
                    headline = str(body.get("headline", "")).strip()
                    tier_val = int(body.get("source_tier", 2))
                    tier = SourceTier(tier_val) if tier_val in (1, 2, 3, 4) else SourceTier.TIER_2_WIRE
                    with server_ref.lock:
                        ev = server_ref.engine.news_engine.parse_headline_into_event(
                            headline=headline, source_tier=tier
                        )
                        ing = server_ref.engine.news_engine.ingest_batch([ev])
                        if server_ref.engine.latest_bars:
                            ts = next(iter(server_ref.engine.latest_bars.values())).timestamp
                            server_ref.engine.latest_advice = (
                                server_ref.engine.allocator.build_allocation_advice(
                                    timestamp=ts,
                                    bars=server_ref.engine.latest_bars,
                                    expert_states=server_ref.engine.learner.get_expert_states(),
                                    learned_rules=server_ref.engine.storage.list_learned_rules(),
                                )
                            )
                    self._send_json(
                        {
                            "ingested_event": ev.to_dict(),
                            "accepted_count": ing["accepted_count"],
                            "duplicates_dropped": ing["duplicates_dropped"],
                        }
                    )
                    return

                if parsed.path == "/api/simulate-mistake":
                    cat = str(body.get("category", "COST_EROSION"))
                    with server_ref.lock:
                        pm, rule = server_ref.engine.learner.inject_synthetic_mistake(
                            timestamp="2026-10-27T12:00:00+00:00",
                            symbol="EUR/USD" if cat in ("COST_EROSION", "NEWS_WHIPSAW") else "BTC/USD",
                            asset_class_str="FOREX" if cat in ("COST_EROSION", "NEWS_WHIPSAW") else "CRYPTO",
                            strategy_name="MacroNewsCatalyst",
                            regime_str="HIGH_DISPERSION_NEUTRAL",
                            category_str=cat,
                        )
                    self._send_json({"postmortem": pm.to_dict(), "learned_rule": rule.to_dict()})
                    return

                if parsed.path == "/api/backtest":
                    bars_count = int(body.get("bars", 90))
                    bt = WalkForwardBacktester().run_comparison(bars_count=min(300, max(10, bars_count)))
                    self._send_json(bt)
                    return

                self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

        return Handler

    def start_background_worker(self) -> None:
        if self.background_interval_sec is None or self.background_interval_sec <= 0:
            return

        def _loop() -> None:
            while not self._stop_event.is_set():
                with self.lock:
                    self.engine.step_cycle()
                time.sleep(self.background_interval_sec or 2.0)

        self._bg_thread = threading.Thread(target=_loop, daemon=True)
        self._bg_thread.start()

    def serve_forever(self) -> None:
        with self.lock:
            if not self.engine.latest_bars:
                self.engine.run_cycles(count=6)
        self.start_background_worker()
        self._httpd = ThreadingHTTPServer((self.host, self.port), self._make_handler())
        try:
            self._httpd.serve_forever()
        finally:
            self._stop_event.set()
            self._httpd.server_close()
