"""Follow-up questions restated as standalone questions."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from mizan_retrieval.condense import MAX_ANSWER_CHARS, MAX_TURNS, GroqQuestionCondenser, Turn


class FakeCompletions:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        message = SimpleNamespace(content=self.reply)
        usage = SimpleNamespace(total_tokens=42)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def condenser(reply: str | Exception) -> tuple[GroqQuestionCondenser, FakeCompletions]:
    completions = FakeCompletions(reply)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return GroqQuestionCondenser(client, model="openai/gpt-oss-20b"), completions


HISTORY = [Turn("Hamilə işçini ixtisar etmək olarmı?", "Xeyr, 79-cu maddəyə görə qadağandır.")]


async def test_a_follow_up_is_restated_and_its_tokens_counted() -> None:
    reply = json.dumps({"question": "Uşağı 4 yaşında olan qadını ixtisar etmək olarmı?"})
    c, calls = condenser(reply)

    out = await c.condense("Bəs uşağı 4 yaşındadırsa?", HISTORY)

    assert out.question == "Uşağı 4 yaşında olan qadını ixtisar etmək olarmı?"
    assert out.tokens == 42
    payload = json.loads(calls.calls[0]["messages"][1]["content"])
    assert payload["follow_up"] == "Bəs uşağı 4 yaşındadırsa?"
    assert payload["history"][0]["question"] == HISTORY[0].question


async def test_a_first_question_needs_no_call() -> None:
    c, calls = condenser("{}")
    out = await c.condense("Sınaq müddəti neçə aydır?", [])
    assert out.question == "Sınaq müddəti neçə aydır?"
    assert calls.calls == []


    replies: list[str | Exception] = [RuntimeError("429"), "not json", json.dumps({"question": ""})]
    for reply in replies:
    for reply in [RuntimeError("429"), "not json", json.dumps({"question": ""})]:
        c, _ = condenser(reply)
        out = await c.condense("Bəs 4 yaşındadırsa?", HISTORY)
        assert out.question == "Bəs 4 yaşındadırsa?"


async def test_only_recent_turns_and_short_answers_are_sent() -> None:
    history = [Turn(f"sual {i}", "c" * 5000) for i in range(MAX_TURNS + 3)]
    c, calls = condenser(json.dumps({"question": "müstəqil sual"}))
    await c.condense("bəs?", history)
    sent = json.loads(calls.calls[0]["messages"][1]["content"])["history"]
    assert [t["question"] for t in sent] == [f"sual {i}" for i in range(3, MAX_TURNS + 3)]
    assert all(len(t["answer"]) == MAX_ANSWER_CHARS for t in sent)
