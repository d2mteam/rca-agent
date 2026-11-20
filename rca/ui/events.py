"""Normalise Claude Code output (stream-json lines or a saved transcript) into UI events.

An event is a dict with a `kind`:
  info         model and tools of the run
  prompt       the instructions the agent was given
  thinking     a reasoning block (summarised when the backend returns one)
  text         text the agent wrote
  tool_call    id, name (Bash, Read, rca_tool...), command, rca (the rca tools it runs)
  tool_result  id, output, is_error
  done         status, duration_ms, cost_usd, turns, usage
  error        text
"""

import re
from datetime import datetime

RCA_TOOL = re.compile(r"rca tool\s+(?:--protocol\s+\S+\s+)?([a-z]+)\s+(\S+)((?:\s+(?![;&|])[^;&|\n]+)?)")
HYPOTHESIS = re.compile(r"^#(\d+) \[(open|supported|refuted)\] (.+?) / (.+)$")


ASSIGNMENT = re.compile(r"""(?:^|[\s;&(])([A-Za-z_]\w*)=(?:"([^"]*)"|'([^']*)'|([^\s;&|]*))""")


def expand(command: str) -> str:
    """The command with the shell variables it assigns itself substituted (agents write `C=bank-004; $B overview $C`)."""
    values = {m[1]: next(v for v in (m[2], m[3], m[4]) if v is not None) for m in ASSIGNMENT.finditer(command or "")}
    for name in sorted(values, key=len, reverse=True):
        command = command.replace("${" + name + "}", values[name])
        command = re.sub(r"\$" + name + r"\b", lambda _: values[name], command)
    return command


def rca_calls(command: str) -> list[dict]:
    """The `rca tool` invocations inside a shell command: tool, case and the remaining arguments."""
    return [{"tool": m[1], "case": m[2], "args": m[3].strip()} for m in RCA_TOOL.finditer(expand(command or ""))]


def parse_hypotheses(text: str) -> list[dict]:
    """Hypotheses as `tool hypothesis` prints them (rca.hypotheses.describe)."""
    found, current = [], None
    for line in (text or "").splitlines():
        m = HYPOTHESIS.match(line.strip())
        if m:
            current = {"id": int(m[1]), "status": m[2], "component": m[3], "reason": m[4], "if_true": "",
                       "if_false": "", "evidence": []}
            found.append(current)
        elif current is not None:
            s = line.strip()
            if s.startswith("if true:"):
                current["if_true"] = s[len("if true:"):].strip()
            elif s.startswith("if false:"):
                current["if_false"] = s[len("if false:"):].strip()
            elif s.startswith("evidence:"):
                current["evidence"].append(s[len("evidence:"):].strip())
    return found


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def _time(line: dict) -> float | None:
    stamp = line.get("timestamp")
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def normalise(line: dict) -> list[dict]:
    """UI events in one Claude Code JSON line (stream-json or transcript); `t` is set when the line has a time."""
    t = _time(line)
    out: list[dict] = []
    kind = line.get("type")
    if kind == "system" and line.get("subtype") == "init":
        out.append({"kind": "info", "model": line.get("model"), "tools": line.get("tools", [])})
    elif kind == "result":
        out.append({"kind": "done", "status": "error" if line.get("is_error") else "finished",
                    "duration_ms": line.get("duration_ms"), "cost_usd": line.get("total_cost_usd"),
                    "turns": line.get("num_turns"), "usage": line.get("usage"), "result": line.get("result")})
    elif kind in ("assistant", "user"):
        content = (line.get("message") or {}).get("content")
        if isinstance(content, str):
            if kind == "user":
                out.append({"kind": "prompt", "text": content})
        else:
            for block in content or []:
                b = block.get("type")
                if b == "thinking" and block.get("thinking", "").strip():
                    out.append({"kind": "thinking", "text": block["thinking"]})
                elif b == "text" and block.get("text", "").strip():
                    out.append({"kind": "prompt" if kind == "user" else "text", "text": block["text"]})
                elif b == "tool_use":
                    command = block.get("input", {}).get("command") or block.get("input", {}).get("file_path") or ""
                    out.append({"kind": "tool_call", "id": block.get("id"), "name": block.get("name"),
                                "command": command, "rca": rca_calls(command)})
                elif b == "tool_result":
                    out.append({"kind": "tool_result", "id": block.get("tool_use_id"),
                                "output": _text(block.get("content")), "is_error": bool(block.get("is_error"))})
    for e in out:
        if t is not None:
            e["t"] = t
    return out


def tidy(events: list[dict]) -> list[dict]:
    """Drop what a saved transcript repeats: calls that never got a result and repeated texts."""
    answered = {e["id"] for e in events if e["kind"] == "tool_result"}
    seen_calls, kept, last_text = set(), [], None
    for e in events:
        if e["kind"] == "tool_call":
            if e["id"] not in answered or e["id"] in seen_calls:
                continue
            seen_calls.add(e["id"])
        if e["kind"] in ("text", "thinking"):
            if e["text"] == last_text:
                continue
            last_text = e["text"]
        kept.append(e)
    return kept


def split_by_case(events: list[dict], case_id: str, order: list[str] | None = None) -> list[dict]:
    """The part of a multi-case transcript that works on one case.

    It starts at the first tool call that names the case and ends before the first call naming the case
    solved after it (`order`, as the instructions list them); a call naming both, such as a submit chained
    with the next question, belongs to both. Text written just before the first call belongs to the case.
    """
    def names(e, case):
        return e["kind"] == "tool_call" and any(c["case"] == case for c in e["rca"])

    start = next((i for i, e in enumerate(events) if names(e, case_id)), None)
    if start is None:
        return []
    while start > 0 and events[start - 1]["kind"] in ("text", "thinking"):
        start -= 1
    later = (order or [])[(order or []).index(case_id) + 1:] if order and case_id in order else []
    end = len(events)
    for i in range(start + 1, len(events)):
        if any(names(events[i], other) for other in later):
            end = i + 1 if names(events[i], case_id) else i
            # Results of a shared call follow it.
            while end < len(events) and events[end]["kind"] == "tool_result" and end == i + 1:
                end += 1
            break
    while end > start + 1 and events[end - 1]["kind"] in ("text", "thinking") and end < len(events):
        end -= 1
    return events[start:end]
