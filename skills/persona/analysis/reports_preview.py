#!/usr/bin/env python3
"""Multi-archetype preview — serves the full 6-slide report for each of
the 7 archetypes from a single shared base fixture.

Parallel to ``cards_preview.py`` but renders the whole report (not just
the share card). Uses the Showrunner fixture as the base data, then mutates
the view per request so each archetype's report reflects that persona:
the archetype name (which drives taglines, blurbs, illustration), the
landscape primary/shadow (which drives the bar markers on slide 2), and
the per-bar flags.

Caveats:
  - The compare-line stat ("You wrote a numbered plan in 67%...") still
    comes from the Showrunner fixture's signals — the % is correct for
    Showrunner, not for the previewed archetype. Fine for layout/voice
    preview; not a substitute for a real fixture.
  - Multi-Mode Journeyman has no shadow, so its slide 2 won't render a Shadow row.

Routes:
  GET /                  — index page with links to each archetype
  GET /<slug>            — full report for that archetype
  GET /__health,/__reload — hot-reload plumbing

Usage:
  python analysis/reports_preview.py
"""
from __future__ import annotations

import argparse
import copy
import html
import logging
import queue
import socket
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from render_report import (  # noqa: E402
    DEFAULT_SCHEMA,
    RefError,
    build_view,
    load_content,
    load_metrics,
)
from render import html as html_renderer  # noqa: E402

log = logging.getLogger("reports_preview")

# slug → display name. Order here = display order on the index page.
ARCHETYPES: list[tuple[str, str]] = [
    ("showrunner", "Showrunner"),
    ("runtime-mechanic", "Runtime Mechanic"),
    ("quick-turn-sprinter", "Quick-Turn Sprinter"),
    ("pair-programmer", "Pair Programmer"),
    ("spec-first-architect", "Spec-First Architect"),
    ("prompt-minimalist", "Prompt Minimalist"),
    ("multi-mode-journeyman", "Multi-Mode Journeyman"),
]
NAME_BY_SLUG: dict[str, str] = {slug: name for slug, name in ARCHETYPES}

# Per-archetype shadow archetype, lifted from baselines/archetype_profiles.json.
# Multi-Mode Journeyman has no shadow.
SHADOW_BY_NAME: dict[str, str] = {
    "The Showrunner": "The Quick-Turn Sprinter",
    "The Quick-Turn Sprinter": "The Showrunner",
    "The Pair Programmer": "The Spec-First Architect",
    "The Spec-First Architect": "The Pair Programmer",
    "The Runtime Mechanic": "The Prompt Minimalist",
    "The Prompt Minimalist": "The Runtime Mechanic",
    "The Multi-Mode Journeyman": "",
}

BASE_CONTENT = ROOT / "report" / "report_content.json"
BASE_METRICS = ROOT / "tests" / "fixtures" / "sample_metrics.manager.json"

RELOAD_SNIPPET = """
<script>
(function () {
  let es;
  function connect() {
    es = new EventSource('/__reload');
    es.onmessage = function () { location.reload(); };
    es.onerror = function () {
      es.close();
      const tick = setInterval(function () {
        fetch('/__health', { cache: 'no-store' })
          .then(function (r) { if (r.ok) { clearInterval(tick); location.reload(); } })
          .catch(function () { /* server still down, keep polling */ });
      }, 500);
    };
  }
  connect();
})();
</script>
"""


