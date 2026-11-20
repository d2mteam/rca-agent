from rca.ui import events as ev
from rca.ui import settings as cfg


def _call(i, command):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"t{i}", "name": "Bash",
                                                          "input": {"command": command}}]}}


def _result(i, text):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": text}]}}


def test_stream_lines_become_events():
    lines = [
        {"type": "system", "subtype": "init", "model": "claude-sonnet-5-5", "tools": ["Bash", "Read"]},
        {"type": "user", "message": {"content": "Solve bank-114"}},
        {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "Check KPIs first."},
                                                      {"type": "text", "text": "Start with the question."}]}},
        _call(1, "uv run python -m rca tool question bank-114"),
        _result(1, [{"type": "text", "text": "During the specified time range..."}]),
        {"type": "result", "is_error": False, "duration_ms": 1200, "total_cost_usd": 0.1, "num_turns": 3},
    ]
    out = [e for line in lines for e in ev.normalise(line)]
    assert [e["kind"] for e in out] == ["info", "prompt", "thinking", "text", "tool_call", "tool_result", "done"]
    assert out[4]["rca"] == [{"tool": "question", "case": "bank-114", "args": "", "argv": []}]
    assert out[5]["output"] == "During the specified time range..."
    assert out[6]["status"] == "finished"


def test_shell_variables_are_expanded_to_find_the_case():
    command = 'B="uv run python -m rca tool"; C=bank-033\n$B series $C Tomcat01 FIN; $B hypothesis ${C} list'
    assert [(c["tool"], c["case"]) for c in ev.rca_calls(command)] == [("series", "bank-033"), ("hypothesis", "bank-033")]


def test_tidy_drops_unanswered_calls_and_repeated_text():
    events = [{"kind": "text", "text": "a"}, {"kind": "text", "text": "a"},
              {"kind": "tool_call", "id": "x", "rca": []}, {"kind": "tool_call", "id": "y", "rca": []},
              {"kind": "tool_result", "id": "y", "output": ""}]
    assert [(e["kind"], e.get("id")) for e in ev.tidy(events)] == [("text", None), ("tool_call", "y"), ("tool_result", "y")]


def test_split_by_case_gives_a_chained_call_to_both_cases():
    lines = [_call(1, "uv run python -m rca tool question bank-001"), _result(1, "q1"),
             _call(2, "uv run python -m rca tool submit bank-001 --time x; uv run python -m rca tool question bank-002"),
             _result(2, "both"),
             _call(3, "uv run python -m rca tool overview bank-002"), _result(3, "o2")]
    events = [e for line in lines for e in ev.normalise(line)]
    first = ev.split_by_case(events, "bank-001", ["bank-001", "bank-002"])
    second = ev.split_by_case(events, "bank-002", ["bank-001", "bank-002"])
    assert [e.get("id") for e in first] == ["t1", "t1", "t2", "t2"]
    assert [e.get("id") for e in second] == ["t2", "t2", "t3", "t3"]


def test_hypotheses_are_read_from_the_tool_output():
    text = ("#1 [refuted] Tomcat02 / JVM Out of Memory (OOM) Heap\n    if true: heap near max\n    if false: heap normal\n"
            "    evidence: heap 41-48%\n#2 [supported] Tomcat02 / high CPU usage\n    if true: CPU up\n    if false: flat")
    found = ev.parse_hypotheses(text)
    assert [(h["id"], h["status"], h["component"], h["reason"]) for h in found] == [
        (1, "refuted", "Tomcat02", "JVM Out of Memory (OOM) Heap"), (2, "supported", "Tomcat02", "high CPU usage")]
    assert found[0]["evidence"] == ["heap 41-48%"]


def test_settings_mask_the_key_and_map_to_claude_variables(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "PATH", tmp_path / "settings.json")
    s = cfg.save({"provider": "custom", "base_url": "https://llm.example.com", "auth": "auth_token",
                  "api_key": "sk-secret-1234", "model": "some-model", "claude_code_use_provider": True})
    assert cfg.public(s)["api_key"] == "••••••••1234"
    assert not cfg.official(s)
    assert cfg.claude_env(s) == {"ANTHROPIC_BASE_URL": "https://llm.example.com", "ANTHROPIC_AUTH_TOKEN": "sk-secret-1234",
                                 "ANTHROPIC_MODEL": "some-model"}
    # Sending the mask back keeps the stored key.
    assert cfg.save({"api_key": "••••••••1234"})["api_key"] == "sk-secret-1234"
    assert (tmp_path / "settings.json").stat().st_mode & 0o777 == 0o600


