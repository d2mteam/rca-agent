"""Reference answers that need no model, to show what a score means.

- oracle: the graded answers themselves; must score 100%, or the scorer is wrong.
- random: a uniform guess over the window minutes and the answer space; chance level.
- openrca-agent: OpenRCA's published RCA-agent answers (rca/archive/agent-Bank.csv).
"""

import csv
import random
from datetime import datetime
from pathlib import Path

from rca import systems
from rca.cases import CST, Case
from rca.scoring import FIELDS, TIME_FORMAT, Answer, expected_value, parse_official


def oracle(cases: list[Case]) -> dict[str, list[Answer]]:
    return {c.case_id: [Answer(*(expected_value(f, field) for field in FIELDS)) for f in c.faults] for c in cases}


def random_guess(cases: list[Case], seed: int = 0) -> dict[str, list[Answer]]:
    """One guess per fault: the query text states how many faults there are."""
    rng = random.Random(seed)
    predictions = {}
    for c in cases:
        minutes = range(c.start, c.end, 60)
        space = systems.get(c.system)
        predictions[c.case_id] = [
            Answer(
                datetime.fromtimestamp(rng.choice(minutes), CST).strftime(TIME_FORMAT),
                rng.choice(space.components),
                rng.choice(space.reasons),
            )
            for _ in c.faults
        ]
    return predictions


def openrca_agent(cases: list[Case], archive: Path) -> dict[str, list[Answer]]:
    """Answers from OpenRCA's archive, whose row_id is the 0-based query row."""
    with open(archive, newline="") as f:
        by_row = {int(float(row["row_id"])) + 1: parse_official(row["prediction"]) for row in csv.DictReader(f)}
    return {c.case_id: by_row[int(c.case_id.rsplit("-", 1)[1])] for c in cases}
