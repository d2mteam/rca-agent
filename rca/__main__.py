"""Command line: prepare data, list cases, write baseline answers, investigate a case, score."""

import argparse
import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from rca import baselines, hypotheses, report, statistical, systems, tools
from rca.cases import CST, Case, load_cases
from rca.scoring import load_predictions, score_case, write_predictions
from rca.telemetry import connect, prepare_day, window_counts

# Bank days whose queries form the evaluation set; 2021_03_05 has no query and serves as the normal-day
# baseline. Other systems default to every day.
EVAL_DAYS = {"Bank": ["2021_03_09", "2021_03_25"]}


def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(ts, CST).strftime("%m-%d %H:%M")


def _cases(args: argparse.Namespace):
    cases = load_cases(args.data / "openrca" / args.system)
    days = args.days if args.days is not None else EVAL_DAYS.get(args.system)
    return [c for c in cases if c.day in days] if days else cases


def _connect(args: argparse.Namespace, system: str | None = None):
    system = system or args.system
    return connect(args.data / "parquet" / system, system=system)


def cmd_prepare(args: argparse.Namespace) -> None:
    telemetry_dir = args.data / "openrca" / args.system / "telemetry"
    days = args.days or sorted(p.name for p in telemetry_dir.iterdir() if p.is_dir())
    for day in days:
        counts = prepare_day(telemetry_dir, args.data / "parquet" / args.system, day, system=args.system)
        print(day, " ".join(f"{name}={n:,}" for name, n in counts.items()), flush=True)


def cmd_cases(args: argparse.Namespace) -> None:
    cases = _cases(args)
    con = _connect(args) if args.counts else None
    for c in cases:
        faults = "; ".join(f"{_fmt(f.time)[6:]} {f.component} {f.reason}" for f in c.faults)
        line = f"{c.case_id} {c.task} asks={'+'.join(sorted(c.asks)):<22} window={_fmt(c.start)}-{_fmt(c.end)[6:]}  {faults}"
        if con is not None:
            counts = window_counts(con, c.start, c.end)
            line += "  " + " ".join(f"{name}={n:,}" for name, n in counts.items())
        print(line)
    print(f"{len(cases)} cases")


def cmd_baseline(args: argparse.Namespace) -> None:
    cases = _cases(args)
    run_name = args.name
    if args.system != "Bank" and args.name not in ("oracle", "random"):
        raise SystemExit(f"{args.name} maps Bank KPI names to reasons; on {args.system} only oracle and random run")
    if args.name == "oracle":
        predictions = baselines.oracle(cases)
    elif args.name == "random":
        predictions = baselines.random_guess(cases, args.seed)
    elif args.name == "openrca-agent":
        if args.archive is None:
            raise SystemExit("openrca-agent needs --archive path/to/OpenRCA/rca/archive/agent-Bank.csv")
        predictions = baselines.openrca_agent(cases, args.archive)
    elif args.name == "baro":
        from rca import baro  # optional dependency group "baro"

        con = _connect(args)
        predictions = {c.case_id: baro.answer(con, c) for c in cases}
    else:
        run_name = f"{args.name}-{args.reference}"
        con = _connect(args)
        predictions, top = {}, {}
        for c in cases:
            findings = statistical.rank_findings(con, c, args.name, args.reference)
            predictions[c.case_id] = statistical.answer(c, findings)
            top[c.case_id] = findings[:5]
        (args.runs / run_name).mkdir(parents=True, exist_ok=True)
        with open(args.runs / run_name / "findings.jsonl", "w") as f:
            for case_id, findings in top.items():
                f.write(json.dumps({"case_id": case_id, "top": [asdict(x) for x in findings]}) + "\n")
    out = args.runs / run_name / "predictions.jsonl"
    write_predictions(out, predictions)
    print(f"{len(predictions)} answers -> {out}")


