"""Markdown renderer — text-only artifact carrying the same content
as the HTML / CLI versions. Used for inline display in Claude Code
surfaces and as portable text.

No images, no charts. Trait comparisons are rendered as text bars
(``▌▎``); pullquote becomes a markdown blockquote; recognition lines
become an em-dash list. Everything else is straight markdown.

The asterisk convention from headings (``Your *fingerprint*``) is
already markdown-native, so the source strings pass through verbatim.
"""
from __future__ import annotations

from typing import Any


def _bar_chars(pct: float, width: int = 30) -> str:
    """Render a 0–100 percentage as a Unicode bar of ``width`` cells.
    Uses ``▌`` for filled cells and ``▎`` for fractional remainder."""
    if pct is None:
        return " " * width
    pct = max(0.0, min(100.0, float(pct)))
    full = int(width * pct / 100)
    remainder = (width * pct / 100) - full
    bar = "▌" * full
    if remainder >= 0.5 and full < width:
        bar += "▎"
        full += 1
    return bar + " " * (width - full)


def _render_trait_bar(trait: dict, width: int = 30) -> str:
    you = trait["you_pct"]
    cohort = trait["cohort_pct"]
    lines = [f"  you      {_bar_chars(you, width)}  {round(you):>3}%"]
    if cohort is not None:
        lines.append(f"  cohort   {_bar_chars(cohort, width)}  {round(cohort):>3}%")
        delta = trait.get("delta_pp")
        if delta is not None:
            sign = "+" if delta > 0 else ""
            lines.append(f"  vs cohort · {sign}{round(delta)}pp")
    return "\n".join(lines)


def _render_gauge(primary: dict, comparisons: list[dict],
                  width: int = 30) -> str:
    """Outcome gauge as text rows."""
    lines = []
    label_w = max(
        len(primary["label"]),
        *(len(c["label"]) for c in comparisons),
        4,
    )
    lines.append(
        f"  {primary['label']:<{label_w}}  "
        f"{_bar_chars(primary['value_raw'], width)}  "
        f"{primary['value_str']:>6}"
    )
    for c in comparisons:
        lines.append(
            f"  {c['label']:<{label_w}}  "
            f"{_bar_chars(c['value_raw'], width)}  "
            f"{c['value_str']:>6}"
        )
    return "\n".join(lines)


