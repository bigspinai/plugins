# Baselines

Population-level baseline rates that the report uses to contextualize a
single user's session corpus. The report says things like *"you cite team
conventions in 18% of your sessions vs 7% in the baseline"* — the
right-hand number comes from here.

## Status: measured

Generated from 4,336 successfully-tagged sessions
(of 4,846 total in the source corpus) on 2026-05-01.

Source: `data/swe_chat_sessions_tagged.csv`

Deterministic-signal baselines were computed from 4,846
enriched sessions in `data/swe_chat_sessions_enriched.csv`.

## File format

| File | Schema | Layer |
|---|---|---|
| `signal_rates.csv` | `signal,category,rate_pct,mean_strength` | interpretive |
| `category_rates.csv` | `category,rate_pct` | interpretive |
| `engagement_depth_distribution.csv` | `engagement_depth,rate_pct` | interpretive |
| `interaction_style_distribution.csv` | `interaction_style,rate_pct` | interpretive |
| `deterministic_baselines.csv` | `signal,value_type,n,mean,median,p10,p25,p75,p90` | deterministic |
| `task_size_distribution.csv` | `task_size,rate_pct` | deterministic |

`rate_pct` is the percent of sessions in the baseline corpus where the
signal/category fired (or, for the distributions, the percent of sessions
in that bucket). `mean_strength` is averaged only over sessions in which
the signal fired, and is blank for presence-only signals (anti-patterns
and reality-contact moments).

For deterministic signals, the per-session values are summarized as
`mean / median / p10 / p25 / p75 / p90`. The downstream consumer compares
a user's per-session distribution against these thresholds (e.g. "your
median `iteration_count` sits at the population p25 — your sessions are
shorter than typical").
