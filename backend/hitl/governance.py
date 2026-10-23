"""Versioned, human-approved policy. Policy drafts never mutate Docker."""

import difflib
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import JSON, Column, update
from sqlmodel import Field as SQLField
from sqlmodel import Session, SQLModel, select

from .store import digest, now

DEFAULT_PORTS = [22, 2375, 2376, 3306, 5432, 6379, 9200, 11211, 27017]


class GatewayPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocked_ports: list[int] = Field(default_factory=lambda: DEFAULT_PORTS.copy(), max_length=100)
    block_sensitive_services: bool = True
    max_replicas: int = Field(default=6, ge=1, le=32)
    max_memory_mib: int = Field(default=4096, ge=64, le=32768)
    blocked_terms: list[str] = Field(default_factory=list, max_length=40)
    allowed_actions: list[str] = Field(
        default_factory=lambda: [
            "mở",
            "open",
            "publish",
            "expose",
            "triển khai",
            "chạy",
            "tăng",
            "giảm",
            "đổi",
            "điều chỉnh",
            "cấu hình",
            "cập nhật",
            "khởi động",
            "khởi tạo",
            "kiểm tra",
            "xem",
            "deploy",
            "scale",
            "adjust",
            "set",
            "update",
            "start",
            "restart",
            "inspect",
            "configure",
            "show",
            "change",
            "increase",
            "decrease",
        ],
        min_length=1,
        max_length=80,
    )
    infrastructure_terms: list[str] = Field(
        default_factory=lambda: [
            "docker",
            "compose",
            "container",
            "nginx",
            "backend",
            "frontend",
            "postgres",
            "postgresql",
            "redis",
            "database",
            "cache",
            "network",
            "volume",
            "replica",
            "replicas",
            "bản sao",
            "port",
            "cổng",
            "service",
            "dịch vụ",
            "hạ tầng",
            "demo",
            "web app",
            "webapp",
            "ứng dụng",
            "ram",
        ],
        min_length=1,
        max_length=80,
    )

    @field_validator("blocked_ports")
    @classmethod
    def ports(cls, values):
        if any(isinstance(v, bool) or not 1 <= v <= 65535 for v in values):
            raise ValueError("Port phải từ 1 đến 65535.")
        return sorted(set(values))

    @field_validator("blocked_terms", "allowed_actions", "infrastructure_terms")
    @classmethod
    def terms(cls, values):
        if any(not v.strip() or len(v) > 80 for v in values):
            raise ValueError("Từ khóa cần từ 1 đến 80 ký tự.")
        return list(dict.fromkeys(v.strip().lower() for v in values))


class PolicyRow(SQLModel, table=True):
    id: int = SQLField(default=1, primary_key=True)
    revision: int = 1
    payload: dict = SQLField(sa_column=Column(JSON))


class PolicyChangeRow(SQLModel, table=True):
    id: str = SQLField(primary_key=True)
    status: str
    payload: dict = SQLField(sa_column=Column(JSON))


class PolicyStore:
    def __init__(self, store):
        self.store = store
        SQLModel.metadata.create_all(store.engine)
        with Session(store.engine) as session:
            if not session.get(PolicyRow, 1):
                session.add(PolicyRow(payload=GatewayPolicy().model_dump()))
                session.commit()

    def current(self):
        with Session(self.store.engine) as session:
            row = session.get(PolicyRow, 1)
            policy = GatewayPolicy.model_validate(row.payload).model_dump()
            return {"revision": row.revision, "policy": policy, "hash": digest(policy)}

    def list(self):
        with Session(self.store.engine) as session:
            return [{**r.payload, "status": r.status} for r in session.exec(select(PolicyChangeRow)).all()][
                ::-1
            ]

    def preview(self, desired, request="", current=None):
        current = current or self.current()
        after = GatewayPolicy.model_validate(desired).model_dump()
        before = current["policy"]
        changes = [{"field": k, "before": before[k], "after": v} for k, v in after.items() if before[k] != v]
        payload = {
            "id": uuid4().hex[:16],
            "created_at": now(),
            "request": request,
            "base_revision": current["revision"],
            "before": before,
            "after": after,
            "changes": changes,
        }
        payload["plan_hash"] = digest(payload)
        payload["diff"] = "".join(
            difflib.unified_diff(
                json.dumps(before, ensure_ascii=False, indent=2).splitlines(True),
                json.dumps(after, ensure_ascii=False, indent=2).splitlines(True),
                fromfile="policy hiện tại",
                tofile="policy đề xuất",
            )
        )
        payload["status"] = "awaiting_approval" if changes else "no_changes"
        with Session(self.store.engine) as session:
            session.add(PolicyChangeRow(id=payload["id"], status=payload["status"], payload=payload))
            session.commit()
        return payload

    def decide(self, change_id, expected_hash, decision):
        with Session(self.store.engine) as session:
            row = session.get(PolicyChangeRow, change_id)
            if not row or row.payload["plan_hash"] != expected_hash or row.status != "awaiting_approval":
                raise ValueError("Policy preview/hash không hợp lệ hoặc đã xử lý.")
            data = row.payload
            frozen = {
                k: data[k]
                for k in ("id", "created_at", "request", "base_revision", "before", "after", "changes")
            }
            if digest(frozen) != expected_hash:
                raise ValueError("Policy preview đã bị thay đổi.")
            if decision == "approve":
                after = GatewayPolicy.model_validate(data["after"]).model_dump()
                result = session.exec(
                    update(PolicyRow)
                    .where(PolicyRow.id == 1, PolicyRow.revision == data["base_revision"])
                    .values(payload=after, revision=data["base_revision"] + 1)
                )
                if result.rowcount != 1:
                    raise ValueError("Chính sách đã đổi; cần preview mới.")
            row.status = "approved" if decision == "approve" else "rejected"
            session.add(row)
            session.commit()
            status = row.status
        self.store.event(
            change_id,
            "policy_" + status,
            "Đã duyệt chính sách." if decision == "approve" else "Đã từ chối chính sách.",
        )
        return {**data, "status": status}
