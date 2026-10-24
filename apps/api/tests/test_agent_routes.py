"""Agent endpoints: JSON answer, SSE stream, disabled agent."""

import json
from dataclasses import replace
from datetime import date

from fakes import fake_legal_services
from fastapi.testclient import TestClient

from mizan_agents.orchestrator import Event, EventSink, Step
from mizan_agents.schemas import AgentAnswer, Verdict
from mizan_api.main import create_app
from mizan_core.config import Environment, Settings


class FakeAgent:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, date]] = []

    async def run(
        self, question: str, *, as_of: date, on_event: EventSink | None = None
    ) -> AgentAnswer:
        self.calls.append((question, as_of))
        if on_event is not None:
            await on_event(Event(Step.PLAN, "Sual təhlil edilir..."))
            await on_event(Event(Step.RESEARCH, "Maddələr axtarılır...", {"sub_questions": ["a"]}))
        if self.fail:
            raise RuntimeError("provider down")
        return AgentAnswer(
            question=question,
            as_of=as_of,
            verdict=Verdict.ANSWER,
            answer="Bir ay.",
            claims=[],
            evidence=[],
            confidence=0.9,
            iterations=1,
        )


def client(agent: FakeAgent | None) -> TestClient:
    settings = Settings(_env_file=None, environment=Environment.TEST)
    services = replace(fake_legal_services(), agent=agent)  # type: ignore[arg-type]
    return TestClient(create_app(settings, checks=[], legal=services))


def test_agent_answers_as_json() -> None:
    agent = FakeAgent()
    with client(agent) as c:
        response = c.post(
            "/v1/agent/ask", json={"question": "İşdən çıxmaq?", "as_of": "2024-01-01"}
        )
    assert response.status_code == 200
    assert response.json()["verdict"] == "answer"
    assert agent.calls == [("İşdən çıxmaq?", date(2024, 1, 1))]


def events(body: str) -> list[tuple[str, dict[str, object]]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_stream_sends_each_step_then_the_result() -> None:
    with client(FakeAgent()) as c:
        response = c.post("/v1/agent/ask/stream", json={"question": "İşdən çıxmaq?"})
    assert response.headers["content-type"].startswith("text/event-stream")
    got = events(response.text)
    assert [name for name, _ in got] == ["step", "step", "result"]
    assert got[1][1] == {
        "step": "research",
        "message": "Maddələr axtarılır...",
        "sub_questions": ["a"],
    }
    assert got[2][1]["answer"] == "Bir ay."


def test_stream_reports_failures_without_details() -> None:
    with client(FakeAgent(fail=True)) as c:
        response = c.post("/v1/agent/ask/stream", json={"question": "İşdən çıxmaq?"})
    got = events(response.text)
    assert got[-1] == ("error", {"detail": "the research run failed"})


def test_disabled_agent_is_a_503() -> None:
    with client(None) as c:
        assert c.post("/v1/agent/ask", json={"question": "sual?"}).status_code == 503
        assert c.post("/v1/agent/ask/stream", json={"question": "sual?"}).status_code == 503
