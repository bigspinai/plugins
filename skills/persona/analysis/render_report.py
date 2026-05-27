#!/usr/bin/env python3
"""Render the practice mirror's three artifacts from a single content JSON.

Reads:
  - report/report_content.json  (LLM-authored, schema-validated)
  - report/metrics.json         (pure data, all numbers come from here)

Writes:
  - report/report.html
  - report/report.md
  - report/hero_card.txt        (with ANSI color)
  - report/hero_card.plain.txt  (no ANSI)

The LLM authors prose. The renderer fetches numbers via ``*_ref`` paths
into ``metrics.json``. The schema gates the contract; an unresolved ref
or a schema failure aborts with a diff so the orchestrator can fix and
retry.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import jsonschema

HERE = Path(__file__).resolve().parent
DEFAULT_SCHEMA = HERE / "report_content.schema.json"
ARCHETYPE_PROFILES_PATH = HERE.parent / "baselines" / "archetype_profiles.json"

log = logging.getLogger("render_report")


def _load_cohort_prevalence() -> dict[str, float]:
    """Per-archetype share of the baseline cohort, in percent (sums ≈ 100).

    Loaded once at import. If the baseline file is missing, returns an
    empty dict — the renderer will fall back to a flat distribution so
    nothing visually breaks.
    """
    try:
        data = json.loads(ARCHETYPE_PROFILES_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {a["name"]: float(a.get("prevalence_pct", 0.0))
            for a in data.get("archetypes", [])}


COHORT_PREVALENCE: dict[str, float] = _load_cohort_prevalence()


# =====================================================================
# Reference resolver — paths into metrics.json
# =====================================================================
#
# Path syntax: dot-separated segments. A segment is either ``key`` or
# ``key[filter_key=filter_value]`` where the filter selects a list entry.
# Examples:
#
#   structural.pr_rate_pct
#   user_archetype.within_archetype_positioning.categoricals
#       .interaction_style.buckets[bucket=delegative].cohort_pct
#
# Filter values may contain spaces (e.g. "Marathon Workshop"); only ``]``
# terminates them.

_SEGMENT_RE = re.compile(r"^(\w+)(?:\[(\w+)=([^\]]+)\])?$")


def _split_path(path: str) -> list[str]:
    """Split a path on dots that aren't inside ``[]`` brackets."""
    segments: list[str] = []
    buf = ""
    depth = 0
    for ch in path:
        if ch == "[":
            depth += 1
            buf += ch
        elif ch == "]":
            depth -= 1
            buf += ch
        elif ch == "." and depth == 0:
            if buf:
                segments.append(buf)
            buf = ""
        else:
            buf += ch
    if buf:
        segments.append(buf)
    return segments


class RefError(Exception):
    """Unresolvable reference. Carries the path and a hint about why."""


def resolve_ref(metrics: dict, path: str) -> Any:
    """Walk ``metrics`` following ``path``. Raises ``RefError`` on any
    unresolvable hop, with enough context for the writer to fix the
    content JSON without inspecting the full data tree."""
    cur: Any = metrics
    walked: list[str] = []
    for segment in _split_path(path):
        m = _SEGMENT_RE.match(segment)
        if not m:
            raise RefError(
                f"unparseable segment '{segment}' in path '{path}'"
            )
        key, fkey, fval = m.groups()

        if not isinstance(cur, dict):
            raise RefError(
                f"path '{path}': can't access '{key}' — "
                f"got {type(cur).__name__} after '{'.'.join(walked)}'"
            )
        if key not in cur:
            available = sorted(cur.keys())
            preview = available[:8] + (["..."] if len(available) > 8 else [])
            raise RefError(
                f"path '{path}': key '{key}' not found at "
                f"'{'.'.join(walked) or '<root>'}'. "
                f"Available: {preview}"
            )
        cur = cur[key]
        walked.append(key)

        if fkey is not None:
            if not isinstance(cur, list):
                raise RefError(
                    f"path '{path}': filter [{fkey}={fval}] applied at "
                    f"'{'.'.join(walked)}' but value is "
                    f"{type(cur).__name__}, not list"
                )
            matches = [
                item for item in cur
                if isinstance(item, dict) and str(item.get(fkey)) == fval
            ]
            if not matches:
                seen = [
                    item.get(fkey) for item in cur
                    if isinstance(item, dict)
                ][:6]
                raise RefError(
                    f"path '{path}': no entry where {fkey}={fval!r} "
                    f"in list at '{'.'.join(walked)}'. "
                    f"Saw {fkey} values: {seen}"
                )
            cur = matches[0]
            walked.append(f"[{fkey}={fval}]")
    return cur


