"""
Pipeline Runner — Full Online Analysis Orchestrator
=====================================================

RESPONSIBILITY
    Orchestrates the complete 9-step online analysis pipeline from
    raw CR text to ImpactReport JSON file. This is the single entry
    point for the analyze command.

EXECUTION FLOW (per Subbab III.5.3.3)
    Step 0. Load persistent stores (SQLite, ChromaDB, graph, BM25).
    Step 1. LLM Call #1 — Interpret CR.
    GIGO CHECKPOINT — If is_actionable is False, return rejection
           report immediately. Do NOT execute Steps 2 through 9.
    Step 2. Dual-path hybrid search + RRF fusion.
    Step 3. Cross-encoder reranking.
    Step 4. LLM Call #2 — Validate SIS.
    Step 5. Resolve doc-chunk SIS nodes to code seeds.
    Step 6. BFS propagation (deterministic, zero LLM).
    Step 7. Fetch backlinks and code snippets.
    Step 8. Build token-budgeted context.
    Step 9. LLM Call #3 — Synthesize report.

LLM CALL BUDGET
    Exactly 3 calls for an actionable CR.
    Exactly 1 call for a rejected CR (GIGO early termination).
    NO EXCEPTIONS. If you find yourself adding a 4th call, the
    architecture is violated and you must refactor.

ARCHITECTURAL CONSTRAINTS
    1. All stores loaded once at startup. Graph never modified.
    2. The runner itself contains NO business logic. It calls
       other modules and passes data between them.
    3. Every step is logged via loguru with timing information.
"""
from __future__ import annotations

import time

from loguru import logger

from impactracer.config import Settings
from impactracer.db.chroma_client import get_chroma_client, init_collections
from impactracer.db.sqlite_client import get_connection
from impactracer.indexer.embedder import Embedder
from impactracer.indexer.reranker import Reranker
from impactracer.llm_client import GeminiClient
from impactracer.models import ImpactReport
from impactracer.pipeline.context_builder import (
    build_synthesis_context,
    estimate_tokens,
    fetch_backlinks,
    fetch_code_snippets,
)
from impactracer.pipeline.graph_bfs import bfs_propagate, build_graph_from_sqlite
from impactracer.pipeline.interpreter import interpret_cr
from impactracer.pipeline.retriever import (
    enrich_candidates,
    hybrid_search,
    load_code_bm25,
    load_doc_bm25,
)
from impactracer.pipeline.seed_resolver import resolve_sis_to_code_seeds
from impactracer.pipeline.synthesizer import synthesize_report
from impactracer.pipeline.validator import validate_sis_candidates