def cmd_score(args: argparse.Namespace) -> None:
    cases = _cases(args)
    if args.cases:
        cases = [c for c in cases if c.case_id in args.cases]
    runs = {}
    for path in args.predictions:
        predictions = load_predictions(path)
        missing = [c.case_id for c in cases if c.case_id not in predictions]
        if missing:
            print(f"{path}: no answer for {len(missing)} cases, scored 0: {' '.join(missing)}")
        runs[path.parent.name] = [score_case(c, predictions.get(c.case_id, [])) for c in cases]

    days = args.days if args.days is not None else EVAL_DAYS.get(args.system)
    print(f"{len(cases)} cases from {', '.join(days) if days else 'all days'}\n")
    print(report.comparison(runs))
    if args.detail:
        for name, scores in runs.items():
            print(f"\n== {name}: faults fully answered, by true reason")
            print(report.by_fault_reason(scores))
            print(f"\n== {name}: per case")
            print(report.case_lines(scores))


def _system_of(case_id: str, default: str) -> str:
    """The system a case id names (telecom-023 is Telecom's), else the --system option."""
    prefix = case_id.split("-", 1)[0]
    return next((name for name in systems.SYSTEMS if name.lower() == prefix), default)


def _normal_window(case_id: str, cases, system: str) -> Case | None:
    """normal-YYYY_MM_DD-HHMM: 30 minutes with no query, at least an hour away from every query window."""
    match = re.fullmatch(r"normal-(\d{4}_\d{2}_\d{2})-([01]\d|2[0-3])([0-5]\d)", case_id)
    if not match:
        return None
    day, hour, minute = match.groups()
    start = int(datetime.strptime(day, "%Y_%m_%d").replace(hour=int(hour), minute=int(minute), tzinfo=CST).timestamp())
    end = start + 1800
    if any(start < c.end + 3600 and end > c.start - 3600 for c in cases):
        raise SystemExit(f"{case_id} lies within an hour of a query window; pick a normal window farther away")
    return Case(case_id, "", "", frozenset(), start, end, (), system)


