"""Which of two conflicting norms applies: the collision rules of Azerbaijani law.

Source: the Constitutional Law "Normativ hüquqi aktlar haqqında" (NHA), cited by article:

- **2.3–2.8, legal force.** Constitution > referendum act > law > decree > Cabinet of
  Ministers decision > act of a central executive body (2.3, 2.4, 2.6, 2.7, 2.8). Codes are
  laws. The Civil Code prevails over other codes and laws on civil-law norms (2.5).
- **10.2, special competence.** An act of a body given special authority in a field prevails
  over an act of a body of the same category in that field.
- **10.3, sectoral act.** Between acts of equal force, the act of the field regulating the
  relation applies.
- **10.4, lex specialis.** Within one act, the special norm prevails over the general one.
- **10.5, lex posterior.** A newer act of the same body on the same matter prevails.
- **10.6, no collision.** Norms that can be applied together do not conflict.
- **11, order.** First 2.3–2.8, then 10.2–10.5, in that order.

The Constitutional Court enforces 2.3. A provision it declared unconstitutional, or whose
application it suspended, loses to the Constitution from the decision's date. Its decisions
are normative acts themselves (NHA 4.1.1; Constitution 130).

The engine resolves; it does not detect. Whether two norms conflict on a given relation is a
question for the caller (an agent in F5, a lawyer), and so are the facts it needs: the field
of the relation, and which norm is special. Inside the Labour Code the graph supplies the
latter from explicit exceptions ("52-ci maddəsində göstərilən hallar istisna olmaqla").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import IntEnum, StrEnum


class Force(IntEnum):
    """Legal force by NHA 2.3–2.8; a larger value prevails."""

    CENTRAL_EXECUTIVE = 1  # mərkəzi icra hakimiyyəti orqanlarının normativ hüquqi aktları
    CABINET = 2  # Nazirlər Kabinetinin qərarları
    DECREE = 3  # Prezidentin fərmanları
    LAW = 4  # qanunlar, məcəllələr
    REFERENDUM = 5  # referendumla qəbul edilmiş aktlar
    CONSTITUTION = 6


# e-qanun's document types -> legal force.
FORCE_BY_TYPE = {
    "konstitusiya": Force.CONSTITUTION,
    "məcəllələr": Force.LAW,
    "qanunlar": Force.LAW,
    "konstitusiya qanunları": Force.LAW,
    "azərbaycan respublikası prezidentinin fərmanları": Force.DECREE,
    "nazirlər kabinetinin qərarları": Force.CABINET,
}


class Rule(StrEnum):
    COURT = "constitutional court"  # NHA 2.3, Constitution 130
    FORCE = "legal force"  # NHA 2.3-2.4, 2.6-2.8
    CIVIL_CODE = "civil code priority"  # NHA 2.5
    COMPETENCE = "special competence"  # NHA 10.2
    SECTORAL = "sectoral act"  # NHA 10.3
    SPECIALIS = "lex specialis"  # NHA 10.4
    POSTERIOR = "lex posterior"  # NHA 10.5
    NO_CONFLICT = "no conflict"  # NHA 10.6
    UNRESOLVED = "unresolved"


BASIS = {
    Rule.COURT: "Konstitusiya 130; NHA Konstitusiya Qanunu 2.3, 4.1.1",
    Rule.FORCE: "NHA Konstitusiya Qanunu 2.3-2.4, 2.6-2.8",
    Rule.CIVIL_CODE: "NHA Konstitusiya Qanunu 2.5",
    Rule.COMPETENCE: "NHA Konstitusiya Qanunu 10.2",
    Rule.SECTORAL: "NHA Konstitusiya Qanunu 10.3",
    Rule.SPECIALIS: "NHA Konstitusiya Qanunu 10.4",
    Rule.POSTERIOR: "NHA Konstitusiya Qanunu 10.5",
    Rule.NO_CONFLICT: "NHA Konstitusiya Qanunu 10.6",
    Rule.UNRESOLVED: "NHA Konstitusiya Qanunu 11; needs a lawyer",
}


@dataclass(frozen=True, slots=True)
class Norm:
    act: str  # act uid, "eqanun:46943"
    provision: str  # "art:51"
    force: Force
    body: str  # adopting body, "milli məclis", "prezident", "mərkəzi bank"...
    adopted_on: date  # of the act (lex posterior compares acts)
    field: str | None = None  # the field the act regulates, "əmək", "dövlət qulluğu"...
    civil_code: bool = False
    # Fields in which the adopting body has special authority (NHA 10.2).
    competence: frozenset[str] = frozenset()
    # Constitutional Court decision that voided or suspended this provision, and its date.
    voided_by: str | None = None
    voided_on: date | None = None

    def void_on(self, day: date) -> bool:
        return self.voided_on is not None and self.voided_on <= day

    @property
    def ref(self) -> str:
        return f"{self.act}/{self.provision}"


@dataclass(frozen=True, slots=True)
class Relation:
    """The facts of the conflict that only the caller knows."""

    on: date  # the date of the facts: what was in force then
    field: str | None = None  # the field the relation belongs to
    civil_law: bool = False  # the conflicting norms are civil-law norms
    # (special, general) provision refs within one act, e.g. from exception edges.
    special_over: frozenset[tuple[str, str]] = frozenset()
    compatible: bool = False  # both norms can be applied at once


@dataclass(frozen=True, slots=True)
class Resolution:
    applies: Norm | None  # None: no conflict (both apply) or unresolved
    rule: Rule
    why: str

    @property
    def basis(self) -> str:
        return BASIS[self.rule]


def resolve(a: Norm, b: Norm, relation: Relation) -> Resolution:
    """Apply NHA 11's order: court voidness, 2.3-2.8, then 10.2-10.5."""
    if relation.compatible:
        return Resolution(None, Rule.NO_CONFLICT, "both norms can be applied together")

    a_void, b_void = a.void_on(relation.on), b.void_on(relation.on)
    if a_void != b_void:
        winner, loser = (b, a) if a_void else (a, b)
        return Resolution(
            winner,
            Rule.COURT,
            f"{loser.ref} was declared unconstitutional or suspended by {loser.voided_by} "
            f"on {loser.voided_on}",
        )
    if a_void and b_void:
        return Resolution(None, Rule.UNRESOLVED, "both provisions were voided by the court")

    if a.force != b.force:
        winner, loser = (a, b) if a.force > b.force else (b, a)
        return Resolution(
            winner, Rule.FORCE, f"{winner.force.name} prevails over {loser.force.name}"
        )

    if relation.civil_law and a.civil_code != b.civil_code:
        winner = a if a.civil_code else b
        return Resolution(winner, Rule.CIVIL_CODE, "the Civil Code prevails on civil-law norms")

    if relation.field is not None:
        a_comp, b_comp = relation.field in a.competence, relation.field in b.competence
        if a_comp != b_comp:  # same force here: 2.3-2.8 did not decide
            winner = a if a_comp else b
            return Resolution(
                winner, Rule.COMPETENCE, f"{winner.body} has special competence in {relation.field}"
            )
        a_field, b_field = a.field == relation.field, b.field == relation.field
        if a_field != b_field and a.act != b.act:
            winner = a if a_field else b
            return Resolution(
                winner, Rule.SECTORAL, f"{winner.act} is the act of the field {relation.field}"
            )

    if a.act == b.act:
        if (a.provision, b.provision) in relation.special_over:
            return Resolution(a, Rule.SPECIALIS, f"{a.provision} is special to {b.provision}")
        if (b.provision, a.provision) in relation.special_over:
            return Resolution(b, Rule.SPECIALIS, f"{b.provision} is special to {a.provision}")

    if a.body == b.body and a.adopted_on != b.adopted_on:
        winner = a if a.adopted_on > b.adopted_on else b
        return Resolution(winner, Rule.POSTERIOR, f"{winner.act} is the newer act of {a.body}")

    return Resolution(None, Rule.UNRESOLVED, "no collision rule decides between these norms")


def specialis_pairs(edges: list[tuple[str, str]]) -> frozenset[tuple[str, str]]:
    """Exception edges (general cites special "... istisna olmaqla") -> (special, general)."""
    return frozenset((special, general) for general, special in edges)
