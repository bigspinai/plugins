#!/usr/bin/env python3
"""User-archetype fingerprints — v1, derived from the SWE-chat 120-user
multi-session subset.

Operational classifier: given a user's per-signal fire rates and
deterministic-signal medians, return the archetype that best describes
their practice. Companion baselines / profiles live in
``baselines/archetype_baselines.csv`` and
``baselines/archetype_profiles.json``. The "why" behind the vocabulary
lives in the SWE-chat research repo (``USER_ARCHETYPES.md``); this file
is the production layer the practice mirror reads.

Methodology
-----------
1. Bottom-up k-means clusters at k = 6 on per-user signal-rate vectors.
   Six natural groupings emerged.
2. Each cluster's signal centroids and deterministic medians were
   inspected; an archetype name was authored to match.
3. For each archetype, an empirical fingerprint was authored (canonical
   signal-rate thresholds + exclusions + optional metadata predicate).
4. The classifier was validated against the bottom-up clusters
   (≥57% per-cluster recovery; 74% of users decisive) and via 100×
   bootstrap resampling at 80% (Manager / Spec-First / Reality Tester
   ≥75% mean stability).

The fingerprints were tuned on users with ≥3 sessions; running the mirror
on a single user with very few sessions can produce noisy assignments.
Above ~10 sessions, scores stabilize.

Six archetypes + Generalist fallback. Each is meant to be recognizable
to a user reading their own report — a horoscope-quality description
that the data backs up.

API
---
    assign_archetype(signal_rates, det_medians)
        → (primary_name, primary_score, secondary_name, secondary_score,
           all_scores)

    ARCHETYPES → list[Archetype]
        The ordered vocabulary. Rule priority breaks ties.

    UNASSIGNED → str
        Sentinel for users who don't fit any fingerprint at score > 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


UNASSIGNED = "The Generalist"

# Shadow archetypes (the "opposite" each archetype contrasts against on
# its most defining axis), signature moves, reflection prompts, and
# borrow-from recommendations all live in
# ``baselines/archetype_profiles.json`` — that file is the canonical
# research artifact and the operational source of truth for everything
# the report writer reads beyond the classifier itself.


# =====================================================================
# Fingerprint DSL
# =====================================================================

@dataclass(frozen=True)
class Archetype:
    """A user-archetype fingerprint over per-user signal rates +
    deterministic-signal medians.

    Scoring:
      - +1 per canonical signal whose user-rate clears its min_rate.
      - +0.5 per bonus signal that clears.
      - 0 (rejected) if any excluded signal exceeds its max_rate.
      - 0 (rejected) if metadata_predicate returns False.
      - 0 (rejected) if canonical hits < min_canonical.

    Tie-break: higher ``priority`` wins.
    """
    name: str
    tagline: str
    canonical: tuple[tuple[str, float], ...]
    min_canonical: int = 2
    excluded: tuple[tuple[str, float], ...] = ()
    bonus: tuple[tuple[str, float], ...] = ()
    metadata_predicate: Optional[Callable[[dict], bool]] = None
    priority: int = 0

    def score(self, signal_rates: dict[str, float],
              det_medians: dict[str, float]) -> float:
        for sig, max_rate in self.excluded:
            if signal_rates.get(sig, 0.0) > max_rate:
                return 0.0
        if self.metadata_predicate is not None:
            if not self.metadata_predicate(det_medians):
                return 0.0
        canonical_hits = sum(
            1 for sig, min_rate in self.canonical
            if signal_rates.get(sig, 0.0) >= min_rate
        )
        if canonical_hits < self.min_canonical:
            return 0.0
        bonus_hits = sum(
            1 for sig, min_rate in self.bonus
            if signal_rates.get(sig, 0.0) >= min_rate
        )
        return float(canonical_hits) + 0.5 * float(bonus_hits)


# =====================================================================
# v1 vocabulary — locked after bottom-up validation
# =====================================================================
#
# Order matters only for tie-breaks; priority does the actual work.
# Higher priority = matched first when two archetypes score the same.
# Pair Programmer / Patcher are highest priority because they're the
# strongest behavioral signals (extreme iteration, anti-pattern
# signature) — we want to surface these before falling back to the
# more "default" archetypes (Manager, Streamliner, etc.).

ARCHETYPES: list[Archetype] = [
    Archetype(
        name="The Pair Programmer",
        tagline="Long, deep sessions. Steers reasoning, reframes goals, verifies.",
        canonical=(
            ("meta_behavioral_steering", 0.25),
            ("intent_reframe", 0.30),
            ("constraint_added_later", 0.50),
            ("in_review_edge_case_surface", 0.35),
            ("safety_constraint_set", 0.30),
        ),
        min_canonical=2,
        bonus=(
            ("post_implementation_correction", 0.50),
            ("pointed_to_specific_issue", 0.50),
            ("external_reality_disclosure", 0.50),
        ),
        # Long iteration profile — Pair Programmer sessions are not short.
        metadata_predicate=lambda m: (
            m.get("iteration_count", 0) >= 12
            or m.get("edit_iteration_count", 0) >= 4
        ),
        priority=10,
    ),

    Archetype(
        name="The Patcher",
        tagline="Spotting bugs and patching via re-asking. Friction signal visible.",
        canonical=(
            ("fix_request_without_specifics", 0.12),
            ("error_repaste", 0.02),
            ("repeated_same_prompt", 0.08),
        ),
        min_canonical=1,
        bonus=(
            ("change_request_vague", 0.05),
            ("plan_rejected", 0.02),
        ),
        excluded=(
            # If they're pointing at issues, they're a Reality Tester.
            ("pointed_to_specific_issue", 0.45),
            # If they're orchestrating subagents, they're a Manager.
            ("subagent_explicitly_delegated", 0.20),
        ),
        priority=8,
    ),

    Archetype(
        name="The Manager",
        tagline="Sets up the work, cites conventions, delegates carefully, stays engaged.",
        canonical=(
            ("decomposition", 0.50),
            ("context_loading_directive", 0.25),
            ("subagent_explicitly_delegated", 0.18),
            ("safety_constraint_set", 0.18),
        ),
        # ≥2 of 4 — in practice this means decomposition + (one of the
        # ceremonial signals). Decomposition alone isn't enough; many
        # users decompose without the Manager's full setup posture.
        min_canonical=2,
        bonus=(
            ("cited_team_convention", 0.10),
            ("plan_approved", 0.15),
        ),
        excluded=(
            ("error_repaste", 0.04),
        ),
        priority=4,
    ),

    Archetype(
        name="The Reality Tester",
        tagline="Reads the draft, points at issues, runs the code, surfaces failures.",
        canonical=(
            ("pointed_to_specific_issue", 0.32),
            ("post_implementation_correction", 0.38),
            ("in_review_edge_case_surface", 0.18),
            ("ran_and_reported", 0.10),
            ("edge_case_failure_observed", 0.07),
            ("change_request_specific", 0.20),
        ),
        # ≥3 of 6 — a true Reality Tester combines pointing-at-issues
        # with running and surfacing edge cases, not just one of those.
        min_canonical=3,
        bonus=(
            ("asked_why_choice", 0.10),
            ("external_reality_disclosure", 0.30),
        ),
        excluded=(
            ("subagent_explicitly_delegated", 0.22),
            ("context_loading_directive", 0.32),
            ("error_repaste", 0.04),
        ),
        priority=3,
    ),

    Archetype(
        name="The Spec-First Architect",
        tagline="Front-loads decomposition + spec/tests at intake, then accepts.",
        canonical=(
            ("decomposition", 0.45),
            ("test_or_spec_provided", 0.08),
            ("existing_code_shared", 0.03),
        ),
        min_canonical=1,
        bonus=(
            ("problem_statement_explicit", 0.75),
            ("accept_verbatim_no_question", 0.35),
        ),
        excluded=(
            ("pointed_to_specific_issue", 0.30),
            ("constraint_added_later", 0.30),
            ("post_implementation_correction", 0.40),
            ("subagent_explicitly_delegated", 0.18),
            ("context_loading_directive", 0.30),
        ),
        # Spec-First sessions are short: write the brief, accept the
        # answer. Longer sessions are Manager territory.
        metadata_predicate=lambda m: m.get("iteration_count", 0) <= 5,
        priority=2,
    ),

    Archetype(
        name="The Streamliner",
        tagline="Clean ask, accept, move on. Workflow delegation without structure.",
        canonical=(
            ("workflow_step_delegation", 0.55),
            ("accept_verbatim_no_question", 0.35),
        ),
        min_canonical=1,
        excluded=(
            ("decomposition", 0.45),
            ("subagent_explicitly_delegated", 0.18),
            ("context_loading_directive", 0.30),
            ("safety_constraint_set", 0.20),
            ("ran_and_reported", 0.18),
            ("in_review_edge_case_surface", 0.28),
            ("edge_case_failure_observed", 0.12),
            ("error_repaste", 0.04),
        ),
        priority=2,
    ),
]


# =====================================================================
# Single-user API
# =====================================================================

def assign_archetype(signal_rates: dict[str, float],
                     det_medians: dict[str, float]
                     ) -> tuple[str, float, str, float, dict[str, float]]:
    """Classify a user's archetype.

    Returns ``(primary, primary_score, secondary, secondary_score, all_scores)``.

    - ``signal_rates`` — the user's fire rate per interpretive signal,
      i.e. (sessions in which signal X fired) / (total sessions).
    - ``det_medians`` — the user's median value for each numeric
      deterministic signal (iteration_count, edit_iteration_count, ...).

    If no archetype scores > 0, primary = ``UNASSIGNED`` ("The Generalist").
    Secondary is the next-highest scoring archetype, or "" if only one
    archetype matched.
    """
    scores: dict[str, float] = {}
    for arch in ARCHETYPES:
        scores[arch.name] = arch.score(signal_rates, det_medians)

    # Sort by (score, priority) — higher first.
    arch_by_name = {a.name: a for a in ARCHETYPES}
    ordered = sorted(
        scores.items(),
        key=lambda kv: (-kv[1], -arch_by_name[kv[0]].priority),
    )

    primary_name = ordered[0][0] if ordered[0][1] > 0 else UNASSIGNED
    primary_score = ordered[0][1]
    secondary_name = ""
    secondary_score = 0.0
    for name, score in ordered[1:]:
        if score > 0:
            secondary_name = name
            secondary_score = score
            break

    return primary_name, primary_score, secondary_name, secondary_score, scores
