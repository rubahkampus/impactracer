"""
IR Metrics — Precision, Recall, F1, MRR Computation
=====================================================

RESPONSIBILITY
    Computes standard Information Retrieval metrics for evaluating
    the system output (CIS) against the ground truth (AIS).

METRICS (per Subbab III.7.4, Manning et al. 2008)
    Precision@K = |CIS_K intersect AIS| / |CIS_K|
    Recall@K    = |CIS_K intersect AIS| / |AIS|
    F1@K        = 2 * P@K * R@K / (P@K + R@K)
    MRR         = (1/|Q|) * sum(1/rank_q) for q in Q

    CIS must be sorted by the deterministic sort key hierarchy
    (depth ASC, structural_weight ASC, -rrf_score) BEFORE metrics
    are computed. The sort_cis_for_evaluation() function in this
    module handles this.

    K values: {5, 10, all} per settings.eval_k_values.
    Aggregation: macro-average across all CRs (each CR weighted equally).

    F1@10 is the PRIMARY reporting metric per Subbab III.7.4.
    Recall@10 is the PRIMARY calibration metric.

INPUTS
    predicted: list[str] (sorted node IDs from CIS).
    actual: set[str] (node IDs from AIS ground truth).

OUTPUTS
    Dict of metric name to float value.

ARCHITECTURAL CONSTRAINTS
    Zero LLM calls. Pure set operations and arithmetic.
"""
from __future__ import annotations

# TODO: Implement precision_at_k(), recall_at_k(), f1_at_k(), mrr(),
#       sort_cis_for_evaluation(), compute_all_metrics()
