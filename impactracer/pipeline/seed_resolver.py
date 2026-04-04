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

# TODO: Implement resolve_sis_to_code_seeds()
