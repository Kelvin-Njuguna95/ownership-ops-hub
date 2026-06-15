// Self-contained tie-out test for the Report Builder workbooks.
//
// It extracts the REAL builder + aggregation functions out of deploy/index.html
// (no copy-paste drift), runs them against a fixture through a tiny XLSX stub,
// and asserts the three workbooks' headline QA numbers are identical and that
// coverage = reviewed / completed.
//
//   node tests/report_metrics_tieout.mjs
//
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dirname, "..", "deploy", "index.html"), "utf8");

// ---- extract a top-level `function NAME(...) { ... }` (last occurrence) ----
function extractFn(name) {
  const re = new RegExp(`function ${name}\\s*\\(`, "g");
  let m, start = -1;
  while ((m = re.exec(html))) start = m.index;            // take the LAST def
  if (start < 0) throw new Error(`fn not found: ${name}`);
  const open = html.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < html.length; i++) {
    const c = html[i];
    if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) return html.slice(start, i + 1); }
  }
  throw new Error(`unbalanced fn: ${name}`);
}

// ---- extract a top-level `const NAME = ...;` (to the depth-0 semicolon) ----
function extractConst(name) {
  const start = html.indexOf(`const ${name}`);
  if (start < 0) throw new Error(`const not found: ${name}`);
  let depth = 0;
  for (let i = start; i < html.length; i++) {
    const c = html[i];
    if ("([{".includes(c)) depth++;
    else if (")]}".includes(c)) depth--;
    else if (c === ";" && depth === 0) return html.slice(start, i + 1);
  }
  throw new Error(`unterminated const: ${name}`);
}

const consts = ["REPORT_HOURS", "REPORT_STATUS_COLS", "REPORT_COMMENT_VALUES", "REPORT_COVERAGE_NOTE"];
const fns = [
  "eatDate", "eatHour", "median", "_enumerateDays", "_isoAddDays", "_isWeekendEat",
  "_round", "_pct", "canonicalAgent", "teamForAgent",
  "_agentAggregate", "_qaAggregate", "_reportMetrics", "_reportTieOut",
  "_buildTasksWorkbook", "_buildAgentsWorkbook", "_buildQAsWorkbook",
];

// ---- tiny XLSX stub: capture the AOA passed to each sheet ----
const XLSX = {
  utils: {
    book_new: () => ({ SheetNames: [], Sheets: {} }),
    aoa_to_sheet: (aoa) => ({ __aoa: aoa }),
    book_append_sheet: (wb, ws, name) => { wb.SheetNames.push(name); wb.Sheets[name] = ws; },
  },
};

// roster so canonicalAgent/teamForAgent resolve the fixture names
const STATE = {
  roster: {
    Alpha: { members: [{ name: "Alice", aliases: [] }, { name: "Bob", aliases: [] }] },
    Beta:  { members: [{ name: "Carol", aliases: [] }, { name: "Dan", aliases: [] }] },
  },
};

const src = [...consts.map(extractConst), ...fns.map(extractFn)].join("\n\n");
const factory = new Function("XLSX", "STATE", "console",
  src + "\nreturn { _reportMetrics, _qaAggregate, _buildTasksWorkbook, _buildAgentsWorkbook, _buildQAsWorkbook, _reportTieOut };");
const M = factory(XLSX, STATE, console);

// ---- fixture: known-answer data ----
const ts = (day, h) => `2026-06-${String(day).padStart(2, "0")}T${String(h - 3).padStart(2, "0")}:30:00Z`; // h = EAT hour
const rep = (n, row) => Array.from({ length: n }, () => ({ ...row }));

