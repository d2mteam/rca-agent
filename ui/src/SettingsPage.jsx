import { useEffect, useState } from "react";
import { api } from "./api.js";

const MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"];

export default function SettingsPage() {
  const [s, setS] = useState(null);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { api.settings().then(setS).catch((e) => setErr(e.message)); }, []);
  if (!s) return <div className="page"><div className="content">{err ? <div className="alert alert-error">{err}</div> : "Đang tải…"}</div></div>;
  const set = (k) => (e) => setS({ ...s, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const official = s.provider === "anthropic";

  async function save() {
    setMsg(""); setErr("");
    try {
      setS(await api.saveSettings({ ...s, max_iterations: +s.max_iterations }));
      setMsg("Đã lưu.");
    } catch (e) { setErr(e.message); }
  }

  return (
    <div className="page">
      <div className="page-header">
        <div className="breadcrumb">Cài đặt</div>
        <h1>Cài đặt</h1>
      </div>
      <div className="content narrow">
        {err && <div className="alert alert-error">{err}</div>}
        {msg && <div className="alert alert-success">{msg}</div>}
        <div className="card">
          <div className="card-head"><span>Nhà cung cấp model</span></div>
          <div className="form">
            <label>Nhà cung cấp
              <select className="input" value={s.provider} onChange={set("provider")}>
                <option value="anthropic">Anthropic</option>
                <option value="custom">Bên thứ ba (API tương thích Anthropic)</option>
              </select>
            </label>
            <label>Base URL
              <input className="input mono" placeholder={official ? "https://api.anthropic.com (mặc định)" : "https://…"}
                value={s.base_url} onChange={set("base_url")} />
            </label>
            <label>Kiểu xác thực
              <select className="input" value={s.auth} onChange={set("auth")}>
                <option value="api_key">API key (x-api-key · ANTHROPIC_API_KEY)</option>
                <option value="auth_token">Bearer token (Authorization · ANTHROPIC_AUTH_TOKEN)</option>
              </select>
            </label>
            <label>Key
              <input className="input mono" type="password" autoComplete="off"
                placeholder={s.has_key ? "" : "chưa có; để trống thì dùng thông tin đăng nhập sẵn trên máy"}
                value={s.api_key} onChange={set("api_key")} />
            </label>
            <label>Model
              <input className="input mono" list="models" value={s.model} onChange={set("model")} />
              <datalist id="models">{MODELS.map((m) => <option key={m} value={m} />)}</datalist>
            </label>
            {official && (
              <label>Mức suy luận (effort)
                <select className="input" value={s.effort} onChange={set("effort")}>
                  {["low", "medium", "high", "xhigh", "max"].map((e) => <option key={e}>{e}</option>)}
                </select>
              </label>
            )}
            <label className="check"><input type="checkbox" checked={s.thinking} onChange={set("thinking")} /> Trả về tóm tắt suy luận (thinking)
              {!official && <span className="muted"> — chỉ bật nếu nhà cung cấp hỗ trợ</span>}
            </label>
            <label>Số vòng tối đa (chế độ API)
              <input className="input" type="number" min="5" max="200" value={s.max_iterations} onChange={set("max_iterations")} />
            </label>
          </div>
        </div>
        <div className="card">
          <div className="card-head"><span>Claude Code trên máy này</span></div>
          <div className="form">
            <label className="check"><input type="checkbox" checked={s.claude_code_use_provider} onChange={set("claude_code_use_provider")} />
              Dùng nhà cung cấp ở trên cho Claude Code (truyền ANTHROPIC_BASE_URL, key, ANTHROPIC_MODEL)</label>
            {!s.claude_code_use_provider && (
              <label>Model của Claude Code
                <input className="input mono" list="cc-models" value={s.claude_code_model} onChange={set("claude_code_model")} />
                <datalist id="cc-models">{["sonnet", "opus", "haiku", ...MODELS].map((m) => <option key={m} value={m} />)}</datalist>
              </label>
            )}
            <div className="muted small">Khi không bật, Claude Code dùng tài khoản đang đăng nhập trên máy.</div>
          </div>
        </div>
        <div className="card">
          <div className="card-head"><span>Agent</span></div>
          <div className="form">
            <label className="check"><input type="checkbox" checked={s.use_profile} onChange={set("use_profile")} />
              Cho agent đọc hồ sơ hệ thống (profiles/) khi hệ đó có hồ sơ</label>
            <label>Báo cáo cuối
              <select className="input" value={s.language} onChange={set("language")}>
                <option value="en">English</option>
                <option value="vi">English + bản tiếng Việt</option>
              </select>
            </label>
            <div className="muted small">Agent luôn điều tra bằng tiếng Anh. Khi chọn thêm tiếng Việt, báo cáo cuối có thêm bản tiếng Việt.</div>
          </div>
        </div>
        <button className="btn btn-primary" onClick={save}>Lưu cài đặt</button>
        <div className="muted small">Lưu ở ~/.config/rca-agent/settings.json, chỉ chủ máy đọc được. Key không bao giờ được gửi lại trình duyệt.</div>
      </div>
    </div>
  );
}
