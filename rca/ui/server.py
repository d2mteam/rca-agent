"""HTTP server of the UI: the React app, the cases, the sessions (live over SSE) and the settings.

Binds to 127.0.0.1. Requests that start or stop a run, or change settings, must carry the header
X-RCA-UI: 1, which a page on another origin cannot send without a CORS preflight this server refuses.
"""

import json
import mimetypes
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from rca import systems
from rca.cases import CST, load_cases
from rca.scoring import load_predictions, score_case
from rca.ui import settings as cfg
from rca.ui import profiles
from rca.ui.agents import Run, profile_path
from rca.ui.sessions import Store

STATIC = Path(__file__).parent / "static"


class App:
    def __init__(self, data: Path, runs: Path):
        self.data, self.runs = data, runs
        self.store = Store(runs)
        self.active: dict[str, Run] = {}
        self._cases: dict[str, list] = {}
        for meta in self.store.list():
            # A run that was live when the server stopped can no longer finish.
            if meta.get("status") == "running":
                self.store.update(meta["id"], status="interrupted")
            # Imported sessions get their score once.
            elif "score" not in meta:
                try:
                    self.store.update(meta["id"], score=self.score(meta["id"]))
                except (ValueError, SystemExit, OSError):
                    pass

    def cases(self, system: str) -> list:
        if system not in self._cases:
            s = systems.get(system)
            path = self.data / "openrca" / s.path
            self._cases[system] = load_cases(path, system=s.name, prefix=s.case_prefix) if (path / "query.csv").exists() else []
        return self._cases[system]

    def case(self, system: str, case_id: str):
        return next((c for c in self.cases(system) if c.case_id == case_id), None)

    def system_list(self) -> list[dict]:
        out = []
        for name, s in systems.SYSTEMS.items():
            cases = self.cases(name)
            if cases:
                prepared = self.data / "parquet" / s.path
                out.append({"name": name, "cases": len(cases), "profile": profile_path(name) is not None,
                            "profiles": {lang: profiles.path(name, lang).exists() for lang in ("en", "vi")},
                            "prepared_days": sorted(p.name for p in prepared.iterdir() if p.is_dir()) if prepared.exists() else []})
        return out

    def case_list(self, system: str) -> list[dict]:
        counts: dict[str, int] = {}
        for meta in self.store.list():
            counts[meta["case"]] = counts.get(meta["case"], 0) + 1
        fmt = lambda ts: datetime.fromtimestamp(ts, CST).strftime("%Y-%m-%d %H:%M")
        prepared = {p.name for p in (self.data / "parquet" / systems.get(system).path).glob("*") if p.is_dir()}
        return [{"id": c.case_id, "task": c.task, "asks": sorted(c.asks), "start": fmt(c.start), "end": fmt(c.end)[11:],
                 "faults": len(c.faults), "instruction": c.instruction, "sessions": counts.get(c.case_id, 0),
                 "ready": c.day in prepared} for c in self.cases(system)]

    def start(self, system: str, case_id: str, backend: str) -> dict:
        if backend not in ("claude_code", "api"):
            raise ValueError(f"unknown backend {backend!r}")
        case = self.case(system, case_id)
        if case is None:
            raise ValueError(f"no case {case_id} in {system}")
        s = cfg.load()
        model = s["model"] if backend == "api" or s["claude_code_use_provider"] else s["claude_code_model"]
        meta = self.store.create(case=case_id, system=system, backend=backend, model=model,
                                 base_url=s["base_url"] if backend == "api" or s["claude_code_use_provider"] else "",
                                 profile=bool(s["use_profile"] and profile_path(system)), language=s["language"])
        session_id = meta["id"]
        self.store.append(session_id, {"kind": "question", "text": case.instruction,
                                       "window": [case.start, case.end], "asks": sorted(case.asks)})

        def finish(status: str):
            self.store.update(session_id, status=status, finished=time.time(), score=self.score(session_id))
            self.active.pop(session_id, None)

        run = Run(backend, case_id, system, meta["run"], lambda e: self.store.append(session_id, e), finish)
        self.active[session_id] = run
        run.start()
        return meta

    def profile_windows(self, system: str) -> tuple[list[str], list[str]]:
        """The windows set for a system (or the suggestion when none is set) and the suggestion itself."""
        suggestion = profiles.suggested(self.data, system, self.cases(system))
        return cfg.load()["profile_windows"].get(system) or suggestion, suggestion

    def start_profile(self, system: str, backend: str, overwrite: bool, chosen: list[str] | None) -> dict:
        if backend not in ("claude_code", "api"):
            raise ValueError(f"unknown backend {backend!r}")
        if chosen is not None:
            profiles.check(chosen, self.cases(system))
            cfg.save({"profile_windows": {**cfg.load()["profile_windows"], system: chosen}})
        chosen = self.profile_windows(system)[0]
        s = cfg.load()
        prompt, out = profiles.instructions(system, chosen, self.cases(system), s["language"])
        if out.exists() and not overwrite:
            raise ValueError(f"{out.relative_to(profiles.ROOT)} already exists")
        model = s["model"] if backend == "api" or s["claude_code_use_provider"] else s["claude_code_model"]
        meta = self.store.create(case=f"{system.lower()}-profile", system=system, backend=backend, model=model,
                                 kind="profile", output=str(out), windows=chosen, language=s["language"],
                                 base_url=s["base_url"] if backend == "api" or s["claude_code_use_provider"] else "")
        session_id = meta["id"]
        self.store.append(session_id, {"kind": "question", "text": f"Viết hồ sơ hệ thống {system} từ các cửa sổ khỏe: "
                                       + ", ".join(chosen), "asks": []})

        def finish(status: str):
            self.store.update(session_id, status=status, finished=time.time())
            self.active.pop(session_id, None)

        run = Run(backend, meta["case"], system, meta["run"], lambda e: self.store.append(session_id, e), finish,
                  profile_task=(prompt, out))
        self.active[session_id] = run
        run.start()
        return meta

    def score(self, session_id: str) -> float | None:
        result = self.answer(session_id)
        return result["score"] if result else None

    def answer(self, session_id: str) -> dict | None:
        meta = self.store.meta(session_id)
        if not meta or meta.get("kind") == "profile":
            return None
        case = self.case(meta["system"], meta["case"])
        predictions = self.runs / meta["run"] / "predictions.jsonl"
        answers = load_predictions(predictions).get(meta["case"], []) if predictions.exists() else []
        if case is None:
            return None
        result = score_case(case, answers)
        fmt = lambda ts: datetime.fromtimestamp(ts, CST).strftime("%Y-%m-%d %H:%M:%S")
        return {"score": result.score, "solved": result.solved, "asks": sorted(case.asks),
                "truth": [{"time": fmt(f.time), "component": f.component, "reason": f.reason} for f in case.faults],
                "answers": [a.__dict__ for a in answers],
                "fields": [f.__dict__ for f in result.fields]}


def handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _json(self, body, status=200):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _static(self, path: str):
            file = (STATIC / path.lstrip("/")).resolve()
            if not str(file).startswith(str(STATIC.resolve())) or not file.is_file():
                file = STATIC / "index.html"  # client-side routes
            if not file.exists():
                return self._json({"error": "the UI is not built: run npm run build in ui/"}, 404)
            data = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _stream(self, session_id: str, start: int):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            n = start
            try:
                while True:
                    for e in app.store.events(session_id, n):
                        self.wfile.write(f"data: {json.dumps(e, ensure_ascii=False)}\n\n".encode())
                        n = e["i"] + 1
                    meta = app.store.meta(session_id) or {}
                    if meta.get("status") != "running":
                        for e in app.store.events(session_id, n):
                            self.wfile.write(f"data: {json.dumps(e, ensure_ascii=False)}\n\n".encode())
                        self.wfile.write(f"event: end\ndata: {json.dumps(meta, ensure_ascii=False)}\n\n".encode())
                        self.wfile.flush()
                        return
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            parts = url.path.strip("/").split("/")
            try:
                if url.path == "/api/systems":
                    return self._json(app.system_list())
                if url.path == "/api/cases":
                    return self._json(app.case_list(q.get("system", "Bank")))
                if url.path == "/api/sessions":
                    return self._json(app.store.list())
                if url.path == "/api/settings":
                    return self._json(cfg.public(cfg.load()))
                if url.path == "/api/profile":
                    system = q.get("system", "Bank")
                    file = profiles.path(system, q.get("language", "en"))
                    chosen, suggestion = app.profile_windows(system)
                    sessions = [m for m in app.store.list() if m.get("kind") == "profile" and m["system"] == system]
                    return self._json({"path": str(file.relative_to(profiles.ROOT)), "exists": file.exists(),
                                       "content": file.read_text() if file.exists() else "", "windows": chosen,
                                       "suggested": suggestion, "sessions": sessions[:10]})
                if parts[:2] == ["api", "sessions"] and len(parts) >= 3:
                    session_id = parts[2]
                    meta = app.store.meta(session_id)
                    if meta is None:
                        return self._json({"error": "no such session"}, 404)
                    if len(parts) == 3:
                        return self._json({"meta": meta, "events": app.store.events(session_id)})
                    if parts[3] == "stream":
                        return self._stream(session_id, int(q.get("from", 0)))
                    if parts[3] == "answer":
                        if meta.get("status") == "running":
                            return self._json({"error": "the run is still going"}, 409)
                        return self._json(app.answer(session_id))
                if url.path.startswith("/api/"):
                    return self._json({"error": "not found"}, 404)
                return self._static(url.path)
            except (ValueError, SystemExit) as e:
                return self._json({"error": str(e)}, 400)

        def do_POST(self):
            if self.headers.get("X-RCA-UI") != "1":
                return self._json({"error": "missing X-RCA-UI header"}, 403)
            url = urlparse(self.path)
            parts = url.path.strip("/").split("/")
            try:
                body = self._body()
                if url.path == "/api/run":
                    return self._json(app.start(body["system"], body["case"], body.get("backend", "claude_code")))
                if url.path == "/api/settings":
                    return self._json(cfg.public(cfg.save(body)))
                if url.path == "/api/profile/run":
                    return self._json(app.start_profile(body["system"], body.get("backend", "claude_code"),
                                                        bool(body.get("overwrite")), body.get("windows")))
                if parts[:2] == ["api", "sessions"] and len(parts) == 4 and parts[3] == "stop":
                    run = app.active.get(parts[2])
                    if run:
                        run.stop()
                    return self._json({"stopped": bool(run)})
                return self._json({"error": "not found"}, 404)
            except (ValueError, KeyError, SystemExit) as e:
                return self._json({"error": str(e)}, 400)

        def do_OPTIONS(self):
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def serve(data: Path, runs: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    app = App(data, runs)
    server = ThreadingHTTPServer((host, port), handler(app))
    server.daemon_threads = True
    print(f"RCA agent UI on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for run in list(app.active.values()):
            run.stop()
