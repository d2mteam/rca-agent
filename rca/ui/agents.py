"""Run one agent on one case, writing UI events as it goes.

Two backends share the same instructions and the same blind-test rules:
- claude_code: the `claude` CLI installed on this machine, in print mode with stream-json output. Its
  permissions allow only `uv run python -m rca tool ...` and reading the system profile; user and project
  settings are not loaded, so no broader rule from them applies.
- api: the Anthropic SDK's tool runner against the configured provider (Anthropic or an Anthropic-compatible base
  URL), with the same two abilities: a shell that runs only `uv run python -m rca tool ...` and a reader that opens
  only the system profile.
"""

import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

from rca.ui import events as ev
from rca.ui import settings as cfg

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_LIMIT = 40_000

# The instructions of the project's blind-test runs, for one case.
RULES = """You are doing root-cause analysis (RCA) on an incident of a system. Solve this one case on your own: {case}.

## Rules (blind test, strict)
{profile_rule}- Apart from that, gather evidence ONLY through `uv run python -m rca tool <name> ...`, run from {root}.
- Do NOT read or grep anything under data/ (no CSV or Parquet), runs/, profiles/ (other than the file above), README.md,
  SETUP.md, docs/, tests/ or any .py source file. Do NOT run `python -m rca cases`, `python -m rca score` or
  `python -m rca baseline`, and do not run ad-hoc Python or SQL against the data. Those reveal the answers or skip the
  reasoning this test measures.
- Do not edit any files.

## How to work
Start with `uv run python -m rca tool question {case}`: it prints the query, the time window, the components and
reasons you must answer with, the available tools and how to submit. Investigate with the tools as you see fit; how you
read the data is up to you. Follow the hypothesis protocol the question describes: state competing hypotheses with what
you expect to see if each is true or false, test them, and record the outcome.
The fault time is graded within ±60 seconds of when the root cause started. Component and reason must be exact strings
from the lists the question prints.
Always add `--run {run}` to every hypothesis and submit command. Submit one answer per failure (`--append` for the
second and later failures of a multi-failure query; a submit without --append replaces earlier answers for that case).
Always submit time, component and reason even if only some are asked. Budget: about 15–25 tool commands. Never leave
the case without a submission.

## Final report
Give: the submitted answer; the hypotheses you tested and why each was supported or refuted (one line each); whether the
profile changed a decision; and your confidence (low/med/high). Keep it compact."""


# Added to the instructions only when the user picks Vietnamese in Settings; the default runs them unchanged.
VIETNAMESE = """

## Language
Think and reason in Vietnamese, and write your explanations in Vietnamese: what you write between tool commands, the
--if-true, --if-false and --evidence text of your hypotheses, and the final report. Keep in their original form: proper names, technical
terms (for example CPU, latency, packet loss, exclusive time, trace, span, change point), component names, KPI and
metric names, tool names, commands and tool output. The --component and --reason you pass to `hypothesis add` and
`submit` must stay the exact strings from the lists the question prints."""


def profile_path(system: str, language: str = "en") -> Path | None:
    """The profile the agent reads: the Vietnamese one when chosen and written, else the English one."""
    from rca.ui import profiles

    for path in ([profiles.path(system, "vi")] if language == "vi" else []) + [profiles.path(system)]:
        if path.exists():
            return path
    return None


def instructions(case: str, system: str, run: str, use_profile: bool, language: str = "en") -> str:
    profile = profile_path(system, language) if use_profile else None
    rule = (f"- First read {profile}: a profile of this system written from healthy periods (no faults). It describes\n"
            "  topology, which data each component has, normal levels and what the statistical tools report as noise\n"
            "  when nothing is wrong. Use it as background.\n") if profile else "- There is no system profile for this system.\n"
    return RULES.format(case=case, run=run, root=ROOT, profile_rule=rule) + (VIETNAMESE if language == "vi" else "")


