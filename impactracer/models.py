"""
ImpacTracer Data Models
========================

RESPONSIBILITY
    Defines every data contract in the system. All inter-component
    communication passes through the types defined in this file.
    No module may define ad-hoc dicts for cross-boundary data transfer.

SCHEMAS DEFINED
    1. CRInterpretation (LLM Call #1 output, 7 attributes).
       Includes is_actionable for GIGO validation and rejection_reason
       for early termination per Subbab III.2.2.1.
    2. CandidateVerdict and SISValidationResult (LLM Call #2 output).
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
from typing import Literal

from pydantic import BaseModel, Field


# ── LLM Call #1 Output ─────────────────────────────────────────────

class CRInterpretation(BaseModel):
    """Structured interpretation of a Change Request.

    The LLM MUST first assess is_actionable. If False, rejection_reason
    is populated and the pipeline halts before any retrieval occurs.
    If True, all remaining six fields are populated.
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


# ── LLM Call #2 Output ─────────────────────────────────────────────

class CandidateVerdict(BaseModel):
    """Per-candidate confirmation or rejection from LLM validation."""

    node_id: str
    confirmed: bool
    justification: str = Field(max_length=200)


class SISValidationResult(BaseModel):
    """Aggregated validation verdicts for all candidates."""

    verdicts: list[CandidateVerdict]


# ── LLM Call #3 Output ─────────────────────────────────────────────

class ImpactedItem(BaseModel):
    """Single impacted element in the final report."""

    node_id: str
    node_type: str
    file_path: str
    severity: Literal["Tinggi", "Menengah", "Rendah"]
    causal_chain: list[str] = Field(
        description="Ordered edge types from SIS root to this node."
    )
    structural_justification: str = Field(max_length=300)
    traceability_backlinks: list[str] = Field(default_factory=list)


class ImpactReport(BaseModel):
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
