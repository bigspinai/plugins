#!/usr/bin/env python3
"""Compute deterministic metrics from a tagged sessions CSV.

Inputs:
  - tagged_sessions.csv  (from tagging/tag_sessions.py)
  - sessions.csv         (optional — from preprocessing; needed for subagent stats)
  - baselines/           (optional dir of baseline CSVs to compare against)

Output: a single metrics.json describing the user's practice. The report-
writing skill consumes this; the chart maker reads the same file.

Pure stdlib. No pandas.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import archetypes as archetypes_module
import shapes as shapes_module

csv.field_size_limit(min(2**31 - 1, sys.maxsize))

log = logging.getLogger("compute_metrics")

HERE = Path(__file__).resolve().parent
DEFAULT_TAXONOMY = HERE.parent / "tagging" / "taxonomy.json"
DEFAULT_BASELINES = HERE.parent / "baselines"

# v2.1 schema categories. The interpretive signals span "behavior";
# deterministic signals split across "pattern" and "outcome" but those
# are computed in code, not in the LLM annotation.
CATEGORIES = ["pattern", "behavior", "outcome"]

# v2.1 anti-pattern signals (negative valence). These are detected by
# name rather than by category, since the v2.1 schema groups them under
# "behavior" alongside positive signals. Keep this in sync with
# `tagging/tag_sessions.py:SIGNAL_GROUPS["anti_pattern"]`.
ANTI_PATTERN_SIGNALS = frozenset({
    "accept_verbatim_no_question",
    "fix_request_without_specifics",
    "repeated_same_prompt",
    "error_repaste",
})

# v2.1 reality-contact signals — capture trigger / surface_type, are
# presence-only, and are summarized separately from the per-signal table.
REALITY_CONTACT_SIGNALS = frozenset({
    "post_implementation_correction",
    "in_review_edge_case_surface",
    "external_reality_disclosure",
    "intent_reframe",
    "edge_case_failure_observed",
})


# =====================================================================
# IO
# =====================================================================

def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_taxonomy(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def signal_to_category_map(taxonomy: dict) -> dict[str, str]:
    """Build {signal_name: category} for **interpretive** signals only.

    The v2.1 schema lists every signal at the top level with a `category`
    field (`pattern` / `behavior` / `outcome`) and a `computation` field
    (`interpretive` / `deterministic`). The LLM only fires interpretive
    signals; deterministic ones are computed by ``preprocessing/enrich.py``
    and live in dedicated columns, not inside the annotation JSON.
    """
    out: dict[str, str] = {}
    for sig_name, sig in (taxonomy.get("signals") or {}).items():
        if not isinstance(sig, dict):
            continue
        if sig.get("computation") != "interpretive":
            continue
        cat = sig.get("category")
        if cat:
            out[sig_name] = cat
    return out


def deterministic_signals_in_schema(taxonomy: dict) -> list[tuple[str, dict]]:
    """``[(signal_name, signal_meta), ...]`` for deterministic signals only,
    in schema order. Used to find which CSV columns to read."""
    out: list[tuple[str, dict]] = []
    for sig_name, sig in (taxonomy.get("signals") or {}).items():
        if isinstance(sig, dict) and sig.get("computation") == "deterministic":
            out.append((sig_name, sig))
    return out


# =====================================================================
# Helpers
# =====================================================================

def _safe_json(s: str | None) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _parse_int(s: str | None) -> int:
    try:
        return int(s) if s not in (None, "") else 0
    except ValueError:
        return 0


def _parse_float(s: str | None) -> float:
    try:
        return float(s) if s not in (None, "") else 0.0
    except ValueError:
        return 0.0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _round(x: float, n: int = 2) -> float:
    return round(float(x), n)


# =====================================================================
# Annotation-derived metrics
# =====================================================================

def _annotation_rows(tagged: list[dict]) -> list[tuple[dict, dict]]:
    """Pair each row with its parsed, valid annotation dict. Errors skipped."""
    pairs: list[tuple[dict, dict]] = []
    for r in tagged:
        ann = _safe_json(r.get("annotation"))
        if not isinstance(ann, dict) or "error" in ann or "signals" not in ann:
            continue
        pairs.append((r, ann))
    return pairs


def engagement_depth_distribution(pairs: list[tuple[dict, dict]]) -> dict:
    counts: Counter[str] = Counter()
    for _, ann in pairs:
        counts[ann.get("engagement_depth", "unknown")] += 1
    n = sum(counts.values())
    return {
        "counts": dict(counts),
        "percentages": {k: _round(v * 100 / n, 1) if n else 0.0
                        for k, v in counts.items()},
        "n": n,
    }


def interaction_style_distribution(pairs: list[tuple[dict, dict]]) -> dict:
    counts: Counter[str] = Counter()
    for _, ann in pairs:
        counts[ann.get("interaction_style", "unknown")] += 1
    n = sum(counts.values())
    return {
        "counts": dict(counts),
        "percentages": {k: _round(v * 100 / n, 1) if n else 0.0
                        for k, v in counts.items()},
        "n": n,
    }


def task_type_distribution(pairs: list[tuple[dict, dict]]) -> dict:
    """v2.1 categorical: implementation / debugging / refactor / etc."""
    counts: Counter[str] = Counter()
    for _, ann in pairs:
        counts[ann.get("task_type", "unknown")] += 1
    n = sum(counts.values())
    return {
        "counts": dict(counts),
        "percentages": {k: _round(v * 100 / n, 1) if n else 0.0
                        for k, v in counts.items()},
        "n": n,
    }


def arc_shape_distribution(pairs: list[tuple[dict, dict]]) -> dict:
    """v2.1 categorical: setup_first / explore_first / jump_in / iterative / unclear."""
    counts: Counter[str] = Counter()
    for _, ann in pairs:
        counts[ann.get("arc_shape", "unknown")] += 1
    n = sum(counts.values())
    return {
        "counts": dict(counts),
        "percentages": {k: _round(v * 100 / n, 1) if n else 0.0
                        for k, v in counts.items()},
        "n": n,
    }


def signal_fire_rates(pairs: list[tuple[dict, dict]],
                      sig_to_cat: dict[str, str]) -> dict:
    """Per-signal fire rate. Mean strength for signals that carry strength."""
    n = len(pairs)
    fire_counts: Counter[str] = Counter()
    strength_acc: dict[str, list[int]] = defaultdict(list)

    for _, ann in pairs:
        signals = ann.get("signals") or {}
        if not isinstance(signals, dict):
            continue
        for sig_name, sig_data in signals.items():
            fire_counts[sig_name] += 1
            if isinstance(sig_data, dict):
                s = sig_data.get("strength")
                if isinstance(s, int):
                    strength_acc[sig_name].append(s)

    out = {}
    for sig_name, cat in sig_to_cat.items():
        fired = fire_counts.get(sig_name, 0)
        rate = (fired / n) if n else 0.0
        mean_strength = (
            statistics.mean(strength_acc[sig_name])
            if strength_acc.get(sig_name) else None
        )
        out[sig_name] = {
            "category": cat,
            "fired": fired,
            "rate": _round(rate, 4),
            "rate_pct": _round(rate * 100, 1),
            "mean_strength": _round(mean_strength, 2) if mean_strength else None,
        }
    return out


def category_fire_rates(pairs: list[tuple[dict, dict]],
                        sig_to_cat: dict[str, str]) -> dict:
    """A category fires if at least one signal in it fired."""
    n = len(pairs)
    cat_fired: Counter[str] = Counter()
    for _, ann in pairs:
        signals = ann.get("signals") or {}
        if not isinstance(signals, dict):
            continue
        cats_in_session: set[str] = set()
        for sig_name in signals:
            cat = sig_to_cat.get(sig_name)
            if cat:
                cats_in_session.add(cat)
        for c in cats_in_session:
            cat_fired[c] += 1

    out = {}
    for cat in CATEGORIES:
        fired = cat_fired.get(cat, 0)
        rate = (fired / n) if n else 0.0
        out[cat] = {
            "fired": fired,
            "rate": _round(rate, 4),
            "rate_pct": _round(rate * 100, 1),
        }
    return out


def reality_contact_summary(pairs: list[tuple[dict, dict]]) -> dict:
    """Counts of reality-contact moments by trigger and surface_type."""
    triggers: Counter[str] = Counter()
    surfaces: Counter[str] = Counter()
    sessions_with_rc = 0
    total = 0

    for _, ann in pairs:
        signals = ann.get("signals") or {}
        if not isinstance(signals, dict):
            continue
        had = False
        for sig_name, sig_data in signals.items():
            if sig_name not in REALITY_CONTACT_SIGNALS or not isinstance(sig_data, dict):
                continue
            had = True
            total += 1
            if sig_data.get("trigger"):
                triggers[sig_data["trigger"]] += 1
            if sig_data.get("surface_type"):
                surfaces[sig_data["surface_type"]] += 1
        if had:
            sessions_with_rc += 1

    n = len(pairs)
    return {
        "total_moments": total,
        "sessions_with_rc": sessions_with_rc,
        "session_rate_pct": _round(sessions_with_rc * 100 / n, 1) if n else 0.0,
        "moments_per_session": _round(total / n, 2) if n else 0.0,
        "by_trigger": dict(triggers),
        "by_surface": dict(surfaces),
    }


# =====================================================================
# Deterministic signal summary — reads enriched columns straight off the
# tagged CSV and compares each signal's user distribution against the
# population baseline (deterministic_baselines.csv).
# =====================================================================

def deterministic_signal_summary(rows: list[dict],
                                 taxonomy: dict,
                                 baselines: dict) -> dict:
    """For each deterministic signal, summarize the user's distribution
    and compare against the population.

    Returns:
      {
        "available": bool,
        "signals": [
          {
            "signal": "iteration_count",
            "value_type": "count",
            "your_n": 32,
            "your_mean": 8.4,
            "your_median": 5.0,
            "baseline_median": 4.0,
            "baseline_p25": 2.0,
            "baseline_p75": 9.0,
            "comparison": "near_baseline" | "above_p75" | "below_p25" |
                          "above_p90" | "below_p10",
            "delta_median": 1.0,         # your_median - baseline_median
          },
          ...
        ],
        "task_size": {
          "your": {"small": 0.4, "medium": 0.5, ...},
          "baseline": {"small": 0.45, "medium": 0.41, ...},
          "delta_pct": {...},
        },
      }

    If the deterministic columns aren't present (e.g. user skipped enrich),
    returns {"available": False, "note": "..."}.
    """
    det_signals = deterministic_signals_in_schema(taxonomy)
    if not det_signals:
        return {"available": False, "note": "no deterministic signals in schema"}

    # Sanity-check: the input rows should have deterministic columns.
    sample_keys = set(rows[0].keys()) if rows else set()
    numeric_signals = [(n, m) for n, m in det_signals
                       if m.get("value_type") != "categorical"]
    if numeric_signals and not any(n in sample_keys for n, _ in numeric_signals):
        return {"available": False,
                "note": "tagged CSV lacks deterministic columns — "
                        "did you run preprocessing/enrich.py?"}

    # Baseline lookup.
    baseline_by_signal: dict[str, dict] = {}
    for row in baselines.get("deterministic_baselines", []) or []:
        name = row.get("signal")
        if not name:
            continue
        try:
            baseline_by_signal[name] = {
                "value_type": row.get("value_type", ""),
                "n": int(row.get("n") or 0),
                "mean": float(row.get("mean") or 0),
                "median": float(row.get("median") or 0),
                "p10": float(row.get("p10") or 0),
                "p25": float(row.get("p25") or 0),
                "p75": float(row.get("p75") or 0),
                "p90": float(row.get("p90") or 0),
            }
        except (TypeError, ValueError):
            continue

    out_signals: list[dict] = []
    for name, meta in numeric_signals:
        values: list[float] = []
        for r in rows:
            raw = (r.get(name) or "").strip()
            if not raw:
                continue
            try:
                values.append(float(raw))
            except ValueError:
                continue

        if not values:
            continue

        your_mean = statistics.mean(values)
        your_median = statistics.median(values)
        base = baseline_by_signal.get(name)
        entry: dict[str, Any] = {
            "signal": name,
            "value_type": meta.get("value_type", "ratio"),
            "your_n": len(values),
            "your_mean": _round(your_mean, 3),
            "your_median": _round(your_median, 3),
        }
        if base:
            entry["baseline_median"] = _round(base["median"], 3)
            entry["baseline_p25"] = _round(base["p25"], 3)
            entry["baseline_p75"] = _round(base["p75"], 3)
            entry["baseline_p10"] = _round(base["p10"], 3)
            entry["baseline_p90"] = _round(base["p90"], 3)
            entry["delta_median"] = _round(your_median - base["median"], 3)
            entry["comparison"] = _describe_position(your_median, base)
        out_signals.append(entry)

    # Task-size cross-tab.
    task_size_summary: dict[str, Any] = {}
    your_ts: Counter[str] = Counter()
    for r in rows:
        ts = (r.get("task_size") or "").strip()
        if ts:
            your_ts[ts] += 1
    n = sum(your_ts.values())
    if n:
        your_pct = {k: _round(v * 100 / n, 1) for k, v in your_ts.items()}
        baseline_ts: dict[str, float] = {}
        for row in baselines.get("task_size_distribution", []) or []:
            try:
                baseline_ts[row["task_size"]] = float(row.get("rate_pct") or 0)
            except (KeyError, TypeError, ValueError):
                continue
        task_size_summary = {
            "your": your_pct,
            "baseline": baseline_ts,
            "delta_pct": {k: _round(your_pct.get(k, 0) - baseline_ts.get(k, 0), 1)
                          for k in set(your_pct) | set(baseline_ts)},
            "n": n,
        }

    return {
        "available": True,
        "signals": out_signals,
        "task_size": task_size_summary,
    }


# =====================================================================
# Shape assignment — composes deterministic + interpretive signals into
# one named pattern per session, then summarizes the distribution.
# =====================================================================

def shape_summary(rows: list[dict]) -> dict:
    """Assign a shape to each session and aggregate.

    Returns:
      {
        "available": bool,
        "n_classified": int,
        "modal_shape": "Workshop" | ...,
        "modal_share_pct": 32.0,
        "shape_entropy": 0.71,           # normalized 0..1
        "distribution": {
          "Workshop": {"n": 16, "share_pct": 32.0, "visual": "..."},
          ...,
        },
        "per_session": [
          {"session_id": "...", "shape": "Workshop", "score": 3.6},
          ...,
        ],
      }

    ``per_session`` exists so the report writer can locate sessions of
    a given shape when grabbing verbatim quotes.
    """
    if not rows:
        return {"available": False, "note": "no rows to classify"}

    counts: Counter[str] = Counter()
    per_session: list[dict] = []
    for row in rows:
        shape, score, _ = shapes_module.assign_shape(row)
        counts[shape] += 1
        per_session.append({
            "session_id": row.get("session_id", ""),
            "shape": shape,
            "score": _round(score, 2),
        })

    total = sum(counts.values())
    if total == 0:
        return {"available": False, "note": "no sessions classified"}

    distribution: dict[str, dict] = {}
    # Iterate the SHAPES vocabulary so the order is stable + meaningful.
    seen: set[str] = set()
    for shape_obj in shapes_module.SHAPES:
        n = counts.get(shape_obj.name, 0)
        if n == 0:
            continue
        seen.add(shape_obj.name)
        distribution[shape_obj.name] = {
            "n": n,
            "share_pct": _round(n * 100 / total, 1),
            "visual": shape_obj.visual,
        }
    # Catch the Unassigned sentinel and any shape names not in SHAPES
    # (defensive — shouldn't happen, but doesn't cost anything).
    for name, n in counts.items():
        if name in seen:
            continue
        distribution[name] = {
            "n": n,
            "share_pct": _round(n * 100 / total, 1),
            "visual": "",
        }

    # Modal shape is the most common shape excluding the Unassigned
    # sentinel, since the user wants to recognize themselves in a
    # *named* pattern. If everyone is unassigned (rare), fall back.
    named_counts = {k: v for k, v in counts.items()
                    if k != shapes_module.UNASSIGNED}
    if named_counts:
        modal_shape, modal_n = max(named_counts.items(), key=lambda kv: kv[1])
    else:
        modal_shape, modal_n = max(counts.items(), key=lambda kv: kv[1])

    # Normalized Shannon entropy across the full distribution including
    # Unassigned. 0 = pure specialist (one shape), 1 = uniform across all
    # shapes that appeared. Useful as a "you flex" indicator.
    n_distinct = len(counts)
    if n_distinct <= 1:
        entropy = 0.0
    else:
        h = 0.0
        for n in counts.values():
            p = n / total
            if p > 0:
                h -= p * math.log(p)
        entropy = h / math.log(n_distinct)

    return {
        "available": True,
        "n_classified": total,
        "modal_shape": modal_shape,
        "modal_share_pct": _round(modal_n * 100 / total, 1),
        "shape_entropy": _round(entropy, 3),
        "n_distinct_shapes": n_distinct,
        "distribution": distribution,
        "per_session": per_session,
    }


# =====================================================================
# Archetype assignment — operational classifier over the user's per-signal
# fire rates + deterministic medians. The archetype is what the report
# leads with ("you're The Runtime Mechanic"); shapes/signals become the
# evidence underneath.
# =====================================================================

def deterministic_medians(rows: list[dict], taxonomy: dict) -> dict[str, float]:
    """Median value per numeric deterministic signal across the user's
    sessions. Used as input to the archetype classifier (which has
    metadata predicates on iteration_count / edit_iteration_count) and
    surfaced in the user_archetype block for transparency."""
    out: dict[str, float] = {}
    for name, meta in deterministic_signals_in_schema(taxonomy):
        if meta.get("value_type") == "categorical":
            continue
        values: list[float] = []
        for r in rows:
            raw = (r.get(name) or "").strip()
            if not raw:
                continue
            try:
                values.append(float(raw))
            except ValueError:
                continue
        if values:
            out[name] = _round(statistics.median(values), 3)
    return out


def load_archetype_data(baselines_dir: Path) -> dict[str, dict]:
    """Merge archetype_baselines.csv + archetype_profiles.json into a
    single ``{archetype_name: profile_dict}`` map. Profile fields
    (tagline, scene, blind_spot, ...) take precedence on overlap."""
    csv_path = baselines_dir / "archetype_baselines.csv"
    json_path = baselines_dir / "archetype_profiles.json"

    base_by_name: dict[str, dict] = {}
    if csv_path.exists():
        for row in read_csv(csv_path):
            name = (row.get("archetype") or "").strip()
            if not name:
                continue
            base_by_name[name] = {
                "prevalence_pct": _parse_float(row.get("prevalence_pct")),
                "pr_yield_pct": _parse_float(row.get("pr_yield_pct")),
                "decisive_pct": _parse_float(row.get("decisive_pct")),
                "bootstrap_stability_pct":
                    _parse_float(row.get("bootstrap_stability_pct")),
                "iteration_count_median":
                    _parse_float(row.get("iteration_count_median")),
                "edit_iteration_count_median":
                    _parse_float(row.get("edit_iteration_count_median")),
                "tests_attempted_median":
                    _parse_float(row.get("tests_attempted_median")),
                "diff_reviewed_median":
                    _parse_float(row.get("diff_reviewed_median")),
                "modal_session_shape":
                    (row.get("modal_session_shape") or "").strip(),
                # Within-archetype primary-score distribution — lets the
                # report label the user as sharp / typical / borderline
                # within their archetype.
                "primary_score_p25":
                    _parse_float(row.get("primary_score_p25")),
                "primary_score_p50":
                    _parse_float(row.get("primary_score_p50")),
                "primary_score_p75":
                    _parse_float(row.get("primary_score_p75")),
                "primary_score_max":
                    _parse_float(row.get("primary_score_max")),
            }

    profile_by_name: dict[str, dict] = {}
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            for prof in data.get("archetypes", []) or []:
                name = (prof.get("name") or "").strip()
                if name:
                    profile_by_name[name] = prof
        except json.JSONDecodeError as exc:
            log.warning("could not parse %s: %s", json_path, exc)

    merged: dict[str, dict] = {}
    for name in set(base_by_name) | set(profile_by_name):
        m = dict(base_by_name.get(name) or {})
        m.update(profile_by_name.get(name) or {})
        merged[name] = m
    return merged


def load_archetype_signal_distributions(
    baselines_dir: Path,
) -> dict[str, dict[str, dict[str, float]]]:
    """Read ``archetype_signal_distributions.csv`` into a nested
    ``{archetype: {signal: {p25, p50, p75, mean, n_users, signal_kind}}}``
    map. Used to position the user's signal rates within their cohort
    ("among Runtime Mechanics, you're top-quartile on diff-review").
    """
    path = baselines_dir / "archetype_signal_distributions.csv"
    if not path.exists():
        return {}
    out: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for row in read_csv(path):
        arch = (row.get("archetype") or "").strip()
        sig = (row.get("signal") or "").strip()
        if not arch or not sig:
            continue
        out[arch][sig] = {
            "signal_kind": (row.get("signal_kind") or "").strip(),
            "n_users": _parse_int(row.get("n_users")),
            "p25": _parse_float(row.get("p25")),
            "p50": _parse_float(row.get("p50")),
            "p75": _parse_float(row.get("p75")),
            "mean": _parse_float(row.get("mean")),
        }
    return dict(out)


def load_archetype_categorical_distributions(
    baselines_dir: Path,
) -> dict[str, dict[str, dict[str, dict[str, float]]]]:
    """Read ``archetype_categorical_distributions.csv`` into a nested
    ``{archetype: {field: {bucket: {share_pct, n_sessions}}}}`` map.
    Powers ``"Runtime Mechanics' modal shape is Workshop at 47%; yours
    is 53% — you're a sharp version."``"""
    path = baselines_dir / "archetype_categorical_distributions.csv"
    if not path.exists():
        return {}
    out: dict[str, dict[str, dict[str, dict[str, float]]]] = (
        defaultdict(lambda: defaultdict(dict))
    )
    for row in read_csv(path):
        arch = (row.get("archetype") or "").strip()
        field = (row.get("categorical_field") or "").strip()
        bucket = (row.get("bucket") or "").strip()
        if not (arch and field and bucket):
            continue
        out[arch][field][bucket] = {
            "share_pct": _parse_float(row.get("share_pct")),
            "n_sessions": _parse_int(row.get("n_sessions")),
        }
    # Strip defaultdict wrappers for clean json output downstream.
    return {a: {f: dict(b) for f, b in fs.items()} for a, fs in out.items()}


