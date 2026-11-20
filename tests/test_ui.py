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
    assert out[4]["rca"] == [{"tool": "question", "case": "bank-114", "args": ""}]
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


def test_api_backend_runs_only_rca_tool_commands():
    from rca.ui.agents import Run

    run = Run("api", "bank-114", "Bank", "ui-test", lambda e: None, lambda s: None)
    for command in ("cat README.md", "uv run python -m rca cases", "uv run python -m rca tool question bank-114; cat x",
                    "uv run python -m rca tool question bank-114 | head", "python -m rca tool question bank-114"):
        output, is_error = run._execute(command)
        assert is_error and output.startswith("Permission denied"), command
