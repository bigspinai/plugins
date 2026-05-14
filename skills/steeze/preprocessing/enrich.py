#!/usr/bin/env python3
"""Enrich a sessions CSV with **deterministic** signals from
``tagging/taxonomy.json``.

Reads the CSV produced by ``sessions_to_csv.py`` and writes an enriched
copy with one column per deterministic signal listed in the schema.
**No LLM call.** Every signal is a function of the messages structure:
counts, ratios, timestamps, tool names, command substrings.

Together with the LLM-tagged interpretive layer, these signals form the
unified signal stream the report consumes. The deterministic layer
carries the strongest predictors of session outcomes (iteration count,
tool diversity, course corrections, the verification arc) and is free
to compute — no API cost, fully reproducible, instantaneous.

Pipeline
--------
    sessions_to_csv.py   →  sessions.csv
        ↓ enrich.py (this)
    sessions_enriched.csv  ← deterministic signal columns added
        ↓ tag_sessions.py
    tagged_sessions.csv    ← interpretive annotation column added
        ↓ compute_metrics.py
    metrics.json

Usage
-----
    python preprocessing/enrich.py sessions.csv [--out OUT.csv]
                                                [--schema SCHEMA.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

csv.field_size_limit(min(2**31 - 1, sys.maxsize))

log = logging.getLogger("enrich")
HERE = Path(__file__).resolve().parent
DEFAULT_SCHEMA = HERE.parent / "tagging" / "taxonomy.json"


# =====================================================================
# Tunable thresholds
# =====================================================================

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
RESEARCH_TOOLS = frozenset({"Read", "Grep", "Glob", "WebFetch", "WebSearch",
                            "NotebookRead"})

LONG_PAUSE_S = 5 * 60
PAUSE_CAP_S = 24 * 3600       # cap individual pauses (multi-day sessions)
COURSE_CORRECTION_CHAR_LIMIT = 60

TEST_RUNNER_PATTERNS = (
    "pytest", "npm test", "npm run test", "yarn test", "yarn run test",
    "cargo test", "go test", "make test", "mvn test", "mvn verify",
    "gradle test", "./gradlew test", "jest", "vitest", "mocha", "rspec",
    "phpunit", "composer test", "rake test", "rake spec", "tox",
    "nosetests", "deno test", "bun test", "rustc --test",
)
DIFF_REVIEW_PATTERNS = ("git diff", "git status", "git show", "git log -p")


# =====================================================================
# Helpers
# =====================================================================

def _iter_csv_lines(path: Path):
    with open(path, "rb") as fh:
        for raw in fh:
            yield raw.replace(b"\x00", b"").decode("utf-8", errors="replace")


def _parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _segments_by_user_prompt(messages: list[dict]) -> list[list[dict]]:
    """Split messages into per-iteration segments, one per user prompt."""
    seg_starts = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    seg_starts.append(len(messages))
    return [messages[seg_starts[i]: seg_starts[i + 1]]
            for i in range(len(seg_starts) - 1)]


def _bash_command(tool_msg: dict) -> str:
    if tool_msg.get("name") != "Bash":
        return ""
    return tool_msg.get("args_preview") or tool_msg.get("content") or ""


# =====================================================================
# Per-signal computers — names match the schema entries exactly.
# =====================================================================

def _iteration_count(msgs: list[dict]) -> float:
    return float(sum(1 for m in msgs if m.get("role") == "user"))


def _edit_iteration_count(msgs: list[dict]) -> float:
    n = 0
    for seg in _segments_by_user_prompt(msgs):
        if any(m.get("role") == "tool" and m.get("name") in EDIT_TOOLS
               for m in seg):
            n += 1
    return float(n)


def _research_iteration_count(msgs: list[dict]) -> float:
    n = 0
    for seg in _segments_by_user_prompt(msgs):
        if any(m.get("role") == "tool" and m.get("name") in RESEARCH_TOOLS
               for m in seg):
            n += 1
    return float(n)


def _tool_call_density(msgs: list[dict]) -> float:
    n_user = sum(1 for m in msgs if m.get("role") == "user")
    n_tool = sum(1 for m in msgs if m.get("role") == "tool")
    return float(n_tool / n_user) if n_user else 0.0


def _tool_diversity(msgs: list[dict]) -> float:
    names = [m.get("name") for m in msgs
             if m.get("role") == "tool" and m.get("name")]
    return float(len(set(names)) / len(names)) if names else 0.0


def _edit_focus(msgs: list[dict]) -> float:
    edits_per_file: dict[str, int] = {}
    total = 0
    for m in msgs:
        if m.get("role") != "tool" or m.get("name") not in EDIT_TOOLS:
            continue
        target = m.get("target") or "<unknown>"
        edits_per_file[target] = edits_per_file.get(target, 0) + 1
        total += 1
    return float(max(edits_per_file.values()) / total) if total else 0.0


def _course_correction_count(msgs: list[dict]) -> float:
    cc = 0
    last_was_edit = False
    for m in msgs:
        if m.get("role") == "tool" and m.get("name") in EDIT_TOOLS:
            last_was_edit = True
        elif m.get("role") == "user":
            if last_was_edit:
                content = m.get("content") or ""
                if 0 < len(content) <= COURSE_CORRECTION_CHAR_LIMIT:
                    cc += 1
            last_was_edit = False
    return float(cc)


def _pauses(msgs: list[dict]) -> list[float]:
    pauses: list[float] = []
    last_ts: Optional[datetime] = None
    for m in msgs:
        ts = _parse_ts(m.get("ts"))
        if ts and last_ts:
            dt = (ts - last_ts).total_seconds()
            pauses.append(min(max(dt, 0.0), float(PAUSE_CAP_S)))
        last_ts = ts or last_ts
    return pauses


def _late_pause_count(msgs: list[dict]) -> float:
    pauses = _pauses(msgs)
    if not pauses:
        return 0.0
    half = len(pauses) // 2
    return float(sum(1 for p in pauses[half:] if p >= LONG_PAUSE_S))


def _median_pause_seconds(msgs: list[dict]) -> float:
    pauses = _pauses(msgs)
    return float(statistics.median(pauses)) if pauses else 0.0


def _prompt_length_ratio(msgs: list[dict]) -> float:
    chars = [len(m.get("content") or "") for m in msgs if m.get("role") == "user"]
    if len(chars) < 4:
        return 1.0
    half = len(chars) // 2
    first = sum(chars[:half]) / half if half else 1.0
    second = sum(chars[half:]) / (len(chars) - half)
    return float(second / first) if first else 1.0


def _tests_attempted(msgs: list[dict]) -> float:
    for m in msgs:
        cmd = _bash_command(m).lower()
        if any(p in cmd for p in TEST_RUNNER_PATTERNS):
            return 1.0
    return 0.0


def _tests_run_count(msgs: list[dict]) -> float:
    n = 0
    for m in msgs:
        cmd = _bash_command(m).lower()
        if any(p in cmd for p in TEST_RUNNER_PATTERNS):
            n += 1
    return float(n)


def _diff_reviewed(msgs: list[dict]) -> float:
    bash_indices = [i for i, m in enumerate(msgs)
                    if m.get("role") == "tool" and m.get("name") == "Bash"]
    if not bash_indices:
        return 0.0
    cutoff = bash_indices[max(0, int(len(bash_indices) * 0.75))]
    for i in bash_indices:
        if i < cutoff:
            continue
        cmd = _bash_command(msgs[i]).lower()
        if any(p in cmd for p in DIFF_REVIEW_PATTERNS):
            return 1.0
    return 0.0


def _task_size(msgs: list[dict]) -> str:
    """Bucket the session by iteration_count into a task-size proxy.

    Cuts:
      small      ≤3 user prompts
      medium     4–15
      large      16–50
      very_large >50

    iteration_count is the strongest single deterministic predictor of
    PR-yield in the SWE-chat corpus (top-Q vs bottom-Q delta ≈ +23 pp),
    and it correlates well with task scope — a defensible single-variable
    proxy for "how big a task did the user attempt." Returned as a
    categorical string so analyses can stratify directly.
    """
    n = sum(1 for m in msgs if m.get("role") == "user")
    if n <= 3:
        return "small"
    if n <= 15:
        return "medium"
    if n <= 50:
        return "large"
    return "very_large"


# Map signal name → computer. Only deterministic signals appear here.
COMPUTERS: dict[str, Callable[[list[dict]], Any]] = {
    "iteration_count":          _iteration_count,
    "edit_iteration_count":     _edit_iteration_count,
    "research_iteration_count": _research_iteration_count,
    "tool_call_density":        _tool_call_density,
    "tool_diversity":           _tool_diversity,
    "edit_focus":               _edit_focus,
    "course_correction_count":  _course_correction_count,
    "late_pause_count":         _late_pause_count,
    "median_pause_seconds":     _median_pause_seconds,
    "prompt_length_ratio":      _prompt_length_ratio,
    "task_size":                _task_size,
    "tests_attempted":          _tests_attempted,
    "tests_run_count":          _tests_run_count,
    "diff_reviewed":            _diff_reviewed,
}


# =====================================================================
# Schema integration
# =====================================================================

def deterministic_signals_from_schema(schema_path: Path) -> list[tuple[str, dict]]:
    """Return ``[(signal_name, signal_metadata), ...]`` for every
    deterministic signal in the schema, in iteration order. Errors loudly
    on schema/code drift to keep the two in sync."""
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    signals = schema.get("signals", {})
    deterministic = [
        (name, meta) for name, meta in signals.items()
        if meta.get("computation") == "deterministic"
    ]

    schema_names = {n for n, _ in deterministic}
    code_names = set(COMPUTERS.keys())
    missing_in_code = schema_names - code_names
    missing_in_schema = code_names - schema_names
    if missing_in_code:
        raise SystemExit(
            f"Schema has deterministic signals with no computer: {sorted(missing_in_code)}"
        )
    if missing_in_schema:
        raise SystemExit(
            f"Code has computers for signals not in schema: {sorted(missing_in_schema)}"
        )
    return deterministic


def compute_all(messages: list[dict],
                signal_order: list[tuple[str, dict]]) -> dict[str, Any]:
    """Run every deterministic signal computer; failures fall back to a
    sensible default for the signal's value_type."""
    out: dict[str, Any] = {}
    for name, meta in signal_order:
        try:
            value = COMPUTERS[name](messages)
            if meta.get("value_type") == "categorical":
                out[name] = str(value) if value else ""
            else:
                out[name] = float(value)
        except Exception as exc:
            log.debug("signal %s failed: %s", name, exc)
            out[name] = "" if meta.get("value_type") == "categorical" else 0.0
    return out


