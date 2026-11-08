"""Blind human labelling in the browser (F3): the same labels as ``mizan-eval label``.

The terminal session needs a working Python on the labeller's machine. This serves one page on
127.0.0.1 instead: question, gold provisions, the answer and its citations. It records a
yes/no per answer into the same ``human_labels.jsonl``. As in the terminal, the judge's
verdict and reason are never shown, so the label stays an independent measurement.

    mizan-eval label --labeller namiq --web        # then open http://127.0.0.1:18095
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from mizan_evals.labels import Label, append_label, load_labels
from mizan_evals.run import Record

PORT = 18095

_PAGE = """<!doctype html><html lang="az"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Mizan · F3</title>
<style>
body{{font:16px/1.55 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:16px;
background:#f7f7f5;color:#1d1d1b}}
@media (prefers-color-scheme:dark){{body{{background:#161615;color:#ececea}}
.box{{background:#1f1f1d!important;border-color:#34342f!important}}}}
.box{{background:#fff;border:1px solid #e3e2dd;border-radius:8px;padding:12px 14px;margin:10px 0;
white-space:pre-wrap;overflow-wrap:anywhere}}
h1{{font-size:18px}} h2{{font-size:14px;text-transform:uppercase;opacity:.7;margin:18px 0 4px}}
.bar{{display:flex;gap:10px;flex-wrap:wrap;position:sticky;bottom:0;padding:12px 0;
background:inherit}}
button{{font:inherit;font-weight:600;padding:12px 20px;border:0;border-radius:8px;cursor:pointer}}
.y{{background:#2f7d4a;color:#fff}} .n{{background:#b42318;color:#fff}}
.s{{background:#888;color:#fff}}
.help{{opacity:.8;font-size:14px}}
</style></head><body>{body}</body></html>"""

_HELP = (
    "<p class='help'><b>Düzgün</b>: əsas hüquqi nəticə etalon maddələrə uyğundur və nəticəni "
    "dəyişən şərt buraxılmayıb. <b>Səhv</b>: nəticə yanlışdır, vacib şərt buraxılıb və ya "
    "maddədə olmayan şərt əlavə edilib. Üsluba və uzunluğa baxma. Əmin deyilsənsə, keç.</p>"
)


def _item(record: Record, gold: str, done: int, total: int) -> str:
    e = html.escape
    cites = "\n".join(f"[{c['key']}] {c['quote']}" for c in record.citations) or "(yoxdur)"
    gold_keys = ", ".join(record.gold) or "yoxdur: sual Məcəllənin əhatəsindən kənardır"
    return (
        f"<h1>{done + 1} / {total} · {e(record.id)} · {e(record.type)} · "
        f"tarix {e(record.as_of)}</h1>"
        f"{_HELP}<h2>Sual</h2><div class='box'>{e(record.question)}</div>"
        f"<h2>Etalon maddələr ({e(gold_keys)})</h2><div class='box'>{e(gold) or '—'}</div>"
        f"<h2>Cavab</h2><div class='box'>{e(record.answer)}</div>"
        f"<h2>Sitatlar</h2><div class='box'>{e(cites)}</div>"
        "<form method='post' class='bar'>"
        f"<input type='hidden' name='id' value='{e(record.id)}'>"
        "<button class='y' name='v' value='y'>Düzgün (y)</button>"
        "<button class='n' name='v' value='n'>Səhv (n)</button>"
        "<button class='s' name='v' value='s'>Keç (s)</button></form>"
        "<script>document.addEventListener('keydown',e=>{const b=document.querySelector("
        "`button[value='${e.key}']`);if(b&&['y','n','s'].includes(e.key))b.click()})</script>"
    )


def serve(
    records: dict[str, Record],
    path: Path,
    *,
    labeller: str,
    gold_text: Callable[[Record], str],
    port: int = PORT,
) -> None:
    todo = sorted((r for r in records.values() if r.error is None), key=lambda r: r.id)
    golds = {r.id: gold_text(r) for r in todo}  # once, before serving: no DB per click
    skipped: set[str] = set()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: str, status: int = 200) -> None:
            data = _PAGE.format(body=body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            done = load_labels(path)
            pending = [r for r in todo if r.id not in done and r.id not in skipped]
            if not pending:
                self._send(
                    f"<h1>Hazırdır</h1><p>{len(done)} cavab işarələndi. Bu pəncərəni bağla və "
                    "Claude-a «bitdi» yaz.</p>"
                )
                return
            self._send(_item(pending[0], golds[pending[0].id], len(done), len(todo)))

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            record_id = form.get("id", [""])[0]
            verdict = form.get("v", [""])[0]
            if record_id in records and verdict in {"y", "n"}:
                stamp = datetime.now(UTC).isoformat(timespec="seconds")
                append_label(path, Label(record_id, verdict == "y", labeller, stamp))
            elif verdict == "s":
                skipped.add(record_id)
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(json.dumps({"url": f"http://127.0.0.1:{port}", "items": len(todo)}), flush=True)
    server.serve_forever()
