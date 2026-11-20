"""Run one agent on one task (a case, a profile survey or a profile translation), writing UI events as it goes.

The agent gets the project's investigation tools and nothing else: no shell, no file access. The tools are defined
once in rca.ui.toolspec and offered in two ways:
- claude_code: the `claude` CLI installed on this machine, in print mode with stream-json output, with every built-in
  tool turned off (--tools "") and the rca tools served by rca.ui.mcp_tools (--mcp-config, --strict-mcp-config);
  user and project settings are not loaded.
- api: the Anthropic SDK against the configured provider (Anthropic or an Anthropic-compatible base URL), a tool-use
  loop over the same tools.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from rca.ui import events as ev
from rca.ui import settings as cfg
from rca.ui import toolspec

ROOT = Path(__file__).resolve().parents[2]

# The project's blind-test instructions for one case, with the shell commands replaced by the tools the agent gets.
RULES = """You are doing root-cause analysis (RCA) on an incident of a system. Solve this one case on your own: {case}.

## Tools
You have only the tools of this RCA project: question, overview, calls, series, logs, baro, exclusive, traces, onset,
hypothesis_add, hypothesis_test, hypothesis_list and submit{read_tool}. They all work on case {case}; you do not pass
the case id. Every investigation tool also takes optional filter, head and tail to return only part of its output.
You can call several tools in one turn, and batch runs several of them in one call and returns all their outputs:
use either for calls that do not depend on each other.
{profile_rule}
## How to work
Start with `question`: it prints the query, the time window and the components and reasons you must answer with.
Then reason about the system as an experienced backend engineer would, and use the tools when you need data to settle
a question you have. Before a tool call, say what you want to find out and why; after it, say what the result means
for the system before you decide the next step. You do not have to call a tool every turn: when what you have seen is
enough to think further or to answer, think or answer. Statistical rankings and labels from the tools are hints, not
conclusions: judge yourself whether what they point at could really be the fault.
hypothesis_add, hypothesis_test and hypothesis_list record candidate causes if you find that useful; they are not
required.
The fault time is graded within ±60 seconds of when the root cause started. Component and reason must be exact strings
from the lists the question prints.
Submit one answer per failure with submit (append=true for the second and later failures of a multi-failure query;
without it a submit replaces earlier answers). Always submit time, component and reason even if only some are asked.
Never leave the case without a submission.

## Final report
Give: the submitted answer; the causes you considered and why you kept or dropped each; whether the profile changed a
decision; and your confidence (low/med/high)."""

# A reader watching the session needs the hypotheses and the evidence written out in full.
WRITING = """## How to write (read this first)
Write your reasoning in full sentences that a reader who does not see the tool output can follow: say what you saw and
what you conclude, without telegraphic notes, shorthand, or arrows and abbreviations you do not explain. Use what you
have already observed; do not run extra tools only to have more to write. If you use the hypothesis tools, write
if_true, if_false and the evidence the same way. In the final report, explain each cause you considered and why you
kept or dropped it the same way.