def load_signal_effect_sizes(baselines_dir: Path) -> dict[str, dict]:
    """Read ``signal_effect_sizes.csv`` into a ``{signal: {...}}`` map
    keyed by signal name. Lets the report rank "try this" suggestions
    by research-effect-size rather than just cohort delta."""
    path = baselines_dir / "signal_effect_sizes.csv"
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    for row in read_csv(path):
        sig = (row.get("signal") or "").strip()
        if not sig:
            continue
        out[sig] = {
            "signal_kind": (row.get("signal_kind") or "").strip(),
            "comparison": (row.get("comparison") or "").strip(),
            "delta_pr_yield_pp": _parse_float(row.get("delta_pr_yield_pp")),
            "n_high": _parse_int(row.get("n_high")),
            "n_low": _parse_int(row.get("n_low")),
            "notes": (row.get("notes") or "").strip(),
        }
    return out


def _percentile_band(value: float, p25: float, p50: float,
                     p75: float) -> str:
    """Coarse band for where ``value`` sits in a cohort distribution.
    Same shape as the deterministic-signals positioning vocabulary so
    the report writer doesn't have to learn two systems."""
    if value >= p75:
        return "above_p75"
    if value <= p25:
        return "below_p25"
    if value >= p50:
        return "above_median"
    return "below_median"


