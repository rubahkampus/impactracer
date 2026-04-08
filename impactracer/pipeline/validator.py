"""
SIS Validator — LLM Call #2 (Contextual Candidate Validation)
==============================================================

RESPONSIBILITY
    Sends all reranked candidates to the LLM in a single call for
    contextual validation against the CR intent. Produces per-candidate
    confirmed/rejected verdicts that form the SIS.

INPUTS
    cr_interp: CRInterpretation (for primary_intent, domain concepts,
               and excluded_operations).
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

FIX A (v3.1) — excluded_operations injection:
    If CRInterpretation.excluded_operations is non-empty, a hard
    DO NOT confirm section is prepended to the user prompt.  This
    prevents the LLM from confirming candidates that belong to
    operations explicitly identified as out-of-scope by LLM Call #1.

FIX B (v3.1) — enriched candidate block:
    Each candidate entry now includes File (file_path) and
    Reranker score fields.  Showing the source module path gives
    the LLM architectural context to identify cross-domain false
    positives (e.g. a contract-service function surfaced for a
    listing-form CR).  The reranker score signals relative confidence.
"""
from __future__ import annotations

from impactracer.llm_client import GeminiClient
from impactracer.models import CRInterpretation, SISValidationResult

_SYSTEM_PROMPT = (
    "You are a software impact analysis expert. "
    "Evaluate candidates strictly against the stated CR intent. "
    "Confirm ONLY candidates that are DIRECTLY affected by the specific change "
    "described in the CR. Reject candidates that are merely topically related "
    "but would not require modification due to this change. Be strict. "
    "Pay close attention to the File path of each candidate — a function in an "
    "unrelated service module (e.g. contract or payment service) is almost never "
    "directly impacted by a CR targeting a different business feature."
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
        Confirmed verdicts form the Starting Impact Set (SIS).
    """
    ordered = mitigate_lost_in_middle(candidates)

    # Fix B: Enrich each candidate entry with file_path and reranker_score.
    # file_path exposes the source module so the LLM can detect cross-domain
    # false positives (e.g. a contract-service function ranked for a
    # listing-form CR). reranker_score signals the cross-encoder's confidence
    # and anchors the LLM's prior before it reads the snippet.
    candidate_block = "\n\n".join(
        f"[{i + 1}] ID: {c['node_id']}\n"
        f"Type: {c.get('node_type', 'unknown')}\n"
        f"File: {c.get('file_path', 'unknown')}\n"
        f"Reranker score: {c.get('reranker_score', 0.0):.3f}\n"
        f"Snippet: {c['text_snippet'][:400]}"
        for i, c in enumerate(ordered)
    )

    # Fix A: Inject excluded_operations as a hard DO NOT confirm list when
    # LLM Call #1 identified out-of-scope operations.  This provides an
    # explicit exclusion signal to counteract retrieval false positives from
    # modules that share vocabulary with the CR's domain but are functionally
    # unrelated.
    excluded_section = ""
    if getattr(cr_interp, "excluded_operations", None):
        ops_formatted = "\n".join(
            f"  - {op}" for op in cr_interp.excluded_operations
        )
        excluded_section = (
            f"\nOUT-OF-SCOPE OPERATIONS — these business operations are "
            f"EXPLICITLY NOT changed by this CR.\n"
            f"DO NOT confirm any candidate that primarily serves one of these "
            f"operations, even if it shares vocabulary with the CR:\n"
            f"{ops_formatted}\n"
        )

    user_prompt = (
        f"Change Request Intent: {cr_interp.primary_intent}\n"
        f"Change Type: {cr_interp.change_type}\n"
        f"Domain Concepts: {', '.join(cr_interp.affected_domain_concepts)}\n"
        f"{excluded_section}"
        f"\nEvaluate each candidate below. Confirm ONLY if it is directly "
        f"relevant to this specific change request. Be strict — reject "
        f"topically related but functionally unaffected candidates.\n\n"
        f"{candidate_block}"
    )

    return client.parse(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=SISValidationResult,
    )
