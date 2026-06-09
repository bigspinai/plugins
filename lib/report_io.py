"""Shared output-naming scheme for bigspin report renderers.

The single source of truth for what a report's deliverable files are *called*.
Both `skills/persona/analysis/render_report.py` and
`skills/token-roi/analysis/render_report.py` import this (it is on PYTHONPATH
via `scripts/new_run.sh`) so the filenames can never drift apart.

A deliverable file is `<slug>-<base>` inside the run directory, e.g. slug
``persona`` + base ``report.html`` -> ``persona-report.html``. An empty slug
yields the bare base name (``report.html``) — the fallback when no ``--slug``
is passed on the CLI.

Canonical base names (use these constants, don't hardcode the strings):
    REPORT_HTML, REPORT_MD, HERO_MD, HERO_CARD_TXT, HERO_CARD_PLAIN_TXT
"""

from __future__ import annotations

from pathlib import Path

REPORT_HTML = "report.html"
REPORT_MD = "report.md"
HERO_MD = "hero.md"
HERO_CARD_TXT = "hero-card.txt"
HERO_CARD_PLAIN_TXT = "hero-card.plain.txt"


def report_name(slug: str, base: str) -> str:
    """Return the deliverable filename: ``<slug>-<base>`` (or ``base`` if slug is empty)."""
    return f"{slug}-{base}" if slug else base


def out_path(out_dir: Path, slug: str, base: str) -> Path:
    """Return the full path for a deliverable inside ``out_dir``."""
    return Path(out_dir) / report_name(slug, base)
