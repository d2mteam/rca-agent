import { useEffect, useState } from "react";
import { api } from "./api.js";
import Markdown from "./Markdown.jsx";
import { STATUS, clock } from "./util.js";

// The system profile of one system: read it (English or Vietnamese), set the healthy windows, write it again.
export default function ProfilePanel({ system, onRun }) {
  const [language, setLanguage] = useState("en");
  const [p, setP] = useState(null);
  const [windows, setWindows] = useState("");
  const [backend, setBackend] = useState("claude_code");
  const [settingsLanguage, setSettingsLanguage] = useState("en");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.settings().then((s) => setSettingsLanguage(s.language)).catch(() => {}); }, []);
  useEffect(() => {
    setP(null); setError("");
    api.profile(system, language).then((r) => { setP(r); setWindows(r.windows.join("\n")); }).catch((e) => setError(e.message));
  }, [system, language]);

  async function write() {
    setError("");
    const list = windows.split(/[\s,]+/).filter(Boolean);
    const target = await api.profile(system, "en");
    if (target.exists && !window.confirm(`${target.path} đã có. Ghi đè hồ sơ này?`)) return;
    setBusy(true);
    try {
      const meta = await api.runProfile(system, backend, target.exists, list);
      onRun();
      window.location.hash = `#/session/${meta.id}`;
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function translate() {
    setError("");
    const vi = await api.profile(system, "vi");
    if (vi.exists && !window.confirm(`${vi.path} đã có. Dịch lại và ghi đè?`)) return;
    setBusy(true);
    try {
      const meta = await api.translateProfile(system, backend, vi.exists);
      onRun();
      window.location.hash = `#/session/${meta.id}`;
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="content profile-layout">
      {error && <div className="alert alert-error">{error}</div>}
      <div className="card profile-doc">
        <div className="card-head">
          <span>Hồ sơ hệ thống {p && <span className="mono muted">{p.path}</span>}</span>
          <div className="segmented small">
            <a className={language === "en" ? "active" : ""} onClick={() => setLanguage("en")}>English</a>
            <a className={language === "vi" ? "active" : ""} onClick={() => setLanguage("vi")}>Tiếng Việt</a>
          </div>
        </div>
        <div className="profile-body">
          {!p ? <div className="muted">Đang tải…</div> : p.exists ? <Markdown text={p.content} />
            : <div className="muted">Chưa có hồ sơ {language === "vi" ? "tiếng Việt" : "tiếng Anh"} cho {system}.</div>}
        </div>
      </div>
      <div className="profile-side">
        <div className="card">
          <div className="card-head"><span>Viết hồ sơ</span></div>
          <div className="form">
            <label>Cửa sổ khỏe (mỗi dòng một cửa sổ)
              <textarea className="input mono windows" rows={7} value={windows} onChange={(e) => setWindows(e.target.value)} />
            </label>
            <div className="row">
              <button className="btn" onClick={() => p && setWindows(p.suggested.join("\n"))}>Dùng gợi ý</button>
              <span className="muted small">Mỗi cửa sổ 30 phút, cách mọi câu hỏi ít nhất 1 giờ.</span>
            </div>
            <label>Chạy bằng
              <select className="input" value={backend} onChange={(e) => setBackend(e.target.value)}>
                <option value="claude_code">Claude Code trên máy này</option>
                <option value="api">Claude API (theo Cài đặt)</option>
              </select>
            </label>
            <div className="muted small">Hồ sơ luôn được viết bằng tiếng Anh, vào file .md. Bản tiếng Việt có qua nút dịch bên dưới.</div>
            <button className="btn btn-primary" disabled={busy} onClick={write}>{busy ? "Đang khởi chạy…" : "▶ Viết hồ sơ"}</button>
          </div>
        </div>
        <div className="card">
          <div className="card-head"><span>Bản tiếng Việt</span></div>
          <div className="form">
            <div className="muted small">Dịch hồ sơ tiếng Anh đang có sang tiếng Việt, giữ nguyên tên riêng, thuật ngữ, tên máy,
              metric, lệnh, số liệu. Agent chỉ được đọc file .md và ghi file .vi.md, không chạy công cụ nào.</div>
            <button className="btn" disabled={busy} onClick={translate}>文 Dịch sang tiếng Việt</button>
          </div>
        </div>
        {p && p.sessions.length > 0 && (
          <div className="card">
            <div className="card-head"><span>Các lần viết hồ sơ</span></div>
            {p.sessions.map((s) => {
              const st = STATUS[s.status] || STATUS.finished;
              return (
                <a key={s.id} className="list-item" href={`#/session/${s.id}`}>
                  <span className={`tag tag-${st.tone}`}>{st.label}</span>
                  <span className="small">{clock(s.created)} · {s.mode === "translate" ? "dịch → VI" : s.language === "vi" ? "VI" : "EN"}</span>
                </a>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
