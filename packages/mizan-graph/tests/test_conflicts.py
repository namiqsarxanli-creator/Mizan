"""Collision rules: 22 scenarios, each tied to the NHA Konstitusiya Qanunu article it tests.

Acts and provisions are real where the scenario exists in the Labour Code (Arts. 45/47, 51/52,
117/118, 144 and Constitutional Court decision 61412); the others use realistic stand-ins.
"""

from dataclasses import replace
from datetime import date

import pytest

from mizan_graph.conflicts import Force, Norm, Relation, Rule, resolve, specialis_pairs

TODAY = date(2026, 10, 18)
LABOUR = "eqanun:46943"
CIVIL = "eqanun:46944"

CONSTITUTION_25 = Norm("eqanun:897", "art:25", Force.CONSTITUTION, "referendum", date(1995, 11, 12))
REFERENDUM = Norm("eqanun:9001", "art:1", Force.REFERENDUM, "referendum", date(2016, 9, 26))
LABOUR_51 = Norm(LABOUR, "art:51", Force.LAW, "milli məclis", date(1999, 2, 1), field="əmək")
LABOUR_52 = replace(LABOUR_51, provision="art:52")
LABOUR_45 = replace(LABOUR_51, provision="art:45")
LABOUR_47 = replace(LABOUR_51, provision="art:47")
LABOUR_117 = replace(LABOUR_51, provision="art:117")
LABOUR_118 = replace(LABOUR_51, provision="art:118")
LABOUR_144 = replace(
    LABOUR_51, provision="art:144", voided_by="KMQ 61412", voided_on=date(2026, 2, 18)
)
CIVIL_CODE = Norm(CIVIL, "art:390", Force.LAW, "milli məclis", date(1999, 12, 28), "mülki", True)
CIVIL_SERVICE = Norm(
    "eqanun:2001", "art:23", Force.LAW, "milli məclis", date(2000, 7, 21), field="dövlət qulluğu"
)
DECREE = Norm("eqanun:7001", "art:3", Force.DECREE, "prezident", date(2024, 5, 1))
OLD_DECREE = replace(DECREE, act="eqanun:7000", adopted_on=date(2015, 3, 1))
CABINET = Norm("eqanun:8001", "art:2", Force.CABINET, "nazirlər kabineti", date(2023, 1, 10))
MINISTRY = Norm("eqanun:8501", "art:4", Force.CENTRAL_EXECUTIVE, "əmək nazirliyi", date(2025, 1, 1))
CENTRAL_BANK = Norm(
    "eqanun:8601",
    "art:7",
    Force.CENTRAL_EXECUTIVE,
    "mərkəzi bank",
    date(2022, 6, 1),
    competence=frozenset({"maliyyə bazarları"}),
)
TAX_SERVICE = Norm(
    "eqanun:8701", "art:9", Force.CENTRAL_EXECUTIVE, "vergi xidməti", date(2024, 2, 1)
)
NEW_LAW = Norm("eqanun:3002", "art:1", Force.LAW, "milli məclis", date(2020, 6, 1))
OLD_LAW = Norm("eqanun:3001", "art:1", Force.LAW, "milli məclis", date(2010, 6, 1))

# From the graph: Art. 51 says "52-ci maddəsində göstərilən hallar istisna olmaqla",
# Art. 45 "47-ci maddəsi ilə müəyyən edilən hallar istisna olmaqla", Art. 117(4) excludes 118.
EXCEPTIONS = specialis_pairs([("art:51", "art:52"), ("art:45", "art:47"), ("art:117", "art:118")])

