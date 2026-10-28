"""Cache calibration: threshold rule, near-miss pairs and the workload simulation."""

import numpy as np

from mizan_evals.cache_eval import (
    Pair,
    half,
    near_misses,
    parse_paraphrases,
    pick_threshold,
    rates,
    simulate,
)


def test_near_misses_share_a_gold_article() -> None:
    gold = {
        "q1": frozenset({"art:51"}),
        "q2": frozenset({"art:51", "art:52"}),
        "q3": frozenset({"art:9"}),
    }
    assert near_misses(gold) == [("q1", "q2")]


def test_threshold_keeps_false_hits_within_the_budget() -> None:
    negatives = [Pair("a", f"n{i}", s, False) for i, s in enumerate(np.linspace(0.5, 0.9, 200))]
    positives = [Pair("a", "p", 0.95, True)]
    t = pick_threshold(negatives + positives, max_false_hit=0.01)
    false_hits = sum(p.similarity >= t for p in negatives)
    assert false_hits <= 2  # 1% of 200
    assert sum(p.similarity >= np.nextafter(t, 0.0) for p in negatives) >= false_hits
    assert rates(negatives + positives, t)["paraphrase_hit_rate"][0] == 1.0


def test_simulation_counts_hits_and_false_hits() -> None:
    v = {
        "q1": np.array([1.0, 0.0]),
        "q1#1": np.array([0.99, 0.141]),  # close to q1
        "q2": np.array([0.98, 0.199]),  # close to q1 too, but another question
        "q3": np.array([0.0, 1.0]),
    }
    phrasings = {"q1": ["q1", "q1#1"], "q2": ["q2"], "q3": ["q3"]}
    out = simulate(phrasings, v, lambda earlier, now: True, threshold=0.97, seed=1)
    assert out["requests"] == 4
    assert out["cache_hits"] + out["llm_calls"] == 4
    assert out["cache_hits"] >= 1
    assert out["false_hits"] <= out["cache_hits"]
    blocked = simulate(phrasings, v, lambda earlier, now: False, threshold=0.97, seed=1)
    assert blocked["cache_hits"] == 0  # sources not retrieved again: never served


def test_half_is_stable_and_splits() -> None:
    assert half("q001") == half("q001")
    assert {half(f"q{i:03d}") for i in range(20)} == {"calib", "eval"}


def test_paraphrases_are_parsed_from_wrapped_json() -> None:
    content = 'Budur: {"items": [{"id": "q1", "paraphrases": ["Sınaq nə qədərdir?", "x"]}]}'
    assert parse_paraphrases(content) == {"q1": ["Sınaq nə qədərdir?"]}
    assert parse_paraphrases("no json") == {}
