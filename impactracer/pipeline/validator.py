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
    client: GeminiClient instance.

OUTPUTS
    SISValidationResult Pydantic model (enforced via response_schema).

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
    5. response_schema=SISValidationResult enforces JSON schema.
"""
from __future__ import annotations

from impactracer.llm_client import GeminiClient
from impactracer.models import CRInterpretation, SISValidationResult

_SYSTEM_PROMPT = (
    "You are a software impact analysis expert. "
    "Evaluate candidates strictly against the stated CR intent. "
    "Confirm ONLY candidates that are DIRECTLY affected by the specific change "
    "described in the CR. Reject candidates that are merely topically related "
    "but would not require modification due to this change. Be strict."
)


def mitigate_lost_in_middle(candidates: list[dict]) -> list[dict]:
    """Reorder candidates for lost-in-the-middle mitigation.

    LLMs tend to under-attend to middle positions in long prompts
    (Liu et al. 2023).  This reordering places the strongest candidates
    at the attention-rich beginning and end positions, with weaker
    candidates filling the middle.

    Ordering produced (all by reranker_score):
      Position 0:     highest score  (most relevant)
      Positions 1..N-2: ascending score  (weakest in middle)
      Position N-1:   second-highest score  (second-most relevant)

    Args:
        candidates: List already sorted descending by reranker_score.

    Returns:
        New list with lost-in-the-middle ordering applied.
    """
    if len(candidates) <= 2:
        return list(candidates)

    first  = candidates[0]                           # highest score -> pos 0
    last   = candidates[-1]                          # lowest score  -> pos N-1
    middle = sorted(
        candidates[1:-1],
        key=lambda c: c.get("reranker_score", 0.0),  # ascending in middle
    )
    return [first] + middle + [last]


def validate_sis_candidates(
    cr_interp: CRInterpretation,
    candidates: list[dict],
    client: GeminiClient,
) -> SISValidationResult:
    """LLM Call #2 — Validate all reranked candidates in a single pass.

    Builds a numbered candidate block (ID, type, 400-char snippet per
    candidate), applies lost-in-the-middle mitigation, and sends the
    full block to Gemini for strict binary validation.

    Args:
        cr_interp:  CRInterpretation from LLM Call #1.
        candidates: Reranked candidates from reranker.rerank(), each dict
                    contains at minimum node_id, text_snippet, node_type,
                    reranker_score.
        client:     Shared GeminiClient instance.

    Returns:
        SISValidationResult with one CandidateVerdict per candidate.
        Confirmed verdicts form the Seed Impact Set (SIS).
    """
    ordered = mitigate_lost_in_middle(candidates)

    candidate_block = "\n\n".join(
        f"[{i + 1}] ID: {c['node_id']}\n"
        f"Type: {c.get('node_type', 'unknown')}\n"
        f"Snippet: {c['text_snippet'][:400]}"
        for i, c in enumerate(ordered)
    )

    user_prompt = (
        f"Change Request Intent: {cr_interp.primary_intent}\n"
        f"Change Type: {cr_interp.change_type}\n"
        f"Domain Concepts: {', '.join(cr_interp.affected_domain_concepts)}\n\n"
        f"Evaluate each candidate below. Confirm ONLY if it is directly relevant "
        f"to this specific change request. Be strict — reject topically related "
        f"but functionally unaffected candidates.\n\n"
        f"{candidate_block}"
    )

    return client.parse(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=SISValidationResult,
    )