class Run(threading.Thread):
    """One agent run; `emit` receives every UI event, `finish` the final status."""

    def __init__(self, backend: str, case: str, system: str, run: str, emit, finish, profile_task=None):
        super().__init__(daemon=True)
        self.backend, self.case, self.system, self.run_name = backend, case, system, run
        # (instructions, output path) when this run writes a system profile instead of solving a case.
        self.profile_task = profile_task
        self.emit, self.finish = emit, finish
        self.stopped = threading.Event()
        self.process: subprocess.Popen | None = None

    def stop(self):
        self.stopped.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def run(self):
        try:
            status = self._claude_code() if self.backend == "claude_code" else self._api()
        except Exception as e:  # report any failure in the session instead of losing it
            self.emit({"kind": "error", "text": f"{type(e).__name__}: {e}"})
            status = "error"
        self.finish("stopped" if self.stopped.is_set() else status)

    # --- claude code -------------------------------------------------------------------------------
    def _claude_code(self) -> str:
        s = cfg.load()
        if self.profile_task:
            prompt, out = self.profile_task
            allowed = [f"Bash(uv run python -m rca --system {self.system} tool:*)", f"Write(//{str(out).lstrip('/')})"]
        else:
            prompt = instructions(self.case, self.system, self.run_name, s["use_profile"], s["language"])
            allowed = ["Bash(uv run python -m rca tool:*)"]
            profile = profile_path(self.system, s["language"]) if s["use_profile"] else None
            if profile:
                allowed.append(f"Read(//{str(profile).lstrip('/')})")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")} | cfg.claude_env(s)
        model = s["model"] if s["claude_code_use_provider"] and s["model"].strip() else s["claude_code_model"]
        command = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", model,
                   "--permission-mode", "dontAsk", "--setting-sources", "", "--no-session-persistence",
                   "--allowedTools", *allowed]
        self.emit({"kind": "prompt", "text": prompt})
        self.emit({"kind": "info", "model": model, "backend": "claude_code",
                   "command": " ".join(shlex.quote(c) if c != prompt else "<prompt>" for c in command)})
        self.process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, bufsize=1)
        started, status = {}, "finished"
        for raw in self.process.stdout:
            if not raw.strip():
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for e in ev.normalise(line):
                if e["kind"] == "prompt":
                    continue  # already emitted
                if e["kind"] == "tool_call":
                    started[e["id"]] = time.time()
                if e["kind"] == "tool_result" and e["id"] in started:
                    e["ms"] = int(1000 * (time.time() - started.pop(e["id"])))
                    e["output"] = e["output"][:OUTPUT_LIMIT]
                if e["kind"] == "done":
                    status = e["status"]
                self.emit(e)
        err = self.process.stderr.read()
        code = self.process.wait()
        if code != 0 and not self.stopped.is_set():
            self.emit({"kind": "error", "text": (err or f"claude exited with {code}")[-4000:]})
            return "error"
        return status

    # --- api ----------------------------------------------------------------------------------------
    def _api(self) -> str:
        import anthropic
        from anthropic import beta_tool

        s = cfg.load()
        if not s["model"].strip():
            raise ValueError("no model configured: open Settings")
        credentials = {"auth_token" if s["auth"] == "auth_token" else "api_key": s["api_key"]} if s["api_key"] else {}
        client = anthropic.Anthropic(base_url=s["base_url"].strip() or None, **credentials)
        if self.profile_task:
            prompt, out = self.profile_task
            profile = None
        else:
            prompt = instructions(self.case, self.system, self.run_name, s["use_profile"], s["language"])
            profile = profile_path(self.system, s["language"]) if s["use_profile"] else None
            out = None

        @beta_tool
        def bash(command: str) -> str:
            """Run a shell command from the repository root and return its output. Only
            `uv run python -m rca tool <name> ...` commands are allowed.

            Args:
                command: The command to run.
            """
            return self._run_command(command)

        @beta_tool
        def read(file_path: str) -> str:
            """Read a text file.

            Args:
                file_path: Absolute path of the file.
            """
            call_id = f"call-{time.time_ns()}"
            self.emit({"kind": "tool_call", "id": call_id, "name": "Read", "command": file_path, "rca": []})
            ok = profile is not None and Path(file_path).resolve() == profile
            text = profile.read_text() if ok else "Permission denied: only the system profile can be read."
            self.emit({"kind": "tool_result", "id": call_id, "output": text, "is_error": not ok, "ms": 0})
            return text

        @beta_tool
        def write(file_path: str, content: str) -> str:
            """Write a text file, replacing it if it exists.

            Args:
                file_path: Absolute path of the file.
                content: The full content to write.
            """
            call_id = f"call-{time.time_ns()}"
            self.emit({"kind": "tool_call", "id": call_id, "name": "Write", "command": file_path, "rca": []})
            ok = out is not None and Path(file_path).resolve() == out
            if ok:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(content)
            text = f"wrote {len(content)} characters to {out}" if ok else "Permission denied: only the profile file can be written."
            self.emit({"kind": "tool_result", "id": call_id, "output": text, "is_error": not ok, "ms": 0})
            return text

        tools = [bash, write] if out else [bash, read] if profile else [bash]
        params = {"model": s["model"].strip(), "max_tokens": 16000, "max_iterations": int(s["max_iterations"]),
                  "tools": tools,
                  "messages": [{"role": "user", "content": prompt}]}
        if cfg.official(s):
            params["thinking"] = {"type": "adaptive", "display": "summarized"} if s["thinking"] else {"type": "adaptive"}
            params["output_config"] = {"effort": s["effort"]}
            if params["model"] in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-2",
                                   "claude-fable-5-1"):
                params["betas"] = ["server-side-fallback-2026-07-01"]
                params["fallbacks"] = "default"
        elif s["thinking"]:
            params["thinking"] = {"type": "adaptive", "display": "summarized"}
        self.emit({"kind": "prompt", "text": prompt})
        self.emit({"kind": "info", "model": params["model"], "backend": "api",
                   "base_url": s["base_url"].strip() or "https://api.anthropic.com"})
        usage = {"input_tokens": 0, "output_tokens": 0}
        started, turns = time.time(), 0
        runner = client.beta.messages.tool_runner(**params)
        status = "finished"
        for message in runner:
            turns += 1
            for block in message.content:
                if block.type == "thinking" and (block.thinking or "").strip():
                    self.emit({"kind": "thinking", "text": block.thinking})
                elif block.type == "text" and block.text.strip():
                    self.emit({"kind": "text", "text": block.text})
            if message.usage:
                usage["input_tokens"] += message.usage.input_tokens or 0
                usage["output_tokens"] += message.usage.output_tokens or 0
            if message.stop_reason == "refusal":
                self.emit({"kind": "error", "text": "the model declined the request (stop_reason: refusal)"})
                status = "error"
                break
            if self.stopped.is_set():
                break
        self.emit({"kind": "done", "status": status, "duration_ms": int(1000 * (time.time() - started)),
                   "turns": turns, "usage": usage})
        return status

    def _run_command(self, command: str) -> str:
        call_id = f"call-{time.time_ns()}"
        self.emit({"kind": "tool_call", "id": call_id, "name": "Bash", "command": command, "rca": ev.rca_calls(command)})
        started = time.time()
        output, is_error = self._execute(command)
        self.emit({"kind": "tool_result", "id": call_id, "output": output[:OUTPUT_LIMIT], "is_error": is_error,
                   "ms": int(1000 * (time.time() - started))})
        return output[:OUTPUT_LIMIT]

    def _execute(self, command: str) -> tuple[str, bool]:
        """Run `uv run python -m rca tool ...` and nothing else, the rule the Claude Code backend gets."""
        if self.stopped.is_set():
            return "the run was stopped", True
        try:
            args = shlex.split(command)
        except ValueError as e:
            return f"cannot parse the command: {e}", True
        rest = args[5:]
        if rest[:2] == ["--system", self.system]:
            rest = rest[2:]
        if args[:5] != ["uv", "run", "python", "-m", "rca"] or rest[:1] != ["tool"] or any(
                ch in command for ch in ";&|`$<>\n"):
            return "Permission denied: only `uv run python -m rca tool <name> ...` commands are allowed.", True
        result = subprocess.run([sys.executable, "-m", "rca", *args[5:]], cwd=ROOT, capture_output=True, text=True,
                                timeout=600)
        return (result.stdout + result.stderr).strip() or "(no output)", result.returncode != 0
