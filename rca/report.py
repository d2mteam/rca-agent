"""Turn case scores into the tables printed by `python -m rca score`."""

from collections import defaultdict
from datetime import datetime

from rca.cases import CST
from rca.scoring import FIELDS, TIME_FORMAT, CaseScore, difficulty

LEVELS = ("easy", "middle", "hard")


def _pct(part: float, whole: int) -> str:
    return f"{part / whole:6.1%}" if whole else "     -"


def _time_outside_window(score: CaseScore) -> tuple[int, int]:
    """Answers whose time parses, and how many of them fall outside the window."""
    times = []
    for answer in score.answers:
        try:
            times.append(datetime.strptime(answer.time, TIME_FORMAT))
        except ValueError:
            continue
    # Answer times are naive strings in China Standard Time, like the queries.
    start = datetime.fromtimestamp(score.case.start, CST).replace(tzinfo=None)
    end = datetime.fromtimestamp(score.case.end, CST).replace(tzinfo=None)
    return len(times), sum(not start <= t < end for t in times)


def comparison(runs: dict[str, list[CaseScore]]) -> str:
    header = (
        f"{'run':<16}{'solved':>9}{'strict':>8}{'partial':>9} |"
        + "".join(f"{level:>8}" for level in LEVELS)
        + " |"
        + "".join(f"{field:>10}" for field in FIELDS)
        + " | time outside window"
    )
    lines = [header, "-" * len(header)]
    for name, scores in runs.items():
        solved = sum(s.solved for s in scores)
        by_level = defaultdict(list)
        for s in scores:
            by_level[difficulty(s.case.task)].append(s)
        graded = defaultdict(lambda: [0, 0])
        for s in scores:
            for f in s.fields:
                graded[f.field][0] += f.passed
                graded[f.field][1] += 1
        timed = [_time_outside_window(s) for s in scores]
        lines.append(
            f"{name:<16}{f'{solved}/{len(scores)}':>9}{_pct(solved, len(scores)):>8}"
            f"{_pct(sum(s.score for s in scores), len(scores)):>9} |"
            + "".join(f"{_pct(sum(s.solved for s in by_level[level]), len(by_level[level])):>8}" for level in LEVELS)
            + " |"
            + "".join(f"{_pct(*graded[field]):>10}" for field in FIELDS)
            + f" | {sum(o for _, o in timed)}/{sum(n for n, _ in timed)}"
        )
    return "\n".join(lines)


def by_fault_reason(scores: list[CaseScore]) -> str:
    """Faults whose every graded field passed, grouped by the true reason."""
    tally = defaultdict(lambda: [0, 0])
    for s in scores:
        for i, fault in enumerate(s.case.faults):
            tally[fault.reason][0] += all(f.passed for f in s.fields if f.fault == i)
            tally[fault.reason][1] += 1
    rows = sorted(tally.items(), key=lambda kv: -kv[1][1])
    return "\n".join(f"  {reason:<30}{ok:>3}/{n:<3}{_pct(ok, n)}" for reason, (ok, n) in rows)


def case_lines(scores: list[CaseScore]) -> str:
    lines = []
    for s in scores:
        marks = []
        for f in s.fields:
            prefix = f"#{f.fault + 1} " if len(s.case.faults) > 1 else ""
            if f.passed:
                marks.append(f"{prefix}{f.field} ok")
            else:
                marks.append(f"{prefix}{f.field} x (got {f.got or '-'!r}, want {f.expected!r})")
        lines.append(f"  {s.case.case_id} {s.case.task} {s.score:4.2f}  " + "; ".join(marks))
    return "\n".join(lines)
