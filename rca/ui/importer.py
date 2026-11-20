"""Import a saved Claude Code transcript (JSONL) as a UI session, so it can be replayed."""

import json
from pathlib import Path

from rca.ui import events as ev
from rca.ui.sessions import Store


def import_transcript(store: Store, transcript: Path, case: str, system: str, run: str, case_text: str,
                      window: tuple[int, int], asks: list[str], model: str = "", order: list[str] | None = None) -> dict:
    lines = [json.loads(l) for l in open(transcript) if l.strip()]
    events = ev.tidy([e for line in lines for e in ev.normalise(line)])
    prompt = next((e for e in events if e["kind"] == "prompt"), None)
    body = ev.split_by_case(events, case, order)
    if not body:
        raise SystemExit(f"{transcript} has no work on {case}")
    times = [e["t"] for e in body if "t" in e]
    meta = store.create(case=case, system=system, backend="claude_code", model=model, imported=str(transcript),
                        profile=bool(prompt and "profiles/" in prompt["text"]))
    store.update(meta["id"], run=run, status="finished", finished=times[-1] if times else meta["created"],
                 created=times[0] if times else meta["created"])
    store.append(meta["id"], {"kind": "question", "text": case_text, "window": list(window), "asks": asks,
                              "t": times[0] if times else None})
    if prompt:
        store.append(meta["id"], {**prompt, "t": times[0] if times else None})
    started = {}
    for e in body:
        if e["kind"] == "tool_call" and "t" in e:
            started[e["id"]] = e["t"]
        if e["kind"] == "tool_result" and e["id"] in started and "t" in e:
            e["ms"] = int(1000 * (e["t"] - started[e["id"]]))
        store.append(meta["id"], e)
    store.append(meta["id"], {"kind": "done", "status": "finished", "t": times[-1] if times else None,
                              "duration_ms": int(1000 * (times[-1] - times[0])) if len(times) > 1 else None})
    return store.meta(meta["id"])
