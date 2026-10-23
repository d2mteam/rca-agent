from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from hitl.api import create_app
from hitl.domain import InfrastructureSpec
from hitl.gateway import evaluate_request
from hitl.governance import GatewayPolicy, PolicyStore
from hitl.policy_planner import PolicyProposal, propose_policy
from hitl.settings import Settings
from hitl.store import Store, now


def test_preview_approval_rejection_revision_and_stale_hash(tmp_path):
    store = Store(tmp_path)
    policies = PolicyStore(store)
    initial = policies.current()
    changed = {**initial["policy"], "max_replicas": 8}
    first = policies.preview(changed)
    stale = policies.preview({**changed, "max_replicas": 9})
    assert policies.current() == initial
    with pytest.raises(ValueError, match="hash"):
        policies.decide(first["id"], "wrong", "approve")
    policies.decide(first["id"], first["plan_hash"], "approve")
    assert policies.current()["policy"]["max_replicas"] == 8
    assert policies.current()["revision"] == 2
    with pytest.raises(ValueError, match="đã đổi"):
        policies.decide(stale["id"], stale["plan_hash"], "approve")
    rejected = policies.preview(initial["policy"])
    policies.decide(rejected["id"], rejected["plan_hash"], "reject")
    assert policies.current()["revision"] == 2
    assert PolicyStore(Store(tmp_path)).current() == policies.current()


def test_configurable_gateway_scope_and_ports():
    policy = GatewayPolicy()
    assert not evaluate_request("Mở port 5432 cho PostgreSQL", policy).allowed
    policy.blocked_ports.remove(5432)
    policy.block_sensitive_services = False
    assert evaluate_request("Mở port 5432 cho PostgreSQL", policy).allowed
    policy.blocked_terms = ["nginx"]
    assert evaluate_request("Triển khai nginx", policy).code == "blocked_term"
    policy.allowed_actions.append("khôi phục")
    assert evaluate_request("Khôi phục backend", policy).allowed
    assert not evaluate_request("Triển khai nginx privileged", GatewayPolicy(blocked_ports=[])).allowed


def test_policy_limits_and_port_exceptions_reach_domain():
    policy = GatewayPolicy(blocked_ports=[], block_sensitive_services=False, max_replicas=8)
    payload = {
        "project": "hitl-test",
        "policy_snapshot": policy.model_dump(),
        "services": {
            "db": {
                "image": "postgres:16",
                "image_id": "sha256:" + "a" * 64,
                "replicas": 1,
                "memory_bytes": 1024,
                "ports": [{"target": 5432, "published": 15432}],
                "networks": [],
            },
            "api": {
                "image": "demo:1",
                "image_id": "sha256:" + "a" * 64,
                "replicas": 8,
                "memory_bytes": 1024,
                "networks": [],
            },
        },
        "networks": {},
    }
    spec = InfrastructureSpec.model_validate(payload)
    assert spec.to_compose()["services"]["api"]["deploy"]["replicas"] == 8
    payload["policy_snapshot"]["max_replicas"] = 6
    with pytest.raises(ValueError, match="replicas"):
        InfrastructureSpec.model_validate(payload)
    payload["services"]["api"]["replicas"] = 1
    payload["services"]["api"]["memory_bytes"] = 8192 * 1024 * 1024
    with pytest.raises(ValueError, match="RAM"):
        InfrastructureSpec.model_validate(payload)

    payload["services"]["api"]["memory_bytes"] = 0
    with pytest.raises(ValueError, match="RAM"):
        InfrastructureSpec.model_validate(payload)


def test_api_policy_change_stales_pending_plans_without_docker(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        service = app.state.service
        service.tools.call = AsyncMock(side_effect=AssertionError("Policy must not access Docker"))
        service.store.save(
            {
                "id": "pending",
                "project": "hitl-demo",
                "status": "awaiting_approval",
                "plan_hash": "abc",
                "created_at": now(),
            }
        )
        current = client.get("/api/policy").json()
        desired = {**current["policy"], "blocked_terms": ["test-restricted"]}
        preview = client.post("/api/policy/preview", json={"policy": desired}).json()
        assert client.get("/api/policy").json() == current
        result = client.post(
            f"/api/policy/changes/{preview['id']}/approve", json={"plan_hash": preview["plan_hash"]}
        )
        assert result.status_code == 200, result.text
        assert service.store.get("pending")["status"] == "stale"
        assert client.post("/api/plans/pending/approve", json={"plan_hash": "abc"}).status_code == 409
        service.tools.call.assert_not_awaited()
        service.store.save(
            {
                "id": "active",
                "project": "hitl-demo",
                "status": "applying",
                "plan_hash": "abc",
                "created_at": now(),
            }
        )
        next_preview = client.post("/api/policy/preview", json={"policy": current["policy"]}).json()
        assert (
            client.post(
                f"/api/policy/changes/{next_preview['id']}/approve",
                json={"plan_hash": next_preview["plan_hash"]},
            ).status_code
            == 409
        )


async def test_policy_language_gateway_and_bounded_validation(monkeypatch):
    model = AsyncMock(side_effect=ValueError("schema error"))
    monkeypatch.setattr("hitl.policy_planner._run_codex", model)
    with pytest.raises(ValueError, match="LLM calls: 0"):
        await propose_policy("Viết một bài thơ", GatewayPolicy().model_dump())
    model.assert_not_awaited()
    with pytest.raises(ValueError, match="3 lượt"):
        await propose_policy("Tăng giới hạn replicas lên 8", GatewayPolicy().model_dump())
    assert model.await_count == 3
    model.reset_mock(side_effect=True)
    model.return_value = PolicyProposal(policy=GatewayPolicy(max_replicas=8), summary="Tăng replicas")
    result = await propose_policy("Tăng giới hạn replicas lên 8", GatewayPolicy().model_dump())
    assert result.policy.max_replicas == 8
