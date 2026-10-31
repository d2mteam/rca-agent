from rca.cases import Case, Fault
from rca.scoring import Answer, difficulty, parse_official, score_case

# 2021-03-25 00:07:00 and 00:19:00 in China Standard Time.
MG02 = Fault(1616602020, "MG02", "network latency", "pod")
APACHE01 = Fault(1616602740, "apache01", "high disk I/O read usage", "pod")


def _case(asks, faults, task="task_7"):
    return Case("bank-104", task, "", frozenset(asks), 1616601600, 1616603400, tuple(faults))


def test_time_passes_within_sixty_seconds_only():
    case = _case({"time"}, [MG02], task="task_1")
    assert score_case(case, [Answer(time="2021-03-25 00:08:00")]).solved
    assert not score_case(case, [Answer(time="2021-03-25 00:08:01")]).solved
    assert not score_case(case, [Answer(time="25/03/2021 00:07")]).solved


def test_partial_score_counts_passed_fields():
    case = _case({"time", "component", "reason"}, [MG02])
    result = score_case(case, [Answer("2021-03-25 00:07:30", "MG02", "network packet loss")])
    assert result.score == 2 / 3
    assert not result.solved
    assert [f.field for f in result.fields if not f.passed] == ["reason"]


def test_answers_are_matched_to_faults_in_the_best_order():
    case = _case({"time", "component"}, [MG02, APACHE01])
    answers = [Answer("2021-03-25 00:19:00", "apache01"), Answer("2021-03-25 00:07:00", "MG02")]
    assert score_case(case, answers).solved


def test_wrong_number_of_answers_fails_every_field():
    case = _case({"time", "component"}, [MG02, APACHE01])
    result = score_case(case, [Answer("2021-03-25 00:07:00", "MG02")])
    assert result.score == 0
    assert len(result.fields) == 4


def test_parse_official_reads_keys_only_in_the_official_order():
    ordered = '{"1": {"root cause occurrence datetime": "2021-03-25 00:07:00", "root cause component": "MG02", "root cause reason": "network latency"}}'
    assert parse_official(ordered) == [Answer("2021-03-25 00:07:00", "MG02", "network latency")]

    reordered = '{"1": {"root cause component": "MG02", "root cause occurrence datetime": "2021-03-25 00:07:00"}}'
    case = _case({"time", "component"}, [MG02], task="task_5")
    assert score_case(case, parse_official(reordered)).score == 0


def test_difficulty_follows_task_number():
    assert [difficulty(f"task_{i}") for i in (1, 3, 4, 6, 7)] == ["easy", "easy", "middle", "middle", "hard"]
