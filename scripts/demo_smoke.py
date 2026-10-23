"""Exercise the real approval API and Docker. Run only with explicit --approve."""
import argparse
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--approve", action="store_true", help="Explicitly authorize the demo's bounded deployments")
parser.add_argument("--planner", choices=["template", "codex"], default="codex")
args = parser.parse_args()
if not args.approve:
    parser.error("This script deploys the demo. Review it, then pass --approve.")

ROOT = Path(__file__).resolve().parents[1]
report = {"planner": args.planner, "checks": [], "plans": []}
client = httpx.Client(base_url="http://127.0.0.1:8765", timeout=540, trust_env=False)


def api(method, path, **body):
    response = client.request(method, path, json=body or None)
    response.raise_for_status()
    return response.json()


def record(name, **details):
    print(json.dumps({"check": name, **details}, ensure_ascii=False), flush=True)
    report["checks"].append({"name": name, **details})


def create(prompt, planner="template", mode="dry-run"):
    plan = api("POST", "/api/plans", project="hitl-demo", request=prompt, planner=planner, mode=mode)
    print(f"Preview {plan['id']} [{plan['status']}] hash={plan['plan_hash']}", flush=True)
    report["plans"].append({"id": plan["id"], "hash": plan["plan_hash"], "planner": plan["planner"]})
    return plan


def approve(plan):
    if plan["status"] == "no_changes":
        return plan
    assert plan["status"] == "awaiting_approval", plan.get("error", plan["status"])
    api("POST", f"/api/plans/{plan['id']}/approve", plan_hash=plan["plan_hash"])
    deadline = time.monotonic() + 240
    previous = None
    while time.monotonic() < deadline:
        latest = api("GET", f"/api/plans/{plan['id']}")
        if latest["status"] != previous:
            print(f"{plan['id']}: {latest['status']}", flush=True)
            previous = latest["status"]
        if latest["status"] in {"verified", "failed", "stale", "rejected"}:
            return latest
        time.sleep(1)
    raise AssertionError("Deployment timed out")


before = api("GET", "/api/projects/hitl-demo")["runtime"]
for prompt in ("Python là gì?", "Xóa mọi container", "Mở port 5432 cho PostgreSQL"):
    response = client.post("/api/plans", json={"project": "hitl-demo", "request": prompt, "planner": "codex"})
    assert response.status_code == 422 and "LLM calls: 0" in response.text, response.text
record("gateway_rejected_before_llm", requests=3)

plan = create("Tăng backend lên 2 replicas và RAM 384m", mode="adjust")
assert plan["status"] == "awaiting_approval"
api("POST", f"/api/plans/{plan['id']}/reject", plan_hash=plan["plan_hash"])
assert api("GET", "/api/projects/hitl-demo")["runtime"] == before
record("reject_does_not_mutate_docker")

plan = create("Tăng backend lên 2 replicas và RAM 384m", mode="adjust")
assert plan["status"] == "awaiting_approval"
if plan["status"] == "awaiting_approval":
    source = ROOT / ".hitl/projects/hitl-demo/compose.yaml"
    original = source.read_text()
    changed = original + "\n# Simulated manual edit after preview\n"
    source.write_text(changed)
    try:
        latest = approve(plan)
        assert latest["status"] == "stale", latest
        assert api("GET", "/api/projects/hitl-demo")["runtime"] == before
        record("manual_edit_invalidates_old_approval")
    finally:
        if source.read_text() == changed:
            source.write_text(original)

prompt = "Triển khai web app gồm Nginx cân bằng tải cho 2 backend Java, dùng chung PostgreSQL có lưu trữ bền vững. Chỉ Nginx mở cổng 8080; backend chỉ khởi động khi database sẵn sàng. Giới hạn RAM mỗi backend 256 MiB."
if args.planner == "template":
    prompt = "Tăng backend lên 2 replicas và RAM 256m"
initial = approve(create(prompt, args.planner))
assert initial["status"] in {"verified", "no_changes"}, initial.get("error", initial)
record("deploy_two_backends", plan=initial["id"], checks=len(initial["verification"]["checks"]))

with httpx.Client(base_url="http://127.0.0.1:8080", timeout=10, trust_env=False) as app:
    value = "persistent-before-adjust-" + uuid4().hex[:10]
    response = app.post("/api/records", json={"value": value})
    response.raise_for_status()
    saved = response.json()
    record("created_persistent_record", id=saved["id"], value=saved["value"])

adjust = "Tăng backend lên 3 bản sao, giới hạn mỗi bản 512 MiB RAM; giữ nguyên địa chỉ truy cập và dữ liệu hiện có."
final = approve(create(adjust, args.planner, "adjust"))
assert final["status"] == "verified", final.get("error", final)
checks = final["verification"]["checks"]
assert all(c["status"] == "pass" for c in checks), checks
record("adjust_three_backends_512m", plan=final["id"], checks=len(checks))

with httpx.Client(base_url="http://127.0.0.1:8080", timeout=10, trust_env=False) as app:
    records = app.get("/api/records").json()
    assert any(r["id"] == saved["id"] and r["value"] == saved["value"] for r in records)
record("record_survives_adjust", id=saved["id"])

no_change = create("Triển khai Compose hiện tại")
assert no_change["status"] == "no_changes", no_change["status"]
record("no_op_recognized")
report["final_plan"] = final["id"]
report["verification"] = final["verification"]
report["passed"] = True
target = ROOT / ".hitl/demo-report.json"
target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(f"PASS — report: {target}", flush=True)