SCENARIOS = [
    # id, a, b, relation, expected winner, rule
    (
        "constitution over code",
        CONSTITUTION_25,
        LABOUR_51,
        Relation(TODAY),
        CONSTITUTION_25,
        Rule.FORCE,
    ),
    ("referendum over law", LABOUR_51, REFERENDUM, Relation(TODAY), REFERENDUM, Rule.FORCE),
    ("code over decree", DECREE, LABOUR_51, Relation(TODAY), LABOUR_51, Rule.FORCE),
    ("decree over cabinet", CABINET, DECREE, Relation(TODAY), DECREE, Rule.FORCE),
    ("cabinet over ministry", MINISTRY, CABINET, Relation(TODAY), CABINET, Rule.FORCE),
    (
        "law beats newer decree",
        NEW_LAW,
        replace(DECREE, adopted_on=date(2026, 1, 1)),
        Relation(TODAY),
        NEW_LAW,
        Rule.FORCE,
    ),
    (
        "force before specialis",
        LABOUR_51,
        replace(DECREE, act=LABOUR),
        Relation(TODAY, special_over=frozenset({("art:3", "art:51")})),
        LABOUR_51,
        Rule.FORCE,
    ),
    (
        "civil code on civil norms",
        LABOUR_51,
        CIVIL_CODE,
        Relation(TODAY, civil_law=True),
        CIVIL_CODE,
        Rule.CIVIL_CODE,
    ),
    (
        "labour relation: sectoral, not civil code",
        CIVIL_CODE,
        LABOUR_51,
        Relation(TODAY, field="əmək"),
        LABOUR_51,
        Rule.SECTORAL,
    ),
    (
        "civil service relation: its own law",
        LABOUR_51,
        CIVIL_SERVICE,
        Relation(TODAY, field="dövlət qulluğu"),
        CIVIL_SERVICE,
        Rule.SECTORAL,
    ),
    (
        "central bank's special competence",
        TAX_SERVICE,
        CENTRAL_BANK,
        Relation(TODAY, field="maliyyə bazarları"),
        CENTRAL_BANK,
        Rule.COMPETENCE,
    ),
    (
        "probation: art 52 special to art 51",
        LABOUR_51,
        LABOUR_52,
        Relation(TODAY, special_over=EXCEPTIONS),
        LABOUR_52,
        Rule.SPECIALIS,
    ),
    (
        "fixed-term contract: art 47 special to art 45",
        LABOUR_47,
        LABOUR_45,
        Relation(TODAY, special_over=EXCEPTIONS),
        LABOUR_47,
        Rule.SPECIALIS,
    ),
    (
        "teachers' leave: art 118 special to art 117",
        LABOUR_117,
        LABOUR_118,
        Relation(TODAY, special_over=EXCEPTIONS),
        LABOUR_118,
        Rule.SPECIALIS,
    ),
    ("newer law of the same body", OLD_LAW, NEW_LAW, Relation(TODAY), NEW_LAW, Rule.POSTERIOR),
    ("newer decree of the same body", DECREE, OLD_DECREE, Relation(TODAY), DECREE, Rule.POSTERIOR),
    (
        "art 144(3) suspended by the court",
        LABOUR_144,
        LABOUR_117,
        Relation(date(2026, 3, 1)),
        LABOUR_117,
        Rule.COURT,
    ),
]


@pytest.mark.parametrize(
    ("a", "b", "relation", "winner", "rule"),
    [s[1:] for s in SCENARIOS],
    ids=[s[0] for s in SCENARIOS],
)
def test_scenario(a: Norm, b: Norm, relation: Relation, winner: Norm, rule: Rule) -> None:
    result = resolve(a, b, relation)
    assert (result.applies, result.rule) == (winner, rule)
    # The rule is symmetric: the order of the arguments never matters.
    swapped = resolve(b, a, relation)
    assert (swapped.applies, swapped.rule) == (winner, rule)


def test_court_decision_applies_only_from_its_date() -> None:
    before = resolve(LABOUR_144, LABOUR_117, Relation(date(2025, 12, 1)))
    assert before.rule is not Rule.COURT


def test_norms_applicable_together_do_not_conflict() -> None:
    result = resolve(LABOUR_51, DECREE, Relation(TODAY, compatible=True))
    assert (result.applies, result.rule) == (None, Rule.NO_CONFLICT)


def test_specialis_needs_one_act() -> None:
    # A "special" relation across different acts is not 10.4; equal force, different bodies.
    other = replace(LABOUR_52, act="eqanun:5555", body="başqa orqan")
    result = resolve(LABOUR_51, other, Relation(TODAY, special_over=EXCEPTIONS))
    assert result.rule is Rule.UNRESOLVED


def test_posterior_needs_the_same_body() -> None:
    result = resolve(MINISTRY, TAX_SERVICE, Relation(TODAY))
    assert (result.applies, result.rule) == (None, Rule.UNRESOLVED)


def test_competence_outside_its_field_does_not_count() -> None:
    result = resolve(TAX_SERVICE, CENTRAL_BANK, Relation(TODAY, field="vergi"))
    assert result.rule is not Rule.COMPETENCE


def test_every_resolution_cites_its_legal_basis() -> None:
    for _, a, b, relation, _, _ in SCENARIOS:
        assert "NHA Konstitusiya Qanunu" in resolve(a, b, relation).basis
