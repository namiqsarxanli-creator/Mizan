"""The fact-based rules: the same fact in another author's phrasing (F10 post-hoc)."""

from __future__ import annotations

from datetime import date

import pytest

from mizan_contracts.clauses import Clause, split_clauses
from mizan_contracts.rules import Context, check, context_of, daily_hours, dates_in

AS_OF = date(2026, 10, 31)
OPEN = Context(term_months=None, minor_age=None, long_leave_job=None)
MINOR = Context(term_months=None, minor_age=16, long_leave_job=None)
PREGNANT = Context(term_months=None, minor_age=None, long_leave_job=None, pregnant=True)
TEACHER = Context(term_months=None, minor_age=None, long_leave_job="müəllim")


def articles(text: str, ctx: Context = OPEN) -> set[str]:
    return {a for f in check([Clause("2.1", None, text)], ctx) for a in f.articles}


def test_dates_in_three_styles() -> None:
    text = "1 iyun 2026-cı ildən 31.07.2026 tarixinədək; 2026-cı il noyabrın 3-dən"
    assert dates_in(text) == [date(2026, 6, 1), date(2026, 7, 31), date(2026, 11, 3)]


def test_context_from_birth_date_date_range_pregnancy_and_teacher() -> None:
    clauses = split_clauses(
        "1.3. İşçi riyaziyyat müəllimi vəzifəsinə qəbul edilir.\n"
        "1.4. Mövsümi iş üçün müqavilə 1 iyun 2026-cı ildən 31 iyul 2026-cı ilədək bağlanır.\n"
        "2.1. İşçinin doğum tarixi 10 dekabr 2009-cu ildir.\n"
        "2.2. İşçi hamiləlik haqqında tibbi arayışı təqdim etmişdir.\n"
    )
    ctx = context_of(clauses, AS_OF)
    assert ctx.minor_age == 16
    assert ctx.term_months == 2.0
    assert ctx.long_leave_job == "müəllim"
    assert ctx.pregnant


def test_a_generic_mention_of_pregnant_women_is_not_a_fact() -> None:
    clauses = split_clauses("5.1. Hamilə qadınlar üçün qanunla nəzərdə tutulan təminatlar verilir.")
    assert not context_of(clauses, AS_OF).pregnant


@pytest.mark.parametrize(
    ("text", "hours"),
    [
        ("Hər gün doqquz saat iş və bir saat nahar fasiləsi nəzərdə tutulur.", [9.0]),
        ("Hər gün 08:00–18:00 arasında bir saat nahar fasiləsi verilir.", [9.0]),
        ("İş saat 09:00-da başlanır, 18:00-da qurtarır; nahar 13:00-dan 14:00-dək.", [8.0]),
        ("İş vaxtı 09:00–17:00, nahar fasiləsi yoxdur.", [8.0]),
    ],
)
def test_daily_hours(text: str, hours: list[float]) -> None:
    from mizan_ingestion.text import az_lower

    assert daily_hours(az_lower(text)) == hours


@pytest.mark.parametrize(
    ("text", "ctx", "expected"),
    [
        (
            "İş həftəsi beş gündür; hər gün 08:00–18:00 arasında bir saat nahar verilir.",
            OPEN,
            "art:89",
        ),
        ("İş həftəsi beş gündür; hər gün 09:00–18:00 arasında bir saat nahar verilir.", OPEN, None),
        (
            "Sifariş artdıqda iki ardıcıl günün hər birində üç saat əlavə iş məcburidir.",
            OPEN,
            "art:100",
        ),
        ("İki ardıcıl günün hər birində iki saat əlavə iş ola bilər.", OPEN, None),
        ("İşçinin üç aylıq sınağı əlavə üç ay uzadıla bilər.", OPEN, "art:51"),
        (
            "Ərizəsi yalnız üç ay əvvəl verildikdə öz təşəbbüsü ilə xitam qəbul edilir.",
            OPEN,
            "art:69",
        ),
        ("İşçi hər il tarixsiz işdən çıxma ərizəsi imzalayıb verməlidir.", OPEN, "art:69"),
        ("Aylıq əməkhaqqı yalnız ABŞ dolları ilə ödənilir.", OPEN, "art:174"),
        (
            "Ayın son cümə günü işçi 22:00-dan 24:00-dək sayımda iştirak etməlidir.",
            MINOR,
            "art:254",
        ),
        ("İş vaxtından artıq işin hər saatı ikiqat ödənilir.", MINOR, None),
        ("Gecə növbəsi işçinin razılığı olmadan təyin edilir.", PREGNANT, "art:242"),
        ("Gecə növbəsinə yalnız işçinin yazılı razılığı ilə cəlb edilir.", PREGNANT, None),
        ("Ştat ixtisarı zamanı hamilə işçi işdən azad edilə bilər.", OPEN, "art:79"),
        ("Əsas illik məzuniyyət 21 təqvim günüdür.", MINOR, "art:119"),
        ("Əsas illik məzuniyyət 35 təqvim günüdür.", MINOR, None),
        ("İllik məzuniyyət altı həftə, yəni 42 təqvim günü ilə məhdudlaşır.", TEACHER, "art:118"),
        ("Əsas illik məzuniyyət 56 təqvim günüdür.", TEACHER, None),
        (
            "Müddət bitdikdə istifadə edilməmiş məzuniyyətə görə pul əvəzi verilmir.",
            OPEN,
            "art:144",
        ),
        (
            "İşçi illik ödənişli məzuniyyətdən istifadə etmək hüququndan tam imtina edir.",
            OPEN,
            "art:135",
        ),
        (
            "Gecikməyə görə direktor maaşın yarısı həcmində intizam cəriməsi tətbiq edir.",
            OPEN,
            "art:186",
        ),
        (
            "Kollektiv müqavilədə nəzərdə tutulduqda aylıq əmək haqqının dörddə biri "
            "həcmində cərimə tətbiq edilə bilər.",
            OPEN,
            None,
        ),
        ("İşçi məhkəməyə müraciət etmək hüququndan imtina edir.", OPEN, "art:9"),
        ("İşəgötürən əməkhaqqını birtərəfli qaydada 20 faiz azalda bilər.", OPEN, "art:56"),
        ("İşçi həmkarlar ittifaqına üzv olduqda müqaviləyə dərhal xitam verilir.", OPEN, "art:79"),
        ("Həmkarlar ittifaqına üzvlüyə görə xitam verilə bilməz.", OPEN, None),
        # The second unseen set's false alarms (post-hoc): lawful, must stay silent.
        (
            "Gündüz postu bazar ertəsi–cümə günləri 08:30–17:30 işləyir; 12:30–13:30 "
            "aralığında əvəzedici nəzarətçi xidmət göstərir.",
            OPEN,
            None,
        ),
        (
            "Kollektiv müqavilədə nəzərdə tutulduğu üçün intizam xətasına aylıq əməkhaqqının "
            "beşdə biri həcmində cərimə tətbiq oluna bilər; bütün adi tutulmalar hər ödənişin "
            "20 faizindən çox olmur.",
            OPEN,
            None,
        ),
        (
            "Bu dörd aylıq müqavilədə on təqvim günlük sınaq razılaşdırılır.",
            Context(4.0, None, None),
            None,
        ),
        (
            "Kollektiv müqavilədə nəzərdə tutulduqda aylıq maaşın yarısı həcmində cərimə "
            "tətbiq edilir.",
            OPEN,
            "art:186",
        ),
    ],
)
def test_fact_rules(text: str, ctx: Context, expected: str | None) -> None:
    found = articles(text, ctx)
    if expected is None:
        assert found == set()
    else:
        assert expected in found, found