def render_landscape_ascii(landscape: dict, width: int = 60,
                           height: int = 14) -> str:
    """Render the 2x2 archetype landscape as a monospace grid.

    Shared by markdown and CLI renderers. ``width`` and ``height`` are
    total dimensions including the box frame; the inner plot area is
    ``(width - 4) × (height - 2)``. Returns the framed text.
    """
    inner_w = width - 4
    inner_h = height - 2
    cx = (inner_w - 1) / 2
    cy = (inner_h - 1) / 2

    grid = [[" "] * inner_w for _ in range(inner_h)]

    def to_inner(x: float, y: float) -> tuple[int, int]:
        col = int(round(cx + x * cx))
        row = int(round(cy - y * cy))  # y-flip for display
        return (max(0, min(inner_w - 1, col)),
                max(0, min(inner_h - 1, row)))

    def place_text(text: str, col: int, row: int) -> None:
        for i, ch in enumerate(text):
            c = col + i
            if 0 <= c < inner_w:
                grid[row][c] = ch

    # Place archetype dots + labels — only the visible ones (primary,
    # shadow, secondary, or all four primaries when Generalist).
    visible = [a for a in landscape["archetypes"] if a.get("is_visible")]
    for a in visible:
        col, row = to_inner(a["x"], a["y"])
        if a.get("is_shadow"):
            glyph = "◌"
        elif a.get("is_user_primary"):
            glyph = "●"
        else:
            glyph = "○"
        grid[row][col] = glyph

        label = a["short_name"]
        if a["x"] > 0.05:
            # label sits to the LEFT of the dot
            place_text(label, col - 1 - len(label), row)
        elif a["x"] < -0.05:
            # label sits to the RIGHT
            place_text(label, col + 2, row)
        else:
            place_text(" " + label, col + 1, row)

    # You marker — drawn last so it's on top.
    you_col, you_row = to_inner(landscape["you"]["x"], landscape["you"]["y"])
    grid[you_row][you_col] = "★"

    rows = ["".join(r) for r in grid]

    # Frame the grid with paired axis labels (ASCII can't rotate, so the
    # vertical axis gets a top-aligned label with ↑, and the horizontal
    # axis gets a centered label below the chart).
    lines: list[str] = []
    y_label = "↑ More iteration"
    x_label = "More structure →"
    lines.append("  " + y_label)
    lines.append("  ┌" + "─" * (width - 4) + "┐")
    for r in rows:
        lines.append("  │" + r + "│")
    lines.append("  └" + "─" * (width - 4) + "┘")
    pad = max(0, (width - len(x_label)) // 2)
    lines.append(" " * pad + x_label)
    return "\n".join(lines)


def _render_compass(view: dict) -> str:
    """Markdown wrapper around the shared ASCII landscape renderer.
    No prose framing — the hero block above introduces the user already.
    """
    landscape = view.get("compass")
    if not landscape:
        return ""
    return "```text\n" + render_landscape_ascii(landscape, width=58, height=14) + "\n```"


def render(view: dict[str, Any]) -> str:
    out: list[str] = []
    title = view["title"]
    # ----- HERO -----
    out.append(f"# The *{title['archetype_name']}* {title['modifier']}")
    out.append("")
    out.append(f"*{view['tagline']}*")
    out.append("")

    # Shadow (still inside the hero region)
    if view["shadow"]:
        sh = view["shadow"]
        out.append("> **Your shadow:** " + sh["name"] + ".  ")
        out.append("> *" + sh["tagline"] + "*  ")
        if sh["axis"]:
            out.append(f"> axis · {sh['axis']}")
        out.append("")

    # 2x2 — also part of the hero region
    compass_md = _render_compass(view)
    if compass_md:
        out.append(compass_md)
        out.append("")

    # ----- Pullquote (transition between hero and Section I) -----
    si = view["section_identity"]
    pq = si["pullquote"]
    out.append("> ## " + pq["text"])
    if pq.get("attribution"):
        out.append(">")
        out.append("> — *" + pq["attribution"] + "*")
    out.append("")

    # Recognition lines — color on the pullquote, before the formal section.
    if si.get("recognition_lines"):
        out.append("**You'll know this is you if…**")
        out.append("")
        for line in si["recognition_lines"]:
            out.append(f"— {line}")
        out.append("")

    # ----- Section I — Identity -----
    out.append(f"## — {si['section_label']} — {si['heading']}")
    out.append("")
    if si.get("lede"):
        out.append(f"*{si['lede']}*")
        out.append("")

    for i, t in enumerate(si["traits_view"], 1):
        out.append(f"### {i:02d}. *{t['name_em']}*")
        out.append("")
        out.append(t["characterization"])
        out.append("")
        out.append("```")
        out.append(_render_trait_bar(t))
        out.append("```")
        out.append("")

    # ----- Section II — Outcome -----
    so = view["section_outcome"]
    out.append(f"## — {so['section_label']} — {so['heading']}")
    out.append("")
    if so.get("lede"):
        out.append(f"*{so['lede']}*")
        out.append("")
    out.append(f"**{so['headline']}**")
    out.append("")
    out.append(so["body"])
    out.append("")
    out.append("```")
    out.append(_render_gauge(so["primary_view"], so["comparison_views"]))
    out.append("```")
    out.append("")

    # ----- Section III — Moves -----
    sm = view["section_moves"]
    out.append(f"## — {sm['section_label']} — {sm['heading']}")
    out.append("")
    if sm.get("lede"):
        out.append(f"*{sm['lede']}*")
        out.append("")
    for i, mv in enumerate(sm["moves"], 1):
        roman = "i" if i == 1 else "ii"
        out.append(f"### {roman}. From {mv['from_archetype']} — {mv['verb_phrase']}")
        out.append("")
        out.append(mv["body_lead"])
        out.append("")
        out.append(mv["body_action"])
        if mv.get("effect_size_note"):
            out.append("")
            out.append(f"*{mv['effect_size_note']}*")
        out.append("")

    # Reflection prompts
    if view.get("reflection_prompts"):
        out.append("## Three questions to *carry* into your next session")
        out.append("")
        for i, p in enumerate(view["reflection_prompts"], 1):
            roman = ["i", "ii", "iii", "iv", "v"][min(i - 1, 4)]
            out.append(f"{roman}. *{p}*")
        out.append("")

    # Colophon
    out.append("---")
    col = view["colophon"]
    line = "*Practice Mirror"
    if col["n_sessions"]:
        line += f" · {col['n_sessions']} sessions"
    if col["earliest"] and col["latest"]:
        line += f" · {col['earliest']} – {col['latest']}*"
    else:
        line += "*"
    out.append(line)
    if col["footnote"]:
        out.append("")
        out.append("*" + col["footnote"] + "*")

    return "\n".join(out) + "\n"
