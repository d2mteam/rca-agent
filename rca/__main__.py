"""Command line: prepare data, list cases, write baseline answers, score answers."""

import argparse
import json
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from rca import bank, baselines, report, statistical, tools
from rca.cases import CST, load_cases
from rca.scoring import load_predictions, score_case, write_predictions
from rca.telemetry import connect, prepare_day, window_counts

# Days whose queries form the evaluation set; 2021_03_05 has no query and serves as the normal-day baseline.
EVAL_DAYS = ["2021_03_09", "2021_03_25"]


def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(ts, CST).strftime("%m-%d %H:%M")


def _cases(args: argparse.Namespace):
    cases = load_cases(args.data / "openrca" / args.system)
    return [c for c in cases if c.day in args.days] if args.days else cases


def cmd_prepare(args: argparse.Namespace) -> None:
    telemetry_dir = args.data / "openrca" / args.system / "telemetry"
    days = args.days or sorted(p.name for p in telemetry_dir.iterdir() if p.is_dir())
    for day in days:
        counts = prepare_day(telemetry_dir, args.data / "parquet" / args.system, day)
        print(day, " ".join(f"{name}={n:,}" for name, n in counts.items()), flush=True)


def cmd_cases(args: argparse.Namespace) -> None:
    cases = _cases(args)
    con = connect(args.data / "parquet" / args.system) if args.counts else None
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
    if args.name == "oracle":
        predictions = baselines.oracle(cases)
    elif args.name == "random":
        predictions = baselines.random_guess(cases, args.seed)
    elif args.name == "openrca-agent":
        if args.archive is None:
            raise SystemExit("openrca-agent needs --archive path/to/OpenRCA/rca/archive/agent-Bank.csv")
        predictions = baselines.openrca_agent(cases, args.archive)
    elif args.name in ("baro", "msbaro"):
        from rca import baro  # optional dependency group "baro"

        con = connect(args.data / "parquet" / args.system)
        predictions = {c.case_id: baro.answer(con, c, multi_source=args.name == "msbaro") for c in cases}
    elif args.name == "torai":
        from rca import torai  # optional dependency group "baro" (scikit-learn, scipy)

        con = connect(args.data / "parquet" / args.system)
        predictions = {c.case_id: torai.answer(con, c) for c in cases}
    else:
        run_name = f"{args.name}-{args.reference}"
        con = connect(args.data / "parquet" / args.system)
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

    print(f"{len(cases)} cases from {', '.join(args.days) if args.days else 'all days'}\n")
    print(report.comparison(runs))
    if args.detail:
        for name, scores in runs.items():
            print(f"\n== {name}: faults fully answered, by true reason")
            print(report.by_fault_reason(scores))
            print(f"\n== {name}: per case")
            print(report.case_lines(scores))


def cmd_agent(args: argparse.Namespace) -> None:
    from rca import ollama_agent

    con = connect(args.data / "parquet" / args.system)
    run_dir = args.runs / (args.run or f"ollama-{args.model.replace(':', '_').replace('/', '_')}")
    cases = [c for c in _cases(args) if not args.cases or c.case_id in args.cases]
    for c in cases:
        started = datetime.now()
        try:
            calls = ollama_agent.solve(con, c, run_dir, args.model, args.url, args.max_steps, args.num_ctx)
        except OSError as error:  # a timed-out or refused model call loses this case only
            print(f"{c.case_id}: failed ({error})", flush=True)
            continue
        print(f"{c.case_id}: {calls} tool calls, {(datetime.now() - started).seconds}s", flush=True)
    print(f"answers -> {run_dir / 'predictions.jsonl'}")


def cmd_workflow(args: argparse.Namespace) -> None:
    from rca import workflow  # needs the optional group "baro"

    con = connect(args.data / "parquet" / args.system)
    prefix = "workflow-msbaro" if args.multi_source else "workflow"
    run_dir = args.runs / (args.run or f"{prefix}-{args.model.replace(':', '_').replace('/', '_')}")
    (run_dir / "reports").mkdir(parents=True, exist_ok=True)
    predictions = load_predictions(run_dir / "predictions.jsonl") if (run_dir / "predictions.jsonl").exists() else {}
    for c in [c for c in _cases(args) if not args.cases or c.case_id in args.cases]:
        started = datetime.now()
        try:
            predictions[c.case_id], report_ = workflow.run(con, c, args.model, args.url, multi_source=args.multi_source)
        except OSError as error:  # a timed-out or refused model call loses this case only
            print(f"{c.case_id}: failed ({error})", flush=True)
            continue
        (run_dir / "reports" / f"{c.case_id}.json").write_text(json.dumps(asdict(report_), indent=1, ensure_ascii=False))
        write_predictions(run_dir / "predictions.jsonl", predictions)
        print(f"{c.case_id}: {len(report_.attempts)} attempt(s), {(datetime.now() - started).seconds}s", flush=True)
    print(f"answers -> {run_dir / 'predictions.jsonl'}")


