import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from hitl import planner
from ruamel.yaml import YAML

COMPOSE = """# Keep this user's comment
services:
  nginx:
    image: nginx:1.28-alpine
    ports: ['127.0.0.1:8080:80']
  backend:
    image: hitl-demo-backend:1.0.0
    environment:
      SPRING_PROFILES_ACTIVE: 'demo'
    mem_limit: 256m
    deploy:
      replicas: 2
    healthcheck:
      test: [CMD, curl, -f, 'http://localhost:8080/health']
  db:
    image: postgres:16-alpine
    volumes: ['data:/var/lib/postgresql/data']
volumes:
  data: {}
"""


def _step(kind="plan", **kwargs):
    values = dict(
        kind=kind,
        compose_yaml=COMPOSE if kind == "plan" else None,
        summary="Đề xuất cấu hình.",
        clarification=None,
        tool_name=None,
        arguments=None,
    )
    values.update(kwargs)
    return planner._PlannerStep.model_validate(values)


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", ["Triển khai demo", "deploy current compose"])
async def test_template_deploy_keeps_full_topology(prompt):
    result = await planner.generate_proposal(prompt, COMPOSE, {}, "template")
    assert result.compose_yaml == COMPOSE
    assert result.clarification is None
    assert "không dùng AI" in result.summary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt", ["Tăng backend lên 3 replicas và RAM 512m", "scale backend to 3 replicas and memory 512 MB"]
)
async def test_template_adjustment_preserves_other_configuration(prompt):
    result = await planner.generate_proposal(prompt, COMPOSE, {}, "template")
    before = YAML(typ="safe").load(COMPOSE)
    after = YAML(typ="safe").load(result.compose_yaml)
    assert result.clarification is None
    assert after["services"]["backend"]["deploy"]["replicas"] == 3
    assert after["services"]["backend"]["mem_limit"] == "512m"
    after["services"]["backend"]["deploy"]["replicas"] = 2
    after["services"]["backend"]["mem_limit"] = "256m"
    assert after == before
    assert "# Keep this user's comment" in result.compose_yaml


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "Thêm Redis",
        "Tăng backend lên 3 replicas và thêm Redis",
        "đổi port thành 9090",
        "deploy and delete db",
    ],
)
async def test_template_unknown_request_clarifies_without_partial_change(prompt):
    result = await planner.generate_proposal(prompt, COMPOSE, {}, "template")
    assert result.clarification
    assert result.compose_yaml == COMPOSE


@pytest.mark.asyncio
async def test_template_needs_service_for_multiservice_adjustment():
    result = await planner.generate_proposal("set replicas 3", COMPOSE, {}, "template")
    assert "service nào" in result.clarification
    assert result.compose_yaml == COMPOSE


@pytest.mark.asyncio
async def test_template_does_not_replace_empty_project_with_trivial_demo():
    result = await planner.generate_proposal("deploy demo", "", {}, "template")
    assert result.clarification
    assert result.compose_yaml == ""


@pytest.mark.asyncio
async def test_codex_read_loop_uses_only_bound_observation_tool(monkeypatch):
    mocked_model = AsyncMock(
        side_effect=[
            _step("observe", tool_name="recent_logs", arguments={"service": "backend", "tail": 50}),
            _step(compose_yaml=COMPOSE.replace("replicas: 2", "replicas: 3")),
        ]
    )
    monkeypatch.setattr(planner, "_run_codex", mocked_model)
    read_tool = AsyncMock(return_value={"logs": "healthy"})
    result = await planner.generate_proposal(
        "scale backend to 3", COMPOSE, {"running": True}, "codex", read_tool
    )
    read_tool.assert_awaited_once_with("recent_logs", {"service": "backend", "tail": 50})
    assert "replicas: 3" in result.compose_yaml
    second_prompt = json.loads(mocked_model.await_args_list[1].args[0])
    assert second_prompt["current_yaml"] == COMPOSE
    assert second_prompt["request"] == "scale backend to 3"
    assert second_prompt["read_results"][0]["tool_name"] == "recent_logs"


@pytest.mark.asyncio
async def test_codex_stops_after_four_model_responses(monkeypatch):
    mocked_model = AsyncMock(return_value=_step("observe", tool_name="inspect_project"))
    read_tool = AsyncMock(return_value={})
    monkeypatch.setattr(planner, "_run_codex", mocked_model)
    result = await planner.generate_proposal("inspect and plan", COMPOSE, {}, "codex", read_tool)
    assert mocked_model.await_count == 4
    assert read_tool.await_count == 3
    assert result.clarification
    assert result.compose_yaml == COMPOSE


@pytest.mark.asyncio
async def test_codex_rejects_logs_for_other_project_service(monkeypatch):
    monkeypatch.setattr(
        planner,
        "_run_codex",
        AsyncMock(
            return_value=_step(
                "observe",
                tool_name="recent_logs",
                arguments={"service": "unrelated-host-service", "tail": 10},
            )
        ),
    )
    read_tool = AsyncMock()
    with pytest.raises(ValueError, match="không thuộc"):
        await planner.generate_proposal("inspect", COMPOSE, {}, "codex", read_tool)
    read_tool.assert_not_awaited()


def test_codex_extracts_final_structured_agent_message():
    events = [
        {"type": "thread.started", "thread_id": "test"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Checking proposal."}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": _step().model_dump_json()}},
        {"type": "turn.completed", "usage": {}},
    ]
    reply = planner._extract_step("\n".join(json.dumps(e) for e in events).encode())
    assert reply.compose_yaml == COMPOSE


def test_codex_does_not_accept_unexpected_builtin_execution():
    stdout = json.dumps(
        {"type": "item.started", "item": {"type": "command_execution", "command": "docker ps"}}
    ).encode()
    with pytest.raises(ValueError, match="tool ngoài"):
        planner._extract_step(stdout)


def test_codex_arguments_disable_commands_plugins_and_inherited_config():
    args = planner._codex_arguments("/usr/bin/codex", Path("/tmp/test-planner"))
    assert "--ignore-user-config" in args
    assert "--ephemeral" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "mcp_servers={}" in args
    disabled = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "--disable"]
    assert {"shell_tool", "code_mode_host", "plugins", "apps", "hooks", "multi_agent", "computer_use"} <= set(
        disabled
    )
    assert args[-1] == "-"


@pytest.mark.asyncio
async def test_codex_process_receives_prompt_on_stdin_not_shell(monkeypatch):
    response = json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": _step().model_dump_json()}}
    ).encode()
    process = AsyncMock()
    process.returncode = 0
    process.communicate.return_value = (response, b"")
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(planner.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(planner.asyncio, "create_subprocess_exec", spawn)
    result = await planner._run_codex("A request containing $(not-a-command)")
    assert result.kind == "plan"
    process.communicate.assert_awaited_once_with(b"A request containing $(not-a-command)")
    assert "$(not-a-command)" not in " ".join(spawn.call_args.args)
