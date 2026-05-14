"""Hero markdown — a tight, markdown-native version of the hero card.

Designed for inline display in chat surfaces that render rich markdown
(Claude Code Desktop, web, IDE plugins). The CLI card with its ASCII
box-drawing is a downgrade in those contexts; this rendering uses the
chat surface's native formatting instead.

About 10 lines: title, tagline, pullquote, shadow, and a single CTA
pointing at the full HTML report. Nothing else — the orchestrator
should paste this and stop, not dump the full markdown report or the
file inventory. The HTML is the main artifact; this is the recognition
moment plus the door.

The plain-text CLI card (cli.py) remains the right artifact for plain
terminals.
"""
from __future__ import annotations

from typing import Any


def render(view: dict[str, Any]) -> str:
    title = view["title"]
    tagline = view["tagline"]
    pullquote = view["section_identity"]["pullquote"]
    shadow = view.get("shadow")

    out: list[str] = []
    out.append("**Your Claude Code archetype**")
    out.append("")
    out.append(f"# The *{title['archetype_name']}* {title['modifier']}")
    out.append("")
    out.append(f"*{tagline}*")
    out.append("")
    out.append(f"> **{pullquote['text']}**")
    out.append("")
    if shadow:
        out.append(
            f"**Your shadow:** {shadow['name']} — "
            f"*{shadow['tagline']}*"
        )
        out.append("")
    out.append("---")
    out.append("")
    out.append(
        "**Your full report is ready** — a clean, screenshot-friendly "
        "page with your traits, cohort comparisons, and two moves to try. "
        "Just say *yes* and I'll open it for you."
    )
    out.append("")

    return "\n".join(out) + "\n"
