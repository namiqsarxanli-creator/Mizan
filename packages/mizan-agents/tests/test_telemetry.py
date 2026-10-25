"""GenAI telemetry: span tree of an agent run, usage and cost attributes, PII redaction."""

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_orchestrator import GOOD_DRAFT, PLAN, SUPPORTED, TODAY, RoleLLM, orchestrator

from mizan_core.genai import cost_usd, prompt_version, redact
from mizan_core.tracing import configure_tracing


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    provider = configure_tracing(service_name="test", service_version="0", environment="test")
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.clear()


def by_name(spans: tuple[ReadableSpan, ...], name: str) -> list[ReadableSpan]:
    return [s for s in spans if s.name == name]


async def test_an_agent_run_is_one_trace_of_genai_spans(spans: InMemorySpanExporter) -> None:
    llm = RoleLLM(plan=PLAN, drafts=[GOOD_DRAFT], checks=[SUPPORTED])
    await orchestrator(llm, ["art:51"]).run("Sınaq müddəti?", as_of=TODAY)
    finished = spans.get_finished_spans()

    (root,) = by_name(finished, "invoke_agent orchestrator")
    assert root.attributes is not None
    assert root.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert root.attributes["mizan.verdict"] == "answer"
    assert {s.context.trace_id for s in finished} == {root.context.trace_id}

    chats = [s for s in finished if s.name.startswith("chat ")]
    assert [s.name for s in chats] == [
        "chat openai/gpt-oss-20b",  # planner
        "chat openai/gpt-oss-120b",  # drafter
        "chat openai/gpt-oss-20b",  # verifier
    ]
    drafter = chats[1].attributes
    assert drafter is not None
    assert drafter["gen_ai.provider.name"] == "groq"
    assert drafter["gen_ai.usage.input_tokens"] == 50
    assert drafter["mizan.cost.usd"] == pytest.approx((50 * 0.15 + 50 * 0.60) / 1e6)
    assert len(str(drafter["mizan.prompt.version"])) == 12

    tools = {s.name for s in finished if s.name.startswith("execute_tool ")}
    assert {"execute_tool search_law", "execute_tool get_court_decisions"} <= tools
    # Every step hangs under the run.
    parents = {s.parent.span_id for s in by_name(finished, "invoke_agent planner") if s.parent}
    assert parents == {root.context.span_id}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mənə yazın: ali.veli@mail.az", "Mənə yazın: [EMAIL]"),
        ("Telefon: +994 50 123 45 67", "Telefon: [PHONE]"),
        ("Nömrəm 050-123-45-67", "Nömrəm [PHONE]"),
        ("FİN kodum 5ARPXK2 idi", "FİN kodum [FIN] idi"),
        ("Şəxsiyyət vəsiqəsi AZE 12345678", "Şəxsiyyət vəsiqəsi [ID]"),
        ("Kart 4169 7388 1234 5678", "Kart [CARD]"),
        (
            "Əmək Məcəlləsinin 70-ci maddəsi, 2026-cı il",
            "Əmək Məcəlləsinin 70-ci maddəsi, 2026-cı il",
        ),
    ],
)
def test_redaction(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_cost_is_estimated_only_for_priced_models() -> None:
    assert cost_usd("openai/gpt-oss-20b", 1_000_000, 1_000_000) == pytest.approx(0.375)
    assert cost_usd("qwen/qwen3.8-27b", 1000, 1000) is None


def test_prompt_version_changes_with_the_text_only() -> None:
    assert prompt_version("a", "b") == prompt_version("a", "b")
    assert prompt_version("a", "b") != prompt_version("a", "c")
