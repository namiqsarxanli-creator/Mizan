import json
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from mizan_retrieval.answer import (
    AnswerUnavailableError,
    ClaudeAnswerer,
    GroqAnswerer,
    build_documents,
    fit_to_budget,
    parse_claude_response,
    parse_groq_response,
    verify_citations,
)
from mizan_retrieval.search import Hit

HITS = [
    Hit(
        key="art:69/part:1",
        article_key="art:69",
        parent_key="art:69",
        text="Əmək Məcəlləsi > Maddə 69. Xitam\n1. İşçi bir təqvim ayı qabaqcadan xəbərdar edir.",
        score=0.9,
        valid_from=date(2025, 6, 13),
        cites=("art:69/part:1",),
    ),
    Hit(
        key="art:51",
        article_key="art:51",
        parent_key="art:51",
        text="Əmək Məcəlləsi > Maddə 51. Sınaq müddəti\nSınaq müddəti üç aydan çox ola bilməz.",
        score=0.5,
        valid_from=date(1999, 2, 1),
        cites=("art:51",),
    ),
]


def text_block(text: str, citations: list[tuple[int, str]] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        type="text",
        text=text,
        citations=[SimpleNamespace(document_index=i, cited_text=q) for i, q in citations or []],
    )


def response(*blocks: Any, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks),
        model="claude-opus-5-5",
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=80),
    )


class FakeMessages:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.result


def fake_client(result: Any) -> tuple[Any, FakeMessages]:
    messages = FakeMessages(result)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def test_each_hit_becomes_a_citable_document() -> None:
    docs = build_documents(HITS)
    assert [d["title"] for d in docs] == ["Maddə 69. Xitam", "Maddə 51. Sınaq müddəti"]
    assert all(d["citations"] == {"enabled": True} for d in docs)
    assert "art:69/part:1" in docs[0]["context"]


def test_citations_map_back_to_provision_keys() -> None:
    answer = parse_claude_response(
        response(
            text_block("Bir ay əvvəl xəbərdar etməlisiniz. ", [(0, "bir  təqvim ayı\nqabaqcadan")]),
            text_block("Ətraflı üçün hüquqşünasa müraciət edin."),
        ),
        HITS,
    )
    assert (
        answer.text == "Bir ay əvvəl xəbərdar etməlisiniz. Ətraflı üçün hüquqşünasa müraciət edin."
    )
    [citation] = answer.citations
    assert citation.key == "art:69/part:1"
    assert citation.title == "Maddə 69. Xitam"
    assert citation.quote == "bir təqvim ayı qabaqcadan"
    assert answer.usage["cache_read_input_tokens"] == 80


def test_out_of_range_citation_indices_are_ignored() -> None:
    answer = parse_claude_response(response(text_block("x", [(7, "?"), (-1, "?")])), HITS)
    assert answer.citations == ()


def test_duplicate_citations_are_collapsed() -> None:
    blocks = [text_block("a", [(1, "üç ay")]), text_block("b", [(1, "üç  ay")])]
    assert len(parse_claude_response(response(*blocks), HITS).citations) == 1


def test_refusal_is_reported_not_parsed() -> None:
    answer = parse_claude_response(response(stop_reason="refusal"), HITS)
    assert answer.refused
    assert answer.citations == ()


async def test_request_shape() -> None:
    client, messages = fake_client(response(text_block("ok")))

    await ClaudeAnswerer(client).answer("Nə qədər?", HITS, as_of=date(2026, 10, 13))

    [call] = messages.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"] == {"effort": "medium"}
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in call  # always on for this model; sending "disabled" is a 400
    system = call["system"][0]
    assert system["cache_control"] == {"type": "ephemeral"}
    content = call["messages"][0]["content"]
    assert [c["type"] for c in content] == ["document", "document", "text"]
    assert content[-1]["text"] == "Tarix: 2026-10-13\nSual: Nə qədər?"


async def test_no_hits_means_no_llm_call() -> None:
    client, messages = fake_client(response())

    answer = await ClaudeAnswerer(client).answer("?", [], as_of=date(2026, 1, 1))

    assert messages.calls == []
    assert "tapılmadı" in answer.text


# ----------------------------------------------------------------------------- Groq backend


def groq_response(content: str, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
        ],
        model="openai/gpt-oss-120b",
        usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10),
    )


class FakeCompletions:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.result


def fake_groq(result: Any) -> tuple[Any, FakeCompletions]:
    completions = FakeCompletions(result)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def test_verified_quotes_are_kept_and_invented_ones_dropped() -> None:
    raw = [
        {"source": 1, "quote": "bir təqvim ayı qabaqcadan"},  # verbatim
        {"source": 1, "quote": "  BİR  təqvim ayı\nqabaqcadan "},  # whitespace/case only
        {"source": 2, "quote": "Sınaq müddəti altı aydan çox ola bilməz"},  # wrong number
        {"source": 3, "quote": "üç aydan"},  # source that was never given
        {"source": "x", "quote": "?"},  # malformed
        {"quote": "no source"},
    ]

    accepted, rejected = verify_citations(raw, HITS)

    assert [(c.key, c.quote) for c in accepted] == [("art:69/part:1", "bir təqvim ayı qabaqcadan")]
    assert rejected == 4


def test_citations_must_be_a_list() -> None:
    assert verify_citations({"source": 1}, HITS) == ([], 0)