def _format_value(name: str, meta: dict, value: Any) -> str:
    """Render a signal value for the CSV based on its schema-declared
    ``value_type``."""
    vt = meta.get("value_type", "ratio")
    if vt == "categorical":
        return str(value) if value else ""
    if vt == "bool":
        return "1" if value else "0"
    if vt == "count":
        return str(int(round(value)))
    if vt == "seconds":
        return str(int(round(value)))
    return f"{value:.4f}"


# =====================================================================
# Driver
# =====================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute deterministic signals from a sessions CSV "
                    "and write an enriched copy."
    )
    parser.add_argument(
        "input_csv", type=Path,
        help="Sessions CSV (output of preprocessing/sessions_to_csv.py).",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output CSV path. Defaults to <input_basename>_enriched.csv.",
    )
    parser.add_argument(
        "--schema", type=Path, default=DEFAULT_SCHEMA,
        help=f"Path to taxonomy.json (default: {DEFAULT_SCHEMA}).",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if not args.input_csv.exists():
        log.error("input not found: %s", args.input_csv)
        return 2
    if not args.schema.exists():
        log.error("schema not found: %s", args.schema)
        return 2

    signal_order = deterministic_signals_from_schema(args.schema)
    signal_columns = [name for name, _ in signal_order]
    log.info("loaded schema: %d deterministic signals", len(signal_order))

    out_path: Path = args.out or args.input_csv.with_name(
        args.input_csv.stem + "_enriched.csv"
    )

    log.info("reading %s", args.input_csv)
    reader = csv.DictReader(_iter_csv_lines(args.input_csv))
    in_fields = list(reader.fieldnames or [])
    if "messages" not in in_fields:
        raise SystemExit(
            f"{args.input_csv} has no 'messages' column — is this a "
            "sessions CSV from preprocessing/sessions_to_csv.py?"
        )
    if "session_id" not in in_fields:
        raise SystemExit(f"{args.input_csv} has no 'session_id' column.")

    # Drop any stale signal columns from a prior enrichment run.
    out_fields = [f for f in in_fields if f not in signal_columns]
    out_fields.extend(signal_columns)

    log.info("writing %s", out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n = 0
    n_messages_parse_errors = 0
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=out_fields,
                                quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row in reader:
            try:
                messages = json.loads(row.get("messages") or "[]")
                if not isinstance(messages, list):
                    messages = []
            except (json.JSONDecodeError, ValueError):
                messages = []
                n_messages_parse_errors += 1
            values = compute_all(messages, signal_order)
            out_row = {f: row.get(f, "") for f in out_fields if f not in signal_columns}
            for name, meta in signal_order:
                out_row[name] = _format_value(name, meta, values[name])
            writer.writerow(out_row)
            n += 1
            if args.verbose and n % 1000 == 0:
                log.debug("  enriched %d rows", n)

    print()
    print("=" * 64)
    print(f"Enriched {n:,} rows  →  {out_path}")
    print("=" * 64)
    print("  Deterministic signal columns added "
          f"({len(signal_columns)}): {', '.join(signal_columns)}")
    if n_messages_parse_errors:
        print(f"  messages parse errors (signals zeroed): {n_messages_parse_errors}")
    print()
    print("Next: tag the same CSV with interpretive signals via")
    print(f"  python tagging/tag_sessions.py {out_path} --out tagged_sessions.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
