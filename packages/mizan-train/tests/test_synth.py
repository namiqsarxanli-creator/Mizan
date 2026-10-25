"""Synthetic question generation with a round-trip check, against a scripted LLM."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mizan_train.synth import load_pairs, synthesise

QUESTIONS = [
    "Sınaq müddəti ən çox nə qədərdir?",
    "Qısa?",  # too short to be a question: dropped
    "Azyaşlıya sınaq müddəti qoymaq olar?",
    "Sınaq müddəti uzadıla bilərmi?",
]


class LLM:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=self)
        self.calls: list[str] = []

    async def create(self, **request: Any) -> Any:
        prompt = request["messages"][0]["content"]
        if "Suallar:" in prompt:  # the round-trip judge
            self.calls.append(f"judge:{request['model']}")
            payload: object = {"answerable": [True, False]}  # no verdict for the third question
        else:
            self.calls.append(f"generate:{request['model']}")
            payload = {"questions": QUESTIONS}
        # Models often wrap JSON in prose; the parser takes the object out.
        message = SimpleNamespace(content="Cavab: " + json.dumps(payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


async def test_questions_are_generated_checked_and_checkpointed(tmp_path: Path) -> None:
    llm = LLM()
    out = tmp_path / "pairs.jsonl"
    articles = [("art:51", "Sınaq müddəti üç aydan çox ola bilməz.")]

    pairs = await synthesise(llm, articles, out, generator="big", judge="small", n=3)

    assert [(p.question, p.kept) for p in pairs] == [
        ("Sınaq müddəti ən çox nə qədərdir?", True),
        ("Azyaşlıya sınaq müddəti qoymaq olar?", False),
        ("Sınaq müddəti uzadıla bilərmi?", False),  # unverified -> rejected
    ]
    assert llm.calls == ["generate:big", "judge:small"]  # one judge call per article
    assert load_pairs(out) == pairs

    again = await synthesise(llm, articles, out, generator="big", judge="small", n=3)
    assert again == pairs
    assert len(llm.calls) == 2  # resumed: nothing regenerated
