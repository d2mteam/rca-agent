"""The UI's HTTP API end to end: a real server on a free port, real data, an agent stand-in that costs nothing."""

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from rca.ui import server, settings

DATA = Path("data")
pytestmark = pytest.mark.skipif(not (DATA / "openrca" / "Bank" / "query.csv").exists(), reason="needs the Bank data")


class FakeRun:
    """Emits a short session and ends when released (or at once with auto=True)."""
    release: dict = {}
    auto = True

    def __init__(self, backend, task, prompt, emit, finish):
        self.task, self.prompt, self.emit, self.finish = task, prompt, emit, finish
        self.case = task.get("case") or f"{task['system'].lower()}-profile"
        FakeRun.release[self.case] = threading.Event()

    def start(self):
        def go():
            self.emit({"kind": "text", "text": "Looking at the overview."})
            self.emit({"kind": "tool_call", "id": "t1", "name": "Bash", "command": f"uv run python -m rca tool overview {self.case}",
                       "rca": [{"tool": "overview", "case": self.case, "args": ""}]})
            self.emit({"kind": "tool_result", "id": "t1", "output": "Business KPIs ...", "is_error": False, "ms": 12})
            if not FakeRun.auto:
                FakeRun.release[self.case].wait(10)
            if self.task.get("write"):  # a profile or translation run writes its file
                Path(self.task["write"]).write_text("# profile\n")
            self.emit({"kind": "done", "status": "finished", "duration_ms": 30})
            self.finish("finished")
        threading.Thread(target=go, daemon=True).start()

    def stop(self):
        FakeRun.release[self.case].set()


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(server, "Run", FakeRun)
    from rca.ui import profiles
    monkeypatch.setattr(profiles, "ROOT", tmp_path)
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "telecom.md").write_text("# Telecom profile\n")
    FakeRun.auto = True
    app = server.App(DATA, tmp_path / "runs")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None, header=True):
        req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Content-Type", "application/json")
        if header and method == "POST":
            req.add_header("X-RCA-UI", "1")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get_content_type() == "application/json" else raw.decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    call.base, call.app, call.tmp = base, app, tmp_path
    yield call
    httpd.shutdown()


