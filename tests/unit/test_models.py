"""Sprint 1 acceptance tests: schema round-trips, truncation, severity, and constants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from impactracer.shared.constants import (
    EDGE_CONFIG,
    LOW_CONF_CAPPED_EDGES,
    PROPAGATION_VALIDATION_EXEMPT_EDGES,
    RRF_PATH_WEIGHTS,
    SEVERITY_BY_EDGE_CHAIN_TYPE,
    layer_compat,
    severity_for_chain,
)
from impactracer.shared.models import (
    CandidateVerdict,
    CRInterpretation,
    ImpactedNode,
    ImpactReport,
    PropagationValidationResult,
    PropagationVerdict,
    SISValidationResult,
    TraceValidationResult,
    TraceVerdict,
)

# =============================================================================
# Group A — TruncatingModel truncation (03_data_models.md §1)
# =============================================================================


def test_truncating_model_truncates_at_max_length() -> None:
    """String exceeding max_length is silently truncated; no ValidationError."""
    overlong = "x" * 201
    verdict = CandidateVerdict(
        node_id="n1",
        function_purpose="does something",
        mechanism_of_impact="some mechanism",
        justification=overlong,
        confirmed=True,
    )
    assert len(verdict.justification) == 200
    assert verdict.justification == "x" * 200


def test_truncating_model_does_not_truncate_within_limit() -> None:
    """String at exactly max_length is stored unchanged."""
    exact = "y" * 200
    verdict = CandidateVerdict(
        node_id="n1",
        function_purpose="does something",
        mechanism_of_impact="some mechanism",
        justification=exact,
        confirmed=False,
    )
    assert verdict.justification == exact


def test_truncating_model_non_string_fields_unaffected() -> None:
    """Boolean field confirmed is not altered by the truncation validator."""
    verdict = CandidateVerdict(
        node_id="n1",
        function_purpose="does something",
        mechanism_of_impact="",
        justification="short",
        confirmed=False,
    )
    assert verdict.confirmed is False


def test_truncating_model_function_purpose_truncated() -> None:
    """function_purpose (max_length=150) is truncated when overlong."""
    verdict = CandidateVerdict(
        node_id="n1",
        function_purpose="a" * 200,
        mechanism_of_impact="",
        justification="ok",
        confirmed=False,
    )
    assert len(verdict.function_purpose) == 150


# =============================================================================
# Group B — Round-trip serialization (03_data_models.md §3–§7)
# =============================================================================


def test_cr_interpretation_round_trip() -> None:
    original = CRInterpretation(
        is_actionable=True,
        actionability_reason=None,
        primary_intent="Add price field to listing entity.",
        change_type="ADDITION",
        affected_layers=["requirement", "code"],
        domain_concepts=["listing", "price"],
        search_queries=["add price field listing", "listing entity price attribute"],
        named_entry_points=["createListing"],
        out_of_scope_operations=["deleteListing"],
    )
    assert CRInterpretation.model_validate_json(original.model_dump_json()) == original


def test_candidate_verdict_round_trip() -> None:
    original = CandidateVerdict(
        node_id="fn::src/lib/services/listing.ts::createListing",
        function_purpose="Creates a new commission listing.",
        mechanism_of_impact="Must add price parameter and persist to DB.",
        justification="Directly creates the listing entity being modified.",
        confirmed=True,
    )
    assert CandidateVerdict.model_validate_json(original.model_dump_json()) == original


def test_sis_validation_result_round_trip() -> None:
    original = SISValidationResult(
        verdicts=[
            CandidateVerdict(
                node_id="fn::src/lib/services/listing.ts::createListing",
                function_purpose="Creates listing.",
                mechanism_of_impact="Adds price field.",
                justification="Directly relevant.",
                confirmed=True,
            ),
            CandidateVerdict(
                node_id="fn::src/lib/utils/format.ts::formatCurrency",
                function_purpose="Formats currency values.",
                mechanism_of_impact="",
                justification="Only topically related, not structurally.",
                confirmed=False,
            ),
        ]
    )
    assert SISValidationResult.model_validate_json(original.model_dump_json()) == original


def test_trace_verdict_round_trip() -> None:
    original = TraceVerdict(
        doc_chunk_id="srs::fr-12",
        code_node_id="fn::src/lib/services/listing.ts::createListing",
        decision="CONFIRMED",
        justification="Code node implements the FR directly.",
    )
    assert TraceVerdict.model_validate_json(original.model_dump_json()) == original


def test_trace_validation_result_round_trip() -> None:
    original = TraceValidationResult(
        verdicts=[
            TraceVerdict(
                doc_chunk_id="srs::fr-12",
                code_node_id="fn::src/lib/services/listing.ts::createListing",
                decision="CONFIRMED",
                justification="Implements FR.",
            ),
            TraceVerdict(
                doc_chunk_id="srs::fr-15",
                code_node_id="fn::src/lib/services/listing.ts::updateListing",
                decision="PARTIAL",
                justification="Partial overlap.",
            ),
            TraceVerdict(
                doc_chunk_id="srs::fr-20",
                code_node_id="fn::src/lib/utils/format.ts::formatCurrency",
                decision="REJECTED",
                justification="No implementation relationship.",
            ),
        ]
    )
    assert (
        TraceValidationResult.model_validate_json(original.model_dump_json()) == original
    )


def test_propagation_verdict_round_trip() -> None:
    original = PropagationVerdict(
        node_id="fn::src/app/api/listings/route.ts::POST",
        semantically_impacted=True,
        justification="Calls createListing which must accept the new price field.",
    )
    assert (
        PropagationVerdict.model_validate_json(original.model_dump_json()) == original
    )


def test_propagation_validation_result_round_trip() -> None:
    original = PropagationValidationResult(
        verdicts=[
            PropagationVerdict(
                node_id="fn::src/app/api/listings/route.ts::POST",
                semantically_impacted=True,
                justification="Caller must pass price to createListing.",
            ),
            PropagationVerdict(
                node_id="fn::src/lib/utils/logger.ts::log",
                semantically_impacted=False,
                justification="Only logs, no structural dependency on price.",
            ),
        ]
    )
    assert (
        PropagationValidationResult.model_validate_json(original.model_dump_json())
        == original
    )


def test_impacted_node_round_trip() -> None:
    original = ImpactedNode(
        node_id="fn::src/lib/services/listing.ts::createListing",
        node_type="Function",
        file_path="src/lib/services/listing.ts",
        severity="Tinggi",
        causal_chain=[],
        structural_justification="Direct SIS seed; must add price parameter.",
        traceability_backlinks=["srs::fr-12"],
    )
    assert ImpactedNode.model_validate_json(original.model_dump_json()) == original


def test_impact_report_round_trip() -> None:
    original = ImpactReport(
        executive_summary="Adding price field to listing affects the service and API layers.",
        impacted_nodes=[
            ImpactedNode(
                node_id="fn::src/lib/services/listing.ts::createListing",
                node_type="Function",
                file_path="src/lib/services/listing.ts",
                severity="Tinggi",
                causal_chain=[],
                structural_justification="SIS seed.",
                traceability_backlinks=["srs::fr-12"],
            )
        ],
        documentation_conflicts=["srs::fr-12 does not mention a price field yet."],
        estimated_scope="menengah",
    )
    assert ImpactReport.model_validate_json(original.model_dump_json()) == original


# =============================================================================
# Group C — ChangeType enum validation (03_data_models.md §2)
# =============================================================================


def test_change_type_accepts_valid_values() -> None:
    """All three ChangeType literals are accepted by CRInterpretation."""
    base = {
        "is_actionable": True,
        "primary_intent": "intent",
        "affected_layers": ["code"],
        "domain_concepts": ["listing"],
        "search_queries": ["query one", "query two"],
    }
    for ct in ("ADDITION", "MODIFICATION", "DELETION"):
        cr = CRInterpretation(**base, change_type=ct)  # type: ignore[arg-type]
        assert cr.change_type == ct


def test_change_type_rejects_invalid() -> None:
    """An unknown change_type value raises ValidationError."""
    with pytest.raises(ValidationError):
        CRInterpretation(
            is_actionable=True,
            primary_intent="intent",
            change_type="UNKNOWN",  # type: ignore[arg-type]
            affected_layers=["code"],
            domain_concepts=["listing"],
            search_queries=["query one", "query two"],
        )


# =============================================================================
# Group D — severity_for_chain (03_data_models.md §7.1)
# =============================================================================


def test_severity_empty_chain() -> None:
    """Empty chain (SIS seed) returns Tinggi."""
    assert severity_for_chain([]) == "Tinggi"


def test_severity_contract_edges() -> None:
    """Any contract edge yields Tinggi."""
    for edge in ("IMPLEMENTS", "TYPED_BY", "FIELDS_ACCESSED"):
        assert severity_for_chain([edge]) == "Tinggi", f"Failed for edge: {edge}"


def test_severity_behavioral_edges() -> None:
    """Pure behavioral chains yield Menengah."""
    for edge in ("CALLS", "INHERITS", "DEFINES_METHOD", "HOOK_DEPENDS_ON", "PASSES_CALLBACK"):
        assert severity_for_chain([edge]) == "Menengah", f"Failed for edge: {edge}"


def test_severity_module_composition_edges() -> None:
    """Pure module-composition chains yield Rendah."""
    for edge in ("IMPORTS", "RENDERS", "DEPENDS_ON_EXTERNAL", "CLIENT_API_CALLS", "DYNAMIC_IMPORT"):
        assert severity_for_chain([edge]) == "Rendah", f"Failed for edge: {edge}"


def test_severity_mixed_chain_takes_highest() -> None:
    """Mixed chain resolves to the most severe edge category."""
    assert severity_for_chain(["IMPORTS", "CALLS"]) == "Menengah"
    assert severity_for_chain(["IMPORTS", "IMPLEMENTS"]) == "Tinggi"
    assert severity_for_chain(["CALLS", "IMPLEMENTS"]) == "Tinggi"
    assert severity_for_chain(["IMPORTS", "CALLS", "TYPED_BY"]) == "Tinggi"


def test_severity_unknown_edge_falls_back_to_rendah() -> None:
    """Unknown edge type defaults to Rendah."""
    assert severity_for_chain(["NONEXISTENT"]) == "Rendah"


def test_severity_by_edge_chain_type_completeness() -> None:
    """SEVERITY_BY_EDGE_CHAIN_TYPE covers all 13 edge types from EDGE_CONFIG."""
    for edge in EDGE_CONFIG:
        assert edge in SEVERITY_BY_EDGE_CHAIN_TYPE, (
            f"Edge {edge!r} is in EDGE_CONFIG but missing from SEVERITY_BY_EDGE_CHAIN_TYPE"
        )


# =============================================================================
# Group E — layer_compat matrix (03_data_models.md §11)
# =============================================================================


def test_layer_compat_known_pairs() -> None:
    """Spot-check every named row from the exact matrix values."""
    cases = [
        ("API_ROUTE",       "FR",     1.0),
        ("API_ROUTE",       "NFR",    0.5),
        ("API_ROUTE",       "Design", 0.8),
        ("API_ROUTE",       "General",0.5),
        ("PAGE_COMPONENT",  "FR",     1.0),
        ("PAGE_COMPONENT",  "NFR",    0.5),
        ("PAGE_COMPONENT",  "Design", 0.9),
        ("PAGE_COMPONENT",  "General",0.5),
        ("UI_COMPONENT",    "FR",     0.9),
        ("UI_COMPONENT",    "NFR",    0.5),
        ("UI_COMPONENT",    "Design", 0.9),
        ("UI_COMPONENT",    "General",0.5),
        ("UTILITY",         "FR",     0.7),
        ("UTILITY",         "NFR",    0.7),
        ("UTILITY",         "Design", 0.8),
        ("UTILITY",         "General",0.5),
        ("TYPE_DEFINITION", "FR",     0.6),
        ("TYPE_DEFINITION", "NFR",    0.3),
        ("TYPE_DEFINITION", "Design", 0.9),
        ("TYPE_DEFINITION", "General",0.5),
    ]
    for code_cls, chunk_type, expected in cases:
        result = layer_compat(code_cls, chunk_type)
        assert result == expected, (
            f"layer_compat({code_cls!r}, {chunk_type!r}) = {result}, expected {expected}"
        )


def test_layer_compat_none_classification() -> None:
    """None classification uses the fallback row."""
    assert layer_compat(None, "FR") == 0.8
    assert layer_compat(None, "NFR") == 0.5
    assert layer_compat(None, "Design") == 0.8
    assert layer_compat(None, "General") == 0.5


def test_layer_compat_unknown_chunk_type_fallback() -> None:
    """Unknown doc_chunk_type returns 0.5 regardless of code classification."""
    assert layer_compat("API_ROUTE", "Unknown") == 0.5
    assert layer_compat(None, "Unknown") == 0.5


# =============================================================================
# Group F — EDGE_CONFIG and constant set completeness (03_data_models.md §12)
# =============================================================================


def test_edge_config_has_13_edges() -> None:
    """EDGE_CONFIG must contain exactly 13 edge types."""
    assert len(EDGE_CONFIG) == 13


def test_edge_config_directions_and_depths() -> None:
    """Spot-check critical direction and depth values."""
    assert EDGE_CONFIG["CALLS"] == {"direction": "reverse", "max_depth": 3}
    assert EDGE_CONFIG["INHERITS"] == {"direction": "reverse", "max_depth": 3}
    assert EDGE_CONFIG["IMPLEMENTS"] == {"direction": "reverse", "max_depth": 3}
    assert EDGE_CONFIG["TYPED_BY"] == {"direction": "reverse", "max_depth": 3}
    assert EDGE_CONFIG["FIELDS_ACCESSED"] == {"direction": "reverse", "max_depth": 2}
    assert EDGE_CONFIG["DEFINES_METHOD"] == {"direction": "forward", "max_depth": 3}
    assert EDGE_CONFIG["PASSES_CALLBACK"] == {"direction": "forward", "max_depth": 1}
    assert EDGE_CONFIG["HOOK_DEPENDS_ON"] == {"direction": "reverse", "max_depth": 1}
    assert EDGE_CONFIG["IMPORTS"] == {"direction": "reverse", "max_depth": 1}
    assert EDGE_CONFIG["RENDERS"] == {"direction": "reverse", "max_depth": 1}
    assert EDGE_CONFIG["DEPENDS_ON_EXTERNAL"] == {"direction": "reverse", "max_depth": 1}
    assert EDGE_CONFIG["CLIENT_API_CALLS"] == {"direction": "reverse", "max_depth": 1}
    assert EDGE_CONFIG["DYNAMIC_IMPORT"] == {"direction": "reverse", "max_depth": 1}


def test_low_conf_capped_edges() -> None:
    """LOW_CONF_CAPPED_EDGES contains exactly CALLS."""
    assert frozenset({"CALLS"}) == LOW_CONF_CAPPED_EDGES


def test_propagation_validation_exempt_edges() -> None:
    """PROPAGATION_VALIDATION_EXEMPT_EDGES contains exactly the three direct-contract edges."""
    assert frozenset({"IMPLEMENTS", "DEFINES_METHOD", "TYPED_BY"}) == PROPAGATION_VALIDATION_EXEMPT_EDGES


# =============================================================================
# Group G — RRF_PATH_WEIGHTS (03_data_models.md §10)
# =============================================================================


def test_rrf_path_weights_keys() -> None:
    """RRF_PATH_WEIGHTS has exactly the three ChangeType keys."""
    assert set(RRF_PATH_WEIGHTS.keys()) == {"ADDITION", "MODIFICATION", "DELETION"}


def test_rrf_path_weights_each_has_four_paths() -> None:
    """Each change type has exactly four retrieval-path weight keys."""
    expected_paths = {"dense_doc", "bm25_doc", "dense_code", "bm25_code"}
    for ct, weights in RRF_PATH_WEIGHTS.items():
        assert set(weights.keys()) == expected_paths, f"Wrong keys for change_type {ct!r}"


def test_rrf_path_weights_values() -> None:
    """Spot-check exact weight values from 03_data_models.md §10."""
    assert RRF_PATH_WEIGHTS["ADDITION"]["dense_doc"] == 1.2
    assert RRF_PATH_WEIGHTS["ADDITION"]["dense_code"] == 1.0
    assert RRF_PATH_WEIGHTS["ADDITION"]["bm25_code"] == 0.8
    assert RRF_PATH_WEIGHTS["MODIFICATION"]["dense_code"] == 1.2
    assert RRF_PATH_WEIGHTS["MODIFICATION"]["dense_doc"] == 1.0
    assert RRF_PATH_WEIGHTS["DELETION"]["dense_code"] == 1.2
    assert RRF_PATH_WEIGHTS["DELETION"]["bm25_doc"] == 0.8
