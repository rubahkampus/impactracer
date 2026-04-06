"""
Report Synthesizer — LLM Call #3 (Impact Report Generation)
=============================================================

RESPONSIBILITY
    Sends the token-budgeted context payload to the LLM and receives
    a structured ImpactReport JSON object.

INPUTS
    context: str (assembled by context_builder.py).
    client:  GeminiClient instance.

OUTPUTS
    ImpactReport Pydantic model (enforced via response_schema).

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
    2. temperature=0.0 (greedy decoding) per NFR-07.
    3. response_schema=ImpactReport enforces JSON schema.
    4. The LLM is instructed NOT to invent relationships not present
       in the provided context. All causal chains are pre-computed
       by BFS and injected into the prompt.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from impactracer.llm_client import GeminiClient
from impactracer.models import ImpactReport

# Hard character limits matching Pydantic model constraints
_EXEC_SUMMARY_LIMIT = 800
_JUSTIFICATION_LIMIT = 300


def _parse_with_truncation(raw_text: str) -> ImpactReport:
    """Parse Gemini JSON response, hard-truncating overlong string fields.

    Called as a fallback when the raw LLM response violates Pydantic
    field length constraints.  Truncates at the model-defined limits
    before re-validating so the pipeline never fails on a too-verbose LLM.

    Args:
        raw_text: Raw JSON string from Gemini response.

    Returns:
        Validated ImpactReport instance.
    """
    data = json.loads(raw_text)

    if "executive_summary" in data:
        data["executive_summary"] = data["executive_summary"][:_EXEC_SUMMARY_LIMIT]

    for item in data.get("impacted_items", []):
        if "structural_justification" in item:
            item["structural_justification"] = (
                item["structural_justification"][:_JUSTIFICATION_LIMIT]
            )

    return ImpactReport.model_validate(data)

_SYNTHESIS_SYSTEM_PROMPT = """\
You are a software change impact analysis report generator.

Given a Change Request and a set of impacted code elements with their causal chains
and traceability backlinks, produce a structured impact report.

FIELD LENGTH CONSTRAINTS (strictly enforced):
- executive_summary: MAXIMUM 800 characters (count carefully -- do NOT exceed).
- structural_justification per item: MAXIMUM 300 characters each.

Severity assignment rules (deterministic, based on edge types in causal_chain):
- Tinggi:   chain contains IMPLEMENTS or TYPED_BY (direct contract dependency)
- Menengah: chain contains CALLS, INHERITS, or DEFINES_METHOD (behavioral propagation)
- Rendah:   chain contains only IMPORTS, RENDERS, or DEPENDS_ON_EXTERNAL (module-level)
- SIS seeds (empty chain / "SIS seed"): Tinggi

For structural_justification, describe WHY this node is impacted by referencing
the specific edge chain shown. Do NOT invent relationships not present in the data.

For traceability_backlinks, include the doc chunk IDs listed in each node's
Backlinks field verbatim. Do NOT invent backlink IDs.

For requirement_conflicts, examine the traceability backlinks and identify any
document sections whose stated requirements may conflict with the proposed change.
If none, return an empty list.

For estimated_change_scope:
- "terlokalisasi"  if <= 5 impacted items
- "menengah"       if 6-20 impacted items
- "ekstensif"      if > 20 impacted items

Return valid JSON matching the schema exactly. Do not add commentary outside the JSON."""


def synthesize_report(
    context: str,
    client: GeminiClient,
) -> ImpactReport:
    """LLM Call #3 — Synthesize a structured ImpactReport from the CIS context.

    The context string produced by build_synthesis_context() is passed
    directly as the user message. The system prompt enforces deterministic
    severity assignment rules and prohibits hallucinated relationships.

    Args:
        context: Token-budgeted context string from context_builder.py.
                 Contains the CR header, interpretation, and per-node
                 blocks with causal chains and traceability backlinks.
        client:  Shared GeminiClient instance (created once in runner.py).

    Returns:
        ImpactReport with all four required fields populated:
          - executive_summary  (max 800 chars)
          - impacted_items     (list of ImpactedItem)
          - requirement_conflicts
          - estimated_change_scope
    """
    try:
        return client.parse(
            system_prompt=_SYNTHESIS_SYSTEM_PROMPT,
            user_prompt=context,
            schema=ImpactReport,
        )
    except ValidationError:
        # Safety net: LLM occasionally exceeds field length limits despite
        # explicit instructions (typically executive_summary > 800 chars).
        # Retrieve the raw response text stored by GeminiClient._last_text,
        # hard-truncate overlong fields, then re-validate.
        return _parse_with_truncation(client._last_text)