def resolve_optional(metrics: dict, path: str | None,
                     default: Any = None) -> Any:
    if not path:
        return default
    try:
        return resolve_ref(metrics, path)
    except RefError:
        return default


# =====================================================================
# Number formatting
# =====================================================================

def format_value(value: float, fmt: str) -> str:
    """Apply a content-JSON ``format`` spec to a numeric value.

    The schema enum:
      percent_int          — input 0-100, output e.g. "60%"
      percent_1_decimal    — input 0-100, output e.g. "60.0%"
      number_1_decimal     — output e.g. "4.0"
      integer              — output e.g. "60"

    Rate-valued refs (0-1, e.g. user_signal_rates.*) aren't surfaced via
    headline_stats in v1; the trait renderer multiplies by 100 internally
    when drawing bars, so no extra format is needed there.
    """
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if fmt == "percent_int":
        return f"{round(v)}%"
    if fmt == "percent_1_decimal":
        return f"{v:.1f}%"
    if fmt == "number_1_decimal":
        return f"{v:.1f}"
    if fmt == "integer":
        return f"{int(round(v))}"
    raise ValueError(f"unknown format: {fmt}")


# =====================================================================
# Content loading + schema validation
# =====================================================================

def load_content(content_path: Path, schema_path: Path) -> dict:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    content = json.loads(content_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(content), key=lambda e: list(e.path))
    if errors:
        msgs = []
        for e in errors:
            where = ".".join(str(p) for p in e.path) or "<root>"
            msgs.append(f"  at {where}: {e.message}")
        raise SystemExit(
            f"\nschema validation failed for {content_path}:\n"
            + "\n".join(msgs)
        )
    return content


def load_metrics(metrics_path: Path) -> dict:
    return json.loads(metrics_path.read_text(encoding="utf-8"))


# =====================================================================
# Build a flat "view" the renderers can iterate
# =====================================================================
#
# Each renderer has its own format conventions, but they all need the
# same upstream operation: resolve refs, attach formatted values. We
# centralize that here so HTML / markdown / CLI all start from the same
# resolved view.

# Archetype landscape: a 2x2 of structure × iteration.
# Four primaries anchor the corners; two variants sit adjacent to their
# parent primary. Multi-Mode Journeyman sits at the center.
#
# Display coords: x ∈ [-1, 1] (low → high structure, left → right);
# y ∈ [-1, 1] (low → high iteration, bottom → top, math convention —
# the renderer flips y for SVG).

ARCHETYPE_POSITIONS: dict[str, tuple[float, float]] = {
    # Primary corners
    "The Pair Programmer":      (-0.78,  0.78),
    "The Runtime Mechanic":       ( 0.78,  0.78),
    "The Quick-Turn Sprinter":              (-0.78, -0.78),
    "The Showrunner":              ( 0.78, -0.78),
    # Variants adjacent to their parent primary
    "The Prompt Minimalist":          (-0.42,  0.45),  # near Pair Programmer
    "The Spec-First Architect": ( 0.92, -0.42),  # near Showrunner (extra structure)
    # Center: the balance point
    "The Multi-Mode Journeyman":           ( 0.00,  0.00),
}

# Categorize for visual treatment.
PRIMARIES = {
    "The Pair Programmer", "The Runtime Mechanic",
    "The Quick-Turn Sprinter", "The Showrunner",
}
VARIANTS = {"The Prompt Minimalist", "The Spec-First Architect"}


