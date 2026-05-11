"""Lateral Query Expansion — Block A of the autonomous helper.

Once a week, the helper looks at the business (business_truth picture
+ competitor brands + observed queries) and asks an LLM:

    "Which other queries should this site plausibly rank for that it
    doesn't already track?"

The output is 15–20 query ideas with a confidence score, a
direct/related/info/weak relation tag, and a one-line rationale.
Owners then triage them in the UI (accept → promotes to demand_map,
reject → silenced).

The stages:

    context  → gather business_truth signals, top competitor brands,
               observed queries with wordstat volumes.
    llm      → single Haiku call returning structured queries.
    persist  → UPSERT into lateral_queries, never trampling owner status.
    task     → Celery beat orchestrator: per-site fan-out weekly.
"""
