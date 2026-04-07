"""
CR Interpreter -- LLM Call #1 (GIGO Validation + Semantic Extraction)
======================================================================

RESPONSIBILITY
    Receives raw CR text in natural language (typically Indonesian).
    Sends it to Gemini with a structured output schema to produce a
    CRInterpretation object with 8 attributes.

    GIGO VALIDATION (per Subbab III.2.2.1):
    The LLM FIRST assesses is_actionable.  If the CR is too vague,
    shorter than one sentence, or lacks identifiable change intent,
    is_actionable is set to False with a rejection_reason.
    runner.py checks this field and halts the pipeline before any
    retrieval if False.

INPUTS
    cr_text: str               Raw CR in any language (typically Indonesian).
    client:  GeminiClient      Shared LLM client from llm_client.py.

OUTPUTS
    CRInterpretation           Validated Pydantic model.

SYSTEM PROMPT DESIGN
    The prompt instructs Gemini to:
      1. Assess actionability first (GIGO gate).
      2. Extract semantic fields only when actionable.
      3. Produce search_queries in English regardless of CR language
         because they are matched against English code identifiers and
         doc section titles.
      4. Include both explicit and implied domain concepts.
      5. Fix A (v3.1): Extract excluded_operations — 2-4 English phrases
         naming business operations that are EXPLICITLY out of scope.
         These are injected into LLM Call #2 as a hard DO NOT confirm list
         to prevent retrieval false positives from topically related but
         functionally unaffected modules (e.g. the "Duplicate Commission"
         CR should exclude "order slot management", "contract lifecycle").

ARCHITECTURAL CONSTRAINTS
    1. This is LLM Call #1 of exactly 3 permitted calls.
    2. temperature=0.0 (greedy decoding) per NFR-07.
    3. response_schema=CRInterpretation enforces JSON structure.
    4. The raw CR text is NEVER accessed again after this step.
       All downstream components consume CRInterpretation only.
"""
from __future__ import annotations

from impactracer.llm_client import GeminiClient
from impactracer.models import CRInterpretation

_SYSTEM_PROMPT = """\
You are a software requirements analyst specialising in change impact analysis.

STEP 1 -- Actionability check (GIGO gate):
Assess whether the Change Request is actionable.
A CR is NOT actionable if ANY of the following apply:
  - It is shorter than one complete sentence.
  - It contains no identifiable change intent (e.g. "improve performance").
  - It is purely administrative or concerns only documentation metadata.
If NOT actionable: set is_actionable=false, populate rejection_reason with
one clear sentence, and leave all other fields at their default values.

STEP 2 -- Semantic extraction (only when is_actionable=true):
  primary_intent:            One sentence stating WHAT is being changed and WHY.
  change_type:               "add" | "modify" | "remove".
  affected_layers:           Subset of ["requirement", "design", "code"].
                             Include "requirement" if the CR alters stated
                             functional/non-functional requirements.
                             Include "design" if it alters system architecture
                             or component design decisions.
                             Include "code" if it requires direct code changes.
  affected_domain_concepts:  List of 1-10 business-domain concepts.
                             Include both explicitly stated and strongly implied
                             concepts (e.g. "dark mode" implies "theme",
                             "user preference", "UI component").
  search_queries:            2-3 English technical phrases optimised for
                             vector search against TypeScript function
                             signatures, class names, and API endpoint paths.
                             MUST be in English even if the CR is in Indonesian.
                             Example: ["theme toggle component", "user preference
                             settings API", "dark mode CSS variable"].
  excluded_operations:       2-4 English phrases naming business operations that
                             are EXPLICITLY OUT OF SCOPE for this CR.
                             These describe what the CR does NOT change — i.e.
                             adjacent modules that share domain vocabulary but
                             whose code will NOT be modified.
                             Think: "what other parts of the system use similar
                             terminology but are completely unrelated to this
                             specific feature change?"
                             Example: if the CR adds a "duplicate listing" feature,
                             excluded_operations = ["order slot management",
                             "contract lifecycle processing",
                             "payment and escrow operations"].
                             Leave as [] only if no meaningful out-of-scope
                             operations can be identified.

Return valid JSON matching the schema exactly."""


def interpret_cr(cr_text: str, client: GeminiClient) -> CRInterpretation:
    """Interpret a raw Change Request into a structured CRInterpretation.

    This is LLM Call #1.  The returned object is the sole input for all
    subsequent pipeline steps.  If is_actionable is False, runner.py
    halts the pipeline immediately and returns a rejection report.

    Args:
        cr_text: Raw Change Request text (any language).
        client:  Initialised GeminiClient (created once in runner.py).

    Returns:
        CRInterpretation with is_actionable assessed and, if True, all
        semantic fields populated.
    """
    return client.parse(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=cr_text,
        schema=CRInterpretation,
    )