def landscape_view(content: dict, metrics: dict) -> dict | None:
    """Compute the user's position on the 2x2 archetype landscape via a
    weighted centroid of ``all_scores`` over ``ARCHETYPE_POSITIONS``.

    A user with score 4.0 in one archetype lands on that archetype's
    anchor point; a user with mixed scores interpolates toward each
    contributing archetype proportionally. Multi-Mode Journeyman primary (or all
    scores zero) → center.

    Returns None when the content JSON sets ``compass.include: false``.
    """
    compass = content.get("compass") or {}
    if not compass.get("include", True):
        return None

    primary = resolve_optional(metrics, compass.get("user_archetype_ref"), "")
    shadow = resolve_optional(metrics, compass.get("shadow_archetype_ref"), "")
    sharpness = resolve_optional(metrics, compass.get("user_sharpness_ref"), "")
    all_scores = resolve_optional(metrics, compass.get("all_scores_ref"), {}) or {}
    # Secondary archetype is read straight from metrics — content JSON
    # doesn't need to thread it through, since it's structural data.
    secondary = resolve_optional(metrics, "user_archetype.secondary", "") or ""
    secondary_score = resolve_optional(metrics, "user_archetype.secondary_score", 0) or 0

    # Weighted centroid. If total score is 0, we sit at center.
    total = sum(max(0.0, float(v)) for v in all_scores.values())
    if total > 0:
        you_x = sum(
            ARCHETYPE_POSITIONS.get(name, (0, 0))[0] * max(0.0, float(score))
            for name, score in all_scores.items()
        ) / total
        you_y = sum(
            ARCHETYPE_POSITIONS.get(name, (0, 0))[1] * max(0.0, float(score))
            for name, score in all_scores.items()
        ) / total
    else:
        you_x, you_y = 0.0, 0.0

    # Multi-Mode Journeyman users sit at center even if they have a Multi-Mode Journeyman
    # "score" — the all_scores dict doesn't include Multi-Mode Journeyman (it's a
    # fallback sentinel), so total may be 0 for them.
    is_generalist = primary == "The Multi-Mode Journeyman" or not primary

    # The four primary archetype corners are ALWAYS visible — they
    # anchor the 2x2 structurally. Showing only 2 dots in one row makes
    # the chart look broken; the corners give it the shape of a map.
    # Variants (Prompt Minimalist, Spec-First) only render when they're the
    # user's primary, shadow, or secondary — otherwise they'd clutter.
    visible_names: set[str] = set(PRIMARIES)
    if not is_generalist:
        if primary:
            visible_names.add(primary)
        if shadow:
            visible_names.add(shadow)
        if secondary and secondary != primary and secondary_score > 0:
            visible_names.add(secondary)

    archetypes = []
    for name, (x, y) in ARCHETYPE_POSITIONS.items():
        kind = (
            "primary" if name in PRIMARIES
            else "variant" if name in VARIANTS
            else "center"
        )
        archetypes.append({
            "name": name,
            "short_name": name.replace("The ", ""),
            "x": x,
            "y": y,
            "kind": kind,
            "is_user_primary": name == primary,
            "is_shadow": name == shadow,
            "is_secondary": name == secondary and name != primary,
            "is_visible": name in visible_names,
            "score": all_scores.get(name, 0.0),
        })

    # Distribution bars: how the cohort (the baseline corpus of measured
    # engineers) distributes across all 7 archetypes — the user's primary
    # is marked "You", their shadow marked "Shadow". This reframes the
    # chart from "your own mix" to "where you sit in the population".
    # The numbers come from baselines/archetype_profiles.json
    # (`prevalence_pct`) and sum to ≈100. Falls back to flat weighting if
    # the baseline file isn't loaded, so the visual still shows.
    prevalence = COHORT_PREVALENCE or {
        name: 100.0 / len(ARCHETYPE_POSITIONS) for name in ARCHETYPE_POSITIONS
    }
    bars: list[dict] = []
    for name, pct in prevalence.items():
        bars.append({
            "name": name,
            "short_name": name.replace("The ", ""),
            "pct": float(pct),
            "is_user_primary": name == primary,
            "is_shadow": name == shadow,
        })
    bars.sort(key=lambda b: b["pct"], reverse=True)

    return {
        "you": {"x": you_x, "y": you_y},
        "archetypes": archetypes,
        "bars": bars,
        "primary": primary,
        "shadow": shadow,
        "secondary": secondary,
        "secondary_score": float(secondary_score),
        "sharpness": sharpness,
        "is_generalist": is_generalist,
    }


# Backward-compatible alias — older code paths may still import compass_view.
compass_view = landscape_view


