#!/usr/bin/env python3
"""Tag a sessions CSV with the AI Coding Practice Taxonomy via the Anthropic API.

Reads the CSV produced by `preprocessing/sessions_to_csv.py`, picks the most
recent N parent sessions (subagents excluded — those are CC-internal, not user
practice), prints a cost estimate, asks for confirmation, then calls Claude
in parallel to produce one annotation per session. Output: a CSV with an
extra `annotation` column holding the JSON annotation.

Defaults:
    - Sonnet 4.6
    - 50 most recent parent sessions
    - 5 concurrent API calls
    - prompt caching on the system prompt

Requires: ANTHROPIC_API_KEY env var, `pip install anthropic`.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# CSV `messages` cells can be huge — same as the producer side.
csv.field_size_limit(min(2**31 - 1, sys.maxsize))

log = logging.getLogger("tag_sessions")

HERE = Path(__file__).resolve().parent
DEFAULT_TAXONOMY = HERE / "taxonomy.json"
DEFAULT_MODEL = "claude-opus-4-7"  # Must match the model used to tag the corpus baseline.

# Per-million-token list prices (USD) for the supported models. Update when
# pricing changes. Cache-read is ~10× cheaper than full input on Anthropic.
PRICING = {
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0, "cache_read": 0.30},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30},
    "claude-opus-4-7":   {"input": 15.0, "output": 75.0, "cache_read": 1.50},
    "claude-haiku-4-5":  {"input": 1.0, "output": 5.0, "cache_read": 0.10},
}

# Approx chars per token for Claude. Conservative.
CHARS_PER_TOKEN = 3.5

# Typical output size per annotation. The schema is small; most annotations
# come in well under this even with 10+ signals fired.
TYPICAL_OUTPUT_TOKENS = 1500


# =====================================================================
# Prompt construction
# =====================================================================
#
# We render only the **interpretive** subset of the schema into the LLM
# prompt. Deterministic signals (iteration_count, tool_diversity,
# tests_attempted, etc.) are computed by `preprocessing/enrich.py` from
# message structure — the LLM is never asked to count them.
#
# Within the interpretive signals, we group by *role in the session arc*
# rather than by schema category, because the prompt is easier for the
# annotator to follow that way.

PROMPT_GROUPS = (
    ("setup",            "## Setup & framing — quote-anchored moments where the user provides intent or context up front."),
    ("structuring",      "## Task structuring — decomposition + plan-mode interactions."),
    ("verification",     "## Verification & engagement — questioning, probing, reviewing the agent's output."),
    ("steering",         "## Steering — corrections and constraints applied as the work proceeds."),
    ("anti_pattern",     "## Anti-patterns — presence-only, no strength field."),
    ("reality_contact",  "## Reality-contact moments — capture `trigger` and `surface_type` (no strength)."),
)

# Hand-mapped signal → group. New interpretive signals not mapped here
# fall into a generic "other" section at the end of the prompt.
SIGNAL_GROUPS: dict[str, str] = {
    "problem_statement_explicit": "setup",
    "cited_team_convention":      "setup",
    "context_loading_directive":  "setup",
    "existing_code_shared":       "setup",
    "test_or_spec_provided":      "setup",

    "decomposition":              "structuring",
    "requested_plan_mode":        "structuring",
    "plan_approved":              "structuring",
    "plan_rejected":              "structuring",
    "plan_edited":                "structuring",

    "pointed_to_specific_issue":  "verification",
    "ran_and_reported":           "verification",
    "asked_why_choice":           "verification",
    "asked_for_alternative":      "verification",
    "asked_about_tradeoffs":      "verification",
    "requested_diff_review":      "verification",

    "rejected_approach":              "steering",
    "constraint_added_later":         "steering",
    "change_request_specific":        "steering",
    "change_request_vague":           "steering",
    "tool_use_steering":              "steering",
    "subagent_explicitly_delegated":  "steering",
    "workflow_step_delegation":       "steering",
    "safety_constraint_set":          "steering",
    "meta_behavioral_steering":       "steering",

    "accept_verbatim_no_question":    "anti_pattern",
    "fix_request_without_specifics":  "anti_pattern",
    "repeated_same_prompt":           "anti_pattern",
    "error_repaste":                  "anti_pattern",

    "post_implementation_correction":  "reality_contact",
    "in_review_edge_case_surface":     "reality_contact",
    "external_reality_disclosure":     "reality_contact",
    "intent_reframe":                  "reality_contact",
    "edge_case_failure_observed":      "reality_contact",
    "taste_override":                  "reality_contact",
}


def _format_signal(name: str, sig: dict) -> str:
    """One signal block in the system prompt."""
    lines = [f"### `{name}`"]
    if sig.get("description"):
        lines.append(sig["description"])
    if sig.get("look_for"):
        lines.append(f"**Look for:** {sig['look_for']}")
    if sig.get("does_not_count"):
        lines.append(f"**Does not count:** {sig['does_not_count']}")
    anchors = sig.get("strength_anchors")
    if anchors:
        anchor_lines = [f"  - **{k}**: {v}" for k, v in sorted(anchors.items())]
        lines.append("**Strength anchors:**\n" + "\n".join(anchor_lines))
    return "\n".join(lines)


def _format_categorical_field(name: str, field: dict) -> str:
    parts = [f"## `{name}`", field.get("description", "")]
    rubrics = field.get("option_rubrics") or {}
    for opt, rubric in rubrics.items():
        parts.append(f"- **{opt}**: {rubric}")
    if field.get("calibration_note"):
        parts.append(f"_{field['calibration_note']}_")
    return "\n".join(p for p in parts if p)


def _interpretive_signals(taxonomy: dict) -> dict[str, dict]:
    return {
        name: meta for name, meta in taxonomy.get("signals", {}).items()
        if isinstance(meta, dict) and meta.get("computation") == "interpretive"
    }


def _interpretive_categorical_fields(taxonomy: dict) -> dict[str, dict]:
    return {
        name: meta for name, meta in taxonomy.get("categorical_fields", {}).items()
        if isinstance(meta, dict) and meta.get("computation") == "interpretive"
    }


def build_system_prompt(taxonomy: dict) -> str:
    from system_prompt_template import SYSTEM_PROMPT_TEMPLATE

    rubrics = "\n\n".join(
        _format_categorical_field(name, f)
        for name, f in _interpretive_categorical_fields(taxonomy).items()
    )

    interpretive = _interpretive_signals(taxonomy)
    grouped: dict[str, list[str]] = {g: [] for g, _ in PROMPT_GROUPS}
    grouped["other"] = []
    for name, meta in interpretive.items():
        bucket = SIGNAL_GROUPS.get(name, "other")
        grouped.setdefault(bucket, []).append(_format_signal(name, meta))

    parts = []
    for group_key, heading in PROMPT_GROUPS:
        items = grouped.get(group_key, [])
        if not items:
            continue
        parts.append(heading + "\n\n" + "\n\n".join(items))
    if grouped.get("other"):
        parts.append("## Other interpretive signals\n\n" + "\n\n".join(grouped["other"]))
    sig_blocks = "\n\n".join(parts)

    return SYSTEM_PROMPT_TEMPLATE.format(
        categorical_field_rubrics=rubrics,
        signal_definitions=sig_blocks,
    )


def build_output_schema() -> dict:
    """Bring in the schema from the prompt template."""
    from system_prompt_template import ANNOTATION_OUTPUT_SCHEMA
    return ANNOTATION_OUTPUT_SCHEMA


# =====================================================================
# Transcript formatting
# =====================================================================

def messages_to_transcript(messages: list[dict]) -> str:
    """Render the messages JSON list as a numbered, readable transcript."""
    lines: list[str] = []
    user_turn = 0
    for m in messages:
        role = m.get("role")
        ts = m.get("ts") or ""
        content = m.get("content") or ""
        if role == "user":
            user_turn += 1
            src = f" source={m.get('source')}" if m.get("source") == "queue" else ""
            lines.append(f"\n[Turn {user_turn} user @ {ts}{src}]")
            lines.append(content)
        elif role == "ai":
            think = m.get("thinking_chars")
            think_marker = f" thinking={think}c" if think else ""
            lines.append(f"\n[ai @ {ts}{think_marker}]")
            lines.append(content)
        elif role == "tool":
            lines.append(f"\n[tool @ {ts}] {content}")
    return "\n".join(lines).strip()


# =====================================================================
# Cost estimation
# =====================================================================

def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def estimate_cost(
    system_prompt: str, transcripts: list[str], model: str
) -> dict:
    """Return a dict of {n_sessions, est_input_tokens, est_output_tokens, est_cost_usd}.

    Assumes prompt caching: the system prompt is paid full price once, then
    cache_read on subsequent calls.
    """
    pricing = PRICING.get(model, PRICING[DEFAULT_MODEL])
    sys_tokens = estimate_tokens(system_prompt)
    transcript_tokens = [estimate_tokens(t) for t in transcripts]

    n = len(transcripts)
    if n == 0:
        return {"n_sessions": 0, "est_input_tokens": 0,
                "est_output_tokens": 0, "est_cost_usd": 0.0}

    # First call: full system + first transcript, full price.
    full_input_tokens = sys_tokens + transcript_tokens[0]
    # Subsequent calls: cache_read on system + full price on transcript.
    cached_sys_tokens = sys_tokens * (n - 1)
    other_transcript_tokens = sum(transcript_tokens[1:])

    output_tokens = TYPICAL_OUTPUT_TOKENS * n

    cost = (
        (full_input_tokens / 1_000_000) * pricing["input"]
        + (cached_sys_tokens / 1_000_000) * pricing["cache_read"]
        + (other_transcript_tokens / 1_000_000) * pricing["input"]
        + (output_tokens / 1_000_000) * pricing["output"]
    )

    total_input = full_input_tokens + cached_sys_tokens + other_transcript_tokens
    return {
        "n_sessions": n,
        "est_input_tokens": total_input,
        "est_output_tokens": output_tokens,
        "est_cost_usd": cost,
        "model": model,
    }


def format_cost_quote(est: dict) -> str:
    return (
        f"  Sessions to tag       : {est['n_sessions']}\n"
        f"  Model                 : {est.get('model', '?')}\n"
        f"  Est. input tokens     : {est['est_input_tokens']:,}\n"
        f"  Est. output tokens    : {est['est_output_tokens']:,}\n"
        f"  Est. cost (USD)       : ${est['est_cost_usd']:.2f}\n"
        f"  (estimate; actual usage will vary, prompt caching applied)"
    )


# =====================================================================
# Tagging
# =====================================================================

def _select_sessions(rows: list[dict], limit: int,
                     include_subagents: bool) -> list[dict]:
    """Pick the most recent N sessions to tag, parents-only by default."""
    selected = [r for r in rows
                if include_subagents or r.get("is_subagent") != "true"]
    # Most recent first.
    selected.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return selected[:limit]


async def _tag_one(client, semaphore, system_prompt: str, schema: dict,
                   model: str, row: dict) -> dict:
    """Tag one session row. Returns the row with `annotation` filled."""
    try:
        messages = json.loads(row.get("messages", "[]") or "[]")
    except json.JSONDecodeError:
        messages = []
    transcript = messages_to_transcript(messages)

    user_msg = (
        "Annotate the following Claude Code session transcript according to "
        "the taxonomy. Submit your annotation via the `submit_annotation` tool.\n\n"
        f"--- TRANSCRIPT (session_id={row.get('session_id')}) ---\n"
        f"{transcript}"
    )

    tools = [{
        "name": "submit_annotation",
        "description": "Submit the structured taxonomy annotation for this transcript.",
        "input_schema": schema,
    }]

    async with semaphore:
        try:
            resp = await client.messages.create(
                model=model,
                max_tokens=4096,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_msg}],
                tools=tools,
                tool_choice={"type": "tool", "name": "submit_annotation"},
            )
        except Exception as exc:
            log.warning("session %s: API call failed: %s",
                        row.get("session_id"), exc)
            row = dict(row)
            row["annotation"] = json.dumps({"error": str(exc)})
            return row

    annotation: dict[str, Any] = {}
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use":
            annotation = block.input or {}
            break

    if not annotation:
        log.warning("session %s: no tool_use block in response",
                    row.get("session_id"))
        annotation = {"error": "no tool_use block"}

    out = dict(row)
    out["annotation"] = json.dumps(annotation, ensure_ascii=False)
    return out


async def tag_sessions(rows: list[dict], system_prompt: str, schema: dict,
                       model: str, concurrency: int) -> list[dict]:
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:
        raise SystemExit(
            "The `anthropic` package is required. Install with: pip install anthropic"
        ) from exc

    client = AsyncAnthropic()
    sem = asyncio.Semaphore(concurrency)

    tasks = [
        _tag_one(client, sem, system_prompt, schema, model, row)
        for row in rows
    ]

    results: list[dict] = []
    done = 0
    for coro in asyncio.as_completed(tasks):
        out = await coro
        done += 1
        sid = out.get("session_id", "?")
        log.info("tagged %d/%d  %s", done, len(tasks), sid)
        results.append(out)

    # Restore input order.
    order = {r["session_id"]: i for i, r in enumerate(rows)}
    results.sort(key=lambda r: order.get(r.get("session_id"), 0))
    return results


# =====================================================================
# IO
# =====================================================================

def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def write_tagged_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    if "annotation" not in fieldnames:
        fieldnames = list(fieldnames) + ["annotation"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


# =====================================================================
# Entry point
# =====================================================================

def _export_prompt(args) -> int:
    sys.path.insert(0, str(HERE))
    if not args.taxonomy.exists():
        log.error("taxonomy file does not exist: %s", args.taxonomy)
        return 2
    taxonomy = json.loads(args.taxonomy.read_text(encoding="utf-8"))
    prompt = build_system_prompt(taxonomy)
    args.export_prompt.parent.mkdir(parents=True, exist_ok=True)
    args.export_prompt.write_text(prompt, encoding="utf-8")
    print(f"Wrote prompt ({len(prompt):,} chars) to {args.export_prompt}")
    return 0


def _export_transcripts(args) -> int:
    if not args.input_csv:
        log.error("--export-transcripts requires the positional input_csv argument")
        return 2
    if not args.input_csv.exists():
        log.error("input CSV does not exist: %s", args.input_csv)
        return 2

    fieldnames, all_rows = read_csv(args.input_csv)
    if not all_rows:
        log.error("input CSV is empty: %s", args.input_csv)
        return 1

    selected = _select_sessions(all_rows, args.limit, args.include_subagents)
    if not selected:
        log.error("no sessions matched the filter")
        return 1

    out_dir: Path = args.export_transcripts
    out_dir.mkdir(parents=True, exist_ok=True)
    # Clear stale per-session annotations from a prior run — pairing
    # transcripts and annotations by session_id only works if the two
    # match. Annotations from a different sample (or different tagger
    # model) would silently pollute the assembled CSV.
    annotations_dir = out_dir.parent / "annotations"
    if annotations_dir.exists():
        n_cleared = 0
        for f in annotations_dir.glob("*.json"):
            f.unlink()
            n_cleared += 1
        if n_cleared:
            log.info("cleared %d stale annotation files from %s",
                     n_cleared, annotations_dir)
    # Clear stale transcript files too — otherwise a smaller --limit run
    # leaves orphan transcripts for the prior run's session_ids.
    n_cleared = 0
    for f in out_dir.glob("*.txt"):
        f.unlink()
        n_cleared += 1
    if n_cleared:
        log.info("cleared %d stale transcript files from %s",
                 n_cleared, out_dir)

    manifest = []
    for r in selected:
        sid = r.get("session_id") or ""
        if not sid:
            continue
        try:
            msgs = json.loads(r.get("messages", "[]") or "[]")
        except json.JSONDecodeError:
            msgs = []
        transcript = messages_to_transcript(msgs)
        (out_dir / f"{sid}.txt").write_text(transcript, encoding="utf-8")
        # Strip the heavy `messages` column from the manifest — the transcript
        # file holds the readable form; the assembler doesn't need both.
        meta = {k: v for k, v in r.items() if k != "messages"}
        manifest.append(meta)

    manifest_path = out_dir / "_manifest.json"
    manifest_path.write_text(
        json.dumps({"fieldnames": fieldnames, "rows": manifest}, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {len(manifest)} transcripts + manifest to {out_dir}/")
    return 0


def _assemble(args) -> int:
    annotations_dir: Path = args.assemble
    manifest_path: Path = args.manifest
    out_path: Path = args.out

    if not annotations_dir.is_dir():
        log.error("annotations dir does not exist: %s", annotations_dir)
        return 2
    if not manifest_path.exists():
        log.error("manifest does not exist: %s", manifest_path)
        return 2

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fieldnames: list[str] = manifest["fieldnames"]
    rows: list[dict] = manifest["rows"]
    # The manifest already stripped `messages` from per-row data; drop the
    # column header too so the assembled CSV stays lean. Downstream
    # compute_metrics doesn't read `messages` from tagged_sessions.csv —
    # it reads structural stats from `--raw sessions.csv`.
    fieldnames = [f for f in fieldnames if f != "messages"]

    n_ok = 0
    n_missing = 0
    n_invalid = 0
    assembled: list[dict] = []
    for r in rows:
        sid = r.get("session_id") or ""
        ann_path = annotations_dir / f"{sid}.json"
        out = dict(r)
        if not ann_path.exists():
            out["annotation"] = json.dumps({"error": "annotation file missing"})
            n_missing += 1
        else:
            try:
                ann = json.loads(ann_path.read_text(encoding="utf-8"))
                out["annotation"] = json.dumps(ann, ensure_ascii=False)
                n_ok += 1
            except json.JSONDecodeError as exc:
                out["annotation"] = json.dumps({"error": f"invalid JSON: {exc}"})
                n_invalid += 1
        assembled.append(out)

    write_tagged_csv(out_path, fieldnames, assembled)
    print()
    print("=" * 64)
    print(f"Assembled {len(assembled)} rows  →  {out_path}")
    print(f"  ok: {n_ok}  missing: {n_missing}  invalid: {n_invalid}")
    print("=" * 64)
    return 0 if n_missing == 0 and n_invalid == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tag Claude Code sessions with the AI Coding Practice Taxonomy."
    )
    parser.add_argument("input_csv", type=Path, nargs="?",
                        help="Path to sessions CSV from preprocessing/sessions_to_csv.py "
                             "(required for API tagging and --export-transcripts)")
    parser.add_argument("--out", type=Path, default=Path("tagged_sessions.csv"),
                        help="Output CSV path (default: ./tagged_sessions.csv)")
    parser.add_argument("--limit", type=int, default=50,
                        help="Number of most recent sessions to tag (default: 50)")
    parser.add_argument("--include-subagents", action="store_true",
                        help="Include subagent sessions (default: parent sessions only)")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        choices=list(PRICING.keys()),
                        help=f"Anthropic model (default: {DEFAULT_MODEL})")
    parser.add_argument("--concurrency", type=int, default=5,
                        help="Parallel API calls (default: 5)")
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY,
                        help="Path to taxonomy.json (default: ./taxonomy.json)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip the cost-confirmation prompt.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the cost estimate and exit. No API calls.")
    parser.add_argument("--verbose", "-v", action="store_true")
    # Subagent-orchestration helpers (no API call):
    parser.add_argument("--export-prompt", type=Path, default=None,
                        metavar="PATH",
                        help="Write the system prompt to PATH and exit. "
                             "For subagent-based tagging.")
    parser.add_argument("--export-transcripts", type=Path, default=None,
                        metavar="DIR",
                        help="Write per-session transcripts and _manifest.json "
                             "to DIR and exit. For subagent-based tagging.")
    parser.add_argument("--assemble", type=Path, default=None,
                        metavar="ANNOTATIONS_DIR",
                        help="Assemble per-session JSON annotations from "
                             "ANNOTATIONS_DIR into a tagged_sessions.csv. "
                             "Requires --manifest.")
    parser.add_argument("--manifest", type=Path, default=None,
                        help="Path to the _manifest.json produced by "
                             "--export-transcripts. Used by --assemble.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Make `system_prompt_template.py` importable by sibling.
    sys.path.insert(0, str(HERE))

    # Helper modes — no API call, no key required.
    if args.export_prompt:
        return _export_prompt(args)
    if args.export_transcripts:
        return _export_transcripts(args)
    if args.assemble:
        if not args.manifest:
            log.error("--assemble requires --manifest")
            return 2
        return _assemble(args)

    if not args.input_csv:
        log.error("an input CSV is required for API tagging "
                  "(or use --export-prompt / --export-transcripts / --assemble)")
        return 2

    if not args.input_csv.exists():
        log.error("input CSV does not exist: %s", args.input_csv)
        return 2

    if not args.taxonomy.exists():
        log.error("taxonomy file does not exist: %s", args.taxonomy)
        return 2

    if not os.environ.get("ANTHROPIC_API_KEY") and not args.dry_run:
        log.error("ANTHROPIC_API_KEY environment variable is not set.")
        log.error("Get a key at https://console.anthropic.com/ and:")
        log.error("    export ANTHROPIC_API_KEY=sk-ant-...")
        return 3

    taxonomy = json.loads(args.taxonomy.read_text(encoding="utf-8"))
    system_prompt = build_system_prompt(taxonomy)
    schema = build_output_schema()

    fieldnames, all_rows = read_csv(args.input_csv)
    if not all_rows:
        log.error("input CSV is empty: %s", args.input_csv)
        return 1

    selected = _select_sessions(all_rows, args.limit, args.include_subagents)
    if not selected:
        log.error("no sessions matched the filter (limit=%d, include_subagents=%s)",
                  args.limit, args.include_subagents)
        return 1

    transcripts = []
    for r in selected:
        try:
            msgs = json.loads(r.get("messages", "[]") or "[]")
        except json.JSONDecodeError:
            msgs = []
        transcripts.append(messages_to_transcript(msgs))

    est = estimate_cost(system_prompt, transcripts, args.model)
    print()
    print("=" * 64)
    print("Cost estimate")
    print("=" * 64)
    print(format_cost_quote(est))
    print()

    if args.dry_run:
        print("(--dry-run set; exiting without tagging.)")
        return 0

    if not args.yes:
        try:
            ans = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("Aborted.")
            return 0

    log.info("tagging %d sessions with %s (concurrency=%d)…",
             len(selected), args.model, args.concurrency)

    tagged = asyncio.run(tag_sessions(
        selected, system_prompt, schema, args.model, args.concurrency,
    ))

    write_tagged_csv(args.out, fieldnames, tagged)

    n_err = sum(1 for r in tagged
                if "error" in (r.get("annotation") or "")
                and json.loads(r["annotation"]).get("error"))
    print()
    print("=" * 64)
    print(f"Wrote {len(tagged)} tagged sessions to {args.out}")
    if n_err:
        print(f"  {n_err} sessions had errors — check the `annotation` column.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
