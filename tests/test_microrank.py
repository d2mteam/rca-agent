from fractions import Fraction

import numpy as np
import pytest

from rca import microrank

ORDER = ["front", "recommend", "checkout", "product"]


def chain(*ops, prefix="s"):
    """A trace whose spans call each other in a line, the first being the root."""
    ids = [f"{prefix}{i}" for i in range(len(ops))]
    return ids, [ids[0]] + ids[:-1], list(ops)


# The abnormal requests of Fig. 3 in the paper: Req-1, Req-2 and Req-4.
FIG3 = [chain("front", "product"), chain("front", "recommend", "product"), chain("front", "checkout", "product")]


def test_transition_blocks_match_the_paper_example():
    operations, a_oo, a_ot, a_to = microrank.transition(FIG3)
    ix = [operations.index(o) for o in ORDER]
    third = 1 / 3
    assert np.allclose(a_oo[np.ix_(ix, ix)], [[0, 0, 0, 0], [third, 0, 0, 0], [third, 0, 0, 0], [third, 1, 1, 0]])
    assert np.allclose(a_ot[ix], [[1 / 2, third, third], [0, third, 0], [0, 0, third], [1 / 2, third, third]])
    assert np.allclose(a_to[:, ix], [[third, 0, 0, third], [third, 1, 0, third], [third, 0, 1, third]])


def test_abnormal_preference_matches_the_paper_example():
    u = microrank.preference(FIG3, abnormal=True)
    assert [Fraction(x).limit_denominator(100) for x in u] == [Fraction(8, 21), Fraction(13, 42), Fraction(13, 42)]


def test_normal_preference_weighs_rarer_kinds_only():
    traces = [chain("front", "product"), chain("front", "product", prefix="t"), chain("front", "recommend")]
    assert np.allclose(microrank.preference(traces, abnormal=False), [1 / 4, 1 / 4, 1 / 2])


def test_pagerank_reproduces_the_paper_example():
    scores = microrank.pagerank(FIG3, abnormal=True)
    assert [scores[o] for o in ORDER] == pytest.approx([0.990, 0.328, 0.328, 1.0], abs=5e-4)


def test_coverage_tree_ignores_span_ids_and_sibling_order():
    one = (["a", "b", "c"], ["a", "a", "a"], ["front", "product", "recommend"])
    other = (["z", "y", "x"], ["z", "z", "z"], ["front", "recommend", "product"])
    assert microrank.coverage_tree(one) == microrank.coverage_tree(other)


def test_coverage_tree_tells_apart_shapes_with_the_same_operations():
    flat = (["a", "b", "c"], ["a", "a", "a"], ["front", "recommend", "product"])
    deep = chain("front", "recommend", "product")
    assert microrank.coverage_tree(flat) != microrank.coverage_tree(deep)


def test_span_with_a_missing_parent_is_a_root():
    orphan = (["a", "b"], ["a", "gone"], ["front", "product"])
    assert microrank.coverage_tree(orphan) == (("front", ()), ("product", ()))


def served(n: int, latency: float) -> list:
    """n reference traces IG01 -> Tomcat01, the call reaching its callee."""
    return [(latency, (["r", "c", "s"], ["r", "r", "c"], ["IG01:serve", "IG01:call", "Tomcat01:serve"])) for _ in range(n)]


def test_slower_than_p99_of_its_kind_is_abnormal():
    reference = served(100, 90.0) + served(100, 100.0)  # p99 = 100 ms
    fast, slow = served(1, 100.0)[0], served(1, 101.0)[0]
    abnormal, normal = microrank.partition(reference, [fast, slow])
    assert (abnormal, normal) == ([slow[1]], [fast[1]])


def test_kind_with_few_reference_traces_falls_back_to_overall_p99():
    lone = (300.0, (["r"], ["r"], ["IG02:serve"]))
    abnormal, _ = microrank.partition(served(200, 100.0) + [(50.0, lone[1])] * 5, [lone])
    assert abnormal == [lone[1]]


def test_call_that_left_no_span_on_a_reliable_callee_is_abnormal():
    failed = (10.0, (["r", "c"], ["r", "r"], ["IG01:serve", "IG01:call"]))
    abnormal, _ = microrank.partition(served(200, 100.0), [failed])
    assert abnormal == [failed[1]]
