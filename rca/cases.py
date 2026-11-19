"""OpenRCA cases: the query an agent answers and the faults that grade it."""

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# OpenRCA queries, records and telemetry folders use China Standard Time.
CST = timezone(timedelta(hours=8))

_DATE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (\d{4})")
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_POINT = re.compile(
    r"The (?:only|(?P<index>\d+)-th) (?:predicted )?root cause "
    r"(?:occurrence time is within 1 minutes \(i\.e\., <=1min\) of (?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
    r"|component is (?P<component>.+)"
    r"|reason is (?P<reason>.+))"
)


@dataclass(frozen=True)
class Fault:
    time: int
    component: str
    reason: str
    level: str


@dataclass(frozen=True)
class Case:
    case_id: str
    task: str
    instruction: str
    asks: frozenset[str]
    start: int
    end: int
    faults: tuple[Fault, ...]
    system: str = "Bank"  # OpenRCA system the case belongs to

    @property
    def day(self) -> str:
        """Telemetry folder holding the window, e.g. 2021_03_09."""
        return datetime.fromtimestamp(self.start, CST).strftime("%Y_%m_%d")


def parse_window(instruction: str) -> tuple[int, int]:
    """Return the query window as epoch seconds.

    A window such as "March 9, 2021, from 23:30 to March 10, 2021, 00:00"
    ends on the next day, which shows up as an end time before the start.
    """
    date = _DATE.search(instruction)
    times = _TIME.findall(instruction)
    if date is None or len(times) < 2:
        raise ValueError(f"no time window in query: {instruction[:120]!r}")
    day = datetime.strptime(f"{date[1]} {date[2]} {date[3]}", "%B %d %Y").replace(tzinfo=CST)
    start = day.replace(hour=int(times[0][0]), minute=int(times[0][1]))
    end = day.replace(hour=int(times[1][0]), minute=int(times[1][1]))
    if end <= start:
        end += timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def parse_scoring_points(points: str) -> list[dict[str, str]]:
    """Split scoring points into the fields graded for each fault, in order.

    "The only ..." grades a single fault; "The 2-th ..." grades the second one.
    """
    graded: dict[int, dict[str, str]] = {}
    for line in filter(None, (line.strip() for line in points.splitlines())):
        match = _POINT.fullmatch(line)
        if match is None:
            raise ValueError(f"unrecognised scoring point: {line!r}")
        fields = graded.setdefault(int(match["index"] or 1), {})
        for field in ("time", "component", "reason"):
            if match[field] is not None:
                fields[field] = match[field]
    return [graded[index] for index in sorted(graded)]


def load_cases(system_dir: Path) -> list[Case]:
    """Read query.csv and attach to each query the record.csv faults it grades.

    A query can grade several faults, so faults are found by matching the
    graded fields against records inside the query window, not by row number.
    """
    with open(system_dir / "query.csv", newline="") as f:
        queries = list(csv.DictReader(f))
    with open(system_dir / "record.csv", newline="") as f:
        records = list(csv.DictReader(f))

    prefix = system_dir.name.lower()
    cases = []
    for index, query in enumerate(queries, start=1):
        start, end = parse_window(query["instruction"])
        in_window = [r for r in records if start <= float(r["timestamp"]) < end]
        graded = parse_scoring_points(query["scoring_points"])
        faults = []
        for fields in graded:
            matches = [
                r
                for r in in_window
                if fields.get("time", r["datetime"]) == r["datetime"]
                and fields.get("component", r["component"]) == r["component"]
                and fields.get("reason", r["reason"]) == r["reason"]
            ]
            if len(matches) != 1:
                raise ValueError(f"query {index}: {len(matches)} records in window match {fields}")
            r = matches[0]
            faults.append(Fault(int(float(r["timestamp"])), r["component"], r["reason"], r["level"]))
        if len(set(faults)) != len(faults):
            raise ValueError(f"query {index}: two graded faults resolve to the same record")
        cases.append(
            Case(
                case_id=f"{prefix}-{index:03d}",
                task=query["task_index"],
                instruction=query["instruction"],
                asks=frozenset(graded[0]),
                start=start,
                end=end,
                faults=tuple(faults),
                system=system_dir.name,
            )
        )
    return cases