def _err_page(exc: BaseException) -> str:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>render error</title>
<style>
  body {{ background: #1F1E1B; color: #FAF7F0;
    font: 14px/1.5 -apple-system, system-ui, sans-serif;
    padding: 36px 24px; max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 18px; color: #FF8A75; margin-bottom: 8px; }}
  pre {{ background: #2A2A28; border-radius: 8px; padding: 16px;
    font: 12px/1.5 ui-monospace, Menlo, monospace;
    white-space: pre-wrap; word-wrap: break-word; }}
</style></head><body>
<h1>render error</h1>
<pre>{html.escape(tb)}</pre>
{RELOAD_SNIPPET}
</body></html>"""


def _inject_reload(rendered: str) -> str:
    if "</body>" in rendered:
        return rendered.replace("</body>", RELOAD_SNIPPET + "</body>", 1)
    return rendered + RELOAD_SNIPPET


def _override_archetype(base_view: dict, archetype_short: str) -> dict:
    """Deep-copy the base view and mutate it to reflect a given archetype.

    Updates ``view.title.archetype_name`` (drives taglines / blurbs /
    illustration lookups) and the landscape primary / shadow / bar flags
    so slide 2 marks the right rows.
    """
    v = copy.deepcopy(base_view)
    full_name = "The " + archetype_short
    v["title"]["archetype_name"] = archetype_short

    landscape = v.get("compass")
    if landscape:
        landscape["primary"] = full_name
        shadow = SHADOW_BY_NAME.get(full_name, "")
        landscape["shadow"] = shadow
        landscape["is_generalist"] = (archetype_short == "Multi-Mode Journeyman")
        for bar in landscape.get("bars", []):
            bar["is_user_primary"] = (bar["name"] == full_name)
            bar["is_shadow"] = bool(shadow) and (bar["name"] == shadow)
    return v


class State:
    """Caches the base view; re-renders on demand per request."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._base_view: dict | None = None
        self._error: str = ""
        self._subscribers: list[queue.Queue[str]] = []
        self._sub_lock = threading.Lock()
        self.refresh()

    def refresh(self) -> None:
        try:
            content = load_content(BASE_CONTENT, DEFAULT_SCHEMA)
            metrics = load_metrics(BASE_METRICS)
            view = build_view(content, metrics)
        except (SystemExit, RefError, Exception) as exc:  # noqa: BLE001
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"
                self._base_view = None
            log.warning("base-view error: %s", self._error)
            return
        with self._lock:
            self._error = ""
            self._base_view = view
        log.info("base view rebuilt")

    def render_archetype(self, slug: str) -> str:
        with self._lock:
            base = self._base_view
            error = self._error
        if base is None:
            return _err_page(RuntimeError(error or "base view not loaded"))
        try:
            display = NAME_BY_SLUG[slug]
            view = _override_archetype(base, display)
            rendered = html_renderer.render(view, template_name="report_wrapped.html.j2")
            return _inject_reload(rendered)
        except Exception as exc:  # noqa: BLE001
            return _err_page(exc)

    def render_index(self) -> str:
        rows = "\n".join(
            f'<li><a href="/{slug}">The {name}</a></li>'
            for slug, name in ARCHETYPES
        )
        return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Report preview — all archetypes</title>
<style>
  body {{ background: #1F1E1B; color: #FAF7F0;
    font: 16px/1.5 -apple-system, system-ui, sans-serif;
    padding: 48px 32px; max-width: 720px; margin: 0 auto; }}
  h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 8px; }}
  p {{ color: rgba(250, 247, 240, 0.65); margin: 0 0 32px; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ margin-bottom: 4px; }}
  a {{ color: #93F099; text-decoration: none; font-size: 18px;
    display: inline-block; padding: 8px 0; }}
  a:hover {{ color: #FAF7F0; }}
  small {{ display: block; color: rgba(250, 247, 240, 0.4);
    font-size: 12px; margin-top: 40px; }}
</style></head><body>
<h1>Full report — each archetype</h1>
<p>Same base fixture across all seven; each page mutates the view to mark that archetype as primary (and its shadow on slide 2). The compare-line stat reflects the underlying Showrunner fixture's signal data — it's correct for Showrunner, approximate for the others.</p>
<ul>
{rows}
</ul>
{RELOAD_SNIPPET}
</body></html>"""

    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue()
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self._sub_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def broadcast_reload(self) -> None:
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            q.put("reload")


def _watch_loop(state: State, watched: list[Path]) -> None:
    from watchfiles import watch
    log.info("watcher started")
    for _changes in watch(*[str(p) for p in watched], recursive=False):
        time.sleep(0.05)
        state.refresh()
        state.broadcast_reload()


def _make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _send_html(self, body_str: str) -> None:
            body = body_str.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0].strip("/")
            if path in ("", "index.html"):
                self._send_html(state.render_index())
                return
            if path in NAME_BY_SLUG:
                self._send_html(state.render_archetype(path))
                return
            if self.path == "/__health":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b"ok")
                return
            if self.path == "/__reload":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q = state.subscribe()
                try:
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            msg = q.get(timeout=15)
                            self.wfile.write(f"data: {msg}\n\n".encode())
                            self.wfile.flush()
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    state.unsubscribe(q)
                return
            self.send_response(404)
            self.end_headers()

    return Handler


def _free_port(start: int) -> int:
    for p in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise SystemExit(f"no free port in {start}..{start + 19}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8780)
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
        HERE / "render_report.py",
        BASE_CONTENT,
        BASE_METRICS,
    ]
    threading.Thread(target=_watch_loop, args=(state, watched), daemon=True).start()

    port = _free_port(args.port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(state))
    url = f"http://localhost:{port}/"
    print(f"serving {url}")
    for slug, name in ARCHETYPES:
        print(f"  http://localhost:{port}/{slug:24s} — The {name}")
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