def wait_status(call, session_id, wanted, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        status = call("GET", f"/api/sessions/{session_id}")[1]["meta"]["status"]
        if status in wanted:
            return status
        time.sleep(0.05)
    raise AssertionError(f"{session_id} never reached {wanted}")


def test_pages_and_lookups(api):
    status, page = api("GET", "/")
    assert status == 200 and "<div id=\"root\">" in page
    assert api("GET", "/cases/Bank")[0] == 200  # client-side routes get the app
    status, systems = api("GET", "/api/systems")
    assert status == 200 and {s["name"] for s in systems} >= {"Bank", "Telecom"}
    status, cases = api("GET", "/api/cases?system=Bank")
    assert status == 200 and len(cases) == 136 and cases[0]["id"] == "bank-001" and "faults" in cases[0]
    assert "Tomcat02" not in json.dumps(cases[113])  # the answer never leaves the server with the case list
    assert api("GET", "/api/nothing")[0] == 404
    assert api("GET", "/api/sessions/nope")[0] == 404
    assert api("GET", "/api/cases?system=Nope")[0] == 400


def test_settings_round_trip_keeps_the_key_server_side(api):
    status, s = api("GET", "/api/settings")
    assert status == 200 and s["has_key"] is False and s["language"] == "en"
    status, s = api("POST", "/api/settings", {"api_key": "sk-test-abcd1234", "model": "claude-sonnet-5-5", "language": "vi"})
    assert status == 200 and s["api_key"].endswith("1234") and "sk-test" not in s["api_key"] and s["language"] == "vi"
    assert json.loads((api.tmp / "settings.json").read_text())["api_key"] == "sk-test-abcd1234"
    assert api("POST", "/api/settings", {"model": "x"}, header=False)[0] == 403


def test_run_streams_scores_and_answers(api):
    status, meta = api("POST", "/api/run", {"system": "Bank", "case": "bank-114", "backend": "claude_code"})
    assert status == 200 and meta["status"] in ("running", "queued")
    wait_status(api, meta["id"], ("finished",))
    status, s = api("GET", f"/api/sessions/{meta['id']}")
    kinds = [e["kind"] for e in s["events"]]
    assert kinds[0] == "question" and "tool_call" in kinds and kinds[-1] == "done"
    with urllib.request.urlopen(api.base + f"/api/sessions/{meta['id']}/stream?from=0", timeout=10) as r:
        body = r.read().decode()
    assert "event: end" in body and body.count("data: {\"i\"") == len(s["events"])
    status, answer = api("GET", f"/api/sessions/{meta['id']}/answer")
    assert status == 200 and answer["truth"][0]["component"] == "Tomcat02" and answer["answers"] == [] and answer["score"] == 0
    assert any(m["id"] == meta["id"] for m in api("GET", "/api/sessions")[1])


def test_runs_queue_and_stop(api):
    FakeRun.auto = False
    first = api("POST", "/api/run", {"system": "Bank", "case": "bank-001", "backend": "claude_code"})[1]
    second = api("POST", "/api/run", {"system": "Bank", "case": "bank-002", "backend": "claude_code"})[1]
    assert wait_status(api, first["id"], ("running",)) == "running"
    assert api("GET", f"/api/sessions/{second['id']}")[1]["meta"]["status"] == "queued"
    assert api("GET", f"/api/sessions/{second['id']}/answer")[0] == 409
    assert api("POST", f"/api/sessions/{second['id']}/stop")[1] == {"stopped": True}
    assert api("GET", f"/api/sessions/{second['id']}")[1]["meta"]["status"] == "stopped"
    FakeRun.release["bank-001"].set()
    assert wait_status(api, first["id"], ("finished",)) == "finished"
    assert api("POST", "/api/run", {"system": "Bank", "case": "bank-999"})[0] == 400
    assert api("POST", "/api/run", {"system": "Bank", "case": "bank-001", "backend": "x"})[0] == 400
    assert api("POST", "/api/run", {"system": "Bank", "case": "bank-001"}, header=False)[0] == 403


def test_profile_endpoints(api):
    status, p = api("GET", "/api/profile?system=Telecom&language=en")
    assert status == 200 and p["exists"] and p["content"].startswith("# Telecom") and len(p["windows"]) == 6
    assert api("GET", "/api/profile?system=Telecom&language=vi")[1]["exists"] is False
    # Bad windows are refused before anything runs.
    assert api("POST", "/api/profile/run", {"system": "Bank", "windows": ["2021_03_06-0930"]})[0] == 400
    assert api("POST", "/api/profile/run", {"system": "Bank", "windows": ["normal-2021_03_04-1500"]})[0] == 400
    # An existing profile needs overwrite; a second run of the same system is refused while one is going.
    assert api("POST", "/api/profile/run", {"system": "Telecom"})[0] == 400
    FakeRun.auto = False
    status, meta = api("POST", "/api/profile/run", {"system": "Bank", "windows": ["normal-2021_03_06-0930"]})
    assert status == 200 and meta["kind"] == "profile"
    assert api("POST", "/api/profile/run", {"system": "Bank", "overwrite": True})[0] == 400
    FakeRun.release["bank-profile"].set()
    assert wait_status(api, meta["id"], ("finished",)) == "finished"
    assert (api.tmp / "profiles" / "bank.md").read_text() == "# profile\n"
    assert api("GET", "/api/profile?system=Bank&language=en")[1]["windows"] == ["normal-2021_03_06-0930"]  # saved


def test_translation_endpoint(api):
    FakeRun.auto = True
    assert api("POST", "/api/profile/translate", {"system": "Market1"})[0] == 400  # no English profile to translate
    status, meta = api("POST", "/api/profile/translate", {"system": "Telecom"})
    assert status == 200 and meta["mode"] == "translate"
    assert wait_status(api, meta["id"], ("finished",)) == "finished"
    assert (api.tmp / "profiles" / "telecom.vi.md").exists()
    assert api("POST", "/api/profile/translate", {"system": "Telecom"})[0] == 400  # now needs overwrite


def test_a_profile_run_that_writes_nothing_fails(api, monkeypatch):
    class Silent(FakeRun):
        def start(self):
            threading.Thread(target=lambda: self.finish("finished"), daemon=True).start()
    monkeypatch.setattr(server, "Run", Silent)
    status, meta = api("POST", "/api/profile/translate", {"system": "Telecom"})
    assert wait_status(api, meta["id"], ("error",)) == "error"
    assert any(e["kind"] == "error" for e in api("GET", f"/api/sessions/{meta['id']}")[1]["events"])
