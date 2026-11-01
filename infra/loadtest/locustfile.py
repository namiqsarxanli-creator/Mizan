"""User behaviour for the F11 load test.

Each simulated user waits 1–3 s between requests, like a person reading results, and mixes:

| share | request | what it exercises |
|---|---|---|
| 50% | `GET /v1/search` | embedding + Qdrant + article expansion |
| 30% | `POST /v1/ask` | the above + screen + (stub) LLM + quality log + budget |
| 10% | `POST /v1/contracts/analyze` (rules only) | upload, clause split, rules |
| 10% | `GET /health/ready` | every dependency check |

Questions are the 281 human questions of the v1 eval set, sampled at random.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from locust import HttpUser, between, task

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = [
    json.loads(line)["question"]
    for line in (ROOT / "evals/datasets/v1/questions.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip()
]
CONTRACT = (ROOT / "evals/datasets/contracts/v1/c04.txt").read_bytes()


class Visitor(HttpUser):
    wait_time = between(1, 3)

    @task(5)
    def search(self) -> None:
        self.client.get("/v1/search", params={"q": random.choice(QUESTIONS)}, name="search")  # noqa: S311

    @task(3)
    def ask(self) -> None:
        self.client.post("/v1/ask", json={"question": random.choice(QUESTIONS)}, name="ask")  # noqa: S311

    @task(1)
    def contract(self) -> None:
        self.client.post(
            "/v1/contracts/analyze",
            files={"file": ("c04.txt", CONTRACT, "text/plain")},
            data={"llm": "false"},
            name="contracts (rules)",
        )

    @task(1)
    def ready(self) -> None:
        self.client.get("/health/ready", name="ready")
