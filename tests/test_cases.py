import csv
from datetime import datetime

import pytest

from rca.cases import CST, Fault, load_cases, parse_scoring_points, parse_window


def _ts(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=CST).timestamp())


def test_parse_window_same_day():
    start, end = parse_window("On March 4, 2021, within the time range of 14:30 to 15:00, a single failure ...")
    assert (start, end) == (_ts("2021-03-04 14:30"), _ts("2021-03-04 15:00"))


def test_parse_window_crossing_midnight_ends_next_day():
    start, end = parse_window("During the specified time range of March 9, 2021, from 23:30 to March 10, 2021, 00:00, ...")
    assert (start, end) == (_ts("2021-03-09 23:30"), _ts("2021-03-10 00:00"))


def test_parse_window_rejects_query_without_window():
    with pytest.raises(ValueError):
        parse_window("Something failed on March 4, 2021.")


def test_parse_scoring_points_groups_fields_per_fault():
    points = (
        "The 1-th root cause occurrence time is within 1 minutes (i.e., <=1min) of 2021-03-25 00:07:00\n"
        "The 1-th predicted root cause component is MG02\n"
        "The 2-th root cause occurrence time is within 1 minutes (i.e., <=1min) of 2021-03-25 00:19:00\n"
        "The 2-th predicted root cause component is apache01\n"
    )
    assert parse_scoring_points(points) == [
        {"time": "2021-03-25 00:07:00", "component": "MG02"},
        {"time": "2021-03-25 00:19:00", "component": "apache01"},
    ]


def test_parse_scoring_points_rejects_unknown_line():
    with pytest.raises(ValueError, match="unrecognised"):
        parse_scoring_points("The only predicted root cause service is MG02")


RECORDS = [
    ["pod", "Redis02", "1614852540.0", "2021-03-04 18:09:00", "high memory usage"],
    ["node", "IG01", "1614852900.0", "2021-03-04 18:15:00", "network latency"],
    ["pod", "Mysql02", "1614841020.0", "2021-03-04 14:57:00", "high memory usage"],
]


def _system(tmp_path, instruction, scoring_points):
    system = tmp_path / "Bank"
    system.mkdir()
    with open(system / "query.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["task_index", "instruction", "scoring_points"])
        writer.writerow(["task_6", instruction, scoring_points])
    with open(system / "record.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["level", "component", "timestamp", "datetime", "reason"])
        writer.writerows(RECORDS)
    return system


def test_load_cases_finds_graded_record_in_window(tmp_path):
    points = "The only predicted root cause component is Redis02\nThe only predicted root cause reason is high memory usage\n"
    [case] = load_cases(_system(tmp_path, "On March 4, 2021, between 18:00 and 18:30, one failure ...", points))
    assert case.case_id == "bank-001"
    assert case.asks == {"component", "reason"}
    assert case.faults == (Fault(1614852540, "Redis02", "high memory usage", "pod"),)
    assert case.day == "2021_03_04"


def test_load_cases_keeps_every_fault_of_a_multi_failure_query(tmp_path):
    points = (
        "The 1-th predicted root cause reason is high memory usage\n"
        "The 2-th predicted root cause reason is network latency\n"
    )
    [case] = load_cases(_system(tmp_path, "On March 4, 2021, between 18:00 and 18:30, two failures ...", points))
    assert case.asks == {"reason"}
    assert [f.component for f in case.faults] == ["Redis02", "IG01"]


def test_load_cases_rejects_points_with_no_record_in_window(tmp_path):
    # Mysql02 has a matching record, but at 14:57, outside this window.
    points = "The only predicted root cause component is Mysql02\n"
    with pytest.raises(ValueError, match="query 1: 0 records"):
        load_cases(_system(tmp_path, "On March 4, 2021, between 18:00 and 18:30, one failure ...", points))