def within_archetype_signal_positioning(
    user_signal_rates: dict[str, float],
    user_det_medians: dict[str, float],
    cohort_dist: dict[str, dict[str, float]],
) -> list[dict]:
    """For each signal where we have cohort distribution data, position
    the user against their archetype's cohort. Returns entries sorted
    by absolute distance from cohort median (most distinctive first),
    so the report writer can lead with where the user is *most* unlike
    their fellow archetype-members.

    Each entry: ``{signal, signal_kind, your_value, cohort_p25,
    cohort_p50, cohort_p75, cohort_mean, n_users, position,
    delta_from_median}``.
    """
    out: list[dict] = []
    for sig, dist in cohort_dist.items():
        if dist.get("signal_kind") == "deterministic":
            your_value = user_det_medians.get(sig)
        else:
            your_value = user_signal_rates.get(sig)
        if your_value is None:
            continue
        p25, p50, p75 = dist["p25"], dist["p50"], dist["p75"]
        out.append({
            "signal": sig,
            "signal_kind": dist["signal_kind"],
            "your_value": _round(your_value, 4),
            "cohort_p25": _round(p25, 4),
            "cohort_p50": _round(p50, 4),
            "cohort_p75": _round(p75, 4),
            "cohort_mean": _round(dist["mean"], 4),
            "n_users": dist["n_users"],
            "position": _percentile_band(your_value, p25, p50, p75),
            "delta_from_median": _round(your_value - p50, 4),
        })
    # Most-distinctive-first.
    out.sort(key=lambda r: abs(r["delta_from_median"]), reverse=True)
    return out


