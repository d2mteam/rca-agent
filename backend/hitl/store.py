import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, event, update
from sqlmodel import Field, Session, SQLModel, create_engine, select


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    raw = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )
    return hashlib.sha256(raw).hexdigest()


class PlanRow(SQLModel, table=True):
    id: str = Field(primary_key=True)
    project: str = Field(index=True)
    status: str = Field(index=True)
    plan_hash: str
    created_at: str = Field(default_factory=now)
    payload: dict = Field(sa_column=Column(JSON))


class ApprovalRow(SQLModel, table=True):
    plan_id: str = Field(primary_key=True)
    plan_hash: str
    decision: str
    created_at: str = Field(default_factory=now)


class EventRow(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    plan_id: str = Field(index=True)
    kind: str
    message: str
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: str = Field(default_factory=now)


class VerifiedRow(SQLModel, table=True):
    project: str = Field(primary_key=True)
    plan_id: str
    payload: dict = Field(sa_column=Column(JSON))
    created_at: str = Field(default_factory=now)


class Store:
    def __init__(self, data_dir):
        self.engine = create_engine(
            f"sqlite:///{data_dir / 'state.sqlite'}", connect_args={"check_same_thread": False, "timeout": 30}
        )

        @event.listens_for(self.engine, "connect")
        def sqlite_options(dbapi, _):
            dbapi.execute("PRAGMA journal_mode=WAL")
            dbapi.execute("PRAGMA busy_timeout=30000")

        SQLModel.metadata.create_all(self.engine)

    def save(self, plan):
        with Session(self.engine) as session:
            session.add(
                PlanRow(
                    id=plan["id"],
                    project=plan["project"],
                    status=plan["status"],
                    plan_hash=plan["plan_hash"],
                    created_at=plan["created_at"],
                    payload=plan,
                )
            )
            session.commit()
        return plan

    def get(self, plan_id):
        with Session(self.engine) as session:
            row = session.get(PlanRow, plan_id)
            if not row:
                raise ValueError("Không tìm thấy kế hoạch.")
            return {**row.payload, "status": row.status}

    def patch(self, plan_id, **fields):
        with Session(self.engine) as session:
            row = session.get(PlanRow, plan_id)
            row.payload = {**row.payload, **fields}
            if "status" in fields:
                row.status = fields["status"]
            session.add(row)
            session.commit()
        return self.get(plan_id)

    def list(self):
        with Session(self.engine) as session:
            return [
                {**r.payload, "status": r.status}
                for r in session.exec(select(PlanRow).order_by(PlanRow.created_at.desc())).all()
            ]

    def decide(self, plan_id, plan_hash, decision):
        with Session(self.engine) as session:
            row = session.get(PlanRow, plan_id)
            if not row or row.plan_hash != plan_hash:
                raise ValueError("Plan hash không đúng. Hãy tải lại preview.")
            if row.status != "awaiting_approval":
                raise ValueError(f"Kế hoạch đã ở trạng thái {row.status}; không thể duyệt lại.")
            status = "approved" if decision == "approve" else "rejected"
            result = session.exec(
                update(PlanRow)
                .where(PlanRow.id == plan_id, PlanRow.status == "awaiting_approval")
                .values(status=status)
            )
            if result.rowcount != 1:
                raise ValueError("Kế hoạch vừa được xử lý bởi yêu cầu khác.")
            session.add(ApprovalRow(plan_id=plan_id, plan_hash=plan_hash, decision=decision))
            session.commit()
        self.event(
            plan_id,
            status,
            "Người dùng đã duyệt." if decision == "approve" else "Người dùng từ chối triển khai.",
        )

    def approved(self, plan_id, plan_hash):
        with Session(self.engine) as session:
            row = session.get(ApprovalRow, plan_id)
            return bool(row and row.decision == "approve" and row.plan_hash == plan_hash)

    def event(self, plan_id, kind, message, data=None):
        with Session(self.engine) as session:
            session.add(EventRow(plan_id=plan_id, kind=kind, message=message, data=data or {}))
            session.commit()

    def events(self, plan_id, after=0):
        with Session(self.engine) as session:
            return [
                r.model_dump()
                for r in session.exec(
                    select(EventRow)
                    .where(EventRow.plan_id == plan_id, EventRow.id > after)
                    .order_by(EventRow.id)
                ).all()
            ]

    def verified(self, project):
        with Session(self.engine) as session:
            row = session.get(VerifiedRow, project)
            return row.model_dump() if row else None

    def save_verified(self, project, plan_id, payload):
        with Session(self.engine) as session:
            row = session.get(VerifiedRow, project)
            if row:
                row.plan_id, row.payload, row.created_at = plan_id, payload, now()
            else:
                row = VerifiedRow(project=project, plan_id=plan_id, payload=payload)
            session.add(row)
            session.commit()
