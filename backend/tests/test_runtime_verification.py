from unittest.mock import Mock

import httpx
import pytest
from hitl.domain import InfrastructureSpec
from hitl.runtime import DockerRuntime
from hitl.settings import Settings
from hitl.store import Store

IMAGE = "sha256:" + "b" * 64


@pytest.fixture
def verifier(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, probe_ready_timeout=0).prepare()
    runtime = DockerRuntime(settings, Store(tmp_path))
    spec = InfrastructureSpec.model_validate(
        {
            "project": "hitl-test",
            "services": {
                "backend": {
                    "image": "test:1",
                    "image_id": IMAGE,
                    "replicas": 1,
                    "memory_bytes": 1024,
                    "ports": [{"target": 8080, "published": 8080}],
                    "networks": [],
                }
            },
            "networks": {},
            "probes": {
                "entry_service": "backend",
                "health": {"path": "/health", "expect": {"database": "UP"}},
                "routing": {"path": "/api/instance", "field": "instance", "service": "backend"},
                "records": {"path": "/api/records", "id_field": "id", "value_field": "value"},
            },
        }
    )
    snapshot = {
        "containers": [
            {
                "service": "backend",
                "name": "hitl-test-backend-1",
                "hostname": "instance-one",
                "status": "running",
                "image": IMAGE,
                "memory": 1024,
                "restart": "no",
                "ports": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}]},
                "networks": [],
                "mounts": [],
            }
        ],
        "networks": [],
        "volumes": [],
    }
    monkeypatch.setattr(runtime, "inspect", lambda project: snapshot)
    plan = {
        "project": "hitl-test",
        "infrastructure_spec": spec.model_dump(),
        "record_baseline": {"1": "saved"},
    }
    return runtime, plan


def fake_http(monkeypatch, *, failure=False, records=None):
    client = Mock()

    def get(url, **kwargs):
        if failure:
            raise httpx.ConnectError("application unreachable")
        payload = (
            {"database": "UP"}
            if url.endswith("/health")
            else ({"instance": "instance-one"} if url.endswith("/api/instance") else records)
        )
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    client.get.side_effect = get
    context = Mock()
    context.__enter__ = Mock(return_value=client)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr("hitl.runtime.httpx.Client", lambda **kwargs: context)
    return client


def test_transient_proxy_502_is_rechecked_with_a_bound(verifier, monkeypatch):
    runtime, plan = verifier
    runtime.settings.probe_ready_timeout = 2
    client = fake_http(monkeypatch, records=[{"id": 1, "value": "saved"}])
    healthy_get = client.get.side_effect
    attempts = []

    def recovering_get(url, **kwargs):
        if url.endswith("/health"):
            attempts.append(url)
            if len(attempts) == 1:
                return httpx.Response(502, request=httpx.Request("GET", url))
        return healthy_get(url, **kwargs)

    client.get.side_effect = recovering_get
    report = runtime.verify(plan)
    assert report["passed"]
    assert len(attempts) == 2
    assert any("2 lần đọc" in c.get("detail", "") for c in report["checks"])


def test_running_container_does_not_pass_failed_http(verifier, monkeypatch):
    runtime, plan = verifier
    fake_http(monkeypatch, failure=True)
    report = runtime.verify(plan)
    assert not report["passed"]
    assert any(c["name"].endswith(": running") and c["status"] == "pass" for c in report["checks"])
    assert any(c["name"] == "HTTP application probes" and c["status"] == "fail" for c in report["checks"])


def test_unavailable_baseline_never_claims_verified(verifier, monkeypatch):
    runtime, plan = verifier
    plan["baseline_status"] = "unavailable"
    fake_http(monkeypatch, records=[{"id": 1, "value": "saved"}])
    report = runtime.verify(plan)
    assert not report["passed"]
    assert [c["status"] for c in report["checks"] if c["status"] != "pass"] == ["skip"]
    assert report["observed_records"] == {"1": "saved"}


def test_lost_record_fails_even_when_http_and_containers_work(verifier, monkeypatch):
    runtime, plan = verifier
    fake_http(monkeypatch, records=[])
    report = runtime.verify(plan)
    assert not report["passed"]
    failed = [c for c in report["checks"] if c["status"] != "pass"]
    assert len(failed) == 1
    assert failed[0]["actual"] == {"1": None}
