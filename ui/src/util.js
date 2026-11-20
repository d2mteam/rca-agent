export const STATUS = {
  running: { label: "RUNNING", tone: "blue" },
  finished: { label: "FINISHED", tone: "green" },
  error: { label: "FAILED", tone: "red" },
  stopped: { label: "CANCELED", tone: "grey" },
  interrupted: { label: "INTERRUPTED", tone: "orange" },
};

export const ASKS = { time: "thời điểm", component: "máy", reason: "lý do" };

export function duration(ms) {
  if (ms == null || Number.isNaN(ms)) return "–";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function clock(t) {
  if (!t) return "";
  return new Date(t * 1000).toLocaleString("vi-VN", { hour12: false });
}

export function cstWindow(win) {
  if (!win) return "";
  const f = (t) =>
    new Date(t * 1000).toLocaleString("sv-SE", { timeZone: "Asia/Shanghai", hour12: false }).slice(0, 16);
  return `${f(win[0])} → ${f(win[1]).slice(11)} (UTC+8)`;
}

// Hypotheses as `rca tool hypothesis` prints them.
const HYP = /^#(\d+) \[(open|supported|refuted)\] (.+?) \/ (.+)$/;
export function parseHypotheses(text) {
  const out = [];
  let cur = null;
  for (const raw of (text || "").split("\n")) {
    const line = raw.trim();
    const m = line.match(HYP);
    if (m) {
      cur = { id: +m[1], status: m[2], component: m[3], reason: m[4], ifTrue: "", ifFalse: "", evidence: [] };
      out.push(cur);
    } else if (cur) {
      if (line.startsWith("if true:")) cur.ifTrue = line.slice(8).trim();
      else if (line.startsWith("if false:")) cur.ifFalse = line.slice(9).trim();
      else if (line.startsWith("evidence:")) cur.evidence.push(line.slice(9).trim());
    }
  }
  return out;
}

// Pair every tool call with its result, keeping the order of the calls.
export function steps(events) {
  const results = new Map(events.filter((e) => e.kind === "tool_result").map((e) => [e.id, e]));
  return events
    .filter((e) => e.kind !== "tool_result")
    .map((e) => (e.kind === "tool_call" ? { ...e, result: results.get(e.id) } : e));
}

export function latestHypotheses(events) {
  let last = [];
  for (const e of events) {
    if (e.kind !== "tool_result") continue;
    const h = parseHypotheses(e.output);
    if (h.length) last = h;
  }
  return last;
}

export function toolNames(call) {
  if (call.rca && call.rca.length) return [...new Set(call.rca.map((c) => c.tool))];
  return [call.name];
}
