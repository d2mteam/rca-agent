import { STATUS, clock } from "./util.js";

export default function SessionsPage({ sessions }) {
  return (
    <div className="page">
      <div className="page-header">
        <div className="breadcrumb">Lịch sử phiên</div>
        <h1>Lịch sử phiên</h1>
      </div>
      <div className="content">
        <div className="card">
          <div className="card-head"><span>{sessions.length} phiên</span></div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>Phiên</th><th>Câu hỏi</th><th>Hệ</th><th>Cách chạy</th><th>Model</th><th>Bắt đầu</th><th>Trạng thái</th><th>Điểm</th></tr>
              </thead>
              <tbody>
                {sessions.map((s) => {
                  const st = STATUS[s.status] || STATUS.finished;
                  return (
                    <tr key={s.id} onClick={() => (window.location.hash = `#/session/${s.id}`)}>
                      <td className="mono">{s.id}</td>
                      <td className="mono">{s.case}</td>
                      <td>{s.system}</td>
                      <td>{s.imported ? "đã lưu (nhập)" : s.backend === "api" ? "Claude API" : "Claude Code"}</td>
                      <td className="mono">{s.model || "–"}</td>
                      <td>{clock(s.created)}</td>
                      <td><span className={`tag tag-${st.tone}`}>{st.label}</span></td>
                      <td>{s.score != null ? `${Math.round(s.score * 100)}%` : "–"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
