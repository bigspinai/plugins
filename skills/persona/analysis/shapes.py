#!/usr/bin/env python3
"""Compose the deterministic + interpretive signal stream into one named
**shape** label per session.

This is the v2 framing replacement for the v1 personas. A shape label is
a deterministic composition of signals via authored rules — it answers
"what kind of session was this?" without an LLM call (the per-signal
work was done by the enrichment + tagging steps).

Vocabulary
----------
The 11 named shapes live in ``SHAPES`` below and are imported from the
SWE-chat research project verbatim, with two adjustments:

  - References to signals dropped in the v2.1 schema have been removed
    (they would have been silent no-ops, but they made the rules harder
    to read).
  - The SWE-chat pipeline-only fields (sessions_full.csv writer, batch
    CLI) have been replaced with a single-session API the practice
    mirror's ``compute_metrics.py`` can call inline.

Where to edit
-------------
The vocabulary is the contract. Edit ``SHAPES``, re-run the report on a
sample, and the practice-mirror's interpret.md will pick up the new
labels automatically.

Public API
----------
    assign_shape(row, *, max_turn=None) → (shape_name, score, all_scores)
        Single-session classifier. Used by ``compute_metrics.py``.

    SHAPES → list[Shape]
        The vocabulary, in priority/iteration order.

    UNASSIGNED → str
        Sentinel for sessions where no shape rule fires.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Optional

UNASSIGNED = "Unassigned"


# =====================================================================
# Shape DSL
# =====================================================================

@dataclass(frozen=True)
class Shape:
    """An authored rule for a named interaction shape.

    Scoring:
      - +1 per canonical signal that fired
      - +0.3 per bonus signal that fired
      - 0 (rejected) if any excluded signal fired
      - 0 (rejected) if engagement_gate is set and engagement_depth not in it
      - 0 (rejected) if total canonical hits < min_canonical
      - 0 (rejected) if metadata_predicate returns False

    Some shapes (Marathon, One-shot) are defined primarily by deterministic
    metadata — set ``metadata_only=True`` so they score on the predicate
    alone (no signals required) with a low constant so any signal-driven
    shape with ≥1 canonical hit beats them.

    Tie-break by ``priority`` (higher first).
    """
    name: str
    visual: str
    rationale: str
    canonical: tuple[str, ...]
    min_canonical: int = 2
    excluded: tuple[str, ...] = ()
    bonus: tuple[str, ...] = ()
    engagement_gate: tuple[str, ...] = ()
    priority: int = 0
    metadata_predicate: Optional[Callable[[dict], bool]] = None
    metadata_only: bool = False

    def score(self, fired: set[str], engagement: str,
              row: Optional[dict] = None) -> float:
        if self.metadata_predicate is not None:
            if row is None or not self.metadata_predicate(row):
                return 0.0
        if self.engagement_gate and engagement not in self.engagement_gate:
            return 0.0
        if any(e in fired for e in self.excluded):
            return 0.0
        if self.metadata_only:
            return 0.5
        canonical_hits = sum(1 for s in self.canonical if s in fired)
        if canonical_hits < self.min_canonical:
            return 0.0
        bonus_hits = sum(1 for s in self.bonus if s in fired)
        return float(canonical_hits) + 0.3 * float(bonus_hits)


# =====================================================================
# Helpers used by shape predicates
# =====================================================================

def _det_float(row: dict, key: str) -> float:
    """Read a deterministic column, returning 0.0 when missing or non-numeric."""
    raw = row.get(key)
    if raw is None or raw == "":
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


# =====================================================================
# The vocabulary
# =====================================================================

SHAPES: list[Shape] = [
    Shape(
        name="Spiral",
        visual="Pasting the same error a second time after the agent's "
               "first fix didn't take.",
        rationale="The death-spiral signature. Strongest single negative "
                  "success delta in the SWE-chat corpus.",
        canonical=("error_repaste",),
        min_canonical=1,
        bonus=("fix_request_without_specifics",),
        priority=10,
    ),

    # ---------- Workshop family --------------------------------------
    # All three subtypes share the editorial-pushback canonical pattern.
    # They differ on the deterministic *shape* of the engagement.

    Shape(
        name="Tight Workshop",
        visual="Rapid edit-redirect cycles. Short course-corrections — "
               "\"no, the other one,\" \"undo that part,\" \"closer "
               "but…\" — in tight succession. Compressed and intense.",
        rationale="Workshop subtype keyed on deterministic: ≥3 short "
                  "post-edit course-corrections in ≤12 iterations.",
        canonical=("in_review_edge_case_surface", "pointed_to_specific_issue",
                   "post_implementation_correction", "constraint_added_later",
                   "meta_behavioral_steering", "rejected_approach"),
        min_canonical=2,
        bonus=("change_request_specific", "external_reality_disclosure",
               "asked_why_choice"),
        metadata_predicate=(
            lambda row: (
                _det_float(row, "course_correction_count") >= 3.0
                and 0.0 < _det_float(row, "iteration_count") <= 12.0
            )
        ),
        priority=8,
    ),

    Shape(
        name="Marathon Workshop",
        visual="Sustained editorial work over many turns — 20+ user "
               "prompts, lots of edits, real elapsed time, often "
               "punctuated by long pauses where the user reads carefully "
               "before the next push.",
        rationale="Workshop subtype keyed on deterministic: ≥20 "
                  "iterations and ≥6 edit-iterations alongside the "
                  "Workshop signal pattern. The highest-shipping "
                  "interpretive-driven shape in research.",
        canonical=("in_review_edge_case_surface", "pointed_to_specific_issue",
                   "post_implementation_correction", "constraint_added_later",
                   "meta_behavioral_steering", "rejected_approach"),
        min_canonical=2,
        bonus=("change_request_specific", "external_reality_disclosure",
               "asked_why_choice"),
        metadata_predicate=(
            lambda row: (
                _det_float(row, "iteration_count") >= 20.0
                and _det_float(row, "edit_iteration_count") >= 6.0
            )
        ),
        priority=8,
    ),

    Shape(
        name="Workshop",
        visual="Reading the agent's draft and marking it up — pointing at "
               "specific lines, surfacing missed edge cases, asking for "
               "a different approach. Editorial work, not adversarial.",
        rationale="The base Workshop pattern; residual when neither the "
                  "Tight nor Marathon predicates fire.",
        canonical=("in_review_edge_case_surface", "pointed_to_specific_issue",
                   "post_implementation_correction", "constraint_added_later",
                   "meta_behavioral_steering", "rejected_approach"),
        min_canonical=2,
        bonus=("change_request_specific", "external_reality_disclosure",
               "asked_why_choice"),
        priority=5,
    ),

    Shape(
        name="Late reveal",
        visual="\"Oh wait, I forgot to mention — \" mid-flight. The user "
               "discloses constraints or context after seeing what the "
               "agent did, things they could have said upfront but "
               "didn't realise mattered.",
        rationale="Reactive specification; the user's knowledge "
                  "surfaces only on contact with the artifact.",
        canonical=("external_reality_disclosure",
                   "post_implementation_correction"),
        min_canonical=2,
        excluded=("in_review_edge_case_surface", "pointed_to_specific_issue"),
        bonus=("constraint_added_later", "intent_reframe"),
        priority=3,
    ),

    Shape(
        name="Blueprint",
        visual="Showing up with the spec already worked out — tests, "
               "decomposition, existing code, framework choices — and "
               "handing it to the agent like a finished RFC. Little "
               "review after.",
        rationale="High LLM-rated success but the lowest PR-yield shape "
                  "in research (0/20 in the SWE-chat corpus). Looks "
                  "rigorous; ships poorly.",
        canonical=("decomposition", "test_or_spec_provided",
                   "existing_code_shared"),
        min_canonical=2,
        excluded=("post_implementation_correction", "rejected_approach",
                  "in_review_edge_case_surface"),
        bonus=("problem_statement_explicit",),
        priority=2,
    ),

    Shape(
        name="Scaffold",
        visual="Setting the table before the work happens — pointing the "
               "agent at the right files, citing project conventions, "
               "scoping safety constraints, delegating subagents.",
        rationale="Concentrates the CC-native scaffolding behaviors. "
                  "Often the opening of Marathon Workshop sessions — "
                  "the Scaffold→Marathon Workshop trajectory has one of "
                  "the highest PR-yields in research.",
        canonical=("context_loading_directive", "cited_team_convention",
                   "safety_constraint_set", "subagent_explicitly_delegated"),
        min_canonical=2,
        bonus=("workflow_step_delegation", "tool_use_steering",
               "requested_plan_mode", "plan_approved"),
        priority=2,
    ),

    Shape(
        name="Clean handoff",
        visual="A clear task, a clean delivery, the user accepts and "
               "moves on. Workflow steps (commit, PR) get delegated. "
               "No friction — and no critical engagement, because none "
               "was needed.",
        rationale="Highest LLM-rated success but ships at population "
                  "rate. Looks great as graded; the lottery-vs-tool "
                  "story lives here.",
        canonical=("accept_verbatim_no_question", "workflow_step_delegation"),
        min_canonical=2,
        excluded=("post_implementation_correction", "in_review_edge_case_surface",
                  "rejected_approach", "pointed_to_specific_issue",
                  "constraint_added_later"),
        bonus=("problem_statement_explicit", "plan_approved"),
        priority=1,
    ),

    Shape(
        name="Drift",
        visual="\"Fix it\" — the agent does something — \"still broken\" — "
               "the agent does something else — fizzle. No specifics, "
               "no engagement with output, no shipping.",
        rationale="The default failure mode in the corpus. Detectable "
                  "from turn 3 with high recall.",
        canonical=("fix_request_without_specifics", "change_request_vague"),
        min_canonical=1,
        excluded=("pointed_to_specific_issue", "in_review_edge_case_surface",
                  "post_implementation_correction", "decomposition",
                  "test_or_spec_provided", "context_loading_directive"),
        engagement_gate=("low", "minimal"),
        bonus=("repeated_same_prompt",),
        priority=0,
    ),

    Shape(
        name="Marathon",
        visual="Long, sustained work — many user prompts, lots of edits, "
               "frequent course-corrections — but no single quotable "
               "moment hits the interpretive precision bar. The agentic-"
               "coding equivalent of long editorial work where the "
               "engagement is real but distributed across turns.",
        rationale="Deterministic-only shape. Closes the taxonomy-coverage "
                  "gap for sessions that are structurally workshop-shaped "
                  "(≥20 iterations + ≥6 edit-iterations + ≥2 course-"
                  "corrections) but no interpretive signal crystallised. "
                  "If interpretive signals also fire, the Workshop family "
                  "wins by score.",
        canonical=(),
        min_canonical=0,
        metadata_only=True,
        metadata_predicate=(
            lambda row: (
                _det_float(row, "iteration_count") >= 20.0
                and _det_float(row, "edit_iteration_count") >= 6.0
                and _det_float(row, "course_correction_count") >= 2.0
            )
        ),
        priority=3,
    ),

    Shape(
        name="One-shot",
        visual="A brief request — \"add this method,\" \"fix the typo,\" "
               "\"explain this function\" — the agent handles it in one "
               "or two exchanges, the user moves on. Transactional, not "
               "lazy.",
        rationale="The genuinely-short tail of the corpus: ≤2 user "
                  "prompts. Default mode of routine agentic work. "
                  "Defined purely by deterministic iteration_count.",
        canonical=(),
        min_canonical=0,
        metadata_only=True,
        metadata_predicate=(
            lambda row: 0 < int(_det_float(row, "iteration_count")) <= 2
        ),
        priority=-1,
    ),
]


# =====================================================================
# Single-session API
# =====================================================================

def _parse_signals_from_annotation(row: dict) -> dict:
    """Extract the signals dict from a tagged row.

    Practice-mirror tagged rows store the LLM annotation as a JSON blob
    in the ``annotation`` column (see ``tagging/tag_sessions.py``).
    SWE-chat-style rows have a separate ``signals_json`` column. We
    accept either, with annotation taking priority — that's the format
    the practice mirror produces.
    """
    raw = row.get("annotation")
    if raw:
        try:
            ann = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            ann = None
        if isinstance(ann, dict):
            sigs = ann.get("signals")
            if isinstance(sigs, dict):
                return sigs

    raw = row.get("signals_json")
    if raw:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    return {}


def _engagement_from_row(row: dict) -> str:
    """The annotation column (practice-mirror) wraps engagement_depth;
    SWE-chat-style rows store it as a top-level column. Try both."""
    raw = row.get("annotation")
    if raw:
        try:
            ann = json.loads(raw)
            if isinstance(ann, dict):
                ed = ann.get("engagement_depth")
                if ed:
                    return str(ed).strip()
        except (json.JSONDecodeError, ValueError):
            pass
    return (row.get("engagement_depth") or "").strip()


def _signals_fired(row: dict, max_turn: Optional[int] = None) -> set[str]:
    """Set of interpretive signal names that fired in this session.

    If ``max_turn`` is set, only signals whose evidence was attached to
    a user turn ≤ ``max_turn`` are kept. Signals without a ``turn``
    field (cross-cutting patterns) are kept regardless.
    """
    signals = _parse_signals_from_annotation(row)
    if max_turn is None:
        return set(signals.keys())
    out: set[str] = set()
    for name, sig in signals.items():
        if not isinstance(sig, dict):
            continue
        turn = sig.get("turn")
        if not isinstance(turn, int):
            out.add(name)   # no turn info → keep
            continue
        if turn <= max_turn:
            out.add(name)
    return out


def assign_shape(row: dict, *,
                 max_turn: Optional[int] = None
                 ) -> tuple[str, float, dict[str, float]]:
    """Return ``(primary_shape, score, all_scores)`` for one session row.

    The row must carry both layers' signals — deterministic signal
    columns from ``preprocessing/enrich.py`` and the interpretive
    annotation from ``tagging/tag_sessions.py``. (compute_metrics
    reads tagged_sessions.csv which has both.)

    When ``max_turn`` is set, interpretive signals are filtered to
    those in turns ≤ max_turn. Deterministic-metric predicates use
    whatever values are in the row — turn-aware deterministic
    recompute is the caller's responsibility.
    """
    fired = _signals_fired(row, max_turn=max_turn)
    engagement = _engagement_from_row(row)

    scores: dict[str, float] = {}
    for shape in SHAPES:
        scores[shape.name] = shape.score(fired, engagement, row=row)

    best_shape: Optional[Shape] = None
    best_score = 0.0
    for shape in SHAPES:
        s = scores[shape.name]
        if s <= 0:
            continue
        if (s > best_score
                or (s == best_score
                    and best_shape is not None
                    and shape.priority > best_shape.priority)):
            best_shape = shape
            best_score = s
    return (
        (best_shape.name if best_shape else UNASSIGNED),
        best_score,
        scores,
    )


# =====================================================================
# Shape metadata for the report writer
# =====================================================================

def shape_visual(name: str) -> str:
    """Return the human-readable visual blurb for a shape — used by
    interpret.md to ground the report's shape description."""
    for s in SHAPES:
        if s.name == name:
            return s.visual
    return ""


def shape_rationale(name: str) -> str:
    for s in SHAPES:
        if s.name == name:
            return s.rationale
    return ""
