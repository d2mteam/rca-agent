"""Tool-calling RCA agent on a local Ollama model.

The model gets the query and the same tools as `python -m rca tool`, calls them
through Ollama's chat API, and submits answers. Every message is logged to
runs/<run>/transcripts/<case>.jsonl.
"""

import contextlib
import io
import json
import urllib.request
from pathlib import Path

import duckdb

from rca import bank, tools
from rca.cases import Case

MAX_OUTPUT = 4000

SYSTEM = f"""You are a site reliability engineer finding the root cause of failures in a bank web system.
Requests flow apache01/02 (web) -> IG01/02 (gateway) -> Tomcat01-04 (app servers, JVM) -> MG01/02 (middleware) -> docker services.
Mysql01/02 and Redis01/02 are datastores. Traces cover IG, Tomcat, MG and docker only.
Components: {", ".join(bank.COMPONENTS)}.
Reasons: {"; ".join(bank.REASONS)}.
The fault time is graded within 60 seconds: give the minute the fault started.

Work in steps: 1) symptoms with overview; 2) find which component changed with anomalies, series, traces and logs;
3) decide the reason from which metrics moved; 4) verify on the chosen component; 5) call submit once per failure.
Big z-scores on near-constant series or single-sample spikes are often noise. Always submit time, component and reason."""


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


_COMPONENT = {"type": "string", "enum": list(bank.COMPONENTS)}
TOOLS = [
    _schema("overview", "Business KPIs per minute, log lines and spans per component vs normal.", {}, []),
    _schema("anomalies", "Metric series ranked by z-score vs normal, with onset minute and a guessed reason.",
            {"component": _COMPONENT, "top": {"type": "integer"}}, []),
    _schema("series", "Minute-by-minute values of one component's KPIs whose name contains `kpi`.",
            {"component": _COMPONENT, "kpi": {"type": "string", "description": "e.g. CPU, HeapMemory, FIN-WAIT, DSKRead"}},
            ["component", "kpi"]),
    _schema("traces", "MicroRank and TraceRCA rankings of traced components.", {}, []),
    _schema("logs", "Log lines per minute and sample lines of one component.",
            {"component": _COMPONENT, "grep": {"type": "string"}}, ["component"]),
    _schema("submit", "Record one root cause. Call once per failure.",
            {"time": {"type": "string", "description": "YYYY-MM-DD HH:MM:SS"}, "component": _COMPONENT,
             "reason": {"type": "string", "enum": list(bank.REASONS)}},
            ["time", "component", "reason"]),
]


def _chat(url: str, model: str, messages: list, tool_list: list, num_ctx: int) -> dict:
    body = {"model": model, "messages": messages, "tools": tool_list, "stream": False,
            "options": {"num_ctx": num_ctx, "temperature": 0}}
    request = urllib.request.Request(f"{url}/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=900) as response:
        return json.load(response)["message"]


def _run_tool(con: duckdb.DuckDBPyConnection, case: Case, answers_dir: Path, name: str, args: dict, submitted: list) -> str:
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            if name == "overview":
                tools.overview(con, case)
            elif name == "anomalies":
                tools.anomalies(con, case, args.get("component"), int(args.get("top") or 15))
            elif name == "series":
                tools.series(con, case, args["component"], args["kpi"])
            elif name == "traces":
                tools.traces(con, case)
            elif name == "logs":
                tools.logs(con, case, args["component"], args.get("grep"), 5)
            elif name == "submit":
                tools.submit(case, answers_dir, args["time"], args["component"], args["reason"], append=bool(submitted))
                submitted.append(args)
            else:
                return f"unknown tool {name}"
    except (SystemExit, KeyError, ValueError, duckdb.Error) as error:
        return f"error: {error}"
    text = out.getvalue()
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + "\n... (truncated)"


def solve(con: duckdb.DuckDBPyConnection, case: Case, run_dir: Path, model: str,
          url: str = "http://localhost:11434", max_steps: int = 14, num_ctx: int = 16384) -> int:
    """Run the agent on one case; return the number of tool calls."""
    question = io.StringIO()
    with contextlib.redirect_stdout(question):
        tools.question(con, case)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": question.getvalue().rsplit("\nSubmit one answer", 1)[0]}]
    submitted: list = []
    calls = 0
    log_path = run_dir / "transcripts" / f"{case.case_id}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as log:
        for step in range(max_steps + 1):
            final = step == max_steps
            if final:
                messages.append({"role": "user", "content": "Stop investigating. Call submit now with your best answer for each failure."})
            reply = _chat(url, model, messages, [TOOLS[-1]] if final else TOOLS, num_ctx)
            messages.append(reply)
            log.write(json.dumps(reply) + "\n")
            requested = reply.get("tool_calls") or []
            if not requested:
                if submitted or final:
                    break
                messages.append({"role": "user", "content": "Use the tools to investigate, then call submit."})
                continue
            for call in requested:
                fn = call["function"]
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args or "{}")
                result = _run_tool(con, case, run_dir / "answers", fn["name"], args, submitted)
                calls += 1
                messages.append({"role": "tool", "tool_name": fn["name"], "content": result})
                log.write(json.dumps({"tool": fn["name"], "args": args, "result": result[:1500]}) + "\n")
            # The query states how many failures there are, so stopping at that count leaks nothing.
            if len(submitted) >= len(case.faults):
                break
    return calls
