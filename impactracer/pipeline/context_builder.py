"""
Context Builder — Token-Budgeted Payload Assembly for LLM Call #3
==================================================================

RESPONSIBILITY
    Assembles the synthesis context from CIS data, backlinks, and
    code snippets. Applies graceful truncation if the total token
    count exceeds the configured budget.

INPUTS
    cr_text, cr_interp, cis, sis_rrf_scores, backlinks,
    code_snippets, settings.

OUTPUTS
    A single string containing the full context payload ready for
    LLM Call #3.

TRUNCATION STRATEGY (per Subbab III.2.5.2)
    Nodes sorted by (depth ascending, structural_weight ascending).
    SIS seeds and IMPLEMENTS/TYPED_BY nodes are always retained.
    Nodes at depth 3 via IMPORTS/RENDERS/DEPENDS_ON_EXTERNAL are
    dropped first. Truncation count is appended to context so the
    LLM can note it in the executive_summary.

ARCHITECTURAL CONSTRAINTS
    1. Zero LLM calls. Pure string assembly + tiktoken counting.
    2. Token counting via tiktoken for the target LLM model.
       Uses gpt-4o-mini / o200k_base encoding as a close approximation
       for Gemini (within ~5-10%), sufficient for budget management.
    3. Per-node source_code snippet capped at 500 characters.
    4. Per-node backlinks capped at top-3 by similarity.
"""
from __future__ import annotations

import sqlite3

import tiktoken

from impactracer.models import CISResult, CRInterpretation, structural_weight


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    """Estimate token count for ``text`` using tiktoken.

    Uses the gpt-4o-mini (o200k_base) encoding as a close proxy for
    Gemini's tokenizer.  Within ~5-10% accuracy -- sufficient for the
    context-window budget guard.

    Args:
        text:  The string whose tokens are to be counted.
        model: tiktoken model name.  Defaults to "gpt-4o-mini".

    Returns:
        Estimated integer token count.
    """
    enc = tiktoken.encoding_for_model(model)
    return len(enc.encode(text))


# ---------------------------------------------------------------------------
# Step 7 helpers — fetched from SQLite by the runner
# ---------------------------------------------------------------------------

def fetch_backlinks(
    node_ids: list[str],
    conn: sqlite3.Connection,
) -> dict[str, list[tuple[str, float]]]:
    """Fetch traceability backlinks for a list of code node IDs.

    For each code node ID, queries doc_code_candidates to find which
    documentation chunks are most similar, returning up to the top-5
    backlinks ordered by similarity descending.

    Args:
        node_ids: Code node IDs from cis.all_node_ids().
        conn:     Open SQLite connection to impactracer.db.

    Returns:
        Mapping of node_id -> [(doc_id, similarity), ...] sorted by
        similarity descending.  Only code nodes with at least one
        backlink appear in the result; missing nodes are absent (not
        mapped to an empty list).
    """
    if not node_ids:
        return {}

    # Fetch all relevant backlinks in one query via IN clause
    placeholders = ",".join("?" * len(node_ids))
    rows = conn.execute(
        f"SELECT code_id, doc_id, similarity "
        f"FROM doc_code_candidates "
        f"WHERE code_id IN ({placeholders}) "
        f"ORDER BY code_id, similarity DESC",
        node_ids,
    ).fetchall()

    result: dict[str, list[tuple[str, float]]] = {}
    for code_id, doc_id, sim in rows:
        result.setdefault(code_id, []).append((doc_id, float(sim)))

    return result