def within_archetype_categorical_positioning(
    user_distributions: dict[str, dict[str, float]],
    cohort_distributions: dict[str, dict[str, dict[str, float]]],
) -> dict[str, dict]:
    """Compare the user's categorical distributions (session_shape,
    interaction_style, ...) against their archetype's cohort. For each
    field, return the user pct, cohort pct, and the per-bucket delta.
    """
    out: dict[str, dict] = {}
    for field, cohort_buckets in cohort_distributions.items():
        user_buckets = user_distributions.get(field) or {}
        all_buckets = set(user_buckets) | set(cohort_buckets)
        rows: list[dict] = []
        for bucket in all_buckets:
            user_pct = _round(user_buckets.get(bucket, 0.0), 1)
            cohort_pct = _round(
                (cohort_buckets.get(bucket) or {}).get("share_pct", 0.0), 1
            )
            rows.append({
                "bucket": bucket,
                "your_pct": user_pct,
                "cohort_pct": cohort_pct,
                "delta_pct": _round(user_pct - cohort_pct, 1),
            })
        rows.sort(key=lambda r: abs(r["delta_pct"]), reverse=True)
        out[field] = {"buckets": rows}
    return out


def _fingerprint_sharpness(primary: str, primary_score: float,
                           baseline: dict) -> str:
    """Where does the user's primary_score sit within the cohort's
    score distribution? ``sharp`` (>=p75), ``typical`` (p25..p75),
    ``borderline`` (<p25). Returns ``""`` when percentiles aren't
    available or the user is unclassified."""
    if primary == archetypes_module.UNASSIGNED:
        return ""
    p25 = baseline.get("primary_score_p25") or 0.0
    p75 = baseline.get("primary_score_p75") or 0.0
    if p25 == 0 and p75 == 0:
        return ""
    if primary_score >= p75:
        return "sharp"
    if primary_score < p25:
        return "borderline"
    return "typical"


