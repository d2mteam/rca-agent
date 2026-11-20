import { useEffect, useState } from "react";
import { api } from "./api.js";
import Sidebar from "./Sidebar.jsx";
import CasesPage from "./CasesPage.jsx";
import SessionPage from "./SessionPage.jsx";
import SessionsPage from "./SessionsPage.jsx";
import SettingsPage from "./SettingsPage.jsx";

function useHash() {
  const [hash, setHash] = useState(window.location.hash || "#/cases");
  useEffect(() => {
    const on = () => setHash(window.location.hash || "#/cases");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash.slice(2).split("/");
}

export default function App() {
  const route = useHash();
  const [sessions, setSessions] = useState([]);
  const refresh = () => api.sessions().then(setSessions).catch(() => {});

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  let page;
  if (route[0] === "session" && route[1]) page = <SessionPage id={route[1]} onChange={refresh} />;
  else if (route[0] === "sessions") page = <SessionsPage sessions={sessions} />;
  else if (route[0] === "settings") page = <SettingsPage />;
  else page = <CasesPage system={route[1]} view={route[2]} sessions={sessions} onRun={refresh} />;

  return (
    <div className="layout">
      <Sidebar route={route} sessions={sessions} />
      <div className="main">{page}</div>
    </div>
  );
}
