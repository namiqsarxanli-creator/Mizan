"""F12 diagnostic: does the gold article reach the answering model?

Found while checking ``q001``: search ranks Art. 69 fifth, but ``/v1/ask`` expands every hit to
its whole article and then cuts the context at ``llm.max_context_chars`` (9000). Art. 77 alone
is ~6200 characters, so Art. 69 never reaches the model. This measures how often that happens,
with no LLM calls: the serving search (k = 6), whole-article expansion, then the same budget.

    uv run python evals/results/f12_colloquial/context.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mizan_core.config import get_settings
from mizan_ingestion.chunking import Strategy
from mizan_retrieval.answer import fit_to_budget
from mizan_retrieval.cli import LABOUR_CODE, _embedders, _qdrant
from mizan_retrieval.evaluation import bootstrap, load_dataset
from mizan_retrieval.search import Mode, SearchConfig, Searcher, expand_to_articles

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
SETS = {
    "colloquial": ROOT / "evals/datasets/colloquial/questions.jsonl",
    "v1": ROOT / "evals/datasets/v1/questions.jsonl",
}
CONFIG = SearchConfig(Mode.DENSE, dense_model="bge-m3", strategy=Strategy.ARTICLE, refs=True)
K = 6  # AskRequest.k default


async def main() -> None:
    settings = get_settings()
    budget = settings.llm.max_context_chars
    client = _qdrant(settings)
    searcher = Searcher(client, _embedders(settings, ["bge-m3"]), None)
    lines = [f"Budget {budget} characters, k = {K}, whole-article expansion (as served).", ""]
    lines += ["| Set | n | gold retrieved (top 6) | gold reaches the model | lost to the budget |"]
    lines += ["|---|---|---|---|---|"]
    out: dict[str, object] = {}
    try:
        for name, path in SETS.items():
            questions = [q for q in load_dataset(path) if q.relevant]
            retrieved, reached, lost = [], [], []
            for q in questions:
                hits = await searcher.search(
                    q.question, source_id=LABOUR_CODE, as_of=q.as_of, config=CONFIG, k=K
                )
                found = {h.article_key for h in hits} & q.relevant
                articles = await expand_to_articles(
                    client, hits, source_id=LABOUR_CODE, as_of=q.as_of
                )
                given = {h.article_key for h in fit_to_budget(articles, budget)} & q.relevant
                retrieved.append(float(bool(found)))
                reached.append(float(bool(given)))
                if found and not given:
                    lost.append(q.id)
            r, g = bootstrap(retrieved), bootstrap(reached)
            missing = f"{len(lost)}: {', '.join(lost)}"
            lines.append(f"| {name} | {len(questions)} | {r} | {g} | {missing} |")
            out[name] = {"retrieved": r.mean, "reached": g.mean, "lost": lost}
    finally:
        await client.close()
    (HERE / "context.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (HERE / "context.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