const data = {
  start: "2026-06-08", end: "2026-06-12",
  dates: M._enumerateDays ? undefined : undefined, // set below
  tasks: [
    { isCompleted: true, firstSeen: "2026-06-08", lastRecorded: "2026-06-12",
      latest: { tat_hours: 20, flags: [], date_last_modified: "2026-06-12" },
      peak: { total_records: 100, dead_vessels: 5, with_reminder: 2, completed: 90,
              qa_reviewed: 999, qa_changed: 99, qa_coverage_pct: 50, valid_pct: 80,
              is_sanctions: false, date_first_seen: "2026-06-08", status_distribution: {},
              comment_distribution: null, agents_worked: [] },
      qaPeak: { qa_reviewers: [{ name: "Alice", reviewed: 999, changed: 99 }] } },
  ],
  // completions: flow A x6, C x4, NULL x3 → completedInRange = 10 (A+C)
  completions: [
    ...rep(6, { completed_by: "Alice", completed_at: ts(8, 9), flow: "A" }),
    ...rep(4, { completed_by: "Bob",   completed_at: ts(9, 10), flow: "C" }),
    ...rep(3, { completed_by: "Carol", completed_at: ts(9, 11), flow: null }),
  ],
  // sampling: 8 rows → recordsSampledInRange = 8
  sampling: [
    ...rep(5, { qa_assignee: "Alice", sampled_at: ts(8, 9) }),
    ...rep(3, { qa_assignee: "Bob",   sampled_at: ts(9, 10) }),
  ],
  // reviews (verdict-time): Alice 5 approve + 3 changed, Bob 2 approve + 1 changed,
  // plus 1 non-verdict row that must be ignored.
  // → qaReviewedInRange = 11, qaChangedInRange = 4
  reviews: [
    ...rep(5, { qa_assignee: "Alice", reviewed_at: ts(8, 9), qa_status: "approve" }),
    ...rep(3, { qa_assignee: "Alice", reviewed_at: ts(8, 9), qa_status: "changed" }),
    ...rep(2, { qa_assignee: "Bob",   reviewed_at: ts(9, 10), qa_status: "approve" }),
    ...rep(1, { qa_assignee: "Bob",   reviewed_at: ts(9, 10), qa_status: "changed" }),
    ...rep(1, { qa_assignee: "Dan",   reviewed_at: ts(9, 10), qa_status: null }),
  ],
  completionsPartial: false, samplingPartial: false, reviewsPartial: false,
};
data.dates = ["2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12"];

// ---- run ----
const EXPECT = { reviewed: 11, changed: 4, completed: 10, sampled: 8, coverage: 110 };
const summaryVal = (wb, label) => {
  const aoa = wb.Sheets["Summary"].__aoa;
  const row = aoa.find(r => r[0] === label);
  if (!row) throw new Error(`Summary row not found: "${label}"`);
  return row[1];
};

let failures = 0;
const check = (name, got, want) => {
  const ok = got === want;
  console.log(`${ok ? "✓" : "✗"} ${name}: ${got}${ok ? "" : ` (expected ${want})`}`);
  if (!ok) failures++;
};

const m = M._reportMetrics(data);
check("_reportMetrics.qaReviewedInRange", m.qaReviewedInRange, EXPECT.reviewed);
check("_reportMetrics.qaChangedInRange", m.qaChangedInRange, EXPECT.changed);
check("_reportMetrics.completedInRange", m.completedInRange, EXPECT.completed);
check("_reportMetrics.recordsSampledInRange", m.recordsSampledInRange, EXPECT.sampled);
check("_reportMetrics.qaCoveragePct (= reviewed/completed)", m.qaCoveragePct, EXPECT.coverage);

const wbTasks = M._buildTasksWorkbook(data);
const wbAgents = M._buildAgentsWorkbook(data);
const wbQAs = M._buildQAsWorkbook(data);

const HL = "QA reviewed (records, in-range)";
const tRev = summaryVal(wbTasks, HL), aRev = summaryVal(wbAgents, HL), qRev = summaryVal(wbQAs, HL);
check("Tasks headline QA reviewed", tRev, EXPECT.reviewed);
check("Agents headline QA reviewed", aRev, EXPECT.reviewed);
check("QAs headline QA reviewed", qRev, EXPECT.reviewed);
check("headlines identical across all 3 workbooks", (tRev === aRev && aRev === qRev), true);

check("QAs coverage % = reviewed/completed",
  summaryVal(wbQAs, "QA coverage % (reviewed/completed, in-range)"), EXPECT.coverage);

check("tie-out passes (no mismatch)", M._reportTieOut(data), true);
check("data.metricMismatch is false", data.metricMismatch, false);

// lifetime peaks must NOT leak into the headline (peak qa_reviewed was 999)
check("Tasks lifetime kept separate", summaryVal(wbTasks, "QA reviewed (task lifetime)"), 999);

// negative case: corrupt the per-QA breakdown and confirm the tie-out fires
const bad = { ...data, reviews: data.reviews.concat([{ qa_assignee: "", reviewed_at: ts(9, 10), qa_status: "approve" }]) };
delete bad._reportMetrics; delete bad._qaAgg; delete bad._tieOut; bad.metricMismatch = undefined;
check("tie-out FIRES on empty-assignee verdict", M._reportTieOut(bad), false);

console.log(failures ? `\n${failures} FAILED` : "\nALL PASSED");
process.exit(failures ? 1 : 0);
