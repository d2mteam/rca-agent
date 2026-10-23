from unittest.mock import AsyncMock

import pytest
from hitl.domain import InfrastructureSpec, ServiceSpec
from hitl.gateway import evaluate_request
from hitl.service import InfraService
from hitl.settings import Settings
from pydantic import ValidationError

IMAGE = "sha256:" + "a" * 64


def valid_spec():
    return InfrastructureSpec.model_validate(
        {
            "project": "hitl-test",
            "services": {
                "api": {
                    "image": "demo:1",
                    "image_id": IMAGE,
                    "replicas": 2,
                    "memory_bytes": 536870912,
                    "networks": ["private"],
                    "dependencies": {"db": {"condition": "service_healthy"}},
                },
                "db": {
                    "image": "postgres:16",
                    "image_id": IMAGE,
                    "replicas": 1,
                    "memory_bytes": 268435456,
                    "networks": ["private"],
                    "healthcheck": {"test": ["CMD", "pg_isready"]},
                },
            },
            "networks": {"private": {"name": "hitl-test_private", "internal": True}},
        }
    )


@pytest.mark.parametrize(
    "prompt,code",
    [
        ("Python là gì?", "out_of_scope"),
        ("Viết code cho backend", "out_of_scope"),
        ("Xóa mọi container", "destructive_operation"),
        ("docker system prune", "destructive_operation"),
        ("Mở port 5432 cho PostgreSQL", "sensitive_port"),
        ("Expose database ra internet", "sensitive_service"),
        ("Deploy nginx privileged", "unsafe_access"),
        ("Triển khai nginx trên 0.0.0.0", "unsafe_access"),
    ],
)
async def test_gateway_blocks_before_any_model_or_runtime_call(tmp_path, monkeypatch, prompt, code):
    service = InfraService(Settings(data_dir=tmp_path))
    model = AsyncMock()
    tools = AsyncMock()
    monkeypatch.setattr("hitl.service.generate_proposal", model)
    monkeypatch.setattr(service.tools, "call", tools)
    assert evaluate_request(prompt).code == code
    with pytest.raises(ValueError, match="LLM calls: 0"):
        await service.build_plan(
            {"project": "hitl-test", "plan_id": "blocked", "request": prompt, "planner": "codex"}
        )
    model.assert_not_awaited()
    tools.assert_not_awaited()


@pytest.mark.parametrize(
    "prompt",
    [
        "Triển khai web app gồm nginx và backend dùng PostgreSQL; không mở port 5432 ra ngoài.",
        "Tăng backend lên 3 bản sao, giới hạn RAM 512 MiB.",
        "Triển khai Compose hiện tại",
    ],
)
def test_gateway_accepts_scoped_operations(prompt):
    assert evaluate_request(prompt).allowed


@pytest.mark.parametrize("field,value", [("replicas", 0), ("replicas", -1), ("replicas", 1.5), ("image", "")])
def test_domain_structural_constraints(field, value):
    payload = valid_spec().services["api"].model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        ServiceSpec.model_validate(payload)


def test_domain_requires_image_and_valid_service_names():
    payload = valid_spec().model_dump()
    del payload["services"]["api"]["image"]
    with pytest.raises(ValidationError):
        InfrastructureSpec.model_validate(payload)
    payload = valid_spec().model_dump()
    payload["services"]["../host"] = payload["services"].pop("api")
    with pytest.raises(ValidationError):
        InfrastructureSpec.model_validate(payload)


def test_domain_rejects_missing_dependency_and_cycles():
    payload = valid_spec().model_dump()
    payload["services"]["api"]["dependencies"] = {"missing": {}}
    with pytest.raises(ValidationError, match="không tồn tại"):
        InfrastructureSpec.model_validate(payload)
    payload = valid_spec().model_dump()
    payload["services"]["db"]["dependencies"] = {"api": {}}
    with pytest.raises(ValidationError, match="vòng lặp"):
        InfrastructureSpec.model_validate(payload)


def test_domain_rejects_port_collision_and_sensitive_ports():
    payload = valid_spec().model_dump()
    payload["policy_snapshot"]["block_sensitive_services"] = False
    for service in payload["services"].values():
        service["replicas"] = 1
        service["ports"] = [{"target": 80, "published": 8080}]
    with pytest.raises(ValidationError, match="trùng"):
        InfrastructureSpec.model_validate(payload)
    payload["services"]["db"]["ports"] = [{"target": 5432, "published": 15432}]
    with pytest.raises(ValidationError, match="nhạy cảm"):
        InfrastructureSpec.model_validate(payload)


def test_compose_is_rendered_from_spec_not_raw_llm_response():
    spec = valid_spec()
    rendered = spec.to_compose()
    assert rendered["services"]["api"]["image"] == spec.services["api"].image_id
    assert rendered["services"]["api"]["deploy"]["replicas"] == spec.services["api"].replicas
    assert rendered["services"]["api"]["mem_limit"] == spec.services["api"].memory_bytes
    assert rendered["networks"]["private"]["internal"] is True


async def test_domain_errors_return_to_planner_bounded(tmp_path, monkeypatch):
    from hitl.planner import Proposal

    service = InfraService(Settings(data_dir=tmp_path))
    project = tmp_path / "projects" / "hitl-test"
    project.mkdir()
    (project / "compose.yaml").write_text("services:\n  api:\n    image: demo:1\n")
    monkeypatch.setattr(
        service.tools, "call", AsyncMock(return_value={"containers": [], "networks": [], "volumes": []})
    )
    invalid = Proposal(compose_yaml="services:\n  api:\n    build: .\n", summary="invalid")
    model = AsyncMock(return_value=invalid)
    monkeypatch.setattr("hitl.service.generate_proposal", model)
    with pytest.raises(ValueError, match="3 lượt"):
        await service.build_plan(
            {"project": "hitl-test", "plan_id": "invalid", "request": "Triển khai demo", "planner": "codex"}
        )
    assert model.await_count == 3
    assert "validation_errors" in model.await_args_list[1].args[2]
    assert service.store.list() == []
