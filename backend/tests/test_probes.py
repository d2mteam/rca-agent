"""Application checks come from the project's probes.yaml, never from the names used by one demo."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from hitl.domain import InfrastructureSpec, ProbeSpec
from hitl.service import InfraService
from hitl.settings import Settings
from hitl.store import now
from pydantic import ValidationError

IMAGE = "sha256:" + "a" * 64
PROBES = """
entry_service: proxy
health:
  path: /health
  expect: {database: UP}
routing: {path: /api/instance, field: instance, service: api}
records: {path: /api/records}
"""


def model(*names):
    """Shape of `docker compose config --format json`; only the first service publishes a port."""
    services = {
        name: {"image": f"{name}:1", "mem_limit": 67108864, "networks": {"default": None}} for name in names
    }
    services[names[0]]["ports"] = [
        {"mode": "ingress", "host_ip": "127.0.0.1", "target": 80, "published": "8080", "protocol": "tcp"}
    ]
    return {"name": "hitl-test", "services": services, "networks": {"default": {"name": "hitl-test_default"}}}


def test_probes_are_declared_not_inferred_from_service_names():
    ids = {name: IMAGE for name in ("nginx", "backend", "db")}
    assert InfrastructureSpec.from_compose(model("nginx", "backend", "db"), ids).probe_url is None
    probes = ProbeSpec.model_validate({"entry_service": "proxy", "health": {"path": "/health"}})
    spec = InfrastructureSpec.from_compose(
        model("proxy", "api"), {"proxy": IMAGE, "api": IMAGE}, probes=probes
    )
    assert spec.probe_url == "http://127.0.0.1:8080"
    assert spec.model_dump()["probes"]["health"]["path"] == "/health"


@pytest.mark.parametrize(
    "probes,match",
    [
        ({"entry_service": "api", "health": {"path": "/health"}}, "entry_service"),
        ({"entry_service": "missing", "health": {"path": "/health"}}, "entry_service"),
        (
            {
                "entry_service": "proxy",
                "health": {"path": "/h"},
                "routing": {"path": "/i", "service": "ghost"},
            },
            "routing",
        ),
        ({"entry_service": "proxy", "health": {"path": "health"}}, "path"),
        ({"entry_service": "proxy", "health": {"path": "/health"}, "shell": "curl"}, "shell"),
    ],
)
def test_probe_declarations_are_validated(probes, match):
    with pytest.raises(ValidationError, match=match):
        InfrastructureSpec.from_compose(
            model("proxy", "api"), {"proxy": IMAGE, "api": IMAGE}, probes=ProbeSpec.model_validate(probes)
        )


async def build(tmp_path, monkeypatch, names, probes_yaml=None, previous_url=None):
    service = InfraService(Settings(data_dir=tmp_path))
    project = tmp_path / "projects" / "hitl-test"
    project.mkdir(parents=True)
    (project / "compose.yaml").write_text(f"services:\n  {names[0]}:\n    image: {names[0]}:1\n")
    if probes_yaml is not None:
        (project / "probes.yaml").write_text(probes_yaml)
    monkeypatch.setattr(
        service.tools, "call", AsyncMock(return_value={"containers": [], "networks": [], "volumes": []})
    )
    monkeypatch.setattr("hitl.service.normalize", lambda text, directory, project: model(*names))
    monkeypatch.setattr(service.runtime, "image_ids", lambda compose: {n: IMAGE for n in compose["services"]})
    if previous_url is not None:
        service.store.save(
            {
                "id": "previous",
                "project": "hitl-test",
                "status": "verified",
                "plan_hash": "previous-hash",
                "created_at": now(),
                "source_model": model(*names),
                "compose_yaml": "",
                "probe_url": previous_url,
            }
        )
        service.store.save_verified(
            "hitl-test", "previous", {"infrastructure_spec": {"probes": {"entry_service": names[0]}}}
        )
    plan = await service.build_plan(
        {
            "project": "hitl-test",
            "plan_id": uuid4().hex[:16],
            "request": "Triển khai Compose hiện tại",
            "planner": "template",
        }
    )
    return service, plan


async def test_project_without_probes_gets_docker_checks_and_a_visible_warning(tmp_path, monkeypatch):
    _, plan = await build(tmp_path, monkeypatch, ("proxy", "api"))
    assert plan["status"] == "awaiting_approval"
    assert plan["infrastructure_spec"]["probes"] is None
    assert plan["probe_url"] is None
    assert plan["checks"] == [{"name": "Replica/image/port/RAM/network/mount/health"}]
    assert plan["baseline_status"] == "not_configured"
    assert any("probes.yaml" in w and "mức Docker" in w for w in plan["warnings"])


async def test_previous_application_checks_dropping_out_is_warned_separately(tmp_path, monkeypatch):
    _, plan = await build(tmp_path, monkeypatch, ("proxy", "api"), previous_url="http://127.0.0.1:8080")
    assert any("không còn" in w for w in plan["warnings"])


async def test_declared_probes_enter_spec_fingerprints_and_planned_checks(tmp_path, monkeypatch):
    _, plan = await build(tmp_path, monkeypatch, ("proxy", "api"), PROBES)
    assert plan["probe_url"] == "http://127.0.0.1:8080"
    assert plan["infrastructure_spec"]["probes"]["routing"]["service"] == "api"
    assert [c["name"] for c in plan["checks"]] == [
        "Replica/image/port/RAM/network/mount/health",
        "HTTP /health qua proxy",
        "Routing tới mọi replica api",
        "Bảo toàn dữ liệu trước triển khai",
    ]
    probes_path = str((tmp_path / "projects" / "hitl-test" / "probes.yaml").resolve())
    assert plan["source_fingerprints"][probes_path]
    assert not any("probes.yaml" in w for w in plan["warnings"])
    assert plan["baseline_status"] == "new_project"


async def test_probes_without_records_disable_only_data_preservation(tmp_path, monkeypatch):
    _, plan = await build(
        tmp_path, monkeypatch, ("proxy",), "entry_service: proxy\nhealth: {path: /health}\n"
    )
    assert plan["baseline_status"] == "not_configured"
    assert [c["name"] for c in plan["checks"]] == [
        "Replica/image/port/RAM/network/mount/health",
        "HTTP /health qua proxy",
    ]
    assert any("records" in w for w in plan["warnings"])


async def test_baseline_is_read_from_previous_entry_point_using_declared_records(tmp_path, monkeypatch):
    seen = []
    real_client = httpx.AsyncClient

    def client(**_kwargs):
        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, json=[{"id": 7, "value": "kept"}])

        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("hitl.service.httpx.AsyncClient", client)
    _, plan = await build(
        tmp_path, monkeypatch, ("proxy", "api"), PROBES, previous_url="http://127.0.0.1:9090"
    )
    assert seen == ["http://127.0.0.1:9090/api/records"]
    assert plan["baseline_status"] == "captured"
    assert plan["record_baseline"] == {"7": "kept"}


async def test_invalid_probes_file_stops_before_any_planner_call(tmp_path, monkeypatch):
    planner = AsyncMock()
    monkeypatch.setattr("hitl.service.generate_proposal", planner)
    with pytest.raises(ValueError, match="probes.yaml"):
        await build(tmp_path, monkeypatch, ("proxy",), "entry_service: [not, a, name]\n")
    planner.assert_not_awaited()
