"""
SIS Validator — LLM Call #2 (Contextual Candidate Validation)
==============================================================

RESPONSIBILITY
    Sends all reranked candidates to the LLM in a single call for
    contextual validation against the CR intent. Produces per-candidate
    confirmed/rejected verdicts that form the SIS.

INPUTS
    cr_interp: CRInterpretation (for primary_intent and domain concepts).
    candidates: list of dicts (from reranker, sorted by reranker_score).
    settings: Settings object.

OUTPUTS
    SISValidationResult Pydantic model (enforced via response_format).

LOST-IN-THE-MIDDLE MITIGATION (per Subbab III.2.3.4, Liu et al. 2023)
    Candidate ordering before injection into prompt:
    Position 0:   highest reranker_score.
    Position N-1: lowest reranker_score.
    Positions 1..N-2: sorted ascending by reranker_score.
    This places the most promising candidates at attention-rich
    positions (beginning and end of context).

ARCHITECTURAL CONSTRAINTS
    1. This is LLM Call #2 of exactly 3 permitted calls.
    2. ALL candidates in ONE call (not one call per candidate).
    3. Each candidate snippet truncated to 400 chars max.
    4. temperature=0.0, seed=42 per NFR-07.
    5. response_format=SISValidationResult enforces JSON schema.
"""
from __future__ import annotations

# TODO: Implement validate_sis_candidates(), mitigate_lost_in_middle()