def signature_sessions(pairs: list[tuple[dict, dict]],
                       archetype_name: str,
                       k: int = 3) -> list[dict]:
    """Find the top-k sessions that exemplify the user's archetype.

    A session "exemplifies" the archetype to the degree its fired signals
    overlap with the archetype's canonical (weight 1.0) and bonus
    (weight 0.5) signals, scaled by signal strength.

    Returns ``[{session_id, date, score, defining_signal, evidence,
    n_defining_signals_fired, all_defining_signals}]``, sorted by score
    descending then date descending. Used by the report writer to
    produce "like that time you XYZ" anecdotes — without this, the
    writer has to dig through raw transcripts.

    Returns ``[]`` for The Multi-Mode Journeyman (no canonical fingerprint).
    """
    archetype = next(
        (a for a in archetypes_module.ARCHETYPES if a.name == archetype_name),
        None,
    )
    if archetype is None:
        return []

    canonical_set = {sig for sig, _ in archetype.canonical}
    bonus_set = {sig for sig, _ in archetype.bonus}

    scored: list[dict] = []
    for row, ann in pairs:
        signals = ann.get("signals") or {}
        if not isinstance(signals, dict):
            continue
        hits: list[tuple[str, dict, float, int]] = []
        for sig_name, sig_data in signals.items():
            if sig_name in canonical_set:
                weight = 1.0
            elif sig_name in bonus_set:
                weight = 0.5
            else:
                continue
            strength = 0
            if isinstance(sig_data, dict):
                s = sig_data.get("strength")
                if isinstance(s, int):
                    strength = s
            hits.append((sig_name, sig_data, weight, strength))
        if not hits:
            continue
        score = sum(w * (1.0 + 0.1 * st) for _, _, w, st in hits)
        # The "headline" signal for this session is the highest-weighted
        # one (canonical > bonus); ties break on strength.
        hits.sort(key=lambda h: (-h[2], -h[3]))
        head_sig, head_data, _, _ = hits[0]
        evidence = ""
        if isinstance(head_data, dict):
            evidence = (head_data.get("evidence") or "").strip()
        scored.append({
            "session_id": row.get("session_id", ""),
            "date": (row.get("date") or "").strip(),
            "score": _round(score, 2),
            "n_defining_signals_fired": len(hits),
            "defining_signal": head_sig,
            "evidence": evidence,
            "all_defining_signals": [h[0] for h in hits],
        })

    # Score DESC, then date DESC. ISO date strings sort lexicographically.
    scored.sort(key=lambda s: (s["score"], s["date"]), reverse=True)
    return scored[:k]


def _archetype_confidence(primary: str,
                          primary_score: float,
                          secondary_score: float,
                          n_sessions: int) -> str:
    """Coarse confidence bucket — high / moderate / low — for the report
    writer to lean on. Thresholds are deliberately lax in v1; refine
    once we have within-archetype fingerprint-score distributions
    from research."""
    margin = primary_score - secondary_score
    if primary == archetypes_module.UNASSIGNED:
        # Multi-Mode Journeyman is a positive label when there's enough data to
        # call it deliberate, "tentative" when not.
        return "moderate" if n_sessions >= 15 else "low"
    if primary_score >= 4.0 and n_sessions >= 10 and margin >= 1.5:
        return "high"
    if primary_score >= 2.0 and n_sessions >= 5 and margin >= 1.0:
        return "moderate"
    return "low"


