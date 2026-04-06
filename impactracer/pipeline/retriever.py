"""
Hybrid Retriever -- Dual-Path Dense+BM25 Search with RRF Fusion
================================================================

RESPONSIBILITY
    Executes parallel search on both doc_chunks and code_units
    collections using two retrieval paradigms (dense cosine via
    ChromaDB and lexical via BM25), then fuses all ranked lists via
    Reciprocal Rank Fusion (RRF).  A separate enrichment step
    populates text_snippet / node_type from ChromaDB for downstream
    reranking and LLM validation.

PIPELINE POSITION
    Called from runner.py Step 2, immediately before the BGE reranker.
    Zero LLM calls.  Pure statistical retrieval.

RRF FORMULA (Cormack et al. 2009, per Subbab III.2.3.2)
    RRF(d) = sum_i( 1 / (k + rank_i(d) + 1) ),  k = 60

LAYER-TO-CHUNK-TYPE MAPPING (Blueprint v3 Section 6.6)
    "requirement" -> ["FR", "NFR"]
    "design"      -> ["Design"]
    "code"        -> activates code_units path; no chunk_type filter

FOUR RANKED LISTS PER QUERY
    1. Dense-doc  : cosine search on doc_chunks ChromaDB collection
    2. BM25-doc   : lexical search on doc chunk texts
    3. Dense-code : cosine search on code_units ChromaDB collection
    4. BM25-code  : lexical search on code unit embed_texts
    All lists across all queries fused in a single RRF pass.

ARCHITECTURAL CONSTRAINTS
    1. Zero LLM calls.  Pure statistical retrieval.
    2. BM25 indices are rebuilt in-memory per session from ChromaDB
       documents.  They are never persisted to disk.
    3. ChromaDB queries are guarded: n_results is clamped to
       collection.count() so empty or small collections never raise.
    4. text_snippet / node_type fields are LEFT EMPTY by hybrid_search
       and filled by enrich_candidates -- keeping retrieval and
       fetch concerns separate.
"""
from __future__ import annotations

import sqlite3

import numpy as np
from rank_bm25 import BM25Okapi

# ---------------------------------------------------------------------------
# Layer -> chunk_type mapping
# ---------------------------------------------------------------------------

LAYER_TO_CHUNK_TYPES: dict[str, list[str]] = {
    "requirement": ["FR", "NFR"],
    "design":      ["Design"],
    "code":        [],   # code goes to code_units path, not filtered by type
}


def resolve_doc_filter(affected_layers: list[str]) -> list[str] | None:
    """Convert affected_layers to chunk_type values for the doc collection.

    Returns None when no doc chunk type filter is required (i.e. when the
    CR does not touch any requirement or design layer), which tells
    hybrid_search to search all doc chunks without a where clause.
    """
    types: list[str] = []
    for layer in affected_layers:
        types.extend(LAYER_TO_CHUNK_TYPES.get(layer, []))
    return types if types else None


def should_search_code(affected_layers: list[str]) -> bool:
    """Return True when the code_units collection should be searched."""
    return "code" in affected_layers


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------