def test_vietnamese_is_added_only_when_chosen():
    from rca.ui.agents import VIETNAMESE_REPORT, WRITING, instructions

    english = instructions("bank-114", "Bank", True)
    vietnamese = instructions("bank-114", "Bank", True, "vi")
    # The investigation stays in English; Vietnamese only adds a Vietnamese copy of the final report.
    assert WRITING in english and english.index(WRITING) < english.index("## Tools")
    assert VIETNAMESE_REPORT not in english and vietnamese == english + VIETNAMESE_REPORT


def test_profile_windows_are_checked_and_written_into_the_instructions():
    import pytest
    from rca.cases import Case
    from rca.ui import profiles

    cases = [Case("bank-001", "task_1", "", frozenset({"time"}), 1614839400, 1614841200, (), "Bank")]  # 03-04 14:30-15:00
    with pytest.raises(ValueError, match="not a window"):
        profiles.check(["2021_03_06-0930"], cases)
    with pytest.raises(ValueError, match="within an hour"):
        profiles.check(["normal-2021_03_04-1530"], cases)
    english, out = profiles.instructions("Bank", ["normal-2021_03_06-0930"], cases)
    assert "normal-2021_03_06-0930" in english and out.name == "bank.md"  # profiles are surveyed in English


def test_runs_wait_their_turn(tmp_path, monkeypatch):
    import threading
    from pathlib import Path
    from rca.ui import server

    release = {}

    class FakeRun:
        def __init__(self, backend, task, prompt, emit, finish):
            self.case, self.finish, self.started = task["case"], finish, threading.Event()
            release[self.case] = threading.Event()

        def start(self):
            self.started.set()
            threading.Thread(target=lambda: (release[self.case].wait(5), self.finish("finished")), daemon=True).start()

        def stop(self):
            release[self.case].set()

    monkeypatch.setattr(server, "Run", FakeRun)
    app = server.App(Path("data"), tmp_path)
    first = app.start("Bank", "bank-001", "claude_code")
    second = app.start("Bank", "bank-002", "claude_code")
    assert app.store.meta(first["id"])["status"] == "running"
    assert app.store.meta(second["id"])["status"] == "queued"
    release["bank-001"].set()
    for _ in range(50):
        if app.store.meta(second["id"])["status"] == "running":
            break
        threading.Event().wait(0.05)
    assert app.store.meta(first["id"])["status"] == "finished"
    assert app.store.meta(second["id"])["status"] == "running"
    release["bank-002"].set()


def test_rca_calls_keep_quoted_evidence_whole():
    command = ('R="--run x"; uv run python -m rca tool hypothesis bank-114 test 1 --status supported '
               '--evidence "gc log: 1/min; jumps to 36 | heap 41->48%" $R; uv run python -m rca tool submit --help 2>&1')
    calls = ev.rca_calls(command)
    assert calls[0]["tool"] == "hypothesis" and calls[0]["argv"] == [
        "test", "1", "--status", "supported", "--evidence", "gc log: 1/min; jumps to 36 | heap 41->48%", "--run", "x"]
    assert calls[1]["tool"] == "submit" and calls[1]["case"] == "--help"


def test_backslash_continued_commands_are_one_command():
    command = ('uv run python -m rca tool hypothesis bank-001 add --run x \\\n  --component MG02 --reason "network latency" \\\n'
               '  --if-true "span duration lên 494 ms; trước Tomcat02" --if-false "bình thường"')
    calls = ev.rca_calls(command)
    assert len(calls) == 1 and calls[0]["argv"][calls[0]["argv"].index("--component") + 1] == "MG02"



def test_the_agent_gets_only_the_project_tools():
    from rca.ui import toolspec

    case = {"mode": "case", "system": "Bank", "case": "bank-114", "run": "ui-x", "read": "/repo/profiles/bank.md", "write": ""}
    names = [t["name"] for t in toolspec.definitions(case)]
    assert names == ["question", "overview", "calls", "series", "logs", "baro", "exclusive", "traces", "onset",
                     "hypothesis_add", "hypothesis_test", "hypothesis_list", "submit", "batch", "read_profile"]
    # The case id and the run name are filled in, never typed by the agent.
    assert toolspec.argv(case, "series", {"component": "Tomcat02", "pattern": "CPU"}) == [
        "tool", "series", "bank-114", "Tomcat02", "CPU"]
    assert toolspec.argv(case, "hypothesis_test", {"id": 2, "status": "refuted", "evidence": "x; y"}) == [
        "tool", "hypothesis", "bank-114", "test", "2", "--run", "ui-x", "--status", "refuted", "--evidence", "x; y"]
    assert toolspec.call(case, "bash", {"command": "cat README.md"}) == ("unknown tool 'bash'", True)
    profile = {"mode": "profile", "system": "Bank", "windows": ["normal-2021_03_06-0930"], "read": "", "write": "/tmp/p.md"}
    assert [t["name"] for t in toolspec.definitions(profile)][-2:] == ["batch", "write_profile"]
    assert toolspec.argv(profile, "overview", {"window": "normal-2021_03_06-0930"}) == [
        "--system", "Bank", "tool", "overview", "normal-2021_03_06-0930"]
    assert toolspec.call(profile, "overview", {"window": "normal-2021_03_07-0930"})[1] is True  # not a given window
    translate = {"mode": "translate", "system": "Bank", "read": "/repo/profiles/bank.md", "write": "/repo/profiles/bank.vi.md"}
    assert [t["name"] for t in toolspec.definitions(translate)] == ["read_profile", "write_profile"]


