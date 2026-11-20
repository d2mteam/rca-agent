import { STATUS } from "./util.js";

const NAV = [
  { key: "cases", href: "#/cases", label: "Câu hỏi", icon: "▤" },
  { key: "sessions", href: "#/sessions", label: "Lịch sử phiên", icon: "◷" },
  { key: "settings", href: "#/settings", label: "Cài đặt", icon: "⚙" },
];

export default function Sidebar({ route, sessions }) {
  const recent = sessions.slice(0, 30);
  return (
    <aside className="sider">
      <div className="logo">
        <span className="logo-mark">RCA</span>
        <span className="logo-text">Agent Dashboard</span>
      </div>
      <a className="new-chat" href="#/cases">＋ Phiên mới</a>
      <nav className="menu">
        {NAV.map((n) => (
          <a key={n.key} href={n.href} className={"menu-item" + (route[0] === n.key ? " active" : "")}>
            <span className="menu-icon">{n.icon}</span>
            {n.label}
          </a>
        ))}
      </nav>
      <div className="history-title">Gần đây</div>
      <div className="history">
        {recent.map((s) => (
          <a
            key={s.id}
            href={`#/session/${s.id}`}
            className={"history-item" + (route[1] === s.id ? " active" : "")}
            title={s.id}
          >
            <span className={`dot dot-${(STATUS[s.status] || STATUS.finished).tone}`} />
            <span className="history-case">{s.case}</span>
            <span className="history-meta">{s.score != null ? `${Math.round(s.score * 100)}%` : ""}</span>
          </a>
        ))}
      </div>
    </aside>
  );
}