def cmd_codex(args: argparse.Namespace) -> None:
    from rca import codex_agent

    name = args.run or "codex-" + ("oss-" if args.oss else "") + (args.model or "default").replace(":", "_").replace("/", "_")
    run_dir = args.runs / name
    for c in [c for c in _cases(args) if not args.cases or c.case_id in args.cases]:
        started = datetime.now()
        try:
            calls, violations, code = codex_agent.solve(c.case_id, Path.cwd(), run_dir, args.model, args.oss, args.effort)
        except subprocess.TimeoutExpired:
            print(f"{c.case_id}: timed out", flush=True)
            continue
        flag = f"  RULE BREAKS: {violations}" if violations else ""
        print(f"{c.case_id}: exit {code}, {calls} commands, {(datetime.now() - started).seconds}s{flag}", flush=True)
    print(f"answers -> {run_dir / 'predictions.jsonl'}")


def cmd_flow(args: argparse.Namespace) -> None:
    """Workflow steps for an agent that is the deciding LLM: print the prompt, or verify and record answers."""
    from rca import workflow  # needs the optional group "baro"

    case = next((c for c in load_cases(args.data / "openrca" / args.system) if c.case_id == args.case_id), None)
    if case is None:
        raise SystemExit(f"no case {args.case_id}")
    con = connect(args.data / "parquet" / args.system)
    prompt, evidence_of, drops, report_ = workflow.build(con, case, args.multi_source)
    if args.step == "prompt":
        print(prompt)
        print(f'\nAnswer with: python -m rca flow submit {case.case_id} --answers \'<JSON>\'  where JSON is '
              f'{{"answers": [{len(case.faults)} x {{"component", "reason", "onset_minute": "HH:MM", "why"}}], "confidence"}}')
        return
    run_dir = args.runs / args.run
    parsed = json.loads(args.answers)
    picks = parsed["answers"][:len(case.faults)]
    wrong = [f"{p.get('component')!r} / {p.get('reason')!r}" for p in picks
             if p.get("component") not in bank.COMPONENTS or p.get("reason") not in bank.REASONS]
    if wrong:
        raise SystemExit(f"Not in the allowed lists: {', '.join(wrong)}. Use the exact component and reason strings "
                         "from the prompt; this submission was not counted.")
    problems = workflow.verify(case, picks, evidence_of, drops)
    (run_dir / "attempts").mkdir(parents=True, exist_ok=True)
    with open(run_dir / "attempts" / f"{case.case_id}.jsonl", "a") as log:
        log.write(json.dumps({"answer": parsed, "problems": problems, "final": args.final}) + "\n")
    if problems and not args.final:
        print("Verification failed:\n- " + "\n- ".join(problems) + "\nRe-read the evidence and submit again "
              "(add --final on your last try).")
        return
    path = run_dir / "predictions.jsonl"
    predictions = load_predictions(path) if path.exists() else {}
    predictions[case.case_id] = workflow.answers_of(case, picks)
    write_predictions(path, predictions)
    print(f"{case.case_id}: recorded" + (" (unverified)" if problems else ""))


