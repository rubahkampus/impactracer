"""
CR Interpreter — LLM Call #1 (GIGO Validation + Semantic Extraction)
=====================================================================

RESPONSIBILITY
    Receives raw CR text in natural language (typically Indonesian).
    Sends it to the LLM with a structured output schema to produce
    a CRInterpretation object with 7 attributes.

    GIGO VALIDATION (per Subbab III.2.2.1):
    The LLM FIRST assesses is_actionable. If the CR is too vague,
    shorter than one sentence, or lacks identifiable change intent,
    is_actionable is set to False with a rejection_reason.
    The runner.py module checks this field and halts the pipeline
    before any retrieval if False.

INPUTS
    cr_text: str (raw CR in any language)
    settings: Settings object

OUTPUTS
    CRInterpretation Pydantic model (enforced via response_format).

ARCHITECTURAL CONSTRAINTS
    1. This is LLM Call #1 of exactly 3 permitted calls.
    2. temperature=0.0, seed=42 (from settings) per NFR-07.
    3. response_format=CRInterpretation enforces JSON schema.
    4. search_queries MUST be in English regardless of CR language.
    5. The raw CR text is NEVER accessed again after this step.
       All downstream components consume CRInterpretation only.
"""
from __future__ import annotations

# TODO: Implement interpret_cr(cr_text, settings) -> CRInterpretation
