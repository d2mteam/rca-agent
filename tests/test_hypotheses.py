from rca import hypotheses


def test_answer_needs_a_supported_hypothesis_and_a_refuted_alternative(tmp_path):
    case = "bank-x"
    hypotheses.add(tmp_path, case, "Tomcat01", "network latency", "callers wait, Tomcat01 spans stay fast", "no wait")
    log = hypotheses.load(tmp_path, case)
    assert "no supported hypothesis" in hypotheses.check_answer(log, "Tomcat01", "network latency")

    hypotheses.test(tmp_path, case, 1, "supported", "exclusive: network in 1 -> 8884 ms")
    assert "no alternative" in hypotheses.check_answer(hypotheses.load(tmp_path, case), "Tomcat01", "network latency")

    hypotheses.add(tmp_path, case, "IG01", "high disk I/O read usage", "reads stay high for minutes", "isolated blips")
    hypotheses.test(tmp_path, case, 2, "refuted", "series: two one-minute blips")
    assert hypotheses.check_answer(hypotheses.load(tmp_path, case), "Tomcat01", "network latency") is None


def test_same_component_with_another_reason_counts_as_an_alternative(tmp_path):
    hypotheses.add(tmp_path, "c", "Tomcat02", "high CPU usage", "a", "b")
    hypotheses.add(tmp_path, "c", "Tomcat02", "JVM Out of Memory (OOM) Heap", "a", "b")
    hypotheses.test(tmp_path, "c", 1, "refuted", "x")
    hypotheses.test(tmp_path, "c", 2, "supported", "y")
    assert hypotheses.check_answer(hypotheses.load(tmp_path, "c"), "Tomcat02", "JVM Out of Memory (OOM) Heap") is None
