from __future__ import annotations

import pytest

from mizan_contracts.model_form import model_form_clause, templates

TERMINATION = (
    "Bu Əmək müqaviləsinə Əmək Məcəlləsinin 68, 69, 70, 73, 74 və 75-ci maddələrində nəzərdə "
    "tutulan əsaslarla xitam verilə bilər."
)


def test_templates_are_the_annex_clauses() -> None:
    t = templates()
    assert {"1.1", "4.16", "9.1", "9.3"} <= set(t)
    assert "8" in t["4.16"]
    assert "40" in t["4.16"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (TERMINATION, "9.1"),  # trimmed, nothing bounding a right dropped
        ("İşçi gündə 8 saatdan, həftə ərzində isə 40 saatdan çox olmayaraq işləyir.", "4.16"),
        # A changed number, an added negation, a filled-in value, a dropped limit: not the form.
        ("İşçi gündə 10 saatdan, həftə ərzində isə 40 saatdan çox olmayaraq işləyir.", None),
        (TERMINATION.replace("verilə bilər", "verilə bilməz"), None),
        ("İşçiyə hər ay 1500 manat məbləğdə əmək haqqı ödənilir.", None),
        ("İşçi gündə 8 saatdan, həftə ərzində isə 40 saatdan işləyir.", None),
        ("Bu Əmək müqaviləsinə xitam verilə bilər.", None),  # too short to be the form
    ],
)
def test_model_form_clause(text: str, expected: str | None) -> None:
    assert model_form_clause(text) == expected


async def test_model_form_clauses_skip_the_llm_but_not_the_rules() -> None:
    from collections.abc import Sequence
    from datetime import date

    from mizan_contracts.analysis import analyse
    from mizan_contracts.clauses import Clause
    from mizan_contracts.document import read_document
    from mizan_contracts.review import ReviewStats
    from mizan_contracts.rules import Context, Finding

    class Recorder:
        def __init__(self) -> None:
            self.seen: list[str] = []

        async def review(
            self, clauses: Sequence[Clause], *, as_of: date, ctx: Context
        ) -> tuple[list[Finding], ReviewStats]:
            self.seen.extend(c.number for c in clauses)
            return [], ReviewStats()

    text = (
        "1.1. İşçi Əliyev Nihad (17 yaş) şagird vəzifəsinə işə qəbul edilir.\n"
        "5.1. İşçi gündə 8 saatdan, həftə ərzində isə 40 saatdan çox olmayaraq işləyir.\n"
        f"8.1. {TERMINATION}\n"
    )
    recorder = Recorder()
    doc = await read_document(text.encode(), "c.txt")
    report = await analyse(doc, as_of=date(2026, 10, 31), reviewer=recorder)
    assert recorder.seen == ["1.1"]
    by_number = {c.number: c for c in report.clauses}
    assert by_number["8.1"].model_form == "9.1"
    assert by_number["8.1"].verdict == "ok"
    # The form's 40-hour week is unlawful for a 17-year-old: the rule still fires.
    assert by_number["5.1"].model_form == "4.16"
    assert by_number["5.1"].verdict == "violation"
