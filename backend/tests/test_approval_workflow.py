from unittest.mock import AsyncMock

import pytest
from hitl.domain import plan_hash
from hitl.runtime import DockerRuntime
from hitl.service import InfraService
from hitl.settings import Settings
from hitl.store import Store, digest, now
from hitl.workflow import build_graph
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command


def minimal_plan(plan_id="approval-test"):
    return {
        "id": plan_id,
        "project": "hitl-test",
        "status": "awaiting_approval",
        "plan_hash": "hash-a",
        "created_at": now(),
    }


def test_approval_rejects_wrong_hash_and_duplicate_decisions(tmp_path):
    store = Store(tmp_path)
    plan = minimal_plan()
    store.save(plan)
    with pytest.raises(ValueError, match="hash"):
        store.decide(plan["id"], "wrong-hash", "approve")
    assert not store.approved(plan["id"], "hash-a")
    store.decide(plan["id"], "hash-a", "approve")
    assert store.approved(plan["id"], "hash-a")
    with pytest.raises(ValueError, match="trạng thái"):
        store.decide(plan["id"], "hash-a", "approve")


async def test_langgraph_pending_approval_survives_restart_and_reject_never_executes(tmp_path):
    service = InfraService(Settings(data_dir=tmp_path))
    plan = minimal_plan()

    async def prepare(_state):
        service.store.save(plan)
        return plan

    service.build_plan = AsyncMock(side_effect=prepare)
    service.execute = AsyncMock(return_value={"applied": True})
    database = str(tmp_path / "checkpoints.sqlite")
    config = {"configurable": {"thread_id": plan["id"]}}
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        graph = build_graph(service, saver)
        result = await graph.ainvoke({"plan_id": plan["id"]}, config, durability="sync")
        assert result["__interrupt__"][0].value["plan_hash"] == "hash-a"
    service.store.decide(plan["id"], "hash-a", "reject")
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        graph = build_graph(service, saver)
        await graph.ainvoke(Command(resume={"decision": "reject"}), config, durability="sync")
    service.execute.assert_not_awaited()
    assert service.store.get(plan["id"])["status"] == "rejected"
    service.build_plan.assert_awaited_once()


async def test_approval_boolean_from_llm_cannot_authorize_execution(tmp_path):
    service = InfraService(Settings(data_dir=tmp_path))
    plan = minimal_plan()
    service.store.save(plan)
    service.build_plan = AsyncMock(return_value=plan)
    service.execute = AsyncMock()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.sqlite")) as saver:
        graph = build_graph(service, saver)
        config = {"configurable": {"thread_id": plan["id"]}}
        await graph.ainvoke({"plan_id": plan["id"]}, config)
        await graph.ainvoke(Command(resume={"decision": "approve", "approved": True}), config)
    service.execute.assert_not_awaited()


def make_executable_plan(tmp_path, store):
    source = tmp_path / "source.yaml"
    source.write_text("services: {}")
    artifact = tmp_path / "artifact.yaml"
    artifact.write_text("services: {}")
    plan = {
        **minimal_plan(),
        "infrastructure_spec": {"test": "immutable"},
        "artifact_path": str(artifact),
        "artifact_hash": digest(artifact.read_bytes()),
        "artifact_fingerprints": {},
        "source_fingerprints": {str(source): digest(source.read_bytes())},
        "runtime_hash": digest({"containers": []}),
        "record_baseline": {},
        "source_model": {},
        "image_ids": {},
    }
    plan["plan_hash"] = plan_hash(plan)
    store.save(plan)
    store.decide(plan["id"], plan["plan_hash"], "approve")
    return plan, source, artifact


@pytest.mark.parametrize("drift", ["source", "artifact", "runtime", "spec", "policy"])
def test_executor_stale_plan_never_calls_compose(tmp_path, monkeypatch, drift):
    settings = Settings(data_dir=tmp_path).prepare()
    store = Store(tmp_path)
    plan, source, artifact = make_executable_plan(tmp_path, store)
    runtime = DockerRuntime(settings, store)
    monkeypatch.setattr(
        runtime,
        "inspect",
        lambda project: {"containers": ["external"]} if drift == "runtime" else {"containers": []},
    )
    monkeypatch.setattr(runtime, "image_ids", lambda model: {})
    writes = []
    monkeypatch.setattr("hitl.runtime.subprocess.run", lambda *args, **kwargs: writes.append(args))
    if drift == "source":
        source.write_text("manual change")
    elif drift == "artifact":
        artifact.write_text("altered artifact")
    elif drift == "spec":
        store.patch(plan["id"], infrastructure_spec={"test": "changed"})
    elif drift == "policy":
        from hitl.governance import PolicyStore

        policies = PolicyStore(store)
        change = policies.preview({**policies.current()["policy"], "max_replicas": 8})
        policies.decide(change["id"], change["plan_hash"], "approve")
    result = runtime.apply(plan["id"], plan["plan_hash"])
    assert result["status"] == "stale"
    assert not writes


def test_failed_verification_preserves_previous_verified_state(tmp_path):
    service = InfraService(Settings(data_dir=tmp_path))
    service.store.save_verified("hitl-test", "previous", {"evidence": "previous success"})
    plan = {
        **minimal_plan(),
        "status": "verifying",
        "verification": {
            "passed": False,
            "checks": [{"name": "HTTP", "status": "fail", "expected": 200, "actual": 503}],
        },
    }
    service.store.save(plan)
    service.finalize(plan["id"])
    assert service.store.verified("hitl-test")["plan_id"] == "previous"
    assert service.store.get(plan["id"])["status"] == "failed"


def test_compose_port_conflict_keeps_previous_verified_state(tmp_path, monkeypatch):
    from types import SimpleNamespace

    settings = Settings(data_dir=tmp_path).prepare()
    store = Store(tmp_path)
    plan, source, _ = make_executable_plan(tmp_path, store)
    store.patch(plan["id"], source_path=str(source), input_yaml=source.read_text())
    store.save_verified("hitl-test", "previous", {"evidence": "last known good"})
    runtime = DockerRuntime(settings, store)
    monkeypatch.setattr(runtime, "inspect", lambda project: {"containers": []})
    monkeypatch.setattr(runtime, "image_ids", lambda model: {})
    calls = []

    def compose(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="port is already allocated")

    monkeypatch.setattr("hitl.runtime.subprocess.run", compose)
    result = runtime.apply(plan["id"], plan["plan_hash"])
    assert result["status"] == "failed"
    assert len(calls) == 1
    assert "port is already allocated" in store.get(plan["id"])["error"]
    assert store.verified("hitl-test")["plan_id"] == "previous"
    assert any(e["kind"] == "docker_output" for e in store.events(plan["id"]))
