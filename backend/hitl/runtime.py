import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import docker
import httpx
from filelock import FileLock, Timeout
from tenacity import Retrying, retry_if_exception_type, stop_after_delay, wait_fixed

from .domain import InfrastructureSpec, plan_hash
from .governance import PolicyStore
from .policy import check_source_fingerprints
from .store import digest


class DockerRuntime:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    @contextmanager
    def client(self):
        client = docker.from_env(timeout=15)
        try:
            yield client
        finally:
            client.close()

    def inspect(self, project):
        with self.client() as client:
            containers = []
            for c in client.containers.list(
                all=True, filters={"label": f"com.docker.compose.project={project}"}
            ):
                a, h, state = c.attrs, c.attrs["HostConfig"], c.attrs["State"]
                labels = a["Config"].get("Labels") or {}
                containers.append(
                    {
                        "id": c.id,
                        "name": c.name,
                        "service": labels.get("com.docker.compose.service"),
                        "hostname": a["Config"].get("Hostname"),
                        "image": a["Image"],
                        "environment": dict(e.split("=", 1) for e in a["Config"].get("Env", []) if "=" in e),
                        "restart": h.get("RestartPolicy", {}).get("Name", "no"),
                        "health_test": a["Config"].get("Healthcheck", {}).get("Test"),
                        "init": h.get("Init"),
                        "config_hash": labels.get("com.docker.compose.config-hash"),
                        "memory": h.get("Memory", 0),
                        "ports": h.get("PortBindings") or {},
                        "mounts": sorted(
                            [
                                {k: m.get(k) for k in ("Type", "Source", "Destination", "Name", "RW")}
                                for m in a.get("Mounts", [])
                            ],
                            key=lambda m: m["Destination"],
                        ),
                        "networks": sorted(a["NetworkSettings"]["Networks"]),
                        "status": state.get("Status"),
                        "health": state.get("Health", {}).get("Status"),
                    }
                )
            networks = [
                {"id": n.id, "name": n.name, "internal": n.attrs.get("Internal", False)}
                for n in client.networks.list(filters={"label": f"com.docker.compose.project={project}"})
            ]
            volumes = [
                {"name": v.name, "driver": v.attrs["Driver"]}
                for v in client.volumes.list(filters={"label": f"com.docker.compose.project={project}"})
            ]
            return {
                "containers": sorted(containers, key=lambda c: c["id"]),
                "networks": sorted(networks, key=lambda n: n["name"]),
                "volumes": sorted(volumes, key=lambda v: v["name"]),
            }

    def image_ids(self, model):
        with self.client() as client:
            result = {}
            for name, service in model["services"].items():
                try:
                    result[name] = client.images.get(service["image"]).id
                except docker.errors.ImageNotFound:
                    raise ValueError(
                        f"Image {service['image']} chưa có trên máy. Chuẩn bị image rồi tạo preview lại."
                    )
            return result

    def logs(self, project, service=None, tail=80):
        with self.client() as client:
            items = {}
            for c in client.containers.list(
                all=True, filters={"label": f"com.docker.compose.project={project}"}
            ):
                if service is None or c.labels.get("com.docker.compose.service") == service:
                    items[c.name] = c.logs(tail=min(int(tail), 200)).decode(errors="replace")[-16000:]
            return {"logs": items}

    def precheck(self, plan):
        policy = PolicyStore(self.store).current()
        if plan.get("policy_revision", 1) != policy["revision"]:
            raise ValueError("Chính sách đã thay đổi sau preview; cần lập lại kế hoạch.")
        if plan_hash(plan) != plan["plan_hash"]:
            raise ValueError("InfrastructureSpec đã thay đổi sau preview.")
        if not self.store.approved(plan["id"], plan["plan_hash"]):
            raise ValueError("Executor từ chối: chưa có phê duyệt hợp lệ.")
        artifact = Path(plan["artifact_path"])
        if digest(artifact.read_bytes()) != plan["artifact_hash"]:
            raise ValueError("Artifact đã thay đổi sau preview.")
        if not check_source_fingerprints(plan["source_fingerprints"]):
            raise ValueError("Compose hoặc file cấu hình đã sửa sau preview.")
        if not check_source_fingerprints(plan["artifact_fingerprints"]):
            raise ValueError("File cấu hình đóng băng đã thay đổi sau preview.")
        if digest(self.inspect(plan["project"])) != plan["runtime_hash"]:
            raise ValueError("Runtime thay đổi sau preview; cần lập lại kế hoạch.")
        if self.image_ids(plan["source_model"]) != plan["image_ids"]:
            raise ValueError("Image tag đã thay đổi sau preview.")

    def apply(self, plan_id, plan_hash):
        plan = self.store.get(plan_id)
        if plan["plan_hash"] != plan_hash or not self.store.approved(plan_id, plan_hash):
            raise ValueError("Executor từ chối plan/hash chưa được duyệt.")
        if plan["status"] in ("applied", "verifying", "verified"):
            return {"applied": True, "reused": True}
        if plan["status"] != "approved":
            raise ValueError(f"Không được apply ở trạng thái {plan['status']}.")
        try:
            with (
                FileLock(str(self.settings.data_dir / "locks" / "policy.lock"), timeout=0),
                FileLock(str(self.settings.data_dir / "locks" / f"{plan['project']}.lock"), timeout=0),
            ):
                try:
                    self.precheck(plan)
                except ValueError as exc:
                    self.store.patch(plan_id, status="stale", error=str(exc))
                    self.store.event(plan_id, "stale", str(exc))
                    return {"applied": False, "status": "stale", "error": str(exc)}
                self.store.patch(plan_id, status="applying")
                self.store.event(
                    plan_id, "applying", "Executor bắt đầu Docker Compose theo artifact đã duyệt."
                )
                # The approved desired state remains editable even if the deployment fails.
                Path(plan["source_path"]).write_text(plan["input_yaml"])
                result = subprocess.run(
                    [
                        "docker",
                        "compose",
                        "--project-directory",
                        str(Path(plan["source_path"]).parent),
                        "-p",
                        plan["project"],
                        "-f",
                        plan["artifact_path"],
                        "up",
                        "-d",
                        "--wait",
                        "--wait-timeout",
                        "150",
                        "--no-build",
                        "--pull",
                        "never",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self.settings.compose_timeout,
                )
                self.store.event(
                    plan_id,
                    "docker_output",
                    "Kết quả Docker Compose",
                    {
                        "stdout": result.stdout[-12000:],
                        "stderr": result.stderr[-12000:],
                        "exit_code": result.returncode,
                    },
                )
                if result.returncode:
                    raise ValueError(f"Docker Compose thất bại: {result.stderr[-3000:]}")
                self.store.patch(plan_id, status="applied")
                self.store.event(plan_id, "applied", "Compose đã áp dụng; bắt đầu kiểm chứng độc lập.")
                return {"applied": True}
        except Timeout:
            raise ValueError("Một deployment khác đang chạy trên project này.")
        except Exception as exc:
            self.store.patch(plan_id, status="failed", error=str(exc))
            self.store.event(plan_id, "failed", str(exc))
            return {"applied": False, "status": "failed", "error": str(exc)}

    def verify(self, plan):
        spec = InfrastructureSpec.model_validate(plan["infrastructure_spec"])
        # The verifier and executor render the same validated spec, never LLM prose.
        model = spec.to_compose()
        snapshot = self.inspect(plan["project"])
        checks = []
        observed_records = None

        def check(name, expected, actual, detail=""):
            checks.append(
                {
                    "name": name,
                    "status": "pass" if expected == actual else "fail",
                    "expected": expected,
                    "actual": actual,
                    "detail": detail,
                }
            )

        expected_services = set(model["services"])
        check(
            "Services thuộc project",
            sorted(expected_services),
            sorted({c["service"] for c in snapshot["containers"]}),
        )
        for name, service in model["services"].items():
            containers = [c for c in snapshot["containers"] if c["service"] == name]
            check(f"{name}: số bản sao", service.get("deploy", {}).get("replicas", 1), len(containers))
            for c in containers:
                prefix = c["name"]
                check(f"{prefix}: running", "running", c["status"])
                if service.get("healthcheck"):
                    check(f"{prefix}: health", "healthy", c["health"])
                check(f"{prefix}: image", spec.services[name].image_id, c["image"])
                check(f"{prefix}: RAM bytes", service.get("mem_limit", 0), c["memory"])
                check(f"{prefix}: restart policy", spec.services[name].restart, c["restart"])
                if spec.services[name].init is not None:
                    check(f"{prefix}: init", spec.services[name].init, c["init"])
                for key, value in spec.services[name].environment.items():
                    # Compare exact values but do not expose credentials in evidence.
                    check(
                        f"{prefix}: environment {key}",
                        True,
                        c["environment"].get(key) == value,
                        "So sánh giá trị; không hiển thị nội dung biến.",
                    )
                if spec.services[name].healthcheck:
                    check(
                        f"{prefix}: healthcheck test", spec.services[name].healthcheck.test, c["health_test"]
                    )
                expected_ports = {}
                for p in service.get("ports", []):
                    expected_ports.setdefault(f"{p['target']}/{p.get('protocol', 'tcp')}", []).append(
                        {"HostIp": p["host_ip"], "HostPort": str(p["published"])}
                    )
                check(f"{prefix}: published ports", expected_ports, c["ports"])
                expected_networks = sorted(model["networks"][n]["name"] for n in service.get("networks", {}))
                check(f"{prefix}: networks", expected_networks, c["networks"])
                for v in service.get("volumes", []):
                    found = next((m for m in c["mounts"] if m["Destination"] == v["target"]), None)
                    if v["type"] == "volume":
                        expected = model["volumes"][v["source"]]["name"]
                        check(
                            f"{prefix}: volume {v['target']}", expected, found.get("Name") if found else None
                        )
                        check(
                            f"{prefix}: volume mode {v['target']}",
                            not v.get("read_only", False),
                            found.get("RW") if found else None,
                        )
                    else:
                        check(
                            f"{prefix}: bind {v['target']}",
                            {"Source": v["source"], "RW": False},
                            {"Source": found["Source"], "RW": found["RW"]} if found else None,
                        )
        for name, network in model.get("networks", {}).items():
            observed = next((n for n in snapshot["networks"] if n["name"] == network["name"]), None)
            check(
                f"network {name}: internal",
                network.get("internal", False),
                observed["internal"] if observed else None,
            )
        base = spec.probe_url
        if base:
            with httpx.Client(timeout=5, trust_env=False) as http:
                try:
                    readiness_attempts = 0
                    for attempt in Retrying(
                        stop=stop_after_delay(self.settings.probe_ready_timeout),
                        wait=wait_fixed(1),
                        retry=retry_if_exception_type((httpx.HTTPError, ValueError)),
                        reraise=True,
                    ):
                        with attempt:
                            readiness_attempts += 1
                            response = http.get(base + "/health")
                            response.raise_for_status()
                            response.json()
                    check(
                        "HTTP qua Nginx /health",
                        200,
                        response.status_code,
                        f"HTTP sẵn sàng sau {readiness_attempts} lần đọc; không chạy lại deploy.",
                    )
                    check("Backend kết nối database", "UP", response.json().get("database"))
                    expected = sorted(
                        c["hostname"] for c in snapshot["containers"] if c["service"] == "backend"
                    )
                    seen = set()
                    deadline = time.monotonic() + 18
                    while time.monotonic() < deadline:
                        reply = http.get(base + "/api/instance", headers={"Connection": "close"})
                        if reply.status_code == 200:
                            seen.add(reply.json().get("instance"))
                        if seen == set(expected):
                            break
                        time.sleep(0.2)
                    check("Nginx chuyển request đến mọi backend", expected, sorted(seen))
                    records = http.get(base + "/api/records")
                    check("Đọc dữ liệu PostgreSQL qua ứng dụng", 200, records.status_code)
                    by_id = (
                        {str(r["id"]): r["value"] for r in records.json()}
                        if records.status_code == 200
                        else {}
                    )
                    baseline = plan.get("record_baseline", {})
                    observed_records = by_id
                    if plan.get("baseline_status") == "unavailable":
                        checks.append(
                            {
                                "name": "Bảo toàn dữ liệu trước triển khai",
                                "status": "skip",
                                "expected": "baseline đọc được trước deploy",
                                "actual": "Ứng dụng đã dừng nên chưa chụp được baseline",
                                "detail": "Chưa cập nhật Verified State. Tạo preview mới khi ứng dụng đã hoạt động để kiểm chứng lại.",
                            }
                        )
                    else:
                        check(
                            "Dữ liệu trước adjust được giữ nguyên",
                            baseline,
                            {k: by_id.get(k) for k in baseline},
                            f"Đối chiếu {len(baseline)} bản ghi có trước khi lập kế hoạch.",
                        )
                except Exception as exc:
                    check("HTTP application probes", "reachable and valid JSON", str(exc))
        return {
            "passed": all(c["status"] == "pass" for c in checks),
            "checks": checks,
            "snapshot": snapshot,
            "observed_records": observed_records,
        }
