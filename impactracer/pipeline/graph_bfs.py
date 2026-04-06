"""
BFS Propagation Engine -- Deterministic Structural Impact Traversal
====================================================================

RESPONSIBILITY
    Loads the full code dependency graph from SQLite into a NetworkX
    MultiDiGraph (once per pipeline session), then performs multi-seed
    BFS with per-edge-type direction rules and depth limits to produce
    the Candidate Impact Set (CIS).

EDGE CONFIGURATION (per Subbab III.2.4.2 and III.2.4.3)

    Edge Type           Direction   Max Depth   Rationale
    -----------------   ---------   ---------   --------------------------
    CALLS               reverse     3           Callers of B are impacted
                                                when B changes.
    INHERITS            reverse     3           Subclasses are impacted
                                                when the parent changes.
    IMPLEMENTS          reverse     3           Implementors must adapt
                                                when an interface changes.
    TYPED_BY            reverse     3           Functions using type B
                                                break when B changes.
    DEFINES_METHOD      forward     3           A class's methods are
                                                impacted when the class
                                                changes.
    IMPORTS             reverse     1           Only direct importers.
                                                Prevents explosion through
                                                transitive import chains.
    DEPENDS_ON_EXTERNAL reverse     1           Only direct dependents.
    RENDERS             reverse     1           Only direct parent
                                                components.

    "reverse": impact flows from target -> sources.
        Edge A->B in graph means A depends on B.
        If B changes, find all A via graph.in_edges(B, edge_type).
    "forward": impact flows from source -> targets.
        Edge A->B means A owns B (DEFINES_METHOD only).
        If A changes, find all B via graph.out_edges(A, edge_type).

CORRECTNESS INVARIANT
    len(result.sis_nodes) + len(result.propagated_nodes) == len(visited)
    Asserted at the end of bfs_propagate().

ARCHITECTURAL CONSTRAINTS
    1. ZERO LLM calls. Entirely deterministic graph traversal.
    2. Identical seeds + identical graph => identical CIS (pure BFS).
    3. Graph is loaded once into memory and NEVER modified.
    4. BFS (not DFS) guarantees nodes are discovered in depth order so
       NodeTrace.depth is always the minimum distance from any seed.
    5. Seeds not present in the graph are silently skipped.
"""
from __future__ import annotations

import sqlite3
from collections import deque

import networkx as nx

from impactracer.models import CISResult, NodeTrace

# ---------------------------------------------------------------------------
# Edge configuration table
# ---------------------------------------------------------------------------

EDGE_CONFIG: dict[str, dict] = {
    "CALLS":               {"direction": "reverse", "max_depth": 3},
    "INHERITS":            {"direction": "reverse", "max_depth": 3},
    "IMPLEMENTS":          {"direction": "reverse", "max_depth": 3},
    "TYPED_BY":            {"direction": "reverse", "max_depth": 3},
    "DEFINES_METHOD":      {"direction": "forward", "max_depth": 3},
    "IMPORTS":             {"direction": "reverse", "max_depth": 1},
    "DEPENDS_ON_EXTERNAL": {"direction": "reverse", "max_depth": 1},
    "RENDERS":             {"direction": "reverse", "max_depth": 1},
}


# ---------------------------------------------------------------------------
# Graph loader
# ---------------------------------------------------------------------------

def build_graph_from_sqlite(conn: sqlite3.Connection) -> nx.MultiDiGraph:
    """Load the full structural_edges table into a NetworkX MultiDiGraph.

    Called once at pipeline startup (runner.py Step 0).  The returned
    graph is read-only for the entire analysis session.

    Args:
        conn: Open SQLite connection to impactracer.db.

    Returns:
        nx.MultiDiGraph where each edge carries an ``edge_type`` attribute.
    """
    G = nx.MultiDiGraph()
    rows = conn.execute(
        "SELECT source_id, target_id, edge_type FROM structural_edges"
    ).fetchall()
    for src, tgt, etype in rows:
        G.add_edge(src, tgt, edge_type=etype)
    return G


# ---------------------------------------------------------------------------
# BFS propagation
# ---------------------------------------------------------------------------

def bfs_propagate(
    graph: nx.MultiDiGraph,
    seed_node_ids: list[str],
) -> CISResult:
    """Multi-seed BFS producing the Candidate Impact Set (CIS).

    Each seed is registered as a depth-0 SIS node.  BFS then expands
    outward following the direction and depth rules in EDGE_CONFIG.
    Every newly discovered node is stored as a propagated_node with its
    full NodeTrace (depth, causal_chain, traversal path, origin seed).

    A node is visited at most once: the first time it is reached
    (guaranteed minimum-depth path) determines its NodeTrace.

    Seeds not present in the graph are silently ignored -- they do not
    enter either sis_nodes or the visited set.

    Args:
        graph:          MultiDiGraph from build_graph_from_sqlite().
        seed_node_ids:  Code node IDs from seed_resolver.py.

    Returns:
        CISResult with sis_nodes (depth 0) and propagated_nodes (depth 1+).
        Satisfies: len(sis_nodes) + len(propagated_nodes) == len(visited).
    """
    result  = CISResult()
    visited: set[str] = set()

    # ── Initialise seeds ──────────────────────────────────────────────────
    for seed in seed_node_ids:
        if seed not in graph:
            continue
        visited.add(seed)
        result.sis_nodes[seed] = NodeTrace(
            depth=0, causal_chain=[], path=[seed], source_seed=seed,
        )

    # BFS queue: (node, depth, chain, path, origin_seed)
    queue: deque[tuple[str, int, list[str], list[str], str]] = deque(
        (seed, 0, [], [seed], seed)
        for seed in seed_node_ids
        if seed in graph
    )

    # ── BFS loop ──────────────────────────────────────────────────────────
    while queue:
        node, depth, chain, path, origin = queue.popleft()

        for edge_type, cfg in EDGE_CONFIG.items():
            if depth >= cfg["max_depth"]:
                continue   # depth cap for this edge type

            if cfg["direction"] == "reverse":
                # Edge A->B in graph; B is `node`; find all A (predecessors)
                neighbors = [
                    u for u, _, d in graph.in_edges(node, data=True)
                    if d.get("edge_type") == edge_type
                ]
            else:
                # Edge A->B in graph; A is `node`; find all B (successors)
                neighbors = [
                    v for _, v, d in graph.out_edges(node, data=True)
                    if d.get("edge_type") == edge_type
                ]

            for nbr in neighbors:
                if nbr in visited:
                    continue
                visited.add(nbr)

                new_chain = chain + [edge_type]
                new_path  = path  + [nbr]
                new_depth = depth + 1

                result.propagated_nodes[nbr] = NodeTrace(
                    depth=new_depth,
                    causal_chain=new_chain,
                    path=new_path,
                    source_seed=origin,
                )
                queue.append((nbr, new_depth, new_chain, new_path, origin))

    # ── Correctness invariant (per Blueprint v3 Section 6.7) ─────────────
    assert len(result.sis_nodes) + len(result.propagated_nodes) == len(visited), (
        f"BFS invariant violated: "
        f"{len(result.sis_nodes)} sis + {len(result.propagated_nodes)} prop "
        f"!= {len(visited)} visited"
    )

    return result
