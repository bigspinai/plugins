"""CLI hero card — the in-flow hit shown when analysis completes.

~25 lines of box-drawn Unicode plus ANSI color. Skips the compass
(that lives in the HTML report). Two outputs: ``hero_card.txt`` (with
ANSI), ``hero_card.plain.txt`` (without). Renderer takes ``ansi=True/False``.

ANSI is gated on ``isatty() + NO_COLOR`` only when this module is the
direct stdout target. Since we always write to a file, the gating
happens at the orchestrator (``cat hero_card.txt`` vs.
``cat hero_card.plain.txt``).
"""
from __future__ import annotations

from typing import Any

from .markdown import render_landscape_ascii

# 24-bit ANSI escape codes — the Kinfolk palette translated.
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITAL = "\033[3m"

# Foreground colors (Kinfolk palette)
INK = "\033[38;2;26;24;22m"           # near-black ink
INK_SOFT = "\033[38;2;58;53;46m"      # soft ink
MUTED = "\033[38;2;124;113;103m"      # muted brown
RULE = "\033[38;2;201;191;174m"       # rule line tan
ACCENT = "\033[38;2;184;65;47m"       # terracotta
ACCENT_SOFT = "\033[38;2;217;118;74m" # soft terracotta
GOOD = "\033[38;2;90;122;61m"         # olive green

# Width
W = 64


def _wrap(text: str, width: int, indent: str = "") -> list[str]:
    """Naive word-wrap for narrow CLI columns."""
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    cur = indent
    for w in words:
        if not cur.strip():
            cur += w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = indent + w
    if cur:
        lines.append(cur)
    return lines


def _ansi(s: str, *codes: str, on: bool = True) -> str:
    if not on or not codes:
        return s
    return "".join(codes) + s + RESET


def _bar(pct: float, width: int = 30, on: bool = True,
         color: str = ACCENT) -> str:
    """Bar pair character: ``█`` for filled, ``░`` for empty."""
    if pct is None:
        return " " * width
    pct = max(0.0, min(100.0, float(pct)))
    full = int(round(width * pct / 100))
    bar = "█" * full + "░" * (width - full)
    if on:
        return f"{color}{'█' * full}{RESET}{DIM}{'░' * (width - full)}{RESET}"
    return bar


def _bar_cohort(pct: float, width: int = 30, on: bool = True) -> str:
    """Cohort bar: muted color for both filled and empty."""
    if pct is None:
        return " " * width
    pct = max(0.0, min(100.0, float(pct)))
    full = int(round(width * pct / 100))
    if on:
        return f"{MUTED}{'█' * full}{RESET}{DIM}{'░' * (width - full)}{RESET}"
    return "█" * full + "░" * (width - full)


