"""Hypothesis log an agent keeps while investigating one case.

The agent states candidate causes, says what it expects to see if each is true or false,
tests them with the other tools and records the outcome. An answer is accepted only when it
matches a supported hypothesis and at least one different hypothesis was refuted, so the
agent cannot settle on the first thing a tool ranked high without weighing an alternative.
The log holds no knowledge of the system: what to expect is the agent's own reasoning.
"""

import json
from pathlib import Path

from rca import bank

STATUSES = ("open", "supported", "refuted")


def _path(run_dir: Path, case_id: str) -> Path:
    return run_dir / "hypotheses" / f"{case_id}.json"


def load(run_dir: Path, case_id: str) -> list[dict]:
    path = _path(run_dir, case_id)
    return json.loads(path.read_text()) if path.exists() else []


def _save(run_dir: Path, case_id: str, hypotheses: list[dict]) -> None:
    path = _path(run_dir, case_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hypotheses, indent=1, ensure_ascii=False))


def add(run_dir: Path, case_id: str, component: str, reason: str, if_true: str, if_false: str) -> dict:
    if component not in bank.COMPONENTS:
        raise SystemExit(f"unknown component {component!r}; choose from {', '.join(bank.COMPONENTS)}")
    if reason not in bank.REASONS:
        raise SystemExit(f"unknown reason {reason!r}; choose from {'; '.join(bank.REASONS)}")
    if not if_true.strip() or not if_false.strip():
        raise SystemExit("state what you expect to observe if the hypothesis is true and if it is false")
    hypotheses = load(run_dir, case_id)
    entry = {"id": len(hypotheses) + 1, "component": component, "reason": reason,
             "if_true": if_true, "if_false": if_false, "status": "open", "evidence": []}
    hypotheses.append(entry)
    _save(run_dir, case_id, hypotheses)
    return entry


def test(run_dir: Path, case_id: str, hypothesis_id: int, status: str, evidence: str) -> dict:
    hypotheses = load(run_dir, case_id)
    entry = next((h for h in hypotheses if h["id"] == hypothesis_id), None)
    if entry is None:
        raise SystemExit(f"no hypothesis {hypothesis_id} for {case_id}")
    if status not in STATUSES:
        raise SystemExit(f"status must be one of {', '.join(STATUSES)}")
    if not evidence.strip():
        raise SystemExit("give the evidence: which tool output, which numbers")
    entry["status"] = status
    entry["evidence"].append(evidence)
    _save(run_dir, case_id, hypotheses)
    return entry


def describe(hypotheses: list[dict]) -> str:
    if not hypotheses:
        return "no hypotheses yet"
    return "\n".join(
        f"#{h['id']} [{h['status']}] {h['component']} / {h['reason']}\n"
        f"    if true: {h['if_true']}\n    if false: {h['if_false']}"
        + "".join(f"\n    evidence: {e}" for e in h["evidence"])
        for h in hypotheses
    )


def check_answer(hypotheses: list[dict], component: str, reason: str) -> str | None:
    """Why the answer is not accepted yet, or None."""
    if not any(h["status"] == "supported" and (h["component"], h["reason"]) == (component, reason) for h in hypotheses):
        return f"no supported hypothesis says {component} / {reason}; add it and test it first"
    if not any(h["status"] == "refuted" and (h["component"], h["reason"]) != (component, reason) for h in hypotheses):
        return "no alternative hypothesis was refuted; state and test at least one competing explanation"
    return None
