"""Build the self-contained training bundle for the GPU pod (run from the Mizan root)."""

import json
import shutil
import sys
from pathlib import Path

from mizan_retrieval import snapshot
from mizan_retrieval.cli import DEFAULT_SNAPSHOT
from mizan_train.cli import DATA, current_articles

out = Path(sys.argv[1])
if out.exists():
    shutil.rmtree(out)
(out / "src").mkdir(parents=True)
for pkg, mod in [
    ("mizan-core", "mizan_core"),
    ("mizan-ingestion", "mizan_ingestion"),
    ("mizan-train", "mizan_train"),
]:
    shutil.copytree(
        Path("packages") / pkg / "src" / mod,
        out / "src" / mod,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
articles = dict(current_articles(snapshot.read(DEFAULT_SNAPSHOT)))
(out / "articles.json").write_text(json.dumps(articles, ensure_ascii=False), encoding="utf-8")
shutil.copy(DATA / "examples.jsonl", out / "examples.jsonl")
for script in ("pod_train.py", "pod_test.py"):
    shutil.copy(Path(__file__).parent / script, out / script)
print(len(articles), "articles")
