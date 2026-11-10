"""Start Mizan and open it in the browser: run this file (in PyCharm: right-click -> Run).

Uses only the standard library, so any Python 3 interpreter works; the project's own
environment is not needed. The stack runs in Docker, so Docker Desktop must be running.
The first start builds the images and downloads the embedding model (about 2 GB) into a
Docker volume, which takes several minutes; later starts take about a minute.

Stop the API afterwards with:  docker compose stop api
"""

import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = os.environ.get("MIZAN_HOST_PORT_API", "18000")
BASE = f"http://127.0.0.1:{PORT}"
TIMEOUT_MINUTES = 30


def ready() -> bool:
    try:
        # A fixed http:// URL to the local API, never a user-supplied scheme.
        with urllib.request.urlopen(f"{BASE}/health/ready", timeout=3) as response:  # noqa: S310
            return bool(response.status == 200)
    except OSError:
        return False


def main() -> int:
    docker = shutil.which("docker")
    if docker is None:
        print("Docker was not found. Install and start Docker Desktop, then run again.")
        return 1
    env_file = ROOT / ".env"
    if not env_file.exists():
        shutil.copy(ROOT / ".env.example", env_file)
        print("Created .env from .env.example: add your Groq key (MIZAN_LLM__GROQ_API_KEY).")

    if ready():
        print("Mizan is already running.")
    else:
        print("Starting the stack in Docker (docker compose up -d --build api)...", flush=True)
        command = [docker, "compose", "up", "-d", "--build", "api"]  # fixed arguments
        result = subprocess.run(command, cwd=ROOT, check=False)  # noqa: S603
        if result.returncode != 0:
            print("docker compose failed. Is Docker Desktop running?")
            return result.returncode
        print("Waiting for the API to load its models...", flush=True)
        deadline = time.monotonic() + TIMEOUT_MINUTES * 60
        while not ready():
            if time.monotonic() > deadline:
                print("The API did not become ready. See: docker compose logs api")
                return 1
            print(".", end="", flush=True)
            time.sleep(5)
        print()

    print("Mizan is ready.")
    print(f"  Contract check:  {BASE}/contracts")
    print(f"  Ask a question:  {BASE}/docs  (POST /v1/ask -> Try it out)")
    print("Stop it with:  docker compose stop api")
    webbrowser.open(f"{BASE}/contracts")
    webbrowser.open(f"{BASE}/docs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
