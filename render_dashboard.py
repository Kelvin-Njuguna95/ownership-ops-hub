#!/usr/bin/env python3
"""Render dashboard_static.html from daily_aggregates.json + pending_queue.json + ww_audit_log.json."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).parent
LOG = HERE / "ww_audit_log.json"
AGG = HERE / "daily_aggregates.json"
PENDING = HERE / "pending_queue.json"
OUT = HERE / "dashboard_static.html"

def load_json(p, default):
    if not p.exists(): return default
    try: return json.loads(p.read_text())
    except Exception: return default

audit = load_json(LOG, {})
aggregates = load_json(AGG, {"date": None, "by_agent": {}, "by_team": {}, "totals": {}})
pending = load_json(PENDING, {"count": 0, "by_team_qa_agent": []})

now_eat = datetime.now(timezone(timedelta(hours=3)))
rendered_at = now_eat.isoformat()

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Windward Ownership QA Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.js"></script>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f7f7f5; color: #1a1a1a; font-size: 14px; line-height: 1.45; }
  .wrap { max-width: 1500px; margin: 0 auto; padding: 18px 22px 48px; }
  h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }
  .sub { color: #6b6b6b; font-size: 13px; margin-bottom: 18px; }
  .controls { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 18px; align-items: center; }
  select { font-family: inherit; font-size: 13px; padding: 7px 12px; border: 1px solid #d8d6cf; background: #fff; border-radius: 6px; color: #1a1a1a; cursor: pointer; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin-bottom: 22px; }
  .kpi { background: #fff; border: 1px solid #ececec; border-radius: 10px; padding: 12px 14px; }
  .kpi .label { font-size: 11px; color: #6b6b6b; text-transform: uppercase; letter-spacing: 0.04em; }
  .kpi .value { font-size: 24px; font-weight: 600; margin-top: 2px; letter-spacing: -0.01em; }
  .kpi .delta { font-size: 11px; color: #6b6b6b; margin-top: 2px; }
  .kpi.alert .value { color: #9b2222; }
  .panel { background: #fff; border: 1px solid #ececec; border-radius: 10px; padding: 16px 18px; margin-bottom: 16px; }
  .panel h2 { font-size: 15px; margin: 0 0 12px; font-weight: 600; display: flex; justify-content: space-between; align-items: center; }
  .panel h2 .meta { font-size: 11px; color: #8a8a8a; font-weight: 400; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 7px 10px; text-align: left; border-bottom: 1px solid #f0eee8; }
  th { font-weight: 600; color: #6b6b6b; background: #fafaf7; text-transform: uppercase; font-size: 11px; letter-spacing: 0.04em; }
  tbody tr:hover { background: #fafaf7; }
  .right { text-align: right; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px; font-weight: 500; }
  .pill.green { background: #def7e0; color: #1f6f2c; }
  .pill.yellow { background: #fff3cd; color: #856404; }
  .pill.red { background: #fde2e2; color: #9b2222; }
  .heatmap { display: grid; gap: 2px; font-size: 11px; overflow-x: auto; }
  .heatmap .cell { padding: 5px 4px; text-align: center; border-radius: 3px; min-height: 24px; font-weight: 500; }
  .heatmap .label { background: transparent; color: #4a4a4a; text-align: left; padding: 5px 6px; font-weight: 500; white-space: nowrap; }
  .heatmap .header { background: transparent; color: #6b6b6b; text-align: center; font-size: 10px; padding: 4px; white-space: nowrap; }
  .legend { display: flex; gap: 12px; align-items: center; margin: 6px 0 12px; font-size: 12px; color: #6b6b6b; flex-wrap: wrap; }
  .legend .swatch { display: inline-block; width: 14px; height: 14px; border-radius: 3px; vertical-align: middle; margin-right: 4px; }
  .empty { padding: 20px; text-align: center; color: #6b6b6b; font-style: italic; }
  .warn { color: #856404; padding: 12px; background: #fff3cd; border-radius: 8px; margin-bottom: 16px; font-size: 13px; }
  .chart-wrap { position: relative; height: 280px; }
  .agent-name { font-weight: 500; }
  .small-mono { font-family: ui-monospace, Menlo, monospace; font-size: 12px; color: #6b6b6b; }
  .silent-flag { background: #fde2e2; color: #9b2222; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .tab-row { display: flex; gap: 4px; margin-bottom: 14px; border-bottom: 1px solid #ececec; flex-wrap: wrap; }
  .tab-row button { background: none; border: none; padding: 8px 14px; cursor: pointer; font-weight: 500; color: #6b6b6b; border-bottom: 2px solid transparent; border-radius: 0; }
  .tab-row button.active { color: #1a1a1a; border-bottom-color: #1f1f1f; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  .arrow { color: #8a8a8a; padding: 0 6px; }
  .companies { font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Windward Ownership QA Dashboard</h1>
  <div class="sub" id="subline"></div>
  <div id="warn" class="warn" style="display:none"></div>

  <div class="controls">
    <label class="small-mono">Team:</label>
    <select id="teamSelect">
      <option value="__all__">All ownership teams</option>
      <option value="Simba">Simba</option>
      <option value="Nyati">Nyati</option>
      <option value="Kobe">Kobe</option>
      <option value="Pweza">Pweza</option>
      <option value="Tembo">Tembo</option>
    </select>
    <span class="small-mono" id="freshness"></span>
  </div>

  <div class="kpis" id="kpis"></div>

  <div class="tab-row" id="tabs">
    <button class="active" data-tab="hourly">Hourly Output</button>
    <button data-tab="pending">Pending QA Queue</button>
    <button data-tab="productivity">Productivity (280)</button>
    <button data-tab="sampling">Sampling (15%)</button>
    <button data-tab="silent">Silent Changes</button>
    <button data-tab="verdicts">Daily Verdicts</button>
  </div>

  <div id="tab-hourly" class="tab-content active">
    <div class="panel">
      <h2>Hourly output per agent <span class="meta" id="hourlyMeta"></span></h2>
      <div class="legend">
        <span><span class="swatch" style="background:#f0eee8"></span>0</span>
        <span><span class="swatch" style="background:#cde8d2"></span>1-20</span>
        <span><span class="swatch" style="background:#7fc189"></span>21-50</span>
        <span><span class="swatch" style="background:#3e8c4f"></span>51-100</span>
        <span><span class="swatch" style="background:#1d5a2c"></span>101+</span>
      </div>
      <div id="heatmap" class="heatmap"></div>
    </div>
    <div class="panel">
      <h2>Team totals by hour</h2>
      <div class="chart-wrap"><canvas id="teamHourChart"></canvas></div>
    </div>
  </div>

  <div id="tab-pending" class="tab-content">
    <div class="panel">
      <h2>Pending QA queue <span class="meta" id="pendingMeta"></span></h2>
      <table id="pendingTable">
        <thead><tr><th>Team</th><th>QA</th><th>Agent</th><th class="right">Pending</th><th class="right">Oldest (h)</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <div id="tab-productivity" class="tab-content">
    <div class="panel">
      <h2>Productivity vs 280 daily minimum</h2>
      <table id="prodTable">
        <thead><tr><th>Team</th><th>Agent</th><th class="right">Tagged today</th><th class="right">vs 280</th><th>Status</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <div id="tab-sampling" class="tab-content">
    <div class="panel">
      <h2>QA sampling rate vs 15% minimum</h2>
      <table id="samplingTable">
        <thead><tr><th>Team</th><th>QA</th><th>Agent</th><th class="right">Sampled</th><th class="right">Of total</th><th class="right">Rate</th><th>Status</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <div id="tab-silent" class="tab-content">
    <div class="panel">
      <h2>Silent QA changes — agent's pick changed, QA marked approve</h2>
      <table id="silentTable">
        <thead><tr><th>Time</th><th>Team</th><th>Agent</th><th>QA</th><th>Agent picked</th><th></th><th>QA changed to</th><th>Flag</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <div id="tab-verdicts" class="tab-content">
    <div class="panel">
      <h2>Daily verdicts</h2>
      <table id="verdictsTable">
        <thead><tr><th>Date</th><th class="right">Agents below 280</th><th class="right">Silent changes</th><th class="right">QA actions</th><th>Locked at</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
</div>

<script>
const AUDIT_LOG = __AUDIT_LOG__;
const AGGREGATES = __AGGREGATES__;
const PENDING = __PENDING__;
const RENDERED_AT = __RENDERED_AT__;

let CHARTS = {};

function escapeHtml(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function teamMatch(t, sel) { return sel === "__all__" || t === sel; }

function render() {
  const team = document.getElementById("teamSelect").value;
  renderKPIs(team);
  renderHeatmap(team);
  renderTeamHourChart(team);
  renderPending(team);
  renderProductivity(team);
  renderSampling(team);
  renderSilent(team);
  renderVerdicts();
}

function renderKPIs(team) {
  const byAgent = AGGREGATES.by_agent || {};
  let tagged = 0, in_bo = 0, qa_actions = 0, silent = 0, activeAgents = 0, agentsBelow = 0;
  const min280 = AGGREGATES.thresholds?.productivity_min || 280;
  for (const name in byAgent) {
    const a = byAgent[name];
    if (!teamMatch(a.team, team)) continue;
    tagged += a.tagged_today || 0;
    in_bo += a.in_bo_qa || 0;
    qa_actions += (a.qa_approved || 0) + (a.qa_changed || 0);
    if ((a.tagged_today || 0) > 0) activeAgents++;
    if (!(a.productivity_met)) agentsBelow++;
  }
  // Silent changes from audit log
  const today = AGGREGATES.date;
  silent = (AUDIT_LOG.qa_events || []).filter(e => e.silent_change === true && (team === "__all__" || e.team === team) && (e.qa_action_at || "").slice(0, 10) === today).length;
  document.getElementById("kpis").innerHTML = `
    <div class="kpi"><div class="label">Pending QA queue</div><div class="value">${in_bo.toLocaleString()}</div><div class="delta">${PENDING.count || 0} total in BO QA</div></div>
    <div class="kpi"><div class="label">Tagged today</div><div class="value">${tagged.toLocaleString()}</div><div class="delta">${activeAgents} active agents</div></div>
    <div class="kpi"><div class="label">QA actions today</div><div class="value">${qa_actions.toLocaleString()}</div><div class="delta">approved + changed</div></div>
    <div class="kpi ${silent>0?'alert':''}"><div class="label">Silent changes</div><div class="value">${silent}</div><div class="delta">unmarked by QA</div></div>
    <div class="kpi ${agentsBelow>0?'alert':''}"><div class="label">Agents under 280</div><div class="value">${agentsBelow}</div><div class="delta">productivity flag</div></div>
    <div class="kpi"><div class="label">Last poll</div><div class="value" style="font-size:14px">${AUDIT_LOG.polling_state?.last_successful_run_at ? new Date(AUDIT_LOG.polling_state.last_successful_run_at).toLocaleString('en-GB', {timeZone:'Africa/Nairobi', hour12:false}) : '-'}</div><div class="delta">poll #${AUDIT_LOG.polling_state?.total_polls || 0}</div></div>
  `;
}

function renderHeatmap(team) {
  const byAgent = AGGREGATES.by_agent || {};
  // Order agents by team in roster order
  const teams = AUDIT_LOG.config?.ownership_teams || [];
  const rows = [];
  for (const t of teams) {
    if (!teamMatch(t, team)) continue;
    const ros = AUDIT_LOG.roster[t];
    for (const a of (ros.members || [])) {
      const data = byAgent[a.name];
      if (!data) continue;
      rows.push({ name: a.name, team: t, hourly: data.hourly || {} });
    }
  }
  const hours = []; for (let i = 6; i <= 23; i++) hours.push(i);
  const grid = document.getElementById("heatmap");
  grid.style.gridTemplateColumns = `220px repeat(${hours.length}, minmax(36px, 1fr)) 60px`;
  let html = '<div class="label">Agent</div>';
  for (const h of hours) html += `<div class="header">${String(h).padStart(2,'0')}h</div>`;
  html += `<div class="header"><strong>Total</strong></div>`;
  let lastTeam = null;
  for (const r of rows) {
    if (r.team !== lastTeam) {
      html += `<div class="label" style="grid-column:1/-1;background:#fafaf7;color:#6b6b6b;font-size:11px;text-transform:uppercase;letter-spacing:0.04em;padding:6px 6px 2px">${escapeHtml(r.team)}</div>`;
      lastTeam = r.team;
    }
    html += `<div class="label">${escapeHtml(r.name)}</div>`;
    let total = 0;
    for (const h of hours) {
      const v = r.hourly[String(h)] || r.hourly[h] || 0;
      total += v;
      html += `<div class="cell" style="background:${heatColor(v)};color:${v>20?'#fff':'#1a1a1a'}">${v || ''}</div>`;
    }
    html += `<div class="cell" style="background:#1f1f1f;color:#fff"><strong>${total}</strong></div>`;
  }
  grid.innerHTML = html;
  document.getElementById("hourlyMeta").textContent = `${rows.length} agents - ${AGGREGATES.date || 'no data'}`;
}

function heatColor(v) {
  if (v === 0) return "#f0eee8";
  if (v <= 20) return "#cde8d2";
  if (v <= 50) return "#7fc189";
  if (v <= 100) return "#3e8c4f";
  return "#1d5a2c";
}

function renderTeamHourChart(team) {
  const byTeam = AGGREGATES.by_team || {};
  const hours = []; for (let i = 6; i <= 23; i++) hours.push(i);
  const labels = hours.map(h => String(h).padStart(2, '0') + 'h');
  const palette = { Simba: "#1f77b4", Nyati: "#ff7f0e", Kobe: "#2ca02c", Pweza: "#d62728", Tembo: "#9467bd" };
  const datasets = (AUDIT_LOG.config?.ownership_teams || []).filter(t => teamMatch(t, team)).map(t => {
    const tdata = byTeam[t] || {};
    const hourly = tdata.hourly || {};
    return {
      label: t,
      data: hours.map(h => hourly[String(h)] || hourly[h] || 0),
      borderColor: palette[t] || "#666",
      backgroundColor: (palette[t] || "#666") + "22",
      tension: 0.25, pointRadius: 3
    };
  });
  if (CHARTS.teamHour) CHARTS.teamHour.destroy();
  CHARTS.teamHour = new Chart(document.getElementById("teamHourChart").getContext("2d"), {
    type: "line", data: { labels, datasets },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } } },
      scales: { y: { beginAtZero: true, title: { display: true, text: "Tagged" } }, x: { title: { display: true, text: "Hour (EAT)" } } }
    }
  });
}

function renderPending(team) {
  const rows = (PENDING.by_team_qa_agent || []).filter(r => teamMatch(r.team, team));
  rows.sort((a, b) => b.count - a.count);
  document.querySelector("#pendingTable tbody").innerHTML = rows.map(r => `<tr><td>${escapeHtml(r.team)}</td><td>${escapeHtml(r.qa)}</td><td class="agent-name">${escapeHtml(r.agent)}</td><td class="right"><strong>${r.count}</strong></td><td class="right">${r.oldest_h || 0}</td></tr>`).join("") || '<tr><td colspan="5" class="empty">No pending records</td></tr>';
  document.getElementById("pendingMeta").textContent = `${PENDING.count || 0} records - captured ${PENDING.captured_at ? new Date(PENDING.captured_at).toLocaleString('en-GB', {timeZone:'Africa/Nairobi', hour12:false}) : '-'}`;
}

function renderProductivity(team) {
  const byAgent = AGGREGATES.by_agent || {};
  const min = AGGREGATES.thresholds?.productivity_min || 280;
  const rows = [];
  for (const t of (AUDIT_LOG.config?.ownership_teams || [])) {
    if (!teamMatch(t, team)) continue;
    for (const a of (AUDIT_LOG.roster[t].members || [])) {
      const d = byAgent[a.name] || { tagged_today: 0, productivity_met: false };
      rows.push({ team: t, agent: a.name, count: d.tagged_today || 0, met: !!d.productivity_met });
    }
  }
  rows.sort((a, b) => b.count - a.count);
  document.querySelector("#prodTable tbody").innerHTML = rows.map(r => {
    const diff = r.count - min;
    const pill = r.met ? '<span class="pill green">Met</span>' : '<span class="pill red">Below 280</span>';
    return `<tr><td>${escapeHtml(r.team)}</td><td class="agent-name">${escapeHtml(r.agent)}</td><td class="right"><strong>${r.count.toLocaleString()}</strong></td><td class="right" style="color:${r.met?'#1f6f2c':'#9b2222'}">${diff>=0?'+':''}${diff}</td><td>${pill}</td></tr>`;
  }).join("");
}

function renderSampling(team) {
  const byAgent = AGGREGATES.by_agent || {};
  const min = AGGREGATES.thresholds?.sampling_min_pct || 15;
  const rows = [];
  for (const t of (AUDIT_LOG.config?.ownership_teams || [])) {
    if (!teamMatch(t, team)) continue;
    const qa = AUDIT_LOG.roster[t]?.qa?.name || "?";
    for (const a of (AUDIT_LOG.roster[t].members || [])) {
      const d = byAgent[a.name];
      if (!d || (d.tagged_today || 0) === 0) continue;
      const sampled = (d.in_bo_qa || 0) + (d.qa_inspected || 0);
      const rate = d.sampling_rate_pct != null ? d.sampling_rate_pct : (d.tagged_today ? sampled / d.tagged_today * 100 : 0);
      rows.push({ team: t, qa, agent: a.name, sampled, total: d.tagged_today, rate, met: rate >= min });
    }
  }
  rows.sort((a, b) => a.rate - b.rate);
  document.querySelector("#samplingTable tbody").innerHTML = rows.map(r => `<tr><td>${escapeHtml(r.team)}</td><td>${escapeHtml(r.qa)}</td><td class="agent-name">${escapeHtml(r.agent)}</td><td class="right">${r.sampled}</td><td class="right">${r.total.toLocaleString()}</td><td class="right" style="color:${r.met?'#1f6f2c':'#9b2222'};font-weight:600">${r.rate.toFixed(1)}%</td><td>${r.met ? '<span class="pill green">>=15%</span>' : '<span class="pill red">Below 15%</span>'}</td></tr>`).join("") || '<tr><td colspan="7" class="empty">No data</td></tr>';
}

function renderSilent(team) {
  const events = (AUDIT_LOG.qa_events || []).filter(e => e.silent_change === true && teamMatch(e.team, team));
  events.sort((a, b) => new Date(b.qa_action_at) - new Date(a.qa_action_at));
  document.querySelector("#silentTable tbody").innerHTML = events.slice(0, 50).map(e => `<tr><td class="small-mono">${e.qa_action_at ? new Date(e.qa_action_at).toLocaleString('en-GB', {timeZone:'Africa/Nairobi', hour12:false}) : ''}</td><td>${escapeHtml(e.team)}</td><td class="agent-name">${escapeHtml(e.agent)}</td><td>${escapeHtml(e.qa || '-')}</td><td class="companies">${escapeHtml(e.agent_company_name || '?')}</td><td class="arrow">-></td><td class="companies">${escapeHtml(e.qa_company_name || '?')}</td><td><span class="silent-flag">SILENT</span></td></tr>`).join("") || '<tr><td colspan="8" class="empty">No silent changes detected yet</td></tr>';
}

function renderVerdicts() {
  const verdicts = AUDIT_LOG.daily_verdicts || {};
  const dates = Object.keys(verdicts).sort().reverse();
  document.querySelector("#verdictsTable tbody").innerHTML = dates.slice(0, 30).map(d => {
    const v = verdicts[d];
    const below = Object.values(v.by_agent || {}).filter(a => !a.productivity_met).length;
    const silent = Object.values(v.by_qa || {}).reduce((s, q) => s + (q.silent_changes_today || 0), 0);
    const total = Object.values(v.by_qa || {}).reduce((s, q) => s + (q.qa_actions_today || 0), 0);
    return `<tr><td><strong>${d}</strong></td><td class="right" style="color:${below>0?'#9b2222':'#1f6f2c'}">${below}</td><td class="right" style="color:${silent>0?'#9b2222':'#1f6f2c'}">${silent}</td><td class="right">${total}</td><td class="small-mono">${v.locked_at ? new Date(v.locked_at).toLocaleString('en-GB', {timeZone:'Africa/Nairobi', hour12:false}) : ''}</td></tr>`;
  }).join("") || '<tr><td colspan="5" class="empty">No verdicts locked yet</td></tr>';
}

function setupTabs() {
  document.querySelectorAll("#tabs button").forEach(b => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#tabs button").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      document.querySelectorAll(".tab-content").forEach(t => t.classList.remove("active"));
      document.getElementById("tab-" + b.dataset.tab).classList.add("active");
    });
  });
}

function main() {
  document.getElementById("teamSelect").addEventListener("change", render);
  setupTabs();
  const totals = AGGREGATES.totals || {};
  document.getElementById("subline").textContent = `Source: daily_aggregates.json (poll-computed) + pending_queue.json + ww_audit_log.json - scope: Simba, Nyati, Kobe, Pweza, Tembo`;
  document.getElementById("freshness").textContent = `Rendered ${new Date(RENDERED_AT).toLocaleString('en-GB', {timeZone:'Africa/Nairobi', hour12:false})} EAT - aggregates computed ${AGGREGATES.computed_at ? new Date(AGGREGATES.computed_at).toLocaleString('en-GB', {timeZone:'Africa/Nairobi', hour12:false}) : '(no data)'}`;
  if (!AGGREGATES.computed_at) {
    const w = document.getElementById("warn");
    w.style.display = "block";
    w.textContent = "No aggregates yet. The polling task needs to run at least once to populate daily_aggregates.json. Click 'Run now' on ww-poll-15min in the Cowork Scheduled sidebar to trigger it.";
  }
  render();
}
main();
</script>
</body>
</html>
"""

html = HTML.replace("__AUDIT_LOG__", json.dumps(audit))
html = html.replace("__AGGREGATES__", json.dumps(aggregates))
html = html.replace("__PENDING__", json.dumps(pending))
html = html.replace("__RENDERED_AT__", json.dumps(rendered_at))
OUT.write_text(html)
print(f"Rendered {OUT}")
print(f"  aggregates: {len(aggregates.get('by_agent', {}))} agents, {aggregates.get('totals', {}).get('tagged_today', 0)} tagged today")
print(f"  pending: {pending.get('count', 0)} records")
print(f"  audit_log: {len(audit.get('qa_events', []))} qa_events, {len(audit.get('pending_baselines', {}))} active baselines")