def test_groq_answer_parsing() -> None:
    content = (
        '{"answer": "Üç aydan çox ola bilməz.", '
        '"citations": [{"source": 2, "quote": "Sınaq müddəti üç aydan çox ola bilməz."}]}'
    )
    answer = parse_groq_response(groq_response(content), HITS)

    assert answer.text == "Üç aydan çox ola bilməz."
    assert [c.key for c in answer.citations] == ["art:51"]
    assert answer.rejected_citations == 0
    assert answer.usage == {"input_tokens": 50, "output_tokens": 10}


@pytest.mark.parametrize("content", ["not json", '{"citations": []}', "", "[1, 2]"])
def test_malformed_groq_output_is_reported_not_trusted(content: str) -> None:
    answer = parse_groq_response(groq_response(content), HITS)
    assert answer.refused
    assert answer.citations == ()


async def test_groq_request_shape() -> None:
    client, completions = fake_groq(groq_response('{"answer": "ok", "citations": []}'))

    await GroqAnswerer(client).answer("Nə qədər?", HITS, as_of=date(2026, 10, 13))

    [call] = completions.calls
    assert call["model"] == "openai/gpt-oss-120b"
    assert call["response_format"] == {"type": "json_object"}
    assert call["reasoning_effort"] == "medium"
    system, user = call["messages"]
    assert "JSON" in system["content"]
    assert '<source id="1" key="art:69/part:1"' in user["content"]
    assert user["content"].endswith("Tarix: 2026-10-13\nSual: Nə qədər?")


async def test_reasoning_effort_is_only_sent_to_models_that_support_it() -> None:
    client, completions = fake_groq(groq_response('{"answer": "ok", "citations": []}'))

    await GroqAnswerer(client, model="llama-3.3-70b-versatile").answer(
        "?", HITS, as_of=date(2026, 1, 1)
    )

    assert "reasoning_effort" not in completions.calls[0]


# ------------------------------------------------------------------- budget and failures


def test_budget_keeps_rank_order_and_truncates_the_last_source() -> None:
    long = Hit(
        "annex:1", "annex:1", "annex:1", "Başlıq\n" + "söz " * 5000, 0.1, date(2000, 1, 1), ()
    )
    kept = fit_to_budget([HITS[0], long, HITS[1]], max_chars=2000)

    assert [h.key for h in kept] == ["art:69/part:1", "annex:1"]
    assert sum(len(h.text) for h in kept) <= 2000
    assert kept[1].text.endswith("[...]")


def test_budget_drops_sources_that_would_only_fit_as_a_stub() -> None:
    assert [h.key for h in fit_to_budget(HITS, max_chars=len(HITS[0].text) + 100)] == [
        "art:69/part:1"
    ]


def test_budget_always_keeps_the_top_source() -> None:
    [kept] = fit_to_budget(HITS[:1], max_chars=40)
    assert kept.key == "art:69/part:1"
    assert len(kept.text) <= 40


class ProviderError(Exception):
    def __init__(self, status_code: int, retry_after: str = "") -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.response = SimpleNamespace(headers={"retry-after": retry_after} if retry_after else {})


class FailingCompletions:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def create(self, **_: Any) -> Any:
        raise self.exc


@pytest.mark.parametrize(
    ("exc", "message", "retry_after"),
    [
        (ProviderError(413), "exceed", None),
        (ProviderError(429, "12"), "rate-limiting", 12),
        (ProviderError(500), r"failed \(500\)", None),
        (TimeoutError(), r"failed \(TimeoutError\)", None),
    ],
)
async def test_provider_errors_become_answer_unavailable(
    exc: Exception, message: str, retry_after: int | None
) -> None:
    client = SimpleNamespace(chat=SimpleNamespace(completions=FailingCompletions(exc)))

    with pytest.raises(AnswerUnavailableError, match=message) as info:
        await GroqAnswerer(client).answer("?", HITS, as_of=date(2026, 1, 1))
    assert info.value.retry_after_s == retry_after


def test_elided_quotes_are_verified_segment_by_segment() -> None:
    raw = [
        # Each segment verbatim, in order: kept, shown in the statute's words.
        {"source": 1, "quote": "İşçi bir təqvim … xəbərdar edir"},
        {"source": 1, "quote": "işçi bir təqvim [...] xəbərdar edir"},
        # Segments out of order, a segment the source lacks, a segment too short to mean much.
        {"source": 1, "quote": "xəbərdar edir … İşçi bir təqvim"},
        {"source": 1, "quote": "İşçi bir təqvim … iki həftə qabaqcadan"},
        {"source": 2, "quote": "Sınaq müddəti … üç"},
    ]
    accepted, rejected = verify_citations(raw, HITS)
    assert [c.quote for c in accepted] == ["İşçi bir təqvim … xəbərdar edir"]
    assert rejected == 3


def test_the_model_can_flag_a_refusal() -> None:
    from mizan_retrieval.answer import parse_groq_response

    def response(payload: dict[str, Any]) -> Any:
        message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
        return SimpleNamespace(
            model="m",
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    declined = parse_groq_response(
        response({"answer": "Üzr istəyirəm, bunu edə bilmərəm.", "refused": True}), HITS
    )
    assert declined.refused
    answered = parse_groq_response(response({"answer": "Bir ay.", "citations": []}), HITS)
    assert not answered.refused  # absent flag: an answer, as before
    assert not parse_groq_response(response({"answer": "x", "refused": "yes"}), HITS).refused