def render(view: dict[str, Any], ansi: bool = True,
           report_path: str = "report.html") -> str:
    out: list[str] = []
    title = view["title"]
    tagline = view["tagline"]
    shadow = view["shadow"]

    # ---- Hero box ----
    out.append("╭" + "─" * (W - 2) + "╮")
    out.append("│" + " " * (W - 2) + "│")

    eyebrow_text = "YOUR CLAUDE CODE ARCHETYPE"
    eyebrow = _ansi(eyebrow_text, DIM, MUTED, on=ansi)
    out.append("│  " + eyebrow + " " * (W - 4 - len(eyebrow_text)) + "│")
    out.append("│" + " " * (W - 2) + "│")

    title_line = (f"The {_ansi(title['archetype_name'], BOLD, ACCENT, on=ansi)}"
                  f" {_ansi(title['modifier'], BOLD, INK, on=ansi)}")
    visible_len = (len("The ") + len(title['archetype_name'])
                   + 1 + len(title['modifier']))
    out.append("│  " + title_line + " " * max(0, W - 4 - visible_len) + "│")

    # Tagline (wrapped, dim italic)
    for line in _wrap(tagline, W - 6):
        styled = _ansi(line, DIM, ITAL, INK_SOFT, on=ansi)
        pad = max(0, W - 4 - len(line))
        out.append("│  " + styled + " " * pad + "│")

    out.append("│" + " " * (W - 2) + "│")

    # Shadow block inside hero
    if shadow:
        sep = "── Your shadow " + "─" * (W - 21)
        out.append("│  " + _ansi(sep, MUTED, on=ansi)
                   + " " * (W - 4 - len(sep)) + "│")
        out.append("│  " + _ansi(shadow["name"], BOLD, INK, on=ansi)
                   + " " * (W - 4 - len(shadow["name"])) + "│")
        for line in _wrap(shadow["tagline"], W - 6):
            styled = _ansi(line, DIM, ITAL, INK_SOFT, on=ansi)
            pad = max(0, W - 4 - len(line))
            out.append("│  " + styled + " " * pad + "│")
        out.append("│" + " " * (W - 2) + "│")

    out.append("╰" + "─" * (W - 2) + "╯")
    out.append("")

    # ---- 2x2 landscape — part of the hero region ----
    landscape = view.get("compass")
    if landscape:
        chart = render_landscape_ascii(landscape, width=60, height=14)
        if ansi:
            chart = (chart
                     .replace("★", f"{ACCENT}★{RESET}")
                     .replace("●", f"{INK_SOFT}●{RESET}")
                     .replace("○", f"{MUTED}○{RESET}")
                     .replace("◌", f"{MUTED}◌{RESET}"))
        out.append(chart)
        out.append("")

    # ---- Signature moves ----
    out.append("  " + _ansi("SIGNATURE MOVES", BOLD, INK, on=ansi))
    out.append("")
    bar_w = 30
    for i, t in enumerate(view["section_identity"]["traits_view"], 1):
        accent = "▎ "
        is_contrast = t["kind"] == "contrast"
        out.append("  " + _ansi(accent, ACCENT if not is_contrast
                                else INK_SOFT, on=ansi)
                   + _ansi(t["name_em"], BOLD, INK, on=ansi))
        for line in _wrap(t["characterization"], W - 6, indent="    "):
            out.append(_ansi(line, DIM, ITAL, INK_SOFT, on=ansi))
        out.append("")
        you_pct_int = int(round(t["you_pct"]))
        out.append("      "
                   + _ansi("you", DIM, MUTED, on=ansi) + "      "
                   + _bar(t["you_pct"], bar_w, on=ansi,
                          color=ACCENT if not is_contrast else INK_SOFT)
                   + f"  {you_pct_int:>3}%")
        if t["cohort_pct"] is not None:
            cohort_pct_int = int(round(t["cohort_pct"]))
            out.append("      "
                       + _ansi("cohort", DIM, MUTED, on=ansi) + "   "
                       + _bar_cohort(t["cohort_pct"], bar_w, on=ansi)
                       + f"  {cohort_pct_int:>3}%")
        out.append("")

    out.append("")

    # ---- Recognition lines ----
    if view["section_identity"].get("recognition_lines"):
        out.append("  " + _ansi("YOU'LL KNOW THIS IS YOU IF…",
                                BOLD, INK, on=ansi))
        out.append("")
        for line in view["section_identity"]["recognition_lines"]:
            for sub in _wrap("— " + line, W - 4, indent="    "):
                out.append("  " + _ansi(sub, ITAL, INK_SOFT, on=ansi))
        out.append("")
        out.append("")

    # ---- Two moves ----
    out.append("  " + _ansi("TWO MOVES TO BORROW", BOLD, INK, on=ansi))
    out.append("")
    for i, mv in enumerate(view["section_moves"]["moves"], 1):
        from_label = f"From {mv['from_archetype']}"
        out.append("  "
                   + _ansi("▌ ", ACCENT, on=ansi)
                   + _ansi(from_label + " — " + mv["verb_phrase"],
                           BOLD, INK, on=ansi))
        # Combine lead + action; let the user open the HTML for the long
        # version of either.
        for line in _wrap(mv["body_lead"], W - 8, indent="    "):
            out.append("  " + _ansi(line, DIM, INK_SOFT, on=ansi))
        out.append("")

    # ---- Footer ----
    out.append("  " + _ansi("─" * (W - 4), MUTED, on=ansi))
    col = view["colophon"]
    if col["n_sessions"]:
        info = (f"  {col['n_sessions']} sessions"
                + (f" · {col['earliest']} – {col['latest']}"
                   if col["earliest"] and col["latest"] else ""))
        out.append(_ansi(info, DIM, MUTED, on=ansi))
    out.append("  " + _ansi(f"Full report:  open {report_path}",
                            DIM, INK_SOFT, on=ansi))

    return "\n".join(out) + "\n"