"""

# Added only when the user picks Vietnamese: the investigation stays in English, the closing report gets a Vietnamese
# version as well.
VIETNAMESE_REPORT = """
After the final report, give the same report again in Vietnamese. Keep component, reason, metric and tool names and
numbers as they are."""


def profile_path(system: str) -> Path | None:
    """The profile the agent reads: the English one (the Vietnamese translation is for people reading the UI)."""
    from rca.ui import profiles

    path = profiles.path(system)
    return path if path.exists() else None


def instructions(case: str, system: str, use_profile: bool, language: str = "en") -> str:
    profile = profile_path(system) if use_profile else None
    rule = ("First call read_profile: a profile of this system written from healthy periods (no faults). It describes\n"
            "topology, which data each component has, normal levels and what the statistical tools report as noise when\n"
            "nothing is wrong. Use it as background.\n") if profile else "There is no system profile for this system.\n"
    text = RULES.format(case=case, profile_rule=rule, read_tool=", and read_profile" if profile else "")
    # The writing section goes right after the opening line, where it is not missed.
    first, rest = text.split("\n\n", 1)
    return f"{first}\n\n{WRITING}{rest}" + (VIETNAMESE_REPORT if language == "vi" else "")


def case_task(case: str, system: str, run: str, use_profile: bool, language: str = "en") -> dict:
    profile = profile_path(system) if use_profile else None
    return {"mode": "case", "system": system, "case": case, "run": run, "read": str(profile or ""), "write": ""}


class Run(threading.Thread):
    """One agent run; `emit` receives every UI event, `finish` the final status."""

    def __init__(self, backend: str, task: dict, prompt: str, emit, finish):
        super().__init__(daemon=True)
        self.backend, self.task, self.prompt = backend, task, prompt
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
    def command(self, settings: dict) -> tuple[list[str], dict]:
        """The claude command line and environment for this run."""
        model = (settings["model"] if settings["claude_code_use_provider"] and settings["model"].strip()
                 else settings["claude_code_model"])
        server = {"type": "stdio", "command": sys.executable, "args": ["-m", "rca.ui.mcp_tools"],
                  "env": {"RCA_UI_TASK": json.dumps(self.task), "PYTHONPATH": str(ROOT)}}
        command = ["claude", "-p", self.prompt, "--output-format", "stream-json", "--verbose", "--model", model,
                   "--permission-mode", "dontAsk", "--setting-sources", "", "--no-session-persistence",
                   "--tools", "", "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": {"rca": server}}),
                   "--allowedTools", "mcp__rca"]
        # Keep the login (CLAUDE_CONFIG_DIR) but drop API credentials and the variables of any Claude Code session the
        # server itself was started from, so each run is its own session.
        own = {k: v for k, v in os.environ.items()
               if not k.startswith(("ANTHROPIC_", "CLAUDE_CODE_", "CLAUDE_AGENT_SDK")) and k not in ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT")}
        env = own | cfg.claude_env(settings) | {
            "MCP_TOOL_TIMEOUT": "600000",  # a tool can be slow on a busy machine
        }
        return command, env

    def _claude_code(self) -> str:
        command, env = self.command(cfg.load())
        self.emit({"kind": "prompt", "text": self.prompt})
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

        s = cfg.load()
        if not s["model"].strip():
            raise ValueError("no model configured: open Settings")
        credentials = {"auth_token" if s["auth"] == "auth_token" else "api_key": s["api_key"]} if s["api_key"] else {}
        client = anthropic.Anthropic(base_url=s["base_url"].strip() or None, **credentials)
        tools = toolspec.definitions(self.task)
        params = {"model": s["model"].strip(), "max_tokens": 16000, "tools": tools}
        if cfg.official(s):
            params["thinking"] = {"type": "adaptive", "display": "summarized"} if s["thinking"] else {"type": "adaptive"}
            params["output_config"] = {"effort": s["effort"]}
            if params["model"] in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-2",
                                   "claude-fable-5-1"):
                params["betas"] = ["server-side-fallback-2026-07-01"]
                params["fallbacks"] = "default"
        elif s["thinking"]:
            params["thinking"] = {"type": "adaptive", "display": "summarized"}
        self.emit({"kind": "prompt", "text": self.prompt})
        self.emit({"kind": "info", "model": params["model"], "backend": "api",
                   "base_url": s["base_url"].strip() or "https://api.anthropic.com", "tools": [t["name"] for t in tools]})
        messages = [{"role": "user", "content": self.prompt}]
        usage = {"input_tokens": 0, "output_tokens": 0}
        started, status = time.time(), "finished"
        for turn in range(int(s["max_iterations"])):
            if self.stopped.is_set():
                break
            response = client.beta.messages.create(**params, messages=messages)
            if response.usage:
                usage["input_tokens"] += response.usage.input_tokens or 0
                usage["output_tokens"] += response.usage.output_tokens or 0
            for block in response.content:
                if block.type == "thinking" and (block.thinking or "").strip():
                    self.emit({"kind": "thinking", "text": block.thinking})
                elif block.type == "text" and block.text.strip():
                    self.emit({"kind": "text", "text": block.text})
            if response.stop_reason == "refusal":
                self.emit({"kind": "error", "text": "the model declined the request (stop_reason: refusal)"})
                status = "error"
                break
            messages.append({"role": "assistant", "content": response.content})  # thinking blocks passed back unchanged
            if response.stop_reason == "pause_turn":
                continue
            calls = [b for b in response.content if b.type == "tool_use"]
            if not calls:
                break
            results = []
            for block in calls:
                self.emit(ev.tool_call_event(block.id, block.name, dict(block.input)))
                t0 = time.time()
                output, is_error = toolspec.call(self.task, block.name, dict(block.input))
                self.emit({"kind": "tool_result", "id": block.id, "output": output, "is_error": is_error,
                           "ms": int(1000 * (time.time() - t0))})
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output, "is_error": is_error})
            messages.append({"role": "user", "content": results})
        self.emit({"kind": "done", "status": status, "duration_ms": int(1000 * (time.time() - started)),
                   "turns": turn + 1, "usage": usage})
        return status
