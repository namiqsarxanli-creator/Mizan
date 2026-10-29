"""Shadow report: per-candidate agreement, latency and cost from shadow rows."""

from datetime import date

from mizan_evals.shadow import Row, render, summarise


def row(overlap: float, agrees: bool, latency: float = 900.0) -> Row:
    return Row(
        candidate="openai/gpt-oss-20b",
        primary_model="openai/gpt-oss-120b",
        tokens=3000,
        primary_tokens=4000,
        latency_ms=latency,
        primary_latency_ms=2800.0,
        citation_overlap=overlap,
        refusal_agrees=agrees,
    )


def test_summary_counts_agreement_latency_and_cost() -> None:
    s = summarise([row(1.0, True), row(0.5, True), row(0.0, False, latency=1500.0)])
    c = s["openai/gpt-oss-20b"]
    assert c["requests"] == 3
    assert c["citation_overlap"][0] == 0.5
    assert c["same_citations"] == round(1 / 3, 3)
    assert c["refusal_agreement"][0] == round(2 / 3, 3)
    assert c["latency_ms"]["primary_p50"] == 2800.0
    assert c["latency_ms"]["candidate_p50"] == 900.0
    # 20b is half the price of 120b per token and uses fewer tokens here.
    assert 0 < c["cost_per_request"]["ratio"] < 0.5


def test_report_renders_and_handles_no_data() -> None:
    text = render(summarise([row(1.0, True)]), date(2026, 10, 29))
    assert "# Shadow report — 2026-10-29" in text
    assert "`openai/gpt-oss-20b` vs `openai/gpt-oss-120b` (1 requests)" in text
    assert "No shadow runs" in render({}, date(2026, 10, 29))