def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    k: int = 60,
) -> dict[str, float]:
    """Fuse N ranked lists via Reciprocal Rank Fusion.

    Args:
        ranked_lists: Each inner list is an ordered sequence of node_ids
                      from one retrieval source (dense or BM25).
        k:            RRF constant (default 60 per Cormack et al.).

    Returns:
        Mapping of node_id -> cumulative RRF score (higher = more relevant).
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, nid in enumerate(ranked):
            scores[nid] = scores.get(nid, 0.0) + 1.0 / (k + rank + 1)
    return scores


# ---------------------------------------------------------------------------
# BM25 index construction and session loading
# ---------------------------------------------------------------------------

def build_bm25_index(
    texts: list[str],
    ids: list[str],
) -> tuple[BM25Okapi, list[str]]:
    """Build an in-memory BM25Okapi index from raw texts.

    Rebuilds per pipeline session (~0.5 s for 300 documents).
    Not persisted to disk -- no stale-index risk.

    Args:
        texts: Raw text strings to index (one per document).
        ids:   Corresponding IDs in the same order.

    Returns:
        (BM25Okapi index, ids list) -- ids are stored alongside the
        index so callers can map argsort positions back to node_ids.
    """
    tokenized = [t.lower().split() for t in texts]
    return BM25Okapi(tokenized), ids


def load_doc_bm25(doc_col) -> tuple[BM25Okapi, list[str]]:
    """Fetch all doc_chunks texts from ChromaDB and build a BM25 index.

    Called once at pipeline startup in runner.py.
    """
    result = doc_col.get(include=["documents"])
    texts = result["documents"] or []
    ids   = result["ids"]
    return build_bm25_index(texts, ids)


def load_code_bm25(code_col) -> tuple[BM25Okapi, list[str]]:
    """Fetch all code_units embed_texts from ChromaDB and build a BM25 index.

    Called once at pipeline startup in runner.py.
    """
    result = code_col.get(include=["documents"])
    texts = result["documents"] or []
    ids   = result["ids"]
    return build_bm25_index(texts, ids)


# ---------------------------------------------------------------------------
# Core hybrid search
# ---------------------------------------------------------------------------

def hybrid_search(
    queries: list[str],
    doc_collection,           # chromadb.Collection: doc_chunks
    code_collection,          # chromadb.Collection: code_units
    doc_bm25: BM25Okapi,
    doc_bm25_ids: list[str],
    code_bm25: BM25Okapi,
    code_bm25_ids: list[str],
    embedder,                 # Embedder instance from indexer/embedder.py
    affected_layers: list[str],
    top_k_per_query: int = 15,
    rrf_k: int = 60,
    max_candidates: int = 15,
) -> list[dict]:
    """Dual-path hybrid search with RRF fusion.

    For each query in `queries`:
      * Embeds the query via BGE-M3.
      * Runs cosine search on doc_chunks (with optional chunk_type filter).
      * Runs BM25 search on doc chunk texts.
      * If "code" in affected_layers: runs cosine + BM25 on code_units.
    All resulting ranked lists are fused via a single RRF pass.

    Returns a list of candidate dicts sorted by rrf_score descending,
    truncated to max_candidates.  text_snippet and node_type are left
    empty -- call enrich_candidates() to populate them.

    Args:
        queries:          2-3 English search phrases from CRInterpretation.
        doc_collection:   ChromaDB doc_chunks collection.
        code_collection:  ChromaDB code_units collection.
        doc_bm25:         Pre-built BM25 index for doc chunks.
        doc_bm25_ids:     IDs corresponding to doc_bm25 rows.
        code_bm25:        Pre-built BM25 index for code units.
        code_bm25_ids:    IDs corresponding to code_bm25 rows.
        embedder:         Embedder instance (BGE-M3).
        affected_layers:  From CRInterpretation.affected_layers.
        top_k_per_query:  Max results to fetch per (query, path) pair.
        rrf_k:            RRF constant.
        max_candidates:   Max candidates to return after fusion.

    Returns:
        List of dicts, each with keys:
          node_id, rrf_score, text_snippet (empty), node_type (empty).
    """
    all_ranked_lists: list[list[str]] = []
    doc_filter  = resolve_doc_filter(affected_layers)
    search_code = should_search_code(affected_layers)

    doc_total  = doc_collection.count()  if doc_bm25_ids  else 0
    code_total = code_collection.count() if code_bm25_ids else 0

    for query in queries:
        query_vec  = embedder.embed_single(query)
        tokenized  = query.lower().split()

        # ── Doc path ──────────────────────────────────────────────────────
        if doc_total > 0:
            where = {"chunk_type": {"$in": doc_filter}} if doc_filter else None
            try:
                doc_dense = doc_collection.query(
                    query_embeddings=[query_vec],
                    n_results=min(top_k_per_query, doc_total),
                    where=where,
                )
                all_ranked_lists.append(doc_dense["ids"][0])
            except Exception:
                pass  # degraded gracefully -- dense-doc list simply absent

        if doc_bm25_ids:
            scores = doc_bm25.get_scores(tokenized)
            bm25_ranked = [
                doc_bm25_ids[i]
                for i in np.argsort(scores)[::-1][:top_k_per_query]
            ]
            all_ranked_lists.append(bm25_ranked)

        # ── Code path (only when affected_layers includes "code") ─────────
        if search_code:
            if code_total > 0:
                try:
                    code_dense = code_collection.query(
                        query_embeddings=[query_vec],
                        n_results=min(top_k_per_query, code_total),
                    )
                    all_ranked_lists.append(code_dense["ids"][0])
                except Exception:
                    pass  # degraded gracefully

            if code_bm25_ids:
                scores = code_bm25.get_scores(tokenized)
                bm25_ranked = [
                    code_bm25_ids[i]
                    for i in np.argsort(scores)[::-1][:top_k_per_query]
                ]
                all_ranked_lists.append(bm25_ranked)

    # ── RRF fusion ────────────────────────────────────────────────────────
    rrf_scores = reciprocal_rank_fusion(all_ranked_lists, k=rrf_k)
    sorted_candidates = sorted(
        rrf_scores.items(), key=lambda x: x[1], reverse=True
    )

    return [
        {
            "node_id":      nid,
            "rrf_score":    score,
            "text_snippet": "",   # populated by enrich_candidates()
            "node_type":    "",   # populated by enrich_candidates()
        }
        for nid, score in sorted_candidates[:max_candidates]
    ]


# ---------------------------------------------------------------------------
# Candidate enrichment (text_snippet + node_type from stores)
# ---------------------------------------------------------------------------

def enrich_candidates(
    candidates: list[dict],
    doc_col,
    code_col,
    conn: sqlite3.Connection,
) -> list[dict]:
    """Populate text_snippet, node_type, file_path, and source for each candidate.

    Batch-fetches from both ChromaDB collections using their IDs.
    ChromaDB's get() silently returns only IDs that exist in that
    collection, so it is safe to query both with the full candidate list.

    Adds three keys to each candidate dict:
      text_snippet  -- embed_text / section text (used by reranker)
      node_type     -- e.g. "Function", "FR", "Design"
      source        -- "doc" | "code" | "unknown"
      file_path     -- file path for code nodes, source_file for doc chunks

    Args:
        candidates: Output of hybrid_search() (text_snippet/node_type empty).
        doc_col:    ChromaDB doc_chunks collection.
        code_col:   ChromaDB code_units collection.
        conn:       Open SQLite connection (available but not used for
                    the primary fetch -- ChromaDB metadata is sufficient).

    Returns:
        Same list with text_snippet / node_type / source / file_path filled.
    """
    if not candidates:
        return candidates

    all_ids = [c["node_id"] for c in candidates]

    # Batch fetch from doc_chunks
    doc_result  = doc_col.get(ids=all_ids,  include=["documents", "metadatas"])
    doc_map: dict[str, dict] = {}
    for i, did in enumerate(doc_result["ids"]):
        text = (doc_result["documents"]  or [""])[i]
        meta = (doc_result["metadatas"] or [{}])[i]
        doc_map[did] = {
            "text_snippet": text,
            "node_type":    meta.get("chunk_type", "DocChunk"),
            "file_path":    meta.get("source_file", ""),
            "source":       "doc",
        }

    # Batch fetch from code_units
    code_result = code_col.get(ids=all_ids, include=["documents", "metadatas"])
    code_map: dict[str, dict] = {}
    for i, cid in enumerate(code_result["ids"]):
        text = (code_result["documents"]  or [""])[i]
        meta = (code_result["metadatas"] or [{}])[i]
        code_map[cid] = {
            "text_snippet": text,
            "node_type":    meta.get("node_type", "CodeNode"),
            "file_path":    meta.get("file_path", ""),
            "source":       "code",
        }

    for c in candidates:
        nid = c["node_id"]
        if nid in doc_map:
            c.update(doc_map[nid])
        elif nid in code_map:
            c.update(code_map[nid])
        else:
            # ID present in RRF result but absent from both stores -- should
            # not happen in a correctly indexed repo; handled gracefully.
            c["text_snippet"] = nid
            c["node_type"]    = "unknown"
            c["file_path"]    = ""
            c["source"]       = "unknown"

    return candidates
