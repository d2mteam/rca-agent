import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import Markdown from "./Markdown.jsx";
import { ASKS, STATUS, TOOL_INFO, clock, cstWindow, describeCall, duration, explainError, hypothesisOps, latestHypotheses,
  parseHypotheses, steps, toolNames } from "./util.js";

const TOOL_TONE = {
  question: "grey", overview: "blue", calls: "cyan", series: "geekblue", logs: "purple", baro: "orange",
  exclusive: "magenta", traces: "volcano", onset: "gold", hypothesis: "green", submit: "red", Read: "lime",
};

function HypothesisOps({ ops, output }) {
  // `hypothesis add` prints "hypothesis #N added" once per call, in order.
  const added = [...(output || "").matchAll(/hypothesis #(\d+) added/g)].map((m) => m[1]);
  let a = 0;
  return ops.map((o, i) => {
    if (o.op === "add") { const n = added[a++]; return (
      <div key={i} className="hop hop-add">
        <div className="hop-title">💡 Nêu giả thuyết{n ? ` #${n}` : ""}: <b className="mono">{o.component}</b> — {o.reason}</div>
        <div className="hop-row"><span className="hop-label">Nếu đúng sẽ thấy</span>{o.ifTrue}</div>
        <div className="hop-row"><span className="hop-label">Nếu sai sẽ thấy</span>{o.ifFalse}</div>
      </div>
    ); }
    if (o.op === "test") {
      const tone = o.status === "supported" ? "ok" : o.status === "refuted" ? "bad" : "open";
      const verb = o.status === "supported" ? "✓ Ủng hộ" : o.status === "refuted" ? "✗ Bác bỏ" : "… Để mở";
      return (
        <div key={i} className={`hop hop-${tone}`}>
          <div className="hop-title">{verb} giả thuyết #{o.id}</div>
          <div className="hop-row"><span className="hop-label">Vì</span>{o.evidence}</div>
        </div>
      );
    }
    return (
      <div key={i} className="hop hop-submit">
        <div className="hop-title">📨 Nộp đáp án</div>
        <div className="mono small">{o.time} · {o.component} · {o.reason}</div>
      </div>
    );
  });
}

function ToolCard({ step, n }) {
  const [open, setOpen] = useState(false);
  const [raw, setRaw] = useState(false);
  const r = step.result;
  const names = toolNames(step);
  const output = r ? r.output || "" : "";
  const lines = output.split("\n");
  const long = lines.length > 16;
  const ops = hypothesisOps(step);
  const listed = !ops.length && names.includes("hypothesis") ? parseHypotheses(output) : [];
  const isRead = ["Read", "Write", "read_profile", "write_profile"].includes(step.name);
  const details = (step.rca || []).map((c) => describeCall(c)).filter(Boolean);
  const purpose = names.map((x) => TOOL_INFO[x]).filter(Boolean);
  return (
    <div className={"tool-card" + (r && r.is_error ? " tool-error" : "") + (ops.length ? " tool-hyp" : "")} id={`step-${step.id}`}>
      <div className="tool-head">
        <span className="step-no">Bước {n}</span>
        <span className="tool-kind">{isRead ? "📄" : "⌘"}</span>
        {names.map((x) => <span key={x} className={`tag tag-${TOOL_TONE[x] || "blue"}`} title={TOOL_INFO[x] || ""}>{x}</span>)}
        <span className="tool-state">
          {!r ? <span className="spinner" /> : r.is_error ? <span className="err">✗ lỗi</span> : <span className="ok">✓</span>}
          {r && r.ms != null && <span className="muted"> {duration(r.ms)}</span>}
        </span>
      </div>
      <div className="tool-purpose">
        {purpose.length > 0 && <span>{purpose.join(" · ")}</span>}
        {details.length > 0 && <span className="tool-detail"> → {details.join("; ")}</span>}
        {isRead && step.name.length < 6 && <span className="mono"> {step.command}</span>}
      </div>
      {r && r.is_error && <div className="tool-why">⚠ {explainError(output)}</div>}
      {listed.length > 0 ? (
        <div className="hop-list">
          <div className="tool-output-label">Sổ giả thuyết lúc này</div>
          <HypothesisCards hyps={listed} events={[]} />
          <button className="btn btn-link small-link" onClick={() => setRaw(!raw)}>{raw ? "Ẩn lệnh gốc" : "Xem lệnh gốc và đầu ra"}</button>
          {raw && <><div className="tool-command mono">{step.command}</div><pre className="output">{output}</pre></>}
        </div>
      ) : ops.length > 0 ? (
        <>
          <HypothesisOps ops={ops} output={output} />
          <button className="btn btn-link small-link" onClick={() => setRaw(!raw)}>{raw ? "Ẩn lệnh gốc" : "Xem lệnh gốc và đầu ra"}</button>
          {raw && <><div className="tool-command mono">{step.command}</div><pre className="output">{output}</pre></>}
        </>
      ) : (
        <>
          {!isRead && <div className="tool-command mono">{step.command}</div>}
          {r && (
            <div className="tool-output">
              <div className="tool-output-label">Công cụ trả về</div>
              <pre className={"output" + (long && !open ? " clipped" : "")}>{long && !open ? lines.slice(0, 16).join("\n") : output}</pre>
              {long && <button className="btn btn-link" onClick={() => setOpen(!open)}>{open ? "Thu gọn" : `Xem đủ ${lines.length} dòng`}</button>}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Thinking({ text }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="thinking">
      <div className="thinking-head" onClick={() => setOpen(!open)}>💭 Suy luận {open ? "▾" : "▸"}</div>
      {open && <Markdown text={text} />}
    </div>
  );
}

function Prompt({ text }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="prompt-card">
      <div className="prompt-head" onClick={() => setOpen(!open)}>📋 Lời dặn agent nhận được {open ? "▾" : "▸"}</div>
      {open && <pre className="output">{text}</pre>}
    </div>
  );
}

function Step({ step, n }) {
  switch (step.kind) {
    case "question":
      return (
        <div className="msg msg-user">
          <div className="bubble">
            <div className="bubble-meta">
              {cstWindow(step.window)} · hỏi {(step.asks || []).map((a) => ASKS[a] || a).join(", ")}
            </div>
            {step.text}
          </div>
        </div>
      );
    case "prompt":
      return <Prompt text={step.text} />;
    case "info":
      if (step.backend === "claude_code") return null; // the CLI reports the model it actually runs just after
      return <div className="info-line">Model <b>{step.model}</b>{step.backend ? ` · ${step.backend === "api" ? "Claude API" : "Claude Code"}` : ""}{step.base_url ? ` · ${step.base_url}` : ""}</div>;
    case "thinking":
      return <div className="msg msg-agent"><div className="avatar">A</div><Thinking text={step.text} /></div>;
    case "text":
      return <div className="msg msg-agent"><div className="avatar">A</div><div className="agent-text"><Markdown text={step.text} /></div></div>;
    case "tool_call":
      return <div className="msg msg-agent"><div className="avatar avatar-tool">⌘</div><ToolCard step={step} n={n} /></div>;
    case "error":
      return <div className="alert alert-error">{step.text}</div>;
    case "done":
      return (
        <div className="done-line">
          Kết thúc · {duration(step.duration_ms)}{step.turns ? ` · ${step.turns} lượt` : ""}
          {step.cost_usd != null ? ` · $${step.cost_usd.toFixed(3)}` : ""}
          {step.usage && step.usage.output_tokens ? ` · ${step.usage.input_tokens} in / ${step.usage.output_tokens} out tokens` : ""}
        </div>
      );
    default:
      return null;
  }
}

function HypothesisTable({ hyps, compact }) {
  return <HypothesisCards hyps={hyps} events={[]} compact={compact} />;
}

function HypothesisCards({ hyps, events }) {
  const label = { supported: "ĐƯỢC ỦNG HỘ", refuted: "BỊ BÁC", open: "ĐANG MỞ" };
  const calls = steps(events).filter((s) => s.kind === "tool_call");
  const stepNo = (s) => calls.indexOf(s) + 1;
  const where = (h) => {
    const added = calls.find((s) => hypothesisOps(s).some((o) => o.op === "add" && o.component === h.component && o.reason === h.reason));
    const tested = calls.filter((s) => hypothesisOps(s).some((o) => o.op === "test" && String(o.id) === String(h.id)));
    return { added, tested };
  };
  const go = (s) => document.getElementById(`step-${s.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  return (
    <div className="hyp-cards">
      {hyps.map((h) => {
        const { added, tested } = where(h);
        return (
          <div key={h.id} className={`hyp-card hyp-${h.status}`}>
            <div className="hyp-head">
              <span className="hyp-id">#{h.id}</span>
              <span className="hyp-name"><b className="mono">{h.component}</b> — {h.reason}</span>
              <span className={`hyp-status hyp-status-${h.status}`}>{label[h.status]}</span>
            </div>
            <div className="hyp-body">
              <div className="hop-row"><span className="hop-label">Nếu đúng sẽ thấy</span>{h.ifTrue || "–"}</div>
              <div className="hop-row"><span className="hop-label">Nếu sai sẽ thấy</span>{h.ifFalse || "–"}</div>
              <div className="hyp-verdict">
                <div className="hop-label">{h.status === "refuted" ? "Vì sao bị bác" : h.status === "supported" ? "Vì sao được ủng hộ" : "Bằng chứng"}</div>
                {h.evidence.length ? h.evidence.map((e, i) => <div key={i} className="hyp-evidence">{e}</div>)
                  : <div className="muted">Chưa kiểm.</div>}
              </div>
              {(added || tested.length > 0) && (
                <div className="hyp-links">
                  {added && <a onClick={() => go(added)}>nêu ở bước {stepNo(added)}</a>}
                  {tested.map((s) => <a key={s.id} onClick={() => go(s)}>kiểm ở bước {stepNo(s)}</a>)}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Problems({ events }) {
  const calls = steps(events).filter((s) => s.kind === "tool_call");
  const failed = calls.map((s, i) => ({ s, n: i + 1 })).filter(({ s }) => s.result && s.result.is_error);
  const errors = events.filter((e) => e.kind === "error");
  if (!failed.length && !errors.length) return <div className="muted small">Không có bước nào lỗi.</div>;
  return (
    <div>
      {errors.map((e, i) => <div key={i} className="problem problem-run">⛔ {e.text}</div>)}
      {failed.map(({ s, n }) => (
        <div key={s.id} className="problem" onClick={() => document.getElementById(`step-${s.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>
          <span className="step-no">Bước {n}</span> {explainError(s.result.output)}
        </div>
      ))}
    </div>
  );
}

function Timeline({ events }) {
  const calls = steps(events).filter((s) => s.kind === "tool_call" && s.t);
  if (!calls.length) return <div className="muted">Chưa có lời gọi công cụ.</div>;
  const t0 = Math.min(...events.filter((e) => e.t).map((e) => e.t));
  const end = Math.max(...events.filter((e) => e.t).map((e) => e.t), ...calls.map((c) => c.t + (c.result?.ms || 0) / 1000));
  const lanes = [...new Set(calls.flatMap((c) => toolNames(c).slice(0, 1)))];
  const W = 340, L = 78, H = 22;
  const x = (t) => L + ((t - t0) / Math.max(end - t0, 1)) * (W - L - 6);
  return (
    <div className="timeline-wrap">
      <svg width={W} height={lanes.length * H + 24} className="timeline">
        {lanes.map((lane, i) => (
          <g key={lane}>
            <text x={0} y={i * H + 15} className="tl-label">{lane}</text>
            <line x1={L} x2={W} y1={i * H + H} y2={i * H + H} className="tl-grid" />
          </g>
        ))}
        {calls.map((c) => {
          const lane = lanes.indexOf(toolNames(c)[0]);
          const w = Math.max(x(c.t + (c.result?.ms || 0) / 1000) - x(c.t), 3);
          return (
            <rect key={c.id} x={x(c.t)} y={lane * H + 5} width={w} height={H - 9} rx={2}
              className={`tl-bar tl-${TOOL_TONE[toolNames(c)[0]] || "blue"}`}
              onClick={() => document.getElementById(`step-${c.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>
              <title>{`${c.command}\n${duration(c.result?.ms)}`}</title>
            </rect>
          );
        })}
        <text x={L} y={lanes.length * H + 18} className="tl-label">0</text>
        <text x={W - 40} y={lanes.length * H + 18} className="tl-label">{duration((end - t0) * 1000)}</text>
      </svg>
    </div>
  );
}

function Sources({ events }) {
  const all = steps(events);
  const prompt = events.find((e) => e.kind === "prompt");
  const reads = all.filter((s) => s.kind === "tool_call" && ["Read", "read_profile"].includes(s.name));
  const counts = {};
  for (const s of all) if (s.kind === "tool_call" && !["Read", "read_profile"].includes(s.name)) for (const n of toolNames(s)) counts[n] = (counts[n] || 0) + 1;
  const max = Math.max(1, ...Object.values(counts));
  return (
    <div>
      <div className="section-title">Tài liệu agent đã đọc</div>
      {prompt && <div className="source">📋 Lời dặn ({prompt.text.length} ký tự)</div>}
      {reads.length ? reads.map((r) => (
        <div key={r.id} className="source link" onClick={() => document.getElementById(`step-${r.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>
          📄 {r.name === "read_profile" ? "hồ sơ hệ thống" : r.command.split("/").slice(-2).join("/")}
        </div>
      )) : <div className="muted">Không đọc tài liệu nào ngoài lời dặn.</div>}
      <div className="section-title">Công cụ đã dùng</div>
      {Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([n, c]) => (
        <div key={n} className="bar-row">
          <span className="bar-label">{n}</span>
          <span className="bar"><span className={`bar-fill tl-${TOOL_TONE[n] || "blue"}`} style={{ width: `${(100 * c) / max}%` }} /></span>
          <span className="bar-count">{c}</span>
        </div>
      ))}
    </div>
  );
}

// A case counts as solved only at a full score.
function verdict(score) {
  return score >= 1 ? { tone: "green", label: "ĐÚNG" } : score > 0 ? { tone: "orange", label: "ĐÚNG MỘT PHẦN" }
    : { tone: "red", label: "SAI" };
}

function Answer({ id, status }) {
  const [a, setA] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (status === "running" || status === "queued") return;
    api.answer(id).then(setA).catch((e) => setErr(e.message));
  }, [id, status]);
  if (status === "running" || status === "queued") return <div className="muted">Đáp án chỉ hiện khi agent đã chạy xong.</div>;
  if (err) return <div className="alert alert-error">{err}</div>;
  if (!a) return <div className="muted">Đang tải…</div>;
  const v = verdict(a.score);
  return (
    <div>
      <div className="score-row">
        <span className={`tag tag-${v.tone}`}>{v.label}</span>
        <span className="score">{Math.round(a.score * 100)}%</span>
      </div>
      <div className="section-title">Từng ô được chấm</div>
      <table className="table table-compact">
        <thead><tr><th>Sự cố</th><th>Trường</th><th>Đáp án</th><th>Agent nộp</th><th></th></tr></thead>
        <tbody>
          {a.fields.map((f, i) => (
            <tr key={i}><td>{f.fault + 1}</td><td>{ASKS[f.field] || f.field}</td><td className="mono">{f.expected}</td>
              <td className="mono">{f.got || "–"}</td><td>{f.passed ? <span className="ok">✓</span> : <span className="err">✗</span>}</td></tr>
          ))}
        </tbody>
      </table>
      <div className="section-title">Agent đã nộp</div>
      {a.answers.length ? a.answers.map((x, i) => <div key={i} className="mono small">{x.time} · {x.component} · {x.reason}</div>)
        : <div className="muted">Không nộp đáp án nào.</div>}
    </div>
  );
}

export default function SessionPage({ id, onChange }) {
  const [meta, setMeta] = useState(null);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("overview");
  const [replay, setReplay] = useState(null); // {shown, speed, playing}
  const bottom = useRef(null);
  const chat = useRef(null);

  useEffect(() => {
    let source;
    setMeta(null); setEvents([]); setReplay(null); setError("");
    api.session(id).then(({ meta, events }) => {
      setMeta(meta); setEvents(events);
      if (meta.status === "running" || meta.status === "queued") {
        source = api.stream(id, events.length);
        source.onmessage = (m) => setEvents((prev) => {
          const e = JSON.parse(m.data);
          return prev.some((p) => p.i === e.i) ? prev : [...prev, e];
        });
        source.addEventListener("meta", (m) => setMeta(JSON.parse(m.data)));
        source.addEventListener("end", (m) => { setMeta(JSON.parse(m.data)); source.close(); onChange(); });
      }
    }).catch((e) => setError(e.message));
    return () => source && source.close();
  }, [id]);

  // Replay: reveal events one by one, keeping their relative timing (scaled and capped).
  useEffect(() => {
    if (!replay || !replay.playing || replay.shown >= events.length) return;
    const cur = events[replay.shown - 1], next = events[replay.shown];
    const gap = cur && next && cur.t && next.t ? (next.t - cur.t) * 1000 : 400;
    const wait = Math.min(Math.max(gap / replay.speed, 60), 1500);
    const t = setTimeout(() => setReplay((r) => r && { ...r, shown: r.shown + 1, playing: r.shown + 1 < events.length }), wait);
    return () => clearTimeout(t);
  }, [replay, events]);

  const visible = replay ? events.slice(0, replay.shown) : events;
  const items = useMemo(() => steps(visible), [visible]);
  const hyps = useMemo(() => latestHypotheses(visible), [visible]);

  useEffect(() => {
    const el = chat.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 400) bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [items.length]);

  if (error) return <div className="page"><div className="content"><div className="alert alert-error">{error}</div></div></div>;
  if (!meta) return <div className="page"><div className="content muted">Đang tải…</div></div>;

  const st = STATUS[meta.status] || STATUS.finished;
  const done = events.find((e) => e.kind === "done");
  const callCount = events.filter((e) => e.kind === "tool_call").length;
  const last = events.filter((e) => e.t).map((e) => e.t);
  const elapsed = done && done.duration_ms ? done.duration_ms : last.length > 1 ? (last[last.length - 1] - last[0]) * 1000 : null;

  return (
    <div className="page session">
      <div className="page-header">
        <div className="breadcrumb"><a href={`#/cases/${meta.system}`}>{meta.system}</a> / {meta.case}</div>
        <div className="title-row">
          <h1>{meta.case}</h1>
          <span className={`tag tag-${st.tone}`}>{meta.status === "running" && <span className="spinner small" />} {st.label}</span>
          {meta.kind !== "profile" && meta.score != null && (
            <a className={`tag tag-${verdict(meta.score).tone}`} onClick={() => setTab("answer")} title="Xem đối chiếu với đáp án">
              {verdict(meta.score).label} · {Math.round(meta.score * 100)}%
            </a>
          )}
          <span className="muted">{meta.imported ? "phiên đã lưu" : meta.backend === "api" ? "Claude API" : "Claude Code"} · {meta.model || "?"}</span>
          <span className="spacer" />
          {meta.status === "running" || meta.status === "queued" ? (
            <button className="btn btn-danger" onClick={() => api.stop(id)}>{meta.status === "queued" ? "✕ Bỏ khỏi hàng đợi" : "■ Dừng"}</button>
          ) : (
            <div className="replay">
              {replay ? (
                <>
                  <button className="btn" onClick={() => setReplay({ ...replay, playing: !replay.playing })}>{replay.playing ? "❚❚ Tạm dừng" : "▶ Tiếp"}</button>
                  <button className="btn" onClick={() => setReplay({ ...replay, shown: Math.min(replay.shown + 1, events.length), playing: false })}>⏭ Bước</button>
                  <select className="input" value={replay.speed} onChange={(e) => setReplay({ ...replay, speed: +e.target.value })}>
                    {[1, 4, 16, 64].map((s) => <option key={s} value={s}>{s}×</option>)}
                  </select>
                  <span className="muted">{replay.shown}/{events.length}</span>
                  <button className="btn" onClick={() => setReplay(null)}>Hiện hết</button>
                </>
              ) : (
                <button className="btn btn-primary" onClick={() => setReplay({ shown: 1, speed: 4, playing: true })}>▶ Phát lại</button>
              )}
            </div>
          )}
        </div>
      </div>
      <div className="session-body">
        <div className="chat" ref={chat}>
          <div className="chat-inner">
            {(() => { let n = 0; return items.map((s, i) => <Step key={s.id || `${s.kind}-${s.i ?? i}`} step={s} n={s.kind === "tool_call" ? ++n : 0} />); })()}
            {meta.status === "queued" && <div className="info-line">Đang chờ: một agent khác đang chạy. Phiên này sẽ tự chạy khi đến lượt.</div>}
            {meta.status === "running" && !replay && <div className="typing"><span /><span /><span /></div>}
            {meta.kind !== "profile" && meta.status !== "running" && meta.status !== "queued"
              && (!replay || replay.shown >= events.length) && (
              <div className="verdict-card">
                <div className="section-title">Kết quả chấm so với đáp án</div>
                <Answer id={id} status={meta.status} />
              </div>
            )}
            <div ref={bottom} />
          </div>
        </div>
        <div className="panel">
          <div className="tabs tabs-small">
            {[["overview", "Tổng quan"], ["hyp", `Giả thuyết${hyps.length ? ` (${hyps.length})` : ""}`], ["timeline", "Dòng thời gian"],
              ["sources", "Nguồn thông tin"], meta.kind === "profile" ? ["profile", "Hồ sơ"] : ["answer", "Đáp án"]].map(([k, label]) => (
              <a key={k} className={"tab" + (tab === k ? " active" : "")} onClick={() => setTab(k)}>{label}</a>
            ))}
          </div>
          <div className="panel-body">
            {tab === "overview" && (
              <div className="stats">
                <div className="stat"><div className="stat-label">Thời gian</div><div className="stat-value">{duration(elapsed)}</div></div>
                <div className="stat"><div className="stat-label">Lời gọi công cụ</div><div className="stat-value">{callCount}</div></div>
                <div className="stat"><div className="stat-label">Giả thuyết</div><div className="stat-value">{hyps.length}</div></div>
                <div className="stat"><div className="stat-label">Điểm</div><div className="stat-value">{meta.score != null ? `${Math.round(meta.score * 100)}%` : "–"}</div></div>
                <div className="desc-wide">
                  <div className="section-title">Các bước lỗi</div>
                  <Problems events={visible} />
                </div>
                <dl className="desc">
                  <dt>Phiên</dt><dd className="mono">{meta.id}</dd>
                  <dt>Hệ thống</dt><dd>{meta.system}</dd>
                  <dt>Run</dt><dd className="mono">{meta.run}</dd>
                  <dt>Hồ sơ hệ thống</dt><dd>{meta.profile ? "có đọc" : "không"}</dd>
                  <dt>Ngôn ngữ</dt><dd>{meta.language === "vi" ? "Tiếng Việt" : "English"}</dd>
                  <dt>Bắt đầu</dt><dd>{clock(meta.created)}</dd>
                  {meta.imported && <><dt>Nguồn</dt><dd className="small mono">{meta.imported.split("/").slice(-1)[0]}</dd></>}
                </dl>
              </div>
            )}
            {tab === "hyp" && (hyps.length ? <HypothesisCards hyps={hyps} events={visible} /> : <div className="muted">Agent chưa nêu giả thuyết nào.</div>)}
            {tab === "timeline" && <Timeline events={visible} />}
            {tab === "sources" && <Sources events={visible} />}
            {tab === "answer" && <Answer id={id} status={meta.status} />}
            {tab === "profile" && (
              <div>
                <div className="section-title">Hồ sơ được ghi vào</div>
                <div className="mono small">{meta.output}</div>
                <div className="section-title">Cửa sổ khỏe</div>
                {(meta.windows || []).map((w) => <div key={w} className="mono small">{w}</div>)}
                <p><a href={`#/cases/${meta.system}/profile`}>Mở hồ sơ hệ thống {meta.system} →</a></p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