def cmd_tool(args: argparse.Namespace) -> None:
    system = _system_of(args.case_id, args.system)
    cases = load_cases(args.data / "openrca" / system)
    case = _normal_window(args.case_id, cases, system) or next((c for c in cases if c.case_id == args.case_id), None)
    if case is None:
        raise SystemExit(f"no case {args.case_id}")
    component = getattr(args, "component", None)
    if component is not None and args.tool not in ("hypothesis", "submit") and component not in systems.get(system).components:
        raise SystemExit(f"unknown component {component!r}; choose from {', '.join(systems.get(system).components)}")
    if args.tool == "submit" and not case.faults:
        raise SystemExit(f"{args.case_id} is a normal window; there is nothing to submit")
    if args.tool == "submit":
        tools.submit(case, args.runs / args.run / "answers", args.time, args.component, args.reason, args.append,
                     args.protocol)
        return
    if args.tool == "hypothesis":
        run_dir = args.runs / args.run
        if args.action == "add":
            entry = hypotheses.add(run_dir, case.case_id, args.component, args.reason, args.if_true, args.if_false,
                                   system=case.system)
            print(f"hypothesis #{entry['id']} added")
        elif args.action == "test":
            hypotheses.test(run_dir, case.case_id, args.id, args.status, args.evidence)
        print(hypotheses.describe(hypotheses.load(run_dir, case.case_id)))
        return
    con = _connect(args, system)
    if args.tool == "question":
        tools.question(con, case, args.protocol)
    elif args.tool == "overview":
        tools.overview(con, case)
    elif args.tool == "calls":
        tools.calls(con, case)
    elif args.tool == "baro":
        tools.baro_ranking(con, case, args.component, args.top)
    elif args.tool == "exclusive":
        tools.exclusive(con, case)
    elif args.tool == "onset":
        tools.onsets(con, case, args.component, args.pattern, args.top)
    elif args.tool == "series":
        tools.series(con, case, args.component, args.kpi)
    elif args.tool == "traces":
        tools.traces(con, case)
    else:
        tools.logs(con, case, args.component, args.grep, args.samples)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m rca")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--system", default="Bank")
    parser.add_argument("--runs", type=Path, default=Path("runs"))
    sub = parser.add_subparsers(required=True)

    prepare = sub.add_parser("prepare", help="convert telemetry CSV of the given days to Parquet")
    prepare.add_argument("days", nargs="*", help="e.g. 2021_03_09; default: every extracted day")
    prepare.set_defaults(func=cmd_prepare)

    cases = sub.add_parser("cases", help="list cases, optionally with telemetry rows per window")
    cases.add_argument("days", nargs="*", help="only cases whose window starts on these days")
    cases.add_argument("--counts", action="store_true", help="count rows per source in each window")
    cases.set_defaults(func=cmd_cases)

    baseline = sub.add_parser("baseline", help="write answers of a reference baseline to runs/<name>/")
    baseline.add_argument("name", choices=["oracle", "random", "openrca-agent", "zscore", "iqr", "baro"])
    baseline.add_argument("--reference", choices=["before", "normal-day"], default="before",
                          help="zscore/iqr: reference period for the normal level")
    baseline.add_argument("--days", nargs="*", help="default: Bank's evaluation days, every day elsewhere")
    baseline.add_argument("--seed", type=int, default=0)
    baseline.add_argument("--archive", type=Path, help="OpenRCA rca/archive/agent-Bank.csv")
    baseline.set_defaults(func=cmd_baseline)

    score = sub.add_parser("score", help="score answer files the way OpenRCA does")
    score.add_argument("predictions", nargs="+", type=Path, help="runs/<name>/predictions.jsonl")
    score.add_argument("--days", nargs="*", help="default: Bank's evaluation days, every day elsewhere")
    score.add_argument("--cases", nargs="*", help="only these case ids, e.g. bank-024")
    score.add_argument("--detail", action="store_true", help="add per-reason and per-case breakdowns")
    score.set_defaults(func=cmd_score)

    tool = sub.add_parser("tool", help="investigation tools for an agent working on one case")
    tool.set_defaults(func=cmd_tool)
    tool.add_argument("--protocol", choices=["hypotheses", "free"], default="hypotheses",
                      help="hypotheses: answers must follow a tested hypothesis; free: no protocol")
    kinds = tool.add_subparsers(dest="tool", required=True)
    for name in ("question", "overview", "calls", "exclusive", "traces"):
        kinds.add_parser(name).add_argument("case_id")
    p = kinds.add_parser("baro")
    p.add_argument("case_id")
    p.add_argument("--component")
    p.add_argument("--top", type=int, default=20)
    p = kinds.add_parser("onset")
    p.add_argument("case_id")
    p.add_argument("component")
    p.add_argument("--pattern", help="only series whose name contains this text")
    p.add_argument("--top", type=int, default=25)
    p = kinds.add_parser("series")
    p.add_argument("case_id")
    p.add_argument("component")
    p.add_argument("kpi", help="substring of the KPI name; \"\" lists every KPI")
    p = kinds.add_parser("logs")
    p.add_argument("case_id")
    p.add_argument("component")
    p.add_argument("--grep", help="only lines containing this text")
    p.add_argument("--samples", type=int, default=10)
    p = kinds.add_parser("hypothesis")
    p.add_argument("case_id")
    p.add_argument("--run", default="agent-tools")
    actions = p.add_subparsers(dest="action", required=True)
    run = argparse.ArgumentParser(add_help=False)
    run.add_argument("--run", default=argparse.SUPPRESS, help="run name, as given to submit")
    a = actions.add_parser("add", parents=[run])
    a.add_argument("--component", required=True)
    a.add_argument("--reason", required=True)
    a.add_argument("--if-true", required=True, help="what you expect to observe if this is the cause")
    a.add_argument("--if-false", required=True, help="what you expect to observe if it is not")
    a = actions.add_parser("test", parents=[run])
    a.add_argument("id", type=int)
    a.add_argument("--status", required=True, choices=hypotheses.STATUSES)
    a.add_argument("--evidence", required=True, help="which tool output and numbers")
    actions.add_parser("list", parents=[run])
    p = kinds.add_parser("submit")
    p.add_argument("case_id")
    p.add_argument("--time", required=True, help="YYYY-MM-DD HH:MM:SS")
    p.add_argument("--component", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--append", action="store_true", help="add to earlier answers (queries with several failures)")
    p.add_argument("--run", default="agent-tools")

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
