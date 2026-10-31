"""Run the free tool-using agent through the Codex CLI, one `codex exec` per case.

The agent gets the same blind-test brief as the Claude subagents: it may only
run `python -m rca tool ...` and must submit through `tool submit --run <run>`.
Codex's JSON event stream is kept per case and audited afterwards for commands
that touch the answers or the data directly.
"""

import json
import re
import subprocess
from pathlib import Path

CODEX = "/usr/lib/chatgpt/resources/codex"
FORBIDDEN = re.compile(r"record\.csv|query\.csv|data/openrca|data/parquet|runs/|rca (score|cases|baseline)|duckdb|python3? -c|\.parquet")

BRIEF = """You are a site reliability engineer doing root-cause analysis (RCA) on the OpenRCA "Bank" dataset. Solve case {case_id}.

## Rules (blind test, strict)
- Gather evidence ONLY by running `{python} -m rca tool <name> ...` from {repo}.
- Do NOT read, list or grep anything under data/, runs/, README.md, tests/ or the .py source files. Do NOT run `python -m rca cases`, `score` or `baseline`, and no ad-hoc Python or SQL against the data.
- Do not edit any files.

## The system
A bank web app under synthetic load (k6). Requests flow apache01/02 (web) -> IG01/02 (gateway) -> Tomcat01-04 (app servers, JVM) -> MG01/02 (middleware) -> dockerA1/A2/B1/B2. Mysql01/02 and Redis01/02 are datastores. Traces only cover IG, Tomcat, MG and docker; apache, Mysql and Redis appear only in metrics and logs. Business KPIs (success rate, response time) are per service, not per host.
Possible components: apache01, apache02, Tomcat01, Tomcat02, Tomcat03, Tomcat04, MG01, MG02, IG01, IG02, Mysql01, Mysql02, Redis01, Redis02.
Possible reasons: high CPU usage; high memory usage; network latency; network packet loss; high disk I/O read usage; high disk space usage; high JVM CPU load; JVM Out of Memory (OOM) Heap.
The fault time is graded within 60 seconds: give the minute the fault started. Component and reason must match exactly.

## Tools
- `question {case_id}`: the query and window. Start here.
- `overview {case_id}`: business KPIs per minute; log lines and spans per component vs normal.
- `anomalies {case_id} [--component X] [--top N]`: metric series ranked by z-score vs normal, with onset minute and a guessed reason (heuristic; big z on near-constant series can be noise).
- `series {case_id} <component> <kpi-substring>`: minute-by-minute values with normal mean/std.
- `traces {case_id}`: MicroRank and TraceRCA rankings (blind to apache/Mysql/Redis).
- `logs {case_id} <component> [--grep TEXT] [--samples N]`: log lines per minute and sample lines.
- `submit {case_id} --run {run} --time "YYYY-MM-DD HH:MM:SS" --component X --reason "..." [--append]`: record one answer; --append for the 2nd and later failures.

## Workflow
1) read the question; 2) symptoms; 3) localise with metrics, traces and logs, cross-checking; 4) decide the reason from which KPIs moved; 5) verify on the chosen component, going back to 3 at most twice.
Always submit time, component and reason. About 6-12 tool calls. Finish with 2-3 sentences on the decisive evidence and your confidence (low/medium/high)."""


def _commands(event) -> list[str]:
    """Every string under a key naming a command, anywhere in a Codex event."""
    found = []
    if isinstance(event, dict):
        for key, value in event.items():
            if key in ("command", "cmd") and isinstance(value, (str, list)):
                found.append(" ".join(value) if isinstance(value, list) else value)
            else:
                found += _commands(value)
    elif isinstance(event, list):
        for item in event:
            found += _commands(item)
    return found


def audit(events_path: Path) -> tuple[int, list[str]]:
    """Commands the agent ran, and those breaking the blind-test rules."""
    commands = []
    for line in events_path.read_text().splitlines():
        try:
            commands += _commands(json.loads(line))
        except json.JSONDecodeError:
            continue
    commands = list(dict.fromkeys(commands))
    return len(commands), [c for c in commands if FORBIDDEN.search(c.replace(" -m rca tool ", " "))]


def solve(case_id: str, repo: Path, run_dir: Path, model: str | None, oss: bool, effort: str | None,
          timeout: int = 1200) -> tuple[int, list[str], float]:
    log_dir = run_dir / "codex"
    log_dir.mkdir(parents=True, exist_ok=True)
    prompt = BRIEF.format(case_id=case_id, run=run_dir.name, repo=repo, python=repo / ".venv/bin/python")
    command = [CODEX, "exec", "--json", "--skip-git-repo-check", "--ephemeral", "-s", "workspace-write",
               "-C", str(repo), "-o", str(log_dir / f"{case_id}.final.txt")]
    if model:
        command += ["-m", model]
    if oss:
        command += ["--oss", "--local-provider", "ollama"]
    if effort:
        command += ["-c", f'model_reasoning_effort="{effort}"']
    events = log_dir / f"{case_id}.jsonl"
    with open(events, "w") as out:
        # An open stdin makes `codex exec` wait for more prompt text.
        result = subprocess.run(command + [prompt], stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                timeout=timeout)
    calls, violations = audit(events)
    return calls, violations, result.returncode
