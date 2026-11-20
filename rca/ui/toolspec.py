"""The project's investigation tools as typed tools, the only abilities an agent run from the UI gets.

Each tool runs the matching `python -m rca tool ...` command and returns what it prints, unchanged. The case (or the
healthy window, when writing a profile) and the run name are filled in from the task, so the agent never types them.

A task is a dict:
  mode     "case" (solve a case), "profile" (survey healthy windows and write a profile) or "translate"
  system   the OpenRCA system
  case     the case id (mode "case")
  windows  the healthy windows it may use (mode "profile")
  run      the run name hypotheses and answers go to (mode "case")
  read     the file read_profile returns, or ""
  write    the file write_profile writes (modes "profile" and "translate")
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_LIMIT = 40_000
BATCH_LIMIT = 20

S = {"type": "string"}
I = {"type": "integer"}

# name: (description, properties, required, how to build the arguments after `rca tool`)
INVESTIGATION = {
    "question": ("The query, the time window and the components and reasons you must answer with.", {}, [],
                 lambda a, w: ["question", w]),
    "overview": ("Business KPIs per minute; log lines and spans per component vs normal.", {}, [],
                 lambda a, w: ["overview", w]),
    "calls": ("Caller -> callee pairs in the traces: volume and call duration vs normal.", {}, [],
              lambda a, w: ["calls", w]),
    "series": ("KPIs of a component whose name contains `pattern`, minute by minute (an empty pattern lists every KPI "
               "name of the component).", {"component": S, "pattern": S}, ["component", "pattern"],
               lambda a, w: ["series", w, a["component"], a["pattern"]]),
    "logs": ("Log lines per minute and sample lines of a component, optionally only those containing `grep`.",
             {"component": S, "grep": S, "samples": I}, ["component"],
             lambda a, w: ["logs", w, a["component"], *(["--grep", a["grep"]] if a.get("grep") else []),
                           *(["--samples", str(a["samples"])] if a.get("samples") else [])]),
    "baro": ("Metric series ranked by BARO: change point, then robust score per series; `component` keeps only its series.",
             {"component": S, "top": I}, [],
             lambda a, w: ["baro", w, *(["--component", a["component"]] if a.get("component") else []),
                           *(["--top", str(a["top"])] if a.get("top") else [])]),
    "exclusive": ("Rises in exclusive time per component, from the traces.", {}, [], lambda a, w: ["exclusive", w]),
    "traces": ("MicroRank ranking of traced components.", {}, [], lambda a, w: ["traces", w]),
    "onset": ("When a component's series started to deviate, and when the components calling it (from the traces) did: "
              "to check a hypothesis's onset and order.", {"component": S, "pattern": S, "top": I}, ["component"],
              lambda a, w: ["onset", w, a["component"], *(["--pattern", a["pattern"]] if a.get("pattern") else []),
                            *(["--top", str(a["top"])] if a.get("top") else [])]),
}

HYPOTHESES = {
    "hypothesis_add": ("Add a hypothesis: a candidate cause with what you expect to observe if it is true and if it is false.",
                       {"component": S, "reason": S, "if_true": S, "if_false": S},
                       ["component", "reason", "if_true", "if_false"],
                       lambda a, c, run: ["hypothesis", c, "add", "--run", run, "--component", a["component"],
                                          "--reason", a["reason"], "--if-true", a["if_true"], "--if-false", a["if_false"]]),
    "hypothesis_test": ("Record the outcome of testing a hypothesis, with the evidence: which tool output, which numbers.",
                        {"id": I, "status": {"type": "string", "enum": ["supported", "refuted", "open"]}, "evidence": S},
                        ["id", "status", "evidence"],
                        lambda a, c, run: ["hypothesis", c, "test", str(a["id"]), "--run", run, "--status", a["status"],
                                           "--evidence", a["evidence"]]),
    "hypothesis_list": ("List your hypotheses with their status and evidence.", {}, [],
                        lambda a, c, run: ["hypothesis", c, "list", "--run", run]),
    "submit": ("Submit one answer per failure (append=true for the second and later failures).",
               {"time": {"type": "string", "description": "YYYY-MM-DD HH:MM:SS"}, "component": S, "reason": S,
                "append": {"type": "boolean"}}, ["time", "component", "reason"],
               lambda a, c, run: ["submit", c, "--run", run, "--time", a["time"], "--component", a["component"],
                                  "--reason", a["reason"], *(["--append"] if a.get("append") else [])]),
}


# Every investigation tool can return part of its output, as `| grep`, `| head` and `| tail` did in a shell.
TRIM = {
    "filter": {"type": "string", "description": "keep only the output lines containing this text (case-insensitive)"},
    "head": {"type": "integer", "description": "keep only the first N lines (after filter)"},
    "tail": {"type": "integer", "description": "keep only the last N lines (after filter)"},
}


def trim(text: str, args: dict) -> str:
    lines = text.splitlines()
    if args.get("filter"):
        needle = str(args["filter"]).lower()
        lines = [line for line in lines if needle in line.lower()]
    if args.get("head"):
        lines = lines[: int(args["head"])]
    if args.get("tail"):
        lines = lines[-int(args["tail"]):]
    return "\n".join(lines) if lines else "(no line left after filter/head/tail)"


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def definitions(task: dict) -> list[dict]:
    """Tool definitions ({name, description, input_schema}) for the task."""
    mode, tools = task["mode"], []
    if mode in ("case", "profile"):
        for name, (desc, props, req, _) in INVESTIGATION.items():
            if mode == "profile":
                props, req = {"window": {"type": "string", "enum": task["windows"]}, **props}, ["window", *req]
            tools.append({"name": name, "description": desc, "input_schema": _schema({**props, **TRIM}, req)})
    if mode == "case":
        tools += [{"name": n, "description": d, "input_schema": _schema(p, r)} for n, (d, p, r, _) in HYPOTHESES.items()]
    if mode in ("case", "profile"):
        # Several tools in one call, as `a; b; c` or a for-loop did in a shell.
        names = [t["name"] for t in tools]
        tools.append({"name": "batch", "description": "Run several of the other tools in one call, one after another, and "
                      "return all their outputs, each under a header. Each call is {\"tool\": name, ...that tool's "
                      "arguments}. Use it for calls that do not depend on each other's output.",
                      "input_schema": _schema({"calls": {"type": "array", "minItems": 1, "maxItems": BATCH_LIMIT, "items": {
                          "type": "object", "properties": {"tool": {"type": "string", "enum": names}}, "required": ["tool"],
                          "additionalProperties": True}}}, ["calls"])})
    if task.get("read"):
        tools.append({"name": "read_profile", "description": "Read the system profile: how this system looks in healthy "
                      "periods." if mode == "case" else "Read the English profile to translate.",
                      "input_schema": _schema({}, [])})
    if mode in ("profile", "translate"):
        tools.append({"name": "write_profile", "description": f"Write the profile (Markdown) to {task['write']}, replacing it.",
                      "input_schema": _schema({"content": S}, ["content"])})
    return tools


def argv(task: dict, name: str, args: dict) -> list[str] | None:
    """The arguments after `python -m rca` for an investigation, hypothesis or submit tool; None for the others."""
    # Cases run without the hypothesis protocol: an answer is accepted without a supported and a refuted hypothesis,
    # and `question` does not ask for one.
    free = ["--protocol", "free"] if name in ("question", "submit") else []
    if name in INVESTIGATION:
        where = args.get("window") if task["mode"] == "profile" else task["case"]
        system = ["--system", task["system"]] if task["mode"] == "profile" else []
        return [*system, "tool", *free, *INVESTIGATION[name][3](args, where)]
    if name in HYPOTHESES:
        return ["tool", *free, *HYPOTHESES[name][3](args, task["case"], task["run"])]
    return None


def _shown(name: str, args: dict) -> str:
    return f"{name}({', '.join(f'{k}={v!r}' for k, v in args.items())})"


def call(task: dict, name: str, args: dict) -> tuple[str, bool]:
    """Run one tool; returns its output and whether it failed."""
    allowed = {t["name"] for t in definitions(task)}
    if name not in allowed:
        return f"unknown tool {name!r}", True
    if name == "batch":
        calls = args.get("calls") or []
        if not calls or len(calls) > BATCH_LIMIT:
            return f"batch takes 1 to {BATCH_LIMIT} calls", True
        parts, failed = [], 0
        for c in calls:
            sub = {k: v for k, v in c.items() if k != "tool"}
            tool = c.get("tool", "")
            output, is_error = (f"{tool!r} cannot be used inside batch", True) if tool == "batch" else call(task, tool, sub)
            failed += is_error
            parts.append(f"=== {_shown(tool, sub)}{' [error]' if is_error else ''} ===\n{output}")
        return "\n\n".join(parts)[:OUTPUT_LIMIT], failed == len(calls)
    if task["mode"] == "profile" and name in INVESTIGATION and args.get("window") not in task["windows"]:
        return f"window must be one of {', '.join(task['windows'])}", True
    if name == "read_profile":
        return Path(task["read"]).read_text(), False
    if name == "write_profile":
        out = Path(task["write"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(args["content"])
        return f"wrote {len(args['content'])} characters to {out}", False
    try:
        result = subprocess.run([sys.executable, "-m", "rca", *argv(task, name, args)], cwd=ROOT, capture_output=True,
                                text=True, timeout=600)
    except KeyError as e:
        return f"missing argument {e}", True
    except subprocess.TimeoutExpired:
        return "the tool took longer than 10 minutes", True
    output = (result.stdout + result.stderr).strip() or "(no output)"
    if name in INVESTIGATION and result.returncode == 0:
        output = trim(output, args)
    return output[:OUTPUT_LIMIT], result.returncode != 0


def ui_call(name: str, args: dict) -> list[dict]:
    """The call as the UI shows rca commands: [{tool, case, argv, args}], argv being what follows the case id."""
    import shlex
    if name == "batch":
        return [u for c in args.get("calls") or [] for u in ui_call(c.get("tool", ""), {k: v for k, v in c.items() if k != "tool"})]
    if name in INVESTIGATION:
        built = INVESTIGATION[name][3](args, args.get("window", ""))
        tool, rest = built[0], built[2:]
    elif name in HYPOTHESES:
        built = HYPOTHESES[name][3](args, "", "")
        tool, rest = built[0], built[2:]
        while "--run" in rest:  # the run name is filled in by the UI, not chosen by the agent
            k = rest.index("--run")
            rest = rest[:k] + rest[k + 2:]
    else:
        return []
    return [{"tool": tool, "case": args.get("window", ""), "argv": rest, "args": shlex.join(rest)}]