def trait_view(trait: dict, metrics: dict) -> dict:
    """Resolve a trait's data block into ``you_pct`` + ``cohort_pct``
    (both 0-100), plus passthrough metadata for the renderer."""
    data = trait["data"]
    if data["kind"] == "signal":
        signal = data["signal"]
        # User: rate from user_signal_rates, OR (for deterministic) from det_medians
        you = resolve_optional(
            metrics, f"user_archetype.user_signal_rates.{signal}"
        )
        if you is None:
            you = resolve_optional(
                metrics, f"user_archetype.user_det_medians.{signal}"
            )
        you_pct = (you or 0.0) * 100
        # Cohort p50 from within-archetype positioning
        cohort = resolve_optional(
            metrics,
            "user_archetype.within_archetype_positioning.signals"
            f"[signal={signal}].cohort_p50"
        )
        cohort_pct = (cohort or 0.0) * 100 if cohort is not None else None
    else:  # categorical
        field, bucket = data["field"], data["bucket"]
        # User pct: use the existing percentage block for the field
        you_pct_paths = {
            "interaction_style": f"interaction_style.percentages.{bucket}",
            "engagement_depth": f"engagement_depth.percentages.{bucket}",
            "task_type": f"task_type.percentages.{bucket}",
            "arc_shape": f"arc_shape.percentages.{bucket}",
            "session_shape": f"shapes.distribution.{bucket}.share_pct",
            "task_size": f"deterministic_signals.task_size.your.{bucket}",
        }
        you_pct = resolve_optional(metrics, you_pct_paths.get(field), 0.0) or 0.0
        # Cohort pct from within-archetype categorical positioning
        cohort_pct = resolve_optional(
            metrics,
            "user_archetype.within_archetype_positioning.categoricals"
            f".{field}.buckets[bucket={bucket}].cohort_pct"
        )
    delta = (you_pct - cohort_pct) if cohort_pct is not None else None
    return {
        "kind": trait["kind"],
        "name_em": trait["name_em"],
        "characterization": trait["characterization"],
        "you_pct": round(you_pct, 1),
        "cohort_pct": round(cohort_pct, 1) if cohort_pct is not None else None,
        "delta_pp": round(delta, 1) if delta is not None else None,
    }


def gauge_value_view(item: dict, metrics: dict) -> dict:
    """Resolve a gauge_value (used by headline_stats and outcome.comparisons)
    into a formatted string + raw float."""
    if "value_ref" in item:
        raw = resolve_ref(metrics, item["value_ref"])
    else:
        raw = item["value_static"]
    return {
        "label": item["label"],
        "value_raw": float(raw) if raw is not None else None,
        "value_str": format_value(raw, item["format"]),
        "kind": item.get("kind"),
        "format": item["format"],
    }


def headline_stat_view(stat: dict, metrics: dict) -> dict:
    out = gauge_value_view(stat, metrics)
    out["detail"] = stat.get("detail")
    if "comparison" in stat:
        comp = stat["comparison"]
        if "value_ref" in comp:
            craw = resolve_ref(metrics, comp["value_ref"])
        else:
            craw = comp["value_static"]
        out["comparison"] = {
            "label": comp["label"],
            "value_str": format_value(craw, comp["format"]),
        }
    return out


def fingerprint_badge_view(content: dict, metrics: dict) -> dict:
    fb = content.get("fingerprint_badge") or {}
    if not fb:
        return {
            "label": "", "detail": "", "score_str": None,
            "score_raw": None, "sharpness": "",
        }
    sharpness = resolve_optional(metrics, fb.get("ref"), "")
    label = fb.get("label_map", {}).get(sharpness, sharpness or "—")
    score_raw = resolve_optional(metrics, fb.get("score_ref"))
    score_str = format_value(score_raw, "number_1_decimal") if score_raw is not None else None
    return {
        "label": label,
        "detail": fb.get("detail", ""),
        "score_str": score_str,
        "score_raw": score_raw,
        "sharpness": sharpness,
    }


def shadow_view(content: dict, metrics: dict) -> dict | None:
    s = content["shadow"]
    name = resolve_optional(metrics, s.get("name_ref"), "")
    if not name:
        return None  # no shadow (e.g., Multi-Mode Journeyman primary)
    axis = resolve_optional(metrics, s.get("axis_ref"), "")
    return {
        "name": name,
        "tagline": s["tagline"],
        "axis": axis,
    }


def reflection_prompts_view(content: dict, metrics: dict) -> list[str]:
    ref = content.get("reflection_prompts_ref")
    prompts = resolve_optional(metrics, ref, []) or []
    return [str(p) for p in prompts]


def colophon_view(content: dict, metrics: dict) -> dict:
    c = content["colophon"]
    return {
        "n_sessions": resolve_optional(metrics, c.get("n_sessions_ref")),
        "earliest": resolve_optional(metrics, c.get("date_earliest_ref")),
        "latest": resolve_optional(metrics, c.get("date_latest_ref")),
        "footnote": c.get("footnote", ""),
    }