def archetype_summary(
    pairs: list[tuple[dict, dict]],
    signals: dict,
    det_medians: dict[str, float],
    archetype_data: dict[str, dict],
    signal_distributions: dict[str, dict[str, dict[str, float]]],
    categorical_distributions: dict[str, dict[str, dict[str, dict[str, float]]]],
    user_categoricals: dict[str, dict[str, float]],
) -> dict:
    """Assign the user an archetype, attach baselines + profile, place
    them within the cohort distributions, and surface signature
    sessions. The report leads with this block."""
    if not pairs:
        return {
            "available": False,
            "note": "no usable annotations to classify",
        }

    # Flat {signal_name: rate} dict for the classifier.
    signal_rates = {sig: info.get("rate", 0.0) for sig, info in signals.items()}

    primary, primary_score, secondary, secondary_score, all_scores = (
        archetypes_module.assign_archetype(signal_rates, det_medians)
    )

    n = len(pairs)
    margin = primary_score - secondary_score
    decisive = primary != archetypes_module.UNASSIGNED and margin >= 1.0
    confidence = _archetype_confidence(
        primary, primary_score, secondary_score, n
    )

    baseline = archetype_data.get(primary, {}) or {}
    secondary_baseline = (
        archetype_data.get(secondary, {}) or {} if secondary else {}
    )

    # Shadow lives in the profile (research-authored). Fall back to ""
    # gracefully — Multi-Mode Journeyman intentionally has shadow_archetype: null.
    shadow_name = (baseline.get("shadow_archetype") or "") or ""
    shadow_axis = (baseline.get("shadow_axis") or "") or ""
    if not isinstance(shadow_name, str):
        shadow_name = ""
    shadow_baseline = (
        archetype_data.get(shadow_name, {}) or {} if shadow_name else {}
    )

    # Within-archetype positioning — where does the user sit *within*
    # their cohort across signals + categoricals?
    cohort_signal_dist = signal_distributions.get(primary, {}) or {}
    cohort_cat_dist = categorical_distributions.get(primary, {}) or {}
    signal_positioning = within_archetype_signal_positioning(
        signal_rates, det_medians, cohort_signal_dist
    )
    categorical_positioning = within_archetype_categorical_positioning(
        user_categoricals, cohort_cat_dist
    )

    sharpness = _fingerprint_sharpness(primary, primary_score, baseline)

    sig_sessions = signature_sessions(pairs, primary, k=3)

    return {
        "available": True,
        "primary": primary,
        "primary_score": _round(primary_score, 2),
        "secondary": secondary or "",
        "secondary_score": _round(secondary_score, 2),
        "margin": _round(margin, 2),
        "decisive": decisive,
        "confidence": confidence,
        "fingerprint_sharpness": sharpness,
        "all_scores": {k: _round(v, 2) for k, v in all_scores.items()},
        "user_signal_rates": {k: _round(v, 4) for k, v in signal_rates.items()},
        "user_det_medians": det_medians,
        "baseline": baseline,
        "secondary_baseline": secondary_baseline,
        "shadow": {
            "name": shadow_name,
            "axis": shadow_axis,
            "baseline": shadow_baseline,
        },
        "within_archetype_positioning": {
            "signals": signal_positioning,
            "categoricals": categorical_positioning,
        },
        "signature_sessions": sig_sessions,
    }


def _describe_position(value: float, base: dict) -> str:
    """Where does ``value`` sit relative to the baseline percentiles?

    Returns a coarse label that the report writer can lean on: 'above_p90',
    'above_p75', 'near_baseline' (between p25 and p75), 'below_p25',
    'below_p10'. The label intentionally compresses information — the
    underlying numbers are also exposed for fine-grained comparison.
    """
    if value >= base["p90"]:
        return "above_p90"
    if value >= base["p75"]:
        return "above_p75"
    if value <= base["p10"]:
        return "below_p10"
    if value <= base["p25"]:
        return "below_p25"
    return "near_baseline"


# =====================================================================
# Structural metrics — from raw CSV cols (no LLM needed)
# =====================================================================

def structural_metrics(rows: list[dict]) -> dict:
    """Stats over raw session rows. Works on tagged CSV directly."""
    durations = [_parse_float(r.get("duration_s")) for r in rows
                 if r.get("duration_s")]
    n_msgs = [_parse_int(r.get("n_messages")) for r in rows]
    n_user = [_parse_int(r.get("n_user_prompts")) for r in rows]
    n_tools = [_parse_int(r.get("n_tool_calls")) for r in rows]
    n_errs = [_parse_int(r.get("n_tool_errors")) for r in rows]

    has_pr = sum(1 for r in rows if r.get("has_pr") == "true")

    models: Counter[str] = Counter()
    projects: Counter[str] = Counter()
    for r in rows:
        if r.get("model"):
            models[r["model"]] += 1
        if r.get("project"):
            projects[r["project"]] += 1

    # Tool usage aggregated across sessions.
    tool_counts: Counter[str] = Counter()
    for r in rows:
        for pair in (r.get("tools_used") or "").split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            name, cnt = pair.rsplit(":", 1)
            try:
                tool_counts[name] += int(cnt)
            except ValueError:
                pass

    n = len(rows)
    return {
        "n_sessions": n,
        "date_range": {
            "earliest": min((r.get("date") or "" for r in rows if r.get("date")),
                            default=None),
            "latest": max((r.get("date") or "" for r in rows if r.get("date")),
                          default=None),
        },
        "duration_s": {
            "median": _round(statistics.median(durations), 1) if durations else 0,
            "p90": _round(_percentile(durations, 0.9), 1),
            "mean": _round(statistics.mean(durations), 1) if durations else 0,
        },
        "messages_per_session": {
            "median": _round(statistics.median(n_msgs), 1) if n_msgs else 0,
            "mean": _round(statistics.mean(n_msgs), 1) if n_msgs else 0,
        },
        "user_prompts_per_session": {
            "median": _round(statistics.median(n_user), 1) if n_user else 0,
            "mean": _round(statistics.mean(n_user), 1) if n_user else 0,
        },
        "tool_calls_per_session": {
            "median": _round(statistics.median(n_tools), 1) if n_tools else 0,
            "mean": _round(statistics.mean(n_tools), 1) if n_tools else 0,
        },
        "tool_error_rate": _round(sum(n_errs) / max(sum(n_tools), 1), 4),
        "pr_rate_pct": _round(has_pr * 100 / n, 1) if n else 0,
        "top_tools": dict(tool_counts.most_common(15)),
        "models_used": dict(models),
        "top_projects": dict(projects.most_common(10)),
    }