def run_analysis(cr_text: str, settings: Settings) -> ImpactReport:
    """Execute the full 9-step online impact analysis pipeline.

    This is the single entry point for ``impactracer analyze``.
    Loads all persistent stores once, then executes the pipeline
    with exactly 3 LLM calls (or 1 if the CR is rejected by GIGO).

    Args:
        cr_text:  Raw Change Request text in any language.
        settings: Fully populated Settings instance.

    Returns:
        ImpactReport ready to be serialised to JSON.
    """
    t_total = time.perf_counter()

    # ── Step 0: Load persistent stores ────────────────────────────────────
    logger.info("Step 0: Loading persistent stores...")
    t0 = time.perf_counter()

    conn    = get_connection(settings.db_path)
    chroma  = get_chroma_client(settings.chroma_path)
    doc_col, code_col = init_collections(chroma)

    graph = build_graph_from_sqlite(conn)
    doc_bm25,  doc_bm25_ids  = load_doc_bm25(doc_col)
    code_bm25, code_bm25_ids = load_code_bm25(code_col)

    embedder = Embedder(settings.embedding_model)
    reranker = Reranker(settings.reranker_model)
    llm      = GeminiClient(settings)

    logger.info(
        "Stores loaded: graph={} nodes/{} edges  ({:.1f}s)",
        graph.number_of_nodes(), graph.number_of_edges(),
        time.perf_counter() - t0,
    )

    # ── Step 1: LLM Call #1 — Interpret CR ───────────────────────────────
    logger.info("Step 1: Interpreting CR (LLM Call #1)...")
    t1 = time.perf_counter()
    cr_interp = interpret_cr(cr_text, llm)
    logger.info(
        "CR interpreted: actionable={}, intent='{}' ({:.1f}s)",
        cr_interp.is_actionable, cr_interp.primary_intent,
        time.perf_counter() - t1,
    )

    # ── GIGO Checkpoint ───────────────────────────────────────────────────
    if not cr_interp.is_actionable:
        logger.warning("CR rejected: {}", cr_interp.rejection_reason)
        conn.close()
        return ImpactReport(
            executive_summary=f"CR rejected: {cr_interp.rejection_reason}",
            impacted_items=[],
            requirement_conflicts=[],
            estimated_change_scope="terlokalisasi",
        )

    # ── Step 2: Dual-path hybrid search + RRF ────────────────────────────
    logger.info("Step 2: Hybrid search + RRF fusion...")
    t2 = time.perf_counter()
    candidates = hybrid_search(
        queries=cr_interp.search_queries,
        doc_collection=doc_col,
        code_collection=code_col,
        doc_bm25=doc_bm25,
        doc_bm25_ids=doc_bm25_ids,
        code_bm25=code_bm25,
        code_bm25_ids=code_bm25_ids,
        embedder=embedder,
        affected_layers=cr_interp.affected_layers,
        rrf_k=settings.rrf_k,
        max_candidates=settings.max_candidates_post_rrf,
    )
    candidates = enrich_candidates(candidates, doc_col, code_col, conn)
    logger.info(
        "Hybrid search: {} candidates post-RRF ({:.1f}s)",
        len(candidates), time.perf_counter() - t2,
    )

    # ── Step 3: Cross-encoder reranking ──────────────────────────────────
    logger.info("Step 3: Cross-encoder reranking...")
    t3 = time.perf_counter()
    candidates = reranker.rerank(
        query=cr_interp.primary_intent,
        candidates=candidates,
        top_k=settings.max_candidates_post_rerank,
    )
    logger.info(
        "Reranked: {} candidates ({:.1f}s)",
        len(candidates), time.perf_counter() - t3,
    )

    # ── Step 4: LLM Call #2 — Validate SIS ───────────────────────────────
    logger.info("Step 4: Validating SIS (LLM Call #2)...")
    t4 = time.perf_counter()
    validation = validate_sis_candidates(cr_interp, candidates, llm)
    sis_node_ids = [v.node_id for v in validation.verdicts if v.confirmed]
    sis_rrf_scores = {
        c["node_id"]: c["rrf_score"]
        for c in candidates
        if c["node_id"] in set(sis_node_ids)
    }
    logger.info(
        "SIS validated: {}/{} confirmed ({:.1f}s)",
        len(sis_node_ids), len(candidates),
        time.perf_counter() - t4,
    )

    # ── Step 5: Resolve doc-chunk SIS nodes to code seeds ─────────────────
    logger.info("Step 5: Resolving doc-chunk seeds...")
    t5 = time.perf_counter()
    code_seeds, doc_code_map = resolve_sis_to_code_seeds(
        sis_node_ids, conn, top_k=settings.top_k_traceability
    )
    logger.info(
        "Code seeds for BFS: {} (from {} SIS nodes) ({:.1f}s)",
        len(code_seeds), len(sis_node_ids),
        time.perf_counter() - t5,
    )

    # ── Step 6: BFS propagation (deterministic, zero LLM) ─────────────────
    logger.info("Step 6: BFS propagation...")
    t6 = time.perf_counter()
    cis = bfs_propagate(graph, code_seeds)
    logger.info(
        "CIS: {} SIS + {} propagated = {} total ({:.1f}s)",
        len(cis.sis_nodes), len(cis.propagated_nodes),
        len(cis.all_node_ids()),
        time.perf_counter() - t6,
    )

    # ── Step 7: Fetch backlinks and code snippets ─────────────────────────
    logger.info("Step 7: Fetching backlinks and code snippets...")
    t7 = time.perf_counter()
    all_node_ids  = cis.all_node_ids()
    backlinks     = fetch_backlinks(all_node_ids, conn)
    code_snippets = fetch_code_snippets(all_node_ids, conn)
    logger.info(
        "Fetched: {}/{} nodes with backlinks, {}/{} with snippets ({:.1f}s)",
        len(backlinks), len(all_node_ids),
        len(code_snippets), len(all_node_ids),
        time.perf_counter() - t7,
    )

    # ── Step 8: Build token-budgeted context ──────────────────────────────
    logger.info("Step 8: Building synthesis context...")
    t8 = time.perf_counter()
    context = build_synthesis_context(
        cr_text, cr_interp, cis, sis_rrf_scores,
        backlinks, code_snippets, settings,
    )
    token_count = estimate_tokens(context)
    logger.info(
        "Synthesis context: ~{} tokens ({:.1f}s)",
        token_count, time.perf_counter() - t8,
    )

    # ── Step 9: LLM Call #3 — Synthesize report ───────────────────────────
    logger.info("Step 9: Synthesizing impact report (LLM Call #3)...")
    t9 = time.perf_counter()
    report = synthesize_report(context, llm)
    logger.info(
        "Report generated: {} impacted items, scope='{}' ({:.1f}s)",
        len(report.impacted_items), report.estimated_change_scope,
        time.perf_counter() - t9,
    )

    conn.close()
    logger.info(
        "Pipeline complete. Total elapsed: {:.1f}s",
        time.perf_counter() - t_total,
    )
    return report