def build_view(content: dict, metrics: dict) -> dict:
    """Compose the resolved view that all three renderers consume."""
    return {
        "title": content["title"],
        "tagline": content["tagline"],
        "fingerprint_badge": fingerprint_badge_view(content, metrics),
        # headline_stats is optional in v3.0 — renderers ignore by default.
        "headline_stats": [
            headline_stat_view(s, metrics)
            for s in (content.get("headline_stats") or [])
        ],
        "shadow": shadow_view(content, metrics),
        "section_identity": {
            **content["section_identity"],
            "traits_view": [
                trait_view(t, metrics)
                for t in content["section_identity"]["traits"]
            ],
        },
        "section_outcome": {
            **content["section_outcome"],
            "primary_view": gauge_value_view(
                content["section_outcome"]["primary_value"], metrics
            ),
            "comparison_views": [
                gauge_value_view(c, metrics)
                for c in content["section_outcome"]["comparison_values"]
            ],
        },
        "section_moves": content["section_moves"],
        "compass": compass_view(content, metrics),
        "reflection_prompts": reflection_prompts_view(content, metrics),
        "colophon": colophon_view(content, metrics),
    }


# =====================================================================
# Guardrail — displayed archetype must match the classifier
# =====================================================================

def validate_archetype(content: dict, metrics: dict) -> None:
    """The displayed archetype must match the deterministic classifier.

    ``title.archetype_name`` is *computed* by ``analysis/archetypes.py``
    from tagged signals — it is not free prose the author may choose. A
    content JSON whose name disagrees with the classifier (or names a
    persona that doesn't exist) is a bug, not a stylistic call: it would
    render an unexpected persona with mismatched static copy. Fail loudly.

    The valid name is derived from the classifier output in
    ``metrics.json`` — not a hardcoded list — so renaming an archetype in
    ``archetypes.py`` propagates automatically with no edit here.
    """
    authored = content["title"]["archetype_name"]
    classified = (metrics.get("user_archetype") or {}).get("primary") or ""
    classified_bare = (
        classified[len("The "):] if classified.startswith("The ") else classified
    )
    if classified_bare and authored != classified_bare:
        raise SystemExit(
            f"\narchetype mismatch: title.archetype_name is {authored!r} but the "
            f"classifier in metrics.json assigned {classified_bare!r}.\n"
            f"The archetype label is computed, not authored — set "
            f"title.archetype_name to {classified_bare!r} (or re-run "
            f"compute_metrics.py if the classification changed)."
        )


# =====================================================================
# Entry point
# =====================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render report_content.json + metrics.json into HTML, "
                    "markdown, hero markdown, and CLI hero card."
    )
    parser.add_argument("--content", type=Path,
                        default=Path("report/report_content.json"))
    parser.add_argument("--metrics", type=Path,
                        default=Path("report/metrics.json"))
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--out", type=Path, default=Path("report"))
    parser.add_argument("--only",
                        choices=["html", "markdown", "hero", "cli", "all"],
                        default="all")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if not args.content.exists():
        log.error("content JSON not found: %s", args.content)
        return 2
    if not args.metrics.exists():
        log.error("metrics JSON not found: %s", args.metrics)
        return 2

    content = load_content(args.content, args.schema)
    metrics = load_metrics(args.metrics)
    validate_archetype(content, metrics)

    try:
        view = build_view(content, metrics)
    except RefError as exc:
        print(f"\nref resolution failed: {exc}", file=sys.stderr)
        return 3

    args.out.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    if args.only in ("html", "all"):
        from render import html as html_renderer
        out = args.out / "report.html"
        out.write_text(html_renderer.render(view), encoding="utf-8")
        written.append(out)
    if args.only in ("markdown", "all"):
        from render import markdown as md_renderer
        out = args.out / "report.md"
        out.write_text(md_renderer.render(view), encoding="utf-8")
        written.append(out)
    if args.only in ("hero", "all"):
        from render import hero_md as hero_md_renderer
        out = args.out / "hero.md"
        out.write_text(hero_md_renderer.render(view), encoding="utf-8")
        written.append(out)
    if args.only in ("cli", "all"):
        from render import cli as cli_renderer
        ansi = args.out / "hero_card.txt"
        plain = args.out / "hero_card.plain.txt"
        ansi.write_text(cli_renderer.render(view, ansi=True), encoding="utf-8")
        plain.write_text(cli_renderer.render(view, ansi=False), encoding="utf-8")
        written.append(ansi)
        written.append(plain)

    print()
    print("=" * 64)
    print("Rendered:")
    for p in written:
        print(f"  {p}")
    print("=" * 64)
    title = view["title"]
    print(f"  archetype: The {title['archetype_name']} {title['modifier']}")
    if view["shadow"]:
        print(f"  shadow:    {view['shadow']['name']}")
    print(f"  sessions:  {view['colophon']['n_sessions']}")
    print()
    if (args.out / "report.html").exists():
        print(f"  open {args.out / 'report.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
