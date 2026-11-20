"""Writing a system profile from healthy windows, with the instructions the project's profiles were written with.

The windows of Bank and Telecom are the ones profiles/bank.md and profiles/telecom.md came from. For another
system, up to six 30-minute windows are chosen in hours that have data, each at least an hour away from every
query window (the rule `rca tool` enforces for normal-YYYY_MM_DD-HHMM).
"""

import re
from datetime import datetime
from pathlib import Path

import duckdb

from rca import systems
from rca.cases import CST

ROOT = Path(__file__).resolve().parents[2]

# Defaults: the windows profiles/bank.md and profiles/telecom.md were written from. Settings can replace them.
DEFAULT_WINDOWS = {
    "Bank": ["normal-2021_03_06-0930", "normal-2021_03_07-0930", "normal-2021_03_23-1230", "normal-2021_03_24-0800",
             "normal-2021_03_10-2030", "normal-2021_03_05-0300"],
    "Telecom": ["normal-2020_05_22-0330", "normal-2020_05_28-0200", "normal-2020_05_28-0530", "normal-2020_05_31-0030",
                "normal-2020_05_31-0100", "normal-2020_05_31-0530"],
}

PROMPT = """You are surveying a software system before anyone investigates incidents in it. Your job is to learn how the system looks when it is healthy and write a factual profile that later investigators will read first.

## Rules (strict)
- Gather evidence ONLY through `uv run python -m rca --system {system} tool <name> <window> ...`, run from {root}, and ONLY on these healthy windows (no fault was labelled in them; brief blips may still occur):
  {windows}
- Do NOT use any case id ({prefix}-...). Do NOT read or grep anything under data/, runs/, profiles/, README.md, SETUP.md, docs/, tests/ or any .py source file, and do not run ad-hoc Python or SQL. Do not run `python -m rca` commands other than `tool`.
- Write exactly one file: {out}. Edit nothing else.

## Start
Run `uv run python -m rca --system {system} tool question {first}` to see the tools and the component list. Then explore however you see fit, across several of the windows (they differ in time of day and load). Budget: about 40–60 tool calls.

## What the profile must contain
Facts about the system only, each backed by what you observed (name the tool and window, give the numbers). Cover at least:
1. Topology: which components call which, how many replicas each layer has, traffic volume per edge, and how volume changes with time of day.
2. For every component: which data sources exist (metric families, log names, whether it appears in traces) and which do not.
3. What each component runs on, as far as the metric names and log formats reveal it (runtime, server software, datastore type), with the evidence.
4. Normal levels of the main resource and latency series per component type, especially levels that look alarming but are normal.
5. False alarms: what the statistical tools (baro, exclusive, traces) report as anomalous in these healthy windows — which series, which components, how high the scores go. Investigators must learn what noise looks like.
6. Anything else an investigator should know about the data: sampling intervals, gaps, units, series that are constant or mostly zero.
Do NOT write rules about which fault causes which symptom, and do not guess at fault types; the profile describes the healthy system only. Keep it compact: at most about 150 lines of Markdown.

## Final report
Reply with the file path, the number of tool calls you used, and 3–5 lines on what surprised you."""

# Added only when the user picks Vietnamese in Settings.
VIETNAMESE = """

## Language
Think, reason and write the profile in Vietnamese, and write your notes and final report in Vietnamese too. Keep in
their original form: proper names, technical terms (for example CPU, latency, packet loss, exclusive time, trace,
span, change point), component names, KPI and metric names, tool names, commands, numbers and units."""


def name(system: str) -> str:
    return system.lower()


def path(system: str, language: str = "en") -> Path:
    return ROOT / "profiles" / (f"{name(system)}.vi.md" if language == "vi" else f"{name(system)}.md")


WINDOW = re.compile(r"^normal-\d{4}_\d{2}_\d{2}-([01]\d|2[0-3])[0-5]\d$")


def suggested(data: Path, system: str, cases) -> list[str]:
    """The default windows of a system: the original ones for Bank and Telecom, else chosen from the data."""
    if system in DEFAULT_WINDOWS:
        return DEFAULT_WINDOWS[system]
    parquet = data / "parquet" / systems.get(system).path
    busy = [(c.start - 3600, c.end + 3600) for c in cases]
    candidates = []
    for day_dir in sorted(p for p in parquet.glob("*") if (p / "metric_app.parquet").exists()):
        lo, hi = duckdb.connect().execute("SELECT min(ts), max(ts) FROM read_parquet(?)",
                                          [str(day_dir / "metric_app.parquet")]).fetchone()
        if lo is None:
            continue
        # The tools need the hour before a window, so a window starts an hour after the data does.
        t = int(lo) + 3600
        t += (1800 - t % 1800) % 1800
        while t + 1800 <= hi:
            if all(t + 1800 <= a or t >= b for a, b in busy):
                candidates.append(t)
            t += 1800
    if not candidates:
        return []
    step = max(1, len(candidates) // 6)
    picked = candidates[::step][:6]
    return [datetime.fromtimestamp(t, CST).strftime("normal-%Y_%m_%d-%H%M") for t in picked]


def check(chosen: list[str], cases) -> None:
    for w in chosen:
        if not WINDOW.match(w):
            raise ValueError(f"{w!r} is not a window: write normal-YYYY_MM_DD-HHMM")
        start = int(datetime.strptime(w[7:], "%Y_%m_%d-%H%M").replace(tzinfo=CST).timestamp())
        if any(start < c.end + 3600 and start + 1800 > c.start - 3600 for c in cases):
            raise ValueError(f"{w} lies within an hour of a query window")


def instructions(system: str, chosen: list[str], cases, language: str = "en") -> tuple[str, Path]:
    if not chosen:
        raise ValueError(f"no healthy window given for {system}")
    check(chosen, cases)
    out = path(system, language)
    text = PROMPT.format(system=system, root=ROOT, windows=", ".join(chosen), prefix=systems.get(system).case_prefix,
                         out=out, first=chosen[0])
    return text + (VIETNAMESE if language == "vi" else ""), out
