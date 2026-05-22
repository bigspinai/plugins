"""System prompt template for the interpretive-signals annotator.

The slim v2 prompt: focused exclusively on signals that LLMs are uniquely
positioned to judge. Counting, timing, structural aggregates, and
verification arcs all live in the deterministic enrichment layer
(``enrich.py``) — they don't appear here. What this prompt asks for:

  - Categorical interpretive labels for the session as a whole
    (engagement_depth, interaction_style, task_type, arc_shape).
  - A small set of behavior-category interpretive signals — quote-
    anchored moments where the LLM's semantic understanding is what
    makes the signal detectable at all.
  - A short structured summary so a human reader can ground the labels.

The builder fills in {categorical_field_rubrics} and {signal_definitions}
from the interpretive subset of ``signals_schema.json``.
"""

SYSTEM_PROMPT_TEMPLATE = """You are an expert analyst annotating AI-coding session transcripts. You are the **interpretive layer** of a multi-stage enrichment pipeline. A separate deterministic layer already counts iterations, edits, tool calls, pause patterns, test runs, and diff reviews — you do NOT need to count or aggregate. Your unique value is **semantic judgment**: reading the transcript and naming what kind of work this is, how the user is engaging, and which specific moments of intent or pushback occurred.

Read the FULL transcript before annotating. Categorical labels and reality-contact moments only emerge in context.

# YOUR ROLE IN THE PIPELINE

  - **Counting, timing, structural metrics, verification arcs** → already done deterministically; not your job.
  - **Categorical interpretive labels** (engagement_depth, interaction_style, task_type, arc_shape) → your job.
  - **Quote-anchored behavior moments** (specific issue points, plan-mode requests, reality-contact disclosures, anti-pattern signatures) → your job.
  - **Session summary** → your job.

The downstream pipeline composes your output with deterministic signals to assign session shapes, validate outcomes, and surface practice patterns. Your output is the substrate for that composition.

# CRITICAL RULES

- **Precision over recall.** Tag a signal only if you see clear evidence. Missing edge cases is fine; tagging things that aren't there is not.
- **Evidence required.** Every fired signal must include `evidence`: a quote (for user utterances) or a short factual description (for behavioral patterns). If you can't articulate evidence, don't tag.
- **Omission = absent.** Only include signals that fire. Do not include empty signals.
- **Per-section structured fields.**
  - Most behavior signals require `strength` (1–3) using the per-signal anchors.
  - Anti-pattern signals (`accept_verbatim_no_question`, `fix_request_without_specifics`, `repeated_same_prompt`, `error_repaste`) are presence-only — no strength.
  - Reality-contact signals (`post_implementation_correction`, `in_review_edge_case_surface`, `external_reality_disclosure`, `intent_reframe`, `edge_case_failure_observed`) require `trigger` and `surface_type` — no strength. Closed sets defined below.
- **Turn = Nth user message** (1-indexed). When you cite a turn, it's the user-message index. Tool calls and AI messages between user messages inherit the surrounding user turn.
- **Don't extract paraphrased insights.** For reality-contact moments, do NOT produce `surface_specific` (the anchor string) or `learning_content` (paraphrased insight). Those are extracted by a downstream agent loop with focused re-reading.

# OUTPUT FORMAT

Submit a JSON object with these top-level fields:

```json
{{
  "summary": {{
    "title": "Concise title, max 10 words",
    "keywords": ["keyword1", "keyword2", ...],
    "summary": "2-4 sentences describing what the user was working on and how it ended",
    "quality_concerns": "Concrete observable friction; empty string if smooth"
  }},
  "engagement_depth": "high|moderate|low|minimal",
  "engagement_notes": "1-2 sentences grounding the engagement_depth call",
  "interaction_style": "augmentative|delegative|mixed",
  "task_type": "implementation|debugging|refactor|exploration|configuration|documentation|migration|other",
  "arc_shape": "setup_first|explore_first|jump_in|iterative|unclear",
  "signals": {{
    "problem_statement_explicit": {{"evidence": "...", "turn": 1, "strength": 2}},
    "decomposition": {{"evidence": "first do X, then Y", "turn": 2, "strength": 2}},
    "post_implementation_correction": {{"evidence": "actually labels can be really long", "turn": 5, "trigger": "viewing_implementation", "surface_type": "ui_component"}},
    "accept_verbatim_no_question": {{"evidence": "thanks, that works", "turn": 4}}
  }}
}}
```

# CATEGORICAL FIELDS

{categorical_field_rubrics}

# REALITY-CONTACT EXTENSION FIELDS

For each reality-contact signal that fires, capture **trigger** and **surface_type** from these closed sets:

**trigger** — what surfaced reality in that moment:
  - `testing_or_running` — user (or the agent's tool) executed the implementation and saw the result
  - `viewing_implementation` — user viewed the produced UI / output / file
  - `code_review_reading` — user read the produced code without running it
  - `error_encountered` — a specific error or failure prompted the moment
  - `comparing_to_existing` — user compared the agent's output to existing code/system
  - `stepping_back` — user paused to reconsider the goal
  - `unclear` — trigger not clearly attributable

**surface_type** — *where* the learning landed:
  - `ui_component` — a specific UI element (label, modal, form, button, layout)
  - `product_flow` — a user-facing flow (onboarding, checkout, search)
  - `technical_surface` — a specific technical artifact (table, service, API, library, function, schema)
  - `problem_class` — a problem category that recurs (reporting, search, auth)
  - `codebase_area` — a folder / module / subsystem path
  - `unclear` — surface not clearly identifiable

---

# SIGNALS

Tag ONLY signals clearly present. For each, provide evidence and any required extension fields.

{signal_definitions}

---

Respond with ONLY the JSON object via the `submit_annotation` tool. No markdown fencing, no preamble, no commentary."""


# Tool input_schema for ``submit_annotation``. Mirrors the prompt's expected
# output. The interpretive enums are listed inline; the signals dict is
# open-ended (additionalProperties) because not every signal fires per
# session — the schema enforces only the *shape* of the entries.
ANNOTATION_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"},
                "quality_concerns": {"type": "string"},
            },
            "required": ["title", "keywords", "summary", "quality_concerns"],
            "additionalProperties": False,
        },
        "engagement_depth": {
            "type": "string",
            "enum": ["high", "moderate", "low", "minimal"],
        },
        "engagement_notes": {"type": "string"},
        "interaction_style": {
            "type": "string",
            "enum": ["augmentative", "delegative", "mixed"],
        },
        "task_type": {
            "type": "string",
            "enum": [
                "implementation", "debugging", "refactor",
                "exploration", "configuration", "documentation",
                "migration", "other",
            ],
        },
        "arc_shape": {
            "type": "string",
            "enum": [
                "setup_first", "explore_first", "jump_in",
                "iterative", "unclear",
            ],
        },
        "signals": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "evidence": {"type": "string"},
                    "turn": {"type": "integer"},
                    "strength": {"type": "integer", "enum": [1, 2, 3]},
                    "trigger": {
                        "type": "string",
                        "enum": [
                            "testing_or_running", "viewing_implementation",
                            "code_review_reading", "error_encountered",
                            "comparing_to_existing", "stepping_back", "unclear",
                        ],
                    },
                    "surface_type": {
                        "type": "string",
                        "enum": [
                            "ui_component", "product_flow", "technical_surface",
                            "problem_class", "codebase_area", "unclear",
                        ],
                    },
                },
                "required": ["evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "summary", "engagement_depth", "engagement_notes",
        "interaction_style", "task_type", "arc_shape", "signals",
    ],
    "additionalProperties": False,
}
