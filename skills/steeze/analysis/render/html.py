"""HTML renderer — Kinfolk register, mobile-vertical, single-file.

Reads the resolved view (see ``render_report.build_view``) and produces a
self-contained HTML document with inline CSS, inline SVG bars, and an
inline SVG compass map. No external assets required at render time;
fonts load from a CDN (Fraunces / Inter via Google Fonts) — graceful
fallback to system serifs offline.

Future iteration (v3.1): ship subset .woff2 in ``assets/fonts/`` and
switch the @font-face declarations to local URLs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import re

import jinja2
from markupsafe import Markup, escape

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"


def _bar_pct(value: float | None) -> float:
    """Clamp a 0-100 value for SVG bar widths."""
    if value is None:
        return 0.0
    return max(0.0, min(100.0, float(value)))


_EM_RE = re.compile(r"\*(.+?)\*")


def _emify(s: str) -> Markup:
    """Convert markdown-style ``*word*`` markers to ``<em>word</em>``.

    Headings in the content JSON use this convention so the LLM can
    indicate which word to italicize without splitting the string into
    pre/em/post fields. Output is marked safe; everything outside the
    em-spans is escaped to prevent injection."""
    parts = []
    last = 0
    for m in _EM_RE.finditer(s):
        parts.append(escape(s[last:m.start()]))
        parts.append(Markup("<em>") + escape(m.group(1)) + Markup("</em>"))
        last = m.end()
    parts.append(escape(s[last:]))
    return Markup("").join(parts)


def render(view: dict[str, Any], template_name: str = "report.html.j2") -> str:
    """Render the resolved view to a complete HTML document."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        autoescape=jinja2.select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["bar_pct"] = _bar_pct
    env.filters["emify"] = _emify
    tmpl = env.get_template(template_name)

    # Pass unit-square coords (-1..1) through to the template; jinja
    # handles the geometry math so layout tweaks don't require Python
    # changes.
    landscape = view.get("compass")  # field still named compass in the view dict
    return tmpl.render(view=view, landscape=landscape)