def subagent_metrics(parent_rows: list[dict],
                     full_rows: list[dict] | None) -> dict:
    """Subagent usage stats. Needs the full (un-filtered) raw CSV.

    If `full_rows` is None, returns an empty / unavailable marker.
    """
    if full_rows is None:
        return {"available": False, "note": "raw sessions.csv not provided"}

    parent_ids = {r.get("session_id") for r in parent_rows}
    sub_by_parent: Counter[str] = Counter()
    total_subs = 0
    for r in full_rows:
        if r.get("is_subagent") != "true":
            continue
        pid = r.get("parent_session_id") or ""
        if pid in parent_ids:
            sub_by_parent[pid] += 1
            total_subs += 1

    n_with_sub = sum(1 for pid in parent_ids if sub_by_parent.get(pid, 0) > 0)
    n = len(parent_ids) or 1
    return {
        "available": True,
        "total_subagents": total_subs,
        "parent_sessions": len(parent_ids),
        "parents_with_subagents": n_with_sub,
        "parents_with_subagents_pct": _round(n_with_sub * 100 / n, 1),
        "subagents_per_parent_mean": _round(total_subs / n, 2),
    }


# =====================================================================
# Baseline comparison
# =====================================================================

def load_baselines(baselines_dir: Path) -> dict:
    """Read all CSVs in the baselines dir into a {filename: list[dict]} map."""
    if not baselines_dir.exists():
        return {}
    out: dict[str, list[dict]] = {}
    for p in sorted(baselines_dir.glob("*.csv")):
        try:
            out[p.stem] = read_csv(p)
        except Exception as exc:
            log.warning("could not read baseline %s: %s", p, exc)
    return out


def compare_to_baselines(signal_rates: dict, category_rates: dict,
                         engagement: dict, style: dict,
                         baselines: dict) -> dict:
    """Produce diff metrics: user vs corpus. Empty dict if no baselines."""
    if not baselines:
        return {"available": False}

    out: dict[str, Any] = {"available": True, "source": "baselines/"}

    # Signal rate baseline -> {signal: {your_rate_pct, baseline_rate_pct, delta_pct}}
    sig_base = {}
    for row in baselines.get("signal_rates", []) or []:
        name = row.get("signal")
        if not name:
            continue
        try:
            sig_base[name] = float(row.get("rate_pct") or 0)
        except ValueError:
            continue

    sig_diffs: list[dict] = []
    for sig, info in signal_rates.items():
        b = sig_base.get(sig)
        if b is None:
            continue
        delta = info["rate_pct"] - b
        sig_diffs.append({
            "signal": sig,
            "category": info["category"],
            "your_rate_pct": info["rate_pct"],
            "baseline_rate_pct": _round(b, 1),
            "delta_pct": _round(delta, 1),
        })

    # Category baseline.
    cat_base = {}
    for row in baselines.get("category_rates", []) or []:
        cid = row.get("category")
        if cid:
            try:
                cat_base[cid] = float(row.get("rate_pct") or 0)
            except ValueError:
                pass
    cat_diffs = []
    for cat, info in category_rates.items():
        b = cat_base.get(cat)
        if b is None:
            continue
        cat_diffs.append({
            "category": cat,
            "your_rate_pct": info["rate_pct"],
            "baseline_rate_pct": _round(b, 1),
            "delta_pct": _round(info["rate_pct"] - b, 1),
        })

    # Engagement / style distribution baselines (optional).
    def _dist_diff(your_pct: dict, baseline_rows: list[dict],
                   key_field: str) -> list[dict]:
        b: dict[str, float] = {}
        for row in baseline_rows or []:
            k = row.get(key_field)
            if k:
                try:
                    b[k] = float(row.get("rate_pct") or 0)
                except ValueError:
                    pass
        return [{
            "key": k,
            "your_rate_pct": _round(your_pct.get(k, 0), 1),
            "baseline_rate_pct": _round(b.get(k, 0), 1),
            "delta_pct": _round(your_pct.get(k, 0) - b.get(k, 0), 1),
        } for k in sorted(set(your_pct) | set(b))]

    out["engagement_depth"] = _dist_diff(
        engagement["percentages"],
        baselines.get("engagement_depth_distribution", []),
        "engagement_depth",
    )
    out["interaction_style"] = _dist_diff(
        style["percentages"],
        baselines.get("interaction_style_distribution", []),
        "interaction_style",
    )

    # Signal diffs sorted by absolute delta.
    sig_diffs.sort(key=lambda r: -abs(r["delta_pct"]))

    out["signals_full"] = sig_diffs
    out["categories"] = cat_diffs

    # Highlights — over-represented (your_rate > baseline) and under-.
    # Anti-patterns are detected by signal name (v2.1 schema groups them
    # under "behavior" alongside positive signals — there's no dedicated
    # category any more), so we use the ANTI_PATTERN_SIGNALS set.
    over = [s for s in sig_diffs if s["delta_pct"] > 0
            and s["signal"] not in ANTI_PATTERN_SIGNALS]
    under = [s for s in sig_diffs if s["delta_pct"] < 0
             and s["signal"] not in ANTI_PATTERN_SIGNALS]
    anti_over = [s for s in sig_diffs if s["delta_pct"] > 0
                 and s["signal"] in ANTI_PATTERN_SIGNALS]
    out["top_over_represented"] = over[:5]
    out["top_under_represented"] = sorted(under, key=lambda r: r["delta_pct"])[:5]
    out["anti_pattern_alerts"] = sorted(
        anti_over, key=lambda r: -r["delta_pct"])[:5]

    return out


