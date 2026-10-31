"""Score answers the way OpenRCA's main/evaluate.py does.

Rules reproduced from the official scorer:
- an answer must list exactly as many root causes as the query has faults,
  otherwise every graded field fails;
- root causes are matched to faults by the permutation passing most fields;
- a time passes within 60 seconds; component and reason must match exactly;
- a case scores passed fields / graded fields and counts as solved only at
  1.0. Tasks 1-3 are easy, 4-6 middle and 7 hard.
"""

import itertools
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from rca.cases import CST, Case, Fault

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
TIME_TOLERANCE_SECONDS = 60
FIELDS = ("time", "component", "reason")

# Same pattern as the official scorer, so the keys must come in this order.
# Listed differently, the lazy groups swallow the following keys: component
# reads as 'MG02", "root cause occurrence datetime": "...' and time as empty.
_OFFICIAL_ANSWER = re.compile(
    r'{\s*'
    r'(?:"root cause occurrence datetime":\s*"(.*?)")?,?\s*'
    r'(?:"root cause component":\s*"(.*?)")?,?\s*'
    r'(?:"root cause reason":\s*"(.*?)")?\s*}'
)


@dataclass(frozen=True)
class Answer:
    time: str = ""
    component: str = ""
    reason: str = ""


@dataclass(frozen=True)
class FieldResult:
    fault: int
    field: str
    expected: str
    got: str
    passed: bool


@dataclass(frozen=True)
class CaseScore:
    case: Case
    answers: tuple[Answer, ...]
    fields: tuple[FieldResult, ...]

    @property
    def score(self) -> float:
        return sum(f.passed for f in self.fields) / len(self.fields)

    @property
    def solved(self) -> bool:
        return all(f.passed for f in self.fields)


def parse_official(prediction: str) -> list[Answer]:
    """Read root causes out of a free-text answer as the official scorer does."""
    return [Answer(*match) for match in _OFFICIAL_ANSWER.findall(prediction)]


def difficulty(task: str) -> str:
    number = int(task.split("_")[1])
    return "easy" if number <= 3 else "middle" if number <= 6 else "hard"


def expected_value(fault: Fault, field: str) -> str:
    if field == "time":
        return datetime.fromtimestamp(fault.time, CST).strftime(TIME_FORMAT)
    return getattr(fault, field)


def field_passes(field: str, expected: str, got: str) -> bool:
    if field != "time":
        return got == expected
    try:
        delta = datetime.strptime(got, TIME_FORMAT) - datetime.strptime(expected, TIME_FORMAT)
    except ValueError:
        return False
    return abs(delta.total_seconds()) <= TIME_TOLERANCE_SECONDS


def score_case(case: Case, answers: list[Answer]) -> CaseScore:
    graded = [field for field in FIELDS if field in case.asks]

    def check(order: tuple[Answer, ...] | None) -> tuple[FieldResult, ...]:
        results = []
        for i, fault in enumerate(case.faults):
            for field in graded:
                expected = expected_value(fault, field)
                got = getattr(order[i], field) if order else ""
                results.append(FieldResult(i, field, expected, got, order is not None and field_passes(field, expected, got)))
        return tuple(results)

    if len(answers) != len(case.faults):
        return CaseScore(case, tuple(answers), check(None))
    best = max((check(order) for order in itertools.permutations(answers)), key=lambda r: sum(f.passed for f in r))
    return CaseScore(case, tuple(answers), best)


def write_predictions(path: Path, predictions: dict[str, list[Answer]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for case_id, answers in predictions.items():
            f.write(json.dumps({"case_id": case_id, "answers": [asdict(a) for a in answers]}, ensure_ascii=False) + "\n")


def load_predictions(path: Path) -> dict[str, list[Answer]]:
    predictions = {}
    with open(path) as f:
        for line in filter(str.strip, f):
            row = json.loads(line)
            predictions[row["case_id"]] = [Answer(**a) for a in row["answers"]]
    return predictions