def cmd_tool(args: argparse.Namespace) -> None:
    case = next((c for c in load_cases(args.data / "openrca" / args.system) if c.case_id == args.case_id), None)
    if case is None:
        raise SystemExit(f"no case {args.case_id}")
    if args.tool == "submit":
        tools.submit(case, args.runs / args.run / "answers", args.time, args.component, args.reason, args.append)
        return
    con = connect(args.data / "parquet" / args.system)
    if args.tool == "question":
        tools.question(con, case)
    elif args.tool == "overview":
        tools.overview(con, case)
    elif args.tool == "anomalies":
        tools.anomalies(con, case, args.component, args.top)
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
    baseline.add_argument("name", choices=["oracle", "random", "openrca-agent", "zscore", "iqr", "baro", "msbaro", "torai"])
    baseline.add_argument("--reference", choices=["before", "normal-day"], default="before",
                          help="zscore/iqr: reference period for the normal level")
    baseline.add_argument("--days", nargs="*", default=EVAL_DAYS)
    baseline.add_argument("--seed", type=int, default=0)
    baseline.add_argument("--archive", type=Path, help="OpenRCA rca/archive/agent-Bank.csv")
    baseline.set_defaults(func=cmd_baseline)

    score = sub.add_parser("score", help="score answer files the way OpenRCA does")
    score.add_argument("predictions", nargs="+", type=Path, help="runs/<name>/predictions.jsonl")
    score.add_argument("--days", nargs="*", default=EVAL_DAYS)
    score.add_argument("--cases", nargs="*", help="only these case ids, e.g. bank-024")
    score.add_argument("--detail", action="store_true", help="add per-reason and per-case breakdowns")
    score.set_defaults(func=cmd_score)

    agent = sub.add_parser("agent", help="run the tool-calling agent on a local Ollama model")
    agent.add_argument("--model", default="qwen3:4b-instruct-2507-q4_K_M")
    agent.add_argument("--url", default="http://localhost:11434")
    agent.add_argument("--cases", nargs="*", help="only these case ids")
    agent.add_argument("--days", nargs="*", default=EVAL_DAYS)
    agent.add_argument("--run", help="run name; default ollama-<model>")
    agent.add_argument("--max-steps", type=int, default=14)
    agent.add_argument("--num-ctx", type=int, default=16384)
    agent.set_defaults(func=cmd_agent)

    flow = sub.add_parser("workflow", help="code-led workflow: BARO + graph tools, a local Ollama model decides")
    flow.add_argument("--model", default="qwen3:4b-instruct-2507-q4_K_M")
    flow.add_argument("--url", default="http://localhost:11434")
    flow.add_argument("--cases", nargs="*", help="only these case ids")
    flow.add_argument("--days", nargs="*", default=EVAL_DAYS)
    flow.add_argument("--run", help="run name; default workflow[-msbaro]-<model>")
    flow.add_argument("--multi-source", action="store_true", help="tool 1 is multi-source BARO (metrics + log lines + trace spans)")
    flow.set_defaults(func=cmd_workflow)

    codex = sub.add_parser("codex", help="run the free tool-using agent through the Codex CLI, one case at a time")
    codex.add_argument("--model", help="Codex model, e.g. gpt-6-luna; with --oss an Ollama model")
    codex.add_argument("--oss", action="store_true", help="use a local Ollama model through Codex")
    codex.add_argument("--effort", help="model_reasoning_effort, e.g. low, medium, high")
    codex.add_argument("--cases", nargs="*", help="only these case ids")
    codex.add_argument("--days", nargs="*", default=EVAL_DAYS)
    codex.add_argument("--run", help="run name; default codex-<model>")
    codex.set_defaults(func=cmd_codex)

    flow = sub.add_parser("flow", help="workflow steps for an agent acting as the deciding LLM")
    flow.set_defaults(func=cmd_flow)
    steps = flow.add_subparsers(dest="step", required=True)
    p = steps.add_parser("prompt")
    p.add_argument("case_id")
    p.add_argument("--multi-source", action="store_true")
    p = steps.add_parser("submit")
    p.add_argument("case_id")
    p.add_argument("--answers", required=True, help="the JSON answer")
    p.add_argument("--final", action="store_true", help="record even if verification fails")
    p.add_argument("--multi-source", action="store_true")
    p.add_argument("--run", default="workflow-sonnet")

    tool = sub.add_parser("tool", help="investigation tools for an agent working on one case")
    tool.set_defaults(func=cmd_tool)
    kinds = tool.add_subparsers(dest="tool", required=True)
    for name in ("question", "overview", "traces"):
        kinds.add_parser(name).add_argument("case_id")
    p = kinds.add_parser("anomalies")
    p.add_argument("case_id")
    p.add_argument("--component", choices=bank.COMPONENTS)
    p.add_argument("--top", type=int, default=15)
    p = kinds.add_parser("series")
    p.add_argument("case_id")
    p.add_argument("component", choices=bank.COMPONENTS)
    p.add_argument("kpi", help="substring of the KPI name, e.g. CPU or HeapMemory")
    p = kinds.add_parser("logs")
    p.add_argument("case_id")
    p.add_argument("component", choices=bank.COMPONENTS)
    p.add_argument("--grep", help="only lines containing this text")
    p.add_argument("--samples", type=int, default=10)
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
