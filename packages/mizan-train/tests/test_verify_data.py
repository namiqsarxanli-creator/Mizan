"""Constructed verification claims and their judge check, against a scripted LLM."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mizan_train.verify_data import KINDS, load_claims, synthesise_claims

CLAIMS = {
    "restated": "Sınaq müddəti üç aydan artıq ola bilməz.",
    "altered": "Sınaq müddəti altı aya qədər ola bilər.",
    "absent": "İşçiyə illik məzuniyyət 30 gündür.",
    "extended": "Sınaq müddəti üç aydan artıq ola bilməz və yazılı ərizə ilə uzadılır.",
}


class LLM:
    def __init__(self, labels: dict[str, list[str]]) -> None:
        self.chat = SimpleNamespace(completions=self)
        self.calls: list[str] = []
        self._labels = labels

    async def create(self, **request: Any) -> Any:
        prompt = request["messages"][0]["content"]
        if "İddialar:" in prompt:  # the judge
            self.calls.append(f"judge:{request['model']}")
            payload: object = {
                "items": [{"id": i, "labels": labels} for i, labels in self._labels.items()]
            }
        else:
            self.calls.append(f"generate:{request['model']}")
            payload = {
                "items": [
                    {"id": "A1", **CLAIMS},
                    {"id": "A2", **CLAIMS, "absent": "qısa"},  # too short: article dropped
                ]
            }
        message = SimpleNamespace(content="Cavab: " + json.dumps(payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


ARTICLES = [("art:51", "Sınaq müddəti üç aydan çox ola bilməz."), ("art:52", "Başqa maddə.")]


async def test_claims_get_labels_by_construction_and_a_judge_filter(tmp_path: Path) -> None:
    # The judge accepts three claims and calls the extended one supported (a disagreement).
    llm = LLM({"A1": ["supported", "contradicted", "not_enough_info", "supported"]})
    out = tmp_path / "claims.jsonl"

    claims = await synthesise_claims([(llm, "gen")], (llm, "judge"), ARTICLES, out)

    assert [(c.kind, c.label, c.judge_label, c.kept) for c in claims] == [
        ("restated", "supported", "supported", True),
        ("altered", "contradicted", "contradicted", True),
        ("absent", "not_enough_info", "not_enough_info", True),
        ("extended", "not_enough_info", "supported", False),
    ]
    assert {c.article for c in claims} == {"art:51"}
    assert all(c.label == KINDS[c.kind] and c.generator == "gen" for c in claims)
    assert load_claims(out) == claims

    again = await synthesise_claims([(llm, "gen")], (llm, "judge"), ARTICLES[:1], out)
    assert again == claims
    assert llm.calls == ["generate:gen", "judge:judge"]  # resumed: nothing regenerated


async def test_a_label_list_of_the_wrong_length_is_not_recorded(tmp_path: Path) -> None:
    llm = LLM({"A1": ["supported", "contradicted"]})  # four claims, two labels
    claims = await synthesise_claims([(llm, "gen")], (llm, "judge"), ARTICLES, tmp_path / "c.jsonl")
    assert claims == []  # redone on the next run, not read as disagreement
