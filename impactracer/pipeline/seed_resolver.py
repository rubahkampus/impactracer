"""
Seed Resolver — Doc-Chunk SIS to Code Node Resolution
======================================================

RESPONSIBILITY
    Resolves SIS nodes that are document chunks (not code nodes)
    to their associated code nodes via the doc_code_candidates
    table. This bridges the gap between semantic search results
    (which may return doc chunks) and BFS traversal (which
    operates exclusively on code nodes).

INPUTS
    sis_node_ids: list[str] (mixed doc chunk IDs and code node IDs).
    conn: sqlite3.Connection.
    top_k: int (candidates per doc chunk, default from settings).

OUTPUTS
    code_seeds: deduplicated list of code node IDs for BFS.
    doc_to_code_map: dict mapping doc_chunk_id to resolved code IDs.

ARCHITECTURAL CONSTRAINTS
    1. Zero LLM calls. Pure SQL queries against doc_code_candidates.
    2. Code nodes in SIS pass through directly (no resolution needed).
    3. Doc chunks are resolved via top-K similarity lookup.
    4. Deduplication preserves insertion order.
"""
from __future__ import annotations

import sqlite3


def resolve_sis_to_code_seeds(
    sis_node_ids: list[str],
    conn: sqlite3.Connection,
    top_k: int = 3,
) -> tuple[list[str], dict[str, list[str]]]:
    """Resolve SIS containing mixed doc/code node IDs to pure code node IDs.

    Iterates over all SIS node IDs. Code nodes (present in code_nodes
    table) pass through directly. Doc chunk IDs (absent from code_nodes)
    are resolved to their top-K most similar code nodes via the
    doc_code_candidates traceability table.

    Args:
        sis_node_ids: Mixed list of node IDs from SIS validation output.
                      May contain both code node IDs and doc chunk IDs.
        conn:         Open SQLite connection to impactracer.db.
        top_k:        Maximum code nodes to resolve per doc chunk.
                      Defaults to 3; settings.top_k_traceability is used
                      by the runner.

    Returns:
        Tuple of:
          - code_seeds: Deduplicated list of code node IDs (insertion order
                        preserved) ready for bfs_propagate().
          - doc_to_code_map: {doc_chunk_id: [resolved_code_ids]} for
                             traceability reporting. Only contains entries
                             for doc-chunk SIS nodes.
    """
    code_seeds: list[str] = []
    doc_to_code_map: dict[str, list[str]] = {}

    # Fetch all known code node IDs in one query (O(N) set membership later)
    code_node_ids: set[str] = set(
        r[0] for r in conn.execute("SELECT node_id FROM code_nodes").fetchall()
    )

    for nid in sis_node_ids:
        if nid in code_node_ids:
            # Direct code node — passes through as a BFS seed
            code_seeds.append(nid)
        else:
            # Doc chunk → resolve via traceability table
            rows = conn.execute(
                "SELECT code_id FROM doc_code_candidates "
                "WHERE doc_id = ? ORDER BY similarity DESC LIMIT ?",
                (nid, top_k),
            ).fetchall()
            resolved = [r[0] for r in rows]
            doc_to_code_map[nid] = resolved
            code_seeds.extend(resolved)

    # Deduplicate preserving insertion order
    seen: set[str] = set()
    deduped: list[str] = []
    for cid in code_seeds:
        if cid not in seen:
            seen.add(cid)
            deduped.append(cid)

    return deduped, doc_to_code_map
