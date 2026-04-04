"""
Ablation Study Controller — Six-Variant Experiment Runner
===========================================================

RESPONSIBILITY
    Executes the six ablation variants (B0, B1, B2, B3, S1, S2) on
    the 20-CR test set with locked parameters and collects per-CR
    metrics for statistical analysis.

VARIANTS (per Subbab III.7.3)
    B0  BM25 only.            No dense, no reranker, no LLM, no BFS.
    B1  Dense only.           No BM25, no reranker, no LLM, no BFS.
    B2  RRF hybrid.           BM25 + dense, no reranker, no LLM, no BFS.
    B3  RRF hybrid + reranker. No LLM validation, no BFS.
    S1  RRF + reranker + LLM. No BFS. CIS = SIS.
    S2  Full system.          RRF + reranker + LLM + BFS.

    For B0/B1/B2/B3 (no LLM validation): top-K retrieval results
    ARE the output. No SIS filtering step.
    For B0/B1/B2/B3/S1 (no BFS): CIS = retrieval/SIS output.
    No structural propagation.

INPUTS
    cr_list: list of (cr_id, cr_text) tuples.
    ais: dict mapping cr_id to set of node IDs (ground truth).
    variant: str in {"B0", "B1", "B2", "B3", "S1", "S2"}.
    settings: Settings object.

OUTPUTS
    DataFrame with columns: cr_id, variant, P@5, P@10, P@all,
    R@5, R@10, R@all, F1@5, F1@10, F1@all, MRR.

STATISTICAL TESTS (per Subbab III.7.5)
    After all variants complete, Wilcoxon signed-rank test
    (one-tailed, alpha=0.05) is run on paired F1@10 differences
    for S2 vs B1 and S2 vs S1.

ARCHITECTURAL CONSTRAINTS
    1. All variants use the SAME indexed data, SAME ChromaDB
       collections, SAME SQLite database. Only pipeline stages
       differ.
    2. Parameters locked before test set execution begins.
    3. Results logged and exported to CSV for Bab V reporting.
"""
from __future__ import annotations

# TODO: Implement run_ablation_variant(), run_full_ablation_study(),
#       compute_wilcoxon_test()
