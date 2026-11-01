"""Run the F11 load test: 10, 50 and 100 concurrent users against one API process.

    python infra/loadtest/run.py [--duration 120] [--out evals/results/f11_load]

Starts the stub-LLM API (``app.py``) with the compose stack behind it, waits for readiness,
then runs Locust headless once per level. Users ramp up at 10 per second and run for the given
duration. Writes the per-endpoint p50/p95/p99, throughput and error rate to
``report.json``. The rate limit is lifted (one anonymous subject would otherwise be throttled
at 60 requests a minute), and online evaluation is off (it would call the real LLM).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
PORT = 18090
LEVELS = (10, 50, 100)


def _wait_ready(url: str, timeout_s: float = 300) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310  local URL
                if r.status == 200:
                    return
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit(f"API not ready at {url}")


def _stats(path: Path) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row["Name"]
            out[name] = {
                "requests": int(row["Request Count"]),
                "failures": int(row["Failure Count"]),
                "rps": round(float(row["Requests/s"]), 2),
                "p50_ms": float(row["50%"]),
                "p95_ms": float(row["95%"]),
                "p99_ms": float(row["99%"]),
                "max_ms": float(row["Max Response Time"]),
            }
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=120, help="seconds per level")
    parser.add_argument("--out", type=Path, default=ROOT / "evals/results/f11_load")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    env = os.environ | {
        "MIZAN_AUTH__REQUESTS_PER_MINUTE": "0",
        "MIZAN_QUALITY__ONLINE_EVAL_SAMPLE_RATE": "0",
        "MIZAN_LOG__LEVEL": "WARNING",
        "PYTHONPATH": str(ROOT),
    }
    server = subprocess.Popen(  # noqa: S603  fixed local command
        [sys.executable, "-m", "uvicorn", "infra.loadtest.app:create", "--factory",
         "--port", str(PORT), "--log-level", "warning", "--no-access-log"],
        cwd=ROOT, env=env,
    )  # fmt: skip
    base = f"http://127.0.0.1:{PORT}"
    try:
        _wait_ready(f"{base}/health/ready")
        # Warm-up: load the embedder and fill connection pools before measuring.
        subprocess.run(  # noqa: S603
            [sys.executable, "-m", "locust", "-f", str(HERE / "locustfile.py"), "--headless",
             "-u", "5", "-r", "5", "-t", "20s", "--host", base, "--only-summary"],
            cwd=ROOT, check=True, capture_output=True,
        )  # fmt: skip
        levels: dict[str, dict[str, dict[str, float]]] = {}
        for users in LEVELS:
            prefix = args.out / f"u{users}"
            subprocess.run(  # noqa: S603
                [sys.executable, "-m", "locust", "-f", str(HERE / "locustfile.py"),
                 "--headless", "-u", str(users), "-r", "10", "-t", f"{args.duration}s",
                 "--host", base, "--csv", str(prefix), "--only-summary"],
                cwd=ROOT, check=True, capture_output=True,
            )  # fmt: skip
            levels[str(users)] = _stats(Path(f"{prefix}_stats.csv"))
            print(users, json.dumps(levels[str(users)]["Aggregated"]), flush=True)
            time.sleep(10)
        report = {
            "duration_s": args.duration,
            "llm_stub_ms": float(env.get("MIZAN_LOADTEST_LLM_MS", "1600")),
            "server": "1 uvicorn worker (as in the Docker image), stub LLM, compose stack",
            "machine": {
                "os": platform.platform(),
                "cpu_count": os.cpu_count(),
                "python": platform.python_version(),
            },
            "levels": levels,
        }
        (args.out / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    finally:
        server.terminate()
        server.wait(timeout=30)


if __name__ == "__main__":
    main()