# =====================================================================
# Entry point
# =====================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute metrics from a tagged sessions CSV."
    )
    parser.add_argument("tagged_csv", type=Path,
                        help="Tagged CSV from tagging/tag_sessions.py")
    parser.add_argument("--raw", type=Path, default=None,
                        help="Raw sessions CSV (parents+subagents) for subagent stats.")
    parser.add_argument("--baselines", type=Path, default=DEFAULT_BASELINES,
                        help=f"Baselines dir (default: {DEFAULT_BASELINES})")
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY,
                        help=f"Taxonomy JSON (default: {DEFAULT_TAXONOMY})")
    parser.add_argument("--out", type=Path, default=Path("report/metrics.json"))
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if not args.tagged_csv.exists():
        log.error("tagged CSV does not exist: %s", args.tagged_csv)
        return 2

    taxonomy = load_taxonomy(args.taxonomy)
    sig_to_cat = signal_to_category_map(taxonomy)

    rows = read_csv(args.tagged_csv)
    pairs = _annotation_rows(rows)

    if not pairs:
        log.warning("no usable annotations found in %s", args.tagged_csv)

    full_rows = read_csv(args.raw) if args.raw and args.raw.exists() else None

    engagement = engagement_depth_distribution(pairs)
    style = interaction_style_distribution(pairs)
    task_type = task_type_distribution(pairs)
    arc_shape = arc_shape_distribution(pairs)
    signals = signal_fire_rates(pairs, sig_to_cat)
    cats = category_fire_rates(pairs, sig_to_cat)
    rc = reality_contact_summary(pairs)
    structural = structural_metrics(rows)
    subagents = subagent_metrics(rows, full_rows)

    baselines = load_baselines(args.baselines)
    comparison = compare_to_baselines(signals, cats, engagement, style, baselines)
    deterministic = deterministic_signal_summary(rows, taxonomy, baselines)

    # Shapes — successor to v1's persona archetypes. Compose deterministic
    # + interpretive signals into a named interaction pattern per session.
    # Only rows with usable annotations get classified; rows whose tagging
    # errored aren't here (pairs already filters them out).
    classifiable_rows = [r for (r, _ann) in pairs]
    shape_data = shape_summary(classifiable_rows)

    # User-archetype layer: the horoscope-quality framing. Composes the
    # per-signal fire rates + deterministic medians into one of seven
    # named archetypes (or The Multi-Mode Journeyman). Baselines + profile fields
    # come from baselines/archetype_baselines.csv +
    # baselines/archetype_profiles.json. Within-cohort positioning uses
    # the new signal- and categorical-distribution baselines.
    det_medians = deterministic_medians(rows, taxonomy)
    archetype_data = load_archetype_data(args.baselines)
    signal_distributions = load_archetype_signal_distributions(args.baselines)
    categorical_distributions = (
        load_archetype_categorical_distributions(args.baselines)
    )
    effect_sizes = load_signal_effect_sizes(args.baselines)

    # User's categorical distributions, keyed by the same field names
    # the cohort CSV uses, so within-archetype categorical positioning
    # can join them.
    user_categoricals = {
        "session_shape": (shape_data.get("distribution") or {}),
        "interaction_style": style.get("percentages", {}),
        "engagement_depth": engagement.get("percentages", {}),
        "task_size": (deterministic.get("task_size") or {}).get("your", {}),
        "arc_shape": arc_shape.get("percentages", {}),
        "task_type": task_type.get("percentages", {}),
    }
    # session_shape distribution is shaped {name: {n, share_pct, ...}};
    # flatten to {name: share_pct} to match other distributions.
    user_categoricals["session_shape"] = {
        name: info.get("share_pct", 0.0)
        for name, info in user_categoricals["session_shape"].items()
    }

    user_archetype = archetype_summary(
        pairs,
        signals,
        det_medians,
        archetype_data,
        signal_distributions,
        categorical_distributions,
        user_categoricals,
    )

    metrics = {
        "schema_version": "2.3",
        "n_sessions_tagged": len(pairs),
        "n_sessions_total": len(rows),
        "user_archetype": user_archetype,
        "signal_effect_sizes": effect_sizes,
        "shapes": shape_data,
        "engagement_depth": engagement,
        "interaction_style": style,
        "task_type": task_type,
        "arc_shape": arc_shape,
        "signals": signals,
        "categories": cats,
        "deterministic_signals": deterministic,
        "reality_contact": rc,
        "structural": structural,
        "subagents": subagents,
        "comparison_to_baseline": comparison,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(metrics, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    print()
    print("=" * 64)
    print(f"Wrote metrics to {args.out}")
    print("=" * 64)
    print(f"  sessions tagged: {len(pairs)} / {len(rows)}")
    if user_archetype.get("available"):
        ua = user_archetype
        sec = (f", secondary {ua['secondary']} ({ua['secondary_score']})"
               if ua.get("secondary") else "")
        print(f"  archetype: {ua['primary']} "
              f"(score {ua['primary_score']}, "
              f"confidence {ua['confidence']}){sec}")
        sig = ua.get("signature_sessions") or []
        if sig:
            print(f"  signature sessions: {len(sig)} "
                  f"(top defining signal: {sig[0]['defining_signal']})")
    if shape_data.get("available"):
        print(f"  modal shape: {shape_data['modal_shape']} "
              f"({shape_data['modal_share_pct']}% of sessions)")
        print(f"  shape entropy: {shape_data['shape_entropy']} "
              f"({shape_data['n_distinct_shapes']} distinct shapes)")
        # Top 3 shapes for the at-a-glance summary.
        top_shapes = sorted(
            shape_data["distribution"].items(),
            key=lambda kv: -kv[1]["share_pct"],
        )[:3]
        print(f"  top shapes: "
              f"{[(name, info['share_pct']) for name, info in top_shapes]}")
    print(f"  engagement depth (%): "
          f"{json.dumps(engagement['percentages'], sort_keys=True)}")
    print(f"  interaction style (%): "
          f"{json.dumps(style['percentages'], sort_keys=True)}")
    print(f"  task type (%): "
          f"{json.dumps(task_type['percentages'], sort_keys=True)}")
    print(f"  arc shape (%): "
          f"{json.dumps(arc_shape['percentages'], sort_keys=True)}")
    print(f"  reality-contact moments/session: {rc['moments_per_session']}")
    if deterministic.get("available"):
        n_det = len(deterministic.get("signals", []))
        print(f"  deterministic signals summarized: {n_det}")
        ts = deterministic.get("task_size", {})
        if ts.get("your"):
            print(f"  task_size (%): {json.dumps(ts['your'], sort_keys=True)}")
    else:
        note = deterministic.get("note", "unavailable")
        print(f"  deterministic signals: not available ({note})")
    if comparison.get("available"):
        top_over = comparison.get("top_over_represented", [])[:3]
        top_under = comparison.get("top_under_represented", [])[:3]
        print(f"  top over vs baseline: "
              f"{[(s['signal'], s['delta_pct']) for s in top_over]}")
        print(f"  top under vs baseline: "
              f"{[(s['signal'], s['delta_pct']) for s in top_under]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
