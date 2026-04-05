"""
Traceability Precomputation — Cosine Similarity Matrix
=======================================================

RESPONSIBILITY
    Computes the full N_code x N_doc cosine similarity matrix between
    all code unit vectors and all document chunk vectors, then stores
    the top-K pairs per code unit into doc_code_candidates in SQLite.

INPUTS
    code_vecs: dict mapping node_id to numpy vector.
    doc_vecs: dict mapping chunk_id to numpy vector.

OUTPUTS
    Rows inserted into doc_code_candidates table.

ALGORITHM
    1. Stack all vectors into matrices C (N_code, D) and D (N_doc, D).
    2. L2-normalize both matrices row-wise.
    3. Compute sim = C @ D.T (pure matrix multiply).
    4. For each code row, take top-K doc indices by descending score.
    5. Insert (code_id, doc_id, similarity) into SQLite.

ARCHITECTURAL CONSTRAINTS
    Zero LLM calls. Pure linear algebra via numpy.
    O(N_code * N_doc * D) complexity. At Haidar scale (500 * 100 * 1024)
    this is approximately 50M FLOPs and completes in under one second.
"""
from __future__ import annotations

import sqlite3

import numpy as np


def compute_doc_code_candidates(
    code_vecs: dict[str, np.ndarray],
    doc_vecs: dict[str, np.ndarray],
    top_k: int = 5,
) -> list[tuple[str, str, float]]:
    """Brute-force cosine similarity between all code and doc vectors.

    Args:
        code_vecs: Mapping of node_id → (D,) float32 vector for each code unit.
        doc_vecs:  Mapping of chunk_id → (D,) float32 vector for each doc chunk.
        top_k:     Number of highest-scoring doc chunks to retain per code unit.

    Returns:
        List of (code_id, doc_id, similarity) tuples, where similarity is the
        cosine similarity in [-1, 1]. Each code unit contributes at most top_k
        tuples. Tuples are in descending similarity order within each code unit
        but are not globally sorted.

    Complexity: O(N_code × N_doc × D) for the matrix multiply.
    At Haidar scale (500 × 100 × 1024): ~50M FLOPs → sub-second.
    """
    if not code_vecs or not doc_vecs:
        return []

    code_ids = list(code_vecs.keys())
    doc_ids = list(doc_vecs.keys())

    C = np.stack([code_vecs[cid] for cid in code_ids])   # (N_code, D)
    D = np.stack([doc_vecs[did] for did in doc_ids])      # (N_doc, D)

    # L2-normalize rows for cosine similarity via dot product
    C = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-10)
    D = D / (np.linalg.norm(D, axis=1, keepdims=True) + 1e-10)

    sim = C @ D.T   # (N_code, N_doc)

    results: list[tuple[str, str, float]] = []
    for i, cid in enumerate(code_ids):
        top_j = np.argsort(sim[i])[::-1][:top_k]
        for j in top_j:
            results.append((cid, doc_ids[j], float(sim[i, j])))

    return results


def store_doc_code_candidates(
    conn: sqlite3.Connection,
    candidates: list[tuple[str, str, float]],
) -> None:
    """Insert (code_id, doc_id, similarity) rows into doc_code_candidates.

    Existing rows for the same (code_node_id, doc_chunk_id) pair are replaced
    via INSERT OR REPLACE to allow re-indexing without constraint violations.

    Args:
        conn:       Open SQLite connection (WAL mode assumed).
        candidates: Output of compute_doc_code_candidates().
    """
    conn.executemany(
        """
        INSERT OR REPLACE INTO doc_code_candidates
            (code_id, doc_id, similarity)
        VALUES (?, ?, ?)
        """,
        candidates,
    )
    conn.commit()
