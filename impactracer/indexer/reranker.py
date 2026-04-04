"""
Reranker Module — BGE-Reranker-v2-M3 Cross-Encoder
====================================================

RESPONSIBILITY
    Wraps the BAAI/bge-reranker-v2-m3 cross-encoder model to compute
    pairwise relevance scores for (query, candidate) pairs. Inserted
    between RRF fusion and LLM Call #2 per Subbab III.2.3.3.

INPUTS
    query: str (primary_intent from CRInterpretation)
    candidates: list of dicts with "node_id" and "text_snippet" keys.

OUTPUTS
    Same list re-sorted by cross-encoder score descending, truncated
    to top_k. Each dict gains a "reranker_score" key.

ARCHITECTURAL CONSTRAINTS
    1. Model runs locally. No API calls.
    2. Deterministic on identical inputs (no sampling).
    3. Maximum 15 candidates per invocation.
    4. This is NOT an LLM call. It is a local transformer inference.
"""
from __future__ import annotations

from FlagEmbedding import FlagReranker


class Reranker:
    """Local BGE-Reranker-v2-M3 cross-encoder.

    Instantiate once and reuse; the model is loaded into memory on __init__.
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3") -> None:
        self.model = FlagReranker(model_name, use_fp16=True)

    def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_k: int = 15,
    ) -> list[dict]:
        """Score each (query, candidate.text_snippet) pair with the cross-encoder.

        Returns candidates re-sorted by cross-encoder score descending,
        truncated to top_k. Each dict in the returned list gains a
        "reranker_score" key containing a normalized float in [0, 1].

        Args:
            query:      The primary_intent string from CRInterpretation.
            candidates: Dicts that must include at least "node_id" and
                        "text_snippet" keys. May contain additional fields
                        (e.g. rrf_score) which are preserved unchanged.
            top_k:      Maximum number of results to return.

        Returns:
            A new list of candidate dicts sorted by reranker_score descending,
            length ≤ top_k.
        """
        if not candidates:
            return []

        pairs = [(query, c["text_snippet"]) for c in candidates]
        scores = self.model.compute_score(pairs, normalize=True)

        for c, s in zip(candidates, scores):
            c["reranker_score"] = float(s)

        ranked = sorted(candidates, key=lambda c: c["reranker_score"], reverse=True)
        return ranked[:top_k]
