"""Sessions on disk: runs/ui/<id>/meta.json and events.jsonl (one UI event per line)."""

import json
import threading
import time
from datetime import datetime
from pathlib import Path

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _lock(session_id: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(session_id, threading.Lock())


class Store:
    def __init__(self, runs: Path):
        self.root = runs / "ui"

    def create(self, **meta) -> dict:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        session_id = f"{meta['case']}-{stamp}"
        n = 1
        while (self.root / session_id).exists():
            n += 1
            session_id = f"{meta['case']}-{stamp}-{n}"
        (self.root / session_id).mkdir(parents=True)
        meta = {"id": session_id, "created": time.time(), "status": "running", "run": f"ui-{session_id}", **meta}
        self.save_meta(meta)
        return meta

    def save_meta(self, meta: dict) -> None:
        path = self.root / meta["id"] / "meta.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        tmp.replace(path)

    def meta(self, session_id: str) -> dict | None:
        path = self.root / session_id / "meta.json"
        return json.loads(path.read_text()) if path.exists() else None

    def update(self, session_id: str, **fields) -> dict:
        with _lock(session_id):
            meta = self.meta(session_id) or {}
            meta.update(fields)
            self.save_meta(meta)
            return meta

    def append(self, session_id: str, event: dict) -> dict:
        with _lock(session_id):
            path = self.root / session_id / "events.jsonl"
            n = sum(1 for _ in open(path)) if path.exists() else 0
            event = {"i": n, "t": event.get("t") or time.time(), **{k: v for k, v in event.items() if k != "t"}}
            with open(path, "a") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
            return event

    def events(self, session_id: str, start: int = 0) -> list[dict]:
        path = self.root / session_id / "events.jsonl"
        if not path.exists():
            return []
        with open(path) as f:
            return [json.loads(line) for n, line in enumerate(f) if n >= start and line.strip()]

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        metas = [self.meta(p.name) for p in self.root.iterdir() if (p / "meta.json").exists()]
        return sorted((m for m in metas if m), key=lambda m: m.get("created", 0), reverse=True)
