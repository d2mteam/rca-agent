import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock, Timeout
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from .governance import GatewayPolicy
from .policy_planner import propose_policy
from .service import InfraService
from .settings import Settings
from .workflow import build_graph


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str = "hitl-demo"
    request: str = Field(default="", max_length=12000)
    compose_yaml: str | None = Field(default=None, max_length=100000)
    mode: Literal["dry-run", "adjust"] = "dry-run"
    planner: Literal["template", "codex"] = "template"


class PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(default="", max_length=4000)
    policy: GatewayPolicy | None = None


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_hash: str


TERMINAL = {"verified", "failed", "rejected", "stale", "no_changes", "needs_clarification"}


def create_app(settings=None):
    settings = settings or Settings()
    jobs = set()

    async def resume(plan_id, decision="approve"):
        try:
            await app.state.graph.ainvoke(
                Command(resume={"decision": decision}),
                {"configurable": {"thread_id": plan_id}},
                durability="sync",
            )
        except Exception as exc:
            app.state.service.store.patch(plan_id, status="failed", error=str(exc))
            app.state.service.store.event(plan_id, "failed", f"Workflow: {exc}")

    def launch(coro):
        task = asyncio.create_task(coro)
        jobs.add(task)
        task.add_done_callback(jobs.discard)

    @asynccontextmanager
    async def lifespan(_app):
        service = InfraService(settings)
        service.seed()
        app.state.service = service
        async with AsyncSqliteSaver.from_conn_string(str(settings.data_dir / "checkpoints.sqlite")) as saver:
            app.state.graph = build_graph(service, saver)
            for plan in service.store.list():
                if plan["status"] in {"applying", "applied", "verifying", "approved"}:
                    # Docker and SQLite cannot commit atomically. Never blindly replay writes.
                    service.store.patch(
                        plan["id"],
                        status="failed",
                        error="Backend khởi động lại trong lúc triển khai. Hãy đọc runtime và tạo kế hoạch mới; không tự chạy lại thao tác.",
                    )
                    service.store.event(
                        plan["id"], "interrupted", "Đã giữ nhật ký; cần preview mới để hòa giải runtime."
                    )
            yield
            if jobs:
                await asyncio.gather(*list(jobs), return_exceptions=True)

    app = FastAPI(title="HITL Infrastructure", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            allowed = {
                f"http://127.0.0.1:{settings.port}",
                f"http://localhost:{settings.port}",
                "http://127.0.0.1:5173",
                "http://localhost:5173",
            }
            if origin and origin not in allowed:
                return JSONResponse({"detail": "Origin không được phép."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def value_error(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/api/health")
    async def health():
        try:
            with app.state.service.runtime.client() as client:
                docker_status = {"available": True, "version": client.version()["Version"]}
        except Exception as exc:
            docker_status = {"available": False, "error": str(exc)}
        return {
            "status": "ok",
            "docker": docker_status,
            "planner": "codex + template" if shutil.which("codex") else "template",
        }

    @app.get("/api/policy")
    async def policy_current():
        return app.state.service.policies.current()

    @app.get("/api/policy/changes")
    async def policy_changes():
        return {"changes": app.state.service.policies.list()}

    @app.post("/api/policy/preview")
    async def policy_preview(body: PolicyRequest):
        policies = app.state.service.policies
        current = policies.current()
        if body.policy is not None:
            desired = body.policy.model_dump()
        else:
            desired = (await propose_policy(body.request, current["policy"])).policy.model_dump()
        return policies.preview(desired, body.request, current)

    @app.post("/api/policy/changes/{change_id}/{decision}")
    async def policy_decision(change_id: str, decision: Literal["approve", "reject"], body: Decision):
        service = app.state.service
        try:
            with FileLock(str(settings.data_dir / "locks" / "policy.lock"), timeout=0):
                if decision == "approve" and any(
                    p["status"] in {"approved", "applying", "applied", "verifying"}
                    for p in service.store.list()
                ):
                    raise HTTPException(409, "Chờ deployment hiện tại hoàn tất trước khi đổi chính sách.")
                result = service.policies.decide(change_id, body.plan_hash, decision)
                if decision == "approve":
                    for plan in service.store.list():
                        if plan["status"] == "awaiting_approval":
                            service.store.patch(
                                plan["id"], status="stale", error="Chính sách đã đổi; tạo preview mới."
                            )
                            service.store.event(
                                plan["id"], "stale", "Chính sách đã đổi; không dùng lại approval cũ."
                            )
                return result
        except Timeout:
            raise HTTPException(409, "Executor đang áp dụng cấu hình; chưa thể đổi chính sách.")

    @app.get("/api/projects")
    async def projects():
        return {
            "projects": [
                {"name": p.parent.name, "compose_path": str(p)}
                for p in sorted((settings.data_dir / "projects").glob("*/compose.yaml"))
            ]
        }

    @app.get("/api/projects/{project}")
    async def project_detail(project: str):
        service = app.state.service
        path = service.project_path(project)
        runtime = await service.tools.call("inspect_project", {"project": project})
        return {
            "name": project,
            "compose_yaml": path.read_text(),
            "runtime": runtime,
            "verified_state": service.store.verified(project),
        }

    @app.post("/api/plans")
    async def create_plan(body: PlanRequest):
        plan_id = uuid4().hex[:16]
        await app.state.graph.ainvoke(
            {**body.model_dump(), "plan_id": plan_id},
            {"configurable": {"thread_id": plan_id}},
            durability="sync",
        )
        return app.state.service.store.get(plan_id)

    @app.get("/api/plans")
    async def plans():
        return {"plans": app.state.service.store.list()}

    @app.get("/api/plans/{plan_id}")
    async def plan_detail(plan_id: str):
        store = app.state.service.store
        return {**store.get(plan_id), "events": store.events(plan_id)}

    @app.post("/api/plans/{plan_id}/approve")
    async def approve(plan_id: str, body: Decision):
        store = app.state.service.store
        plan = store.get(plan_id)
        try:
            with (
                FileLock(str(settings.data_dir / "locks" / "policy.lock"), timeout=0),
                FileLock(str(settings.data_dir / "locks" / f"{plan['project']}.lock"), timeout=0),
            ):
                if any(
                    p["project"] == plan["project"]
                    and p["status"] in {"approved", "applying", "applied", "verifying"}
                    for p in store.list()
                ):
                    raise HTTPException(409, "Project đang có deployment chạy.")
                if plan.get("policy_revision", 1) != app.state.service.policies.current()["revision"]:
                    raise HTTPException(409, "Chính sách đã đổi; tạo preview mới.")
                store.decide(plan_id, body.plan_hash, "approve")
        except Timeout:
            raise HTTPException(409, "Project đang được executor xử lý.")
        launch(resume(plan_id))
        return store.get(plan_id)

    @app.post("/api/plans/{plan_id}/reject")
    async def reject(plan_id: str, body: Decision):
        store = app.state.service.store
        store.decide(plan_id, body.plan_hash, "reject")
        await resume(plan_id, "reject")
        return store.get(plan_id)

    @app.get("/api/plans/{plan_id}/verification")
    async def verification(plan_id: str):
        return app.state.service.store.get(plan_id).get("verification", {"passed": False, "checks": []})

    @app.get("/api/plans/{plan_id}/events")
    async def events(plan_id: str, request: Request):
        store = app.state.service.store
        store.get(plan_id)

        async def stream():
            last = int(request.headers.get("last-event-id", "0"))
            while not await request.is_disconnected():
                for item in store.events(plan_id, last):
                    last = item["id"]
                    yield {"id": str(last), "data": json.dumps(item, ensure_ascii=False)}
                if store.get(plan_id)["status"] in TERMINAL:
                    break
                await asyncio.sleep(0.7)

        return EventSourceResponse(stream())

    static = settings.root / "frontend" / "dist"
    if static.exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}")
        async def frontend(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "API không tồn tại.")
            return FileResponse(static / "index.html")

    return app


app = create_app()
