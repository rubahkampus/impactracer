"""
ImpacTracer Data Models
========================

RESPONSIBILITY
    Defines every data contract in the system. All inter-component
    communication passes through the types defined in this file.
    No module may define ad-hoc dicts for cross-boundary data transfer.

SCHEMAS DEFINED
    1. CRInterpretation (LLM Call #1 output, 8 attributes).
       Includes is_actionable for GIGO validation and rejection_reason
       for early termination per Subbab III.2.2.1.
       Fix A (v3.1): added excluded_operations field — 2-4 English phrases
       naming operations explicitly out of scope for the CR.  Propagated
       into LLM Call #2 as a hard DO NOT confirm list.
    2. CandidateVerdict and SISValidationResult (LLM Call #2 output).
       CandidateVerdict uses a 5-field structured Chain-of-Thought schema:
       function_purpose → mechanism_of_impact → justification → confirmed.
       Fields are defined in reasoning-first order so the LLM must complete
       all reasoning steps before emitting the binary verdict.
    3. ImpactedItem and ImpactReport (LLM Call #3 output, 4 top-level
       attributes per Subbab III.2.5.3).
    4. NodeTrace and CISResult (BFS output, deterministic dataclasses).
    5. EDGE_WEIGHT dict and structural_weight() function for
       deterministic CIS sorting per Subbab III.7.4.

ARCHITECTURAL CONSTRAINTS
    All three LLM output schemas are Pydantic BaseModel subclasses
    and MUST be passed to the response_format parameter of the OpenAI
    API to enforce structured JSON output. The LLM cannot produce
    attributes outside these schemas.

    CISResult and NodeTrace are stdlib dataclasses (not Pydantic)
    because they are internal computation artifacts, never serialized
    to/from LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# ── Shared Base Model ─────────────────────────────────────────────

class TruncatingModel(BaseModel):
    """Pydantic base model with graceful LLM output string truncation.

    LLMs cannot reliably count characters, so string fields constrained
    by max_length may be exceeded.  This validator intercepts raw input
    before field-level validation and silently truncates any overlong
    string to its field's declared max_length, preventing ValidationError
    crashes in the pipeline.

    Architectural rule: All LLM-generated string fields must utilise
    graceful truncation fallback mechanisms to prevent pipeline crashes
    from Pydantic strict length limits.
    """

    @model_validator(mode='before')
    @classmethod
    def _truncate_overlong_strings(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for field_name, field_info in cls.model_fields.items():
            value = data.get(field_name)
            if not isinstance(value, str):
                continue
            for meta in field_info.metadata:
                max_len = getattr(meta, 'max_length', None)
                if max_len is not None and len(value) > max_len:
                    data[field_name] = value[:max_len]
                    break
        return data


# ── LLM Call #1 Output ─────────────────────────────────────────────

class CRInterpretation(BaseModel):
    """Structured interpretation of a Change Request.

    The LLM MUST first assess is_actionable. If False, rejection_reason
    is populated and the pipeline halts before any retrieval occurs.
    If True, all remaining seven fields are populated.
    """

    is_actionable: bool = Field(
        description=(
            "False if the CR is too ambiguous, contains no identifiable "
            "change intent, or is shorter than one sentence."
        )
    )
    rejection_reason: str | None = Field(
        default=None,
        description=(
            "One sentence explaining why the CR was rejected. "
            "Null when is_actionable is True."
        ),
    )
    primary_intent: str = Field(
        description="Single sentence stating what is being changed and why.",
    )
    change_type: Literal["add", "modify", "remove"]
    affected_layers: list[Literal["requirement", "design", "code"]]
    affected_domain_concepts: list[str] = Field(
        description="Business domain concepts, explicit and implied.",
        min_length=1,
        max_length=10,
    )
    search_queries: list[str] = Field(
        description=(
            "2-3 English technical phrases optimized for vector search "
            "against code function signatures and doc section titles."
        ),
        min_length=2,
        max_length=3,
    )
    excluded_operations: list[str] = Field(
        default_factory=list,
        description=(
            "2-4 English phrases naming business operations that are "
            "EXPLICITLY OUT OF SCOPE for this CR.  These describe what the "
            "CR does NOT change — e.g. if the CR adds a 'duplicate listing' "
            "feature, excluded operations might include 'order slot management', "
            "'contract lifecycle', 'payment processing'. "
            "Used as a hard DO NOT confirm list in the SIS Validator (LLM Call #2) "
            "to prevent poisoned-seed false positives from topically related but "
            "functionally unaffected modules. Leave empty ([]) only if no "
            "meaningful out-of-scope operations can be identified."
        ),
        max_length=4,
    )


# ── LLM Call #2 Output ─────────────────────────────────────────────

class CandidateVerdict(TruncatingModel):
    """Per-candidate confirmation or rejection from LLM validation.

    Fields are ordered to enforce Chain-of-Thought reasoning before
    the binary verdict is produced. Because Gemini generates JSON fields
    in schema-definition order, placing `confirmed` last forces the model
    to complete all three reasoning steps first.

    CoT Step 1 — function_purpose:   What does this node do?
    CoT Step 2 — mechanism_of_impact: How exactly does the CR require it
                                       to change? (empty = reject signal)
    CoT Step 3 — justification:       Final one-sentence summary.
    Verdict    — confirmed:            Binary decision, produced last.
    """

    node_id: str = Field(description="The candidate's node_id, copied verbatim.")
    function_purpose: str = Field(
        max_length=150,
        description=(
            "One sentence describing what this function/interface does "
            "in the business domain, based solely on its snippet."
        ),
    )
    mechanism_of_impact: str = Field(
        max_length=200,
        description=(
            "The concrete mechanism by which the CR's change would require "
            "modification of this specific node — e.g. 'This function creates "
            "a new listing and must be extended to accept a source listing ID.' "
            "Leave EMPTY if no direct modification mechanism exists. "
            "Topical relevance or same-file co-location are NOT valid mechanisms."
        ),
    )
    justification: str = Field(
        max_length=200,
        description=(
            "One-sentence summary of the confirmation or rejection decision."
        ),
    )
    confirmed: bool = Field(
        description=(
            "True only if mechanism_of_impact is non-empty AND describes a "
            "direct structural dependency on the CR's change. False otherwise."
        ),
    )


class SISValidationResult(BaseModel):
    """Aggregated validation verdicts for all candidates."""

    verdicts: list[CandidateVerdict]


# ── LLM Call #3 Output ─────────────────────────────────────────────

class ImpactedItem(TruncatingModel):
    """Single impacted element in the final report."""

    node_id: str
    node_type: str
    file_path: str
    severity: Literal["Tinggi", "Menengah", "Rendah"]
    causal_chain: list[str] = Field(
        description="Ordered edge types from SIS root to this node."
    )
    structural_justification: str = Field(max_length=200)
    traceability_backlinks: list[str] = Field(default_factory=list)


class ImpactReport(TruncatingModel):
    """Final structured impact analysis report.

    This schema is enforced via response_format on LLM Call #3.
    The LLM cannot omit any of the four required attributes.
    """

    executive_summary: str = Field(max_length=800)
    impacted_items: list[ImpactedItem]
    requirement_conflicts: list[str] = Field(default_factory=list)
    estimated_change_scope: Literal[
        "terlokalisasi", "menengah", "ekstensif"
    ]


# ── BFS Output (deterministic, not Pydantic) ──────────────────────

@dataclass
class NodeTrace:
    """Provenance record for a single node discovered by BFS."""

    depth: int
    causal_chain: list[str]
    path: list[str]
    source_seed: str


@dataclass
class CISResult:
    """Complete BFS traversal result.

    sis_nodes contains the seed nodes at depth 0.
    propagated_nodes contains all nodes discovered at depth >= 1.
    combined() merges both for evaluation and report synthesis.
    """

    sis_nodes: dict[str, NodeTrace] = field(default_factory=dict)
    propagated_nodes: dict[str, NodeTrace] = field(default_factory=dict)

    def combined(self) -> dict[str, NodeTrace]:
        return {**self.sis_nodes, **self.propagated_nodes}

    def all_node_ids(self) -> list[str]:
        return list(self.sis_nodes.keys()) + list(
            self.propagated_nodes.keys()
        )


# ── Evaluation Sort ────────────────────────────────────────────────

EDGE_WEIGHT: dict[str, int] = {
    "IMPLEMENTS": 0,
    "TYPED_BY": 0,
    "CALLS": 1,
    "INHERITS": 1,
    "DEFINES_METHOD": 1,
    "IMPORTS": 2,
    "RENDERS": 2,
    "DEPENDS_ON_EXTERNAL": 2,
}


def structural_weight(causal_chain: list[str]) -> int:
    """Compute sort weight from edge chain. Lower is more significant.

    SIS seeds (empty chain) return -1 so they always sort first.
    For propagated nodes, the minimum edge weight in the chain
    determines the significance tier.
    """
    if not causal_chain:
        return -1
    return min(EDGE_WEIGHT.get(e, 2) for e in causal_chain)
