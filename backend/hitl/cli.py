import time
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.syntax import Syntax

app = typer.Typer(help="Docker HITL: dry-run, deploy, adjust. Backend mặc định http://127.0.0.1:8765")
console = Console()


def request(method, path, body=None):
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=540, trust_env=False) as client:
        response = client.request(method, path, json=body)
        if response.is_error:
            console.print(response.text, style="red")
            raise typer.Exit(1)
        return response.json()


def preview(project, prompt, file, planner, mode):
    result = request(
        "POST",
        "/api/plans",
        {
            "project": project,
            "request": prompt,
            "compose_yaml": file.read_text() if file else None,
            "planner": planner,
            "mode": mode,
        },
    )
    console.print(f"Plan: {result['id']} · {result['status']} · {result['planner']}")
    console.print(result.get("summary", ""))
    console.print(Syntax(result.get("diff", ""), "diff", theme="ansi_dark"))
    console.print(f"Hash: {result['plan_hash']}")
    for warning in result.get("warnings", []):
        console.print(warning, style="yellow")
    if result.get("error"):
        console.print(result["error"], style="yellow")


@app.command("dry-run")
def dry_run(
    prompt: str = "Triển khai Compose hiện tại",
    project: str = "hitl-demo",
    file: Path | None = None,
    planner: str = "template",
):
    preview(project, prompt, file, planner, "dry-run")


@app.command()
def adjust(prompt: str, project: str = "hitl-demo", file: Path | None = None, planner: str = "template"):
    preview(project, prompt, file, planner, "adjust")


@app.command()
def deploy(plan_id: str, approve_hash: str | None = None):
    plan = request("GET", f"/api/plans/{plan_id}")
    console.print(Syntax(plan["diff"], "diff", theme="ansi_dark"))
    console.print(f"Plan {plan_id}\nHash {plan['plan_hash']}")
    for warning in plan.get("warnings", []):
        console.print(warning, style="yellow")
    if approve_hash is not None and approve_hash != plan["plan_hash"]:
        raise typer.BadParameter("Hash không đúng với preview.")
    if approve_hash is None and not typer.confirm("Duyệt chính xác kế hoạch này và triển khai?"):
        request("POST", f"/api/plans/{plan_id}/reject", {"plan_hash": plan["plan_hash"]})
        raise typer.Exit()
    request("POST", f"/api/plans/{plan_id}/approve", {"plan_hash": plan["plan_hash"]})
    while True:
        plan = request("GET", f"/api/plans/{plan_id}")
        if plan["status"] in {"verified", "failed", "stale", "rejected"}:
            console.print(plan["status"], style="green" if plan["status"] == "verified" else "red")
            if plan.get("error"):
                console.print(plan["error"])
            raise typer.Exit(0 if plan["status"] == "verified" else 1)
        time.sleep(1)


@app.command()
def serve():
    import uvicorn

    uvicorn.run("hitl.api:app", host="127.0.0.1", port=8765)