def test_claude_code_command_has_no_builtin_tools():
    import json
    from rca.ui import agents, settings

    task = {"mode": "case", "system": "Bank", "case": "bank-114", "run": "ui-x", "read": "", "write": ""}
    command, env = agents.Run("claude_code", task, "prompt", None, None).command(settings.DEFAULTS)
    assert command[command.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in command and command[command.index("--allowedTools") + 1] == "mcp__rca"
    server = json.loads(command[command.index("--mcp-config") + 1])["mcpServers"]["rca"]
    assert server["args"] == ["-m", "rca.ui.mcp_tools"] and json.loads(server["env"]["RCA_UI_TASK"]) == task


def test_mcp_server_lists_and_calls_the_tools():
    import json, os, subprocess, sys
    task = {"mode": "translate", "system": "Bank", "read": __file__, "write": ""}
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_profile", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "overview", "arguments": {}}}]
    out = subprocess.run([sys.executable, "-m", "rca.ui.mcp_tools"], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                         capture_output=True, text=True, env={**os.environ, "RCA_UI_TASK": json.dumps(task)}, timeout=60).stdout
    replies = {r["id"]: r for r in map(json.loads, out.splitlines())}
    assert [t["name"] for t in replies[2]["result"]["tools"]] == ["read_profile", "write_profile"]
    assert "test_mcp_server_lists_and_calls_the_tools" in replies[3]["result"]["content"][0]["text"]
    assert replies[4]["result"]["isError"] is True  # a tool this task does not have



def test_investigation_tools_can_return_part_of_their_output():
    from rca.ui import toolspec

    case = {"mode": "case", "system": "Bank", "case": "bank-114", "run": "ui-x", "read": "", "write": ""}
    schema = {t["name"]: t["input_schema"]["properties"] for t in toolspec.definitions(case)}
    assert {"filter", "head", "tail"} <= set(schema["baro"]) and "filter" not in schema["submit"]
    text = "a Tomcat01\nb tomcat02\nc MG01\nd Tomcat03"
    assert toolspec.trim(text, {"filter": "TOMCAT"}) == "a Tomcat01\nb tomcat02\nd Tomcat03"
    assert toolspec.trim(text, {"filter": "tomcat", "head": 2}) == "a Tomcat01\nb tomcat02"
    assert toolspec.trim(text, {"tail": 1}) == "d Tomcat03"
    assert toolspec.trim(text, {"filter": "redis"}).startswith("(no line left")


def test_instructions_allow_several_tools_per_turn():
    from rca.ui.agents import instructions
    assert "several tools in one turn" in instructions("bank-114", "Bank", True)



def test_batch_runs_several_tools_in_one_call():
    from rca.ui import events, toolspec

    case = {"mode": "case", "system": "Bank", "case": "bank-114", "run": "ui-batch-test", "read": "", "write": ""}
    calls = [{"tool": "question", "head": 1}, {"tool": "series", "component": "Tomcat02", "pattern": "JVMMemoryUsed",
                                                "tail": 1}, {"tool": "series", "component": "Nope", "pattern": "x"}]
    output, failed = toolspec.call(case, "batch", {"calls": calls})
    assert not failed  # one of three failed, not all
    assert output.count("=== ") == 3 and "=== question(head=1) ===" in output and "16:28=48.00" in output
    assert "[error]" in output.split("=== series(component='Nope'")[1]
    assert toolspec.call(case, "batch", {"calls": [{"tool": "batch", "calls": []}]})[1] is True
    # The UI sees every call inside the batch.
    shown = events.tool_call_event("t", "mcp__rca__batch", {"calls": calls})
    assert [c["tool"] for c in shown["rca"]] == ["question", "series", "series"]
