import asyncio
import difflib
import shutil
from pathlib import Path

import httpx

from .domain import InfrastructureSpec, plan_hash
from .gateway import enforce_request
from .governance import GatewayPolicy, PolicyStore
from .mcp_client import DockerTools
from .planner import generate_proposal
from .policy import dump_yaml, normalize, source_fingerprints, validate_model, validate_request
from .runtime import DockerRuntime
from .store import Store, digest, now


class InfraService:
    def __init__(self, settings):
        self.settings = settings.prepare()
        self.store = Store(settings.data_dir)
        self.policies = PolicyStore(self.store)
        self.tools = DockerTools(settings)
        self.runtime = DockerRuntime(settings, self.store)

    def seed(self):
        target = self.settings.data_dir / "projects" / "hitl-demo"
        if not target.exists():
            shutil.copytree(self.settings.root / "examples" / "webapp", target)

    def project_path(self, project):
        validate_request(project, "read", None)
        path = self.settings.data_dir / "projects" / project / "compose.yaml"
        if not path.is_file():
            raise ValueError("Project chưa được đăng ký.")
        return path

    async def build_plan(self, state):
        project, plan_id = state["project"], state["plan_id"]
        request, supplied = state.get("request", ""), state.get("compose_yaml")
        validate_request(project, request, supplied)
        current_policy = self.policies.current()
        policy = GatewayPolicy.model_validate(current_policy["policy"])
        gateway = (
            enforce_request(request, policy).model_dump()
            if request
            else {"allowed": True, "code": "compose_input", "llm_calls": 0}
        )
        path = self.project_path(project)
        current = path.read_text()
        snapshot = await self.tools.call("inspect_project", {"project": project})
        previous = self.store.verified(project)

        async def read_tool(name, arguments):
            if name not in {"inspect_project", "recent_logs", "last_verification"}:
                raise ValueError("Planner chỉ được gọi tools đọc đã định sẵn.")
            return await self.tools.call(name, {**arguments, "project": project})

        observations = {"runtime": snapshot, "verified": previous, "policy": current_policy}
        validation_history = []
        for attempt in range(3):
            if supplied:
                desired, summary, clarification = (
                    supplied,
                    "Đề xuất từ Compose do người dùng chỉnh sửa.",
                    None,
                )
            else:
                proposal = await generate_proposal(
                    request, current, observations, state["planner"], read_tool
                )
                desired, summary, clarification = (
                    proposal.compose_yaml,
                    proposal.summary,
                    proposal.clarification,
                )
            if clarification:
                break
            try:
                source_model = await asyncio.to_thread(normalize, desired, path.parent, project)
                validate_model(source_model, path.parent)
                image_ids = await asyncio.to_thread(self.runtime.image_ids, source_model)
                spec = InfrastructureSpec.from_compose(source_model, image_ids, policy)
                break
            except (ValueError, KeyError, TypeError) as exc:
                validation_history.append({"attempt": attempt + 1, "error": str(exc)})
                if supplied or state["planner"] != "codex" or attempt == 2:
                    raise ValueError(
                        f"Domain Schema: {exc}. Đã dừng sau {attempt + 1} lượt kiểm tra."
                    ) from exc
                observations = {
                    **observations,
                    "validation_errors": validation_history,
                    "previous_candidate_yaml": desired,
                    "instruction": "Sửa các lỗi domain trên, giữ nguyên phạm vi yêu cầu.",
                }
        if clarification:
            plan = {
                "id": plan_id,
                "project": project,
                "status": "needs_clarification",
                "plan_hash": digest({"request": request, "clarification": clarification}),
                "request": request,
                "planner": state["planner"],
                "created_at": now(),
                "compose_yaml": current,
                "previous_yaml": current,
                "diff": "",
                "changes": [],
                "checks": [],
                "error": clarification,
                "summary": summary,
            }
            self.store.save(plan)
            self.store.event(plan_id, "needs_clarification", clarification)
            return plan
        if previous:
            previous_plan = self.store.get(previous["plan_id"])
            if set(previous_plan["source_model"]["services"]) - set(source_model["services"]):
                raise ValueError("Demo chưa hỗ trợ xóa service. Hãy giữ service hiện có.")
        fingerprints = source_fingerprints(path, source_model)
        artifact_fingerprints = {}
        for service in spec.services.values():
            for volume in service.mounts:
                if volume.type == "bind":
                    source = Path(volume.source)
                    frozen = self.settings.data_dir / "assets" / digest(source.read_bytes()) / source.name
                    frozen.parent.mkdir(parents=True, exist_ok=True)
                    if not frozen.exists():
                        shutil.copyfile(source, frozen)
                    volume.source = str(frozen.resolve())
                    artifact_fingerprints[str(frozen.resolve())] = digest(frozen.read_bytes())
        model = spec.to_compose()
        folder = self.settings.data_dir / "plans" / plan_id
        folder.mkdir(parents=True, exist_ok=True)
        artifact = folder / "compose.yaml"
        artifact.write_text(dump_yaml(model))
        # Show the last approved configuration; manual edits are represented in the diff as well.
        previous_yaml = self.store.get(previous["plan_id"])["compose_yaml"] if previous else ""
        old = self.store.get(previous["plan_id"])["source_model"] if previous else {"services": {}}
        changes = []
        for name, service in source_model["services"].items():
            before = old["services"].get(name)
            if before is None:
                action, detail = "create", "Tạo service và các phụ thuộc thuộc project."
            elif before != service:
                old_count, count = (
                    before.get("deploy", {}).get("replicas", 1),
                    service.get("deploy", {}).get("replicas", 1),
                )
                action = "scale" if old_count != count else "recreate"
                detail = f"Bản sao {old_count} → {count}; container có cấu hình đổi sẽ được tạo lại."
            else:
                action, detail = "unchanged", "Giữ nguyên cấu hình service."
                observed = [c for c in snapshot["containers"] if c["service"] == name]
                if len(observed) != spec.services[name].replicas or any(
                    c["status"] != "running" for c in observed
                ):
                    action, detail = (
                        "reconcile",
                        "Runtime thiếu bản sao hoặc đang dừng; khởi động lại theo spec đã duyệt.",
                    )
            changes.append({"service": name, "action": action, "detail": detail})
        probe_url = spec.probe_url
        baseline = {}
        baseline_status = "new_project"
        warnings = []
        if previous:
            previous_url = self.store.get(previous["plan_id"]).get("probe_url")
            if previous_url:
                try:
                    async with httpx.AsyncClient(timeout=4, trust_env=False) as client:
                        response = await client.get(previous_url + "/api/records")
                        response.raise_for_status()
                        baseline = {str(r["id"]): r["value"] for r in response.json()}
                        baseline_status = "captured"
                except Exception as exc:
                    baseline_status = "unavailable"
                    warnings.append(
                        f"Không đọc được baseline dữ liệu trước triển khai: {exc}. Có thể khởi động lại, nhưng chưa thể xác minh bảo toàn dữ liệu; cần preview/verify lại sau khi ứng dụng hoạt động."
                    )
        frozen = {
            "policy_revision": current_policy["revision"],
            "project": project,
            "infrastructure_spec": spec.model_dump(),
            "artifact_hash": digest(artifact.read_bytes()),
            "artifact_fingerprints": artifact_fingerprints,
            "source_fingerprints": fingerprints,
            "runtime_hash": digest(snapshot),
            "image_ids": image_ids,
            "record_baseline": baseline,
            "baseline_status": baseline_status,
            "probe_url": probe_url,
        }
        plan = {
            "id": plan_id,
            "project": project,
            "status": "awaiting_approval",
            "plan_hash": plan_hash(frozen),
            "request": request,
            "planner": state["planner"] if not supplied else "manual",
            "created_at": now(),
            "compose_yaml": artifact.read_text(),
            "input_yaml": desired,
            "previous_yaml": previous_yaml,
            "diff": "".join(
                difflib.unified_diff(
                    previous_yaml.splitlines(True),
                    artifact.read_text().splitlines(True),
                    fromfile="đã xác minh",
                    tofile="đề xuất",
                )
            ),
            "changes": changes,
            "summary": summary,
            "source_model": source_model,
            "model": model,
            "source_path": str(path.resolve()),
            "artifact_path": str(artifact.resolve()),
            **frozen,
            "gateway": gateway,
            "validation_history": validation_history,
            "warnings": warnings,
            "checks": [
                {"name": "Replica/image/port/RAM/network/mount/health"},
                {"name": "HTTP, routing mọi backend và bảo toàn dữ liệu"},
            ]
            if probe_url
            else [{"name": "Replica/image/port/RAM/network/mount/health"}],
        }
        if (
            previous
            and source_model == old
            and previous["payload"].get("runtime_hash") == digest(snapshot)
            and image_ids == previous["payload"].get("image_ids")
            and artifact_fingerprints == previous["payload"].get("artifact_fingerprints")
        ):
            report = await asyncio.to_thread(self.runtime.verify, plan)
            if report["passed"]:
                plan["status"], plan["verification"] = "no_changes", report
        self.store.save(plan)
        self.store.event(plan_id, "static_gateway", "Static Gateway chấp nhận trước LLM.", gateway)
        for warning in warnings:
            self.store.event(plan_id, "verification_limit", warning)
        for item in validation_history:
            self.store.event(plan_id, "domain_retry", "Domain Schema trả lỗi về Planner để sửa.", item)
        self.store.event(
            plan_id,
            "preview",
            "Đã tạo preview. Docker chưa thay đổi.",
            {"plan_hash": plan["plan_hash"], "planner": plan["planner"]},
        )
        return plan

    async def execute(self, plan_id):
        plan = self.store.get(plan_id)
        try:
            return await self.tools.call(
                "docker_apply_plan", {"plan_id": plan_id, "plan_hash": plan["plan_hash"]}
            )
        except Exception as exc:
            self.store.patch(plan_id, status="failed", error=str(exc))
            self.store.event(plan_id, "failed", str(exc))
            return {"applied": False}

    async def verify(self, plan_id):
        self.store.patch(plan_id, status="verifying")
        self.store.event(plan_id, "verifying", "Đọc runtime và chạy HTTP probes độc lập với LLM.")
        try:
            report = await self.tools.call("docker_verify_plan", {"plan_id": plan_id})
        except Exception as exc:
            report = {
                "passed": False,
                "checks": [
                    {
                        "name": "Verifier",
                        "status": "fail",
                        "expected": "runtime accessible",
                        "actual": str(exc),
                    }
                ],
            }
        self.store.patch(plan_id, verification=report)
        return report

    def finalize(self, plan_id):
        plan = self.store.get(plan_id)
        report = plan["verification"]
        if report["passed"]:
            self.store.save_verified(
                plan["project"],
                plan_id,
                {
                    "infrastructure_spec": plan["infrastructure_spec"],
                    "runtime_hash": digest(report["snapshot"]),
                    "image_ids": plan["image_ids"],
                    "artifact_fingerprints": plan["artifact_fingerprints"],
                    "report": report,
                },
            )
            self.store.patch(plan_id, status="verified")
            self.store.event(plan_id, "verified", "Runtime đạt mọi tiêu chí; đã cập nhật Verified State.")
        else:
            self.store.patch(
                plan_id,
                status="failed",
                error="Apply thành công nhưng kiểm chứng chưa đạt; giữ Verified State trước đó.",
            )
            self.store.event(
                plan_id, "verification_failed", "Kiểm chứng chưa đạt. Không tự sửa hoặc rollback."
            )
