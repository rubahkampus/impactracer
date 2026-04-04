"""
Report Synthesizer — LLM Call #3 (Impact Report Generation)
=============================================================

RESPONSIBILITY
    Sends the token-budgeted context payload to the LLM and receives
    a structured ImpactReport JSON object.

INPUTS
    context: str (assembled by context_builder.py).
    settings: Settings object.

OUTPUTS
    ImpactReport Pydantic model (enforced via response_format).

SEVERITY ASSIGNMENT RULES (per Subbab III.2.5.3)
    The system prompt instructs the LLM to assign severity based on
    deterministic criteria derived from causal_chain edge types,
    NOT from free-form LLM judgment.

    Tinggi:    chain contains IMPLEMENTS or TYPED_BY.
    Menengah:  chain contains CALLS, INHERITS, or DEFINES_METHOD.
    Rendah:    chain contains only IMPORTS, RENDERS, or DEPENDS_ON_EXTERNAL.
    SIS seeds (empty chain): Tinggi.

ARCHITECTURAL CONSTRAINTS
    1. This is LLM Call #3 of exactly 3 permitted calls.
    2. temperature=0.0, seed=42 per NFR-07.
    3. response_format=ImpactReport enforces JSON schema.
    4. The LLM is instructed NOT to invent relationships not present
       in the provided context. All causal chains are pre-computed
       by BFS and injected into the prompt.
"""
from __future__ import annotations

# TODO: Implement synthesize_report(context, settings) -> ImpactReport
