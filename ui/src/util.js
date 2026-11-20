export const STATUS = {
  queued: { label: "QUEUED", tone: "gold" },
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

// What each tool is for, in plain Vietnamese, shown on every tool step.
export const TOOL_INFO = {
  question: "đọc đề bài: khoảng thời gian, danh sách máy và lý do được phép trả lời, danh sách công cụ",
  overview: "xem tổng quan: KPI nghiệp vụ từng phút (tỉ lệ thành công, thời gian phản hồi, số request), số log và số span của từng máy so với lúc bình thường",
  calls: "xem các cặp máy gọi nhau (từ trace): số lời gọi và thời gian gọi so với lúc bình thường",
  series: "xem giá trị từng phút của một metric trên một máy",
  logs: "xem log của một máy: số dòng mỗi phút và vài dòng mẫu",
  baro: "BARO: tìm thời điểm hệ thống đổi trạng thái, rồi xếp hạng metric lệch mạnh nhất",
  exclusive: "exclusive time: máy nào tự xử lý chậm đi, hay mạng vào máy nào chậm đi",
  traces: "MicroRank: máy nào hay có mặt trong các request bất thường",
  onset: "kiểm tra giả thuyết: máy này bắt đầu bất thường lúc nào, trước hay sau các máy gọi nó",
  hypothesis: "ghi hoặc cập nhật sổ giả thuyết",
  submit: "nộp đáp án",
  Read: "đọc tài liệu (hồ sơ hệ thống)",
  Write: "ghi file hồ sơ",
  read_profile: "đọc hồ sơ hệ thống (tài liệu mô tả hệ thống lúc khỏe)",
  write_profile: "ghi hồ sơ hệ thống",
};

// Split a shell argument string, respecting quotes.
export function tokenize(s) {
  const out = [];
  const re = /"((?:[^"\\]|\\.)*)"|'([^']*)'|(\S+)/g;
  let m;
  while ((m = re.exec(s || ""))) out.push(m[1] !== undefined ? m[1].replace(/\\(.)/g, "$1") : m[2] !== undefined ? m[2] : m[3]);
  return out;
}

function options(tokens) {
  const o = { _: [] };
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.startsWith("--")) {
      const next = tokens[i + 1];
      if (next !== undefined && !next.startsWith("--")) { o[t.slice(2)] = next; i++; } else o[t.slice(2)] = true;
    } else o._.push(t);
  }
  return o;
}

// What a tool call does, in words: "metric 'CPU' của Tomcat02".
const argv = (c) => c.argv || tokenize(c.args);

export function describeCall(c) {
  if (String(c.case).startsWith("-") || argv(c).includes("--help")) return "";
  const o = options(argv(c));
  switch (c.tool) {
    case "series": return `metric chứa '${o._[1] ?? ""}' của ${o._[0] ?? "?"}`;
    case "logs": return `log của ${o._[0] ?? "?"}${o.grep ? ` có chứa '${o.grep}'` : ""}`;
    case "onset": return `thời điểm bắt đầu lệch của ${o._[0] ?? "?"}${o.pattern ? ` (chuỗi chứa '${o.pattern}')` : ""}`;
    case "baro": return o.component ? `chỉ các metric của ${o.component}` : `${o.top ?? 20} chuỗi lệch mạnh nhất`;
    default: return "";
  }
}

// Hypothesis and submit commands, read from their arguments.
export function hypothesisOps(step) {
  const ops = [];
  for (const c of step.rca || []) {
    const o = options(argv(c));
    // Only commands that carry what they claim (not --help, not a half-parsed line).
    if (c.tool === "hypothesis" && o._[0] === "add" && o.component && o.reason)
      ops.push({ op: "add", component: o.component, reason: o.reason, ifTrue: o["if-true"], ifFalse: o["if-false"] });
    else if (c.tool === "hypothesis" && o._[0] === "test" && o._[1] && o.status)
      ops.push({ op: "test", id: o._[1], status: o.status, evidence: o.evidence });
    else if (c.tool === "submit" && o.time && o.component && o.reason)
      ops.push({ op: "submit", time: o.time, component: o.component, reason: o.reason });
  }
  return ops;
}

// Why a step failed, in words, for the errors agents run into.
export function explainError(output) {
  const o = output || "";
  if (o.includes("Blocked by the RCA test rules")) return "Bị chặn vì vi phạm luật làm mù: agent chỉ được chạy công cụ rca, không được đọc file hay chạy lệnh khác.";
  if (/unrecognized arguments: --run/.test(o)) return "Agent thêm --run vào một công cụ chỉ đọc; chỉ hypothesis và submit nhận --run.";
  if (/no case /.test(o)) return "Agent viết sai thứ tự tham số: mã câu hỏi phải đứng ngay sau tên công cụ, trước tên máy.";
  if (/unknown component/.test(o)) return "Tên máy không có trong danh sách được phép.";
  if (/^unknown tool/.test(o)) return "Agent gọi một công cụ không có trong bộ công cụ của project.";
  if (/^window must be one of/.test(o)) return "Cửa sổ không nằm trong danh sách cửa sổ khỏe được giao.";
  if (/no hypothesis \d+ for/.test(o)) return "Không tìm thấy giả thuyết: agent quên --run nên lệnh đọc sổ giả thuyết của một run khác.";
  if (/unknown reason/.test(o)) return "Lý do không đúng nguyên văn chuỗi trong danh sách.";
  if (/no supported hypothesis|no alternative hypothesis/.test(o)) return "submit bị từ chối: chưa có giả thuyết được ủng hộ trùng đáp án, hoặc chưa bác giả thuyết nào khác.";
  if (/denied because Claude Code is running in don't ask mode/.test(o)) return "Claude Code từ chối lệnh này (ngoài quyền được cấp).";
  if (/lies within an hour/.test(o)) return "Cửa sổ khỏe nằm quá gần một câu hỏi.";
  return "Lệnh trả về lỗi.";
}
