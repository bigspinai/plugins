#!/usr/bin/env python3
"""Multi-fixture preview for the share card — renders all 7 archetypes side-by-side.

Reuses the Showrunner fixture as a base, mutating ``view.title.archetype_name``
for each archetype. The card is extracted from each rendered report via
the ``@CARD_START``/``@CARD_END`` markers in the template.

Usage:
  python analysis/cards_preview.py
"""
from __future__ import annotations

import argparse
import copy
import logging
import re
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from render_report import (  # noqa: E402
    DEFAULT_SCHEMA,
    build_view,
    load_content,
    load_metrics,
)
from render import html as html_renderer  # noqa: E402

log = logging.getLogger("cards_preview")

ARCHETYPES = [
    "Showrunner",
    "Runtime Mechanic",
    "Quick-Turn Sprinter",
    "Pair Programmer",
    "Spec-First Architect",
    "Prompt Minimalist",
    "Multi-Mode Journeyman",
]

BASE_CONTENT = ROOT / "report" / "report_content.json"
BASE_METRICS = ROOT / "tests" / "fixtures" / "sample_metrics.manager.json"

CARD_RE = re.compile(r"<!-- @CARD_START -->(.*?)<!-- @CARD_END -->", re.S)
STYLE_RE = re.compile(r"<style>.*?</style>", re.S)
FONT_LINK_RE = re.compile(r"<link[^>]+fonts\.googleapis\.com[^>]+>", re.S)


def _free_port(start: int) -> int:
    for p in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise SystemExit(f"no free port in {start}..{start + 19}")


def _build_preview_html() -> str:
    content = load_content(BASE_CONTENT, DEFAULT_SCHEMA)
    metrics = load_metrics(BASE_METRICS)
    base_view = build_view(content, metrics)

    cards: list[tuple[str, str]] = []
    style_block = ""
    font_link = ""

    for arch in ARCHETYPES:
        v = copy.deepcopy(base_view)
        v["title"]["archetype_name"] = arch
        rendered = html_renderer.render(v, template_name="report_wrapped.html.j2")

        m = CARD_RE.search(rendered)
        if not m:
            raise RuntimeError(
                f"could not find @CARD_START/@CARD_END markers in render for {arch}"
            )
        cards.append((arch, m.group(1).strip()))

        if not style_block:
            sm = STYLE_RE.search(rendered)
            if sm:
                style_block = sm.group(0)
            fm = FONT_LINK_RE.search(rendered)
            if fm:
                font_link = fm.group(0)

    cards_html = "\n".join(
        f'<figure class="card-cell">'
        f'<figcaption>{arch}</figcaption>'
        f'{markup}'
        f'</figure>'
        for arch, markup in cards
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Card preview — all archetypes</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
{font_link}
{style_block}
<style>
  body {{
    background: #1F1E1B;
    padding: 40px 32px 80px;
    min-height: 100vh;
    overflow-y: auto;
    scroll-snap-type: none;
    height: auto;
  }}
  body::after {{ display: none; }}
  .preview-h {{
    font-family: 'Inter', -apple-system, sans-serif;
    color: #FAF7F0;
    font-weight: 600;
    font-size: 22px;
    letter-spacing: -0.01em;
    margin: 0 0 8px;
  }}
  .preview-sub {{
    font-family: 'Inter', -apple-system, sans-serif;
    color: rgba(250, 247, 240, 0.6);
    font-size: 13px;
    margin: 0 0 32px;
  }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(432px, 1fr));
    gap: 32px;
    max-width: 1640px;
    margin: 0 auto;
  }}
  .card-cell {{
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 12px;
    align-items: center;
  }}
  .card-cell figcaption {{
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: rgba(250, 247, 240, 0.55);
    padding-left: 4px;
    align-self: flex-start;
  }}
  .card-cell .share-card {{
    margin: 0;
    width: 432px;
    max-width: 100%;
  }}
</style>
</head>
<body>
  <h1 class="preview-h">Share card — all 7 archetypes</h1>
  <p class="preview-sub">Reusing the Showrunner fixture; only the archetype name varies.
    Tagline + description text comes from the in-template lookup dicts; illustration is per-archetype.</p>
  <div class="grid">
    {cards_html}
  </div>
</body>
</html>
"""


class State:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._html = ""
        self.rebuild()

    def rebuild(self) -> None:
        try:
            page = _build_preview_html()
        except Exception as exc:  # noqa: BLE001
            page = f"<pre>{type(exc).__name__}: {exc}</pre>"
            log.warning("build error: %s: %s", type(exc).__name__, exc)
        else:
            log.info("built preview (%d bytes)", len(page))
        with self._lock:
            self._html = page

    @property
    def html(self) -> str:
        with self._lock:
            return self._html


def _watch_loop(state: State, watched: list[Path]) -> None:
    from watchfiles import watch
    log.info("watcher started")
    for _changes in watch(*[str(p) for p in watched], recursive=False):
        time.sleep(0.05)  # let editor finish writing
        state.rebuild()


def _make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            log.debug("%s - %s", self.address_string(), fmt % args)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                body = state.html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    state = State()
    watched = [
        HERE / "render" / "templates" / "report_wrapped.html.j2",
        HERE / "render" / "html.py",
    ]
    threading.Thread(target=_watch_loop, args=(state, watched), daemon=True).start()

    port = _free_port(args.port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(state))
    url = f"http://localhost:{port}/"
    print(f"serving {url}")
    print("(Ctrl-C to stop)")

    if not args.no_open:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
