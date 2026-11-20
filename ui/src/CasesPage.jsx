import { useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import { ASKS } from "./util.js";
import ProfilePanel from "./ProfilePanel.jsx";
import LanguageSelect from "./LanguageSelect.jsx";

export default function CasesPage({ system, view, sessions, onRun }) {
  const [systems, setSystems] = useState([]);
  const [cases, setCases] = useState([]);
  const [selected, setSelected] = useState(null);
  const [backend, setBackend] = useState("claude_code");
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const current = system || (systems[0] && systems[0].name);
  const onProfile = view === "profile";

  useEffect(() => {
    api.systems().then(setSystems).catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (!current) return;
    setSelected(null);
    api.cases(current).then(setCases).catch((e) => setError(e.message));
  }, [current]);

  const latest = useMemo(() => {
    const m = {};
    for (const s of sessions) if (!m[s.case]) m[s.case] = s;
    return m;
  }, [sessions]);

  const shown = cases.filter(
    (c) => !filter || c.id.includes(filter) || c.instruction.toLowerCase().includes(filter.toLowerCase()),
  );

  async function run() {
    setBusy(true);
    setError("");
    try {
      const meta = await api.run(current, selected.id, backend);
      onRun();
      window.location.hash = `#/session/${meta.id}`;
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <div className="breadcrumb">Hệ thống / {current || "…"}</div>
        <div className="title-row">
          <h1>{current || "…"}</h1>
          <div className="segmented">
            <a className={!onProfile ? "active" : ""} href={`#/cases/${current}`}>Câu hỏi</a>
            <a className={onProfile ? "active" : ""} href={`#/cases/${current}/profile`}>Hồ sơ hệ thống</a>
          </div>
        </div>
        <div className="tabs">
          {systems.map((s) => (
            <a key={s.name} href={`#/cases/${s.name}${onProfile ? "/profile" : ""}`} className={"tab" + (s.name === current ? " active" : "")}>
              {s.name} <span className="tab-count">{s.cases}</span>
              {s.profiles && (s.profiles.en || s.profiles.vi) && <span className="tab-count"> · hồ sơ</span>}
            </a>
          ))}
        </div>
      </div>
      {onProfile && current ? <ProfilePanel system={current} onRun={onRun} /> : <>
      <div className="content">
        {error && <div className="alert alert-error">{error}</div>}
        <div className="card">
          <div className="card-head">
            <span>Danh sách câu hỏi</span>
            <input className="input search" placeholder="Tìm theo mã hoặc nội dung…" value={filter}
              onChange={(e) => setFilter(e.target.value)} />
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Mã</th><th>Loại</th><th>Hỏi</th><th>Cửa sổ (UTC+8)</th><th>Sự cố</th><th>Phiên</th><th></th>
                </tr>
              </thead>
              <tbody>
                {shown.map((c) => (
                  <tr key={c.id} className={selected && selected.id === c.id ? "selected" : ""}
                    onClick={() => setSelected(c)}>
                    <td className="mono">{c.id}</td>
                    <td>{c.task}</td>
                    <td>{c.asks.map((a) => <span key={a} className="tag tag-blue">{ASKS[a] || a}</span>)}</td>
                    <td className="mono">{c.start} – {c.end}</td>
                    <td>{c.faults}</td>
                    <td>{c.sessions || ""}</td>
                    <td className="right">
                      {latest[c.id] && (
                        <a href={`#/session/${latest[c.id].id}`} onClick={(e) => e.stopPropagation()}>Xem phiên</a>
                      )}
                      {!c.ready && <span className="tag tag-grey" title="Chưa có dữ liệu Parquet cho ngày này">chưa có dữ liệu</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
      <div className="composer">
        {selected ? (
          <>
            <div className="composer-question">
              <span className="tag tag-blue mono">{selected.id}</span> {selected.instruction}
            </div>
            <div className="composer-actions">
              <LanguageSelect />
              <select className="input" value={backend} onChange={(e) => setBackend(e.target.value)}>
                <option value="claude_code">Claude Code trên máy này</option>
                <option value="api">Claude API (theo Cài đặt)</option>
              </select>
              <button className="btn btn-primary" disabled={busy || !selected.ready} onClick={run}>
                {busy ? "Đang khởi chạy…" : "▶ Chạy agent"}
              </button>
            </div>
          </>
        ) : (
          <div className="composer-hint">Chọn một câu hỏi trong bảng để chạy agent, hoặc mở một phiên đã có.</div>
        )}
      </div>
      </>}
    </div>
  );
}
