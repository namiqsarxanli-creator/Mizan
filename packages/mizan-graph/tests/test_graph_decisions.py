"""Court decisions: naming acts, linking decisions to articles, and answering context."""

from collections.abc import Collection
from datetime import date

from mizan_graph.decisions import (
    Decision,
    act_name,
    attach_decisions,
    decision_hit,
    interpreted_articles,
    with_decisions,
)
from mizan_graph.store import InMemoryGraph
from mizan_retrieval.answer import source_title
from mizan_retrieval.search import Hit

SELF = frozenset({"əmək məcəllə"})
D61412 = Decision(
    61412,
    "Azərbaycan Respublikası Əmək Məcəlləsinin 144-cü maddəsinin 2 və 3-cü hissələrinin "
    "Azərbaycan Respublikası Konstitusiyasının 25-ci maddəsinin I və III hissələrinə "
    "uyğunluğunun yoxlanılmasına dair",
    date(2026, 2, 18),
    "unconstitutional",
    "1. ... tətbiqi dayandırılsın.",
    ("art:144",),
)
D54192 = Decision(54192, "...", date(2023, 4, 11), "interpretation", "1. ...", ("art:172",))


def article(key: str) -> Hit:
    return Hit(key, key, key, f"Kod > Maddə {key}\nmətn", 0.5, date(2021, 1, 1), (key,))


def test_act_names_match_citation_names() -> None:
    assert act_name("Azərbaycan Respublikasının Əmək Məcəlləsi", "Məcəllələr") == "əmək məcəllə"
    assert act_name("Azərbaycan Respublikasının Mülki Prosessual Məcəlləsi", "Məcəllələr") == (
        "mülki prosessual məcəllə"
    )
    assert act_name("Azərbaycan Respublikasının Konstitusiyası", "Konstitusiya") == "konstitusiya"
    assert act_name("Mediasiya haqqında", "Qanunlar") == "qanun: mediasiya haqqında"
    assert act_name("Fərman", "AZƏRBAYCAN RESPUBLİKASI PREZİDENTİNİN FƏRMANLARI") is None


def test_a_decision_interprets_the_code_articles_its_title_names() -> None:
    # The Constitution's Art. 25 is the standard of review, not an interpreted article.
    assert interpreted_articles(D61412.title, SELF) == ("art:144",)
    title = (
        "Azərbaycan Respublikası Əmək Məcəlləsinin 3-cü maddəsinin 1-ci hissəsinin, 294 və "
        "296-cı maddələrinin şərh edilməsinə dair"
    )
    assert interpreted_articles(title, SELF) == ("art:3", "art:294", "art:296")


def test_decisions_follow_the_article_they_interpret_once() -> None:
    hits = [article("art:144"), article("art:116"), article("art:144")]
    out = with_decisions(hits, [D61412])
    assert [h.key for h in out] == ["art:144", "kmq:61412", "art:116", "art:144"]


def test_a_decision_reads_like_a_source() -> None:
    hit = decision_hit(D61412)
    assert source_title(hit).startswith(
        "Konstitusiya Məhkəməsi Plenumunun 18 fevral 2026-ci il tarixli qərarı"
    )
    assert hit.text.splitlines()[1] == "QƏRARA ALDI:"
    assert hit.cites == ("art:144",)


async def test_only_decisions_adopted_by_the_date_are_attached() -> None:
    store = InMemoryGraph(46943, [], [D61412, D54192])
    hits = [article("art:144"), article("art:172")]

    before = await attach_decisions(hits, store, source_id=46943, as_of=date(2025, 12, 1))
    after = await attach_decisions(hits, store, source_id=46943, as_of=date(2026, 3, 1))

    assert [h.key for h in before] == ["art:144", "art:172", "kmq:54192"]
    assert [h.key for h in after] == ["art:144", "kmq:61412", "art:172", "kmq:54192"]


class BrokenStore:
    async def decisions_for(
        self, source_id: int, keys: Collection[str], *, as_of: date
    ) -> list[Decision]:
        raise ConnectionError("neo4j is down")


async def test_graph_outage_leaves_the_articles() -> None:
    hits = [article("art:144")]
    out = await attach_decisions(hits, BrokenStore(), source_id=46943, as_of=date(2026, 3, 1))
    assert out == hits