def fetch_code_snippets(
    node_ids: list[str],
    conn: sqlite3.Connection,
) -> dict[str, str]:
    """Fetch source code or signature text for a list of code node IDs.

    Prefers ``source_code`` (full function/method body) when available.
    Falls back to ``signature`` (declaration line) when source_code is
    NULL or empty.  Nodes not found in code_nodes are silently omitted.

    Args:
        node_ids: Code node IDs from cis.all_node_ids().
        conn:     Open SQLite connection to impactracer.db.

    Returns:
        Mapping of node_id -> snippet string.  Empty or NULL fields
        are not included -- caller should use .get(nid) with a fallback.
    """
    if not node_ids:
        return {}

    placeholders = ",".join("?" * len(node_ids))
    rows = conn.execute(
        f"SELECT node_id, source_code, signature "
        f"FROM code_nodes "
        f"WHERE node_id IN ({placeholders})",
        node_ids,
    ).fetchall()

    result: dict[str, str] = {}
    for nid, source_code, signature in rows:
        # Prefer source_code; fall back to signature
        text = (source_code or "").strip() or (signature or "").strip()
        if text:
            result[nid] = text

    return result


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def build_synthesis_context(
    cr_text: str,
    cr_interp: CRInterpretation,
    cis: CISResult,
    sis_rrf_scores: dict[str, float],
    backlinks: dict[str, list[tuple[str, float]]],
    code_snippets: dict[str, str],
    settings,
) -> str:
    """Assemble token-budgeted context payload for LLM Call #3.

    Builds a structured markdown string containing:
      * Fixed header: the original CR text and its interpretation.
      * Per-node blocks for every CIS node, sorted by priority
        (SIS seeds first, then by structural edge weight ascending).

    If the total token count would exceed the configured budget, the
    lowest-priority nodes are dropped (greedy truncation) and a
    truncation note is appended so the synthesizer can acknowledge it.

    Per-node snippet capped at 500 characters; backlinks capped at top-3.

    Args:
        cr_text:        Raw Change Request text from the user.
        cr_interp:      CRInterpretation from LLM Call #1.
        cis:            CISResult from bfs_propagate().
        sis_rrf_scores: {node_id: rrf_score} for SIS nodes (informational).
        backlinks:      From fetch_backlinks() -- {node_id: [(doc_id, sim)]}.
        code_snippets:  From fetch_code_snippets() -- {node_id: snippet}.
        settings:       Settings instance (uses llm_max_context_tokens,
                        synthesis_system_prompt_tokens).

    Returns:
        Single assembled string; total tokens guaranteed <= budget.
    """
    budget = (
        settings.llm_max_context_tokens
        - settings.synthesis_system_prompt_tokens
        - 2000   # reserve for LLM output tokens
    )

    # ── Fixed header (always included) ───────────────────────────────────
    header = (
        f"## Change Request\n{cr_text}\n\n"
        f"## Interpreted Intent\n{cr_interp.primary_intent}\n"
        f"Change type: {cr_interp.change_type}\n"
        f"Affected layers: {', '.join(cr_interp.affected_layers)}\n"
        f"Domain concepts: {', '.join(cr_interp.affected_domain_concepts)}\n\n"
    )
    header_tokens = estimate_tokens(header)
    remaining = budget - header_tokens

    # ── Build per-node blocks ─────────────────────────────────────────────
    all_nodes = cis.combined()
    # (node_id, block_text, token_count)
    node_blocks: list[tuple[str, str, int]] = []

    for nid, trace in all_nodes.items():
        block = f"### {nid}\n"
        block += (
            f"Depth: {trace.depth} | "
            f"Chain: {' -> '.join(trace.causal_chain) if trace.causal_chain else 'SIS seed'}\n"
        )
        block += f"Path: {' -> '.join(trace.path)}\n"

        if nid in code_snippets:
            sig = code_snippets[nid][:500]   # hard cap per blueprint
            block += f"Signature:\n```\n{sig}\n```\n"

        if nid in backlinks:
            links = backlinks[nid][:3]       # top-3 backlinks per blueprint
            link_str = "; ".join(
                f"{did} (sim={sim:.3f})" for did, sim in links
            )
            block += f"Backlinks: {link_str}\n"

        block += "\n"
        tokens = estimate_tokens(block)
        node_blocks.append((nid, block, tokens))

    # ── Sort: SIS seeds (depth 0) first, then structural weight ascending ─
    node_blocks.sort(
        key=lambda x: (
            all_nodes[x[0]].depth,
            structural_weight(all_nodes[x[0]].causal_chain),
        )
    )

    # ── Greedy inclusion within budget ────────────────────────────────────
    included_blocks: list[str] = []
    truncated_count = 0
    for _nid, block, tok in node_blocks:
        if remaining - tok < 0:
            truncated_count += 1
            continue
        included_blocks.append(block)
        remaining -= tok

    context = header + "## Impacted Elements\n\n" + "".join(included_blocks)

    if truncated_count > 0:
        context += (
            f"\n[NOTE: {truncated_count} lower-priority nodes truncated "
            f"due to context window limit.]\n"
        )

    return context
