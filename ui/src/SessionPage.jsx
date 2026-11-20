import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import Markdown from "./Markdown.jsx";
import { ASKS, STATUS, clock, cstWindow, duration, latestHypotheses, parseHypotheses, steps, toolNames } from "./util.js";

const TOOL_TONE = {
  question: "grey", overview: "blue", calls: "cyan", series: "geekblue", logs: "purple", baro: "orange",
  exclusive: "magenta", traces: "volcano", onset: "gold", hypothesis: "green", submit: "red", Read: "lime",
};

function ToolCard({ step }) {
  const [open, setOpen] = useState(false);
  const r = step.result;
  const names = toolNames(step);
  const output = r ? r.output || "" : "";
  const lines = output.split("\n");
  const long = lines.length > 16;
  const hyps = names.includes("hypothesis") || names.includes("submit") ? parseHypotheses(output) : [];
  const isRead = step.name === "Read";
  return (
    <div className={"tool-card" + (r && r.is_error ? " tool-error" : "")} id={`step-${step.id}`}>
      <div className="tool-head" onClick={() => setOpen(!open)}>
        <span className="tool-kind">{isRead ? "📄 Đọc tài liệu" : "⌘ Gọi công cụ"}</span>
        {names.map((n) => <span key={n} className={`tag tag-${TOOL_TONE[n] || "blue"}`}>{n}</span>)}
        <span className="tool-state">
          {!r ? <span className="spinner" /> : r.is_error ? <span className="err">✗ lỗi</span> : <span className="ok">✓</span>}
          {r && r.ms != null && <span className="muted"> {duration(r.ms)}</span>}
        </span>
      </div>
      <div className="tool-command mono">{step.command}</div>
      {hyps.length > 0 && <HypothesisTable hyps={hyps} compact />}
      {r && (
        <div className="tool-output">
          <div className="tool-output-label">Công cụ trả về</div>
          <pre className={"output" + (long && !open ? " clipped" : "")}>{long && !open ? lines.slice(0, 16).join("\n") : output}</pre>
          {long && <button className="btn btn-link" onClick={() => setOpen(!open)}>{open ? "Thu gọn" : `Xem đủ ${lines.length} dòng`}</button>}
        </div>
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

function Step({ step }) {
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
      return <div className="info-line">Model <b>{step.model}</b>{step.backend ? ` · ${step.backend === "api" ? "Claude API" : "Claude Code"}` : ""}{step.base_url ? ` · ${step.base_url}` : ""}</div>;
    case "thinking":
      return <div className="msg msg-agent"><div className="avatar">A</div><Thinking text={step.text} /></div>;
    case "text":
      return <div className="msg msg-agent"><div className="avatar">A</div><div className="agent-text"><Markdown text={step.text} /></div></div>;
    case "tool_call":
      return <div className="msg msg-agent"><div className="avatar avatar-tool">⌘</div><ToolCard step={step} /></div>;
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
  const tone = { supported: "green", refuted: "red", open: "blue" };
  const label = { supported: "ủng hộ", refuted: "bác", open: "đang mở" };
  return (
    <table className={"table" + (compact ? " table-compact" : "")}>
      <thead><tr><th>#</th><th>Máy</th><th>Lý do</th><th>Trạng thái</th>{!compact && <th>Nếu đúng / nếu sai / bằng chứng</th>}</tr></thead>
      <tbody>
        {hyps.map((h) => (
          <tr key={h.id}>
            <td>{h.id}</td><td className="mono">{h.component}</td><td>{h.reason}</td>
            <td><span className={`tag tag-${tone[h.status]}`}>{label[h.status]}</span></td>
            {!compact && (
              <td className="small">
                <div><b>Nếu đúng:</b> {h.ifTrue}</div>
                <div><b>Nếu sai:</b> {h.ifFalse}</div>
                {h.evidence.map((e, i) => <div key={i}><b>Bằng chứng:</b> {e}</div>)}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
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
  const reads = all.filter((s) => s.kind === "tool_call" && s.name === "Read");
  const counts = {};
  for (const s of all) if (s.kind === "tool_call" && s.name !== "Read") for (const n of toolNames(s)) counts[n] = (counts[n] || 0) + 1;
  const max = Math.max(1, ...Object.values(counts));
  return (
    <div>
      <div className="section-title">Tài liệu agent đã đọc</div>
      {prompt && <div className="source">📋 Lời dặn ({prompt.text.length} ký tự)</div>}
      {reads.length ? reads.map((r) => (
        <div key={r.id} className="source link" onClick={() => document.getElementById(`step-${r.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>
          📄 {r.command.split("/").slice(-2).join("/")}
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

function Answer({ id, status }) {
  const [a, setA] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (status === "running") return;
    api.answer(id).then(setA).catch((e) => setErr(e.message));
  }, [id, status]);
  if (status === "running") return <div className="muted">Đáp án chỉ hiện khi agent đã chạy xong.</div>;
  if (err) return <div className="alert alert-error">{err}</div>;
  if (!a) return <div className="muted">Đang tải…</div>;
  return (
    <div>
      <div className="score-row">
        <span className={`tag tag-${a.solved ? "green" : a.score > 0 ? "orange" : "red"}`}>{a.solved ? "GIẢI ĐÚNG" : "CHƯA ĐÚNG"}</span>
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
      if (meta.status === "running") {
        source = api.stream(id, events.length);
        source.onmessage = (m) => setEvents((prev) => {
          const e = JSON.parse(m.data);
          return prev.some((p) => p.i === e.i) ? prev : [...prev, e];
        });
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
          <span className="muted">{meta.imported ? "phiên đã lưu" : meta.backend === "api" ? "Claude API" : "Claude Code"} · {meta.model || "?"}</span>
          <span className="spacer" />
          {meta.status === "running" ? (
            <button className="btn btn-danger" onClick={() => api.stop(id)}>■ Dừng</button>
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
            {items.map((s, i) => <Step key={s.id || `${s.kind}-${s.i ?? i}`} step={s} />)}
            {meta.status === "running" && !replay && <div className="typing"><span /><span /><span /></div>}
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
            {tab === "hyp" && (hyps.length ? <HypothesisTable hyps={hyps} /> : <div className="muted">Agent chưa nêu giả thuyết nào.</div>)}
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
